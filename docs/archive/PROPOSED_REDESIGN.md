# Proposed Redesign: SMS Transaction Analyzer

> ## ⚠ ARCHIVED — shelved rationale, superseded before it was ever built
>
> **Archived 2026-08-26.** This is the *rationale* document for the `sms-txn-analyzer` rewrite. It was
> superseded by `IMPLEMENTATION_PLAN.md` (which resolved 16 contradictions found in here), and that
> plan was then shelved too: on **2026-08-08** the user chose a minimal in-place uv setup instead.
> None of the architecture proposed below — the package layout, pydantic models, SQLite store,
> spending categorizer, `sms-txn-analyzer` CLI verbs — exists in the code.
>
> **Do not implement from this document directly**, even if the rewrite is revived; it is the older
> and less consistent of the two. Its lasting value is the critique of the original single-bank
> design, which is what motivated both the multi-bank parsers and the rename of the entry point away
> from `hbl_sms_query_tool.py`. Both of those shipped — by the incremental route, not this one.
>
> **For the code as it stands, read these instead:** `CLAUDE.md` for the architecture and
> limitations, `src/IMPROVEMENTS.md` for the open defects, `README.md` for usage.

A comprehensive proposal to revamp the SMS Messages Scraper into a well-architected, extensible, and testable Python CLI application.

---

## Table of Contents

1. [Analysis of the Current Application](#1-analysis-of-the-current-application)
2. [Design Principles for the Redesign](#2-design-principles-for-the-redesign)
3. [Proposed Architecture](#3-proposed-architecture)
4. [Module-by-Module Design](#4-module-by-module-design)
5. [CLI Interface Design](#5-cli-interface-design)
6. [Spending Categorization System](#6-spending-categorization-system)
7. [Output & Reporting](#7-output--reporting)
8. [Error Handling Strategy](#8-error-handling-strategy)
9. [Testing Strategy](#9-testing-strategy)
10. [Migration Path](#10-migration-path)
11. [Why SQLite Over In-Memory](#11-why-sqlite-over-in-memory)
12. [Why Pydantic Over Dataclasses](#12-why-pydantic-over-dataclasses)
13. [Review Refinements & Corrections](#13-review-refinements--corrections)
14. [Second Review: Correctness & Scalability Fixes](#14-second-review-correctness--scalability-fixes)

---

## 1. Analysis of the Current Application

### What It Does

The application parses Android SMS backup XML files (from the "SMS Backup & Restore" app) to extract credit card transaction data from bank notification messages. It currently:

- Loads an XML file containing all SMS messages from a phone backup
- Filters messages by sender short code to identify bank messages
- Detects duplicate messages via SHA3-512 hashing
- Parses HBL (Habib Bank Limited) credit card transaction SMS messages using regex
- Extracts: vendor name, amount, currency, date, and card last 4 digits
- Provides three query commands: list vendors, list all transactions, monthly spending summary

### Why It Was Designed This Way

The application evolved organically from a script into a multi-module CLI tool:

1. **Script origins**: `HBLSmsParser` contains both static methods (used by the orchestrator) and dead instance methods (`loadFromSmsBackupFile`, `parseMessages`) — evidence that it was originally a standalone script that loaded and parsed files directly, before `SmsBackupFileParser` was introduced as a higher-level orchestrator.

2. **Single-bank focus**: The architecture was built around HBL specifically (the CLI is literally named `hbl_sms_query_tool.py`). Other banks were added as afterthoughts — their messages are counted but not parsed.

3. **Flat structure without packaging**: No `__init__.py`, no `pyproject.toml`, everything runs via `cd src && python script.py`. This is typical of a personal utility that grew beyond its initial scope.

4. **Global state for simplicity**: The CLI uses a module-level `smsParser` global because Click's group/subcommand pattern requires shared state, and the simplest path was a global variable.

### Key Weaknesses

| Area | Problem | Impact |
|------|---------|--------|
| **Extensibility** | Adding a new bank requires modifying `SmsBackupFileParser.parseMessages()` — an `if/elif` chain that grows with each bank | Hard to extend |
| **Separation of concerns** | `HBLSmsParser` mixes message identification, transaction detection, detail extraction, and file I/O (dead code) in one class | Hard to test, hard to reuse |
| **Data pipeline** | Parsing, filtering, and aggregation are tangled together. No way to query/filter after parsing without modifying code | Inflexible queries |
| **Error handling** | Mix of `print()`, `assert`, and a single `except PermissionError`. Asserts vanish with `-O` flag | Silent failures in production |
| **Output** | Hardcoded `print()`/`click.echo()` with no format options. No CSV/JSON export | Not scriptable |
| **Testing** | Only 4 tests covering 2 identification methods. Zero coverage on parsing, aggregation, duplicate detection, or CLI | Regressions go undetected |
| **Naming** | camelCase methods mixed with snake_case instance variables. File named for one bank but processes all | Confusing to navigate |
| **Duplicate detection** | SHA3-512 of body text only (ignores sender/timestamp). Hash recomputed up to 4 times per message in the `elif` chain | Incorrect dedup semantics, wasted computation |
| **Type safety** | `CurrencyAmountTuple` uses old-style `namedtuple` with no type info. No validation on parsed values beyond `assert` | Silent type errors |

### What Works Well

- **Click for CLI**: Solid framework choice, just underutilized
- **Regex-based parsing**: Appropriate for structured SMS templates
- **Dataclass for transactions**: Correct instinct to use a structured type, but a plain `@dataclass` without validation leaves a gap — parsed regex strings flow through unvalidated. Pydantic `BaseModel` fills this gap with declarative constraints and automatic coercion.
- **Static methods on parsers**: Clean API surface for bank identification/extraction
- **StrEnum for currencies**: Modern, self-documenting

---

## 2. Design Principles for the Redesign

1. **Open/Closed Principle for parsers**: Adding a new bank should require creating one new file and zero modifications to existing code.

2. **Separation of concerns**: Parsing, storage, querying, and presentation are independent layers.

3. **Validate at the boundary**: Data entering the system from SMS text (regex-parsed strings) passes through Pydantic models that coerce types and enforce constraints. Invalid data fails loudly with structured errors, not silent `print()` or `assert`.

4. **Scriptable by default**: Every command supports `--format table|csv|json`. Table for humans, CSV/JSON for piping.

5. **Testable at every layer**: Business logic is importable and testable without the CLI. CLI is testable via `CliRunner`.

6. **Right-sized dependencies**: Click for CLI, Pydantic for data validation/serialization, SQLite for persistence (stdlib). No pandas, no SQLAlchemy, no ORM. Each dependency earns its place by replacing significant hand-written code.

7. **Proper Python packaging**: `pyproject.toml`, entry points, editable install. No more `cd src`.

---

## 3. Proposed Architecture

### Directory Structure

```
sms_txn_analyzer/
├── pyproject.toml                  # Package metadata, deps, entry point
├── categories.toml                 # Default vendor-to-category mapping
├── src/
│   └── sms_txn_analyzer/
│       ├── __init__.py             # Package version
│       ├── cli.py                  # Click CLI entry point (thin layer)
│       ├── config.py               # Configuration loading (TOML + defaults)
│       ├── exceptions.py           # Domain exception hierarchy
│       ├── models.py               # Pydantic models (Transaction, CurrencyAmount, enums)
│       ├── importer.py             # XML file loading → parse → insert into SQLite
│       ├── db.py                   # SQLite schema, connection, query helpers
│       ├── formatters.py           # Output formatting (table, CSV, JSON)
│       ├── categorizer.py          # Vendor-to-category classification
│       └── parsers/
│           ├── __init__.py         # Auto-discovery + registry
│           ├── base.py             # BaseBankParser ABC with __init_subclass__ registry
│           ├── hbl.py              # HBL parser
│           ├── scb.py              # Standard Chartered parser (stub)
│           ├── faysal.py           # Faysal Bank parser (stub)
│           └── meezan.py           # Meezan Bank parser (stub)
└── tests/
    ├── conftest.py                 # Shared fixtures (sample SMS elements, transactions)
    ├── test_models.py              # Transaction model validation + serialization
    ├── test_importer.py            # XML loading + import pipeline
    ├── test_db.py                  # SQLite schema, queries, dedup
    ├── test_categorizer.py         # Vendor categorization
    ├── test_formatters.py          # Output formatting
    ├── test_parsers/
    │   ├── test_hbl.py             # HBL parser (identification + extraction)
    │   └── ...
    └── test_cli.py                 # CLI integration tests via CliRunner
```

### Layer Diagram

```
┌──────────────────────────────────────────────────────┐
│                     cli.py                            │
│          (Click commands — thin wiring layer)         │
├──────────────┬──────────────┬────────────────────────┤
│  formatters  │    config    │      exceptions         │
│  (output)    │    (TOML)    │      (error types)      │
├──────────────┴──────┬───────┴────────────────────────┤
│               db.py (SQLite)                          │
│    (persistent store — all queries are SQL)            │
├──────────────┬──────┴────────────────────────────────┤
│  categorizer │          models.py                     │
│  (classify)  │   (Transaction, CurrencyAmount, etc)   │
├──────────────┴───────────────────────────────────────┤
│              importer.py                              │
│  (XML iteration → parse → deduplicate → INSERT)       │
├──────────────────────────────────────────────────────┤
│              parsers/                                 │
│   (BaseBankParser ABC → HBL, SCB, Faysal, Meezan)    │
└──────────────────────────────────────────────────────┘
```

**Two distinct operations:**

1. **Import** (runs once per backup file): `importer.py` reads the XML, routes SMS elements through the parser registry, deduplicates via `INSERT OR IGNORE` on a UNIQUE constraint, and writes transactions into the SQLite database. This is the expensive step.

2. **Query** (runs every CLI invocation): `cli.py` opens the existing SQLite database and executes SQL queries. No XML parsing, no regex, no dedup. Instant results even over thousands of transactions.

The SQLite database file (e.g., `~/.sms-txn-analyzer/transactions.db`) is the **persistent source of truth**. Backup XML files are inputs that get imported, not re-parsed on every query.

---

## 4. Module-by-Module Design

### 4a. `models.py` — Data Model (Pydantic)

Replace the current `CreditCardTxnDC` + `CurrencyAmountTuple` + scattered enums with Pydantic models that validate, coerce, and serialize automatically.

```python
from collections import defaultdict
from datetime import datetime
from enum import StrEnum, auto
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_TZ = ZoneInfo("Asia/Karachi")


class Currency(StrEnum):
    PKR = auto()
    USD = auto()
    CAD = auto()
    AED = auto()
    GBP = auto()
    EUR = auto()


class TransactionType(StrEnum):
    CREDIT_CARD = auto()
    DEBIT_CARD = auto()
    BANK_TRANSFER = auto()


def _normalize_currency(v):
    """Shared validator: accept 'PKR', 'pkr', 'Pkr' — normalize to lowercase for StrEnum."""
    return v.strip().lower() if isinstance(v, str) else v


def _parse_amount(v):
    """Shared validator: accept comma-formatted strings like '25,170.49' from regex output."""
    if isinstance(v, str):
        return float(v.strip().replace(",", ""))
    return v


class CurrencyAmount(BaseModel):
    """Validated currency + amount pair.

    Replaces the old untyped collections.namedtuple. Pydantic coerces
    string inputs (e.g., from regex groups) into the correct types and
    validates constraints automatically.
    """
    model_config = ConfigDict(frozen=True)

    currency: Currency
    amount: float = Field(gt=0, description="Transaction amount, must be positive")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return _normalize_currency(v)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount_string(cls, v):
        return _parse_amount(v)


class Transaction(BaseModel):
    """An immutable, validated financial transaction.

    This is the core data model of the application. It is constructed by
    bank parsers from regex-parsed SMS text, serialized to/from SQLite rows,
    and dumped to JSON/CSV by the formatters.

    Pydantic handles:
    - Type coercion (str → Currency enum, str → datetime, str → float)
    - Constraint validation (amount > 0, vendor non-empty, date is tz-aware)
    - Immutability (frozen=True)
    - Serialization (model_dump() for SQLite inserts, model_dump_json() for CLI output)
    """
    model_config = ConfigDict(frozen=True)

    bank: str = Field(min_length=1, description="Bank identifier, e.g. 'HBL'")
    txn_type: TransactionType
    currency: Currency
    amount: float = Field(gt=0, description="Transaction amount, must be positive")
    vendor: str = Field(min_length=1, description="Merchant/vendor name")
    date: datetime = Field(description="Transaction date, must be timezone-aware")
    card_last_four: int = Field(default=0, ge=0, le=9999)
    category: str = Field(default="Uncategorized")
    sms_body: str = Field(default="", description="Original SMS text for auditing")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return _normalize_currency(v)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount_string(cls, v):
        return _parse_amount(v)

    @model_validator(mode="after")
    def validate_tz_aware_date(self) -> "Transaction":
        if self.date.tzinfo is None:
            raise ValueError("Transaction date must be timezone-aware")
        return self


class ImportResult(BaseModel):
    """Mutable accumulator for import statistics. Used by importer.py → CLI.

    Unlike Transaction and CurrencyAmount, this is NOT frozen — it is
    mutated during the import process as messages are processed.
    """
    filepath: str
    total_sms_count: int = 0
    txns_added: int = 0
    duplicates_skipped: int = 0
    skipped_mms_count: int = 0
    bank_counts: dict[str, int] = Field(default_factory=lambda: defaultdict(int))
    parse_errors: list[str] = Field(default_factory=list)
```

**Why Pydantic replaces dataclasses here:**

| Concern | dataclass + `__post_init__` | Pydantic `BaseModel` |
|---------|:---:|:---:|
| **Type coercion** | Manual: `float(string.replace(",",""))` scattered in parsers | Automatic: `field_validator` on `amount` handles `"25,170.49"` → `25170.49` |
| **Enum normalization** | Manual: `Currency(string.strip().lower())` with try/except | Automatic: `field_validator` on `currency` normalizes before enum lookup |
| **Constraint validation** | Hand-written `if` checks in `__post_init__` | Declarative: `Field(gt=0)`, `Field(min_length=1)` |
| **Error messages** | Hand-written `ValueError` strings | Structured `ValidationError` with field path, input value, and constraint |
| **Immutability** | `frozen=True` | `ConfigDict(frozen=True)` — same behavior |
| **Serialization to dict** | Manual: build dict field-by-field for SQL INSERT | `model_dump()` — one call, handles enums and datetimes |
| **Deserialization from dict** | Manual: construct from `sqlite3.Row` field-by-field | `model_validate(dict(row))` — one call, with coercion |
| **JSON output** | Manual: `json.dumps` with custom dict comprehension | `model_dump_json(indent=2)` or `model_dump(mode="json")` |
| **Schema documentation** | None | `model_json_schema()` generates JSON Schema automatically |

**Key design decisions:**

1. **Flat `Transaction` model** — no nested `CurrencyAmount` in the DB-facing model. The old `CurrencyAmountTuple` bundled currency and amount together, which made sense as a parsing intermediate but created friction when serializing to/from a flat SQL table. The `Transaction` model now has top-level `currency` and `amount` fields, matching the DB schema 1:1. `CurrencyAmount` is retained as a parser-internal type for the regex extraction step.

2. **Validators do the parser's dirty work** — `parse_amount_string` accepts the raw comma-formatted string that comes out of the regex group (e.g., `"25,170.49"`) and coerces it to a float. This means the parser doesn't need its own `try/except float(...)` logic — it can pass the raw regex output directly to the model constructor.

3. **`ImportResult` is also a Pydantic model** — not a dataclass. This means the CLI can serialize import summaries to JSON (`result.model_dump_json()`) for scripting without custom formatting code.

4. **`frozen=True` on Transaction and CurrencyAmount** — these are immutable value objects. To change the category, use `txn.model_copy(update={"category": "Groceries"})` instead of reconstructing from scratch (which was the awkward pattern with frozen dataclasses). `ImportResult` is deliberately *not* frozen — it is a mutable accumulator incremented during import.

5. **`float` for financial amounts** — `float64` provides 15 significant digits, which is more than sufficient for currency amounts with 2 decimal places (even PKR amounts in the hundreds of millions). An earlier draft used `Decimal` for precision, but this created a semantic mismatch with SQLite: storing as `TEXT` broke `SUM()` aggregation and comparison operators (lexicographic, not numeric), while `model_dump(mode="json")` silently converted `Decimal` → `float` anyway, defeating the purpose. The simpler approach — `float` in Python, `REAL` in SQLite — avoids these bugs with no practical loss of precision for this application.

6. **Shared validators extracted as module-level functions** — `_normalize_currency` and `_parse_amount` are defined once and called by both `CurrencyAmount` and `Transaction` validators, eliminating the duplication that would arise from having identical `field_validator` logic in both models.

---

### 4b. `exceptions.py` — Domain Exceptions

```python
class SmsAnalyzerError(Exception):
    """Base exception for all domain errors."""

class FileLoadError(SmsAnalyzerError):
    """Failed to load or parse the SMS backup file."""

class ParseError(SmsAnalyzerError):
    """Failed to parse a transaction from an SMS message."""

class ConfigError(SmsAnalyzerError):
    """Invalid or missing configuration."""
```

The CLI layer catches `SmsAnalyzerError` subtypes and converts them to `click.ClickException` for clean user-facing messages. Business logic never imports Click.

**Interaction with Pydantic's `ValidationError`:** When a parser constructs a `Transaction` with invalid data (e.g., negative amount, empty vendor, naive datetime), Pydantic raises `pydantic.ValidationError` automatically. The parser catches this and wraps it in a `ParseError` with context about which SMS message failed:

```python
from pydantic import ValidationError

try:
    txn = Transaction(bank=self.BANK_ID, ...)
except ValidationError as e:
    raise ParseError(f"[{self.BANK_ID}] Invalid transaction data: {e}") from e
```

This keeps the exception hierarchy clean — callers only need to catch `ParseError`, not both `ParseError` and `ValidationError`.

---

### 4c. `parsers/base.py` — Self-Registering ABC

This is the core extensibility mechanism. Each bank parser subclass automatically registers itself and its short codes into a central registry.

```python
from abc import ABC, abstractmethod
import xml.etree.ElementTree as ET
from models import Transaction

class BaseBankParser(ABC):
    """Abstract base class for bank-specific SMS parsers.

    Subclasses automatically register themselves in the parser registry.
    To add a new bank, create a subclass and define BANK_ID and SHORT_CODES.
    No other files need modification.

    SMS format evolution: banks may change their message templates over time.
    Parsers should support multiple regex patterns (newest first) so that
    historical messages from old formats and new messages from updated
    formats both parse correctly. See HBLParser for the pattern.
    """

    # --- Class-level registry (populated by __init_subclass__) ---
    _registry: dict[str, type["BaseBankParser"]] = {}   # short_code -> parser class
    _parsers: dict[str, "BaseBankParser"] = {}           # bank_id -> parser instance

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "BANK_ID") and hasattr(cls, "SHORT_CODES"):
            instance = cls()
            cls._parsers[cls.BANK_ID] = instance
            for code in cls.SHORT_CODES:
                if code in cls._registry:
                    existing = cls._registry[code]
                    raise ValueError(
                        f"Short code '{code}' already registered by {existing.BANK_ID}"
                    )
                cls._registry[code] = type(instance)

    @classmethod
    def get_parser_for_address(cls, address: str) -> "BaseBankParser | None":
        """Look up the parser for a given sender short code. O(1) dict lookup."""
        parser_cls = cls._registry.get(address)
        if parser_cls:
            return cls._parsers[parser_cls.BANK_ID]
        return None

    @classmethod
    def known_addresses(cls) -> set[str]:
        """All registered short codes across all parsers."""
        return set(cls._registry.keys())

    @classmethod
    def _reset_registry(cls):
        """Clear all registered parsers. For testing only."""
        cls._registry.clear()
        cls._parsers.clear()

    # --- Abstract interface that every bank parser must implement ---

    BANK_ID: str            # e.g., "HBL"
    SHORT_CODES: list[str]  # e.g., ["4250"]

    @abstractmethod
    def is_transaction_sms(self, body: str) -> bool:
        """Return True if the SMS body represents a financial transaction."""
        ...

    @abstractmethod
    def parse_transaction(self, body: str, timestamp_ms: int) -> Transaction:
        """Parse an SMS body into a Transaction. Raise ParseError on failure."""
        ...
```

**How this solves the extensibility problem:**

- Current design: adding a bank means editing `SmsBackupFileParser.parseMessages()` to add an `elif` branch, importing the new parser, and wiring up short codes manually.
- New design: create `parsers/newbank.py`, define a class inheriting from `BaseBankParser` with `BANK_ID` and `SHORT_CODES`, implement two methods. Done. The `__init_subclass__` hook registers it automatically.

---

### 4d. `parsers/hbl.py` — HBL Parser (Concrete Example)

```python
import re
from datetime import datetime

from pydantic import ValidationError

from models import Transaction, TransactionType, CurrencyAmount, Currency, DEFAULT_TZ
from exceptions import ParseError
from parsers.base import BaseBankParser


class HBLParser(BaseBankParser):
    BANK_ID = "HBL"
    SHORT_CODES = ["4250"]

    # Patterns tried in order — newest first. When HBL changes their SMS
    # template, prepend the new pattern here. Old patterns still match
    # historical messages from previous backup files.
    _TXN_PATTERNS = [
        re.compile(
            r"Dear Customer, Your HBL CreditCard \(ending with (?P<last4>\d{4})\) "
            r"has been charged at (?P<vendor>.*) for (?P<amount>.*) on (?P<date>.*)"
        ),
    ]
    _AMOUNT_RE = re.compile(
        r"(?P<currency>\w+)-(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
    )
    _DATE_FMT = "%d/%b/%Y"

    def is_transaction_sms(self, body: str) -> bool:
        return "CreditCard" in body and "has been charged at" in body

    def _match_body(self, body: str) -> re.Match | None:
        """Try each pattern in order. Returns first match or None."""
        for pattern in self._TXN_PATTERNS:
            m = pattern.match(body)
            if m:
                return m
        return None

    def parse_transaction(self, body: str, timestamp_ms: int) -> Transaction:
        m = self._match_body(body)
        if not m:
            raise ParseError(f"[HBL] SMS body did not match any transaction pattern: {body[:80]}...")

        amount_match = self._AMOUNT_RE.match(m.group("amount").strip())
        if not amount_match:
            raise ParseError(f"[HBL] Could not parse amount from: {m.group('amount')}")

        date_str = m.group("date").strip().rstrip(".")
        try:
            date = datetime.strptime(date_str, self._DATE_FMT).replace(tzinfo=DEFAULT_TZ)
        except ValueError:
            raise ParseError(f"[HBL] Could not parse date from: {date_str}")

        # Let Pydantic handle type coercion and validation.
        # The field_validators on Transaction/CurrencyAmount will:
        #   - normalize "PKR" → Currency.PKR (case-insensitive)
        #   - coerce "25,170.49" → 25170.49 (comma removal)
        #   - validate amount > 0, vendor non-empty, date tz-aware
        try:
            return Transaction(
                bank=self.BANK_ID,
                txn_type=TransactionType.CREDIT_CARD,
                currency=amount_match.group("currency"),     # raw string → Pydantic coerces
                amount=amount_match.group("value"),           # raw string → Pydantic coerces
                vendor=m.group("vendor").strip(),
                date=date,
                card_last_four=int(m.group("last4")),
                sms_body=body,
            )
        except ValidationError as e:
            raise ParseError(f"[HBL] Invalid transaction data: {e}") from e
```

**Key changes from the dataclass version:**

1. **Raw regex groups passed directly to the model constructor** — no manual `float()` conversion, no `Currency(string.lower())`, no `try/except ValueError` for each field. Pydantic's `field_validator` on `currency` normalizes case and maps to the enum; `field_validator` on `amount` strips commas and converts to float. The parser's job is just regex extraction; validation is the model's job.

2. **Single `try/except ValidationError`** wraps the constructor call. If any field is invalid (negative amount, unknown currency, empty vendor, naive datetime), Pydantic produces a structured error with all failures at once, not just the first one. The parser wraps this in `ParseError` for the domain exception hierarchy.

3. **No `_parse_amount` or `_parse_date` helper methods** needed for coercion — the date is still parsed with `strptime` (Pydantic can't guess the `%d/%b/%Y` format), but the amount and currency coercion logic moved to the model validators where it belongs.

4. **All regexes still pre-compiled at class level** (fixes the current per-call `re.compile` bug).

5. **Multi-pattern support for SMS format evolution** — `_TXN_PATTERNS` is a list of compiled regexes tried in order (newest first). When HBL changes their SMS template, prepend the new pattern. Old patterns continue to match historical messages. The `_match_body()` helper tries each pattern and returns the first match.

---

### 4e. `parsers/__init__.py` — Auto-Discovery

```python
"""Auto-discovers all parser modules in this package on import.

Any module in the parsers/ directory that defines a BaseBankParser subclass
with BANK_ID and SHORT_CODES will be automatically registered.
"""
import importlib
import pkgutil
from pathlib import Path

# Import base first (so __init_subclass__ hook is ready)
from parsers.base import BaseBankParser

# Auto-import all sibling modules to trigger subclass registration
_package_dir = Path(__file__).parent
for _finder, _name, _ispkg in pkgutil.iter_modules([str(_package_dir)]):
    if _name != "base":
        importlib.import_module(f".{_name}", package=__name__)
```

This means dropping a new `.py` file into `parsers/` is truly all you need. No import to add, no registry to update.

---

### 4f. `db.py` — SQLite Persistent Store

This is the **central architectural change**. Instead of re-parsing the XML on every CLI invocation, transactions live in a local SQLite database. The database is the source of truth; XML files are just import sources.

```python
import sqlite3
from pathlib import Path

from models import Transaction, Currency, DEFAULT_TZ
from exceptions import SmsAnalyzerError

import logging
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".sms-txn-analyzer" / "transactions.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bank            TEXT    NOT NULL,
    txn_type        TEXT    NOT NULL,
    currency        TEXT    NOT NULL,
    amount          REAL    NOT NULL CHECK(amount > 0),
    vendor          TEXT    NOT NULL,
    date            TEXT    NOT NULL,  -- ISO 8601 with timezone
    card_last_four  INTEGER NOT NULL DEFAULT 0,
    category        TEXT    NOT NULL DEFAULT 'Uncategorized',
    sms_body        TEXT    NOT NULL,
    -- Dedup key: hash of sender address + body text
    sms_hash        TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_vendor   ON transactions(vendor);
CREATE INDEX IF NOT EXISTS idx_txn_bank     ON transactions(bank);
CREATE INDEX IF NOT EXISTS idx_txn_currency ON transactions(currency);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);

CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath    TEXT    NOT NULL,
    imported_at TEXT    NOT NULL,  -- ISO 8601
    sms_total   INTEGER NOT NULL DEFAULT 0,
    txns_added  INTEGER NOT NULL DEFAULT 0,
    duplicates  INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);
"""

class TransactionDB:
    """SQLite-backed persistent transaction store.

    All queries are SQL — no Python-side filtering or aggregation needed.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._setup()

    @classmethod
    def in_memory(cls) -> "TransactionDB":
        """Create an in-memory database. For testing only."""
        instance = object.__new__(cls)
        instance._db_path = ":memory:"
        instance._conn = sqlite3.connect(":memory:")
        instance._setup()
        return instance

    def _setup(self):
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Write operations (used by importer) ---

    def insert_transaction(self, txn: Transaction, sms_hash: str) -> bool:
        """Insert a transaction. Returns True if inserted, False if duplicate.

        Uses Pydantic's model_dump() for serialization — enum values are
        converted to strings, datetime to ISO format, automatically.
        """
        try:
            data = txn.model_dump(mode="json")  # enums → str, datetime → ISO string
            self._conn.execute(
                """INSERT INTO transactions
                   (bank, txn_type, currency, amount, vendor, date,
                    card_last_four, category, sms_body, sms_hash)
                   VALUES (:bank, :txn_type, :currency, :amount, :vendor, :date,
                           :card_last_four, :category, :sms_body, :hash)""",
                {**data, "hash": sms_hash},
            )
            return True
        except sqlite3.IntegrityError:
            # UNIQUE constraint on sms_hash → duplicate
            return False

    def log_import(self, filepath: str, sms_total: int,
                   txns_added: int, duplicates: int, errors: int):
        from datetime import datetime, timezone
        self._conn.execute(
            """INSERT INTO import_log (filepath, imported_at, sms_total,
               txns_added, duplicates, errors) VALUES (?, ?, ?, ?, ?, ?)""",
            (filepath, datetime.now(timezone.utc).isoformat(),
             sms_total, txns_added, duplicates, errors),
        )

    def commit(self):
        self._conn.commit()

    def update_categories(self, categorizer) -> int:
        """Re-categorize all transactions using the given categorizer.
        Returns the number of rows updated."""
        cursor = self._conn.execute("SELECT id, vendor FROM transactions")
        updated = 0
        for row in cursor:
            new_cat = categorizer.classify(row["vendor"])
            if new_cat != "Uncategorized":
                self._conn.execute(
                    "UPDATE transactions SET category = ? WHERE id = ?",
                    (new_cat, row["id"]),
                )
                updated += 1
        self._conn.commit()
        return updated

    # --- Read operations (used by CLI commands) ---

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def query_transactions(self, *, bank: str | None = None,
                           currency: str | None = None,
                           vendor: str | None = None,
                           from_date: str | None = None,
                           to_date: str | None = None,
                           min_amount: float | None = None,
                           category: str | None = None,
                           limit: int | None = None) -> list[Transaction]:
        """Flexible transaction query with optional filters.
        All filters are AND-combined. Returns validated Transaction models sorted by date.

        Uses Pydantic's model_validate() to hydrate each DB row back into a
        Transaction instance — re-applying all validators and type coercion.
        """
        clauses = []
        params = []

        if bank:
            clauses.append("bank = ?")
            params.append(bank.upper())
        if currency:
            clauses.append("currency = ?")
            params.append(currency.lower())
        if vendor:
            clauses.append("vendor LIKE ?")
            params.append(f"%{vendor}%")
        if from_date:
            clauses.append("date >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("date <= ?")
            params.append(to_date)
        if min_amount is not None:
            clauses.append("amount >= ?")
            params.append(min_amount)
        if category:
            clauses.append("category = ?")
            params.append(category)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM transactions WHERE {where} ORDER BY date"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [Transaction.model_validate(dict(row)) for row in rows]

    def unique_vendors(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT vendor FROM transactions ORDER BY vendor"
        ).fetchall()
        return [r["vendor"] for r in rows]

    def top_vendors(self, n: int = 10) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT vendor, COUNT(*) as cnt FROM transactions "
            "GROUP BY vendor ORDER BY cnt DESC LIMIT ?", (n,)
        ).fetchall()
        return [(r["vendor"], r["cnt"]) for r in rows]

    def monthly_summary(self) -> list[sqlite3.Row]:
        """Monthly spending aggregated by currency."""
        return self._conn.execute(
            """SELECT
                strftime('%Y-%m', date) as month,
                currency,
                SUM(amount) as total,
                COUNT(*) as txn_count
               FROM transactions
               GROUP BY month, currency
               ORDER BY month, currency"""
        ).fetchall()

    def category_summary(self) -> list[sqlite3.Row]:
        """Spending totals by category."""
        return self._conn.execute(
            """SELECT category, currency, SUM(amount) as total, COUNT(*) as txn_count
               FROM transactions
               GROUP BY category, currency
               ORDER BY total DESC"""
        ).fetchall()

    def import_history(self) -> list[sqlite3.Row]:
        """List all previous imports."""
        return self._conn.execute(
            "SELECT * FROM import_log ORDER BY imported_at DESC"
        ).fetchall()

    def stats(self) -> dict:
        """Quick database statistics."""
        row = self._conn.execute(
            """SELECT COUNT(*) as total_txns,
                      COUNT(DISTINCT vendor) as unique_vendors,
                      COUNT(DISTINCT bank) as banks,
                      MIN(date) as earliest,
                      MAX(date) as latest
               FROM transactions"""
        ).fetchone()
        return dict(row)
```

**Why SQLite replaces both `dedup.py` and `store.py`:**

| Concern | Old Design (In-Memory) | New Design (SQLite) |
|---------|----------------------|---------------------|
| **Dedup** | Custom `DuplicateDetector` class with a Python `set` | `UNIQUE` constraint on `sms_hash` (address + body) + `INSERT OR IGNORE`. Zero custom code. |
| **Filtering** | Custom `TransactionFilter` callables composed in Python | SQL `WHERE` clauses with parameterized queries. Indexed. |
| **Aggregation** | `defaultdict` + manual loops in Python | `GROUP BY` / `SUM()` / `COUNT()` in SQL. One line each. |
| **Persistence** | None — re-parse XML every run | Automatic. Parse once, query forever. |
| **Incremental updates** | Not possible — must re-parse entire file | `INSERT OR IGNORE` skips existing rows. Import a newer backup and only new messages are added. |
| **Ad-hoc queries** | Impossible without code changes | Users can open the `.db` file in any SQLite tool and run arbitrary SQL. |

---

### 4g. `importer.py` — XML Import Pipeline

Replaces the old `loader.py` concept. This module reads an XML backup file, parses transactions via the parser registry, and inserts them into the SQLite database.

```python
import hashlib
import xml.etree.ElementTree as ET

from db import TransactionDB
from models import ImportResult
from parsers.base import BaseBankParser
from categorizer import Categorizer
from exceptions import FileLoadError, ParseError

import logging
logger = logging.getLogger(__name__)

# ImportResult is now a Pydantic model defined in models.py


def _hash_sms(body: str, address: str) -> str:
    """Compute a dedup hash from sender address + body text.

    Includes the sender address so that identical body text from different
    banks (unlikely but possible) is not incorrectly deduplicated.
    SHA-256 is fast and sufficient for non-adversarial duplicate detection.
    """
    key = f"{address.strip()}:{body.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def import_sms_backup(filepath: str, db: TransactionDB,
                      categorizer: Categorizer | None = None) -> ImportResult:
    """Parse an SMS backup XML file and insert new transactions into the database.

    Duplicate messages (same sender + body text) are silently skipped via
    the DB's UNIQUE constraint. This makes it safe to re-import the same
    file or import overlapping backup files.
    """
    result = ImportResult(filepath=filepath)

    # Use iterparse for memory efficiency — processes elements one at a
    # time and frees them after use, instead of loading the entire XML DOM.
    try:
        context = ET.iterparse(filepath, events=("end",))
    except FileNotFoundError:
        raise FileLoadError(f"File not found: {filepath}")
    except PermissionError:
        raise FileLoadError(f"Permission denied: {filepath}")

    for event, elem in context:
        if elem.tag == "mms":
            result.skipped_mms_count += 1
            elem.clear()
            continue

        if elem.tag != "sms":
            continue

        result.total_sms_count += 1
        address = elem.attrib.get("address", "")
        body = elem.attrib.get("body", "").strip()  # normalize once, used everywhere
        timestamp_ms = int(elem.attrib.get("date", "0"))

        if not address or not body:
            continue

        # Route to the correct bank parser via registry lookup
        parser = BaseBankParser.get_parser_for_address(address)
        if parser is None:
            result.bank_counts["OTHER"] += 1
            continue

        result.bank_counts[parser.BANK_ID] += 1

        if not parser.is_transaction_sms(body):
            continue

        try:
            txn = parser.parse_transaction(body, timestamp_ms)

            # Apply category if categorizer provided.
            # model_copy() cleanly updates a frozen Pydantic model — no need
            # to reconstruct all fields manually (the old frozen dataclass pain).
            if categorizer:
                category = categorizer.classify(txn.vendor)
                txn = txn.model_copy(update={"category": category})

            # Insert into DB — duplicates are caught by UNIQUE constraint
            sms_hash = _hash_sms(body, address)
            inserted = db.insert_transaction(txn, sms_hash)
            if inserted:
                result.txns_added += 1
            else:
                result.duplicates_skipped += 1

        except ParseError as e:
            result.parse_errors.append(str(e))
            logger.warning("Parse error: %s", e)

        elem.clear()  # free memory after processing each element

    # Log this import and commit everything atomically (single commit
    # ensures both transaction inserts and import log are consistent)
    db.log_import(
        filepath=filepath,
        sms_total=result.total_sms_count,
        txns_added=result.txns_added,
        duplicates=result.duplicates_skipped,
        errors=len(result.parse_errors),
    )
    db.commit()  # single atomic commit for all inserts + import log

    return result
```

**Key design decisions:**

1. **Dedup is now a DB concern, not an application concern.** The `UNIQUE` constraint on `sms_hash` means you can import the same file twice, import overlapping files, or import a newer backup that contains all previous messages — and only genuinely new transactions get added. No custom `DuplicateDetector` class needed.

2. **Hash includes sender address + body text** — the hash is computed from `f"{address}:{body}"`, not body alone. This prevents the (unlikely but possible) case where identical body text from different senders is incorrectly deduplicated. SHA-256 produces a fixed-length (64 char) key, making the UNIQUE index efficient.

3. **Parse errors are non-fatal.** They're collected in `ImportResult` and logged, but don't stop the import. The CLI reports them in the summary.

4. **Import log** tracks what files have been imported and when, so users can see the history and know when their DB was last updated.

5. **`iterparse` for memory efficiency.** Unlike the current `ET.parse()` which loads the entire XML DOM into memory, `ET.iterparse()` processes elements one at a time. Each element is cleared after processing (`elem.clear()`), keeping memory usage constant regardless of backup file size.

6. **Single atomic commit.** All transaction inserts and the import log entry are committed in one `db.commit()` call. This prevents the inconsistency where transactions exist in the DB but the import log has no record of them (which would happen if the process crashed between two separate commits).

---

### 4i. `categorizer.py` — Vendor Classification

A new component backed by a TOML config file. The config structure itself is validated by a Pydantic model, catching malformed category files at load time rather than at classification time.

```python
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from models import Transaction
from exceptions import ConfigError

DEFAULT_CATEGORIES_PATH = Path(__file__).parent.parent.parent / "categories.toml"


class CategoryRule(BaseModel):
    """A single category rule from the TOML config."""
    keywords: list[str] = Field(min_length=1, description="List of vendor substrings")


class CategoriesConfig(BaseModel):
    """Validated structure of categories.toml."""
    categories: dict[str, CategoryRule] = Field(default_factory=dict)


class Categorizer:
    """Classifies transactions into spending categories based on vendor name."""

    def __init__(self, config_path: Path | None = None):
        self._rules: dict[str, list[str]] = {}
        path = config_path or DEFAULT_CATEGORIES_PATH
        if path.exists():
            with open(path, "rb") as f:
                raw = tomllib.load(f)
            try:
                config = CategoriesConfig.model_validate(raw)
            except ValidationError as e:
                raise ConfigError(f"Invalid categories file {path}: {e}") from e
            for category, rule in config.categories.items():
                self._rules[category] = [kw.lower() for kw in rule.keywords]

    def classify(self, vendor: str) -> str:
        """Return the category for a vendor name, or 'Uncategorized'."""
        vendor_lower = vendor.lower()
        for category, keywords in self._rules.items():
            if any(kw in vendor_lower for kw in keywords):
                return category
        return "Uncategorized"

    def apply(self, transactions: list[Transaction]) -> list[Transaction]:
        """Return new Transaction list with categories applied.

        Uses Pydantic's model_copy() to update the frozen model cleanly —
        no need to reconstruct every field manually.
        """
        return [
            txn.model_copy(update={"category": self.classify(txn.vendor)})
            for txn in transactions
        ]
```

Example `categories.toml`:

```toml
[categories.Groceries]
keywords = ["imtiaz", "carrefour", "metro", "al-fatah", "chase up"]

[categories.Fuel]
keywords = ["pso", "shell", "total", "attock", "byco"]

[categories.Restaurants]
keywords = ["mcdonald", "kfc", "domino", "pizza hut", "subway"]

[categories.Online_Shopping]
keywords = ["amazon", "daraz", "alibaba"]

[categories.Medical]
keywords = ["pharmacy", "hospital", "clinic", "lab", "agha khan"]

[categories.Fashion]
keywords = ["khaadi", "sapphire", "gul ahmed", "junaid jamshed"]
```

---

### 4j. `formatters.py` — Output Formatting

With Pydantic models, JSON serialization is trivial — `model_dump(mode="json")` handles enum-to-string conversion and datetime-to-ISO formatting automatically. No more hand-built dict comprehensions.

```python
import csv
import io
import json
import sqlite3
from models import Transaction


def format_transactions(txns: list[Transaction], fmt: str) -> str:
    """Format a list of Transaction models in the specified format."""
    match fmt:
        case "json":
            return _txns_to_json(txns)
        case "csv":
            return _txns_to_csv(txns)
        case "table" | _:
            return _txns_to_table(txns)


def format_monthly_rows(rows: list[sqlite3.Row], fmt: str) -> str:
    """Format monthly summary SQL rows."""
    match fmt:
        case "json":
            return json.dumps(
                [{"month": r["month"], "currency": r["currency"],
                  "total": r["total"], "txn_count": r["txn_count"]}
                 for r in rows],
                indent=2,
            )
        case "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Month", "Currency", "Total", "Txn Count"])
            for r in rows:
                writer.writerow([r["month"], r["currency"].upper(),
                                 f"{r['total']:.2f}", r["txn_count"]])
            return buf.getvalue()
        case _:
            return _monthly_to_table(rows)


def format_category_rows(rows: list[sqlite3.Row], fmt: str) -> str:
    """Format category summary SQL rows."""
    match fmt:
        case "json":
            return json.dumps(
                [{"category": r["category"], "currency": r["currency"],
                  "total": r["total"], "txn_count": r["txn_count"]}
                 for r in rows],
                indent=2,
            )
        case _:
            lines = ["Spending by category:\n"]
            for r in rows:
                lines.append(
                    f"  {r['category']:<25} {r['currency'].upper():>4} "
                    f"{r['total']:>12,.2f}  ({r['txn_count']} txns)"
                )
            return "\n".join(lines)


def _txns_to_table(txns: list[Transaction]) -> str:
    if not txns:
        return "No transactions found."
    lines = []
    header = (f"{'#':>4}  {'Date':>12}  {'Currency':>8}  {'Amount':>12}  "
              f"{'Vendor':<30}  {'Card':>4}  {'Category':<18}")
    lines.append(header)
    lines.append("-" * len(header))
    for i, txn in enumerate(txns, 1):
        lines.append(
            f"{i:>4}  {txn.date.strftime('%Y-%m-%d'):>12}  "
            f"{txn.currency.value.upper():>8}  "
            f"{txn.amount:>12,.2f}  "
            f"{txn.vendor:<30.30}  "
            f"{txn.card_last_four:>4}  "
            f"{txn.category:<18}"
        )
    return "\n".join(lines)


def _monthly_to_table(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "No data."
    lines = []
    header = f"{'Month':>8}  {'Currency':>8}  {'Total':>14}  {'Txns':>5}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        lines.append(
            f"{r['month']:>8}  {r['currency'].upper():>8}  "
            f"{r['total']:>14,.2f}  {r['txn_count']:>5}"
        )
    return "\n".join(lines)


def _txns_to_json(txns: list[Transaction]) -> str:
    """Serialize transactions to JSON using Pydantic's built-in serialization.

    model_dump(mode='json') converts:
      - Currency enum → string ('pkr')
      - TransactionType enum → string ('credit_card')
      - datetime → ISO 8601 string
    No manual dict-building needed.
    """
    return json.dumps(
        [txn.model_dump(mode="json", exclude={"sms_body"}) for txn in txns],
        indent=2,
    )


def _txns_to_csv(txns: list[Transaction]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Bank", "Date", "Currency", "Amount", "Vendor",
                     "Card Last 4", "Category"])
    for txn in txns:
        # model_dump(mode="json") handles all type conversions
        d = txn.model_dump(mode="json", exclude={"sms_body", "txn_type"})
        writer.writerow([
            d["bank"], d["date"], d["currency"].upper(),
            f"{d['amount']:.2f}", d["vendor"], d["card_last_four"], d["category"],
        ])
    return buf.getvalue()
```

---

### 4k. `cli.py` — Click CLI (Thin Layer)

The CLI now has two distinct command groups: **`import`** (write path) and **everything else** (read path). Query commands never touch XML files — they just open the SQLite database.

```python
import logging
from pathlib import Path
from time import perf_counter

import click

from db import TransactionDB, DEFAULT_DB_PATH
from importer import import_sms_backup
from categorizer import Categorizer
from exceptions import SmsAnalyzerError
from models import Currency
import formatters

@click.group()
@click.version_option("1.0.0", prog_name="sms-txn-analyzer")
@click.option("--db", "db_path", type=click.Path(path_type=Path),
              default=None, help=f"Database path (default: {DEFAULT_DB_PATH})")
@click.option("--format", "output_format", type=click.Choice(["table", "csv", "json"]),
              default="table", help="Output format.")
@click.option("--debug", is_flag=True, help="Show debug output and tracebacks.")
@click.pass_context
def cli(ctx, db_path, output_format, debug):
    """Analyze credit card transactions from SMS backup files."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    ctx.ensure_object(dict)
    ctx.obj["output_format"] = output_format
    ctx.obj["db_path"] = db_path

# ──────────────────────────────────────────────
#  IMPORT COMMAND (write path — touches XML)
# ──────────────────────────────────────────────

@cli.command(name="import")
@click.argument("filepath", type=click.Path(exists=True, path_type=Path))
@click.option("--categories", "categories_file", type=click.Path(path_type=Path),
              default=None, help="Path to categories TOML file.")
@click.pass_context
def import_cmd(ctx, filepath, categories_file):
    """Import transactions from an SMS backup XML file into the database.

    Safe to run multiple times — duplicate messages are automatically skipped.
    Import a newer backup file to add only the new messages.
    """
    try:
        categorizer = Categorizer(categories_file)
        with TransactionDB(ctx.obj["db_path"]) as db:
            t0 = perf_counter()
            result = import_sms_backup(str(filepath), db, categorizer)
            elapsed = perf_counter() - t0

            click.echo(f"Imported from: {filepath}")
            click.echo(f"  Time:              {elapsed:.2f}s")
            click.echo(f"  SMS messages read:  {result.total_sms_count}")
            click.echo(f"  Transactions added: {result.txns_added}")
            click.echo(f"  Duplicates skipped: {result.duplicates_skipped}")
            if result.parse_errors:
                click.echo(f"  Parse errors:       {len(result.parse_errors)}")
            for bank, count in sorted(result.bank_counts.items()):
                click.echo(f"  {bank}: {count} messages")
            click.echo(f"\nDatabase: {db._db_path}")
            click.echo(f"Total transactions in DB: {db.count()}")
    except SmsAnalyzerError as e:
        raise click.ClickException(str(e))

# ──────────────────────────────────────────────────
#  QUERY COMMANDS (read path — SQL only, no XML)
# ──────────────────────────────────────────────────

@cli.command()
@click.option("--bank", default=None, help="Filter by bank ID (e.g., HBL).")
@click.option("--currency", type=click.Choice([c.value for c in Currency], case_sensitive=False),
              default=None, help="Filter by currency.")
@click.option("--vendor", default=None, help="Filter by vendor name (substring match).")
@click.option("--from", "from_date", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD).")
@click.option("--min-amount", type=float, default=None, help="Minimum transaction amount.")
@click.option("--category", default=None, help="Filter by spending category.")
@click.option("--limit", "-n", type=int, default=None, help="Max transactions to show.")
@click.pass_context
def transactions(ctx, bank, currency, vendor, from_date, to_date, min_amount, category, limit):
    """List transactions with optional filters. All filters are AND-combined."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        rows = db.query_transactions(
            bank=bank, currency=currency, vendor=vendor,
            from_date=from_date, to_date=to_date,
            min_amount=min_amount, category=category,
            limit=limit,
        )
        click.echo(formatters.format_transactions(rows, ctx.obj["output_format"]))

@cli.command()
@click.pass_context
def vendors(ctx):
    """List unique vendors from all transactions."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        vendor_list = db.unique_vendors()
        fmt = ctx.obj["output_format"]

        if fmt == "json":
            import json
            click.echo(json.dumps(vendor_list, indent=2))
        elif fmt == "csv":
            for v in vendor_list:
                click.echo(v)
        else:
            click.echo(f"Found {len(vendor_list)} unique vendors:\n")
            for i, v in enumerate(vendor_list, 1):
                click.echo(f"  {i:>3}. {v}")

@cli.command()
@click.pass_context
def monthly(ctx):
    """Show month-by-month spending summary."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        rows = db.monthly_summary()
        click.echo(formatters.format_monthly_rows(rows, ctx.obj["output_format"]))

@cli.command()
@click.pass_context
def categories(ctx):
    """Show spending breakdown by category."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        rows = db.category_summary()
        click.echo(formatters.format_category_rows(rows, ctx.obj["output_format"]))

@cli.command()
@click.option("-n", default=10, help="Number of top vendors to show.")
@click.pass_context
def top_vendors(ctx, n):
    """Show most frequent vendors by transaction count."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        top = db.top_vendors(n)
        fmt = ctx.obj["output_format"]

        if fmt == "json":
            import json
            click.echo(json.dumps([{"vendor": v, "count": c} for v, c in top], indent=2))
        else:
            click.echo(f"Top {n} vendors by transaction count:\n")
            for i, (vendor, count) in enumerate(top, 1):
                click.echo(f"  {i:>3}. {vendor:<30} ({count} txns)")

@cli.command()
@click.pass_context
def stats(ctx):
    """Show database statistics and import history."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        s = db.stats()
        click.echo(f"Database: {db._db_path}")
        click.echo(f"  Total transactions: {s['total_txns']}")
        click.echo(f"  Unique vendors:     {s['unique_vendors']}")
        click.echo(f"  Banks:              {s['banks']}")
        click.echo(f"  Date range:         {s['earliest'] or 'N/A'} to {s['latest'] or 'N/A'}")

        history = db.import_history()
        if history:
            click.echo(f"\nImport history ({len(history)} imports):")
            for h in history:
                click.echo(f"  {h['imported_at'][:19]}  {h['filepath']}"
                           f"  (+{h['txns_added']} txns, {h['duplicates']} dups)")

@cli.command()
@click.option("--categories", "categories_file", type=click.Path(path_type=Path),
              default=None, help="Path to categories TOML file.")
@click.pass_context
def recategorize(ctx, categories_file):
    """Re-apply category rules to all existing transactions."""
    categorizer = Categorizer(categories_file)
    with TransactionDB(ctx.obj["db_path"]) as db:
        updated = db.update_categories(categorizer)
        click.echo(f"Updated {updated} transactions with new categories.")

def main():
    cli()
```

---

## 5. CLI Interface Design

### The Import / Query Split

The most significant UX change: **importing data and querying data are separate operations.**

```
sms-txn-analyzer import backup.xml      # Parse XML → insert into SQLite (once)
sms-txn-analyzer transactions            # Query SQLite (instant, repeatable)
sms-txn-analyzer monthly                 # Query SQLite (instant, repeatable)
```

No more passing the XML file path to every command. The database persists between invocations.

### Command Comparison: Current vs Proposed

| Current | Proposed | What Changed |
|---------|----------|-------------|
| `python hbl_sms_query_tool.py file.xml list_all_vendors` | `sms-txn-analyzer import file.xml` then `sms-txn-analyzer vendors` | Import once, query many times |
| `python hbl_sms_query_tool.py file.xml list_all_cc_txns` | `sms-txn-analyzer transactions` | Filterable via options, instant |
| `python hbl_sms_query_tool.py file.xml monthly_cc_spending_summary` | `sms-txn-analyzer monthly` | Concise, multi-format |
| *(not available)* | `sms-txn-analyzer categories` | **New**: spending by category |
| *(not available)* | `sms-txn-analyzer top-vendors` | **New**: most frequent vendors |
| *(not available)* | `sms-txn-analyzer stats` | **New**: DB statistics + import history |
| *(not available)* | `sms-txn-analyzer recategorize` | **New**: re-apply updated category rules |
| *(not available)* | `--format csv/json` on any command | **New**: machine-readable output |
| *(not available)* | `--vendor`, `--from`, `--to`, `--currency`, `--min-amount`, `--category`, `--limit` on `transactions` | **New**: composable filters |

### Global Options (on the group)

| Option | Purpose |
|--------|---------|
| `--db PATH` | Custom database path (default: `~/.sms-txn-analyzer/transactions.db`) |
| `--format table\|csv\|json` | Output format (default: table) |
| `--debug` | Enable debug logging and full tracebacks |
| `--version` | Show version |

### Example Usage

```bash
# Step 1: Import (run once per backup file — safe to re-run)
sms-txn-analyzer import sms-backup-2024-01.xml
sms-txn-analyzer import sms-backup-2024-06.xml   # only new msgs added
sms-txn-analyzer import sms-backup-2024-12.xml   # overlapping msgs skipped

# Step 2: Query (instant — reads from SQLite, no XML parsing)
sms-txn-analyzer transactions
sms-txn-analyzer transactions --from 2023-10-01 --to 2023-12-31 --currency pkr
sms-txn-analyzer transactions --vendor SHELL --min-amount 5000
sms-txn-analyzer transactions --category Groceries --format csv > groceries.csv

# Summaries
sms-txn-analyzer monthly --format json | jq '.[] | select(.currency == "pkr")'
sms-txn-analyzer categories
sms-txn-analyzer top-vendors -n 5

# Database management
sms-txn-analyzer stats                           # show DB size, date range, import history
sms-txn-analyzer recategorize --categories updated_categories.toml

# Use a different database
sms-txn-analyzer --db ./test.db import test-backup.xml
sms-txn-analyzer --db ./test.db transactions
```

---

## 6. Spending Categorization System

This is entirely new — the current app has a `SpendingCategories` enum that is never used.

### How It Works

1. A `categories.toml` file ships with the project (default mappings)
2. Users can override with `--categories path/to/custom.toml`
3. The `Categorizer` does case-insensitive substring matching of vendor names against keyword lists
4. Each transaction gets a `category` field set after parsing, before storage
5. The `categories` CLI command shows a breakdown
6. Filters support `--category` to restrict other commands by category

### Future Enhancements (Not in Scope for v1)

- Fuzzy matching for vendor name variations (via `difflib.get_close_matches`)
- A `--show-uncategorized` flag that lists vendors with no category match
- An interactive `categorize` command that prompts the user for each unknown vendor

---

## 7. Output & Reporting

### Format Support Matrix

| Command | `--format table` | `--format csv` | `--format json` |
|---------|:-:|:-:|:-:|
| `transactions` | Aligned columns with header | Standard CSV with headers | Array of objects |
| `vendors` | Numbered list | One per line | JSON array |
| `monthly` | Month/currency columns | Flattened rows | Array of period objects |
| `categories` | Category + total | Two columns | Object |
| `top-vendors` | Numbered + count | Vendor, count | Array of objects |

### Table format is the default (optimized for terminal)

```
   #          Date  Currency        Amount  Vendor                          Card  Category
------------------------------------------------------------------------------------------
   1    2023-09-25       PKR    22,001.00  A MOOSAJEE SONS                 8526  Fashion
   2    2023-10-01       PKR    25,170.49  IMTIAZ SUPER MARKET             8526  Groceries
   3    2023-10-03       USD        49.99  AMAZON.COM                      8526  Online_Shopping
```

---

## 8. Error Handling Strategy

### Current (Broken)

```
print("ERROR: unable to parse txn amount...")  # user may never see this
assert ccTxn is not None                       # vanishes with python -O
```

### Proposed (Layered)

```
Layer 0: Pydantic models  → ValidationError on invalid data (automatic, structured)
Layer 1: Bank parsers     → catch ValidationError, wrap in ParseError with SMS context
Layer 2: Importer         → catches ParseError, collects into ImportResult.parse_errors
Layer 3: CLI              → catches SmsAnalyzerError, converts to click.ClickException
Layer 4: --debug flag     → lets exceptions propagate for full traceback
```

**What Pydantic adds to error handling:**

The current code validates with `assert` (vanishes with `-O`) and `print()` (output silently lost). The dataclass `__post_init__` approach improves this with explicit `ValueError` checks, but you still write each validation by hand and get one error at a time.

Pydantic validates **all fields at once** and produces a structured `ValidationError` that lists every invalid field, the value that was provided, and the constraint that failed:

```python
# Example: constructing a Transaction with bad data
Transaction(bank="HBL", txn_type="credit_card", currency="XYZ",
            amount=-5, vendor="", date=naive_datetime, card_last_four=99999)

# Pydantic raises ValidationError with ALL failures:
# 1 validation error for Transaction
# currency
#   Input should be 'pkr', 'usd', 'cad', ... [type=enum, input_value='xyz']
# amount
#   Input should be greater than 0 [type=greater_than, input_value=-5]
# vendor
#   String should have at least 1 character [type=string_too_short, input_value='']
# date
#   Transaction date must be timezone-aware [type=value_error]
# card_last_four
#   Input should be less than or equal to 9999 [type=less_than_equal, input_value=99999]
```

This structured error is caught by the parser and wrapped in `ParseError`, which the importer collects non-fatally. The user sees a clear summary of what went wrong and which SMS message caused it.

**Principle:** Pydantic handles field-level validation declaratively. Parsers handle SMS-level error wrapping. The importer collects errors non-fatally. The CLI formats them for the user. Business logic never imports Click.

---

## 9. Testing Strategy

### Test Pyramid

```
                    ┌──────────────┐
                    │  CLI tests   │  (CliRunner, integration)
                    │   ~10 tests  │
                  ┌─┴──────────────┴─┐
                  │   DB + Formatters │  (SQL queries, output)
                  │    ~18 tests      │
                ┌─┴──────────────────┴─┐
                │   Parser unit tests   │  (per bank, regex, edge cases)
                │     ~25 tests         │
              ┌─┴────────────────────────┴─┐
              │ Model + Importer + Categorizer │ (validation, import, classify)
              │         ~17 tests              │
              └────────────────────────────────┘
```

### What Gets Tested (vs Current)

| Area | Current Tests | Proposed Tests |
|------|:---:|:---:|
| SMS identification (`isSmsFromHBL`) | 2 | 2 (preserved) |
| CC txn detection (`isMsgCreditCardTxn`) | 2 | 2 (preserved) |
| Transaction extraction (`extractDetailsFromTxnMsg`) | **0** | 6+ (PKR, USD, commas, edge cases) |
| Amount parsing | **0** | 4+ (various formats, invalid input) |
| Date parsing | **0** | 3+ (various months, malformed) |
| Transaction model validation | **0** | 7 (valid, coercion, negative amount, empty vendor, naive date, unknown currency, model_dump round-trip) |
| SQLite dedup (UNIQUE constraint) | **0** | 3 (new insert, duplicate, re-import same file) |
| DB query filters (SQL WHERE) | **0** | 8+ (each filter, combined filters, empty results) |
| Monthly/category aggregation (SQL) | **0** | 4 (single month, multi-month, multi-currency, by category) |
| Import pipeline (XML → DB) | **0** | 4 (valid file, corrupt XML, overlapping imports, MMS skipped) |
| Categorizer | **0** | 4 (exact match, substring, case-insensitive, uncategorized) |
| Output formatting (table/csv/json) | **0** | 6 (each format × 2 commands) |
| CLI integration | **0** | 6 (import, query, stats, bad file, version, help) |
| **Total** | **4** | **~75** |

### Testing SQLite

All DB tests use an **in-memory SQLite database** (`":memory:"`), making them fast and isolated. No filesystem cleanup needed.

```python
@pytest.fixture
def db():
    """Fresh in-memory database for each test."""
    from db import TransactionDB
    return TransactionDB.in_memory()
```

### Testing Pydantic Models

Pydantic makes model testing clean and expressive. Invalid data raises `ValidationError` with structured details — tests can assert on specific error types and field names:

```python
import pytest
from pydantic import ValidationError
from models import Transaction, TransactionType, Currency, DEFAULT_TZ
from datetime import datetime


def test_valid_transaction():
    """Pydantic accepts valid data and coerces types automatically."""
    txn = Transaction(
        bank="HBL",
        txn_type=TransactionType.CREDIT_CARD,
        currency="pkr",            # string → Currency enum (via validator)
        amount="25,170.49",         # comma string → float (via validator)
        vendor="IMTIAZ SUPER MARKET",
        date=datetime(2023, 10, 1, tzinfo=DEFAULT_TZ),
        card_last_four=8526,
    )
    assert txn.currency == Currency.PKR
    assert txn.amount == 25170.49


def test_negative_amount_rejected():
    with pytest.raises(ValidationError, match="greater than 0"):
        Transaction(
            bank="HBL", txn_type="credit_card", currency="pkr",
            amount=-100, vendor="SHOP", date=datetime(2023, 1, 1, tzinfo=DEFAULT_TZ),
        )


def test_empty_vendor_rejected():
    with pytest.raises(ValidationError, match="at least 1 character"):
        Transaction(
            bank="HBL", txn_type="credit_card", currency="pkr",
            amount=100, vendor="", date=datetime(2023, 1, 1, tzinfo=DEFAULT_TZ),
        )


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        Transaction(
            bank="HBL", txn_type="credit_card", currency="pkr",
            amount=100, vendor="SHOP", date=datetime(2023, 1, 1),  # no tzinfo
        )


def test_unknown_currency_rejected():
    with pytest.raises(ValidationError, match="currency"):
        Transaction(
            bank="HBL", txn_type="credit_card", currency="XYZ",
            amount=100, vendor="SHOP", date=datetime(2023, 1, 1, tzinfo=DEFAULT_TZ),
        )


def test_model_dump_for_sqlite():
    """model_dump(mode='json') produces a dict ready for SQL INSERT."""
    txn = Transaction(
        bank="HBL", txn_type="credit_card", currency="pkr",
        amount=1000, vendor="SHELL", date=datetime(2023, 10, 1, tzinfo=DEFAULT_TZ),
    )
    d = txn.model_dump(mode="json")
    assert isinstance(d["date"], str)       # ISO string, not datetime
    assert isinstance(d["currency"], str)   # "pkr", not Currency.PKR enum
    assert isinstance(d["amount"], float)   # 1000.0, not Decimal or string


def test_model_copy_updates_category():
    """model_copy() creates a new instance with updated fields (frozen model)."""
    txn = Transaction(
        bank="HBL", txn_type="credit_card", currency="pkr",
        amount=1000, vendor="SHELL", date=datetime(2023, 10, 1, tzinfo=DEFAULT_TZ),
    )
    updated = txn.model_copy(update={"category": "Fuel"})
    assert updated.category == "Fuel"
    assert txn.category == "Uncategorized"  # original unchanged
```

**What Pydantic enables in testing:**
- `pytest.raises(ValidationError, match="...")` tests that specific constraints fire on bad input
- No need to test manual coercion logic — if the `field_validator` on `amount` handles `"25,170.49"`, you test it once on the model, not again in each parser
- `model_dump()` / `model_copy()` are tested once here, then trusted in `db.py` / `importer.py` / `formatters.py`
- The round-trip test (`model_dump` → INSERT → SELECT → `model_validate`) gives you confidence that nothing is lost in serialization

### Test Framework

Switch from `unittest` to **pytest** — it's the de facto standard, requires less boilerplate (no class required, plain `assert`, fixtures via `conftest.py`), and has better output. `pytest` can still run existing `unittest`-style tests during migration.

### Shared Fixtures (`conftest.py`)

```python
import pytest
import xml.etree.ElementTree as ET
from models import Transaction, TransactionType, CurrencyAmount, Currency, DEFAULT_TZ
from datetime import datetime

@pytest.fixture
def sample_hbl_sms():
    """A valid HBL CC transaction SMS element."""
    sms = ET.Element("sms")
    sms.set("address", "4250")
    sms.set("body", "Dear Customer, Your HBL CreditCard (ending with 8526) "
            "has been charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023.")
    sms.set("date", "1696185000000")
    return sms

@pytest.fixture
def sample_transaction():
    """A valid Transaction instance (flat model — no nested CurrencyAmount)."""
    return Transaction(
        bank="HBL",
        txn_type=TransactionType.CREDIT_CARD,
        currency=Currency.PKR,
        amount=25170.49,
        vendor="IMTIAZ SUPER MARKET",
        date=datetime(2023, 10, 1, tzinfo=DEFAULT_TZ),
        card_last_four=8526,
    )
```

---

## 10. Migration Path

A phased approach to avoid a risky big-bang rewrite.

### Phase 1: Foundation (Non-Breaking)

1. Add `pyproject.toml` with project metadata, dependencies, and `[project.scripts]` entry point
2. Create `src/sms_txn_analyzer/` package with `__init__.py`
3. Add `models.py` (new Pydantic `Transaction` model alongside existing `CreditCardTxnDC`)
4. Add `exceptions.py`
5. Add `db.py` with SQLite schema and `TransactionDB` class
6. Add empty `tests/conftest.py` with in-memory DB fixture

**Outcome:** New modules exist alongside old code. Nothing breaks. The SQLite schema is defined and testable.

### Phase 2: Parser Registry

1. Add `parsers/base.py` with `BaseBankParser` ABC
2. Add `parsers/__init__.py` with auto-discovery
3. Port `HBLSmsParser` to new `parsers/hbl.py` inheriting from `BaseBankParser`
4. Add comprehensive parser tests
5. Delete dead instance methods from old `HBLSmsParser`

**Outcome:** HBL parsing works through both old and new paths. New parser tests provide safety net.

### Phase 3: Import Pipeline + SQLite Store

1. Add `importer.py` (XML → parse → INSERT into SQLite)
2. Add `categorizer.py` + `categories.toml`
3. Add `formatters.py` (table/CSV/JSON output from DB rows)
4. Add tests: import pipeline, DB queries, dedup via UNIQUE constraint, categorizer
5. Verify by importing a real backup file and querying the resulting DB

**Outcome:** Complete new pipeline exists and is tested. Data flows from XML into SQLite and back out as formatted output.

### Phase 4: CLI Switchover

1. Create new `cli.py` with `import` command and all query commands
2. Wire up `[project.scripts]` entry point to new CLI
3. Test via `CliRunner` (import + query round-trip)
4. Delete old files: `hbl_sms_query_tool.py`, `sms_backup_file_parser.py`, old `parser/hbl_sms_parser.py`, `cc_txn.py`
5. Clean up: remove `common.py` dead code (`SpendingCategories`, duplicate `DEFAULT_TZ`)

**Outcome:** Clean codebase with only new architecture. Old files are gone.

### Phase 5: Polish & Extend

1. Add stub parsers for Faysal, SCB, Meezan (just `is_transaction_sms` returning `False` initially)
2. Add `stats` and `recategorize` commands
3. Add Rich output as an optional enhancement
4. Add `--show-uncategorized` flag
5. Expand test coverage for edge cases
6. Update `CLAUDE.md` and `README.md`

---

## 11. Why SQLite Over In-Memory

An earlier draft of this proposal used a Python in-memory `TransactionStore` class with custom filter predicates, `defaultdict`-based aggregation, and a `DuplicateDetector` class. That design was replaced with SQLite after recognizing a fundamental flaw: **the in-memory approach re-parses the entire XML file on every CLI invocation**.

### The Core Problem

The current application (and the in-memory redesign) treats the XML backup file as the data source for every command. To list transactions, it must:

1. Load the entire XML DOM into memory
2. Iterate every SMS element
3. Compute hashes for duplicate detection
4. Run regex parsing on every bank message
5. Build Python data structures in memory
6. Only then answer the user's query

For a 10,000-message backup, this takes seconds every time. A SQLite database eliminates steps 1–5 on query — the data is already structured, indexed, and ready.

### What SQLite Buys Us (for Zero Extra Dependencies)

| Capability | In-Memory Python | SQLite (`sqlite3` stdlib) |
|-----------|:---:|:---:|
| Persistence between CLI runs | No | Yes |
| Incremental import (new backup → add only new msgs) | No | `INSERT OR IGNORE` |
| Duplicate detection | Custom class | `UNIQUE` constraint |
| Filtering (date, vendor, amount, currency, category) | Custom predicate chain | `WHERE` clause |
| Aggregation (monthly, by category) | `defaultdict` + loops | `GROUP BY` + `SUM()` |
| Indexed lookups | No (linear scan) | B-tree indexes |
| Ad-hoc queries by user | Impossible | Any SQLite tool |
| Export to CSV | Custom code | `sqlite3` CLI: `.mode csv` `.output file.csv` |
| Backup/portability | N/A | Copy one `.db` file |
| Import history / audit trail | N/A | `import_log` table |
| Re-categorize without re-importing | Impossible | `UPDATE ... SET category = ?` |
| Memory usage for large datasets | All in RAM | Disk-backed, OS page cache |

### What SQLite Costs

- **One-time setup**: The database file must be created and populated before querying. This is a feature (import once, query forever) but it does mean the first run requires `sms-txn-analyzer import file.xml` before `sms-txn-analyzer transactions` will return results.
- **Slight complexity in the DB layer**: ~120 lines of schema + query methods in `db.py`. But this replaces ~90 lines of `DuplicateDetector` + `TransactionStore` + custom filter predicates, so the net complexity is roughly the same.
- **File management**: Users need to know where their `.db` file lives. Mitigated by a sensible default (`~/.sms-txn-analyzer/transactions.db`) and the `--db` flag for overriding.

### The Clinching Argument

With an in-memory store, every new query type requires writing Python code: a new method, a new filter predicate, tests for both. With SQLite, a new query is just a new SQL string — and users who know SQL can skip the CLI entirely and query the database directly.

---

## 12. Why Pydantic Over Dataclasses

### The Problem with Dataclasses for This Application

The `Transaction` object sits at the boundary between **untrusted external data** (SMS text parsed by regex) and **structured internal storage** (SQLite rows). This is exactly the kind of boundary where validation and coercion matter most.

With a plain dataclass, every piece of validation is hand-written and manual:

```python
# What you end up writing with dataclasses:
@dataclass(frozen=True)
class Transaction:
    amount: float
    currency: str
    vendor: str
    date: datetime

    def __post_init__(self):
        if self.amount <= 0:
            raise ValueError(f"amount must be positive, got {self.amount}")
        if not self.vendor:
            raise ValueError("vendor cannot be empty")
        if self.date.tzinfo is None:
            raise ValueError("date must be timezone-aware")
        # ... and every parser must also do:
        #   float(string.replace(",", ""))
        #   Currency(string.strip().lower())
        #   try/except ValueError for each conversion
```

With Pydantic, the same validations are declarative, and type coercion is automatic:

```python
# What you write with Pydantic:
class Transaction(BaseModel):
    model_config = ConfigDict(frozen=True)
    amount: float = Field(gt=0)
    currency: Currency  # StrEnum — Pydantic coerces "PKR" → Currency.PKR
    vendor: str = Field(min_length=1)
    date: datetime  # model_validator checks tzinfo

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, v):
        if isinstance(v, str): return float(v.replace(",", ""))
        return v
```

### What Pydantic Eliminates

| Code You'd Write By Hand | Pydantic Equivalent | Lines Saved |
|---------------------------|---------------------|:-----------:|
| `float(value.replace(",",""))` in every parser | `field_validator("amount", mode="before")` on the model — comma removal + coercion once | ~5 per parser |
| `Currency(string.strip().lower())` with try/except | `field_validator("currency", mode="before")` (once) | ~5 per parser |
| `if amount <= 0: raise ValueError(...)` | `Field(gt=0)` | 2 → 0 |
| `if not vendor: raise ValueError(...)` | `Field(min_length=1)` | 2 → 0 |
| Building dicts for SQL INSERT field-by-field | `model_dump(mode="json")` | ~10 |
| Constructing objects from SQL SELECT rows field-by-field | `model_validate(dict(row))` | ~10 |
| `json.dumps([{field: txn.field, ...} for txn in txns])` | `[txn.model_dump(mode="json") for txn in txns]` | ~8 |
| Reconstructing frozen objects to change one field | `txn.model_copy(update={"category": "Fuel"})` | ~8 per site |

### The Cost

- **One new runtime dependency** (`pydantic>=2.0`). Pydantic v2 is Rust-backed (`pydantic-core`), fast, and one of the most widely installed Python packages.
- **Learning curve**: Developers need to understand `field_validator`, `model_validator`, `model_dump`, `model_validate`, `model_copy`, and `ConfigDict`. This is a small API surface compared to what it replaces.
- **Slightly different mental model**: Fields are validated on construction, not after. Validators run in a defined order. `mode="before"` vs `mode="after"` matters for coercion-then-validation.

### Where Pydantic is Used in This Architecture

| Module | Pydantic Feature Used | What It Replaces |
|--------|----------------------|-----------------|
| `models.py` | `BaseModel`, `Field`, `field_validator`, `model_validator`, `ConfigDict(frozen=True)` | `@dataclass(frozen=True)` + `__post_init__` |
| `parsers/hbl.py` | `Transaction(currency="PKR", amount="25,170.49", ...)` — raw strings passed to constructor | Manual `float()`, `Currency()`, try/except in parser |
| `db.py` (write) | `txn.model_dump(mode="json")` for INSERT | Hand-built tuple of `(txn.bank, txn.txn_type.value, ...)` |
| `db.py` (read) | `Transaction.model_validate(dict(row))` for SELECT | Manual construction from `sqlite3.Row` fields |
| `importer.py` | `txn.model_copy(update={"category": ...})` | Reconstructing all fields of a frozen dataclass |
| `categorizer.py` | `CategoriesConfig.model_validate(toml_data)` for config | Manual dict traversal with `.get()` |
| `formatters.py` | `txn.model_dump(mode="json", exclude={"sms_body"})` | Hand-built dict comprehension per transaction |
| `exceptions.py` | `ValidationError` caught and wrapped in `ParseError` | Hand-written ValueError messages |

---

## 13. Review Refinements & Corrections

A post-review pass identified the following issues in the original proposal. Bugs in code samples have been fixed inline in the sections above. This section documents the design-level refinements and the rationale behind each correction.

### Summary of Inline Fixes (applied to code samples above)

| # | Fix | Section | What Changed |
|---|-----|---------|-------------|
| 1 | ~~`Decimal` for financial amounts~~ (reverted — see Section 14) | 4a, 4f | `amount: float` kept; SQLite column uses `REAL` — see Section 14 for rationale |
| 2 | `ImportResult` cannot be frozen | 4a | Removed `frozen=True` claim; added docstring noting it is a mutable accumulator |
| 3 | `bank_counts` KeyError | 4a | `default_factory=dict` → `default_factory=lambda: defaultdict(int)` |
| 4 | `iterparse` for memory efficiency | 4g | `ET.parse()` → `ET.iterparse()` with `elem.clear()` per element |
| 5 | In-memory DB for testing | 4f, 9 | `TransactionDB.in_memory()` classmethod; default constructor uses `DEFAULT_DB_PATH` (see Section 14) |
| 6 | Duplicate validators (DRY) | 4a | Extracted `_normalize_currency` and `_parse_amount` as shared functions |
| 7 | Fixture contradicts flat model | 9 | `sample_transaction` uses flat `currency`/`amount` fields, not nested `CurrencyAmount` |
| 8 | `LIMIT` parameterization | 4f | `f" LIMIT {limit}"` → `" LIMIT ?"` with parameterized query |
| 9 | Double commit atomicity | 4g | Two `db.commit()` calls → single atomic commit after both inserts and import log |
| 10 | Parser registry testability | 4c | Added `_reset_registry()` classmethod for test isolation |

### 13a. Windows-Appropriate Default DB Path

The default `DEFAULT_DB_PATH = Path.home() / ".sms-txn-analyzer"` creates a dotfile directory, which is unconventional on Windows. The standard user data location on Windows is `%LOCALAPPDATA%`.

**Recommendation:** Use `platformdirs` (lightweight, zero-dependency package) for cross-platform paths:

```python
from platformdirs import user_data_dir
DEFAULT_DB_PATH = Path(user_data_dir("sms-txn-analyzer")) / "transactions.db"
# Windows: C:\Users\<user>\AppData\Local\sms-txn-analyzer\transactions.db
# Linux:   ~/.local/share/sms-txn-analyzer/transactions.db
# macOS:   ~/Library/Application Support/sms-txn-analyzer/transactions.db
```

Or detect the platform manually without adding a dependency:

```python
import sys
if sys.platform == "win32":
    _base = Path.home() / "AppData" / "Local" / "sms-txn-analyzer"
else:
    _base = Path.home() / ".sms-txn-analyzer"
DEFAULT_DB_PATH = _base / "transactions.db"
```

If `platformdirs` is used, add it to the dependency list (`platformdirs>=4.0`).

---

### 13b. Missing `config.py` Module

The directory structure (Section 3) lists `config.py` ("Configuration loading (TOML + defaults)") and the layer diagram references it, but no section defines its contents. Several constants are scattered across modules (`DEFAULT_TZ` in `models.py`, `DEFAULT_DB_PATH` in `db.py`, `DEFAULT_CATEGORIES_PATH` in `categorizer.py`).

**Recommendation:** Centralize all configuration into `config.py`:

```python
# config.py
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Karachi")

if sys.platform == "win32":
    _DATA_DIR = Path.home() / "AppData" / "Local" / "sms-txn-analyzer"
else:
    _DATA_DIR = Path.home() / ".sms-txn-analyzer"

DEFAULT_DB_PATH = _DATA_DIR / "transactions.db"
```

Other modules import from `config.py` instead of defining their own constants. This eliminates the scattered `DEFAULT_TZ` problem that existed in the current codebase (`common.py` and `cc_txn.py` both defining it).

---

### 13c. Schema Migration Strategy

The schema uses `CREATE TABLE IF NOT EXISTS`, which means shipping a new version with an added column (e.g., `transaction_id TEXT`) silently leaves existing databases on the old schema. Queries referencing the new column then fail.

**Recommendation:** Add a `schema_version` table and a simple migration runner (~15 lines):

```python
MIGRATIONS = [
    # v1: initial schema
    SCHEMA_SQL,
    # v2 (future): example migration
    # "ALTER TABLE transactions ADD COLUMN source_file TEXT DEFAULT '';",
]

def _run_migrations(conn: sqlite3.Connection):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] or 0

    for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
    conn.commit()
```

Call `_run_migrations(self._conn)` in `TransactionDB.__init__` instead of `self._conn.executescript(SCHEMA_SQL)` directly. This is minimal overhead now and prevents painful manual migration later.

---

### 13d. `parse_transaction` Takes `timestamp_ms` But Doesn't Use It

The `BaseBankParser.parse_transaction(body, timestamp_ms)` abstract method signature includes `timestamp_ms`, and the importer passes it from the XML element's `date` attribute. However, `HBLParser.parse_transaction` ignores it entirely — it parses the date from the SMS body text instead.

The `timestamp_ms` parameter has valid uses for other bank parsers (some banks don't include dates in the body) and for validation. Consider documenting the intent and optionally adding a sanity check:

```python
# In HBLParser.parse_transaction, after parsing the date from body text:
msg_date = datetime.fromtimestamp(timestamp_ms / 1000, tz=DEFAULT_TZ)
if abs((date - msg_date).total_seconds()) > 172800:  # 48 hours
    logger.warning("[HBL] Parsed date %s differs from SMS timestamp %s by >48h",
                   date, msg_date)
```

This catches regex date-parsing bugs without failing the transaction.

---

### 13e. Fragile Default Categories Path

The `categorizer.py` default path uses relative traversal:

```python
DEFAULT_CATEGORIES_PATH = Path(__file__).parent.parent.parent / "categories.toml"
```

This breaks after `pip install` (the package is in `site-packages/`, not next to `categories.toml`). It also breaks if the directory structure changes.

**Recommendation:** Use `importlib.resources` to bundle `categories.toml` as package data:

```python
from importlib.resources import files

DEFAULT_CATEGORIES_PATH = files("sms_txn_analyzer").joinpath("categories.toml")
```

Move `categories.toml` inside the package directory (`src/sms_txn_analyzer/categories.toml`) and declare it as package data in `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/sms_txn_analyzer"]
# categories.toml must be inside src/sms_txn_analyzer/
```

This ensures the default categories file is always findable regardless of installation method.

---

### 13f. Additional CLI Features Worth Including in v1

The following features are low-effort, high-value additions that should be considered for inclusion before Phase 5:

| Feature | Rationale | Effort |
|---------|-----------|--------|
| `--dry-run` on `import` | Preview what would be imported without writing to DB. Builds user trust. | Low — run the pipeline but skip `db.insert_transaction` and `db.commit` |
| `reset` command | Delete all transactions or the entire DB file. Without it, users must manually find and delete the `.db` file. | Low — `DROP TABLE` or `Path.unlink()` |
| `--verbose` / `--quiet` | More granular than `--debug`. `--verbose` for import progress counts, `--quiet` for piping output to other tools. | Low — map to `logging.INFO` / `logging.ERROR` |
| `search` command | Full-text search across `sms_body` in the DB. Useful for finding "that one transaction at that place." | Low — `SELECT * FROM transactions WHERE sms_body LIKE ?` |
| `--since DATE` on `import` | Skip messages before a given date during import. Useful for very large backup files where only recent transactions are needed. | Low — filter by `timestamp_ms` in the import loop |

Example additions to `cli.py`:

```python
@cli.command()
@click.confirmation_option(prompt="This will delete ALL transactions. Are you sure?")
@click.pass_context
def reset(ctx):
    """Delete all transactions and import history from the database."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        db._conn.execute("DELETE FROM transactions")
        db._conn.execute("DELETE FROM import_log")
        db.commit()
        click.echo("Database reset. All transactions and import history deleted.")


@cli.command()
@click.argument("query")
@click.pass_context
def search(ctx, query):
    """Search transaction SMS bodies for a keyword."""
    with TransactionDB(ctx.obj["db_path"]) as db:
        rows = db.query_transactions(vendor=query)  # reuses existing LIKE filter
        click.echo(formatters.format_transactions(rows, ctx.obj["output_format"]))
```

---

### 13g. `__all__` Exports for Public API

For a proper package with `pyproject.toml` and entry points, key modules should define `__all__` to control the public API surface. This is particularly important for:

- `models.py` — export `Transaction`, `CurrencyAmount`, `Currency`, `TransactionType`, `ImportResult`, `DEFAULT_TZ`
- `parsers/__init__.py` — export `BaseBankParser` only (auto-imported subclasses are an implementation detail)
- `db.py` — export `TransactionDB`, `DEFAULT_DB_PATH`
- `exceptions.py` — export all exception classes

```python
# models.py
__all__ = [
    "Currency", "TransactionType", "CurrencyAmount",
    "Transaction", "ImportResult", "DEFAULT_TZ",
]

# parsers/__init__.py
__all__ = ["BaseBankParser"]
```

---

## 14. Second Review: Correctness & Scalability Fixes

A second deep review focused on whether the redesign is **optimal, scalable, and complete enough to handle future changes**. This pass found 7 issues — 3 correctness bugs that would surface at runtime, 1 major scalability gap, and 3 usability improvements. All have been fixed inline in the code samples above. This section documents the rationale.

### Summary of Inline Fixes (applied to code samples above)

| # | Fix | Section(s) | What Changed |
|---|-----|-----------|-------------|
| 1 | `float` for amounts, `REAL` in SQLite | 4a, 4f | Reverted `Decimal`/`TEXT` back to `float`/`REAL` — fixes broken `SUM()` and comparison operators |
| 2 | DB path resolution | 4f, 9 | `TransactionDB(None)` no longer creates in-memory DB; default constructor uses `DEFAULT_DB_PATH`; added `in_memory()` classmethod for tests |
| 3 | Multi-pattern parser | 4c, 4d | `_TXN_RE` → `_TXN_PATTERNS` list; added `_match_body()` helper; documented convention in base class |
| 4 | Dedup hash includes sender | 4f, 4g | `_hash_sms_body(body)` → `_hash_sms(body, address)`; `sms_body_hash` → `sms_hash` column |
| 5 | `--limit` on `transactions` | 4k | Added `--limit/-n` option wired to `query_transactions(limit=...)` |
| 6 | Body normalization | 4g | `body.strip()` applied once in the importer before passing to any parser method |
| 7 | Test fixture DB creation | 9 | `TransactionDB()` → `TransactionDB.in_memory()` |

### 14a. Why `float` Instead of `Decimal` (Reversal of Section 13 Fix #1)

The previous review introduced `Decimal` for financial amounts and `TEXT` storage in SQLite. This created three bugs:

1. **`SUM(amount)` on a TEXT column** — SQLite attempts implicit conversion, but returns `REAL` (float), destroying the Decimal precision that was the entire point of storing as TEXT.

2. **`amount >= ?` comparisons are lexicographic on TEXT** — the query `WHERE amount >= 5000` would incorrectly match `"9.99"` because `'9' > '5'` in string comparison. Every amount filter in `query_transactions()` was silently broken.

3. **`model_dump(mode="json")` converts Decimal → float** — Pydantic v2's JSON serialization mode converts `Decimal` to `float` by default. This means `insert_transaction()` (which uses `model_dump(mode="json")`) was silently discarding Decimal precision before it even reached SQLite.

**Why `float` is correct here:** `float64` provides 15 significant digits of precision. Pakistani Rupee amounts with 2 decimal places would need to exceed 10 trillion (10^13) before any precision loss. USD/CAD amounts would need to exceed 100 billion. These thresholds are far beyond any credit card transaction. The `Decimal` overhead was solving a theoretical problem while introducing three real bugs.

### 14b. DB Path Resolution Bug

The previous constructor `TransactionDB(db_path=None)` mapped `None` to `:memory:`. But the CLI's `--db` option also defaults to `None` when not specified. This meant:

- `sms-txn-analyzer import backup.xml` → imports into in-memory DB (lost on exit)
- `sms-txn-analyzer transactions` → opens empty in-memory DB (no data found)

The fix separates the two use cases:
- **Production path:** `TransactionDB()` or `TransactionDB(path)` → always creates a file-backed database (defaults to `DEFAULT_DB_PATH`)
- **Test path:** `TransactionDB.in_memory()` → explicitly creates an in-memory database

This is a cleaner API because the two use cases have different semantics. Tests want isolation and no cleanup; production wants persistence.

### 14c. SMS Format Evolution (Scalability Gap)

The redesign handles adding **new banks** elegantly (`__init_subclass__` + auto-discovery). But it didn't handle **banks changing their SMS format**, which is the more likely real-world change.

With a single `_TXN_RE` regex per parser, any format change (new wording, added reference number, reordered fields) causes **every new-format message to become a ParseError**. Worse, you won't know the format changed until you see a spike in parse errors after importing a newer backup.

The fix uses a `_TXN_PATTERNS` list (newest pattern first):
- When HBL changes their template, prepend the new pattern
- Old patterns continue to match historical messages from previous backups
- `_match_body()` tries each pattern in order and returns the first match
- This is documented as a convention in `BaseBankParser`'s docstring

This also makes adding debit card transaction parsing straightforward — add a second pattern with different named groups and extend `is_transaction_sms()` accordingly.

### 14d. Dedup Hash Must Include Sender Address

The previous hash used body text only (`_hash_sms_body(body)`), repeating the exact weakness documented in Section 1's analysis of the current codebase:

> **Duplicate detection**: SHA3-512 of body text only (ignores sender/timestamp). Incorrect dedup semantics.

The fix hashes `f"{address}:{body}"` so that:
- Identical body text from different banks is not incorrectly deduplicated
- The same bank sending identical notification text for recurring charges (same vendor, same amount, but different dates where the date is not in the body) would still be deduplicated — which is the correct behavior since such messages are genuinely duplicates in the backup file

The column was renamed from `sms_body_hash` to `sms_hash` to reflect the broader input.

### 14e. `--limit` on `transactions` Command

The `query_transactions()` method already supported a `limit` parameter, but the CLI didn't expose it. Listing hundreds of transactions in the terminal is unwieldy. The `--limit/-n` option lets users see just the most recent or first N results:

```bash
sms-txn-analyzer transactions --limit 20
sms-txn-analyzer transactions --vendor SHELL -n 5
```

### 14f. Body Normalization in the Importer

The SMS body text was used in three places within the import loop:
1. Passed to `parser.is_transaction_sms(body)`
2. Passed to `parser.parse_transaction(body, timestamp_ms)`
3. Passed to `_hash_sms(body, address)` (which called `.strip()` internally)

If the body had leading/trailing whitespace, parsers 1 and 2 would see untrimmed text while the hash saw trimmed text — a subtle inconsistency. The fix normalizes once at the top of the loop:

```python
body = elem.attrib.get("body", "").strip()  # normalize once, used everywhere
```

---

## Appendix: Dependency Summary

| Dependency | Current | Proposed | What It Replaces |
|-----------|:-------:|:--------:|-----------------|
| `click` | Required | Required | CLI framework (kept) |
| `pydantic` | — | **Required** (v2+) | Hand-written `__post_init__` validation, manual type coercion in parsers, manual dict serialization for SQLite and JSON, manual field-by-field object reconstruction |
| `tomllib` | — | Required (stdlib 3.11+) | N/A — new feature |
| `sqlite3` | — | Required (stdlib) | In-memory `TransactionStore` class, `DuplicateDetector` class |
| `pytest` | — | Dev dependency | `unittest` (migration) |
| `platformdirs` | — | Optional | Cross-platform default DB path (see 13a) |
| `rich` | — | Optional | Plain text table output |

**Total new runtime dependencies: 2** (`click` + `pydantic`). Both are mature, well-maintained, and widely used. Pydantic v2 is Rust-backed and fast — model construction overhead is negligible compared to XML parsing and regex matching. `platformdirs` is recommended but optional (a manual platform check is a viable alternative).

---

## Appendix: `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sms-txn-analyzer"
version = "1.0.0"
description = "Parse and analyze financial transactions from SMS backup files"
requires-python = ">=3.11"
dependencies = [
    "click>=8.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0"]
rich = ["rich>=13.0"]

[project.scripts]
sms-txn-analyzer = "sms_txn_analyzer.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/sms_txn_analyzer"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```
