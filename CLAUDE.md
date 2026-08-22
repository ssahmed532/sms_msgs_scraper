# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMS Messages Scraper — A Python CLI tool for parsing and analyzing credit card transaction SMS messages from Pakistani banks. It processes Android SMS backup files (XML format from the "SMS Backup & Restore" app) to extract, deduplicate, and summarize financial transactions.

Currently only **HBL** (Habib Bank Limited) CC transaction messages are fully parsed. Faysal Bank, Standard Chartered, and Meezan Bank messages are counted but not parsed into transactions.

**Version:** 0.2.0 (declared in `hbl_sms_query_tool.py` via `@click.version_option`)

## Development Commands

Dependencies are managed with **uv**. `uv run` creates/syncs `.venv` automatically — there is no
manual `pip install` or activation step.

### Running Tests
```bash
# Run all tests (must run from src/ directory — imports resolve relative to it)
cd src
uv run python -m unittest discover -s ../tests/ -v

# Run a single test file
cd src
uv run python -m unittest discover -s ../tests/ -p "test_hbl_sms_parser.py" -v
```

### Running the CLI Tool
```bash
# from the repo root
uv run src/hbl_sms_query_tool.py <path_to_sms_backup.xml> <command> [OPTIONS]

# Available commands:
#   list_all_vendors            - List unique vendors from CC transactions (sorted alphabetically)
#   list_all_cc_txns            - List all credit card transactions
#   monthly_cc_spending_summary - Month-wise spending aggregation broken down by currency (PKR/USD/CAD)
#
# All three accept an optional date range filter:
#   --from-date YYYY-MM-DD      - only txns on or after this date (inclusive)
#   --to-date   YYYY-MM-DD      - only txns on or before this date (inclusive)
```
```bash
uv run src/hbl_sms_query_tool.py backup.xml list_all_cc_txns --from-date 2024-01-01 --to-date 2024-12-31
uv run src/hbl_sms_query_tool.py backup.xml monthly_cc_spending_summary --from-date 2025-01-01
```

### Dependency Management
```bash
uv sync                 # install/refresh .venv from uv.lock
uv lock --upgrade       # re-resolve within pyproject.toml constraints
```

### Dependencies
- `click==8.4.2` — CLI framework
- `tzdata==2026.3` — **required on Windows.** Windows ships no system IANA tz database and
  uv-managed interpreters bundle none, so the module-scope `ZoneInfo("Asia/Karachi")` in
  `common.py` / `cc_txn.py` raises `ZoneInfoNotFoundError` without it. Bump periodically — this is
  the tz rule database, not just a library.
- Standard library only otherwise (`xml.etree.ElementTree`, `hashlib`, `datetime`, `re`, `dataclasses`, `zoneinfo`, `collections`, `pathlib`, `pprint`, `time`)

## Architecture

### Directory Structure
```
sms_msgs_scraper/
├── CLAUDE.md
├── README.md
├── pyproject.toml                # uv project metadata + pinned deps ([tool.uv] package = false)
├── uv.lock                       # committed — the reproducibility guarantee
├── .python-version               # 3.14
├── .gitignore                    # ignores .venv/, __pycache__/, *.xml (SMS backups are personal data)
├── src/
│   ├── hbl_sms_query_tool.py    # CLI entry point (Click-based)
│   ├── sms_backup_file_parser.py # Root-level orchestrator
│   ├── cc_txn.py                 # Transaction data model (CreditCardTxnDC dataclass)
│   ├── common.py                 # Enums (Currency, SpendingCategories), constants (DEFAULT_TZ)
│   ├── parser/
│   │   └── hbl_sms_parser.py     # HBL-specific SMS parsing (regex-based)
│   └── IMPROVEMENTS.md           # Codebase audit doc with known issues
└── tests/
    ├── test_hbl_sms_parser.py         # Unit tests for HBL parser identification + extraction methods
    ├── test_date_range_filter.py      # Unit tests for the CLI --from-date/--to-date filter
    ├── test_sms_backup_file_parser.py # End-to-end tests for SmsBackupFileParser (dedup, routing, MMS skip)
    └── test_cli_commands.py           # Guards the explicit underscore subcommand names
```

No `__init__.py` files and no importable package — `pyproject.toml` sets `[tool.uv] package = false`
and declares dependencies only. Imports still rely on `src/` being on `sys.path`, which happens
because that is where the executed script lives.

### Module Relationships
```
hbl_sms_query_tool.py (CLI entry point via Click)
    ├── common.py (Currency enum)
    ├── cc_txn.py (CreditCardTxnDC dataclass)
    └── sms_backup_file_parser.py (orchestrator)
            └── parser/hbl_sms_parser.py (HBL-specific parsing)
                    └── cc_txn.py (CreditCardTxnDC, CurrencyAmountTuple)
```

### Key Components

**`hbl_sms_query_tool.py`** — Click CLI entry point. Defines a `@click.group()` with a required `filepath` argument and three subcommands. Uses a **global** `smsParser` variable shared between the group callback and subcommands. Measures parse time with `perf_counter`. Only catches `PermissionError` from file loading.

**`SmsBackupFileParser`** (`sms_backup_file_parser.py`) — Orchestrator class. Loads the entire XML tree via `ET.parse()`, iterates `<sms>` elements, skips `<mms>` elements, performs duplicate detection via SHA3-512 hashing of message body text, routes messages to bank-specific logic by sender short code, and tracks per-bank message counts via `defaultdict(int)`. Only HBL messages are parsed into CC transactions; other banks are only counted. Also has a `__main__` block with hardcoded file paths for manual testing.

**`HBLSmsParser`** (`parser/hbl_sms_parser.py`) — Contains both **static methods** used by `SmsBackupFileParser` (the active code path) and **instance methods** (`loadFromSmsBackupFile`, `parseMessages`) that are legacy dead code from a prior architecture. The static methods do:
- `isSmsFromHBL()` — checks sender short code against `HBL_SHORT_CODES`
- `isMsgCreditCardTxn()` — quick string check for "CreditCard" and "has been charged at"
- `extractDetailsFromTxnMsg()` — regex extraction of last 4 CC digits, vendor, amount (with currency), and date
- `_extractCurrencyAndAmount()` — parses "PKR-25,170.49" format into `CurrencyAmountTuple`
- `_convertToDateTime()` — parses "01/Oct/2023" format using `%d/%b/%Y`, then **stamps** the result
  with `DEFAULT_TZ` via `.replace(tzinfo=...)`. Returns `None` on an unparseable date.

**`CreditCardTxnDC`** (`cc_txn.py`) — Dataclass with fields: `amountTuple` (CurrencyAmountTuple namedtuple), `date` (datetime), `vendor` (str), `ccLastFourDigits` (int, default 0). Has a custom `__repr__`. Also defines `DEFAULT_TZ = ZoneInfo("Asia/Karachi")` (duplicated from `common.py`).

**`common.py`** — Defines `DEFAULT_CURRENCY = "PKR"`, `DEFAULT_TZ`, `Currency` StrEnum (PKR/USD/CAD), and `SpendingCategories` StrEnum (currently unused/dead code).

### Bank Short Codes
| Bank | Short Code(s) | Parser Status |
|------|---------------|---------------|
| HBL | `"4250"`, `"14250"` | Fully implemented (`HBLSmsParser`) |
| Faysal Bank | `"8756"` | Counted only, no parser |
| Standard Chartered | `"7220"` | Counted only, no parser |
| Meezan Bank | `"8079"`, `"9779"` | Counted only, no parser |

**HBL migrated CC txn alerts from `4250` to `14250` in mid-Jan 2025** (last `4250` 15/Jan/2025,
first `14250` 18/Jan/2025) with the body format byte-for-byte unchanged — both codes must stay
listed. When txns stop appearing after some date, suspect a re-homed short code first: unrecognized
senders fall through the `elif` chain in `parseMessages()` into `OTHER` and are counted, never
parsed, so **nothing is logged**.

### Data Flow
1. CLI receives XML filepath → `SmsBackupFileParser.loadFromSmsBackupFile()` loads full DOM
2. `parseMessages()` iterates all child elements, skips MMS
3. Duplicate detection: SHA3-512 hash of `body` attribute (stripped) — checked per bank branch (not pre-checked)
4. HBL messages → `HBLSmsParser.extractDetailsFromTxnMsg()` → regex parse → `CreditCardTxnDC`
5. Transactions and vendors accumulated in `SmsBackupFileParser.ccTxns` and `ccVendors`
6. CLI subcommand reads from global `smsParser` to display results

### SMS XML Structure
SMS backup files use `<smses count="N">` as root with `<sms>` child elements. Key attributes:
- `address` — sender short code (e.g., `"4250"` for HBL)
- `body` — message text content
- `date` — Unix timestamp in milliseconds
- `readable_date` — human-readable date string (e.g., `"Oct 2, 2023 9:57:06 PM"`)
- Also: `protocol`, `type`, `subject`, `read`, `status`, `date_sent`, `service_center`, `contact_name`

### HBL CC Transaction SMS Format
```
Dear Customer, Your HBL CreditCard (ending with XXXX) has been charged at VENDOR_NAME for CURRENCY-AMOUNT on DD/Mon/YYYY.
```
Example: `"Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."`

### Adding a New Bank Parser
1. Create `src/parser/<bank>_sms_parser.py` following `HBLSmsParser` pattern
2. Define bank-specific short codes and regex patterns for CC transactions
3. Expose static methods: `isSmsFrom<Bank>()`, `isMsgCreditCardTxn()`, `extractDetailsFromTxnMsg()`
4. Register short codes in `SmsBackupFileParser.parseMessages()` routing and replace the count-only `elif` branch with actual parsing

## Conventions

- **Python 3.14** (`requires-python = ">=3.14"`, `.python-version` = `3.14`). Windows 11 is the only
  supported platform — no cross-platform branches, no back-compat shims.
- **All timestamps in an SMS backup file are Asia/Karachi local time.** This is an assumption the
  app makes, not something the backup declares — the XML carries no offset. So naive datetimes
  parsed out of a backup must be **stamped** with `DEFAULT_TZ` (`.replace(tzinfo=DEFAULT_TZ)`),
  never **converted** into it (`.astimezone(DEFAULT_TZ)`). `astimezone()` on a naive value reads it
  as the *host machine's* local time and shifts it, which silently moves txns across day boundaries
  on any machine not set to +05:00. Never use `astimezone()` on a value parsed from a backup.
- **Tests must run from `src/`** — imports are relative to that directory (no package setup). The CLI
  itself can be launched from anywhere via `uv run src/hbl_sms_query_tool.py`.
- **Subcommands are registered with an explicit name string** — `@cli.command("list_all_vendors")`.
  Click ≥8.2 derives command names by replacing underscores with dashes, so without the explicit
  name the documented `list_all_vendors` invocation silently becomes `list-all-vendors`.
- Methods use **camelCase** (e.g., `isSmsFromHBL`, `parseMessages`, `loadFromSmsBackupFile`)
- Instance variables use **camelCase** in `SmsBackupFileParser` (e.g., `ccVendors`, `ccTxns`, `msgsParsed`) but **snake_case** in `HBLSmsParser` (e.g., `xml_tree`, `cc_txns`, `all_vendors`) — inconsistent
- Bank parsers expose **static methods** for identification and extraction
- Tests use `unittest` (not pytest) and create XML elements programmatically via `ET.Element` rather than loading from fixture files
- Error handling uses `print()` for parse failures and `assert` for post-extraction validation; exceptions are not raised on malformed messages
- No `logging` module usage — all output goes through `print()` or `click.echo()` (mixed inconsistently in the CLI module)

## Known Issues & Technical Debt

Documented in detail in `src/IMPROVEMENTS.md`. Key items:

- **Missing `@staticmethod`** on `printSmsMsg` and `smsMsgToString` in `SmsBackupFileParser` (bug — calling as instance methods would fail)
- **Duplicate hash computed multiple times** per message in the `elif` chain of `parseMessages()`
- **Regex recompiled per call** in `_extractCurrencyAndAmount` (unlike `HBL_CC_TXN_PTTRN` which is pre-compiled)
- **Dead code** in `HBLSmsParser` — instance methods `loadFromSmsBackupFile()` and `parseMessages()` (lines 119-159) plus associated `__init__` state are never called
- **Duplicate `DEFAULT_TZ`** definition in both `common.py` and `cc_txn.py`
- **`SpendingCategories` enum** defined but unused anywhere
- **Global mutable state** (`smsParser`) in CLI module instead of using Click context
- **Unused import** `PrettyPrinter` in `hbl_sms_query_tool.py`
- **Remaining test gaps** — CLI subcommand output/end-to-end runs (`CliRunner` invocations) and `_updateMonthlyTotals`. The parser methods, `SmsBackupFileParser` (incl. duplicate detection), and command-name registration are covered.
- **Amount regex requires comma-grouped thousands** — `PKR-25170.49` (no comma, >= 1,000) fails to parse, returns the `(None, -1.2345)` sentinel, and the `assert` in `extractDetailsFromTxnMsg` then aborts the whole run. Pinned by `test_extractCurrencyAndAmount_ungrouped_thousands`.
- **Duplicate HBL msgs are counted as OTHER** — a dup fails the HBL branch condition in `parseMessages()` and falls through the `elif` chain. Pinned by `test_duplicate_msg_parsed_once`.
