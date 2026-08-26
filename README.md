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
uv run src/hbl_sms_query_tool.py <path_to_sms_backup.xml> <command> [OPTIONS]
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
uv run src/hbl_sms_query_tool.py backup.xml list_all_cc_txns \
    --from-date 2024-01-01 --to-date 2024-12-31

# just Faysal Bank, month by month
uv run src/hbl_sms_query_tool.py backup.xml monthly_cc_spending_summary --bank FBL

# every ATM withdrawal since the start of 2025
uv run src/hbl_sms_query_tool.py backup.xml list_all_debit_txns \
    --txn-type atm_withdrawal --from-date 2025-01-01
```

## Notes

- **SMS backup files are personal financial data.** `*.xml` is gitignored; keep it that way.
- All timestamps in a backup are assumed to be **Asia/Karachi** local time — the XML carries no
  offset.
- Duplicate detection hashes the message body alone, so two identical repeats collapse into one
  transaction. See `CLAUDE.md` for the accepted limitations that follow from this.

## Development

```bash
# run the test suite (from src/ — imports resolve relative to it)
cd src && uv run python -m unittest discover -s ../tests/ -v
```

`CLAUDE.md` documents the architecture, the message formats each parser handles, the reference
transaction counts to gate changes against, and the known limitations.
