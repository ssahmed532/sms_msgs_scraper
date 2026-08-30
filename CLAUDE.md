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

**Version:** 2.4.0.

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

# 3. branch coverage (threshold 90%, currently 95%)
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
#   cc_spend_for_month             - One month's CC total, all banks together
#
# Meezan account debit commands:
#   list_all_debit_txns            - List all account debit transactions
#   monthly_debit_spending_summary - Month-wise debit spending by currency
#
# Across BOTH stores at once:
#   monthly_vendor_chart           - Stacked monthly bars, CC + debit together
#
# All six of those accept:
#   --from-date YYYY-MM-DD         - only txns on or after this date (inclusive)
#   --to-date   YYYY-MM-DD         - only txns on or before this date (inclusive)
#
# cc_spend_for_month takes the month instead, and requires it:
#   --month YYYY-MM                - the month IS the range
#
# The three CC commands that take a date range also accept:
#   --bank {HBL|FBL|SCB}           - case-insensitive; default all
#
# list_all_debit_txns also accepts:
#   --txn-type {card_purchase|atm_withdrawal|account_debit|funds_transfer}
#
# ALL SEVEN accept the vendor pair:
#   --vendor TEXT                  - case-insensitive substring, matched against
#                                    the vendor as sent AND its canonical name
#   --canonical-vendors            - report vendors under their canonical names
#
# Both monthly summary commands and cc_spend_for_month accept --verbose / -v.
#
# monthly_vendor_chart also accepts:
#   --group-by {vendor|bank|txn-type|none}   - what each bar is split into;
#                                              default vendor
#
# When list_all_cc_txns or list_all_debit_txns is given a date range and/or
# --vendor, its table output also carries an "Aggregate spend" block: one
# exact total per currency for the matching txns. Currencies are never added
# together, and the JSON/CSV row shapes are unchanged -- they are a contract.
#
# GLOBAL options must be written BEFORE the filepath, because a Click group
# stops parsing its own options at the first positional argument:
#   --format {table,json,csv}      - how results are written to stdout
#   --quiet / -q                   - suppress everything on stderr
#   --strict                       - exit 3 if anything could not be parsed
#   --duplicates {exact,none,review}
#   --vendor-map PATH              - a vendor alias table replacing the shipped one
#   --no-color
```

`monthly_vendor_chart` is the only command that reads **both** stores: it matches CC txns and
Meezan debits together, because a merchant is a merchant and which store paid it is not something
you should have to know before searching. It draws **every month between the first and the last,
including the empty ones** — a chart that closes its gaps up lies about the shape of the series —
and gives **each currency its own chart**, since one bar cannot mix PKR and USD. At most four
series are named and the rest become `Other`; under `--format json` / `--format csv` it writes the
series behind the chart (`month, series, currency, amount`) with **every** series named, because
the four-series cap is a readability limit of a terminal bar and a consumer handed `Other` could
never recover what was in it.

```bash
uv run sms-txn backup.xml list_all_cc_txns --from-date 2024-01-01 --to-date 2024-12-31
uv run sms-txn backup.xml list_all_cc_txns --bank FBL
uv run sms-txn backup.xml cc_spend_for_month --month 2025-03
uv run sms-txn --format csv backup.xml list_all_debit_txns --txn-type atm_withdrawal > atm.csv
uv run sms-txn backup.xml list_all_cc_txns --vendor PSO
uv run sms-txn backup.xml monthly_cc_spending_summary --vendor PSO --canonical-vendors
uv run sms-txn backup.xml monthly_vendor_chart --vendor "KE 04000003" --from-date 2025-01-01
uv run sms-txn backup.xml monthly_vendor_chart --group-by bank
uv run sms-txn --format csv backup.xml monthly_vendor_chart --group-by txn-type
```

`python -m sms_msgs_scraper` is equivalent to `sms-txn` and works from a source checkout.

### Verifying against a real backup

```bash
uv run python scripts/verify_against_backup.py            # the reference backup, in the repo root
uv run python scripts/verify_against_backup.py other.xml
```

Run this after **any** change to a parser, to the routing, or to the dedup identity. The unit suite
proves the parsers work on hand-built msgs; this proves they still work on ~4,700 real ones.

It performs four kinds of check, and the first is the one that matters most:

- **Discovery.** It scans *every* sender for the banks' txn signatures **without consulting the
  registry's sender list**, and fails on a signature from a sender no bank claims. This check is
  expectation-independent, which is the only kind that could have caught the missing SCB sender.
  Deleting `9220` from the registry must make this fail — verified.
- **Invariants**, asserted for any backup: the conservation identity, exact envelope accounting,
  and the structural properties of every txn.
- **Expected values**, tied to one backup by its SHA-256 — counts *and* exact per-bank
  per-currency `Decimal` totals. Counts alone cannot catch an amount parsed wrongly: 1,696 txns
  summing to the wrong number is still 1,696 txns.
- **Vendor map liveness.** Every alias must claim at least one real vendor, and every canonical
  name must collapse two or more spellings. An alias matching nothing is dead config — written
  against a string the banks stopped sending, or mistyped — and it would otherwise sit there looking
  like it grouped something. Read from `vendor_aliases.local.json` in the repo root (gitignored,
  and the file `--vendor-map` should point at); asserted only when that file *and* the reference
  backup are both present, since the packaged examples match nothing anywhere by design and another
  backup may simply lack a merchant. Only alias and canonical *names* are printed, never a corpus
  vendor string.

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
  `domain/tz.py` raises `ZoneInfoNotFoundError` without it. Bump periodically — this is the tz rule
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
│       ├── data/
│       │   └── vendor_aliases.json    # EXAMPLE canonical-vendor table (no real data)
│       ├── domain/               # the core: stdlib only, imports nothing above it
│       │   ├── money.py          # Money, the amount grammar, minor units
│       │   ├── types.py          # CardReference
│       │   ├── tz.py             # DEFAULT_TZ, and nothing else
│       │   ├── message.py        # SmsRecord
│       │   ├── cc_txn.py         # CreditCardTxnDC
│       │   ├── debit_txn.py      # DebitTxnDC, DebitTxnType
│       │   ├── diagnostics.py    # SkipReason, ParseDiagnostic, ParseResult
│       │   ├── report.py         # ParseReport, EnvelopeCounts, DuplicatePolicy
│       │   ├── bank.py           # BankSpec, BankRegistry, Capability, TxnKind
│       │   ├── vendors.py        # VendorAliasMap, the canonical-name lookup
│       │   └── aggregate.py      # exact totals over Money, and txnSortKey
│       ├── parser/
│       │   ├── registry.py       # BANK_SPECS, REGISTRY — the only sender list
│       │   ├── hbl_sms_parser.py
│       │   ├── fbl_sms_parser.py
│       │   ├── scb_sms_parser.py
│       │   └── mezn_sms_parser.py
│       └── render/
│           ├── console_ui.py     # the two consoles, the theme, cell helpers
│           ├── tables.py         # Rich tables
│           ├── charts.py         # stacked monthly bar charts
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
    ├── test_vendors.py / test_vendor_filter.py
    ├── test_filtered_spend_aggregate.py
    ├── test_monthly_vendor_chart.py
    ├── test_adversarial_input.py / test_adversarial_cli.py
    ├── test_import_layering.py
    ├── test_synthetic_corpus.py
    └── test_versioning.py
```

Each bank's end-to-end backup tests live in **that bank's own** test file. That is deliberate: it
keeps parallel work on different banks off a shared file, at the cost of each test file carrying its
own copy of a small `_parseBackup` helper.

### Module Relationships

**The import graph is a strict, one-directional DAG**, and it is pinned by
`tests/test_import_layering.py`. Four layers, each importing only from itself and the ones below:

```
app     sms_txn_query_tool.py (CLI)  ·  sms_backup_file_parser.py (orchestrator)
  ↓
render  console_ui.py  ·  tables.py  ·  charts.py  ·  machine.py
  ↓
parser  registry.py (BANK_SPECS)  ·  hbl / fbl / scb / mezn _sms_parser.py
  ↓
domain  money · types · tz · message · cc_txn · debit_txn · diagnostics · report
        · bank · vendors · aggregate              ← stdlib only, imports nothing above
```

```
sms_txn_query_tool.py (CLI, rich_click)
    ├── render/console_ui.py (consoles, theme, cell helpers)
    ├── render/tables.py     (Rich tables)
    ├── render/charts.py     (stacked monthly bars)
    ├── render/machine.py    (JSON, CSV)
    ├── domain/aggregate.py  (exact totals over Money, the output order)
    ├── domain/vendors.py    (--vendor, --canonical-vendors, --vendor-map)
    ├── parser/registry.py   (--bank choices)
    └── sms_backup_file_parser.py (orchestrator)
            ├── parser/registry.py   ──→ parser/*   (signal + extract callables)
            ├── domain/bank.py       (Capability, TxnKind)
            ├── domain/message.py    (SmsRecord)
            ├── domain/diagnostics.py
            ├── domain/aggregate.py  (txnSortKey — the output order)
            └── domain/report.py     ──→ domain/cc_txn.py, domain/debit_txn.py
                                     ──→ domain/money.py

parser/*.py  ──→ domain/money.py, domain/diagnostics.py, domain/message.py,
                 domain/types.py, domain/cc_txn.py / domain/debit_txn.py, domain/tz.py

domain/money.py, domain/types.py, domain/message.py, domain/tz.py are leaves.
```

The parsing layer imports nothing from `render/`, so a parser cannot print.

**Only composition modules live at the package root.** Everything else that was ever put there
turned out to belong in a subpackage: `cc_txn`, `debit_txn` and `common` were domain values that
`domain/report.py` had to reach *up* out of its own package to import, and `console_ui` was a
renderer that both `render/` modules reached up for. `test_import_layering.py` asserts the root's
contents exactly, so the next module cannot land there by default.

**The registry is split along that same seam.** `domain/bank.py` holds the *types* — `BankSpec`,
`BankRegistry`, `Capability`, `TxnKind` — and `parser/registry.py` holds the *wiring*, because
binding a bank to its extractor means importing all four parsers. Keeping them in one file under
`domain/` made the pure core depend on the parsing layer built on top of it.

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

**`parser/registry.py`** — one `BankSpec` per bank, and the only place a sender short code appears.
Routing, `--bank` choices, summary rows and the verifier all derive from it. Registration raises
`DuplicateSenderError` if two banks claim one sender. `Capability.TXN_TIME` is what decides whether
a suppressed duplicate is ambiguous.

**`domain/vendors.py`** — `VendorAliasMap`, and the only place a vendor name is ever rewritten.

One canonical name owns two or more aliases, each an `exact` full vendor string or a `prefix` of
one. The prefix form is load-bearing rather than convenient: an SSGC bill embeds its own consumer
number and a bank-truncated name has no fixed ending, so neither can be enumerated exactly.

**`data/vendor_aliases.json` ships the mechanism, not anyone's data.** Three worked examples of the
two alias forms, every name marked `EXAMPLE`, claiming no real merchant — the file exists so the
schema documents itself, JSON having no comments. A real table is a private file passed to
`--vendor-map`, kept out of this public repository exactly as the reference backup is: a list of the
merchants, schools, hospitals and utilities someone actually pays is a map of their life.
`tests/test_vendors.py::TestThePackagedMap.test_it_carries_examples_and_nothing_else` fails if a
non-EXAMPLE entry ever lands there, so this is enforced rather than remembered.

**Nothing is inferred.** No suffix stripping, no fuzzy distance, no "looks like a city" rule — each
of those mis-attributes spending the first time a real merchant name ends in something city-shaped.
Only case and runs of internal whitespace are normalized, because neither can carry meaning. A
vendor no alias claims comes back *exactly* as the bank sent it.

Loading is strict for the same reason the registry's is: every way a map can be subtly wrong is
silent. A misspelled entry key, an alias two canonical names both claim, an entry with no aliases —
none of those raise on their own, they just group less than the file appears to say. The longest
matching prefix wins, so one entry can refine another; two canonical names claiming *the same*
alias is refused outright, because that is a question the file does not answer.

**`render/charts.py`** — the stacked monthly bar chart, and the only module that decides how many
series a reader can be shown at once.

The cap is four named series plus `Other`, and it is a *colour* limit rather than a layout
preference. A terminal background may be near-black or near-white, so a series colour has to sit in
the lightness band that works against both, and that band is narrow enough that a fifth
well-separated hue does not fit in it. The four in `console_ui` were computed rather than chosen —
worst protanope/deuteranope separation 14.6 in OKLab dE x100 against a target of 8, worst
normal-vision separation 16.2 against a floor of 15, every pair, both surfaces. Cycling a colour
back round for a fifth series would give two series one identity, which is worse than saying
`Other`.

**Colour is never the only encoding.** Each series also carries its own block glyph and a fixed
position in the stack, and the totals table under each chart repeats the exact figures. That is
what keeps a chart readable when it is piped to a file, rendered without colour, or read by a
tritanope — the one colour-vision case the narrow band cannot solve.

The series a chart names are selected **once, across every currency**, not per currency. Selecting
per currency gave the same bank a solid block in the PKR chart and a shaded one in the USD chart
directly beneath it. Selection is by amount when there is one currency and by how much of the chart
a series occupies when there is more than one, because ranking series across currencies by amount
would mean adding PKR to USD.

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
share midnight on the same day. The key is `txnSortKey` in `domain/aggregate.py`, not private to
the orchestrator, because *vendor* is one of those tie-breakers: `--canonical-vendors` rewrites
vendors and so must re-sort, or the listing silently leaves the order the tool documents.

### Bank Short Codes

Declared in `parser/registry.py` and nowhere else.

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
5. Add a `BankSpec` to `BANK_SPECS` in `parser/registry.py`, with its sender codes and capabilities.
   That is the only registration step — routing, `--bank`, the summary and the verifier all follow.
6. Add `tests/test_<bank>_sms_parser.py`, and add messages to
   `tests/fixtures/build_synthetic_backup.py`. Regenerate the fixture by running that
   file, then update the expectations in **`tests/test_synthetic_corpus.py` and nowhere
   else** — derive each number from what you added, then confirm the code agrees. No
   other test may pin a count taken from the fixture; `tests/test_adversarial_cli.py`
   reads it but deliberately asserts only that it is non-empty, because a second copy of
   the counts once turned a one-message fixture edit into failures in two files.

### Adding a canonical vendor

1. Read the spellings the banks actually sent — that is what an alias has to match, and guessing at
   it is how a dead entry gets written:
   `uv run sms-txn --quiet --format csv backup.xml list_all_vendors`
2. Add an entry to **`vendor_aliases.local.json`** in the repo root — gitignored, and what
   `--vendor-map` should point at. Use `exact` for a full vendor string and `prefix` where a bill
   number, a station number or a truncation varies. Put the grounds in `note` — JSON has no
   comments, and an entry that groups two spellings on a judgement should say so.
   **Never add a real merchant to the packaged `data/vendor_aliases.json`**; it carries examples
   only, and a test enforces that.
3. **Prefer the narrowest alias that covers the family.** A broad prefix silently claims merchants
   that merely start with the same word, and mis-attributed spending looks exactly like real
   spending. `SHELL` is a prefix of a great many things that are not the fuel brand.
   **A trailing space does not anchor a prefix on a word boundary.** `normalizeVendor` strips
   leading and trailing whitespace before an alias is stored, so `"prefix": ["KE "]` is held as
   `ke` and claims `KENTUCKY...` as readily as the electricity bill. The packaged example's note
   asserted the opposite until 2.4.0. Anchor by extending the prefix into content that is actually
   stable — the fixed opening digits of a consumer number, or the whole word.
4. **Nothing derived from a real backup gets committed** — this repository is public. That covers
   third parties (Meezan's funds-transfer payees are largely individuals) and the account holder
   equally: a school, a hospital and a utility together locate a person. All of it belongs in the
   local file.
5. Re-run `uv run python scripts/verify_against_backup.py`. It fails on a dead alias, on a canonical
   name that collapses fewer than two spellings, and on two canonical names claiming one alias.

## Conventions

- **Python 3.14 and Windows 11 are single targets, not floors.** No cross-platform branches, no
  back-compat shims. The rationale is recorded in `pyproject.toml` next to `requires-python`.
- **All timestamps in an SMS backup are Asia/Karachi local time.** So naive datetimes parsed out of
  a backup must be **stamped** with `DEFAULT_TZ` (`.replace(tzinfo=DEFAULT_TZ)`), never
  **converted** into it (`.astimezone(DEFAULT_TZ)`). `astimezone()` on a naive value reads it as the
  *host machine's* local time and shifts it, silently moving txns across day boundaries on any
  machine not set to +05:00. `DEFAULT_TZ` is defined in `domain/tz.py` and only there.
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
| `vendor_aliases.local.json` aliases / canonical names | 69 / 58 |
| raw vendor strings (CC ∪ debit) → canonical | 545 → 431 |
| unique CC vendors under `--canonical-vendors` | 271 |

The three vendor rows describe **`vendor_aliases.local.json`**, the private table, not the packaged
examples — which claim nothing and collapse nothing. That file is not in the repository, so these
three are the only reference numbers a fresh clone cannot reproduce.

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
- **A chart shows at most four named series.** Everything past the fourth becomes `Other`, chosen
  by amount within a single currency and by share of the chart across several. The exact figures
  for every series are always in the machine formats, which never fold — so the cap costs a reader
  of the terminal chart resolution, and costs a program nothing.

- **A sub-cell segment disappears from a bar.** A series worth less than one cell at the chart's
  scale rounds to zero width, so a PKR 2 charge next to a PKR 260 one has no visible segment. The
  totals table under the chart still carries it, and the bar's *own* length is never rounded away:
  a month with any spending in it is at least one cell.

- **`monthly_vendor_chart` dates a transaction by when the bank sent the alert**, which for a bill
  is when it was *paid*, not the period it covers. A December electricity bill paid on 2 January is
  a January bar. The tool has no way to know a billing period — the messages do not carry one.

- **`list_all_debit_vendors` does not exist.** `debitVendors` is populated and the command would be
  trivial; it was simply not asked for. `list_all_debit_txns --vendor X` covers the search case.

- **Canonicalization is opt-in, and that is a deliberate interface decision, not timidity.** Making
  it the default would change what `list_all_vendors` returns (359 → 271 CC vendors) and would be a
  MAJOR release under this project's own rule. `--canonical-vendors` regroups; it never changes an
  amount, a transaction count or a total, and `test_vendor_filter.py` pins that.

- **The packaged alias table groups nothing.** It ships three worked examples so the schema
  documents itself, and claims no real merchant, so out of the box `--canonical-vendors` is a no-op.
  That is deliberate: this repository is public, and a table of the merchants, schools, hospitals
  and utilities someone actually pays is a map of their life. The real table is
  `vendor_aliases.local.json`, gitignored, passed with `--vendor-map` and read automatically by the
  verifier. A merchant no table claims passes through with the string the bank sent.

- **A canonical name is matched case-insensitively but rendered exactly as written in the file.**
  Two entries whose names differ only in case are refused on load, because a lookup folds case and
  so could never tell them apart.

- **`--vendor` is a substring match, so a short needle over-matches.** `--vendor PSO` also matches a
  merchant with `PSO` anywhere in its name. This is the intended trade: the alternative — exact
  equality — cannot find `PSO SERVICE STATION 7Karachi PAK` from the word a person would type.
- **A tz test cannot fail on a machine already set to +05:00** — `astimezone()` is a no-op there.
  Windows offers no way to simulate another timezone in-process, so the stamping rule is enforced by
  convention, by review, and by the verifier's tz-aware invariant.
- **The synthetic corpus cannot catch a regression that only appears at scale.** That is what
  `scripts/verify_against_backup.py` and the private reference backup are for, and it is why the CI
  workflow states explicitly that the private-corpus job is a local step.
