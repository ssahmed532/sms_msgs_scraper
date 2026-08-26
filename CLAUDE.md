# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMS Messages Scraper — A Python CLI tool for parsing and analyzing credit card transaction SMS messages from Pakistani banks. It processes Android SMS backup files (XML format from the "SMS Backup & Restore" app) to extract, deduplicate, and summarize financial transactions.

All four banks are fully parsed: **HBL** (Habib Bank Limited), **FBL** (Faysal Bank) and
**SCB** (Standard Chartered) credit-card txns, plus **MEZN** (Meezan Bank) account debits.

CC txns from the three CC banks share one store (`ccTxns`) and are tagged with their issuing bank,
so the CC commands report them together and `--bank` splits them apart. Meezan account debits are a
different kind of transaction (card purchases, ATM withdrawals, bill payments, funds transfers) and
live in their own store (`debitTxns`) with their own two commands.

**Version:** 1.0.0 — declared in **two** places that must agree: `@click.version_option` in `hbl_sms_query_tool.py` and `[project].version` in `pyproject.toml` (which `uv.lock` also carries). A test pins them equal, because they had already drifted apart once.

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

# Credit card commands (HBL + FBL + SCB combined):
#   list_all_vendors               - Unique vendors from CC transactions (sorted alphabetically)
#   list_all_cc_txns               - List all credit card transactions
#   monthly_cc_spending_summary    - Month-wise CC spending by currency (PKR/USD/CAD)
#
# Meezan account debit commands:
#   list_all_debit_txns            - List all account debit transactions
#   monthly_debit_spending_summary - Month-wise debit spending by currency
#
# All five accept an optional date range filter:
#   --from-date YYYY-MM-DD         - only txns on or after this date (inclusive)
#   --to-date   YYYY-MM-DD         - only txns on or before this date (inclusive)
#
# The three CC commands also accept:
#   --bank {HBL|FBL|SCB}           - only txns from this bank (case-insensitive; default all)
#
# list_all_debit_txns also accepts:
#   --txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}
```
```bash
uv run src/hbl_sms_query_tool.py backup.xml list_all_cc_txns --from-date 2024-01-01 --to-date 2024-12-31
uv run src/hbl_sms_query_tool.py backup.xml monthly_cc_spending_summary --from-date 2025-01-01
uv run src/hbl_sms_query_tool.py backup.xml list_all_cc_txns --bank FBL
uv run src/hbl_sms_query_tool.py backup.xml list_all_debit_txns --txn-type atm_withdrawal
```

### Verifying against a real backup
```bash
# assert the parsers against the reference backup (repo root, gitignored)
uv run scripts/verify_against_backup.py

# ...or against any other backup
uv run scripts/verify_against_backup.py path/to/sms-backup.xml
```

Run this after **any** change to a parser, to the routing in `SmsBackupFileParser`, or to the dedup
identity. The unit suite proves the parsers work on hand-built msgs; this proves they still work on
~4,700 real ones, which is where a regex change that passes every unit test quietly loses 40 txns
shows up.

It separates two kinds of check. **Invariants** hold for any backup and are always asserted: the
conservation identity, plus every txn having a non-empty vendor, a positive amount, a tz-aware
Asia/Karachi date, a known bank and a known debit type. **Expected counts** are tied to one backup
file by its SHA-256 — asserted exactly against that file, and merely reported against any other,
since they cannot mean anything there. Output is counts only; the parsers' own per-msg warnings are
captured and reported as a line count, because a backup is personal financial data.

If an expected count misses, see the Anti-drift rule in the script's docstring: fix the code, or
re-derive the expectation and record the derivation — never edit the number to match the output.

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
│   ├── cc_txn.py                 # CC txn data model (CreditCardTxnDC dataclass)
│   ├── debit_txn.py              # Account debit data model (DebitTxnDC, DebitTxnType)
│   ├── common.py                 # Enums (Currency, SpendingCategories), constants (DEFAULT_TZ)
│   ├── parser/
│   │   ├── hbl_sms_parser.py     # HBL CC txn parsing (regex-based)
│   │   ├── fbl_sms_parser.py     # Faysal Bank CC txn parsing
│   │   ├── scb_sms_parser.py     # Standard Chartered CC txn parsing
│   │   └── mezn_sms_parser.py    # Meezan Bank account debit parsing
│   └── IMPROVEMENTS.md           # Codebase audit doc with known issues
├── scripts/
│   └── verify_against_backup.py  # Asserts the parsers against a real backup (counts only)
└── tests/
    ├── test_hbl_sms_parser.py         # HBL parser identification + extraction
    ├── test_fbl_sms_parser.py         # FBL parser + its end-to-end backup cases
    ├── test_scb_sms_parser.py         # SCB parser + its end-to-end backup cases
    ├── test_mezn_sms_parser.py        # Meezan parser + its end-to-end backup cases
    ├── test_date_range_filter.py      # CLI --from-date/--to-date filter (CC + debit txns)
    ├── test_sms_backup_file_parser.py # SmsBackupFileParser (dedup, routing, conservation, mixed-bank)
    └── test_cli_commands.py           # Subcommand names, --bank/--txn-type, monthly-totals seeding
```

Each bank's end-to-end backup tests live in **that bank's own** test file rather than in
`test_sms_backup_file_parser.py`. That is deliberate: it keeps parallel work on different banks off
a shared file, at the cost of each test file carrying its own copy of the small `_parseBackup`
helper.

No `__init__.py` files and no importable package — `pyproject.toml` sets `[tool.uv] package = false`
and declares dependencies only. Imports still rely on `src/` being on `sys.path`, which happens
because that is where the executed script lives.

### Module Relationships
```
hbl_sms_query_tool.py (CLI entry point via Click)
    ├── common.py (Currency enum)
    ├── cc_txn.py (CreditCardTxnDC dataclass)
    ├── debit_txn.py (DebitTxnDC, DebitTxnType)
    └── sms_backup_file_parser.py (orchestrator)
            ├── parser/hbl_sms_parser.py  ─┐
            ├── parser/fbl_sms_parser.py   ├─→ cc_txn.py (CreditCardTxnDC, CurrencyAmountTuple)
            ├── parser/scb_sms_parser.py  ─┘
            └── parser/mezn_sms_parser.py ──→ debit_txn.py (DebitTxnDC, DebitTxnType)
                                              cc_txn.py (CurrencyAmountTuple)
```

### Key Components

**`hbl_sms_query_tool.py`** — Click CLI entry point. Defines a `@click.group()` with a required `filepath` argument and five subcommands (three CC, two debit). Uses a **global** `smsParser` variable shared between the group callback and subcommands. Measures parse time with `perf_counter`. Only catches `PermissionError` from file loading.

**`SmsBackupFileParser`** (`sms_backup_file_parser.py`) — Orchestrator class. Loads the entire XML
tree via `ET.parse()`, iterates `<sms>` elements, skips `<mms>` elements, then for each msg:
increments `ALL`, checks for a duplicate **once** (SHA3-512 of the stripped body) and routes to a
bank parser via that parser's `isSmsFrom*()`. Per-bank counts live in a `defaultdict(int)`. Owns no
short-code lists — each parser owns its own. Also has a `__main__` block with hardcoded file paths
for manual testing.

Counting buckets, and the identity that ties them together:

| Bucket | Meaning |
|---|---|
| `ALL` | `<sms>` elements seen (duplicates included; `<mms>` excluded) |
| `HBL` / `FBL` / `SCB` / `MEZN` | non-duplicate msgs from that bank's short codes |
| `OTHER` | non-duplicate msgs from an unrecognized sender |
| `DUP` | msgs whose body was already seen (any sender) |
| `<ID>_SKIPPED` | msgs that carried a txn signal but could not be parsed |

**`ALL == HBL + FBL + SCB + MEZN + OTHER + DUP` must hold for any backup** — every counted msg lands
in exactly one bucket. Pinned by `test_msg_count_conservation` and asserted by the verification
harness on every real-backup run. If it stops holding, a routing branch is counting twice or not at
all.

**Dedup happens once, before routing** — not inside each bank's branch condition. Two consequences
worth knowing, both pinned by tests:
- A duplicate is counted as `DUP` and nothing else. It used to fail its bank's branch condition and
  fall through the `elif` chain into `OTHER`.
- The identity is **(sender short code, stripped body)**, so a msg can only ever be suppressed by an
  earlier msg *from the same short code*. It deliberately does **not** include a received timestamp:
  see the dedup limitations below for the measurements that rule both candidate timestamps out.

The per-duplicate body dump is deliberately gone: global dedup finds 258 duplicates in the reference
backup, and printing the original/duplicate pair for each buried real output under ~1,500 lines.

**Skip accounting** — the three new banks do **not** assert on their parse results. A malformed msg
is skipped, warned about once, and counted in `<ID>_SKIPPED`. HBL's `assert`s remain (that path has
never seen a malformed txn msg), but they are the brittleness `IMPROVEMENTS.md` describes: one bad
msg would abort the entire run.

**`HBLSmsParser`** (`parser/hbl_sms_parser.py`) — Contains both **static methods** used by `SmsBackupFileParser` (the active code path) and **instance methods** (`loadFromSmsBackupFile`, `parseMessages`) that are legacy dead code from a prior architecture. The static methods do:
- `isSmsFromHBL()` — checks sender short code against `HBL_SHORT_CODES`
- `isMsgCreditCardTxn()` — quick string check for "CreditCard" and "has been charged at"
- `extractDetailsFromTxnMsg()` — regex extraction of last 4 CC digits, vendor, amount (with currency), and date
- `_extractCurrencyAndAmount()` — parses "PKR-25,170.49" format into `CurrencyAmountTuple`
- `_convertToDateTime()` — parses "01/Oct/2023" format using `%d/%b/%Y`, then **stamps** the result
  with `DEFAULT_TZ` via `.replace(tzinfo=...)`. Returns `None` on an unparseable date.

**`FBLSmsParser`** (`parser/fbl_sms_parser.py`) — Faysal Bank CC txns. Static methods only. Notable:
amounts carry **no thousands separators** (so the HBL amount regex cannot be reused), **two** date
formats occur (`%d-%b-%y %I:%M:%S %p` and `%d-%m-%Y %H:%M:%S`), the msg carries **no card digits** at
all (`ccLastFourDigits` stays 0), and the vendor sits in a fixed-width **22-char column** followed by
a 14-char city column. Vendor extraction strips backslash escape characters first, then either splits
on runs of 2+ spaces (≥3 parts → first part) or slices to 22 chars. The unescape must happen
**before** the slice: the backslash is an extra character beyond the column, so slicing first costs a
column-filling vendor its last letter.

**`SCBSmsParser`** (`parser/scb_sms_parser.py`) — Standard Chartered CC txns. Static methods only.
`isMsgCreditCardTxn()` checks for `"have been paid at"` **and nothing else** — deliberately loose, so
that the bank's known-unusable msgs (truncated mid-body, or carrying a literal `PKR .00`) pass the
signal and fail extraction, landing in `SCB_SKIPPED` rather than vanishing silently. Card masks come
in exactly two shapes: `5452xxxxxxxx1280` → last4 `1280`, and a BIN-only `5495` → last4 `0`.

**`MeznSmsParser`** (`parser/mezn_sms_parser.py`) — Meezan account debits, producing `DebitTxnDC`.
Static methods only. Five template families tried in order, first match wins, mapping onto four
`DebitTxnType` values (both funds-transfer families map to `funds_transfer`). `isMsgDebitTxn()` is an
**independent keyword signal, deliberately looser than the extraction regexes** — an amount-head
anchor plus a required keyword plus excluded keywords. That gap is the point: if the signal were
"one of the templates matched", a changed Meezan template would be counted as an ordinary MEZN msg
with **no warning at all**, which is exactly the silent gap the HBL `4250` → `14250` migration
taught this codebase to avoid. A drifted template instead lands in `MEZN_SKIPPED` with a warning.

**`CreditCardTxnDC`** (`cc_txn.py`) — Dataclass with fields: `amountTuple` (CurrencyAmountTuple namedtuple), `date` (datetime), `vendor` (str), `ccLastFourDigits` (int, default 0), `bank` (str, default `"HBL"` so pre-existing call sites stay valid). Has a custom `__repr__`. Also defines `DEFAULT_TZ = ZoneInfo("Asia/Karachi")` (duplicated from `common.py`).

**`DebitTxnDC`** / **`DebitTxnType`** (`debit_txn.py`) — Account debit model, reusing
`CurrencyAmountTuple` from `cc_txn.py`. Fields: `amountTuple`, `date`, `vendor` (merchant / ATM
location / bill description / payee, depending on type), `txnType`, `acctMask` (`""` when the msg
carries none), `bank` (default `"MEZN"`). `DebitTxnType` is a `StrEnum` with `auto()`, so its values
are the lowercase member names — which is what the `--txn-type` Choice is built from.

**`common.py`** — Defines `DEFAULT_CURRENCY = "PKR"`, `DEFAULT_TZ`, `Currency` StrEnum (PKR/USD/CAD), and `SpendingCategories` StrEnum (currently unused/dead code).

### Bank Short Codes
Each parser owns its own short-code list as a class constant; `SmsBackupFileParser` holds none.

| Bank | Short Code(s) | Parser | Produces |
|------|---------------|--------|----------|
| HBL | `"4250"`, `"14250"` | `HBLSmsParser` | `CreditCardTxnDC` (`bank="HBL"`) |
| Faysal Bank | `"8756"` | `FBLSmsParser` | `CreditCardTxnDC` (`bank="FBL"`) |
| Standard Chartered | `"7220"` | `SCBSmsParser` | `CreditCardTxnDC` (`bank="SCB"`) |
| Meezan Bank | `"8079"`, `"9779"` | `MeznSmsParser` | `DebitTxnDC` (`bank="MEZN"`) |

**HBL migrated CC txn alerts from `4250` to `14250` in mid-Jan 2025** (last `4250` 15/Jan/2025,
first `14250` 18/Jan/2025) with the body format byte-for-byte unchanged — both codes must stay
listed. When txns stop appearing after some date, suspect a re-homed short code first: unrecognized
senders fall through the `elif` chain in `parseMessages()` into `OTHER` and are counted, never
parsed, so **nothing is logged**.

### Data Flow
1. CLI receives XML filepath → `SmsBackupFileParser.loadFromSmsBackupFile()` loads full DOM
2. `parseMessages()` iterates all child elements, skips MMS
3. `ALL` is incremented, **then** duplicate detection runs once (SHA3-512 of the stripped `body`);
   a duplicate is counted as `DUP` and skipped before any routing
4. Routing by sender short code, via each parser's `isSmsFrom*()`, in chain order
   HBL → FBL → SCB → MEZN → `OTHER`
5. HBL/FBL/SCB msgs carrying a txn signal → `extractDetailsFromTxnMsg()` → `CreditCardTxnDC`
   accumulated in `ccTxns` / `ccVendors`; MEZN debits → `DebitTxnDC` in `debitTxns` / `debitVendors`.
   An extraction returning `None` is counted in `<ID>_SKIPPED` (HBL asserts instead)
6. CLI subcommand reads from global `smsParser`, applies `--bank` / `--txn-type` / date filters,
   and displays results

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
1. Create `src/parser/<bank>_sms_parser.py` following the `FBLSmsParser` / `SCBSmsParser` pattern —
   static methods only, no legacy instance state
2. Own the short codes there as a class constant (`ID`, `<BANK>_SHORT_CODES`), and pre-compile every
   regex at class level
3. Expose `isSmsFrom<Bank>()`, a txn-signal check, and `extractDetailsFromTxnMsg()` returning `None`
   plus one printed warning on any failure — never raising, never asserting
4. Keep the txn-signal check **looser** than the extraction regexes. If signal and extraction are
   equivalent, a changed template is silently counted as an ordinary msg from that bank instead of
   being warned about and counted in `<ID>_SKIPPED`
5. Add an `elif` to the `parseMessages()` chain routing via `isSmsFrom<Bank>()`, appending to the
   right store and counting `<ID>_SKIPPED` on `None`. Add the bank to the parse summary and, for a
   CC bank, to `CC_BANKS` in the CLI so `--bank` offers it
6. Add `tests/test_<bank>_sms_parser.py` holding both the unit tests and that bank's end-to-end
   backup cases

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
- **A parse failure returns `None` and prints exactly one warning line**, carrying the reason and the
  msg's `readable_date` — never the body. Bodies are personal financial data, and the reference
  backup produces 26 skips in one run. The orchestrator only counts; it does not warn again. (HBL's
  parser predates this convention and still prints bodies.)
- **Only HBL asserts** on extraction results. New bank branches skip + warn + count instead: the
  other three banks have known-malformed real msgs, and an `assert` aborts the whole run
- Error handling uses `print()` for parse failures and `assert` for post-extraction validation in the HBL path; exceptions are not raised on malformed messages
- No `logging` module usage — all output goes through `print()` or `click.echo()` (mixed inconsistently in the CLI module)

## Reference numbers (backup `sms-20251011130814.xml`, measured 2026-08-26)

Gate on these when changing parsing or routing. They are **post-dedup runtime** values — the parser
dedups before parsing, so a raw-corpus grep will always be higher and is not comparable.

| Metric | Value |
|---|---|
| `ALL` / `DUP` | 4,665 / 235 |
| msgs HBL / FBL / SCB / MEZN / OTHER | 798 / 674 / 614 / 1,228 / 1,116 |
| `ccTxns` total | 1,678 |
| CC txns HBL / FBL / SCB | 717 / 583 / 378 |
| skipped FBL / SCB / MEZN | 0 / 26 / 0 |
| `debitTxns` total | 875 |
| debit card_purchase / atm_withdrawal / account_debit / funds_transfer | 8 / 361 / 96 / 410 |
| unique vendors HBL / FBL / SCB / MEZN | 180 / 166 / 94 / 189 |
| unique CC vendor union (HBL ∪ FBL ∪ SCB) | 357 |
| FBL currency split PKR / USD / CAD | 574 / 8 / 1 |

`OTHER` is 1,116 rather than 1,354 because dedup now runs once for every sender rather than only
inside a bank branch, so retransmitted promos from non-bank senders are counted as `DUP`.

The per-bank counts are each one higher than they were before the dedup identity gained the sender
(HBL 797, FBL 673, SCB 613, MEZN 1,227, `DUP` 258). Those 23 msgs — 4 from bank short codes — were
being suppressed by an unrelated sender that happened to repeat their text. **No txn figure moved**:
all 23 are non-txn msgs, which is why the CC and debit counts are identical either way.

The 26 SCB skips are the bank's own malformed msgs: 21 truncated mid-body and 5 carrying a literal
`PKR .00` (foreign-currency txns whose amount is absent). They are expected, not a defect — a change
in that number means something moved.

**If a number here stops reproducing, fix the code or re-derive the expectation from the corpus and
record the derivation.** Never quietly edit this table to match observed output.

## Known Issues & Technical Debt

Documented in detail in `src/IMPROVEMENTS.md`. Key items:

- **Missing `@staticmethod`** on `printSmsMsg` and `smsMsgToString` in `SmsBackupFileParser` (bug — calling as instance methods would fail)
- **Regex recompiled per call** in HBL's `_extractCurrencyAndAmount` (unlike `HBL_CC_TXN_PTTRN` which is pre-compiled). The three newer parsers pre-compile everything at class level.
- **Dead code** in `HBLSmsParser` — instance methods `loadFromSmsBackupFile()` and `parseMessages()` plus associated `__init__` state are never called
- **Duplicate `DEFAULT_TZ`** definition in both `common.py` and `cc_txn.py`. The three newer parsers import it from `common.py`; HBL still uses `CreditCardTxnDC.DEFAULT_TZ`.
- **`SpendingCategories` enum** defined but unused anywhere
- **Global mutable state** (`smsParser`) in CLI module instead of using Click context
- **Unused import** `PrettyPrinter` in `hbl_sms_query_tool.py`
- **HBL amount regex requires comma-grouped thousands** — `PKR-25170.49` (no comma, >= 1,000) fails to parse, returns the `(None, -1.2345)` sentinel, and the `assert` in `extractDetailsFromTxnMsg` then aborts the whole run. Pinned by `test_extractCurrencyAndAmount_ungrouped_thousands`. The newer parsers return `None` and skip instead of asserting.
- **HBL is the only bank that asserts** on extraction results, so one malformed HBL txn msg aborts the entire run. The other three skip + warn + count.
- **Remaining test gaps** — CLI subcommand output/end-to-end runs (`CliRunner` invocations). Parser methods, `SmsBackupFileParser` (dedup, routing, conservation, mixed-bank), command registration, option wiring, the bank filter and monthly-total seeding are all covered.
- **A tz test cannot fail on a machine already set to +05:00** — `astimezone()` is a no-op there, so a stamped-vs-converted bug passes locally. Windows offers no way to simulate another timezone in-process, so the stamping rule is enforced by convention and review, not by the suite.

### Known parsing limitations (accepted, not bugs)

- **Two legitimately identical purchases can still collapse into one txn, but only for HBL and SCB.**
  FBL and Meezan bodies carry a time of day (to the second and to the minute), so an identical body
  provably means the same txn and dedup is exact for them. HBL and SCB bodies carry a *date only*, so
  a genuine second identical purchase on the same day is indistinguishable from a retransmission. On
  the reference backup this is at most 3 msgs (2 HBL, 1 SCB), whose repeats arrived 1.5–6 minutes
  apart; every other repeat arrived within 8 seconds and is plainly a retransmission.

  **A received timestamp cannot fix this, and would make things worse.** Measured on the reference
  backup: the network redelivers the *same* alert as much as 2.9 hours late (two FBL retransmissions
  arrived 19 minutes and 2.9 hours after their originals, and their bodies pin the txn to the second,
  so they are provably the same txn) — any useful "within N minutes" window would have admitted them
  as second purchases and inflated spending. `date_sent` is no better: it differs on 138 of the 145
  repeated (sender, body) groups, including those same provably-identical txns, so adding it would
  disable dedup almost entirely. Fabricating spending is worse than the residual, so the identity
  stays with what the msg says rather than when it arrived.
- **Meezan normalizes internal whitespace for matching, but the hash does not.** Two Meezan bodies
  differing only in internal spacing hash differently and both parse. Real duplicates are
  byte-identical retransmissions, so this has no effect in practice.
- **SCB vendors keep a glued-on city** (`SOUTH CITY HOSPITALKarachi PAK`) — the corpus offers no
  reliable separator, so no split is attempted.
- **SCB card masks:** the last 4 digits are recovered from any mask whose digits are interrupted by a
  masked section, so a 6-digit BIN (`545221xxxxxx1280`) works rather than silently recording card 0.
  A shape that is neither that nor an unmasked BIN-only run is warned about, since a bare `0` is
  otherwise indistinguishable from a legitimate BIN-only mask.
- **An FBL vendor containing an internal double space would mis-split** to its first token. No such
  vendor exists in the corpus; the split rule is pinned by unit tests on the known shapes.
- **Meezan cheque-clearing debits are deliberately out of scope** — they risk double-counting against
  the separate "cheque received" notice, and a cheque is neither a card nor a digital txn.
- **`list_all_debit_vendors` does not exist.** `debitVendors` is populated and the command would be
  trivial; it was simply not asked for.
