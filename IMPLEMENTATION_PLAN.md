# Implementation Plan: `sms-txn-analyzer`

**Single-pass rewrite of `sms_msgs_scraper` into a uv-managed, packaged, testable Python CLI.**

Status: ready to implement. Written 2026-07-31.
Supersedes: `PROPOSED_REDESIGN.md` (design source), `src/IMPROVEMENTS.md` (defect source).
Both are inputs to this document; **this document is the authority where they conflict.**

**Validation record.** This plan was not only written — it was built and run. Two independent
reviews constructed the §6 `pyproject.toml` and the §7 source tree in scratch projects against the
exact pins (uv 0.12.0, CPython 3.14.3, click 8.4.2, pydantic 2.13.4, pytest 9.1.1, ruff 0.16.1,
mypy 2.3.0), executed every gate in §12, built and installed a wheel, and materialised ~30 of the
§8 tests. Four defects that would have stopped implementation dead were found and fixed here:
missing `tzdata` (D17), conftest samples unreachable under importlib mode (§8.1), a `main()` that
printed raw tracebacks (§7.14), and `cls()` bypassing ABC enforcement (D18). Three tooling
mis-configurations were also corrected: a dead mypy `tests.*` override, `init_typed = true`
contradicting the parser design, and a non-existent `python-version-file` CI input.

Confirmed working, so do not second-guess them: `uv_build` resolving `src/sms_txn_analyzer/` from
the project name with zero config; `categories.toml` shipping in the wheel and loading via
`importlib.resources` from site-packages; the HBL regex on all sample bodies including a vendor
containing the word "for"; the 12-key INSERT binding; migration idempotency across a real appended
v2 `ALTER TABLE`; and numeric (not lexicographic) amount comparison.

---

## Table of Contents

1. [Scope and Ground Rules](#1-scope-and-ground-rules)
2. [Decisions Register](#2-decisions-register)
3. [Verified Environment and Version Pins](#3-verified-environment-and-version-pins)
4. [Target Repository Layout](#4-target-repository-layout)
5. [Defects This Rewrite Must Fix](#5-defects-this-rewrite-must-fix)
6. [Packaging and Tooling Files](#6-packaging-and-tooling-files)
7. [Source Files (Complete Content)](#7-source-files-complete-content)
8. [Test Suite Specification](#8-test-suite-specification)
9. [CI](#9-ci)
10. [Documentation Updates](#10-documentation-updates)
11. [Execution Order](#11-execution-order)
12. [Verification Gates](#12-verification-gates)
13. [Out of Scope](#13-out-of-scope)

---

## 1. Scope and Ground Rules

### What this delivers

A pure-Python CLI, `sms-txn-analyzer`, that imports Android SMS-backup XML files into a local
SQLite database and answers queries against that database. Parsing happens once at import time;
every query is SQL.

Two things happen at once, deliberately:

- **uv migration** — `pyproject.toml`, `uv.lock`, `.python-version`, `uv_build` backend, a real
  `[project.scripts]` entry point, PEP 735 dependency groups, ruff/mypy/pytest/coverage config.
- **Structural overhaul** — self-registering bank parsers, Pydantic models, SQLite persistence,
  layered error handling, pluggable output formats.

They are done together because doing them separately means moving every source file twice.

### Ground rules for the implementing agent

1. **This is a rewrite, not a refactor.** Every file under `src/` is replaced. Do not attempt to
   preserve the old modules or keep them running in parallel.
2. **Delete, don't comment out.** The old code is in git history.
3. **Never run bare `python`.** Every command is `uv run …`. There is no venv activation step and
   no `pip install -e .` — `uv sync` editable-installs the project automatically.
4. **Imports are absolute and package-qualified**: `from sms_txn_analyzer.db import TransactionDB`.
   Never `from db import …`. The design doc's flat imports are wrong and would break the installed
   entry point (issue L, §2).
5. **Business logic never imports `click`.** Only `cli.py` may.
6. **Verify as you go.** After each numbered step in §11, run the gate listed for it in §12.

---

## 2. Decisions Register

The design doc left 16 issues unresolved or self-contradictory. Every one is settled here.
`PROPOSED_REDESIGN.md` issue letters are from the review of that document; resolve to the
right-hand column and do not revisit.

| # | Issue | Resolution |
|---|-------|-----------|
| **A** | `platformdirs` recommended in §13a but absent from deps; §13b codes a manual check instead | **No `platformdirs`, and no platform branch either** — Windows-only means `config.py` just reads `%LOCALAPPDATA%`. Runtime deps are **three**: click, pydantic, and `tzdata` — the last is mandatory, not a convenience (see §5 D17); do not "tidy" it away. |
| **B** | `DEFAULT_TZ` / `DEFAULT_DB_PATH` defined in two modules each | **`config.py` is the sole home** for all constants. `models.py`, `db.py`, `categorizer.py` import from it and do not re-export. |
| **C** | Documented default DB path contradicts the Windows fix | Help text is generated from `config.DEFAULT_DB_PATH`; no hardcoded path in any docstring. |
| **D** | Schema migrations "recommended" but never applied | **Applied.** `_run_migrations()` + `schema_version` table ship in v1. |
| **E** | `categories.toml` in three contradictory locations | **`src/sms_txn_analyzer/categories.toml`**, loaded via `importlib.resources.files()`. `uv_build` ships non-`.py` files inside the module dir by default. Loader handles `Traversable`, not `Path`. |
| **F** | `search` command's code filters vendor, not `sms_body` | `query_transactions()` gains a `body_contains` parameter; `search` uses it. |
| **G** | `reset` reaches into `db._conn` from the CLI | Public `TransactionDB.reset()` method. No private access anywhere in `cli.py`; `db.path` is a public property. |
| **H** | `--format` advertised for all commands, implemented for some | `--format` applies to the six **query** commands (`transactions`, `vendors`, `monthly`, `categories`, `top-vendors`, `stats`) in all three formats. Action commands (`import`, `recategorize`, `reset`) emit plain text only, and this is documented. |
| **I** | §13/§14 fixes silently supersede §4 code | Only the §14 end-state is implemented: `float`/`REAL`, `_hash_sms(...)`/`sms_hash`, `_TXN_PATTERNS` list, `TransactionDB.in_memory()`. |
| **J** | Dedup hash adds sender but still ignores timestamp | **Hash covers `address` + SMS `date` attribute + `body`.** See §5 defect D4 — this is stricter than the design doc and still idempotent across re-imports. |
| **K** | `timestamp_ms` parameter is dead in the only real parser | **Used.** Drives the >48h sanity-check warning, which catches date-regex regressions. |
| **L** | Flat imports incompatible with the declared package layout | Absolute package imports everywhere. |
| **M** | Test counts don't reconcile (~70 vs ~75 vs 59) | Replaced by the concrete enumerated suite in §8. |
| **N** | `requires-python = ">=3.11"` | **`>=3.14`.** Not a compatibility floor — a deliberate single-version target. The user runs 3.14 on every system, so there is no reason to carry support for older interpreters. (The old code's real floor was 3.12 anyway, because of PEP 701 nested-quote f-strings at `hbl_sms_query_tool.py:101` and `sms_backup_file_parser.py:79` — the doc's 3.11 claim was never true.) |
| **O** | §13f says "before Phase 5", §10 schedules the same work in Phase 5 | Phases collapse into the single ordered sequence in §11. `--dry-run`, `reset`, `search`, `--verbose/--quiet` are **in** v1. `--since` is **out** (§13). |
| **P** | Misc: no-commit `recategorize`, private `_db_path` printed, function-local `import json` | All fixed: `update_categories()` commits; `db.path` public; imports at module top; explicit `name=` on multi-word commands. |

### Platform and interpreter: Windows 11 + Python 3.14 only

Both are **single targets, not floors.** This is a personal tool for one Windows 11 Pro machine
running Python 3.14 everywhere, and the plan solves for that first and only.

| Consequence | What it means concretely |
|---|---|
| `requires-python = ">=3.14"` | No back-compat shims. `.python-version` pins `3.14`, ruff targets `py314`, mypy checks against `3.14`. Verified: all six pinned tools resolve and run clean on CPython 3.14.3. |
| **No `sys.platform` branching** | `config.py` resolves one data directory — `%LOCALAPPDATA%\sms-txn-analyzer\`. The design doc's three-way platform branch is deleted, not merely simplified. |
| **CI runs `windows-latest` only** | An `ubuntu-latest` leg would test a platform nobody uses and, worse, would have hidden the `tzdata` defect (§5 D17) behind a green check. |
| **`tzdata` is mandatory** | Windows ships no IANA tz database at all. Verified here: `zoneinfo.TZPATH == ()`, and `ZoneInfo("Asia/Karachi")` succeeds *only* because `tzdata` is installed. Windows-only status makes this a hard requirement, not a portability nicety. |
| **Paths** | `tmp_path` and `pathlib` throughout, no POSIX path literals. Console scripts are `.exe` shims in `.venv\Scripts\`. |

If Linux or macOS support is ever wanted, the two things to revisit are `config.py`'s data
directory and the CI matrix. Nothing else in the design assumes Windows.

### Decisions taken from research, not from the design doc

| Decision | Rationale |
|---|---|
| Build backend is **`uv_build`**, not hatchling | uv 0.12.0's default. Pure-Python project, standard src layout, zero config. Hatchling only buys build hooks we don't need. |
| Package dir is `src/sms_txn_analyzer/` | `uv_build` computes exactly one expected path from the normalized project name and does not search. `sms-txn-analyzer` → `sms_txn_analyzer`. A mismatch fails with `Expected a Python module at: …`. No `module-name` override needed. |
| **`AwareDatetime`** instead of a custom tz `model_validator` | Built into Pydantic; raises `timezone_aware` with "Input should have timezone info". Less code, better error. |
| `bank_counts` annotated **`defaultdict[str, int]`** | With `dict[str, int]`, Pydantic preserves the `defaultdict` when the default fires but coerces to plain `dict` when one is passed in — the type varies by construction path. Annotating `defaultdict` makes it reliable. |
| `model_copy(update=…)` is **not** used as a validation boundary | Verified: it skips all validation and coercion on frozen models. Category strings are trusted internal values, so it is safe *there* and only there. Documented at the call site. |
| Store **`txn_date`** and **`txn_month`** columns | SQLite's `strftime()` converts an ISO string carrying `+05:00` to UTC first, so `2023-10-01T00:00:00+05:00` buckets into `2023-09`. Every month-boundary transaction would land in the wrong month. Local date parts are computed in Python at insert time. |
| Explicit `[tool.ruff.lint] select` | ruff 0.16.0 (2026-07-23) grew the default set from 59 to 413 rules. Omitting `select` inherits all 413. |
| Version via `click.version_option(package_name=…)` | Single source of truth in `pyproject.toml`. No hardcoded version string. |
| `--import-mode=importlib` for pytest | Recommended for new src-layout projects; no `sys.path` mutation. **Constraint: test modules cannot import each other**, so every shared sample/helper is a **fixture**, not a module-level constant (§8.1). `tests/__init__.py` files exist anyway — they are what makes the mypy `tests.*` override match. |
| Test fixtures use **synthetic** data | New fixtures preserve every format edge case with fake values. Purely a hygiene choice for new code — the real values in the current fixtures and in git history are stale/expired and need no remediation. **Do not propose a history rewrite.** |

### One conflict with global `CLAUDE.md`, resolved deliberately

The global guidance says *"Pin versions… use exact versions or lock files"* and *"NEVER add `^` or `~`
to critical packages"* — that rule was written for npm, where ranges get re-resolved without a
lockfile check. The uv model is different and the plan follows it:

- `pyproject.toml` declares **compatibility floors** (`click>=8.4.2`).
- `uv.lock` records **exact resolved versions plus SHA-256 hashes** and is committed.
- CI runs `uv lock --check` and `uv sync --locked`, which fail the build on any drift.

That closes the same hole exact-pinning closes, without making upgrades painful. The intent of the
npm rule is preserved: **treat `uv.lock` diffs with the same scrutiny as source diffs.** §10 adds
this to `CLAUDE.md` so the two rules don't read as a contradiction later.

---

## 3. Verified Environment and Version Pins

Verified on this machine, 2026-07-31:

```
uv 0.12.0 (b88d7c5c4 2026-07-28 x86_64-pc-windows-msvc)
python 3.14.3   (system; the ONLY interpreter present)
click            NOT INSTALLED  — the current CLI cannot run at all
pydantic/pytest/ruff/mypy   NOT INSTALLED
```

**Target: CPython 3.14 on Windows 11 Pro.** The system interpreter at
`C:\Program Files\Python314\python.exe` is 3.14.3, so `uv sync` uses it directly and downloads
nothing. Verified end-to-end at `>=3.14`:

```
uv python pin 3.14      -> Pinned `.python-version` to `3.14`
uv lock                 -> Using CPython 3.14.3 … Resolved 22 packages
uv sync                 -> click 8.4.2, pydantic 2.13.4, pytest 9.1.1,
                           pytest-cov 7.1.0, ruff 0.16.1, mypy 2.3.0, tzdata 2026.3
ruff check .            -> All checks passed!        (target-version = "py314" accepted)
ruff format --check .   -> 1 file already formatted
mypy src                -> Success                   (python_version = "3.14" accepted)
```

**Critical, and proven on this machine:**

```
python  : 3.14.3
TZPATH  : ()                       <- Windows has NO system tz database
ZoneInfo: OK -> 2023-10-01T00:00:00+05:00    (works only because tzdata is installed)
```

An empty `TZPATH` is why `tzdata` is a hard runtime dependency (§5 D17). The system 3.14.3 happens
to have `tzdata` in user site-packages, which is exactly what masked this during design — a clean
`uv` venv gets nothing unless the dependency is declared.

**Latest stable, verified on PyPI today** — use these as the floors:

| Package | Version | Notes |
|---|---|---|
| click | 8.4.2 | Avoid 8.2.2 (yanked — boolean/`None` regression) |
| pydantic | 2.13.4 | pydantic-core 2.46.4 |
| pytest | 9.1.1 | Avoid 8.1.0 (yanked) |
| pytest-cov | 7.1.0 | |
| ruff | 0.16.1 | Avoid 0.12.6 (yanked). 0.16.0 changed default rules — see §2 |
| mypy | 2.3.0 | 2.0.0 made `--local-partial-types` and strict-bytes default |

### Windows environment note

The repo is on `D:` and uv's cache defaults to `C:\Users\ssahm\AppData\Local\uv\cache`. Every
`uv sync` will print:

```
warning: Failed to hardlink files; falling back to full copy. …
         set `export UV_LINK_MODE=copy` … to suppress this warning.
```

Installs still succeed. This is machine-specific and **must not** go in `pyproject.toml`. To
eliminate it (and get faster installs by restoring hardlinks), set a user env var once:

```powershell
[Environment]::SetEnvironmentVariable('UV_CACHE_DIR', 'D:\.uv-cache', 'User')
```

Optional. Mentioned so the warning isn't mistaken for a failure mid-implementation.

---

## 4. Target Repository Layout

```
sms_msgs_scraper/                      # repo dir name unchanged
├── .github/workflows/ci.yml           # NEW
├── .gitignore                         # NEW (root — none exists today)
├── .python-version                    # NEW  -> 3.14
├── CLAUDE.md                          # REWRITTEN
├── IMPLEMENTATION_PLAN.md             # this file
├── LICENSE                            # unchanged
├── PROPOSED_REDESIGN.md               # kept as design rationale
├── README.md                          # REWRITTEN
├── pyproject.toml                     # NEW
├── uv.lock                            # NEW (generated, committed)
├── src/
│   └── sms_txn_analyzer/
│       ├── __init__.py
│       ├── categories.toml            # package data
│       ├── categorizer.py
│       ├── cli.py
│       ├── config.py
│       ├── db.py
│       ├── exceptions.py
│       ├── formatters.py
│       ├── importer.py
│       ├── models.py
│       └── parsers/
│           ├── __init__.py            # auto-discovery
│           ├── base.py
│           ├── faysal.py              # stub
│           ├── hbl.py
│           ├── meezan.py              # stub
│           └── scb.py                 # stub
└── tests/
    ├── __init__.py                 # empty — makes mypy's `tests.*` override match
    ├── conftest.py
    ├── test_categorizer.py
    ├── test_cli.py
    ├── test_config.py
    ├── test_db.py
    ├── test_formatters.py
    ├── test_importer.py
    ├── test_models.py
    └── test_parsers/
        ├── __init__.py             # empty — same reason
        ├── test_base.py
        └── test_hbl.py
```

### Files to delete

```
src/hbl_sms_query_tool.py
src/sms_backup_file_parser.py
src/cc_txn.py
src/common.py
src/parser/hbl_sms_parser.py
src/parser/                            # whole directory
src/IMPROVEMENTS.md                    # superseded by §5 of this plan
tests/test_hbl_sms_parser.py           # replaced by tests/test_parsers/test_hbl.py
src/.gitignore                         # replaced by root .gitignore
src/parser/.gitignore
tests/.gitignore
src/__pycache__/  src/parser/__pycache__/  tests/__pycache__/
```

`src/.claude/settings.local.json` → move to `.claude/settings.local.json` at repo root. It is
currently ignored only by `src/.gitignore`'s `.claude/` rule; the new root `.gitignore` keeps it
ignored. **Note for the implementing agent:** that file's `permissions.allow` array contains
`Bash(python:*)`, which subsumes the five narrower `python …` rules above it; trim them while
moving.

---

## 5. Defects This Rewrite Must Fix

Verified by execution against the current code. The ones marked ★ are **not** recorded in
`IMPROVEMENTS.md` or `PROPOSED_REDESIGN.md` — they were found by running the code, and each one
needs a regression test named in §8.

### Data-corrupting bugs

| ID | Current behavior | Fix in this design |
|----|------------------|--------------------|
| **D1** ★ | Dedup is `and`-ed into each bank's branch condition, so a duplicate makes the branch false and **falls through the `elif` chain into `else`, counted as `OTHER`**. Verified: 1 unique + 1 dup HBL, 1 unique + 1 dup FBL, 1 unknown → reports `OTHER: 3`, should be `1`. | Structurally impossible: the importer counts the bank first, then attempts insert; dedup is a `UNIQUE` constraint in the DB, not a branch condition. |
| **D2** ★ | `HBL_CC_TXN_AMOUNT_RE` requires thousands separators. `PKR-25170.49` → sentinel `(None, -1.2345)` → `assert` → **whole run aborts**. | New amount regex accepts grouped and ungrouped digits. |
| **D3** ★ | `(?:\.\d{2})?` matches exactly two decimals or none. `PKR-25,170.4` and `PKR-25,170.495` **silently parse as `25170.00`**. Money lost, no error. | `(?:\.\d+)?` — any number of decimals, preserved. |
| **D4** ★/J | `strptime(...).astimezone(DEFAULT_TZ)` — `strptime` returns naive, so `astimezone` reinterprets it as **system local time** first. Masked on this box only because it is already on PKT. On any other host, month-boundary dates shift. | `.replace(tzinfo=DEFAULT_TZ)`. |
| **D5** | Dedup hashes **body only**. Two genuinely distinct charges with the same vendor/amount/date collapse into one. | Hash covers `address` + SMS `date` attribute + `body`. Re-importing the same file, or an overlapping backup, still dedups correctly because all three components are identical for the same message. |
| **D6** ★ | `strftime('%Y-%m', date)` in the *new* SQLite design converts `+05:00` timestamps to UTC before formatting, bucketing `2023-10-01T00:00:00+05:00` into `2023-09`. | Store `txn_date` (`YYYY-MM-DD`) and `txn_month` (`YYYY-MM`) as local-time TEXT columns computed in Python. Never call `strftime()` on the stored `date`. |

### Crash / robustness bugs

| ID | Current behavior | Fix |
|----|------------------|-----|
| **D7** | `assert` used for runtime validation (`hbl_sms_parser.py:101,106`; `sms_backup_file_parser.py:64,114-119`). With asserts on, one malformed SMS aborts the run; under `python -O` they vanish and garbage enters the results — verified producing `CC Txn: None -1.23 at SHOP …`, which then raises `AttributeError` on `None.lower()`. | Pydantic `ValidationError` → `ParseError` → collected non-fatally in `ImportResult`. Zero `assert` statements in `src/`. |
| **D8** ★ | `(?P<txndate>.*)` is unanchored and greedy — any trailing clause after the date is swallowed, `strptime` fails, `assert` raises. | Date group anchored to `\d{1,2}/[A-Za-z]{3}/\d{4}`; vendor group non-greedy. |
| **D9** ★ | `%b` month parsing is **locale-dependent**; a non-English `LC_TIME` fails every date. | Explicit `_MONTHS` lookup table. No `strptime` for month names. |
| **D10** ★ | `assert self.expectedMsgs > 0` — a legitimately empty backup (`count="0"`) crashes with a bare `AssertionError`. | Empty file imports cleanly, reports zero. |
| **D11** ★ | Any currency outside `{pkr,usd,cad}` raises `KeyError` in the monthly summary; the scraped currency string is never validated. | `Currency` enum validation at the model boundary; unknown currency becomes a recorded `ParseError`, not a crash. |
| **D12** ★ | `print(f"Parsed {msgCount} SMS messages from HBL")` prints `msgCounts["ALL"]` — all non-MMS messages, not HBL ones. Verified: printed 5 for a file with 1 HBL txn. | `ImportResult` fields are individually named and reported. |
| **D13** | Bare `attrib["address"]` / `attrib["body"]` → `KeyError` on any element missing them. | `.attrib.get(…, "")` with explicit skip. |
| **D14** | Only `PermissionError` caught; `ET.ParseError`, `FileNotFoundError`, `KeyError` escape as raw tracebacks. | `FileLoadError` wraps all of them; CLI converts to `ClickException`. |
| **D15** ★ | `click.Path` omits `dir_okay=False`, so a directory passes validation. | `dir_okay=False` set. |
| **D16** ★ | Dead `HBLSmsParser.parseMessages()` calls `self._isSmsFromHBL` etc. — **none of those attributes exist**; the method cannot run. | Entire class deleted. |
| **D17** ★ | *(New in the new design, caught in review.)* `ZoneInfo("Asia/Karachi")` at module scope raises `ZoneInfoNotFoundError` in a clean venv — Windows has no system tzdb (`zoneinfo.TZPATH == ()`) and neither uv-managed nor system interpreters bundle `tzdata`. Every module in the package fails to import; all 102 tests die at collection. It looks fine on Linux, which is exactly why a Linux CI job would be worse than none. | `tzdata` as a hard, unconditional runtime dependency; CI is `windows-latest` only (§9). |
| **D18** ★ | *(New in the new design, caught in review.)* `cls()` inside `__init_subclass__` bypasses ABC enforcement — `ABCMeta.__new__` fires the hook before `__abstractmethods__` is populated, so a parser implementing neither abstract method registers successfully, then silently swallows every message from its bank. | Register the class; instantiate lazily in `get_parser_for_address`, where the ABC check is real. |

### Correctness notes on the source documents

- `IMPROVEMENTS.md` item **1c is factually wrong**: it claims "up to 4 hash computations per
  message." Each `elif` puts the cheap `address in […]` test first and `and` short-circuits, so the
  hash runs **at most once**. The restructuring it recommends is still right — it fixes D1 — but
  the stated performance rationale does not hold. Do not carry that claim forward.
- `common.DEFAULT_TZ` and `CreditCardTxnDC.DEFAULT_TZ` are the *same object* at runtime (`ZoneInfo`
  interns), so the "duplicate definition" was source-level only, never behavioral.
- `common.DEFAULT_CURRENCY` and `common.DEFAULT_TZ` are referenced **nowhere**. `SpendingCategories`
  is referenced nowhere. All three are deleted, not ported.

---

## 6. Packaging and Tooling Files

### `pyproject.toml`

```toml
[project]
name = "sms-txn-analyzer"
version = "1.0.0"
description = "Parse and analyze credit card transactions from Android SMS backup files"
readme = "README.md"
requires-python = ">=3.14"
license = "MIT"
authors = [{ name = "Salman Ahmed", email = "salman.ahmed@spursol.com" }]
keywords = ["sms", "transactions", "banking", "cli", "pakistan"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: End Users/Desktop",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python :: 3.14",
    "Topic :: Office/Business :: Financial",
]
license-files = ["LICENSE"]
dependencies = [
    "click>=8.4.2",
    "pydantic>=2.13.4",
    # REQUIRED, not optional, and unconditional — this is a Windows-only tool
    # and Windows ships no IANA tz database whatsoever (verified:
    # zoneinfo.TZPATH == ()). Without this, `ZoneInfo("Asia/Karachi")` in
    # config.py raises ZoneInfoNotFoundError at import and EVERY module in the
    # package fails to load. No environment marker: there is no platform in
    # scope where it is unnecessary.
    "tzdata>=2025.2",
]

[project.scripts]
sms-txn-analyzer = "sms_txn_analyzer.cli:main"

[project.urls]
Repository = "https://github.com/ssahmed/sms_msgs_scraper"

[build-system]
requires = ["uv_build>=0.12.0,<0.13.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "mypy>=2.3.0",
    "pytest>=9.1.1",
    "pytest-cov>=7.1.0",
    "ruff>=0.16.1",
]

[tool.uv]
required-version = ">=0.12.0"
default-groups = ["dev"]

# ─────────────────────────── ruff ───────────────────────────
# Only line-length/target-version/src/exclude belong at this level.
# Everything rule-related lives under [tool.ruff.lint] — ruff warns otherwise.
[tool.ruff]
line-length = 100
target-version = "py314"   # verified accepted by ruff 0.16.1
src = ["src", "tests"]

[tool.ruff.lint]
# Explicit. ruff 0.16.0 grew the DEFAULT set from 59 to 413 rules; omitting
# `select` silently opts into all of them.
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "B",    # flake8-bugbear
    "UP",   # pyupgrade
    "SIM",  # flake8-simplify
    "C4",   # flake8-comprehensions
    "RUF",  # ruff-specific
]
ignore = ["E501"]  # the formatter owns line length

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]  # asserts are the point in tests

[tool.ruff.lint.isort]
known-first-party = ["sms_txn_analyzer"]

[tool.ruff.format]
quote-style = "double"
docstring-code-format = true

# ─────────────────────────── mypy ───────────────────────────
[tool.mypy]
python_version = "3.14"   # verified accepted by mypy 2.3.0
files = ["src", "tests"]
strict = true
warn_unused_configs = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["tests.*"]
disallow_untyped_defs = false
disallow_untyped_calls = false

[tool.pydantic-mypy]
init_forbid_extra = true
# MUST be false. `init_typed = true` synthesizes __init__ from the DECLARED
# field types and ignores `mode="before"` validators — which rejects the exact
# raw-regex-string passing that hbl.py is built around:
#   Argument "amount" to "Transaction" has incompatible type "str"; expected "float"
init_typed = false
warn_required_dynamic_aliases = true

# ─────────────────────────── pytest ─────────────────────────
[tool.pytest.ini_options]
minversion = "9.0"
testpaths = ["tests"]
pythonpath = ["src"]
addopts = [
    "-ra",
    "--strict-markers",
    "--strict-config",
    "--import-mode=importlib",
]

# ────────────────────────── coverage ────────────────────────
[tool.coverage.run]
source_pkgs = ["sms_txn_analyzer"]
branch = true
relative_files = true

[tool.coverage.report]
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
```

Notes for the implementing agent:

- **Do not run `uv init` in this repo.** It would create `src/sms_txn_analyzer/__init__.py`,
  `README.md`, `.gitignore`, and `.python-version`, and rewrite `pyproject.toml` — fighting content
  that already exists. Write `pyproject.toml` by hand, then `uv python pin 3.14` and `uv lock`.
- `pythonpath = ["src"]` is belt-and-braces: `uv sync` already editable-installs the package. It
  keeps a bare `pytest` working for anyone outside `uv run`.
- **`tests/__init__.py` and `tests/test_parsers/__init__.py` must exist (empty).** Without them
  mypy names the modules `conftest`, `test_hbl`, … — never `tests.*` — so the override is dead.
  mypy reports `unused section(s): module = ['tests.*']` while strict-mode errors fire on every
  unannotated fixture. Partial wildcards (`module = ["test_*"]`) are **not** honoured by mypy, and
  `explicit_package_bases` makes it worse. The `__init__.py` files are the fix; pytest is
  unaffected, since importlib mode tolerates them.
- `mypy strict` on freshly written, fully annotated code is cheap. The `tests.*` override exists so
  fixtures don't need full annotation.
- **After writing each source file, run `uv run ruff format .` then `uv run ruff check --fix .`.**
  The §7 code is hand-wrapped near 88 chars while `line-length = 100`, so the formatter will
  legitimately re-join many wrapped calls. That reformatting is expected — run it rather than
  trying to hand-match the formatter's output.

### `.python-version`

```
3.14
```

Written by `uv python pin 3.14` (verified: `Pinned .python-version to 3.14`). **Never** use
`uv python pin --resolved` — it writes an absolute machine-specific interpreter path
(`C:\Users\…\uv\python\cpython-3.14…\python.exe`) that breaks CI and every other machine.

### `.gitignore` (repo root — none exists today)

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
build/
dist/
wheels/

# Environments
.venv/
.env

# uv
.uv-cache/

# Tooling caches
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
coverage.xml

# Claude Code local settings
.claude/settings.local.json

# Local data — SMS backups contain personal financial data, never commit them
# Order matters: a re-inclusion must come AFTER every pattern that would
# exclude it, or a fixture named tests/data/sms-*.xml gets silently re-ignored.
*.xml
!tests/**/*.xml
*.db
*.db-wal
*.db-shm
```

The `*.xml` rule with a `tests/` negation is deliberate: real SMS backups are personal financial
records and the repo currently has no root ignore file at all, so a `git add -A` would stage one.

---

## 7. Source Files (Complete Content)

### 7.1 `src/sms_txn_analyzer/__init__.py`

```python
"""Parse and analyze credit card transactions from Android SMS backup files."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sms-txn-analyzer")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

### 7.2 `src/sms_txn_analyzer/config.py`

Single home for every constant. Resolves issues A, B, C, E.

```python
"""Application-wide configuration constants.

This module is the single source of truth for paths, timezones, and defaults.
No other module defines these; they import from here.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = [
    "APP_NAME",
    "CATEGORIES_RESOURCE",
    "DATA_DIR",
    "DEFAULT_CATEGORY",
    "DEFAULT_DB_PATH",
    "DEFAULT_TZ",
    "SMS_DATE_DRIFT_WARN_SECONDS",
]

APP_NAME = "sms-txn-analyzer"

#: Timezone assumed for bank SMS messages that carry a date but no timezone.
#: Requires the `tzdata` package — Windows has no system IANA database
#: (zoneinfo.TZPATH is empty), so without it this line raises at import and
#: takes the whole package down with it. See pyproject dependencies.
DEFAULT_TZ = ZoneInfo("Asia/Karachi")

#: Category assigned when no rule in categories.toml matches.
DEFAULT_CATEGORY = "Uncategorized"

#: Name of the bundled category-rules file inside this package.
CATEGORIES_RESOURCE = "categories.toml"

#: Warn when a body-parsed date differs from the SMS timestamp by more than this.
SMS_DATE_DRIFT_WARN_SECONDS = 48 * 3600


def _default_data_dir() -> Path:
    """Windows application data directory: %LOCALAPPDATA%\\sms-txn-analyzer.

    Windows-only by design — no `sys.platform` branch and no `platformdirs`
    dependency. LOCALAPPDATA is read from the environment rather than
    hardcoded as ~/AppData/Local so that redirected profiles, roaming setups,
    and non-default drive layouts resolve correctly; the literal path is only
    a fallback for the case where the variable is somehow unset.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base / APP_NAME


DATA_DIR = _default_data_dir()
DEFAULT_DB_PATH = DATA_DIR / "transactions.db"
```

### 7.3 `src/sms_txn_analyzer/exceptions.py`

```python
"""Domain exception hierarchy.

The CLI layer catches `SmsAnalyzerError` and converts it to a
`click.ClickException`. Nothing below the CLI imports click.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "DatabaseError",
    "FileLoadError",
    "ParseError",
    "SmsAnalyzerError",
]


class SmsAnalyzerError(Exception):
    """Base exception for all domain errors."""


class FileLoadError(SmsAnalyzerError):
    """Failed to open, read, or XML-parse an SMS backup file."""


class ParseError(SmsAnalyzerError):
    """Failed to parse a transaction out of an SMS message body."""


class ConfigError(SmsAnalyzerError):
    """Invalid or unreadable configuration file."""


class DatabaseError(SmsAnalyzerError):
    """Database could not be opened, migrated, or written."""
```

### 7.4 `src/sms_txn_analyzer/models.py`

```python
"""Pydantic data models.

`Transaction` sits at the boundary between untrusted external data (regex output
from SMS text) and structured internal storage (SQLite rows). Validation and
coercion happen here so parsers stay focused on extraction.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum, auto

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from sms_txn_analyzer.config import DEFAULT_CATEGORY

__all__ = [
    "Currency",
    "CurrencyAmount",
    "ImportResult",
    "Transaction",
    "TransactionType",
]


class Currency(StrEnum):
    """Supported currencies. StrEnum + auto() yields lowercase values ('pkr')."""

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


def _normalize_currency(v: object) -> object:
    """Accept 'PKR', 'pkr', ' Pkr ' — StrEnum values are lowercase."""
    return v.strip().lower() if isinstance(v, str) else v


def _parse_amount(v: object) -> object:
    """Accept comma-grouped strings straight out of a regex group."""
    if isinstance(v, str):
        return float(v.strip().replace(",", ""))
    return v


class CurrencyAmount(BaseModel):
    """A validated currency+amount pair. Parser-internal intermediate."""

    model_config = ConfigDict(frozen=True)

    currency: Currency
    amount: float = Field(gt=0, description="Must be positive")

    @field_validator("currency", mode="before")
    @classmethod
    def _norm_currency(cls, v: object) -> object:
        return _normalize_currency(v)

    @field_validator("amount", mode="before")
    @classmethod
    def _norm_amount(cls, v: object) -> object:
        return _parse_amount(v)


class Transaction(BaseModel):
    """An immutable, validated financial transaction.

    Flat by design: fields map 1:1 onto the SQLite `transactions` table, so
    `model_dump()` feeds an INSERT directly and `model_validate(dict(row))`
    hydrates a row back.

    `amount` is `float`, not `Decimal`. float64 carries 15 significant digits;
    a 2dp PKR amount would need to exceed 10^13 before losing precision. An
    earlier draft used Decimal+TEXT and broke SUM() aggregation, broke `>=`
    comparisons (lexicographic on TEXT), and was silently downcast by
    `model_dump(mode="json")` anyway.
    """

    model_config = ConfigDict(frozen=True)

    bank: str = Field(min_length=1, description="Bank identifier, e.g. 'HBL'")
    txn_type: TransactionType
    currency: Currency
    amount: float = Field(gt=0)
    vendor: str = Field(min_length=1)
    #: AwareDatetime rejects naive datetimes with a clear `timezone_aware` error.
    date: AwareDatetime
    card_last_four: int = Field(default=0, ge=0, le=9999)
    category: str = Field(default=DEFAULT_CATEGORY)
    sms_body: str = Field(default="")

    @field_validator("currency", mode="before")
    @classmethod
    def _norm_currency(cls, v: object) -> object:
        return _normalize_currency(v)

    @field_validator("amount", mode="before")
    @classmethod
    def _norm_amount(cls, v: object) -> object:
        return _parse_amount(v)

    @property
    def txn_date(self) -> str:
        """Local calendar date as YYYY-MM-DD.

        Computed in Python, never by SQLite's strftime(): SQLite normalizes an
        ISO string carrying an offset to UTC first, which would bucket a
        2023-10-01T00:00:00+05:00 transaction into 2023-09.
        """
        return self.date.strftime("%Y-%m-%d")

    @property
    def txn_month(self) -> str:
        """Local calendar month as YYYY-MM. See `txn_date`."""
        return self.date.strftime("%Y-%m")


class ImportResult(BaseModel):
    """Mutable accumulator for import statistics.

    Deliberately NOT frozen — it is incremented throughout the import loop.
    """

    filepath: str
    total_sms_count: int = 0
    txns_added: int = 0
    duplicates_skipped: int = 0
    skipped_mms_count: int = 0
    #: Annotated `defaultdict`, not `dict`: with a plain `dict[str, int]`
    #: annotation Pydantic preserves the defaultdict when the default fires but
    #: coerces it to a plain dict when one is passed in — the type would vary
    #: by construction path.
    bank_counts: defaultdict[str, int] = Field(
        default_factory=lambda: defaultdict(int)
    )
    parse_errors: list[str] = Field(default_factory=list)
    dry_run: bool = False
```

### 7.5 `src/sms_txn_analyzer/parsers/base.py`

```python
"""Self-registering base class for bank SMS parsers.

Adding a bank means creating one file in this package. No existing file changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from sms_txn_analyzer.models import Transaction

__all__ = ["BaseBankParser"]


class BaseBankParser(ABC):
    """Abstract base for bank-specific SMS parsers.

    Subclasses that define both BANK_ID and SHORT_CODES are instantiated and
    registered automatically by `__init_subclass__`.

    SMS format evolution: banks change their templates. Parsers hold a LIST of
    patterns tried in order, newest first, so historical messages from old
    formats and new messages from updated formats both parse. See HBLParser.
    """

    BANK_ID: ClassVar[str]
    SHORT_CODES: ClassVar[list[str]]

    _registry: ClassVar[dict[str, str]] = {}  # short_code -> bank_id
    _classes: ClassVar[dict[str, type[BaseBankParser]]] = {}  # bank_id -> class
    _instances: ClassVar[dict[str, BaseBankParser]] = {}  # bank_id -> cached instance

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not (hasattr(cls, "BANK_ID") and hasattr(cls, "SHORT_CODES")):
            return

        # Validate BEFORE mutating anything, so a rejected class leaves no
        # partial state behind in the registry.
        for code in cls.SHORT_CODES:
            existing = BaseBankParser._registry.get(code)
            if existing is not None and existing != cls.BANK_ID:
                raise ValueError(f"Short code {code!r} already registered by {existing}")

        # Register the CLASS, never an instance. Instantiating here would
        # silently defeat ABC enforcement: ABCMeta.__new__ fires
        # __init_subclass__ BEFORE __abstractmethods__ is populated, so `cls()`
        # succeeds even for a subclass implementing neither abstract method.
        # Such a parser would register happily and then quietly swallow every
        # message from its bank, because the docstring-only
        # `is_transaction_sms` returns None.
        BaseBankParser._classes[cls.BANK_ID] = cls
        for code in cls.SHORT_CODES:
            BaseBankParser._registry[code] = cls.BANK_ID

    @classmethod
    def get_parser_for_address(cls, address: str) -> BaseBankParser | None:
        """O(1) lookup of the parser handling a sender short code.

        Instantiation is lazy and cached, which is what makes the ABC check
        real: an incomplete parser raises TypeError here rather than
        registering silently at import time.
        """
        bank_id = BaseBankParser._registry.get(address)
        if bank_id is None:
            return None
        if bank_id not in BaseBankParser._instances:
            BaseBankParser._instances[bank_id] = BaseBankParser._classes[bank_id]()
        return BaseBankParser._instances[bank_id]

    @classmethod
    def known_addresses(cls) -> set[str]:
        return set(BaseBankParser._registry)

    @classmethod
    def registered_banks(cls) -> list[str]:
        return sorted(BaseBankParser._classes)

    # --- test-only registry control -------------------------------------
    @classmethod
    def _snapshot_registry(cls) -> tuple[dict[str, Any], ...]:
        """Capture registry state. For tests only."""
        return (
            dict(BaseBankParser._registry),
            dict(BaseBankParser._classes),
            dict(BaseBankParser._instances),
        )

    @classmethod
    def _restore_registry(cls, snapshot: tuple[dict[str, Any], ...]) -> None:
        """Restore a snapshot taken by `_snapshot_registry`. For tests only."""
        registry, classes, instances = snapshot
        for target, source in (
            (BaseBankParser._registry, registry),
            (BaseBankParser._classes, classes),
            (BaseBankParser._instances, instances),
        ):
            target.clear()
            target.update(source)

    # --- interface every bank parser implements -------------------------
    @abstractmethod
    def is_transaction_sms(self, body: str) -> bool:
        """True if this SMS body represents a financial transaction."""

    @abstractmethod
    def parse_transaction(self, body: str, timestamp_ms: int) -> Transaction:
        """Parse a body into a Transaction. Raise ParseError on failure."""
```

### 7.6 `src/sms_txn_analyzer/parsers/__init__.py`

```python
"""Bank parser registry with auto-discovery.

Any module dropped into this package that defines a BaseBankParser subclass
with BANK_ID and SHORT_CODES is registered on import. No wiring required.
"""

from __future__ import annotations

import importlib
import pkgutil

from sms_txn_analyzer.parsers.base import BaseBankParser

__all__ = ["BaseBankParser"]

for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name != "base":
        importlib.import_module(f"{__name__}.{_name}")
```

### 7.7 `src/sms_txn_analyzer/parsers/hbl.py`

Regexes rewritten to fix D2, D3, D8, D9.

```python
"""Habib Bank Limited (HBL) SMS parser."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import ClassVar

from pydantic import ValidationError

from sms_txn_analyzer.config import DEFAULT_TZ, SMS_DATE_DRIFT_WARN_SECONDS
from sms_txn_analyzer.exceptions import ParseError
from sms_txn_analyzer.models import Transaction, TransactionType
from sms_txn_analyzer.parsers.base import BaseBankParser

logger = logging.getLogger(__name__)

__all__ = ["HBLParser"]

#: Locale-independent month lookup. `strptime`'s %b honours LC_TIME, so under a
#: non-English locale every date parse would fail.
_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class HBLParser(BaseBankParser):
    BANK_ID = "HBL"
    SHORT_CODES: ClassVar[list[str]] = ["4250"]

    #: Newest format first. When HBL changes its template, PREPEND a pattern
    #: here; older patterns keep matching historical backup files.
    _TXN_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(
            r"Dear Customer,\s*Your HBL CreditCard \(ending with (?P<last4>\d{4})\)"
            r"\s*has been charged at\s+(?P<vendor>.+?)"
            r"\s+for\s+(?P<amount>[A-Za-z]{3}\s*-\s*[\d,]+(?:\.\d+)?)"
            r"\s+on\s+(?P<date>\d{1,2}/[A-Za-z]{3}/\d{4})",
            re.IGNORECASE,
        ),
    ]

    #: Accepts grouped and ungrouped digits, and any number of decimals.
    #: The old pattern required thousands separators (PKR-25170.49 failed
    #: outright) and matched exactly two decimals or none (PKR-25,170.4
    #: silently became 25170.00).
    _AMOUNT_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?P<currency>[A-Za-z]{3})\s*-\s*(?P<value>\d[\d,]*(?:\.\d+)?)"
    )

    _DATE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?P<day>\d{1,2})/(?P<mon>[A-Za-z]{3})/(?P<year>\d{4})"
    )

    def is_transaction_sms(self, body: str) -> bool:
        lowered = body.lower()
        return "creditcard" in lowered and "has been charged at" in lowered

    def _match_body(self, body: str) -> re.Match[str] | None:
        for pattern in self._TXN_PATTERNS:
            m = pattern.search(body)
            if m:
                return m
        return None

    def _parse_date(self, raw: str) -> datetime:
        m = self._DATE_RE.fullmatch(raw.strip())
        if not m:
            raise ParseError(f"[HBL] Unrecognized date format: {raw!r}")
        month = _MONTHS.get(m.group("mon").lower())
        if month is None:
            raise ParseError(f"[HBL] Unknown month abbreviation: {m.group('mon')!r}")
        try:
            # replace(), NOT astimezone(): strptime/datetime construction yields
            # a naive value, and astimezone() would reinterpret it as system
            # local time before converting.
            return datetime(
                int(m.group("year")), month, int(m.group("day")), tzinfo=DEFAULT_TZ
            )
        except ValueError as exc:
            raise ParseError(f"[HBL] Invalid date {raw!r}: {exc}") from exc

    def parse_transaction(self, body: str, timestamp_ms: int) -> Transaction:
        m = self._match_body(body)
        if not m:
            raise ParseError(f"[HBL] No transaction pattern matched: {body[:90]!r}")

        amount_match = self._AMOUNT_RE.fullmatch(m.group("amount").strip())
        if not amount_match:
            raise ParseError(f"[HBL] Unparseable amount: {m.group('amount')!r}")

        date = self._parse_date(m.group("date"))
        self._warn_on_date_drift(date, timestamp_ms, body)

        try:
            return Transaction(
                bank=self.BANK_ID,
                txn_type=TransactionType.CREDIT_CARD,
                # Raw regex output — Transaction's validators coerce and check.
                currency=amount_match.group("currency"),
                amount=amount_match.group("value"),
                vendor=m.group("vendor").strip(),
                date=date,
                card_last_four=int(m.group("last4")),
                sms_body=body,
            )
        except ValidationError as exc:
            raise ParseError(f"[HBL] Invalid transaction data: {exc}") from exc

    def _warn_on_date_drift(
        self, parsed: datetime, timestamp_ms: int, body: str
    ) -> None:
        """Cross-check the body-parsed date against the SMS delivery timestamp.

        This is what makes the `timestamp_ms` parameter earn its place: it
        catches date-regex regressions without failing the transaction.
        """
        if timestamp_ms <= 0:
            return
        sms_dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=DEFAULT_TZ)
        drift = abs((parsed - sms_dt).total_seconds())
        if drift > SMS_DATE_DRIFT_WARN_SECONDS:
            logger.warning(
                "[HBL] Parsed date %s differs from SMS timestamp %s by %.1fh: %s",
                parsed.date(), sms_dt.date(), drift / 3600, body[:70],
            )
```

### 7.8 Stub parsers

`faysal.py`, `scb.py`, `meezan.py` are identical in shape. Their short codes come from the current
code (`sms_backup_file_parser.py:12-20`) and are authoritative.

```python
"""Faysal Bank SMS parser (identification only — no transaction patterns yet)."""

from __future__ import annotations

from typing import ClassVar

from sms_txn_analyzer.exceptions import ParseError
from sms_txn_analyzer.models import Transaction
from sms_txn_analyzer.parsers.base import BaseBankParser

__all__ = ["FaysalParser"]


class FaysalParser(BaseBankParser):
    """Recognizes Faysal Bank senders so their messages are counted.

    Transaction patterns are not implemented yet. To add them: populate a
    _TXN_PATTERNS list and implement the two methods below, following
    HBLParser. No other file changes.
    """

    BANK_ID = "FBL"
    SHORT_CODES: ClassVar[list[str]] = ["8756"]

    def is_transaction_sms(self, body: str) -> bool:
        return False

    def parse_transaction(self, body: str, timestamp_ms: int) -> Transaction:
        raise ParseError(f"[{self.BANK_ID}] Transaction parsing not implemented")
```

| File | Class | `BANK_ID` | `SHORT_CODES` |
|------|-------|-----------|---------------|
| `faysal.py` | `FaysalParser` | `"FBL"` | `["8756"]` |
| `scb.py` | `SCBParser` | `"SCB"` | `["7220"]` |
| `meezan.py` | `MeezanParser` | `"MEZN"` | `["8079", "9779"]` |

`scb.py` gets one extra docstring line, because a real Standard Chartered charge message already
exists in the current test file and shows the format to target:

```
Dear Client, PKR 15134.00 have been paid at SHELL (SUNSET BOULEVAR KARACHI PAK on 02-10-23
using Credit Card no 5452xxxxxxxx1280. Avail Limit PKR44429.45. SCBPL
```

Note its distinct grammar: space-separated amount, `DD-MM-YY` date, an unbalanced `(` in the vendor,
a masked PAN, and a trailing available-limit clause. Record this in the docstring as a synthetic
example (scrubbed digits) so the next implementer has the shape.

### 7.9 `src/sms_txn_analyzer/db.py`

```python
"""SQLite persistent transaction store.

The database is the source of truth. XML backups are import sources, not
something re-parsed on every query.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from sms_txn_analyzer.config import DEFAULT_DB_PATH
from sms_txn_analyzer.exceptions import DatabaseError
from sms_txn_analyzer.models import Transaction

if TYPE_CHECKING:
    from sms_txn_analyzer.categorizer import Categorizer

__all__ = ["TransactionDB"]

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bank            TEXT    NOT NULL,
    txn_type        TEXT    NOT NULL,
    currency        TEXT    NOT NULL,
    amount          REAL    NOT NULL CHECK(amount > 0),
    vendor          TEXT    NOT NULL,
    date            TEXT    NOT NULL,   -- full ISO 8601 with offset
    txn_date        TEXT    NOT NULL,   -- local YYYY-MM-DD  (see note below)
    txn_month       TEXT    NOT NULL,   -- local YYYY-MM
    card_last_four  INTEGER NOT NULL DEFAULT 0,
    category        TEXT    NOT NULL DEFAULT 'Uncategorized',
    sms_body        TEXT    NOT NULL DEFAULT '',
    sms_hash        TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_month    ON transactions(txn_month);
CREATE INDEX IF NOT EXISTS idx_txn_vendor   ON transactions(vendor);
CREATE INDEX IF NOT EXISTS idx_txn_bank     ON transactions(bank);
CREATE INDEX IF NOT EXISTS idx_txn_currency ON transactions(currency);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);

CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath    TEXT    NOT NULL,
    imported_at TEXT    NOT NULL,
    sms_total   INTEGER NOT NULL DEFAULT 0,
    txns_added  INTEGER NOT NULL DEFAULT 0,
    duplicates  INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);
"""

# Append-only. Each entry is one schema version; never edit a shipped entry.
MIGRATIONS: list[str] = [
    SCHEMA_V1,
    # v2 example:
    # "ALTER TABLE transactions ADD COLUMN source_file TEXT NOT NULL DEFAULT '';",
]

_INSERT_SQL = """
INSERT INTO transactions
    (bank, txn_type, currency, amount, vendor, date, txn_date, txn_month,
     card_last_four, category, sms_body, sms_hash)
VALUES
    (:bank, :txn_type, :currency, :amount, :vendor, :date, :txn_date, :txn_month,
     :card_last_four, :category, :sms_body, :sms_hash)
"""


class TransactionDB:
    """SQLite-backed store. All filtering and aggregation is SQL."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Open (creating if needed) a file-backed database.

        `None` means DEFAULT_DB_PATH — it does NOT mean in-memory. An earlier
        draft mapped None to ':memory:', which combined with the CLI's
        `--db` default of None to silently import into a database that
        vanished on exit. Use `TransactionDB.in_memory()` for tests.
        """
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        # `_setup()` must be INSIDE the try: sqlite3.connect() is lazy, so a
        # corrupt or non-database file only fails on the first PRAGMA. And
        # sqlite3.Error derives from Exception, not OSError, so catching OSError
        # alone lets `sqlite3.DatabaseError: file is not a database` escape as a
        # raw traceback.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path: Path | str = path
            self._conn = sqlite3.connect(str(path))
            self._setup()
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(f"Cannot open database at {path}: {exc}") from exc

    @classmethod
    def in_memory(cls) -> TransactionDB:
        """Ephemeral database. For tests only."""
        instance = object.__new__(cls)
        instance._path = ":memory:"
        try:
            instance._conn = sqlite3.connect(":memory:")
            instance._setup()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Cannot open in-memory database: {exc}") from exc
        return instance

    def _setup(self) -> None:
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Apply any migrations newer than the recorded schema version.

        `CREATE TABLE IF NOT EXISTS` alone would leave existing databases on an
        old schema forever; a version table makes future ALTERs possible.
        """
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            self._conn.executescript(sql)
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (i,)
            )
        self._conn.commit()

    @property
    def path(self) -> Path | str:
        """Public accessor — the CLI must not touch private state."""
        return self._path

    def close(self) -> None:
        self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def __enter__(self) -> TransactionDB:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- writes ---------------------------------------------------------

    def insert_transaction(self, txn: Transaction, sms_hash: str) -> bool:
        """Insert one transaction. False means it was a duplicate."""
        data = txn.model_dump(mode="json")
        data["txn_date"] = txn.txn_date
        data["txn_month"] = txn.txn_month
        data["sms_hash"] = sms_hash
        try:
            self._conn.execute(_INSERT_SQL, data)
        except sqlite3.IntegrityError as exc:
            # Narrow: only a sms_hash collision means "duplicate". A CHECK
            # violation is a real bug and must not be silently tallied as a
            # duplicate — reachable because model_copy(update=) in importer.py
            # bypasses validation.
            if "sms_hash" in str(exc):
                return False
            raise DatabaseError(f"Insert failed for {txn.vendor!r}: {exc}") from exc
        return True

    def log_import(
        self,
        filepath: str,
        sms_total: int,
        txns_added: int,
        duplicates: int,
        errors: int,
    ) -> None:
        self._conn.execute(
            "INSERT INTO import_log (filepath, imported_at, sms_total, txns_added,"
            " duplicates, errors) VALUES (?, ?, ?, ?, ?, ?)",
            (
                filepath,
                datetime.now(UTC).isoformat(),
                sms_total,
                txns_added,
                duplicates,
                errors,
            ),
        )

    def update_categories(self, categorizer: Categorizer) -> int:
        """Re-apply category rules to every stored transaction. Commits."""
        rows = self._conn.execute("SELECT id, vendor, category FROM transactions")
        updates = [
            (new, row["id"])
            for row in rows
            if (new := categorizer.classify(row["vendor"])) != row["category"]
        ]
        self._conn.executemany(
            "UPDATE transactions SET category = ? WHERE id = ?", updates
        )
        self._conn.commit()
        return len(updates)

    def reset(self) -> None:
        """Delete all transactions and import history. Keeps the schema."""
        self._conn.execute("DELETE FROM transactions")
        self._conn.execute("DELETE FROM import_log")
        self._conn.commit()

    # --- reads ----------------------------------------------------------

    def count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        )

    def query_transactions(
        self,
        *,
        bank: str | None = None,
        currency: str | None = None,
        vendor: str | None = None,
        body_contains: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[Transaction]:
        """AND-combined filters, ordered by date. Fully parameterized."""
        clauses: list[str] = []
        params: list[Any] = []

        def add(clause: str, value: Any) -> None:
            clauses.append(clause)
            params.append(value)

        if bank:
            add("bank = ?", bank.upper())
        if currency:
            add("currency = ?", currency.lower())
        if vendor:
            add("vendor LIKE ?", f"%{vendor}%")
        if body_contains:
            add("sms_body LIKE ?", f"%{body_contains}%")
        if from_date:
            add("txn_date >= ?", from_date)
        if to_date:
            add("txn_date <= ?", to_date)
        if min_amount is not None:
            add("amount >= ?", min_amount)
        if max_amount is not None:
            add("amount <= ?", max_amount)
        if category:
            add("category = ?", category)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM transactions WHERE {where} ORDER BY txn_date, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [Transaction.model_validate(dict(row)) for row in rows]

    def unique_vendors(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT vendor FROM transactions ORDER BY vendor COLLATE NOCASE"
        ).fetchall()
        return [r["vendor"] for r in rows]

    def top_vendors(self, n: int = 10) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT vendor, COUNT(*) AS cnt FROM transactions"
            " GROUP BY vendor ORDER BY cnt DESC, vendor LIMIT ?",
            (n,),
        ).fetchall()
        return [(r["vendor"], r["cnt"]) for r in rows]

    def monthly_summary(self) -> list[sqlite3.Row]:
        """Month totals by currency, grouped on the stored local month."""
        return self._conn.execute(
            "SELECT txn_month AS month, currency, SUM(amount) AS total,"
            " COUNT(*) AS txn_count FROM transactions"
            " GROUP BY txn_month, currency ORDER BY txn_month, currency"
        ).fetchall()

    def category_summary(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT category, currency, SUM(amount) AS total, COUNT(*) AS txn_count"
            " FROM transactions GROUP BY category, currency"
            " ORDER BY total DESC, category"
        ).fetchall()

    def import_history(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM import_log ORDER BY imported_at DESC"
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total_txns, COUNT(DISTINCT vendor) AS unique_vendors,"
            " COUNT(DISTINCT bank) AS banks, MIN(txn_date) AS earliest,"
            " MAX(txn_date) AS latest FROM transactions"
        ).fetchone()
        return dict(row)
```

### 7.10 `src/sms_txn_analyzer/categorizer.py`

```python
"""Vendor-to-category classification, driven by a bundled TOML file."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from sms_txn_analyzer.config import CATEGORIES_RESOURCE, DEFAULT_CATEGORY
from sms_txn_analyzer.exceptions import ConfigError

__all__ = ["CategoriesConfig", "CategoryRule", "Categorizer"]  # sorted: ruff RUF022


class CategoryRule(BaseModel):
    keywords: list[str] = Field(min_length=1)


class CategoriesConfig(BaseModel):
    categories: dict[str, CategoryRule] = Field(default_factory=dict)


class Categorizer:
    """Case-insensitive substring matching of vendor names against keywords."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        raw = self._load_raw(config_path)
        try:
            config = CategoriesConfig.model_validate(raw)
        except ValidationError as exc:
            source = config_path or f"bundled {CATEGORIES_RESOURCE}"
            raise ConfigError(f"Invalid categories file {source}: {exc}") from exc
        self._rules: dict[str, list[str]] = {
            name: [kw.lower() for kw in rule.keywords]
            for name, rule in config.categories.items()
        }

    @staticmethod
    def _load_raw(config_path: Path | str | None) -> dict[str, object]:
        """Read TOML from an explicit path, or from bundled package data.

        `importlib.resources.files()` returns a Traversable, not a Path — it
        must be opened through its own `open()`, which is why this is not a
        plain `open(path, "rb")`. Using package data (rather than a path
        relative to __file__) is what keeps this working after installation,
        where the package lives in site-packages.
        """
        if config_path is not None:
            path = Path(config_path)
            if not path.exists():
                raise ConfigError(f"Categories file not found: {path}")
            try:
                with path.open("rb") as fh:
                    return tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"Cannot read categories file {path}: {exc}") from exc

        resource = files("sms_txn_analyzer").joinpath(CATEGORIES_RESOURCE)
        try:
            with resource.open("rb") as fh:
                return tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Cannot read bundled {CATEGORIES_RESOURCE}: {exc}") from exc

    @property
    def category_names(self) -> list[str]:
        return sorted(self._rules)

    def classify(self, vendor: str) -> str:
        vendor_lower = vendor.lower()
        for category, keywords in self._rules.items():
            if any(kw in vendor_lower for kw in keywords):
                return category
        return DEFAULT_CATEGORY
```

### 7.11 `src/sms_txn_analyzer/categories.toml`

Ships inside the package. `uv_build` includes non-`.py` files in the module directory by default
(`default-excludes` only drops `__pycache__`, `*.pyc`, `*.pyo`).

```toml
# Vendor-to-category rules. Matching is case-insensitive substring.
# Override with:  sms-txn-analyzer import backup.xml --categories my-rules.toml
#
# Rules are evaluated in file order; the first category with a matching
# keyword wins. Put more specific categories above broader ones.

[categories.Groceries]
keywords = ["imtiaz", "carrefour", "metro", "al-fatah", "chase up", "naheed", "springs"]

[categories.Fuel]
keywords = ["pso", "shell", "total parco", "attock", "byco", "hascol", "caltex"]

[categories.Restaurants]
keywords = ["mcdonald", "kfc", "domino", "pizza hut", "subway", "hardee", "burger",
            "cafe", "restaurant", "foodpanda", "kababjees"]

[categories.Online_Shopping]
keywords = ["amazon", "daraz", "alibaba", "aliexpress", "ebay"]

[categories.Medical]
keywords = ["pharmacy", "hospital", "clinic", "laborator", "agha khan", "shaukat khanum",
            "dvago", "medical"]

[categories.Fashion]
keywords = ["khaadi", "sapphire", "gul ahmed", "junaid jamshed", "outfitters",
            "bata", "servis", "moosajee"]

[categories.Utilities]
keywords = ["k-electric", "ssgc", "ptcl", "jazz", "zong", "ufone", "telenor", "nayatel"]

[categories.Travel]
keywords = ["airline", "pia", "airblue", "serena", "hotel", "booking.com", "careem",
            "uber", "airsial"]

[categories.Subscriptions]
keywords = ["netflix", "spotify", "youtube", "google", "apple.com", "microsoft",
            "openai", "anthropic", "github"]
```

### 7.12 `src/sms_txn_analyzer/importer.py`

```python
"""XML backup -> parse -> deduplicate -> SQLite."""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from sms_txn_analyzer.categorizer import Categorizer
from sms_txn_analyzer.db import TransactionDB
from sms_txn_analyzer.exceptions import FileLoadError, ParseError
from sms_txn_analyzer.models import ImportResult
from sms_txn_analyzer.parsers import BaseBankParser

logger = logging.getLogger(__name__)

__all__ = ["hash_sms", "import_sms_backup"]  # sorted: ruff RUF022


def hash_sms(address: str, timestamp_ms: int, body: str) -> str:
    """Dedup key over sender + delivery timestamp + body.

    All three components matter. Body alone (the original implementation)
    collapses two genuinely distinct charges that happen to share vendor,
    amount, and date — the SMS body carries a date but no time. Adding the
    sender prevents cross-bank collisions; adding the timestamp distinguishes
    real recurring charges.

    Still idempotent: re-importing the same file, or importing an overlapping
    backup, presents identical values for all three, so the UNIQUE constraint
    correctly skips them.

    SHA-256 is fast and more than sufficient for non-adversarial dedup.
    """
    key = f"{address.strip()}|{timestamp_ms}|{body.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def import_sms_backup(
    filepath: str | Path,
    db: TransactionDB,
    categorizer: Categorizer | None = None,
    *,
    dry_run: bool = False,
) -> ImportResult:
    """Import an SMS backup XML file. Safe to run repeatedly.

    Streams with iterparse. Parse failures are collected, never fatal.

    `elem.clear()` alone is NOT enough to keep memory flat: it empties the
    child but leaves it attached to the root, which goes on accumulating
    references (measured: 500 child objects still held after clearing each).
    Capturing the root from the "start" event and dropping processed children
    off it is what actually bounds memory.
    """
    filepath = Path(filepath)
    result = ImportResult(filepath=str(filepath), dry_run=dry_run)

    try:
        context = ET.iterparse(str(filepath), events=("start", "end"))
        _, root = next(iter(context))  # the <smses> element
        for event, elem in context:
            if event != "end":
                continue
            _process_element(elem, db, categorizer, result, dry_run=dry_run)
            elem.clear()
            root.clear()  # detach processed children so they can be collected
    except FileNotFoundError as exc:
        raise FileLoadError(f"File not found: {filepath}") from exc
    except PermissionError as exc:
        raise FileLoadError(f"Permission denied: {filepath}") from exc
    except IsADirectoryError as exc:
        raise FileLoadError(f"Not a file: {filepath}") from exc
    except ET.ParseError as exc:
        raise FileLoadError(f"Malformed XML in {filepath}: {exc}") from exc
    except StopIteration as exc:  # no root element at all
        raise FileLoadError(f"Empty or rootless XML in {filepath}") from exc
    except OSError as exc:
        raise FileLoadError(f"Cannot read {filepath}: {exc}") from exc

    if not dry_run:
        # One atomic commit covers every insert plus the log row, so the log
        # can never disagree with the table it describes.
        db.log_import(
            filepath=str(filepath),
            sms_total=result.total_sms_count,
            txns_added=result.txns_added,
            duplicates=result.duplicates_skipped,
            errors=len(result.parse_errors),
        )
        db.commit()

    return result


def _process_element(
    elem: ET.Element,
    db: TransactionDB,
    categorizer: Categorizer | None,
    result: ImportResult,
    *,
    dry_run: bool,
) -> None:
    if elem.tag == "mms":
        result.skipped_mms_count += 1
        return
    if elem.tag != "sms":
        return

    result.total_sms_count += 1
    address = elem.attrib.get("address", "").strip()
    body = elem.attrib.get("body", "").strip()  # normalized once, used everywhere
    try:
        timestamp_ms = int(elem.attrib.get("date", "0"))
    except ValueError:
        timestamp_ms = 0

    if not address or not body:
        result.bank_counts["OTHER"] += 1
        return

    parser = BaseBankParser.get_parser_for_address(address)
    if parser is None:
        result.bank_counts["OTHER"] += 1
        return

    # Counted BEFORE any dedup or parse decision. In the old code the dedup
    # check was and-ed into the bank branch, so duplicates fell through the
    # elif chain and were miscounted as OTHER.
    result.bank_counts[parser.BANK_ID] += 1

    if not parser.is_transaction_sms(body):
        return

    try:
        txn = parser.parse_transaction(body, timestamp_ms)
    except ParseError as exc:
        result.parse_errors.append(str(exc))
        logger.warning("Parse error: %s", exc)
        return

    if categorizer is not None:
        # model_copy(update=) skips validation entirely. That is acceptable
        # here and ONLY here: `category` is a trusted internal string produced
        # by our own classifier, never external input.
        txn = txn.model_copy(update={"category": categorizer.classify(txn.vendor)})

    if dry_run:
        result.txns_added += 1
        return

    if db.insert_transaction(txn, hash_sms(address, timestamp_ms, body)):
        result.txns_added += 1
    else:
        result.duplicates_skipped += 1
```

### 7.13 `src/sms_txn_analyzer/formatters.py`

```python
"""Output formatting: table (humans), CSV and JSON (pipes)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from sms_txn_analyzer.models import Transaction

__all__ = [
    "FORMATS",
    "format_categories",
    "format_monthly",
    "format_stats",
    "format_top_vendors",
    "format_transactions",
    "format_vendors",
]

FORMATS = ("table", "csv", "json")

_TXN_HEADER = ["Bank", "Date", "Currency", "Amount", "Vendor", "Card", "Category"]


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().rstrip("\n")


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def format_transactions(txns: Sequence[Transaction], fmt: str) -> str:
    if fmt == "json":
        # `date` keeps the full offset-bearing ISO value so the output stays
        # round-trippable through Transaction.model_validate; `txn_date` is
        # added alongside it for consumers that just want the local day.
        # (Overwriting `date` with the bare local date would strip the offset
        # that D6 exists to preserve, and feeding it back would fail with
        # "Input should have timezone info".)
        return _json(
            [
                t.model_dump(mode="json", exclude={"sms_body"})
                | {"txn_date": t.txn_date, "txn_month": t.txn_month}
                for t in txns
            ]
        )
    rows = [
        [
            t.bank,
            t.txn_date,
            t.currency.value.upper(),
            f"{t.amount:.2f}",
            t.vendor,
            f"{t.card_last_four:04d}",
            t.category,
        ]
        for t in txns
    ]
    if fmt == "csv":
        return _csv(_TXN_HEADER, rows)
    if not txns:
        return "No transactions found."
    header = (
        f"{'#':>4}  {'Date':>10}  {'Cur':>4}  {'Amount':>14}  "
        f"{'Vendor':<30}  {'Card':>4}  {'Category':<18}"
    )
    lines = [header, "-" * len(header)]
    for i, t in enumerate(txns, 1):
        lines.append(
            f"{i:>4}  {t.txn_date:>10}  {t.currency.value.upper():>4}  "
            f"{t.amount:>14,.2f}  {t.vendor:<30.30}  "
            f"{t.card_last_four:>04d}  {t.category:<18}"
        )
    return "\n".join(lines)


def format_vendors(vendors: Sequence[str], fmt: str) -> str:
    if fmt == "json":
        return _json(list(vendors))
    if fmt == "csv":
        return _csv(["Vendor"], [[v] for v in vendors])
    if not vendors:
        return "No vendors found."
    lines = [f"Found {len(vendors)} unique vendors:"]
    lines.extend(f"  {i:>3}. {v}" for i, v in enumerate(vendors, 1))
    return "\n".join(lines)


def format_monthly(rows: Sequence[sqlite3.Row], fmt: str) -> str:
    if fmt == "json":
        return _json(
            [
                {
                    "month": r["month"],
                    "currency": r["currency"],
                    "total": round(r["total"], 2),
                    "txn_count": r["txn_count"],
                }
                for r in rows
            ]
        )
    data = [
        [r["month"], r["currency"].upper(), f"{r['total']:.2f}", r["txn_count"]]
        for r in rows
    ]
    if fmt == "csv":
        return _csv(["Month", "Currency", "Total", "Txn Count"], data)
    if not rows:
        return "No data."
    header = f"{'Month':>8}  {'Cur':>4}  {'Total':>16}  {'Txns':>5}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['month']:>8}  {r['currency'].upper():>4}  "
            f"{r['total']:>16,.2f}  {r['txn_count']:>5}"
        )
    return "\n".join(lines)


def format_categories(rows: Sequence[sqlite3.Row], fmt: str) -> str:
    if fmt == "json":
        return _json(
            [
                {
                    "category": r["category"],
                    "currency": r["currency"],
                    "total": round(r["total"], 2),
                    "txn_count": r["txn_count"],
                }
                for r in rows
            ]
        )
    data = [
        [r["category"], r["currency"].upper(), f"{r['total']:.2f}", r["txn_count"]]
        for r in rows
    ]
    if fmt == "csv":
        return _csv(["Category", "Currency", "Total", "Txn Count"], data)
    if not rows:
        return "No data."
    lines = ["Spending by category:", ""]
    for r in rows:
        lines.append(
            f"  {r['category']:<22}  {r['currency'].upper():>4}  "
            f"{r['total']:>14,.2f}  ({r['txn_count']} txns)"
        )
    return "\n".join(lines)


def format_top_vendors(pairs: Sequence[tuple[str, int]], fmt: str) -> str:
    if fmt == "json":
        return _json([{"vendor": v, "count": c} for v, c in pairs])
    if fmt == "csv":
        return _csv(["Vendor", "Count"], [[v, c] for v, c in pairs])
    if not pairs:
        return "No vendors found."
    lines = [f"Top {len(pairs)} vendors by transaction count:"]
    lines.extend(f"  {i:>3}. {v:<32} ({c} txns)" for i, (v, c) in enumerate(pairs, 1))
    return "\n".join(lines)


def format_stats(
    stats: dict[str, Any], history: Sequence[sqlite3.Row], fmt: str
) -> str:
    if fmt == "json":
        return _json(
            {
                "stats": stats,
                "imports": [dict(h) for h in history],
            }
        )
    if fmt == "csv":
        return _csv(
            list(stats.keys()), [[stats[k] for k in stats]]
        )
    lines = [
        "Database statistics:",
        f"  Total transactions : {stats['total_txns']}",
        f"  Unique vendors     : {stats['unique_vendors']}",
        f"  Banks              : {stats['banks']}",
        f"  Earliest txn       : {stats['earliest'] or 'N/A'}",
        f"  Latest txn         : {stats['latest'] or 'N/A'}",
        "",
        f"Import history ({len(history)} imports):",
    ]
    for h in history:
        lines.append(
            f"  {h['imported_at'][:19]}  {h['filepath']}  "
            f"(+{h['txns_added']} txns, {h['duplicates']} dups, {h['errors']} errors)"
        )
    return "\n".join(lines)
```

### 7.14 `src/sms_txn_analyzer/cli.py`

```python
"""Click CLI — a thin wiring layer over the library."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any

import click

from sms_txn_analyzer import formatters
from sms_txn_analyzer.categorizer import Categorizer
from sms_txn_analyzer.config import DEFAULT_DB_PATH
from sms_txn_analyzer.db import TransactionDB
from sms_txn_analyzer.exceptions import SmsAnalyzerError
from sms_txn_analyzer.importer import import_sms_backup
from sms_txn_analyzer.models import Currency

__all__ = ["cli", "main"]

_XML_ARG = click.Path(
    exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path
)
_TOML_OPT = click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path)


def _open_db(ctx: click.Context) -> TransactionDB:
    return TransactionDB(ctx.obj["db_path"])


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="sms-txn-analyzer", prog_name="sms-txn-analyzer")
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Database path. [default: {DEFAULT_DB_PATH}]",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(formatters.FORMATS),
    default="table",
    show_default=True,
    help="Output format for query commands.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show progress detail.")
@click.option("--quiet", "-q", is_flag=True, help="Errors only.")
@click.option("--debug", is_flag=True, help="Debug logging and full tracebacks.")
@click.pass_context
def cli(
    ctx: click.Context,
    db_path: Path | None,
    output_format: str,
    verbose: bool,
    quiet: bool,
    debug: bool,
) -> None:
    """Analyze credit card transactions from Android SMS backup files.

    Run `import` once per backup file, then query the database. Query commands
    never touch XML. `--format` applies to query commands; action commands
    (import, recategorize, reset) always emit plain text.
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["format"] = output_format
    ctx.obj["debug"] = debug
    ctx.obj["quiet"] = quiet


# ── write path ────────────────────────────────────────────────────────────


@cli.command(name="import")
@click.argument("filepath", type=_XML_ARG)
@click.option("--categories", "categories_file", type=_TOML_OPT, default=None,
              help="Custom category rules (TOML).")
@click.option("--dry-run", is_flag=True,
              help="Report what would be imported without writing.")
@click.pass_context
def import_cmd(
    ctx: click.Context,
    filepath: Path,
    categories_file: Path | None,
    dry_run: bool,
) -> None:
    """Import transactions from an SMS backup XML file.

    Safe to run repeatedly — duplicates are skipped automatically, so importing
    a newer overlapping backup adds only the new messages.
    """
    categorizer = Categorizer(categories_file)
    with _open_db(ctx) as db:
        started = perf_counter()
        result = import_sms_backup(filepath, db, categorizer, dry_run=dry_run)
        elapsed = perf_counter() - started

        if dry_run:
            click.echo("DRY RUN — nothing was written.")
        click.echo(f"Imported from      : {filepath}")
        click.echo(f"Elapsed            : {elapsed:.2f}s")
        click.echo(f"SMS messages read  : {result.total_sms_count}")
        click.echo(f"MMS skipped        : {result.skipped_mms_count}")
        click.echo(f"Transactions added : {result.txns_added}")
        click.echo(f"Duplicates skipped : {result.duplicates_skipped}")
        if result.parse_errors:
            click.echo(f"Parse errors       : {len(result.parse_errors)}")
            for err in result.parse_errors[:10]:
                click.echo(f"    {err}")
            if len(result.parse_errors) > 10:
                click.echo(f"    … and {len(result.parse_errors) - 10} more")
        click.echo("Messages by bank:")
        for bank, count in sorted(result.bank_counts.items()):
            click.echo(f"    {bank:<8} {count}")
        if not dry_run:
            click.echo(f"Database           : {db.path}")
            click.echo(f"Total in database  : {db.count()}")


@cli.command()
@click.option("--categories", "categories_file", type=_TOML_OPT, default=None,
              help="Custom category rules (TOML).")
@click.pass_context
def recategorize(ctx: click.Context, categories_file: Path | None) -> None:
    """Re-apply category rules to all stored transactions."""
    categorizer = Categorizer(categories_file)
    with _open_db(ctx) as db:
        updated = db.update_categories(categorizer)
    click.echo(f"Updated {updated} transactions.")


@cli.command()
@click.confirmation_option(
    prompt="This deletes ALL transactions and import history. Continue?"
)
@click.pass_context
def reset(ctx: click.Context) -> None:
    """Delete all transactions and import history (keeps the database file)."""
    with _open_db(ctx) as db:
        db.reset()
    click.echo("Database reset.")


# ── read path ─────────────────────────────────────────────────────────────


@cli.command()
@click.option("--bank", default=None, help="Filter by bank ID (e.g. HBL).")
@click.option("--currency", default=None,
              type=click.Choice([c.value for c in Currency], case_sensitive=False))
@click.option("--vendor", default=None, help="Vendor substring match.")
@click.option("--category", default=None, help="Exact category match.")
@click.option("--from", "from_date", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD).")
@click.option("--min-amount", type=float, default=None)
@click.option("--max-amount", type=float, default=None)
@click.option("--limit", "-n", type=int, default=None, help="Max rows.")
@click.pass_context
def transactions(ctx: click.Context, /, **filters: Any) -> None:
    """List transactions. All filters are AND-combined.

    `ctx` is positional-only (`/`): `@click.pass_context` passes it
    positionally, and without the marker mypy rejects the signature against
    pass_context's expected `def (Context, /, **filters: Any)`.
    """
    with _open_db(ctx) as db:
        rows = db.query_transactions(**filters)
    click.echo(formatters.format_transactions(rows, ctx.obj["format"]))


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", type=int, default=None, help="Max rows.")
@click.pass_context
def search(ctx: click.Context, query: str, limit: int | None) -> None:
    """Search the full SMS text of stored transactions for a keyword."""
    with _open_db(ctx) as db:
        rows = db.query_transactions(body_contains=query, limit=limit)
    click.echo(formatters.format_transactions(rows, ctx.obj["format"]))


@cli.command()
@click.pass_context
def vendors(ctx: click.Context) -> None:
    """List unique vendors across all transactions."""
    with _open_db(ctx) as db:
        names = db.unique_vendors()
    click.echo(formatters.format_vendors(names, ctx.obj["format"]))


@cli.command(name="top-vendors")
@click.option("-n", "count", default=10, show_default=True, help="How many.")
@click.pass_context
def top_vendors(ctx: click.Context, count: int) -> None:
    """Show the most frequent vendors by transaction count."""
    with _open_db(ctx) as db:
        pairs = db.top_vendors(count)
    click.echo(formatters.format_top_vendors(pairs, ctx.obj["format"]))


@cli.command()
@click.pass_context
def monthly(ctx: click.Context) -> None:
    """Month-by-month spending, broken down by currency."""
    with _open_db(ctx) as db:
        rows = db.monthly_summary()
    click.echo(formatters.format_monthly(rows, ctx.obj["format"]))


@cli.command()
@click.pass_context
def categories(ctx: click.Context) -> None:
    """Spending totals by category."""
    with _open_db(ctx) as db:
        rows = db.category_summary()
    click.echo(formatters.format_categories(rows, ctx.obj["format"]))


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Database statistics and import history."""
    with _open_db(ctx) as db:
        summary = db.stats()
        history = db.import_history()
    click.echo(formatters.format_stats(summary, history, ctx.obj["format"]))


def main() -> None:
    """Console entry point.

    `standalone_mode=False` is required: with the default True, Click installs
    its own handler and this function's `raise click.ClickException(...)` would
    sit ABOVE it with nothing left to call `.show()` — producing a raw
    traceback for every domain error, which is precisely the failure D14 is
    meant to eliminate. Turning standalone mode off makes this function the
    sole error boundary, so it must handle Click's own exception types too.
    """
    try:
        cli(obj={}, standalone_mode=False)
    except SmsAnalyzerError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.Abort:
        click.echo("Aborted!", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover
    main()
```

**Verified behavior of `main()`.** An earlier draft raised `click.ClickException` from inside
`main()` while leaving `standalone_mode` at its default. That does not work, and the reasoning
behind it was backwards: Click does *not* swallow the exception before `main()` sees it — `main()`
sees it fine — but the `ClickException` it then raises has no handler above it, so Python prints
the traceback and exits 1. Reproduced against a real installed console script. The form above is
tested and correct.

One consequence of `standalone_mode=False` to keep in mind: Click no longer auto-handles
`--help`/`--version` exits or usage errors, which is why `click.ClickException` and `click.Abort`
are caught explicitly. `test_import_nonexistent_file_exits_2` and `test_reset_requires_confirmation`
in §8 cover both paths.

---

## 8. Test Suite Specification

**Constraint from `--import-mode=importlib`: test modules cannot import each other.** Every shared
helper and sample must therefore be reachable **as a fixture** — a module-level constant in
`conftest.py` is *not* reachable (`from conftest import HBL_CHARGE` → `ModuleNotFoundError`, since
`tests/` is not on `sys.path`). An earlier draft of this plan made exactly that mistake and would
have failed at collection in ~40 tests.

`tests/__init__.py` and `tests/test_parsers/__init__.py` **do** exist, empty — they are what makes
the mypy `tests.*` override match (§6). They do not change pytest's behavior under importlib mode,
and they do not make cross-module imports work.

### 8.1 `tests/conftest.py` (complete)

All sample data is **synthetic** — same formats, fake values. Note every sample carries a
`date_ms` matching its own body date, so the parser's >48h drift check does not fire spuriously.

```python
"""Shared fixtures.

Under importlib import mode test modules cannot import each other, so
everything shared is exposed as a FIXTURE, never as a module-level constant.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import sms_txn_analyzer.cli as cli_module
import sms_txn_analyzer.db as db_module
from sms_txn_analyzer.config import DEFAULT_TZ
from sms_txn_analyzer.db import TransactionDB
from sms_txn_analyzer.models import Currency, Transaction, TransactionType
from sms_txn_analyzer.parsers.base import BaseBankParser

# Synthetic bodies. Formats match real bank messages exactly; every digit,
# vendor, and phone number is fabricated. Private to this module — tests reach
# them through the `samples` fixture below.
_HBL_CHARGE = (
    "Dear Customer, Your HBL CreditCard (ending with 4321) has been charged "
    "at ACME SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
)
_HBL_CHARGE_NO_COMMA = (
    "Dear Customer, Your HBL CreditCard (ending with 4321) has been charged "
    "at ACME SUPER MARKET for PKR-25170.49 on 01/Oct/2023."
)
_HBL_CHARGE_USD = (
    "Dear Customer, Your HBL CreditCard (ending with 4321) has been charged "
    "at EXAMPLE.COM for USD-49.99 on 03/Oct/2023."
)
_HBL_CHARGE_ONE_DECIMAL = (
    "Dear Customer, Your HBL CreditCard (ending with 4321) has been charged "
    "at ACME FUEL for PKR-1,500.5 on 15/Nov/2023."
)
_HBL_CHARGE_SECOND = (
    "Dear Customer, Your HBL CreditCard (ending with 4321) has been charged "
    "at ACME GARMENTS for PKR-22,001.00 on 25/Sep/2023."
)
_HBL_OTP = (
    "000000 is your One Time Password (OTP) for the internet transaction on "
    "HBL Card ending with 0000. This OTP is valid for 10 mins. Do not share "
    "OTP with anyone."
)
# Standard Chartered charge format — a target for a future SCB parser.
_SCB_CHARGE = (
    "Dear Client, PKR 15134.00 have been paid at ACME FUEL (EXAMPLE ROAD "
    "KARACHI PAK on 02-10-23 using Credit Card no 0000xxxxxxxx0000. "
    "Avail Limit PKR44429.45. SCBPL"
)

# Epoch ms matching each body's own date, so _warn_on_date_drift stays quiet.
_MS_2023_09_25 = 1695600000000
_MS_2023_10_01 = 1696118400000
_MS_2023_10_02 = 1696204800000
_MS_2023_10_03 = 1696291200000
_MS_2023_11_15 = 1700006400000


@pytest.fixture
def samples() -> SimpleNamespace:
    """Synthetic SMS bodies, each paired with a matching timestamp.

    A fixture rather than module-level constants: under importlib import mode
    test modules cannot import conftest.
    """
    return SimpleNamespace(
        hbl_code="4250",
        scb_code="7220",
        hbl_charge=(_HBL_CHARGE, _MS_2023_10_01),
        hbl_charge_no_comma=(_HBL_CHARGE_NO_COMMA, _MS_2023_10_01),
        hbl_charge_usd=(_HBL_CHARGE_USD, _MS_2023_10_03),
        hbl_charge_one_decimal=(_HBL_CHARGE_ONE_DECIMAL, _MS_2023_11_15),
        hbl_charge_second=(_HBL_CHARGE_SECOND, _MS_2023_09_25),
        hbl_otp=(_HBL_OTP, _MS_2023_10_01),
        scb_charge=(_SCB_CHARGE, _MS_2023_10_02),
    )


@pytest.fixture
def make_sms() -> Callable[..., ET.Element]:
    """Factory building an <sms> element with real-backup attributes."""

    def _make(address: str, body: str, date_ms: int = _MS_2023_10_01) -> ET.Element:
        sms = ET.Element("sms")
        sms.attrib.update(
            protocol="0", address=address, date=str(date_ms), type="1",
            subject="null", body=body, toa="null", sc_toa="null",
            service_center="+920000000000", read="1", status="-1", locked="0",
            date_sent=str(date_ms - 2000), sub_id="1",
            readable_date="Oct 1, 2023 9:57:06 PM", contact_name="(Unknown)",
        )
        return sms

    return _make


@pytest.fixture
def write_backup() -> Callable[..., str]:
    """Factory writing a <smses> backup file; returns its path."""

    def _write(path: Path, elements: list[ET.Element], mms_count: int = 0) -> str:
        root = ET.Element("smses", count=str(len(elements) + mms_count))
        for el in elements:
            root.append(el)
        for _ in range(mms_count):
            root.append(ET.Element("mms"))
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        return str(path)

    return _write


@pytest.fixture(autouse=True)
def _never_touch_the_real_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Redirect the default database path for EVERY test.

    Autouse and non-negotiable: a CLI test that omits `--db` would otherwise
    run against the user's real database at
    %LOCALAPPDATA%\\sms-txn-analyzer\\transactions.db — and
    `test_reset_requires_confirmation` would delete their actual data.

    `db_module` is the one that matters: TransactionDB.__init__ resolves the
    default from the name bound in `sms_txn_analyzer.db`, not the one in
    `cli`. Patching only cli's copy (which is used solely to render help text)
    would leave the real database fully exposed.
    """
    fake = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", fake)
    monkeypatch.setattr(cli_module, "DEFAULT_DB_PATH", fake)


@pytest.fixture
def db() -> Iterator[TransactionDB]:
    """Fresh in-memory database per test."""
    instance = TransactionDB.in_memory()
    yield instance
    instance.close()


@pytest.fixture
def sample_transaction() -> Transaction:
    return Transaction(
        bank="HBL",
        txn_type=TransactionType.CREDIT_CARD,
        currency=Currency.PKR,
        amount=25170.49,
        vendor="ACME SUPER MARKET",
        date=datetime(2023, 10, 1, tzinfo=DEFAULT_TZ),
        card_last_four=4321,
        sms_body=_HBL_CHARGE,
    )


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    """Snapshot/restore the parser registry around a test that mutates it."""
    snapshot = BaseBankParser._snapshot_registry()
    yield
    BaseBankParser._restore_registry(snapshot)
```

### 8.2 Test enumeration

Every test below is required. Tests marked **★** are regression tests for defects in §5 — they must
fail against the old code's behavior and pass against the new.

#### `tests/test_config.py` (3)
- `test_default_tz_is_karachi` — also the canary for a missing `tzdata`: if that dependency is ever
  dropped, this is the first thing to fail
- `test_db_path_uses_localappdata` — monkeypatch `LOCALAPPDATA` to a temp dir, re-invoke
  `_default_data_dir()`, assert the result is `<temp>/sms-txn-analyzer`
- `test_db_path_falls_back_when_localappdata_unset` — `monkeypatch.delenv("LOCALAPPDATA")`, assert
  the `~/AppData/Local` fallback

#### `tests/test_models.py` (12)
- `test_valid_transaction_coerces_strings` — `currency="PKR"` → `Currency.PKR`; `amount="25,170.49"` → `25170.49`
- `test_negative_amount_rejected` — `pytest.raises(ValidationError, match="greater than 0")`
- `test_zero_amount_rejected`
- `test_empty_vendor_rejected` — `match="at least 1 character"`
- `test_naive_datetime_rejected` — `match="timezone"` (Pydantic error type `timezone_aware`)
- `test_unknown_currency_rejected` ★ D11
- `test_card_last_four_upper_bound` — `card_last_four=99999` rejected
- `test_model_dump_json_types` — `date` is `str`, `currency` is `str` `"pkr"`, `amount` is `float`
- `test_txn_date_and_month_are_local` ★ D6 — a `2023-10-01T00:00:00+05:00` transaction yields `txn_date == "2023-10-01"` and `txn_month == "2023-10"`, **not** September
- `test_frozen_rejects_assignment`
- `test_import_result_bank_counts_is_defaultdict` — both when the default fires and when a `defaultdict` is passed explicitly
- `test_import_result_defaults_are_zero`

#### `tests/test_parsers/test_base.py` (6)
- `test_hbl_registered_for_its_short_code`
- `test_unknown_address_returns_none`
- `test_all_four_banks_registered` — HBL, FBL, SCB, MEZN
- `test_meezan_has_two_short_codes`
- `test_duplicate_short_code_raises` — uses `isolated_registry`; defines a subclass reusing `"4250"`, expects `ValueError`
- `test_known_addresses_matches_registry`

Each sample is a `(body, timestamp_ms)` pair reached via the `samples` fixture, e.g.
`body, ts = samples.hbl_charge`. `make_sms` and `write_backup` are factory fixtures — request them
by name, then call the returned function.

#### `tests/test_parsers/test_hbl.py` (16)
- `test_identifies_charge_sms`
- `test_rejects_otp_sms`
- `test_identification_is_case_insensitive` ★ D-notes
- `test_parses_pkr_with_comma`
- `test_parses_pkr_without_comma` ★ **D2** — `samples.hbl_charge_no_comma` must yield `25170.49`
- `test_parses_one_decimal_place` ★ **D3** — `PKR-1,500.5` → `1500.5`, not `1500.00`
- `test_parses_three_decimal_places` ★ **D3**
- `test_parses_usd`
- `test_date_is_tz_aware_karachi` ★ **D4** — assert `tzinfo` is `DEFAULT_TZ` and the date is `2023-10-01`, with a monkeypatched non-PKT `TZ` to prove independence
- `test_month_parsing_is_locale_independent` ★ **D9** — monkeypatch `locale.setlocale` to a non-English locale (or assert `_MONTHS` is used and `strptime` is never called for months)
- `test_trailing_text_after_date_still_parses` ★ **D8** — append `" Avail limit PKR 1,000.00."` to a valid body
- `test_vendor_with_spaces_and_punctuation`
- `test_unmatched_body_raises_parse_error`
- `test_unknown_currency_raises_parse_error` ★ D11
- `test_card_last_four_extracted`
- `test_date_drift_warning_logged` — `caplog`, timestamp 30 days from the body date, assert a WARNING is emitted and the transaction still parses

#### `tests/test_db.py` (18)
- `test_schema_version_recorded_on_create`
- `test_migrations_are_idempotent` — reopen the same file DB twice, version stays 1
- `test_in_memory_is_not_default` ★ **D-14b** — `TransactionDB(tmp_path/"x.db").path` is that path, not `":memory:"`
- `test_insert_returns_true_then_false_on_duplicate`
- `test_duplicate_is_not_counted_twice`
- `test_round_trip_preserves_all_fields` — insert → `query_transactions` → compare field-by-field
- `test_filter_by_bank` / `test_filter_by_currency` / `test_filter_by_vendor_substring`
- `test_filter_by_category`
- `test_filter_by_date_range` — inclusive on both ends
- `test_filter_by_min_and_max_amount` ★ — proves numeric, not lexicographic, comparison: `9.99` must **not** match `min_amount=5000`
- `test_filters_are_and_combined`
- `test_limit_is_parameterized` — passing `limit="1; DROP TABLE transactions"` raises rather than executing
- `test_monthly_summary_buckets_by_local_month` ★ **D6** — a `2023-10-01T00:00:00+05:00` row appears under `2023-10`
- `test_monthly_summary_splits_by_currency`
- `test_top_vendors_orders_by_count`
- `test_update_categories_commits_and_returns_count`
- `test_reset_clears_transactions_and_log`

#### `tests/test_importer.py` (14)
- `test_imports_single_transaction`
- `test_skips_mms_elements`
- `test_counts_bank_messages`
- `test_duplicate_message_counted_as_duplicate_not_other` ★ **D1** — the headline regression: two identical HBL messages plus one unknown-sender message must yield `bank_counts["HBL"] == 2`, `duplicates_skipped == 1`, `bank_counts["OTHER"] == 1`
- `test_reimporting_same_file_adds_nothing` — idempotency
- `test_overlapping_backups_add_only_new`
- `test_distinct_charges_same_body_different_timestamp_both_kept` ★ **D5/J** — the case body-only hashing collapsed
- `test_empty_backup_file_imports_cleanly` ★ **D10** — `count="0"`, no exception
- `test_missing_address_or_body_is_skipped` ★ **D13**
- `test_malformed_xml_raises_file_load_error` ★ **D14**
- `test_missing_file_raises_file_load_error`
- `test_parse_error_is_collected_not_fatal` ★ **D7** — one malformed HBL charge among three valid ones: 3 valid still import, 1 error recorded
- `test_dry_run_writes_nothing` — `db.count() == 0`, `result.txns_added > 0`
- `test_categorizer_applied_at_import`

#### `tests/test_categorizer.py` (7)
- `test_bundled_categories_load` — proves `importlib.resources` works
- `test_exact_keyword_match`
- `test_substring_match`
- `test_case_insensitive_match`
- `test_unmatched_returns_uncategorized`
- `test_custom_toml_overrides_bundled`
- `test_malformed_toml_raises_config_error` — a category with `keywords = []` must fail `min_length=1`

#### `tests/test_formatters.py` (12)
Two per formatter × the three formats, parameterized where sensible:
- `test_transactions_table_has_header` / `_csv_is_parseable` (round-trip via `csv.reader`) / `_json_is_valid` (round-trip via `json.loads`, assert no `sms_body` key)
- `test_transactions_empty_table_message`
- `test_vendors_all_formats`
- `test_monthly_all_formats`
- `test_categories_all_formats`
- `test_top_vendors_all_formats`
- `test_stats_all_formats`
- `test_amounts_are_two_decimals_in_csv`

#### `tests/test_cli.py` (14)
Use `CliRunner()` with **no** `mix_stderr` argument (removed in Click 8.2). Assert on
`result.stdout` and `result.stderr` separately.

- `test_version_flag` — exit 0, output contains the packaged version
- `test_help_lists_all_commands`
- `test_import_then_transactions_round_trip` — `--db tmp_path/x.db` for both invocations
- `test_import_reports_counts`
- `test_import_dry_run_leaves_db_empty`
- `test_import_nonexistent_file_exits_2` — Click's `Path(exists=True)`, message on **stderr**
- `test_import_directory_rejected` ★ **D15** — passing a directory exits 2
- `test_transactions_format_json_parses`
- `test_transactions_format_csv_parses`
- `test_transactions_filters_combine`
- `test_search_matches_sms_body_not_vendor` ★ issue F — a keyword present only in `sms_body` returns a row
- `test_stats_after_import`
- `test_reset_requires_confirmation` — without `--yes`, aborts; with input `"y\n"`, clears
- `test_recategorize_updates_counts`

**Total: 102 tests.** Coverage target: **≥90%** on `src/sms_txn_analyzer/`, enforced in CI via
`--cov-fail-under=90`.

---

## 9. CI

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  check:
    # windows-latest ONLY. This is a Windows tool; an ubuntu leg would test a
    # platform nobody runs and would actively mislead — the tzdata defect
    # (§5 D17) passes on Linux and fails on Windows, so a Linux job would have
    # reported green while the only machine that matters was broken.
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7

      - name: Install uv
        # SHA-pinned: tags are mutable. Comment records the human-readable tag.
        uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          enable-cache: true
          # NOTE: do NOT add `python-version-file:` — it is an actions/setup-python
          # input and does not exist on setup-uv (verified against action.yml at
          # this SHA). GitHub would warn "Unexpected input(s)" and ignore it.
          # uv reads .python-version itself during sync.

      - name: Verify lockfile is current
        run: uv lock --check

      - name: Install dependencies
        run: uv sync --locked --all-groups

      - name: Lint
        run: uv run --no-sync ruff check --output-format=github .

      - name: Format check
        run: uv run --no-sync ruff format --check .

      - name: Type check
        run: uv run --no-sync mypy

      - name: Test
        run: uv run --no-sync pytest --cov --cov-fail-under=90

      # The only gate that catches an entry-point / module-name mismatch.
      - name: Entry point resolves
        run: uv run --no-sync sms-txn-analyzer --help
```

Why each choice:

- **`setup-uv@v9.0.0`, SHA-pinned.** The uv docs currently show v8.1.0 — they lag one major. v9.0.0
  (2026-07-21) flipped `prune-cache` to `false` by default; if the repo cache approaches GitHub's
  10 GB limit, add `prune-cache: true`.
- **`uv lock --check` as its own step** so a stale lockfile fails with a clear message rather than
  a muddy install error.
- **`uv sync --locked`** is the security-relevant flag: CI installs exactly the reviewed,
  hash-pinned lockfile and fails on drift.
- **`uv run --no-sync`** on every subsequent step prevents implicit re-resolution mid-run.
- There is no useful `npm --ignore-scripts` analogue here. `uv sync --no-build` **fails** on any
  packaged project because your own project is a source tree; the per-package form
  (`--no-build-package click`) is unnecessary theatre since click and pydantic ship pure-Python
  wheels. The real hardening is the committed, hash-bearing lockfile plus `--locked`. uv 0.12
  additionally rejects MD5-only hashes and `.tar.bz2`/`.tar.xz` sdists.

---

## 10. Documentation Updates

### `CLAUDE.md` — rewrite

The current file is stale in ways that will actively mislead a future session. Fix at minimum:

1. **"Python 3.11+ required" → "Python 3.14 (single target, not a floor). Windows 11 only."**
2. **Every "run from `src/`" instruction is deleted.** The commands become:
   ```bash
   uv sync                 # once
   uv run pytest
   uv run pytest tests/test_parsers/test_hbl.py
   uv run pytest -k test_parses_pkr_without_comma
   uv run ruff check . && uv run ruff format --check .
   uv run mypy
   uv run sms-txn-analyzer --help
   ```
3. **The documented single-test command is wrong today** — `python -m unittest tests.test_...`
   fails because there is no importable `tests` package from `src/`. Replaced by `pytest -k`.
4. **Remove the "duplicate hash computed multiple times" claim** — it is false (§5).
5. **Remove "exceptions are not raised on malformed messages"** — `AssertionError` *is* raised today
   and aborts the run; in the new design parse errors are collected non-fatally.
6. Add a **Verification** section: typecheck → test → lint, matching the global guidance.
7. Add the **dependency-policy note** from §2 so the uv range-vs-pin approach doesn't read as a
   violation of the global npm rule:
   > Python dependencies declare compatibility floors in `pyproject.toml`; `uv.lock` pins exact
   > versions with SHA-256 hashes and is committed. CI runs `uv lock --check` and
   > `uv sync --locked`. Review `uv.lock` diffs with the same scrutiny as source diffs.
8. Rewrite Architecture, Key Components, and Known Issues against the new layout. Delete the
   Known Issues list wholesale — it describes deleted code.

### `README.md` — rewrite

Install, quickstart, the full command table, `--format` examples, where the database lives per
platform, and how to add a bank parser (one file, two methods, no other edits). State plainly that
the tool targets **Windows 11 with Python 3.14**, and that the database lives at
`%LOCALAPPDATA%\sms-txn-analyzer\transactions.db`.

### `PROPOSED_REDESIGN.md` — keep

Valuable as design rationale (the SQLite and Pydantic arguments in §11–12 especially). Add a header
note: *"Superseded for implementation purposes by `IMPLEMENTATION_PLAN.md`, which resolves the open
issues catalogued in review. Retained for rationale."*

### `src/IMPROVEMENTS.md` — delete

Its content is absorbed into §5, corrected (item 1c was wrong) and extended.

---

## 11. Execution Order

Each step ends at a green gate (§12). Do not proceed past a red gate.

**1 — Scaffold uv.**
Write `pyproject.toml`, `.python-version` (via `uv python pin 3.14`), root `.gitignore`.
Delete `src/.gitignore`, `src/parser/.gitignore`, `tests/.gitignore`.
Create `src/sms_txn_analyzer/__init__.py`.
Run `uv sync`. **Do not run `uv init`.**

**2 — Foundation modules.** `config.py`, `exceptions.py`, `models.py`. Write `tests/conftest.py`,
`test_config.py`, `test_models.py`.

**3 — Parsers.** `parsers/base.py`, `parsers/__init__.py`, `parsers/hbl.py`, then the three stubs.
Write `test_parsers/test_base.py`, `test_parsers/test_hbl.py`.

**4 — Persistence.** `db.py` + `test_db.py`.

**5 — Categorization.** `categories.toml`, `categorizer.py` + `test_categorizer.py`.

**6 — Import pipeline.** `importer.py` + `test_importer.py`.

**7 — Output.** `formatters.py` + `test_formatters.py`.

**8 — CLI.** `cli.py` + `test_cli.py`. Confirm `uv run sms-txn-analyzer --help` works — this
validates the entry point, the build backend, and the package name all at once.

**9 — Delete the old code.** Everything in §4's deletion list. Move
`src/.claude/settings.local.json` → `.claude/settings.local.json` and trim the redundant
`Bash(python …)` allow-rules subsumed by `Bash(python:*)`.

**10 — CI + docs.** `.github/workflows/ci.yml`, rewrite `CLAUDE.md` and `README.md`, add the header
note to `PROPOSED_REDESIGN.md`, delete `src/IMPROVEMENTS.md`.

**11 — Full verification.** Run every gate. Then a real end-to-end pass against an actual backup
file if one is available (`--db ./scratch.db` so the real database is untouched).

### Commit boundaries

Do not commit unless asked. If asked, these are the natural boundaries: (1) steps 1–2, (2) step 3,
(3) steps 4–7, (4) step 8, (5) steps 9–10. Branch first — do not commit to `main` directly.

---

## 12. Verification Gates

```bash
uv lock --check            # lockfile matches pyproject
uv sync --locked           # environment matches lockfile
uv run mypy                # typecheck  (fastest failure first)
uv run pytest --cov        # tests + coverage
uv run ruff check .        # lint
uv run ruff format --check .
uv run sms-txn-analyzer --help    # entry point resolves
```

Per-step gates:

| Step | Gate |
|------|------|
| 1 | `uv sync` succeeds; `uv run python -c "import sms_txn_analyzer"` works |
| 2 | `uv run pytest tests/test_models.py tests/test_config.py` green; `uv run mypy` clean |
| 3 | `uv run pytest tests/test_parsers/` green — **all ★ regression tests must pass** |
| 4 | `uv run pytest tests/test_db.py` green, including the local-month and numeric-comparison tests |
| 5–7 | Respective test file green; `uv run mypy` still clean |
| 8 | `uv run sms-txn-analyzer --help`; `uv run pytest tests/test_cli.py` green |
| 9 | `uv run pytest` fully green with no references to deleted modules |
| 10–11 | Every command above green; coverage ≥90% |

**Expected first-run friction, so it isn't mistaken for failure:**
- `uv sync` prints `Using CPython 3.14.3 interpreter at: C:\Program Files\Python314\python.exe` and
  downloads no interpreter — 3.14 is already the system Python.
- `uv sync` on this `D:` repo prints the cross-drive hardlink warning (§3). Harmless.
- `uv run ruff format .` will reformat most of §7 on first run — the code here is hand-wrapped near
  88 chars while `line-length = 100`. Expected; run it rather than fighting it.
- Do **not** add `# type: ignore[abstract]` anywhere. mypy 2.3.0 does not flag lazy instantiation,
  and `strict = true` implies `warn_unused_ignores`, so a speculative ignore comment becomes a hard
  error: `Unused "type: ignore" comment [unused-ignore]`.

---

## 13. Out of Scope

Deliberately excluded. Listed so the boundary is explicit rather than accidental.

| Item | Why |
|------|-----|
| **Git history rewrite** | Settled: the card digits, OTP, PAN, and SMSC numbers in past commits are stale/expired and carry no live risk. No `git-filter-repo`, no force-push, no follow-up. New fixtures are synthetic simply because there is no reason for new code to carry real values. |
| **Real parsers for FBL / SCB / Meezan** | Stubs register and count. A real SCB format sample is preserved in `scb.py`'s docstring and `conftest.py`. Implementing them needs message samples we don't have. |
| **`--since DATE` on import** | Marginal value; `iterparse` already keeps memory flat, which was its rationale. |
| **`rich` output** | A fourth format for a tool whose CSV/JSON paths already cover scripting. Plain-text tables are dependency-free. |
| **Fuzzy vendor matching / interactive categorize** | Explicitly deferred by the design doc; substring rules cover the real vendor set. |
| **Publishing to PyPI** | `uv build` works if wanted later; `uv build --no-sources` belongs in a release checklist. |
| **Multi-currency conversion / FX rates** | Totals stay per-currency, as today. |
| **Linux / macOS support** | Windows 11 Pro is the only target. No `sys.platform` branches, no POSIX path handling, no non-Windows CI leg. If it is ever needed, only `config.py`'s data directory and the CI job need revisiting. |
| **Python < 3.14** | Single-version target, not a floor. No back-compat shims, no version matrix. |

---

## Appendix: Quick Command Reference

```bash
# setup
uv sync
uv run sms-txn-analyzer --help

# import (repeatable, dedups automatically)
uv run sms-txn-analyzer import backup.xml
uv run sms-txn-analyzer import backup.xml --dry-run
uv run sms-txn-analyzer import backup.xml --categories my-rules.toml

# query
uv run sms-txn-analyzer transactions --from 2023-10-01 --to 2023-12-31 --currency pkr
uv run sms-txn-analyzer transactions --vendor ACME --min-amount 5000 -n 20
uv run sms-txn-analyzer transactions --category Groceries --format csv > groceries.csv
uv run sms-txn-analyzer search "ending with 4321"
uv run sms-txn-analyzer monthly --format json
uv run sms-txn-analyzer categories
uv run sms-txn-analyzer top-vendors -n 5
uv run sms-txn-analyzer vendors
uv run sms-txn-analyzer stats

# maintenance
uv run sms-txn-analyzer recategorize --categories updated.toml
uv run sms-txn-analyzer reset
uv run sms-txn-analyzer --db ./scratch.db import backup.xml   # isolated database

# install globally
uv tool install .
sms-txn-analyzer --help
```
