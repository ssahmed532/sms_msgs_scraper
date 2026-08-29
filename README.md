# sms_msgs_scraper

A Python CLI tool for extracting and summarizing bank transactions from my SMS messages.

It reads an Android SMS backup file (the XML that the *SMS Backup & Restore* app produces),
identifies transaction alerts by sender short code, parses them into transactions, deduplicates
repeats, and reports them — as listings, as unique vendor lists, or as month-by-month spending
totals broken down by currency.

**Version 2.1.0**, which adds `cc_spend_for_month`. See [what changed](#whats-new-in-200) if you
are coming from 1.x — three things behave differently for an existing caller.

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
lists the transactions the summary was built from.

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
      "prefix": ["EXAMPLE UTILITY "]
    }
  }
}
```

An alias is either an `exact` full vendor string or a `prefix` of one. The prefix form is not a
convenience: an SSGC bill embeds its own consumer number and a truncated name has no fixed ending,
so neither can be enumerated exhaustively. A more specific alias wins over a broader one, so a map
may claim a whole brand and then carve one station back out of it. Two canonical names claiming one
alias is refused outright rather than resolved by ordering — that is a question the file does not
answer, and either answer would move real money into the wrong bucket.

To build your own table, start from the list of spellings the banks actually sent — that is what
an alias has to match, and guessing at it is how a dead entry gets written:

```bash
uv run sms-txn --quiet --format csv backup.xml list_all_vendors > vendors.csv
uv run sms-txn --vendor-map ./vendor_aliases.local.json backup.xml list_all_vendors --canonical-vendors
```

`vendor_aliases.local.json` in the repository root is gitignored and is where
`scripts/verify_against_backup.py` looks. When it is there, the verifier checks it against the
corpus: every alias must claim at least one real vendor, and every canonical name must collapse two
or more spellings. An alias that matches nothing is dead config — written against a string the banks
stopped sending, or mistyped — and it would otherwise sit there looking like it grouped something.
With no such file, the packaged examples are reported and nothing is asserted, since they match
nothing anywhere by design.

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

Runs from the repository root. 283 tests over hand-built messages and a committed synthetic corpus:
each bank's identification, extraction and skip paths, message routing and deduplication, exact
decimal arithmetic, the CLI's options, filters, formats and exit codes, hostile input, and
`python -O` parity.

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

It performs three kinds of check:

- **Discovery**, which finds what routing missed. It scans *every* sender in the file for the banks'
  own transaction signatures **without consulting the registry's sender list**, and fails when a
  signature turns up on a sender no bank claims. This is the check that the previous version lacked,
  and its absence is why a green verifier certified a missing sender for the life of the project.
- **Invariants**, asserted for any backup: the conservation identity, exact envelope accounting, and
  the structural properties of every transaction.
- **Expected values**, tied to one backup by its SHA-256 — counts, and **exact per-bank
  per-currency totals**. Against a different backup they cannot mean anything, so it prints that
  backup's numbers and asserts only discovery and the invariants.

Output is counts and totals only, never message contents.

If an expected value misses, fix the code, or re-derive the expectation from the corpus and record
that derivation in the **Reference numbers** table in `CLAUDE.md`. Never edit a number in the script
to match what the code printed: that turns the one check that would have caught a regression into a
rubber stamp.

### Further reading

`CLAUDE.md` documents the architecture, the message formats each parser handles, the reference
transaction counts to gate changes against, and the known limitations.
`docs/ADVERSARIAL_REMEDIATION_PLAN.md` records what 2.0.0 set out to fix and why.
