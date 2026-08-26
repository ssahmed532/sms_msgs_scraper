# sms_msgs_scraper

A Python CLI tool for extracting and summarizing bank transactions from my SMS messages.

It reads an Android SMS backup file (the XML that the *SMS Backup & Restore* app produces),
identifies transaction alerts by sender short code, parses them into transactions, deduplicates
repeats, and reports them — as listings, as unique vendor lists, or as month-by-month spending
totals broken down by currency.

## Supported banks

| Bank | Sender code(s) | Transactions parsed |
|------|----------------|---------------------|
| HBL (Habib Bank) | `4250`, `14250` | credit card |
| Faysal Bank | `8756` | credit card |
| Standard Chartered | `7220` | credit card |
| Meezan Bank | `8079`, `9779` | account debits — card purchases, ATM withdrawals, bill payments, funds transfers |

Credit card transactions from the three CC banks are reported together, each tagged with its issuing
bank, and `--bank` narrows them to one. Meezan account debits are a different kind of transaction and
have their own two commands.

## Usage

Dependencies are managed with [uv](https://docs.astral.sh/uv/); `uv run` creates the virtual
environment on first use, so there is nothing to install by hand.

```bash
uv run src/sms_txn_query_tool.py <path_to_sms_backup.xml> <command> [OPTIONS]
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

### Options

All five commands accept an inclusive date range:

- `--from-date YYYY-MM-DD` — only transactions on or after this date
- `--to-date YYYY-MM-DD` — only transactions on or before this date

The three credit card commands also accept `--bank {HBL|FBL|SCB}` (case-insensitive), and
`list_all_debit_txns` accepts
`--txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}`.

### Examples

```bash
# every CC transaction in 2024, across all three CC banks
uv run src/sms_txn_query_tool.py backup.xml list_all_cc_txns \
    --from-date 2024-01-01 --to-date 2024-12-31

# just Faysal Bank, month by month
uv run src/sms_txn_query_tool.py backup.xml monthly_cc_spending_summary --bank FBL

# every ATM withdrawal since the start of 2025
uv run src/sms_txn_query_tool.py backup.xml list_all_debit_txns \
    --txn-type atm_withdrawal --from-date 2025-01-01
```

## Notes

- **SMS backup files are personal financial data.** `*.xml` is gitignored; keep it that way.
- All timestamps in a backup are assumed to be **Asia/Karachi** local time — the XML carries no
  offset.
- Duplicate detection keys on the **sender plus the message body**, so a repeat from the same short
  code collapses into one transaction and an unrelated sender can never suppress a bank message. Two
  identical HBL or SCB purchases on the same day are indistinguishable from a retransmission, since
  those bodies carry no time of day; FBL and Meezan bodies do, so dedup is exact for them. See
  `CLAUDE.md` for the measurements behind that.

## Development

Two commands verify a change, and they catch different things. Run them in this order — the fast one
first, then the slow one before committing.

### 1. The test suite

```bash
cd src && uv run python -m unittest discover -s ../tests/ -v
```

**Must be run from `src/`.** There is no installable package, so imports resolve relative to that
directory; running it from the repo root fails with `ModuleNotFoundError`.

120 tests over hand-built messages: each bank's identification, extraction and skip paths, message
routing and deduplication, the CLI's options and filters. Fast (well under a second), so run it
freely while working.

### 2. Verification against a real backup

```bash
# the reference backup, expected in the repo root
uv run scripts/verify_against_backup.py

# ...or any other backup file
uv run scripts/verify_against_backup.py path/to/sms-backup.xml
```

Run this **after any change to a parser, to the message routing, or to the deduplication identity**,
and before committing such a change. Run it from the repo root (unlike the suite).

The suite proves the parsers handle messages you thought of; this proves they still handle ~4,700
real ones. It is what notices a regex change that passes every unit test while quietly dropping 40
real transactions.

It checks two different things:

- **Invariants**, asserted for *any* backup: every counted message lands in exactly one bucket, and
  every transaction has a non-empty vendor, a positive amount, a timezone-aware Asia/Karachi date, a
  known bank, and a known debit type. These catch what totals cannot — 583 transactions with empty
  vendors would satisfy every expected count.
- **Expected counts**, tied to one backup file by its SHA-256. Against that file they are asserted
  exactly. Against a different backup they cannot mean anything, so it prints that backup's numbers
  as a baseline and asserts only the invariants.

It ends in `RESULT: PASS` or `RESULT: FAIL` with the offending metric's expected-vs-actual. Output is
counts only — never message contents — because a backup is personal financial data.

If an expected count misses, fix the code, or re-derive the expectation from the corpus and record
that derivation in `MULTI_BANK_PARSERS_PLAN.md`. Never edit a number in the script to match what the
code printed: that turns the one check that would have caught a regression into a rubber stamp.

### Further reading

`CLAUDE.md` documents the architecture, the message formats each parser handles, the reference
transaction counts to gate changes against, and the known limitations.
