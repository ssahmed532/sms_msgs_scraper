# sms_msgs_scraper

A Python CLI tool for extracting and summarizing bank transactions from my SMS messages.

It reads an Android SMS backup file (the XML that the *SMS Backup & Restore* app produces),
identifies transaction alerts by sender short code, parses them into transactions, deduplicates
repeats, and reports them — as listings, as unique vendor lists, or as month-by-month spending
totals broken down by currency.

**Version 2.0.0.** See [what changed](#whats-new-in-200) if you are coming from 1.x — three things
behave differently for an existing caller.

## Supported banks

| Bank | Sender code(s) | Transactions parsed |
|------|----------------|---------------------|
| HBL (Habib Bank) | `4250`, `14250` | credit card |
| Faysal Bank | `8756` | credit card |
| Standard Chartered | `7220`, `9220` | credit card |
| Meezan Bank | `8079`, `9779` | account debits — card purchases, ATM withdrawals, bill payments, funds transfers |

Every sender code is declared in exactly one place, `src/sms_msgs_scraper/domain/registry.py`, and
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

### Meezan account debit commands

| Command | Shows |
|---|---|
| `list_all_debit_txns` | every account debit transaction |
| `monthly_debit_spending_summary` | month-by-month totals per currency |

### Command options

All five commands accept an inclusive date range:

- `--from-date YYYY-MM-DD` — only transactions on or after this date
- `--to-date YYYY-MM-DD` — only transactions on or before this date

The three credit card commands also accept `--bank {HBL|FBL|SCB}` (case-insensitive), and
`list_all_debit_txns` accepts
`--txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}`.

The two monthly summary commands accept `--verbose` / `-v`, which also lists the transactions the
summary was built from.

### Global options

These go **before** the backup file path — a Click group stops parsing its own options at the first
positional argument.

| Option | Effect |
|---|---|
| `--format {table,json,csv}` | how results are written to stdout (default `table`) |
| `--quiet` / `-q` | suppress the header, parse summary and diagnostics on stderr |
| `--strict` | exit non-zero if any message could not be parsed |
| `--duplicates {exact,none,review}` | how a repeated message is treated (default `exact`) |
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

# every ATM withdrawal since the start of 2025
uv run sms-txn backup.xml list_all_debit_txns --txn-type atm_withdrawal --from-date 2025-01-01

# machine-readable, quiet, straight into a file
uv run sms-txn --quiet --format csv backup.xml list_all_debit_txns > debits.csv
```

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
