# sms_msgs_scraper

A Python CLI tool for extracting and summarizing bank transactions from my SMS messages.

It reads an Android SMS backup file (the XML that the *SMS Backup & Restore* app produces),
identifies transaction alerts by sender short code, parses them into transactions, deduplicates
repeats, and reports them — as listings, as unique vendor lists, or as month-by-month spending
totals broken down by currency.

**Version 2.4.0**, which adds `monthly_vendor_chart` — stacked monthly bars over credit card
transactions and account debits together. See
[what changed](#whats-new-in-200) if you are coming from 1.x — three things behave differently for
an existing caller.

## Supported banks

| Bank | Sender code(s) | Transactions parsed |
|------|----------------|---------------------|
| HBL (Habib Bank) | `4250`, `14250` | credit card |
| Faysal Bank | `8756` | credit card |
| Standard Chartered | `7220`, `9220` | credit card |
| Meezan Bank | `8079`, `9779` | account debits — card purchases, ATM withdrawals, bill payments, funds transfers |

Every sender code is declared in exactly one place, `src/sms_msgs_scraper/parser/registry.py`, and
routing, the `--bank` choices, the parse summary and the verifier all derive from it.

Credit card transactions from the three CC banks are reported together, each tagged with its issuing
bank, and `--bank` narrows them to one. Meezan account debits are a different kind of transaction and
have their own two commands.

## Install

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # creates .venv and installs the project
uv run sms-txn --help
```

`uv run sms-txn` is the normal way to invoke it. `python -m sms_msgs_scraper` does the same thing
from a source checkout.

## Usage

```bash
uv run sms-txn [GLOBAL OPTIONS] <path_to_sms_backup.xml> <command> [OPTIONS]
```

### Credit card commands

| Command | Shows |
|---|---|
| `list_all_cc_txns` | every credit card transaction |
| `list_all_vendors` | unique vendors, sorted alphabetically |
| `monthly_cc_spending_summary` | month-by-month totals per currency |
| `cc_spend_for_month` | one month's total across every card and all three banks |

### Meezan account debit commands

| Command | Shows |
|---|---|
| `list_all_debit_txns` | every account debit transaction |
| `monthly_debit_spending_summary` | month-by-month totals per currency |

### Across both, at once

| Command | Shows |
|---|---|
| `monthly_vendor_chart` | stacked monthly bars, credit card **and** account debit together |

This is the only command that reads both stores. A merchant is a merchant, and which of the two
paid a bill is not something you should have to know before you can search for it — K-Electric is
only ever an account debit, a fuel station is only ever a card, and one command finds either.

```
         ██ ELECTRICITY — METER 1   ▓▓ ELECTRICITY — METER 2

  Jan 2025 ████████████████▓▓▓▓▓▓▓                         42,000.00
  Feb 2025 ███████████████▓▓▓▓▓▓                           38,500.00  ▼   8.3%
  Mar 2025 ██████████████▓▓▓▓▓                             35,000.00  ▼   9.1%
  Apr 2025 █████████████▓▓▓▓                               31,000.00  ▼  11.4%
  May 2025 █████████████████████████▓▓▓▓▓▓▓▓▓▓▓            68,000.00  ▲ 119.4%
  Jun 2025 ███████████████████████████████▓▓▓▓▓▓▓▓▓▓▓▓▓    82,000.00  ▲  20.6%
  Jul 2025 ············································   no transactions
  Aug 2025 ███████████████████████████▓▓▓▓▓▓▓▓▓▓▓          71,500.00
          └────────────────────────────────────────────
           0         20k        41k       62k       82k
```

*(Every figure in this README's sample output is invented. Nothing derived from a real backup is
committed to this repository — see [Notes](#notes).)*

Three things about it are deliberate:

- **Every month between the first and the last is drawn, including the empty ones.** July above is
  a row of dots, not a missing row. A chart that closes its gaps up shows June sitting next to
  August, and the shape of a time series is the entire reason to draw one.
- **Each currency gets its own chart.** One bar cannot mix PKR and USD without an exchange rate,
  and this tool does not have one.
- **Colour is never the only thing telling two series apart.** Each carries its own block glyph and
  a fixed position in the stack, and the exact figures are repeated in a totals table underneath —
  so the chart survives being piped to a file, rendered without colour, or read by someone who
  cannot distinguish two of the hues.

`--group-by` decides what each bar is split into:

| Value | Each bar is split by |
|---|---|
| `vendor` (default) | the distinct vendor strings the banks sent |
| `bank` | the issuing or debiting bank — HBL, FBL, SCB, MEZN |
| `txn-type` | `credit_card` plus the four account debit types |
| `none` | nothing; one solid bar per month |

At most four series are named and the rest become `Other`, because there are four series colours —
see [Notes](#notes) for why there cannot be a fifth. Under `--format json` or `--format csv` the
command writes the series behind the chart (`month, series, currency, amount`) with **every**
series named: the four-series cap is a readability limit of a terminal bar, and a consumer handed
`Other` could never recover what was in it.

### Command options

All commands except `cc_spend_for_month` accept an inclusive date range:

- `--from-date YYYY-MM-DD` — only transactions on or after this date
- `--to-date YYYY-MM-DD` — only transactions on or before this date

`cc_spend_for_month` takes `--month YYYY-MM` instead, and requires it: the month *is* the range.

`list_all_cc_txns`, `list_all_vendors` and `monthly_cc_spending_summary` also accept
`--bank {HBL|FBL|SCB}` (case-insensitive), and `list_all_debit_txns` accepts
`--txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}`.

**Every** command accepts the two vendor options:

- `--vendor TEXT` — only transactions whose vendor matches. Case-insensitive substring, tested
  against the vendor as the bank sent it **and** against its canonical name, so `--vendor PSO`
  finds every spelling the alias table groups under PSO, and `--vendor amazon` finds a transaction
  whose vendor string is `AMZN MKTP US`.
- `--canonical-vendors` — report each vendor under its canonical name. Off by default, so output
  carries the strings the banks actually sent.

The two monthly summary commands and `cc_spend_for_month` accept `--verbose` / `-v`, which also
lists the transactions the summary was built from. `monthly_vendor_chart` accepts
`--group-by {vendor|bank|txn-type|none}`.

When `list_all_cc_txns` or `list_all_debit_txns` is given a date range and/or `--vendor`, its table
output also carries an **Aggregate spend** block under the listing: one row per currency, with that
currency's transaction count and exact total. Currencies are never added together — a search that
spans PKR and USD has two answers, not one. JSON and CSV output is unchanged: their row shapes are
a contract, and a totals row smuggled into them would corrupt a naive reader.

### Global options

These go **before** the backup file path — a Click group stops parsing its own options at the first
positional argument.

| Option | Effect |
|---|---|
| `--format {table,json,csv}` | how results are written to stdout (default `table`) |
| `--quiet` / `-q` | suppress the header, parse summary and diagnostics on stderr |
| `--strict` | exit non-zero if any message could not be parsed |
| `--duplicates {exact,none,review}` | how a repeated message is treated (default `exact`) |
| `--vendor-map PATH` | a canonical-vendor table to use instead of the one shipped with the tool |
| `--no-color` | render without colour |

## Output

**stdout carries results; stderr carries everything else.** The rows you asked for go to stdout, and
the header, the parse summary, progress and parse diagnostics go to stderr. Piping the tool into
another program therefore yields data with no decoration in it, while you still see what happened:

```bash
uv run sms-txn --format csv backup.xml list_all_cc_txns > txns.csv   # only rows in the file
uv run sms-txn --format json backup.xml monthly_cc_spending_summary | jq '.rows[]'
```

JSON and CSV are schema-versioned, and amounts are emitted as **strings** rather than JSON numbers —
a JSON number is a float in almost every consumer, and re-introducing binary floating point on the
way out would undo the point of parsing exactly. CSV fields beginning `=`, `+`, `-` or `@` are
prefixed with a quote so a spreadsheet reads them as text rather than as a formula.

The table format is rendered with [Rich](https://rich.readthedocs.io/), colour-coded by bank,
currency and debit type. Colour is dropped automatically when output is piped or when `NO_COLOR` is
set.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | the backup file could not be read or parsed |
| 2 | usage error (a bad option or argument) |
| 3 | `--strict` was given and the run had something to report |

### Examples

```bash
# every CC transaction in 2024, across all three CC banks
uv run sms-txn backup.xml list_all_cc_txns --from-date 2024-01-01 --to-date 2024-12-31

# just Faysal Bank, month by month
uv run sms-txn backup.xml monthly_cc_spending_summary --bank FBL

# what went on the credit cards in March 2025, all banks together
uv run sms-txn backup.xml cc_spend_for_month --month 2025-03

# every ATM withdrawal since the start of 2025
uv run sms-txn backup.xml list_all_debit_txns --txn-type atm_withdrawal --from-date 2025-01-01

# machine-readable, quiet, straight into a file
uv run sms-txn --quiet --format csv backup.xml list_all_debit_txns > debits.csv

# every transaction at one merchant, however the banks spelled it
uv run sms-txn backup.xml list_all_cc_txns --vendor PSO

# what that merchant cost per month, under one name
uv run sms-txn backup.xml monthly_cc_spending_summary --vendor PSO --canonical-vendors
```

## Tracking one merchant over time

This is the workflow `monthly_vendor_chart` was built for: *how has my electricity bill moved over
the year?* It takes three steps, and the middle one is the part that is easy to skip and then get
wrong.

### 1. Find out how the bank actually spells it

You cannot search for a string you have not seen. **The banks do not send the name you would use** —
they send whatever the acquirer or the biller put in the message, often with an account number, a
station number or a payment channel glued on.

Which command lists them depends on which store the merchant is in, and this is the one asymmetry
worth knowing about:

```bash
# credit card vendors -- a first-class command
uv run sms-txn --quiet --format csv backup.xml list_all_vendors > cc_vendors.csv

# account debit vendors -- there is no list_all_debit_vendors, so take the column
uv run sms-txn --quiet --format csv backup.xml list_all_debit_txns \
    | cut -d, -f4 | tail -n +2 | sort -u > debit_vendors.csv
```

A utility bill is an account debit, so it is the second list it appears in. Say it turns up as six
strings — two meters, each paid through three different channels:

```
KE 0400000000001 ATM        KE 0400000000002 ATM
KE 0400000000001 FROM IB    KE 0400000000002 FROM IB
KE 0400000000001 FROM MB    KE 0400000000002 FROM MB
```

Chart that as it stands and you get six series for what is one bill — and because the chart names
only four, two of them fold into `Other`. Worse, a month paid from the app and a month paid at an
ATM land in *different series*, so the same bill changes colour between months for a reason that has
nothing to do with the money. That is what step 2 is for.

### 2. Write down that those strings are one merchant

Add an entry to your `--vendor-map` file (see [Canonical vendor names](#canonical-vendor-names)
below for the full schema). Within one meter the consumer number is fixed and only the channel
suffix varies — and a suffix has no fixed set you could enumerate — so a `prefix` alias stopping at
the end of the consumer number is the narrowest thing that covers all three spellings:

```json
{
  "schemaVersion": 1,
  "canonicalVendors": {
    "ELECTRICITY — METER 1": {
      "note": "Consumer number is fixed; the suffix is the payment channel.",
      "prefix": ["KE 0400000000001"]
    },
    "ELECTRICITY — METER 2": {
      "prefix": ["KE 0400000000002"]
    }
  }
}
```

One entry claiming both meters — `"prefix": ["KE 04000000000"]` under a single
`ELECTRICITY` name — gives you the combined bill instead. Which you want is a real choice: two
entries chart the meters as separate stacked segments, one entry charts their sum.

### 3. Chart it

```bash
uv run sms-txn --vendor-map ./vendor_aliases.local.json backup.xml \
    monthly_vendor_chart --vendor ELECTRICITY --canonical-vendors --from-date 2025-01-01
```

`--vendor ELECTRICITY` finds the transactions — matching works against the canonical name as well as
the raw string, so the name you invented in step 2 is a name you can search by. `--canonical-vendors`
is what makes them *render* under that name; without it the chart still finds the right transactions
but splits them back into the six raw strings.

```
         ██ ELECTRICITY — METER 1   ▓▓ ELECTRICITY — METER 2

  Jan 2025 ████████████████▓▓▓▓▓▓▓                         42,000.00
  Feb 2025 ███████████████▓▓▓▓▓▓                           38,500.00  ▼   8.3%
  Mar 2025 ██████████████▓▓▓▓▓                             35,000.00  ▼   9.1%
  Apr 2025 █████████████▓▓▓▓                               31,000.00  ▼  11.4%
  May 2025 █████████████████████████▓▓▓▓▓▓▓▓▓▓▓            68,000.00  ▲ 119.4%
  Jun 2025 ███████████████████████████████▓▓▓▓▓▓▓▓▓▓▓▓▓    82,000.00  ▲  20.6%
  Jul 2025 ············································   no transactions
  Aug 2025 ███████████████████████████▓▓▓▓▓▓▓▓▓▓▓          71,500.00
          └────────────────────────────────────────────
           0         20k        41k       62k       82k

PKR totals
┌─────────────────────────┬──────┬────────────┐
│ Vendor                  │ Txns │      Total │
├─────────────────────────┼──────┼────────────┤
│ █ ELECTRICITY — METER 1 │    7 │ 263,000.00 │
│ ▓ ELECTRICITY — METER 2 │    7 │ 105,000.00 │
├─────────────────────────┼──────┼────────────┤
│ TOTAL                   │   14 │ 368,000.00 │
└─────────────────────────┴──────┴────────────┘
```

The totals table under every chart is not decoration: the bars encode proportion, and this is where
you read the figure without measuring it off the axis. It is also what keeps the chart legible to
someone who cannot tell two of the series colours apart.

Three things to read carefully in that output:

- **July is a row of dots, not a missing row.** No bill was paid that month. The chart draws every
  month between the first and the last precisely so a gap cannot be mistaken for continuity.
- **The month after a gap has no percentage.** August is not compared against June — there is no
  honest change to state across a month that has no value at all.
- **The bars are dated by payment, not by billing period.** A December bill paid on 2 January is a
  January bar. The messages carry no billing period, so the tool cannot know one.

### Getting the numbers out

The chart is for reading. For anything downstream, ask for the series behind it:

```bash
uv run sms-txn --quiet --format csv --vendor-map ./vendor_aliases.local.json backup.xml \
    monthly_vendor_chart --vendor ELECTRICITY --canonical-vendors --from-date 2025-01-01
```

```
month,series,currency,amount
2025-01,ELECTRICITY — METER 1,PKR,30000.00
2025-01,ELECTRICITY — METER 2,PKR,12000.00
2025-02,ELECTRICITY — METER 1,PKR,28000.00
2025-02,ELECTRICITY — METER 2,PKR,10500.00
```

One row per month per series per currency, amounts as exact strings. Months with no transactions
carry **no rows** — a drawn gap and an asserted zero are different claims, and only one of them is
true here. And unlike the chart, the machine formats never fold anything into `Other`.

### Other things the same command answers

```bash
# where did the money go this year -- one bar per month, split by bank
uv run sms-txn backup.xml monthly_vendor_chart --group-by bank --from-date 2025-01-01

# cards vs cash vs bills vs transfers, over time
uv run sms-txn backup.xml monthly_vendor_chart --group-by txn-type

# total monthly outgoings, one solid bar, nothing split out
uv run sms-txn backup.xml monthly_vendor_chart --group-by none

# one merchant, no map needed, however the bank spelled it
uv run sms-txn backup.xml monthly_vendor_chart --vendor PSO
```

`--vendor` is optional throughout: with it you get one merchant, without it you get everything that
matched the other filters.

## Canonical vendor names

A bank's alert carries whatever string the acquirer put in the transaction, and that string is not
stable for one merchant. The reference backup holds ten spellings of what a person would call "the
PSO station":

```
PSO SERVICE STATION 7             PSO SERVICE STATION 25
PSO SERVICE STATION 7 A           PSO SERVICE STATION 25 KARACHI PAK
PSO SERVICE STATION 7 Karachi PAK PSO SERVICE STATION 25 Karachi PAK
PSO SERVICE STATION 7Karachi PAK  PSO SERVICE STATION 23 Karachi PAK
PSO SERVICE STATION               PSO SERVICE STATION KARACHI PAK
```

Three things vary independently in there: a trailing city and country that is sometimes absent,
that suffix's case, and whether there is a space before it at all. Other merchants add a fourth —
the bank truncates the name mid-word, so `SHELL (CREEK SERVICE S` is all that ever arrives.

**None of that is guessed at.** No suffix stripping, no fuzzy matching, no "looks like a city"
rule: each of those silently mis-attributes spending the first time a real merchant name happens to
end in something that looks like a city. Grouping happens only where a person wrote down that two
names are one merchant. What is normalized is only what cannot carry meaning: case, and runs of
internal whitespace.

`src/sms_msgs_scraper/data/vendor_aliases.json` ships the **mechanism, not anyone's data** — three
worked examples of the two alias forms, so the file documents its own schema. It claims no real
merchant, and `tests/test_vendors.py` fails if an entry appears there that is not marked EXAMPLE.
Your own table is a private file you pass to `--vendor-map`: the list of merchants, schools,
hospitals and utilities someone actually pays is a map of their life, and it belongs nowhere near a
public repository — the same reason the reference backup is never committed.

```json
{
  "schemaVersion": 1,
  "canonicalVendors": {
    "EXAMPLE FUEL": {
      "note": "One station, spelled several ways.",
      "prefix": ["EXAMPLE FUEL SERVICE STATION"],
      "exact": ["EXAMPLE FUEL DEPOT"]
    },
    "EXAMPLE UTILITY": {
      "note": "Bills embed their own consumer number, so no exact list is possible.",
      "prefix": ["EXAMPLE UTILITY"]
    }
  }
}
```

### The file format

| Key | Where | Required | Meaning |
|---|---|---|---|
| `schemaVersion` | top level | **yes** | must be `1`; a different value is refused rather than guessed at |
| `canonicalVendors` | top level | **yes** | an object whose *keys* are the canonical names you invented |
| `exact` | in an entry | one of the two | full vendor strings, matched in their entirety |
| `prefix` | in an entry | one of the two | leading fragments; any vendor starting with one matches |
| `note` | in an entry | no | why these strings are one merchant — JSON has no comments, and a grouping made on a judgement should say so |

The canonical name is **whatever you want the output to say**. It is not looked up anywhere, it does
not have to resemble the raw strings, and it is what `--canonical-vendors` prints and what
`--vendor` can search by. Only `exact`, `prefix` and `note` are allowed inside an entry — a
misspelled key is an error, because it would otherwise read as "this entry has no aliases" and the
grouping would silently not happen.

### The two alias forms

An alias is either an `exact` full vendor string or a `prefix` of one.

**The prefix form is not a convenience.** A utility bill embeds its own consumer number and a
bank-truncated name has no fixed ending, so neither can be enumerated exhaustively — `SHELL (CREEK
SERVICE S` is all that ever arrives, and there is no complete list of what follows it. Use `exact`
when you can see every spelling and they are all in front of you; use `prefix` when the tail varies
with something you do not control.

**Prefer the narrowest prefix that covers the family.** A broad one silently claims merchants that
merely start with the same word, and mis-attributed spending looks exactly like real spending.
`SHELL` is a prefix of a great many things that are not the fuel brand.

**A trailing space does not anchor a prefix.** Aliases are normalized before they are stored, and
normalization strips leading and trailing whitespace — so `"prefix": ["KE "]` is stored as `ke` and
claims `KENTUCKY...` and `KEENU...` along with the electricity bill. To anchor on a word boundary,
include the next thing that is actually stable: a digit of the account number, or the whole word.

### The rules the loader enforces

Loading is strict, and deliberately so: **every way an alias table can be subtly wrong is silent.**
A misspelled key, an alias two names both claim, an entry with no aliases — none of those raise on
their own. They just group less than the file appears to say, and the totals still look plausible.

- **A more specific alias wins.** Prefixes are tried longest-first, so a map may claim
  `PSO SERVICE STATION` for `PSO` and then carve `PSO SERVICE STATION 25` back out under its own
  name. The refinement is deterministic, not a conflict.
- **Two canonical names may not claim one alias.** Refused outright rather than resolved by file
  order — that is a question the file does not answer, and either answer would move real money into
  the wrong bucket.
- **An entry with neither `exact` nor `prefix` is refused.** It groups nothing.
- **Two canonical names differing only in case or spacing are refused**, because lookups fold both
  and could never tell them apart. A name is *matched* case-insensitively but *rendered* exactly as
  you wrote it.
- **An empty alias is refused.** It would claim every vendor.

Case and runs of internal whitespace are folded on both sides of a comparison, and **nothing else
is**. A trailing city, a glued-on suffix and a truncation are exactly what an alias is for.

### Building your own

Start from the strings the banks actually sent — that is what an alias has to match, and guessing at
it is how a dead entry gets written. Note that `list_all_vendors` covers **credit card vendors
only**; account debit vendors come out of the transaction listing:

```bash
# credit card
uv run sms-txn --quiet --format csv backup.xml list_all_vendors > cc_vendors.csv

# account debit -- there is no list_all_debit_vendors
uv run sms-txn --quiet --format csv backup.xml list_all_debit_txns \
    | cut -d, -f4 | tail -n +2 | sort -u > debit_vendors.csv

# then check what your table actually collapses
uv run sms-txn --vendor-map ./vendor_aliases.local.json backup.xml \
    list_all_vendors --canonical-vendors
```

`vendor_aliases.local.json` in the repository root is gitignored and is where
`scripts/verify_against_backup.py` looks. When it is there, the verifier checks it against the
corpus and fails on:

- **a dead alias** — one that claims no real vendor, written against a string the banks stopped
  sending or simply mistyped, which would otherwise sit there looking like it grouped something;
- **a canonical name collapsing fewer than two spellings** — if it only ever matches one string it
  is renaming, not grouping, which may be what you want but is worth stating deliberately;
- **two canonical names claiming one alias**, the same conflict the loader refuses.

With no such file the packaged examples are reported and nothing is asserted, since they match
nothing anywhere by design.

**Canonicalization never changes an amount, a transaction count or a total** — it only changes what
the output calls things, and `tests/test_vendor_filter.py` pins that. It is also opt-in: without
`--canonical-vendors`, every command reports the strings the banks sent.

## What's new in 2.4.0

`monthly_vendor_chart`: stacked monthly bars over credit card transactions and account debits
together, so a merchant can be charted without knowing which of the two stores paid it.

```bash
uv run sms-txn --vendor-map ./vendor_aliases.local.json backup.xml \
    monthly_vendor_chart --vendor K-Electric --canonical-vendors --from-date 2025-01-01
```

Purely additive — a new command, reachable only by naming it. Every existing command, option,
default and output stream is exactly what 2.3.0 shipped, which is what makes this a MINOR release.

## What's new in 2.3.0

A filtered listing now answers the question that always follows it. When `list_all_cc_txns` or
`list_all_debit_txns` is narrowed by a date range, a vendor needle, or both, the table output adds
an **Aggregate spend** block — one exact total per currency over the matching transactions — so
"how much did I pay K-Electric this quarter?" no longer means summing a column by eye:

```bash
uv run sms-txn --vendor-map ./vendor_aliases.local.json backup.xml \
    list_all_debit_txns --vendor K-Electric --from-date 2025-01-01 --to-date 2025-03-31
```

An unfiltered listing renders exactly what 2.2.0 rendered, and the JSON and CSV row shapes are
untouched in every case, which is what makes this a MINOR release. Totals over *everything* remain
the summary commands' job.

## What's new in 2.2.0

Vendor search and canonical vendor names. `--vendor TEXT` finds every transaction at one merchant
across all four banks; `--canonical-vendors` reports the spellings under one name; `--vendor-map`
replaces the shipped table with your own.

Canonicalization is **opt-in**, which is what makes this a minor release: a run that does not ask
for it sees the same vendor strings, the same row counts and the same totals as 2.1.1, and every
existing figure in the reference table is unchanged.

The packaged table ships worked examples only. A real one is a private file passed to
`--vendor-map`, because a list of the merchants, schools, hospitals and utilities someone pays is a
map of their life and this repository is public.

## What's new in 2.1.0

`cc_spend_for_month --month YYYY-MM` answers one question directly: what went on the credit cards
that month, across every card and all three banks. The table's TOTAL row is the answer — one exact
total per currency — and the rows above it say which bank each part of it came from.

Nothing else changed. Every existing invocation behaves exactly as it did in 2.0.0, which is what
makes this a MINOR release.

## What's new in 2.0.0

Three changes affect an existing caller, which is what makes this a MAJOR release:

1. **Results moved to stdout, diagnostics to stderr.** Anything that used to scrape the tool's
   combined output will now see only data.
2. **The tool is installed as `sms-txn`** rather than run as a script path.
3. **Standard Chartered totals changed.** SCB sends from two short codes and only one was declared,
   so 18 transactions — PKR 94,294.32 — were counted as messages from an unrecognized sender and
   never parsed. They are now included, and every SCB total is correspondingly higher.

Also new: `--format json|csv`, `--quiet`, `--strict`, `--duplicates`, exact decimal arithmetic
throughout, and a deterministic output order.

## Notes

- **SMS backup files are personal financial data.** `*.xml` is gitignored; keep it that way. Parse
  diagnostics identify a message by its sender and received time, never by quoting its body.
- **Nothing derived from a real backup is committed**, and that includes documentation. Every vendor
  string, account number and amount in this README's sample output is invented — the charts above
  were rendered by the real renderer from made-up figures, so the bars are honest without the
  numbers being anyone's. Your own alias table is the same: it lives in `vendor_aliases.local.json`,
  which is gitignored, because a list of the merchants, schools, hospitals and utilities someone
  pays is a map of their life.
- All timestamps in a backup are assumed to be **Asia/Karachi** local time — the XML carries no
  offset.
- **Money is exact.** Amounts are captured as `Decimal` straight from the matched token and never
  pass through a float. Aggregating as binary floating point drifted 21 of 93 bank/month/currency
  buckets in the reference corpus, and `,.2f` hid every one of them.
- Duplicate detection keys on the **sender plus the message body**, so a repeat from the same short
  code collapses into one transaction and an unrelated sender can never suppress a bank message. Two
  identical HBL or SCB purchases on the same day are indistinguishable from a retransmission, since
  those bodies carry no time of day; FBL and Meezan bodies do, so dedup is exact for them. The report
  counts the ambiguous suppressions rather than pretending the question does not arise. See
  `CLAUDE.md` for the measurements behind that.
- **A chart names at most four series**, and the rest become `Other`. The cap is a colour limit, not
  a layout preference: a terminal background may be near-black or near-white, so a series colour has
  to sit in the lightness band that reads against both, and that band is too narrow to hold a fifth
  well-separated hue. The four were computed rather than picked by eye — worst separation under
  simulated protanopia and deuteranopia 14.6 in OKLab dE x100 against a target of 8, every pair,
  both backgrounds. Reusing a colour for a fifth series would give two series one identity, which is
  worse than saying `Other`; the machine formats never fold, so nothing is actually lost.
- **A chart dates a transaction by when the bank sent the alert**, which for a bill is when it was
  *paid* rather than the period it covers. A December electricity bill paid on 2 January is a
  January bar — the messages carry no billing period, so the tool cannot know one.

## Development

Three checks verify a change. Run them in this order — fastest first.

### 1. Lint and type check

```bash
uv run --with ruff ruff check .
uv run --with pyright pyright
```

### 2. The test suite

```bash
uv run python -m unittest discover -s tests/ -v
```

Runs from the repository root. 435 tests over hand-built messages and a committed synthetic corpus:
each bank's identification, extraction and skip paths, message routing and deduplication, exact
decimal arithmetic, the CLI's options, filters, formats and exit codes, the alias table's loading
rules, the chart's geometry and series selection, hostile input, and `python -O` parity.

The synthetic corpus (`tests/fixtures/synthetic_backup.xml`) is entirely invented data using the
banks' real message templates. It covers every template family and every skip path, so this suite —
unlike the one below — can run anywhere, including CI.

### 3. Verification against a real backup

```bash
uv run python scripts/verify_against_backup.py            # the reference backup, in the repo root
uv run python scripts/verify_against_backup.py other.xml  # or any other backup
```

Run this **after any change to a parser, to the message routing, or to the deduplication identity**,
and before releasing. The suite proves the parsers handle messages you thought of; this proves they
still handle ~4,700 real ones.

It performs four kinds of check:

- **Discovery**, which finds what routing missed. It scans *every* sender in the file for the banks'
  own transaction signatures **without consulting the registry's sender list**, and fails when a
  signature turns up on a sender no bank claims. This is the check that the previous version lacked,
  and its absence is why a green verifier certified a missing sender for the life of the project.
- **Invariants**, asserted for any backup: the conservation identity, exact envelope accounting, and
  the structural properties of every transaction.
- **Expected values**, tied to one backup by its SHA-256 — counts, and **exact per-bank
  per-currency totals**. Against a different backup they cannot mean anything, so it prints that
  backup's numbers and asserts only discovery and the invariants.
- **Vendor map liveness**, when `vendor_aliases.local.json` is present: every alias must claim at
  least one real vendor and every canonical name must collapse two or more spellings, so a dead or
  mistyped entry is reported rather than sitting there looking like it grouped something. Only alias
  and canonical *names* are printed, never a vendor string out of the corpus.

Output is counts and totals only, never message contents.

If an expected value misses, fix the code, or re-derive the expectation from the corpus and record
that derivation in the **Reference numbers** table in `CLAUDE.md`. Never edit a number in the script
to match what the code printed: that turns the one check that would have caught a regression into a
rubber stamp.

### Further reading

`CLAUDE.md` documents the architecture, the message formats each parser handles, the reference
transaction counts to gate changes against, and the known limitations.
`docs/ADVERSARIAL_REMEDIATION_PLAN.md` records what 2.0.0 set out to fix and why.
