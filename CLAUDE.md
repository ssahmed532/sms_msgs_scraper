# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMS Messages Scraper — A Python CLI tool for parsing and analyzing bank transaction SMS messages
from Pakistani banks. It processes Android SMS backup files (XML from the "SMS Backup & Restore"
app) to extract, deduplicate, and summarize financial transactions.

All four banks are fully parsed: **HBL** (Habib Bank Limited), **FBL** (Faysal Bank) and
**SCB** (Standard Chartered) credit-card txns, plus **MEZN** (Meezan Bank) account debits.

CC txns from the three CC banks share one store (`ccTxns`) and are tagged with their issuing bank,
so the CC commands report them together and `--bank` splits them apart. Meezan account debits are a
different kind of transaction (card purchases, ATM withdrawals, bill payments, funds transfers) and
live in their own store (`debitTxns`) with their own two commands.

**Version:** 2.0.0.

### Semantic versioning is mandatory

**This tool MUST follow [semantic versioning](https://semver.org/).** Every change that ships is
released under a version number chosen by what it does to someone already using the tool, never by
how large the diff was:

- **MAJOR** — an existing invocation stops working or starts meaning something else: a command or
  option renamed or removed, an option's accepted values narrowed, a default that changes which
  txns come back, or output moving between streams.
- **MINOR** — new capability, every existing invocation unaffected: a new command, a new option, a
  new bank parser, a reworked presentation of the same data.
- **PATCH** — a fix with no new capability: a parser corrected, a miscount, a crash.

Judge it from the outside. A rewrite that leaves every command behaving identically is a PATCH or
nothing at all; a one-line new option is a MINOR. Output that a script could be parsing counts as
interface.

The number lives in **two** places that must agree — it used to be three, and they had already
drifted apart once:

1. `[project].version` in `pyproject.toml` — the single authority
2. `uv.lock` — refreshed by re-running `uv lock`, never hand-edited

The CLI no longer restates it: `sms_msgs_scraper.__version__` reads the installed package metadata
via `importlib.metadata`, and `@click.version_option` uses that. All of this is pinned by
`tests/test_versioning.py`, including the lockfile — the one that gets forgotten, because nothing
about editing `pyproject.toml` prompts you to re-run `uv lock`.

## Development Commands

Dependencies are managed with **uv**. `uv sync` creates `.venv` and installs the project.

### Running the checks — fastest first

```bash
# 1. lint and type check
uv run --with ruff ruff check .
uv run --with pyright pyright

# 2. the test suite, from the REPOSITORY ROOT
uv run python -m unittest discover -s tests/ -v

# a single test file
uv run python -m unittest discover -s tests/ -p "test_hbl_sms_parser.py" -v

# 3. branch coverage (threshold 90%, currently 93%)
uv run --with coverage coverage run -m unittest discover -s tests/
uv run --with coverage coverage report
```

Tests run from the repository root. They used to have to run from `src/`, because imports resolved
through a `sys.path` accident rather than a real package; that is no longer true.

### Running the CLI Tool

```bash
uv run sms-txn [GLOBAL OPTIONS] <path_to_sms_backup.xml> <command> [OPTIONS]

# Credit card commands (HBL + FBL + SCB combined):
#   list_all_vendors               - Unique vendors from CC transactions
#   list_all_cc_txns               - List all credit card transactions
#   monthly_cc_spending_summary    - Month-wise CC spending by currency
#
# Meezan account debit commands:
#   list_all_debit_txns            - List all account debit transactions
#   monthly_debit_spending_summary - Month-wise debit spending by currency
#
# All five accept:
#   --from-date YYYY-MM-DD         - only txns on or after this date (inclusive)
#   --to-date   YYYY-MM-DD         - only txns on or before this date (inclusive)
#
# The three CC commands also accept:
#   --bank {HBL|FBL|SCB}           - case-insensitive; default all
#
# list_all_debit_txns also accepts:
#   --txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}
#
# Both monthly summary commands accept --verbose / -v.
#
# GLOBAL options must be written BEFORE the filepath, because a Click group
# stops parsing its own options at the first positional argument:
#   --format {table,json,csv}      - how results are written to stdout
#   --quiet / -q                   - suppress everything on stderr
#   --strict                       - exit 3 if anything could not be parsed
#   --duplicates {exact,none,review}
#   --no-color
```

```bash
uv run sms-txn backup.xml list_all_cc_txns --from-date 2024-01-01 --to-date 2024-12-31
uv run sms-txn backup.xml list_all_cc_txns --bank FBL
uv run sms-txn --format csv backup.xml list_all_debit_txns --txn-type atm_withdrawal > atm.csv
```

`python -m sms_msgs_scraper` is equivalent to `sms-txn` and works from a source checkout.

### Verifying against a real backup

```bash
uv run python scripts/verify_against_backup.py            # the reference backup, in the repo root
uv run python scripts/verify_against_backup.py other.xml
```

Run this after **any** change to a parser, to the routing, or to the dedup identity. The unit suite
proves the parsers work on hand-built msgs; this proves they still work on ~4,700 real ones.

It performs three kinds of check, and the first is the one that matters most:

- **Discovery.** It scans *every* sender for the banks' txn signatures **without consulting the
  registry's sender list**, and fails on a signature from a sender no bank claims. This check is
  expectation-independent, which is the only kind that could have caught the missing SCB sender.
  Deleting `9220` from the registry must make this fail — verified.
- **Invariants**, asserted for any backup: the conservation identity, exact envelope accounting,
  and the structural properties of every txn.
- **Expected values**, tied to one backup by its SHA-256 — counts *and* exact per-bank
  per-currency `Decimal` totals. Counts alone cannot catch an amount parsed wrongly: 1,696 txns
  summing to the wrong number is still 1,696 txns.

Output is counts and totals only. Parse diagnostics are typed values on the report now, so nothing
needs to capture stdout to keep msg bodies out of the output.

If an expected value misses, see the Anti-drift rule in the script's docstring: fix the code, or
re-derive the expectation and record the derivation — never edit the number to match the output.

### Dependency Management

```bash
uv sync                 # install/refresh .venv from uv.lock
uv sync --frozen        # what CI does: enforce the lockfile rather than trust it
uv lock --upgrade       # re-resolve within pyproject.toml constraints
uv build                # build the wheel and sdist
```

### Dependencies

- `click==8.4.2` — CLI framework
- `tzdata==2026.3` — **required on Windows.** Windows ships no system IANA tz database and
  uv-managed interpreters bundle none, so the module-scope `ZoneInfo("Asia/Karachi")` in
  `common.py` raises `ZoneInfoNotFoundError` without it. Bump periodically — this is the tz rule
  database, not just a library.
- `rich==15.0.0` — the table renderer, the parse summary and the diagnostics.
- `rich-click==1.9.8` — Rich rendering for Click's own `--help` and usage errors. A drop-in
  `import rich_click as click`, so every decorator, type and exception in the CLI module is still
  Click's own.
- Standard library only otherwise.

**Rich is a renderer, not a dependency of the parsing layer.** No parser and no orchestrator module
imports it. That is checkable: `grep -rl rich src/sms_msgs_scraper/parser/` finds nothing.

## Architecture

### Directory Structure

```
sms_msgs_scraper/
├── CLAUDE.md
├── README.md
├── pyproject.toml                # metadata, deps, ruff/pyright/coverage config
├── uv.lock                       # committed — the reproducibility guarantee
├── .python-version               # 3.14
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml      # windows-latest + Python 3.14 only
├── docs/
│   ├── ADVERSARIAL_CODE_REVIEW.md
│   ├── ADVERSARIAL_REMEDIATION_PLAN.md   # what 2.0.0 set out to fix, and why
│   └── archive/                  # finished + shelved plans; NOT current guidance
│       ├── IMPLEMENTATION_PLAN.md
│       ├── IMPROVEMENTS.md             # the pre-2.0.0 audit; its items are closed
│       ├── MULTI_BANK_PARSERS_PLAN.md
│       └── PROPOSED_REDESIGN.md
├── src/
│   └── sms_msgs_scraper/         # a real, installable package
│       ├── __init__.py           # __version__, from importlib.metadata
│       ├── __main__.py           # python -m sms_msgs_scraper
│       ├── sms_txn_query_tool.py # CLI entry point (rich_click)
│       ├── sms_backup_file_parser.py  # orchestrator
│       ├── cc_txn.py             # CreditCardTxnDC
│       ├── debit_txn.py          # DebitTxnDC, DebitTxnType
│       ├── common.py             # DEFAULT_TZ, and nothing else
│       ├── console_ui.py         # the two consoles, the theme, cell helpers
│       ├── domain/
│       │   ├── money.py          # Money, the amount grammar, minor units
│       │   ├── types.py          # CardReference
│       │   ├── message.py        # SmsRecord
│       │   ├── diagnostics.py    # SkipReason, ParseDiagnostic, ParseResult
│       │   ├── report.py         # ParseReport, EnvelopeCounts, DuplicatePolicy
│       │   ├── registry.py       # BankSpec, BankRegistry — the only sender list
│       │   └── aggregate.py      # exact totals over Money
│       ├── parser/
│       │   ├── hbl_sms_parser.py
│       │   ├── fbl_sms_parser.py
│       │   ├── scb_sms_parser.py
│       │   └── mezn_sms_parser.py
│       └── render/
│           ├── tables.py         # Rich tables
│           └── machine.py        # JSON and CSV
├── scripts/
│   └── verify_against_backup.py
└── tests/
    ├── fixtures/
    │   ├── build_synthetic_backup.py   # regenerates the fixture
    │   └── synthetic_backup.xml        # invented data, real templates
    ├── test_money.py / test_money_properties.py
    ├── test_report.py / test_registry.py
    ├── test_hbl_sms_parser.py / test_fbl_sms_parser.py
    ├── test_scb_sms_parser.py / test_mezn_sms_parser.py
    ├── test_sms_backup_file_parser.py
    ├── test_cli_commands.py / test_date_range_filter.py
    ├── test_adversarial_input.py / test_adversarial_cli.py
    ├── test_synthetic_corpus.py
    └── test_versioning.py
```

Each bank's end-to-end backup tests live in **that bank's own** test file. That is deliberate: it
keeps parallel work on different banks off a shared file, at the cost of each test file carrying its
own copy of a small `_parseBackup` helper.

### Module Relationships

```
sms_txn_query_tool.py (CLI, rich_click)
    ├── console_ui.py        (consoles, theme, cell helpers)
    ├── render/tables.py     (Rich tables)
    ├── render/machine.py    (JSON, CSV)
    ├── domain/aggregate.py  (exact totals over Money)
    ├── domain/registry.py   (--bank choices)
    └── sms_backup_file_parser.py (orchestrator)
            ├── domain/registry.py   ──→ parser/*   (signal + extract callables)
            ├── domain/message.py    (SmsRecord)
            ├── domain/diagnostics.py
            └── domain/report.py     ──→ cc_txn.py, debit_txn.py ──→ domain/money.py

parser/*.py  ──→ domain/money.py, domain/diagnostics.py, domain/message.py,
                 domain/types.py, cc_txn.py / debit_txn.py, common.py

domain/money.py, domain/types.py, domain/message.py are leaves.
```

The parsing layer imports nothing from `render/` or `console_ui.py`, so a parser cannot print.

### Key Components

**`domain/money.py`** — `Money`, and the one amount grammar every parser is checked against.

- The amount is a `Decimal` captured **straight from the matched token**, never through a float.
- Frozen and slotted; addition only within a single currency.
- A currency minor-unit table, with scale validated on construction — so an over-precise amount is
  *rejected* rather than silently rounded away by `,.2f` at render time.
- Digit length bounded, so no input can grow an amount without limit.
- Three token patterns: `AMOUNT_TOKEN_RE` (grouped-or-plain), `AMOUNT_TOKEN_2DP_RE` (mandatory two
  decimals), `AMOUNT_TOKEN_PLAIN_RE` (separator-free, for FBL). All three reject a mixed grouping
  like `1,2,3.00` that a permissive `[\d,]+` accepts and then "repairs" by stripping the commas.

There is deliberately **no invalid `Money`**. The `-1.2345` sentinel it replaces was worse than
invalid — it was *negative*, so a failed parse reaching a monthly total silently **reduced**
reported spending.

**`domain/registry.py`** — one `BankSpec` per bank, and the only place a sender short code appears.
Routing, `--bank` choices, summary rows and the verifier all derive from it. Registration raises
`DuplicateSenderError` if two banks claim one sender. `Capability.TXN_TIME` is what decides whether
a suppressed duplicate is ambiguous.

**`domain/diagnostics.py`** — a parse failure is a *value*, not a `print()`. `ParseDiagnostic` has
no field a msg body would fit in: it carries a `SkipReason` from a closed vocabulary plus a locator
built from the sender and the received timestamp. `ParseResult` is either a txn, a diagnostic, or —
via `okWithWarning` — a txn *and* a note, which is how an unrecognized SCB card mask keeps its
transaction while still being reported.

**`domain/report.py`** — the frozen `ParseReport`. Carries the envelope counts, the routing counts,
the txns, the diagnostics, the duplicate policy and per-duplicate provenance. Serialises and reads
back (`toDict` / `fromDict`), with amounts as exact strings.

**`SmsBackupFileParser`** — one-shot. `parse(filepath)` returns a `ParseReport`; a second call on
the same instance raises. It streams the file with `iterparse`, releasing each element as its record
is taken, so msg bodies are not retained for the process lifetime. It owns the file handle
explicitly, because `iterparse` closes a file it opened only when iteration completes — and a
mid-parse error would otherwise leak it, which on Windows locks the file.

Counting buckets, and the identity that ties them together:

| Bucket | Meaning |
|---|---|
| `ALL` | usable `<sms>` records seen (duplicates included) |
| `HBL` / `FBL` / `SCB` / `MEZN` | non-duplicate msgs from that bank's senders |
| `OTHER` | non-duplicate msgs from an unrecognized sender |
| `DUP` | msgs whose (sender, body) was already seen |
| `<ID>_SKIPPED` | msgs that carried a txn signal but could not be parsed |

**`ALL == HBL + FBL + SCB + MEZN + OTHER + DUP` must hold for any backup.** Pinned by tests and
asserted by the verifier on every run.

Envelope counts are tracked **separately** and mean different things: `declared` is the root's own
`count` attribute, which covers *all* child elements (the reference backup declares 4,719 = 4,665
`<sms>` + 54 `<mms>`); `actual`, `sms`, `mms` and `invalid` are what was found. A single "all"
meaning one or the other is how a truncated backup would go unnoticed.

**Dedup happens once, before routing.** The identity is **(sender short code, stripped body)**, so a
msg can only ever be suppressed by an earlier msg *from the same short code*. It deliberately does
**not** include a received timestamp — see the dedup limitations below for the measurements that
rule both candidate timestamps out.

**Output order is a contract.** Txns are sorted by `(date, bank, vendor, currency, amount)`. The
tie-breakers make the order *total*: HBL and SCB alerts carry a date only, so a great many txns
share midnight on the same day.

### Bank Short Codes

Declared in `domain/registry.py` and nowhere else.

| Bank | Short Code(s) | Parser | Produces |
|------|---------------|--------|----------|
| HBL | `4250`, `14250` | `HBLSmsParser` | `CreditCardTxnDC` (`bank="HBL"`) |
| Faysal Bank | `8756` | `FBLSmsParser` | `CreditCardTxnDC` (`bank="FBL"`) |
| Standard Chartered | `7220`, `9220` | `SCBSmsParser` | `CreditCardTxnDC` (`bank="SCB"`) |
| Meezan Bank | `8079`, `9779` | `MeznSmsParser` | `DebitTxnDC` (`bank="MEZN"`) |

**SCB sends from two short codes, and `9220` was undeclared until 2.0.0** — 18 txns, PKR 94,294.32,
counted as `OTHER` and never parsed. **HBL migrated CC txn alerts from `4250` to `14250` in mid-Jan
2025** (last `4250` 15/Jan/2025, first `14250` 18/Jan/2025) with the body format byte-for-byte
unchanged. Both are the same failure: a sender that nothing complains about. The verifier's
discovery scan is the guard against the next one.

### Data Flow

1. CLI resolves arguments. **Nothing is parsed until a command asks for the report**, so `--help`
   costs nothing.
2. `SmsBackupFileParser.parse()` streams the file, validating the root tag and the declared count.
3. Each `<sms>` becomes an `SmsRecord`, or is counted `invalid` with a diagnostic.
4. `ALL` is incremented, then dedup runs once (SHA3-512 of `sender \x00 stripped body`).
5. Routing by registry lookup on the sender; unrecognized senders land in `OTHER`.
6. If the bank's signal fires, its extractor returns a `ParseResult`. A diagnostic is collected; a
   failure increments `<ID>_SKIPPED`.
7. Txns are sorted and frozen into a `ParseReport`.
8. The command filters, aggregates over `Money`, and renders as table, JSON or CSV.

### SMS XML Structure

Root `<smses count="N">` with `<sms>` and `<mms>` children. Key `<sms>` attributes: `address`
(sender short code), `body`, `date` (Unix ms), `readable_date`. `<mms>` elements carry nested
`<parts>` / `<addrs>` children — which is why the reader tracks depth rather than counting every
element it sees.

### HBL CC Transaction SMS Format

```
Dear Customer, Your HBL CreditCard (ending with XXXX) has been charged at VENDOR for CUR-AMOUNT on DD/Mon/YYYY.
```

### Adding a New Bank Parser

1. Create `src/sms_msgs_scraper/parser/<bank>_sms_parser.py` following the existing pattern —
   static methods only, every regex pre-compiled at class level.
2. Expose exactly two things: `isTxnMsg(record) -> bool` and `extract(record) -> ParseResult`.
   Never raise, never assert, never print.
3. Build amounts with `Money.parse` and one of the shared token patterns. Do not write a new amount
   regex.
4. Keep the txn signal **looser** than the extraction regexes. If signal and extraction are
   equivalent, a changed template is silently counted as an ordinary msg from that bank instead of
   being reported and counted in `<ID>_SKIPPED`.
5. Add a `BankSpec` to `BANK_SPECS` in `domain/registry.py`, with its sender codes and capabilities.
   That is the only registration step — routing, `--bank`, the summary and the verifier all follow.
6. Add `tests/test_<bank>_sms_parser.py`, and add messages to
   `tests/fixtures/build_synthetic_backup.py`.

## Conventions

- **Python 3.14 and Windows 11 are single targets, not floors.** No cross-platform branches, no
  back-compat shims. The rationale is recorded in `pyproject.toml` next to `requires-python`.
- **All timestamps in an SMS backup are Asia/Karachi local time.** So naive datetimes parsed out of
  a backup must be **stamped** with `DEFAULT_TZ` (`.replace(tzinfo=DEFAULT_TZ)`), never
  **converted** into it (`.astimezone(DEFAULT_TZ)`). `astimezone()` on a naive value reads it as the
  *host machine's* local time and shifts it, silently moving txns across day boundaries on any
  machine not set to +05:00. `DEFAULT_TZ` is defined in `common.py` and only there.
- **Naming is camelCase**, deliberately and throughout — methods, locals and instance attributes.
  This is an explicit decision, not drift: Ruff's `N802`/`N803`/`N806` are disabled in
  `pyproject.toml` with the reason written next to them. Match it.
- **stdout is results; stderr is everything else.** Nothing prints with bare `print()`. Results go
  through `console`, everything else through `errConsole` or the `print*` helpers in `console_ui`.
- **Pass data-derived strings as `Text`** (`vendorText`, `labelText`, …), never as `str`: a vendor
  name containing brackets would otherwise be read as Rich console markup. Vendor and account fields
  are also stripped of control characters at the render boundary.
- **A new bank, currency or debit type must not need a theme entry.** `_styleFor()` falls back to
  the namespace's `.unknown` style.
- **A parse failure returns a `ParseResult` carrying a `ParseDiagnostic`** — never `None`, never an
  exception, never a printed line, and never the msg body.
- **No `assert` anywhere in `src/`.** Pinned by
  `test_adversarial_input.TestNoAssertionsOnInput`, which walks the AST of every module. An `assert`
  disappears under `python -O`, so any assertion whose truth depends on a message is a behaviour
  that changes with an interpreter flag. `tests/test_adversarial_cli.py` runs the whole parse under
  both and requires byte-identical output.
- Subcommands are registered with an **explicit name string** — `@cli.command("list_all_vendors")`.
  Click ≥8.2 derives command names by replacing underscores with dashes.
- Tests use `unittest` (not pytest) and build XML programmatically via `ET.Element`.

## Reference numbers (backup `sms-20251011130814.xml`, re-derived 2026-08-29)

Gate on these when changing parsing or routing. Msg counts are **post-dedup runtime** values — a
raw-corpus grep will always be higher and is not comparable.

| Metric | Value |
|---|---|
| envelope declared / actual / sms / mms / invalid | 4,719 / 4,719 / 4,665 / 54 / 0 |
| `ALL` / `DUP` | 4,665 / 235 |
| msgs HBL / FBL / SCB / MEZN / OTHER | 798 / 674 / 641 / 1,228 / 1,089 |
| `ccTxns` total | 1,696 |
| CC txns HBL / FBL / SCB | 717 / 583 / 396 |
| skipped HBL / FBL / SCB / MEZN | 0 / 0 / 26 / 0 |
| `debitTxns` total | 875 |
| debit card_purchase / atm_withdrawal / account_debit / funds_transfer | 8 / 361 / 96 / 410 |
| unique vendors HBL / FBL / SCB / MEZN | 180 / 166 / 96 / 189 |
| unique CC vendor union (HBL ∪ FBL ∪ SCB) | 359 |
| FBL currency split PKR / USD / CAD | 574 / 8 / 1 |
| ambiguous duplicates | 31 |

Exact totals (`Decimal`, asserted by the verifier):

| Bank / currency | Total |
|---|---|
| HBL / PKR | 4,272,661.09 |
| HBL / USD | 306.71 |
| HBL / CAD | 1,034.00 |
| FBL / PKR | 4,279,414.69 |
| FBL / USD | 603.00 |
| FBL / CAD | 2.00 |
| SCB / PKR | 2,918,984.99 |
| MEZN / PKR | 37,034,319.58 |

**Derivation of the changes from 1.1.0.** Declaring SCB's second short code `9220` moved 27 msgs
from `OTHER` to `SCB` (1,116 → 1,089 and 614 → 641) and added 18 CC txns (1,678 → 1,696; SCB
378 → 396), worth exactly PKR 94,294.32 — 81,448.32 in Sep 2025 and 12,846.00 in Oct 2025. Those 18
carry 18 unique bodies and parse with no warnings. SCB's vendor count rose 94 → 96 and the CC vendor
union 357 → 359, so 9 of the 11 vendor strings in those msgs were already known. **No other figure
moved**: `ALL`, `DUP`, HBL, FBL, MEZN and every debit count are unchanged, which is what makes the
recovery attributable rather than merely coincident with a rewrite.

The 26 SCB skips are the bank's own malformed msgs: 21 truncated mid-body and 5 carrying a literal
`PKR .00`. They are expected, not a defect.

`ambiguous duplicates` is a conservative **upper bound**: it counts every suppressed duplicate from
a bank whose alerts carry no time of day (HBL and SCB), not only those whose arrival gap makes a
genuine repeat plausible.

**If a number here stops reproducing, fix the code or re-derive the expectation from the corpus and
record the derivation.** Never quietly edit this table to match observed output.

## Known limitations (accepted, not bugs)

- **Two legitimately identical purchases can still collapse into one txn, but only for HBL and SCB.**
  FBL and Meezan bodies carry a time of day, so an identical body provably means the same txn. HBL
  and SCB bodies carry a *date only*. On the reference backup this is at most 3 msgs (2 HBL, 1 SCB),
  whose repeats arrived 1.5–6 minutes apart; every other repeat arrived within 8 seconds.

  **A received timestamp cannot fix this, and would make things worse.** Measured on the reference
  backup: the network redelivers the *same* alert as much as 2.9 hours late (two FBL retransmissions
  arrived 19 minutes and 2.9 hours after their originals, and their bodies pin the txn to the
  second), so any useful "within N minutes" window would have admitted them as second purchases and
  inflated spending. `date_sent` is no better: it differs on 138 of the 145 repeated (sender, body)
  groups. Fabricating spending is worse than the residual, so the identity stays with what the msg
  says rather than when it arrived — and the report now *counts* the ambiguous suppressions instead
  of pretending the question does not arise. `--duplicates none` gives the upper bound.
- **Meezan normalizes internal whitespace for matching, but the hash does not.** Two Meezan bodies
  differing only in internal spacing hash differently and both parse. Real duplicates are
  byte-identical retransmissions, so this has no effect in practice.
- **SCB vendors keep a glued-on city** (`SOUTH CITY HOSPITALKarachi PAK`) — the corpus offers no
  reliable separator, so no split is attempted.
- **SCB card masks:** the last 4 digits are recovered from any mask whose digits are interrupted by
  a masked section, so a 6-digit BIN works. A shape that is neither that nor an unmasked BIN-only
  run keeps its transaction and is reported, because a silent absent-card is exactly what a change
  in the bank's masking would look like.
- **An FBL vendor containing an internal double space would mis-split** to its first token. No such
  vendor exists in the corpus; the split rule is pinned by unit tests on the known shapes.
- **FBL comma-grouped amounts are refused.** FBL has never sent one, so a comma in an FBL amount is
  a template change worth reporting rather than an amount worth trusting. It lands in `FBL_SKIPPED`.
- **Meezan cheque-clearing debits are deliberately out of scope** — they risk double-counting
  against the separate "cheque received" notice.
- **`list_all_debit_vendors` does not exist.** `debitVendors` is populated and the command would be
  trivial; it was simply not asked for.
- **A tz test cannot fail on a machine already set to +05:00** — `astimezone()` is a no-op there.
  Windows offers no way to simulate another timezone in-process, so the stamping rule is enforced by
  convention, by review, and by the verifier's tz-aware invariant.
- **The synthetic corpus cannot catch a regression that only appears at scale.** That is what
  `scripts/verify_against_backup.py` and the private reference backup are for, and it is why the CI
  workflow states explicitly that the private-corpus job is a local step.
