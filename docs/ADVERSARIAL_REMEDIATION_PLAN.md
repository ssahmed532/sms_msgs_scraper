# Adversarial Review — Remediation & Implementation Plan

**Responds to:** [`ADVERSARIAL_CODE_REVIEW.md`](ADVERSARIAL_CODE_REVIEW.md)
**Planned against revision:** `d9f757b` (`main`, version 1.1.0)
**Plan date:** 2026-08-26
**Target release:** 2.0.0
**Visual roadmap:** <https://claude.ai/code/artifact/be5c6fca-d781-430d-a9ea-2b207abc244d>

**Status: plan only.** No code has been changed and no work item has been started. Implementation
begins at Wave 0.

## Scope

The review raises 26 findings (3 High, 13 Medium, 10 Low). This plan turns 25 of them into 19
sequenced work items across 6 waves, rejects one, and amends the framing of three. It is written to
be executed by multiple agents working concurrently, so every work item names the files it owns and
the condition that closes it.

## Verification of the review's claims

The review was not accepted on trust. Every claim below was independently re-derived against the
working tree and the reference backup before it became a work item. Counts and aggregates only — no
msg bodies, vendors or card digits, per the convention in `CLAUDE.md`.

| Claim | Method | Result |
|---|---|---|
| H1 · sender `9220` | Scanned every sender for the `have been paid at` signal, fed the hits to the live SCB extractor | **Confirmed.** 18 msgs, 18 unique stripped bodies, 18/18 parse, 0 warnings; PKR 94,294.32 omitted (81,448.32 Sep 2025 · 12,846.00 Oct 2025), 11 distinct vendor strings |
| H2 · assert abort | Synthetic HBL msg carrying an ungrouped `PKR-25170.49`, run normally and under `python -O` | **Confirmed, and worse than described.** Normally an `AssertionError` aborts the run; under `-O` it returns a txn with `amount = -1.2345`, `currency = None` |
| H3 · float drift | Aggregated all CC txns per bank/month/currency as `float` and as `Decimal`, compared `repr` | **Confirmed.** 21 of 93 buckets drift visibly; all masked by `,.2f` |
| M3 · non-idempotent | Called `parseMessages()` twice on one instance | **Confirmed.** `DUP` 235 → 4,900 with `ALL` fixed at 4,665; the conservation identity breaks |
| M4 · element counts | Read the XML envelope | **Confirmed.** Declared 4,719 = 4,665 `<sms>` + 54 `<mms>`; `ALL` reports 4,665 |
| M9 · body in output | Read the HBL regex-miss path | **Confirmed.** Prints the complete msg body on failure |
| M12 · not importable | `unittest discover` from `src/`, then from the repo root | **Confirmed.** 133 pass from `src/`; 7 import errors from root |
| L2 / L9 · metadata, gates | Read `pyproject.toml`; looked for `.github/` and lint config | **Confirmed.** Description is CC-only; no CI, no Ruff, no type checker, no coverage |

Two figures deserve a note so they are not mistaken for regression targets:

- **H2 understates the risk.** A `-1.2345` amount is not merely invalid state — it is *negative*.
  Summed into a monthly total it silently **reduces** reported spending, which is the failure mode
  that gets noticed last. The `python -O` parity test is therefore a release gate, not a
  nice-to-have.
- **H3's bucket count differs from the review's.** The review reports 19 of 119; measuring CC txns
  alone across bank/month/currency gives 21 of 93. Different bucket set — theirs includes debits.
  The phenomenon and the conclusion are identical.

## Why the plan is shaped like this

The review is organised by **finding**. A plan meant for concurrent execution cannot be: H3 alone
touches four parsers and the CLI, M6 rewrites routing, and M8 rewrites every shared type. Agents
assigned per finding collide on the same files continuously.

This plan is organised by **file ownership**. Each agent owns a disjoint set of files for the
duration of its work item, which is what makes the parallelism real rather than nominal. Three
consequences follow, and they are the load-bearing part of the sequencing:

1. **The shared type surface is frozen first, alone.** `Money`, the bank registry and the
   diagnostic types define each other, and every later wave imports them. Landing them concurrently
   would mean rewriting each other's signatures. Wave 0 is therefore serial and purely *additive* —
   no existing file is modified, so it cannot break the 133 tests that currently pass.
2. **Parsers partition perfectly.** Four banks, four files, four agents, zero overlap. Each does its
   own de-assertion, `Decimal` capture, amount grammar and diagnostics in a single pass.
3. **The packaging move must run alone.** Turning `src/` into a real package is a repo-wide
   `git mv` plus an import rewrite. Concurrent with anything else, every other agent's branch
   conflicts. It gets its own wave, late, once the files it moves have stopped changing.

## Wave and lane overview

Rows are agent lanes; a wave ends when every lane in it is done, because the next wave imports the
previous wave's contracts.

| Lane | Wave 0 | Wave 1 | Wave 2 | Wave 3 | Wave 4 | Wave 5 |
|---|---|---|---|---|---|---|
| **A1** | WI-00 · 01 · 02 *(solo barrier)* | WI-10 HBL | WI-20 → WI-23 orchestrator | WI-30 verifier | WI-33 *(solo barrier)* | WI-40 CI |
| **A2** | — | WI-11 FBL | WI-21 CLI boundary | WI-31 adversarial tests | — | WI-41 cleanup |
| **A3** | — | WI-12 SCB | WI-22 renderers | WI-32 sanitized corpus | — | WI-42 hardening |
| **A4** | — | WI-13 Meezan | *idle* | *idle* | — | WI-43 docs + release |

Peak concurrency 4. Two hard barriers: Wave 0 and WI-33.

## Wave 0 — Contract Freeze

*1 agent · 3 items · blocks everything · additive only*

### WI-00 — An immutable `Money` type
**Owns:** `src/domain/money.py`, `src/domain/types.py`, `tests/test_money.py`
**Closes:** H3 (type), M8

- `Decimal` captured straight from the matched token — never through an intermediate `float`.
- Frozen and slotted; addition permitted only within a single currency.
- Currency minor-unit table, with scale validated on construction.
- Retires the `-1.2345` sentinel in favour of a result type.

**Exit criteria:** summing 100 × `0.01` gives exactly `1.00`; an unknown three-letter currency is
handled by declared policy rather than a crash.

### WI-01 — One bank registry
**Owns:** `src/domain/registry.py`, `tests/test_registry.py`
**Closes:** M6, L1 (capability half)

- One `BankSpec` per bank: id, sender codes, txn kind, signal predicate, extractor, capabilities.
- Fails fast on a duplicate sender registration.
- **Declares SCB `9220` here.** The durable fix for H1 is that senders live in exactly one place.
- Declared but not yet consumed; routing switches over in WI-20.

**Exit criteria:** routing, `--bank` choices, summary rows, documentation and verifier dimensions
are all derivable from this one object.

### WI-02 — Typed diagnostics and an immutable report
**Owns:** `src/domain/diagnostics.py`, `src/domain/report.py`
**Closes:** M8, M9 (contract half)

- `ParseDiagnostic` carries a safe locator — sender, `readable_date`, reason code — and **never** a
  body.
- `SkipReason` enum, so skips are categorised rather than merely counted.
- `ParseResult`, plus a frozen `ParseReport` holding counts, txns, diagnostics and duplicate
  provenance.

**Exit criteria:** a parser can report any failure without printing, and a report can be serialised
and read back.

## Wave 1 — Parser Remediation

*4 agents · one file each · fully parallel*

### WI-10 — HBL: the only bank that still asserts
**Owns:** `src/parser/hbl_sms_parser.py`, `tests/test_hbl_sms_parser.py`
**Closes:** H2, H3 (HBL), M7 (HBL), M9 (HBL), L5 (HBL legacy)

- Remove **every** input-dependent assertion; return `ParseResult` and add `HBL_SKIPPED`.
- Drop the `-1.2345`, `-9999` and `-1` sentinels.
- Accept ungrouped thousands (`PKR-25170.49`) under the shared grammar.
- Stop printing msg bodies; emit a diagnostic instead.
- Delete the legacy instance-method file loader and its `__init__` state; pre-compile the amount
  regex.

**Exit criteria:** one malformed HBL record produces one diagnostic and suppresses nothing, and the
run behaves identically under `python -O`.

### WI-11 — FBL: exact amounts, bounded scale
**Owns:** `src/parser/fbl_sms_parser.py`, `tests/test_fbl_sms_parser.py`
**Closes:** H3 (FBL), M7 (FBL), M9 (FBL)

- `Decimal` capture; reject arbitrary decimal scale rather than silently rounding it away at render
  time.
- Adopt the shared full-match grammar, parameterised for FBL's separator-free amounts.
- Typed diagnostics in place of printed warnings.
- Preserve the unescape-before-slice invariant on the 22-char vendor column.

**Exit criteria:** every existing FBL template variant still parses and the PKR/USD/CAD split is
unchanged.

### WI-12 — SCB: the 18 missing transactions
**Owns:** `src/parser/scb_sms_parser.py`, `tests/test_scb_sms_parser.py`
**Closes:** H1 (parser half), H3 (SCB), M7 (SCB), M9 (SCB)

- Consume the registry's `9220` declaration; add explicit per-sender tests.
- Reject malformed grouping such as `1,2,3.00` instead of erasing the commas.
- Bound digit length, so no input can reach floating-point infinity.
- Card masks become a `CardReference` that keeps `0000` distinct from absent.
- Keep the deliberately loose signal check, so the 26 known-malformed msgs still land in
  `SCB_SKIPPED`.

**Exit criteria:** both `7220` and `9220` route to SCB, and the recovered total is exactly
PKR 94,294.32.

### WI-13 — Meezan: grammar and exactness
**Owns:** `src/parser/mezn_sms_parser.py`, `tests/test_mezn_sms_parser.py`
**Closes:** H3 (MEZN), M7 (MEZN), M9 (MEZN)

- `Decimal` capture across all five template families.
- Reject malformed grouping; bound digit length.
- Typed diagnostics, keeping the signal deliberately looser than extraction so template drift stays
  visible.

**Exit criteria:** all four `DebitTxnType` counts hold, and a drifted template still lands in
`MEZN_SKIPPED` with a reason code.

## Wave 2 — Core & Interface

*3 agents · parallel on a documented seam*

The orchestrator, the CLI and the renderers each own one file or one new package. They meet at two
seams — the shape of `ParseReport` and the renderer interface — both fixed in Wave 0, which is what
lets them run concurrently instead of in a chain.

### WI-20 — Orchestrator: one-shot and immutable
**Owns:** `src/sms_backup_file_parser.py`, `tests/test_sms_backup_file_parser.py`
**Closes:** M1 (record half), M3, M4, M5, M6 (consume), M13

- Parsing becomes one-shot, returning a frozen `ParseReport`; reuse is rejected outright — this is
  what kills `DUP` 235 → 4,900.
- Registry-driven routing replaces the `elif` chain.
- Envelope validation for root tag and `count`, raising real errors rather than asserting.
- Track `declared`, `actual`, `sms`, `mms` and `invalid` counts separately, so "all" has one
  meaning.
- Per-record isolation: a bad element becomes a diagnostic, not an exception.
- Dedup stores digests only — no msg bodies retained.
- Explicit duplicate policy (`exact` · `none` · `review`) with provenance and an
  ambiguous-duplicate count.

**Exit criteria:** conservation holds after a second parse attempt *and* after loading a second
file; totals declare which duplicate policy produced them.

### WI-21 — A real CLI boundary
**Owns:** `src/sms_txn_query_tool.py`, `tests/test_cli_commands.py`
**Closes:** M1 (fatal half), M2, H3 (aggregation), L8

- Defer parsing until after help and argument resolution — `--help` stops parsing 4,665 msgs first.
- Results to stdout; diagnostics, progress and summaries to stderr.
- `--quiet`, `--verbose`, `--strict`, and stable nonzero exits via `ClickException`.
- Aggregation over `Money`, so totals are exact before formatting.

**Exit criteria:** `<command> --help` emits zero warnings and parses nothing; piping stdout yields
data with no decoration.

**Sequencing constraint:** must land atomically with the capture changes in WI-30 and WI-31 — see
the M2 amendment below.

### WI-22 — Renderers as peers
**Owns:** new `src/render/`, `src/console_ui.py`
**Closes:** M2 (render half), L7, L8, L10 (CSV/terminal half)

- Table, JSON and CSV renderers all consume report objects — none of them an afterthought.
- Schema-versioned machine output, so a script can pin a shape.
- CSV formula-injection guard; terminal control characters sanitised in SMS-derived fields.
- Rich stays a renderer, not a dependency of the parsing layer.

**Exit criteria:** one report renders as a table, as JSON and as CSV with identical amounts in all
three.

### WI-23 — Deterministic output order
**Owns:** ordering helper + a test in `tests/test_sms_backup_file_parser.py`
**Closes:** L6 · *runs after WI-20 in lane A1*

- Sort txn listings by timestamp with a documented tie-breaker, instead of inheriting XML order by
  accident.
- Make it a stated contract, so merged or exported files stay comparable.

**Exit criteria:** two runs over one corpus produce byte-identical ordering, and the contract is
written down.

## Wave 3 — Proof

*3 agents · parallel*

The green suite and the green corpus verifier coexisted with H1 for a reason: neither performed
independent discovery. This wave fixes the instruments rather than the code.

### WI-30 — A verifier that can find what routing missed
**Owns:** `scripts/verify_against_backup.py`
**Closes:** H1 (durable guard), M11

- Scan **every** sender for known txn signatures, independent of the registry.
- **Fail** on a signature hit from an unregistered sender unless explicitly allowlisted.
- Assert exact `Decimal` totals, not just counts.
- Check declared-vs-actual element counts, skip-reason categories and duplicate-policy metrics.
- Keep corpus-derived expectations separate from parser-derived metrics.
- Re-derive the whole reference-numbers table and record the derivation.

**Exit criteria:** deleting `9220` from the registry makes the verifier **fail**. Today it passes,
which is exactly how a stable omission got certified.

### WI-31 — Adversarial regressions
**Owns:** `tests/test_adversarial_*.py`, `tests/test_money_properties.py`
**Closes:** M10, plus the regression half of H1/H2/H3

- Malformed XML, wrong root tag, bad `count`, missing `address`/`body`, unexpected child tags.
- One malformed HBL record among valid records from all four banks — nothing else is lost.
- `--help` proves no parse and no warning output.
- stdout/stderr separation and stable exit codes.
- `Decimal` property tests: repeated `0.01`, scale boundaries, rounding edges.
- `python -O` parity — the run must behave identically with assertions stripped.
- Repeated parse and second-file load; terminal control characters; privacy-safe error text.
- **Un-pin `test_extractCurrencyAndAmount_ungrouped_thousands`** — it currently asserts the bug;
  make it assert the fix.

**Exit criteria:** each of H1, H2 and H3 has a test that fails on `d9f757b` and passes after its
work item.

### WI-32 — A corpus CI can actually run
**Owns:** `tests/fixtures/`, `tests/test_synthetic_corpus.py`
**Closes:** M11 (CI half)

- Synthetic, sanitised backup covering every template family and every skip path.
- Zero real personal data, so it can be committed and run in CI.
- Keeps the private-corpus job strictly separate from the public suite.

**Exit criteria:** the full verifier runs in CI against the synthetic corpus with no access to the
private backup.

## Wave 4 — Repackage

*1 agent · exclusive · no concurrent work*

### WI-33 — An installable CLI
**Owns:** the whole tree — every module, every test, `pyproject.toml`
**Closes:** M12, L1 (version half)

- `src/sms_msgs_scraper/` with package-relative imports, replacing the `sys.path` accident.
- `[project.scripts]` console entry point — `sms-txn`.
- Test discovery works from the repository root, closing the 7 import errors.
- Wheel build and install smoke test.
- Version read through `importlib.metadata`, retiring one of the three hand-synced copies.

**Exit criteria:** `unittest discover` passes from the repo root, and the installed `sms-txn`
answers `--version` from wheel metadata.

**Why it is a barrier:** this rewrites the import line of every module and test in the repository.
Run beside anything else and every other agent's branch conflicts.

## Wave 5 — Harden & Release

*4 agents · parallel*

### WI-40 — A visible quality gate
**Owns:** `.github/workflows/`, `.pre-commit-config.yaml`, tool sections of `pyproject.toml`
**Closes:** L9

- GitHub Actions on `windows-latest` / Python 3.14 only — matching the project's declared single
  target. An ubuntu leg is worse than no CI here.
- Ruff, pyright, branch coverage with a threshold, pre-commit.
- `uv sync --frozen`, so the lockfile is enforced rather than trusted.
- Private-corpus job kept separate from the sanitised public suite.

**Exit criteria:** a pull request cannot merge through a failing typecheck, lint or coverage drop.

### WI-41 — One naming and contract convention
**Owns:** domain modules + residual legacy paths
**Closes:** L4, L5 (remainder)

- Settle camelCase vs PEP 8 as an explicit, documented decision, then apply it once.
- One parser protocol, one result contract, one annotation style.
- Remove `SpendingCategories`, the duplicated `DEFAULT_TZ`, `msgsParsed`, the XML stringify
  helpers, the hardcoded `__main__` paths and obsolete TODOs.

**Exit criteria:** Ruff passes with no per-file ignores, and no module has two ways of saying the
same thing.

### WI-42 — Hardening the untrusted edge
**Owns:** XML load path, field sanitisers
**Closes:** L10

- `defusedxml`, or a written rationale for relying on the interpreter's own protections.
- Size, element-count and depth limits on the backup file.
- Control-character sanitisation at the field boundary, not at the renderer.

**Exit criteria:** a hostile backup file is rejected with a clear message rather than exhausting
memory or corrupting the terminal.

### WI-43 — Docs and the 2.0.0 release
**Owns:** `README.md`, `CLAUDE.md`, `pyproject.toml`, `uv.lock`, `docs/`
**Closes:** H1 (documentation half), L1, L2

- README and `CLAUDE.md`: short-code table, conventions, the new output contract.
- Reference-numbers table replaced with WI-30's re-derived figures, including the recovered SCB
  txns.
- `pyproject` description finally mentions account debits, ATM withdrawals, bill payments and
  transfers.
- Document the Python 3.14 rationale (see the L3 rejection below).
- Version bumped in all three places with `uv lock` re-run — the one that always gets forgotten.

**Exit criteria:** both version-sync tests pass, and no document still lists `7220` as SCB's only
sender.

## Departures from the review

Twenty-five of the twenty-six findings become work. One does not, and three need their framing
corrected before anyone builds against them.

### Rejected — L3: the Python 3.14 floor
The review reads `requires-python = ">=3.14"` as accidental portability damage and asks for a lower
floor tested across maintained versions. This project has explicitly chosen Windows 11 and Python
3.14 as **single targets rather than floors**, and explicitly refuses cross-platform branches and
back-compat shims. Acting on L3 would add exactly the code the project has declined to carry.

**Action:** document the rationale in `pyproject.toml` under WI-43. Do not widen support.

### Amended — M2: the stderr split breaks two things on the way
Routing diagnostics to stderr is the correct call and the review's argument is sound. But
all-output-to-stdout is currently load-bearing in two places: `scripts/verify_against_backup.py`
swallows parser warnings with `redirect_stdout` because they can identify a msg, and
`tests/test_scb_sms_parser.py` asserts on that same stream. Move the channel without moving those
and both go green while genuinely broken.

**Action:** WI-21 lands atomically with the capture changes in WI-30 and WI-31. This is also the
reason the program is a MAJOR release.

### Amended — M5: keep the deduplication key, make the policy visible
The review is right that collapsing two genuine same-day purchases is an invisible financial
decision. It should not be read as a case for changing the key. This project already measured the
alternatives: the network redelivered one identical alert 2.9 hours late, so any "within N minutes"
window would have admitted provable retransmissions as second purchases and *inflated* spending,
and `date_sent` differs on 138 of 145 repeated `(sender, body)` groups, which would disable
deduplication almost entirely.

**Action:** WI-20 adds provenance, an ambiguous-duplicate count and an explicit policy flag. The
default identity does not change.

### Raised — H2: the `-O` path is worse than "invalid state"
See the verification table. A negative amount does not merely look wrong in a listing; summed into
a monthly total it silently reduces reported spending.

**Action:** WI-31's `python -O` parity test is a release gate.

## Findings disposition

| ID | Sev | Finding | Work items | Status |
|---|---|---|---|---|
| H1 | High | Sender `9220` silently omits 18 SCB txns | WI-01 · 12 · 30 · 43 | Fix |
| H2 | High | HBL asserts on external input; aborts the run, diverges under `-O` | WI-10 · 31 | Fix — severity raised |
| H3 | High | Money represented and aggregated as binary float | WI-00 · 10 · 11 · 12 · 13 · 21 | Fix — counts amended |
| M1 | Med | Incomplete XML and record validation; raw exceptions | WI-20 · 21 | Fix |
| M2 | Med | Eager parsing; no stdout data contract | WI-21 · 22 | Fix — sequencing amended |
| M3 | Med | Parser is stateful, non-idempotent, unsafe to reuse | WI-20 | Fix |
| M4 | Med | Element counts neither validated nor accurately named | WI-20 | Fix |
| M5 | Med | Deduplication makes an invisible financial decision | WI-20 | Fix — scope amended |
| M6 | Med | Parser registration duplicated across the repository | WI-01 · 20 · 21 | Fix |
| M7 | Med | Amount grammars inconsistent and too permissive | WI-10 · 11 · 12 · 13 | Fix |
| M8 | Med | Domain model permits invalid and lossy state | WI-00 · 02 | Fix |
| M9 | Med | Parser-side rendering leaks msg bodies | WI-02 · 10–13 · 22 | Fix |
| M10 | Med | Adversarial CLI behaviour untested | WI-31 | Fix |
| M11 | Med | Corpus verification can certify a stable blind spot | WI-30 · 32 | Fix |
| M12 | Med | Not an installable or root-testable Python CLI | WI-33 | Fix |
| M13 | Med | Whole-tree retention extends sensitive-data lifetime | WI-20 | Fix |
| L1 | Low | Version and capability metadata have several authorities | WI-01 · 33 · 43 | Fix |
| L2 | Low | Project description omits half the product | WI-43 | Fix |
| L3 | Low | Python 3.14 floor said to be unjustified | — | **Reject** |
| L4 | Low | Naming, typing and parser contracts inconsistent | WI-41 | Fix |
| L5 | Low | Dead and legacy code enlarges the review surface | WI-10 · 41 | Fix |
| L6 | Low | Output ordering is accidental, not guaranteed | WI-23 | Fix |
| L7 | Low | Rich layer over-centralised for a data tool | WI-22 | Fix |
| L8 | Low | Automation-facing CLI capabilities absent | WI-21 · 22 | Fix |
| L9 | Low | No automated quality gate | WI-40 | Fix |
| L10 | Low | XML and terminal hardening not explicit | WI-22 · 42 | Fix |

## Target architecture

The shape the waves converge on. Each arrow is a boundary an agent can own without knowing what
sits on the far side of it.

```text
validated XML stream              WI-20 · WI-42
  -> SmsMessage records           WI-02
  -> sender / parser registry     WI-01
  -> ParseResult + ParseDiagnostic    WI-02 · WI-10..13
  -> duplicate policy w/ provenance   WI-20
  -> immutable ParseReport        WI-02 · WI-20
  -> query & aggregate over Money WI-00 · WI-21
  -> table | JSON | CSV renderer  WI-22
```

## Release framing

By this project's own rule — judge the bump by what it does to someone already using the tool — the
completed program is **2.0.0**, a MAJOR release. Not because the diff is large, but because three
things change meaning for an existing caller:

- results move to stdout while diagnostics move to stderr (WI-21);
- the tool gains an `sms-txn` entry point instead of a script path (WI-33);
- the recovered SCB txns change what every SCB total returns (WI-12).

If Wave 1 has to ship on its own before the rest is ready, it stands as **1.1.1** — three parser
corrections and a recovered sender, with no interface change. Waves 2 and 3 are where the MAJOR is
incurred.

All three version locations move together and `uv lock` is re-run. The lockfile is the one nothing
prompts you to update, which is why a test already pins it.
