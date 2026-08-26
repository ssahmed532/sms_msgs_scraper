# Adversarial Code Review

**Project:** `sms_msgs_scraper`  
**Reviewed revision:** `d9f757b` (`main`, version 1.1.0)  
**Review date:** 2026-08-26  
**Scope:** Production code, CLI behavior, parser correctness, financial arithmetic, XML handling, privacy, architecture, packaging, verification tooling, and tests.

## Executive assessment

This is a useful personal-data tool with unusually good documentation of the bank-message formats it has encountered. The multi-bank parsers, sender-aware deduplication, timezone stamping, Rich output, 133-test suite, and pinned real-backup verification are meaningful strengths. The code is readable enough to diagnose, and the project has already fixed several subtle errors around cross-sender deduplication, parser drift, and timezone conversion.

It is nevertheless **not reliable enough to be treated as an authoritative financial reporting tool**. The current build has two demonstrated correctness/availability defects and an unsuitable monetary representation:

1. It silently omits 18 parseable Standard Chartered transactions sent from `9220`.
2. One malformed or newly formatted HBL transaction can abort the entire command; under `python -O`, the same data can instead flow through as invalid state.
3. All amounts and totals use binary floating point. The new Rich tables hide the observed rounding noise at two decimal places, but the underlying arithmetic is still inexact and unsuitable as the domain model for money.

The broader design also makes these defects harder to discover than they should be. Parsing, diagnostics, statistics, and presentation are coupled; bank registration is duplicated; expected corpus counts can bless a stable omission; malformed XML records are not isolated; and normal CLI output is not composable. The project has a solid parser prototype and a polished human interface, but not yet a robust parsing/reporting architecture.

**Release recommendation:** do not use version 1.1.0 as the sole source for reconciliation, expense reporting, or audit totals. Fix H1-H3 and add the corresponding adversarial regressions before calling results financially trustworthy.

## Severity model

- **High:** Can omit or corrupt transaction results, make totals untrustworthy, or prevent access to otherwise valid results.
- **Medium:** Materially harms resilience, privacy, automation, diagnosability, extensibility, or the ability of tests to detect future financial errors.
- **Low:** Quality, consistency, packaging, performance, or usability debt that does not currently invalidate core results.

## What was verified

The review did not merely accept the existing tests or expected corpus counts:

- Ran the full suite with `PYTHONPATH=src`: **133 tests passed** in approximately 0.46 seconds.
- Ran `scripts/verify_against_backup.py` against the pinned reference backup: **PASS** under its current expectations.
- Compiled `src`, `tests`, and `scripts`: **PASS**.
- Ran ordinary root-level discovery without path manipulation: **7 import errors**, confirming the project is not normally importable/testable from its root.
- Independently scanned the reference XML's sender/signature combinations: `9220` has 28 messages, 18 containing `have been paid at`; all 18 parse successfully with the current SCB extractor.
- Compared float aggregation with decimal aggregation across bank/month/currency buckets: 19 of 119 buckets have representation-visible drift, while 66 of 119 differ at the exact binary-value level. Current `,.2f` table formatting masks the observed drift.
- Parsed the reference backup twice through the same `SmsBackupFileParser`: the first run reported `(ALL=4665, DUP=235, CC=1678, debit=875)` and the second `(ALL=4665, DUP=4900, CC=1678, debit=875)`, breaking the conservation invariant.
- Requested `list_all_cc_txns --help`: the tool first parsed all 4,665 SMS messages, emitted 26 warning lines and two parse-summary tables, and only then displayed help.
- Confirmed the XML root declares 4,719 elements: 4,665 SMS and 54 MMS. The CLI's `ALL` label reports only SMS.

The corpus is private and gitignored, so these measurements are reproducible only for someone who possesses the same file and matching SHA-256.

## High-priority findings

### H1. Sender `9220` causes silent omission of 18 parseable SCB transactions

**Affected code:** `src/parser/scb_sms_parser.py:14`, `src/sms_backup_file_parser.py:185-200`, `scripts/verify_against_backup.py:47-85`

`SCB_SHORT_CODES` contains only `7220`. An independent scan of the reference backup found:

- 28 messages from `9220`;
- 18 with the SCB transaction signal `have been paid at`;
- 18 unique stripped bodies, so none would be removed by the current deduplication rule;
- all 18 accepted by the existing SCB extractor without modification;
- PKR 94,294.32 omitted in total: PKR 81,448.32 in September 2025 and PKR 12,846.00 in October 2025, across 11 extracted vendor strings.

They are routed to `OTHER`, so they cannot appear in listings, vendor sets, summaries, or totals. This is silent data loss, not merely an unsupported format. README's supported-sender table repeats the same incomplete registry.

The pinned corpus verifier still passes because its expected values were derived from the application's routing result. It verifies that the omission remains stable rather than independently asking whether transaction-shaped messages escaped all parsers.

**Required correction:** add `9220`, independently regenerate counts/totals, and add explicit sender tests. More importantly, make corpus verification scan every sender for known transaction signatures and fail on unregistered signature hits unless explicitly allowlisted.

### H2. HBL treats external input as assertions and can abort the whole query

**Affected code:** `src/parser/hbl_sms_parser.py:89-128`, `src/sms_backup_file_parser.py:156-169`

The HBL parser asserts on regex-derived currency/amount and date fields, and the orchestrator asserts on every extracted field. A plausible ungrouped amount such as `PKR-25170.49` is rejected by the current amount grammar, reaches an assertion, and terminates processing of the entire backup. Invalid dates, empty vendors, and card suffix `0000` expose related paths.

This violates the application's otherwise sensible FBL/SCB/Meezan policy of warning, counting a skip, and continuing. A single external record must not deny access to thousands of valid records.

Assertions are especially dangerous because `python -O` removes them. The same malformed input then follows different control flow and can create or retain transactions containing a sentinel amount, `None` date, empty vendor, or invalid card value. Interpreter optimization must never change input-validation semantics.

The HBL class also retains an unused legacy file-parser path with more assertions, multiplying the inconsistent contract.

**Required correction:** make HBL extraction return a structured success/failure result, add `HBL_SKIPPED`, remove all input-dependent assertions and sentinel values, and prove end-to-end that one bad HBL record does not suppress valid records from any bank. Accept valid ungrouped thousands or reject them as a recoverable, visible skip.

### H3. Money is represented and aggregated with binary floating point

**Affected code:** `src/parser/hbl_sms_parser.py:49-69`, `src/parser/fbl_sms_parser.py:94-102`, `src/parser/scb_sms_parser.py:81-88`, `src/parser/mezn_sms_parser.py:162-175`, `src/sms_txn_query_tool.py:381-408`, `src/sms_txn_query_tool.py:520-539`

Every amount is converted to `float`; monthly and grand totals are sums of floats. Against the reference backup, 19 bank/month/currency buckets expose drift through normal string representation, including:

```text
268417.32000000007  instead of 268417.32
126897.30999999998  instead of 126897.31
580908.3600000001   instead of 580908.36
```

Version 1.1.0's Rich tables format values with `,.2f`, so these examples currently display as the expected two-decimal numbers. That presentation fix does not make the arithmetic exact. The in-memory API is wrong, equality and future threshold calculations remain fragile, and JSON/CSV output would either expose noise or require another rounding policy. The parsers also accept arbitrary currencies while the renderer assumes two decimal places for all of them.

**Required correction:** capture the numeric token directly as `Decimal`, never via an intermediate float; validate scale; define currency minor-unit behavior; and centralize parsing, addition, and rendering in an immutable `Money` type. Add exact aggregation tests using adversarial values such as repeated `0.01`, not only formatted-output assertions.

## Medium-priority findings

### M1. XML and record validation is incomplete, producing raw exceptions

**Affected code:** `src/sms_txn_query_tool.py:128-141`, `src/sms_backup_file_parser.py:101-105`, `src/sms_backup_file_parser.py:139-156`

The CLI translates `PermissionError` and `ElementTree.ParseError`, but many routine input failures remain raw `KeyError`, `ValueError`, `AssertionError`, `OSError`, or attribute errors:

- missing or non-integer root `count`;
- zero/negative `count`;
- wrong root tag;
- missing `address` or `body` on an SMS;
- unexpected child tags, which are treated as SMS unless the tag is exactly `mms`;
- changed HBL fields;
- a file removed or changed after Click's path check.

The parser should validate the backup envelope and isolate bad records. Fatal input errors should become concise `click.ClickException` messages with stable nonzero exits; record-level errors should normally become typed diagnostics and skips.

### M2. Parsing and presentation are eagerly coupled, and stdout has no clean data contract

**Affected code:** `src/sms_txn_query_tool.py:107-147`, `src/sms_backup_file_parser.py:232-234`, `src/console_ui.py:1-24`, `src/console_ui.py:362-374`

The group callback loads and parses the whole file before Click dispatches the subcommand. Consequently, subcommand help is expensive and noisy: the reference file produces its header, 26 SCB warnings, parse summaries, and timing before help appears.

Every line—including warnings—is intentionally written to stdout. That design makes a basic shell contract impossible:

- query data cannot be piped without decorative headers and parse summaries;
- warnings cannot be handled separately from results;
- structured JSON/CSV output cannot be cleanly introduced;
- tests and the verification script must capture global stdout to suppress private diagnostics.

The rationale that stderr would “leak” past the verifier is backwards: the verifier should capture diagnostics explicitly. Query results belong on stdout; warnings/progress belong on stderr; parser code should return diagnostics rather than render them. Parsing should occur only after argument/help resolution, with summaries behind `--verbose` and a quiet mode for automation.

### M3. `SmsBackupFileParser` is stateful, non-idempotent, and unsafe to reuse

**Affected code:** `src/sms_backup_file_parser.py:86-99`, `src/sms_backup_file_parser.py:130-139`

`parseMessages()` resets only `msgCounts["ALL"]`. It retains hashes, transaction lists, vendor sets, and every other counter. On the reference backup, the second call changes `DUP` from 235 to 4,900 while leaving `ALL` at 4,665. The summary is internally impossible.

Loading a second file on the same instance has the same contamination risk. Either make parsing one-shot and return an immutable `ParseReport`, explicitly reject reuse, or reset all state atomically. Tests must cover both repeated parse and second-file load.

### M4. Backup element counts are neither validated nor named accurately

**Affected code:** `src/sms_backup_file_parser.py:101-105`, `src/sms_backup_file_parser.py:139-146`, `src/sms_backup_file_parser.py:265-269`

The root `count` is asserted positive but never compared with actual children. `expectedMsgs` is otherwise unused. MMS is silently excluded and any non-MMS tag is assumed to be SMS. The reference file declares 4,719 elements, while `ALL` reports 4,665 SMS; “all” therefore has two incompatible meanings.

Track and validate `declared_element_count`, `actual_element_count`, `sms_count`, `mms_count`, `invalid_element_count`, and processing outcomes separately. Never use an assertion for schema validation.

### M5. Deduplication silently makes an irreversible financial decision

**Affected code:** `src/sms_backup_file_parser.py:22-77`, `src/sms_backup_file_parser.py:107-128`

Using `(sender, stripped body)` fixes the earlier cross-sender suppression bug and correctly handles known FBL/Meezan retransmissions whose bodies include a time. HBL and SCB bodies contain only a date, however. Two genuine purchases with the same card, vendor, amount, and date are indistinguishable from retransmission and are silently collapsed. Project notes estimate up to three ambiguous messages in the reference backup.

The limitation may be inherent in the source data, but the policy should be visible. Preserve duplicate provenance, report ambiguous transaction-shaped duplicates, and support an explicit policy such as `exact`, `none`, or `review`. Totals should say which policy produced them.

### M6. Parser registration and capabilities are duplicated across the repository

**Affected code:** `src/sms_backup_file_parser.py:14-17`, `src/sms_backup_file_parser.py:156-223`, `src/sms_txn_query_tool.py:153-163`, `README.md`, `scripts/verify_against_backup.py:47-85`

Adding a sender or bank requires synchronized edits to parser constants, routing branches, CLI choices, summary rows, verification dimensions, tests, and documentation. H1 is direct evidence that this arrangement fails in practice.

Define one registry containing bank ID, sender codes, transaction kind, signal detector, extractor, and capabilities. Derive routing, filter choices, supported-bank documentation, and verification dimensions from it. Fail fast on duplicate sender registrations.

### M7. Amount grammars are inconsistent and insufficiently strict

**Affected code:** `src/parser/hbl_sms_parser.py:19-23`, `src/parser/fbl_sms_parser.py:27`, `src/parser/scb_sms_parser.py:20-24`, `src/parser/mezn_sms_parser.py:18-24`

- HBL rejects ordinary ungrouped values with four or more digits.
- SCB and Meezan accept malformed grouping such as `1,2,3.00`, then erase commas and interpret it as `123.00`.
- FBL accepts arbitrary decimal scale even though output silently rounds to two places.
- SCB/Meezan digit sequences are unbounded; an extreme input can become floating-point infinity and still pass a positive-amount check.
- Currency support is effectively “any three capitals,” while the domain enum and table assumptions name only PKR/USD/CAD.

Use one full-match grammar, for example `(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{2})?`, parameterized where a bank genuinely differs. Enforce finite values, positive bounds, scale, and supported/unknown currency policy. Property-based tests are well suited to this boundary.

### M8. The domain model permits invalid and lossy state

**Affected code:** `src/cc_txn.py`, `src/debit_txn.py`, `src/common.py`

`CurrencyAmountTuple` lives in the credit-card module but is reused for debits. Amount is untyped. Banks and currencies are free strings. Bank defaults can conceal a parser that forgot to assign one. Card suffixes are integers, losing leading-zero semantics and making a legitimate `0000` indistinguishable from “not supplied.” Dataclasses are mutable. `DEFAULT_TZ` is duplicated. Parsing failures use magic values such as `-1.2345`.

Introduce shared, validated, frozen/slotted domain objects: `Money`, `Bank`, `Currency`, `TransactionKind`, `CardReference`, and transaction records. Make required fields required; model absence with `None`; model failure with a result/diagnostic type.

### M9. Parser-side rendering leaks private data and prevents controlled diagnostics

**Affected code:** `src/parser/hbl_sms_parser.py:65-66`, `src/parser/hbl_sms_parser.py:83-85`, `src/parser/hbl_sms_parser.py:123-126`, all parser warning/error helpers

Parsers print directly as a side effect. HBL's regex-miss path includes the complete SMS body, which can expose merchant, amount, card suffix, and date in captured terminal logs or pasted bug reports. Other parsers mostly identify messages by received timestamp, but still render from the parsing layer.

Return typed diagnostics containing a safe record locator and reason. Redact bodies by default; permit raw bodies only in an explicit debug mode with a privacy warning. Render diagnostics in the CLI layer to stderr.

### M10. CLI integration coverage improved, but adversarial command behavior is still untested

**Affected code:** `tests/test_cli_commands.py:259-463`

Version 1.1.0 added useful `CliRunner` coverage for all five commands, filters, empty states, plain output, `--no-color`, and directory rejection. The old characterization that CLI behavior is “largely untested” is no longer fair.

The suite still omits the failure modes most likely to damage trust:

- malformed XML, root schema/count errors, and missing SMS attributes;
- malformed HBL among valid cross-bank records;
- `--help` proving no file parse or warning output occurs;
- stdout/stderr separation and stable exit codes;
- exact monetary aggregation and rounding boundaries;
- `python -O` parity;
- `9220` routing and unknown-sender signature discovery;
- repeated parser use;
- terminal control characters and privacy-safe errors.

Several parser tests also allow real output to spill into the test run, and the HBL ungrouped-thousands test explicitly pins the known bug rather than the desired behavior.

### M11. Corpus verification is valuable but can certify stable blind spots

**Affected code:** `scripts/verify_against_backup.py`

The SHA pin, conservation check, field invariants, per-bank counts, currency counts, and output redaction are all good. They do not establish completeness. Expected counts derived after routing cannot notice a sender that routing excludes. Count-only checks also do not verify exact totals or sampled field correctness.

Add independent signature discovery across all senders, unknown-sender failure/allowlists, exact Decimal totals, skipped-reason categories, declared-vs-actual element counts, and duplicate-policy metrics. Keep corpus-derived expectations separate from parser-derived metrics. Because the corpus is private, maintain a sanitized public integration corpus for CI as well.

### M12. The project is not an installable or normally testable Python CLI

**Affected code:** `pyproject.toml:18-19`, flat `src/` imports, README development instructions

`package = false` and the absence of `[project.scripts]` mean users run a source file directly. Imports work because the script directory is injected into `sys.path`; ordinary test discovery from the repository root fails with seven `ModuleNotFoundError` import failures. There is no wheel/install smoke test.

Create `src/sms_msgs_scraper/`, use package-relative imports, expose a console entry point such as `sms-txn`, and test both the source tree and an installed wheel. A CLI tool should not require callers to know its internal directory layout.

### M13. XML processing and retained data have avoidable scaling and exposure costs

**Affected code:** `src/sms_backup_file_parser.py:93-103`, `src/sms_backup_file_parser.py:96-98`, `src/sms_backup_file_parser.py:125-126`

`ET.parse()` retains the whole XML tree, then the parser retains transaction objects, vendor sets, hashes, and original private bodies as dictionary values. The values are not needed for deduplication. This is acceptable for 4,719 elements but scales poorly and unnecessarily extends the lifetime of sensitive data.

Use `iterparse()` and clear elements, store only a digest set plus minimal duplicate metadata, and stream when the requested query permits it. Document resource expectations and consider file/element limits.

## Low-priority findings

### L1. Version and capability metadata have multiple authorities

The version is literal in `pyproject.toml` and the Click decorator, with tests policing synchronization. Supported banks/senders are duplicated even more widely. Read installed version through `importlib.metadata` and derive capabilities from the central registry proposed in M6.

### L2. The project description is materially incomplete

`pyproject.toml` describes credit-card analysis, but the tool also parses account debits, ATM withdrawals, bill payments, and funds transfers. Metadata should describe the product users actually receive.

### L3. The Python 3.14 floor is not justified by the implementation

Requiring a newly released interpreter sharply reduces portability for a small CLI. Identify the real language/API minimum and test maintained versions. If 3.14 is intentional, document the concrete dependency on it.

### L4. Naming, typing, and parser contracts are inconsistent

The code mixes camelCase and snake_case, legacy instance methods and static parsers, `xml`/`ElementTree` annotation styles, precise and missing `None` return types, and error/warning terminology for equivalent skip outcomes. Standardize on PEP 8 names, one parser protocol, and one result contract.

### L5. Dead and legacy code increases the review surface

Examples include the standalone HBL file-loading/parser implementation, `SpendingCategories`, duplicated timezone state, `msgsParsed`, `expectedMsgs` after load, XML stringify helpers, hard-coded standalone behavior in the backup parser, and TODOs for architecture already implemented elsewhere. Remove code that is not part of the supported path.

### L6. Ordering is accidental rather than an output guarantee

Transaction listings follow XML order; vendor order is explicit; monthly rows are explicitly sorted. The transaction contract should explicitly sort by transaction timestamp (with a deterministic tie-breaker) or document that backup order is preserved. This matters when files are merged or exported by another tool.

### L7. The Rich layer is polished but over-centralized for a data tool

The shared theme and escaped `Text` construction improve consistency and avoid Rich-markup interpretation of vendor names. However, parser summaries, notices, tables, and warning behavior are now tightly coupled to one global console. Renderers should consume domain/report objects, and plain/CSV/JSON renderers should be peers rather than afterthoughts.

### L8. CLI capabilities needed for verification and automation are absent

High-value additions after correctness work include JSON/CSV, currency filtering, debit-type filtering on summaries, aggregate-only output, duplicate/skipped reports, `--strict`, `--quiet`, `--verbose`, configurable timezone, supported-bank/sender inspection, and stable schemas/versioning for machine output.

### L9. No automated quality gate is visible

There is no checked-in CI, formatter/linter configuration, type checker, coverage report/threshold, security scan, or packaging test. Add Ruff, pyright or mypy, coverage with branch measurement, pre-commit, and CI across supported Python versions. Keep the private corpus job separate from a sanitized public suite.

### L10. Defensive XML and terminal hardening should be explicit

Consider `defusedxml` or document the protections expected from the supported interpreter; impose sensible size/element/depth limits; sanitize terminal control characters in SMS-derived fields; and, if CSV is added, prevent spreadsheet-formula injection. These controls are secondary to correctness but appropriate for user-controlled backup files.

## Test-suite assessment

### What the tests do well

- Cover all four bank parser families and important observed template variants.
- Exercise sender-aware deduplication and the conservation identity.
- Verify Karachi timezone stamping and inclusive date filters.
- Cover FBL/SCB/Meezan skip paths without aborting the parser.
- Invoke all five CLI commands through `CliRunner` and inspect rendered output.
- Test new Rich behavior including non-TTY plain text and `--no-color` placement.
- Pin project/lock/CLI version consistency.
- Back unit fixtures with a separate real-corpus verification script.

### What keeps the suite from proving correctness

The suite is example-heavy and happy-path-heavy at system boundaries. It proves that known templates still work but not that transaction-like messages cannot escape routing, malformed records cannot abort valid work, money is exact, parser lifecycle is coherent, or error channels are safe. The green suite and green corpus verifier coexist with H1 because neither performs independent discovery.

The next test investment should therefore be adversarial, not merely more examples of known messages:

1. Independent signal-vs-registry corpus checks.
2. End-to-end malformed-record isolation for every bank.
3. Decimal and rounding properties.
4. Schema fuzzing/property tests for XML attributes and child tags.
5. Parser lifecycle/state-transition tests.
6. CLI stdout/stderr/exit/help contract tests.
7. Sanitized corpus regression fixtures that run in CI.

## Recommended target architecture

```text
validated XML stream
    -> SmsMessage records
    -> sender/parser registry
    -> ParseResult[Transaction] + ParseDiagnostic
    -> duplicate-policy engine with provenance
    -> immutable ParseReport
    -> query/filter/aggregate service using Decimal Money
    -> human table | JSON | CSV renderer
```

Key properties of this design:

- XML reading knows nothing about Rich or banks.
- Bank parsers are pure functions over a validated `SmsMessage`.
- Every transaction-shaped failure is typed and counted.
- Registration has one source of truth.
- Deduplication is an explicit policy with auditable provenance.
- Financial aggregation is exact and separate from formatting.
- CLI rendering controls stdout, stderr, verbosity, and output format.
- A parse report can be tested, serialized, and reused without hidden mutable state.

Suggested core types are `SmsMessage`, `Money`, `Bank`, `Currency`, `TransactionKind`, `Transaction`, `ParseDiagnostic`, `ParseResult`, `DuplicateRecord`, and `ParseReport`.

## Recommended implementation order

### Phase 1: stop incorrect or unavailable results

1. Register SCB `9220`; independently re-derive counts and exact totals.
2. Replace HBL assertions/sentinels with recoverable structured failures and `HBL_SKIPPED`.
3. Replace every float with `Decimal` and define amount/currency validation.
4. Add adversarial end-to-end regressions for all three changes.

### Phase 2: make incompleteness visible

1. Add independent unknown-sender signature discovery to verification.
2. Introduce validated `SmsMessage`, structured diagnostics, and immutable `ParseReport`.
3. Validate root/element counts and isolate malformed records.
4. Make duplicate policy and ambiguous duplicate counts explicit.
5. Add strict mode that fails when transaction-like messages are skipped.

### Phase 3: create a real CLI boundary

1. Delay parsing until help and arguments are resolved.
2. Put results on stdout and diagnostics/progress on stderr.
3. Add quiet/verbose behavior and JSON/CSV renderers.
4. Package the tool with a console entry point and root-runnable tests.

### Phase 4: consolidate and harden

1. Replace bank branches with a central registry/protocol.
2. Stream XML and minimize sensitive-data retention.
3. Add property-based tests, sanitized corpus fixtures, coverage, typing, linting, and CI.
4. Remove legacy paths, duplicated constants, and obsolete TODOs.

## Final verdict

The codebase is better than its size might suggest: it contains careful corpus observations, good comments around subtle behavior, and a visibly improved CLI. Those strengths make the remaining failures more—not less—important. A financial parser's standard is not “the known examples pass”; it is “missing, malformed, duplicated, novel, or adversarial input cannot silently change money.” Version 1.1.0 does not meet that standard yet.
