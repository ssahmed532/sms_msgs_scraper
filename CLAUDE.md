# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMS Messages Scraper - A Python CLI tool for parsing and analyzing credit card transaction SMS messages from Pakistani banks (primarily HBL). It processes Android SMS backup files (XML format) to extract, deduplicate, and summarize financial transactions.

## Development Commands

### Running Tests
```bash
# Run all tests (must run from src/ directory)
cd src
python -m unittest discover -s ../tests/ -v

# Run a single test file
cd src
python -m unittest discover -s ../tests/ -p "test_hbl_sms_parser.py" -v

# Run a specific test method
cd src
python -m unittest tests.test_hbl_sms_parser.TestHBLSmsParser.test_hbl_sms_msg -v
```

### Running the CLI Tool
```bash
cd src
python hbl_sms_query_tool.py <path_to_sms_backup.xml> <command>

# Available commands:
#   list_all_vendors          - List unique vendors from transactions
#   list_all_cc_txns          - List all credit card transactions
#   monthly_cc_spending_summary - Month-wise spending aggregation
```

### Dependencies
- `click` - CLI framework (required, install via pip)
- Standard library only otherwise (xml, hashlib, datetime, re, dataclasses, zoneinfo)

## Architecture

### Module Relationships
```
hbl_sms_query_tool.py (CLI entry point)
    └── sms_backup_file_parser.py (root-level orchestrator)
            ├── parser/hbl_sms_parser.py (HBL-specific parsing)
            └── cc_txn.py (transaction data model)
                    └── common.py (enums, constants)
```

### Key Components

**SmsBackupFileParser** (`sms_backup_file_parser.py`): Orchestrates XML parsing, identifies bank by short code, detects duplicates via SHA3-512 hashing, delegates to bank-specific parsers.

**HBLSmsParser** (`parser/hbl_sms_parser.py`): Regex-based extraction of transaction details (currency, amount, vendor, date, card last 4 digits) from HBL SMS messages.

**CreditCardTxnDC** (`cc_txn.py`): Dataclass representing a transaction with timezone-aware dates (Asia/Karachi default). Supports PKR, USD, and CAD currencies.

### Bank Short Codes
- HBL: "4250"
- Faysal Bank: "8756"
- Standard Chartered: "7220"
- Meezan Bank: "8079", "9779"

### Data Flow
1. XML SMS backup loaded → messages filtered by bank short code
2. Duplicate detection via SHA3-512 hash of message body text only
3. Bank-specific parser extracts transaction details via regex
4. Transactions stored as `CreditCardTxnDC` dataclass instances

### SMS XML Structure
SMS messages are stored as `<sms>` elements with attributes:
- `address`: sender short code (e.g., "4250" for HBL)
- `body`: message text content
- `date`: Unix timestamp in milliseconds
- `readable_date`: human-readable date string

### Adding a New Bank Parser
1. Create `parser/<bank>_sms_parser.py` following `HBLSmsParser` pattern
2. Define bank-specific short codes and regex patterns for CC transactions
3. Register short codes in `SmsBackupFileParser` and add to `parseMessages()` routing

## Conventions

- **Python 3.11+** required (uses `StrEnum`, `ZoneInfo`)
- **All commands must run from `src/`** — imports are relative to that directory (no `setup.py`/`pyproject.toml`)
- Methods use **camelCase** (e.g., `isSmsFromHBL`, `parseMessages`, `loadFromSmsBackupFile`)
- Bank parsers expose **static methods** for identification and extraction (`isSmsFromHBL`, `isMsgCreditCardTxn`, `extractDetailsFromTxnMsg`)
- Tests create XML elements programmatically via `ET.Element` rather than loading from fixture files
- Error handling uses `print()` for parse failures and `assert` for post-extraction validation; exceptions are not raised on malformed messages
