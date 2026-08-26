# Implementation Plan: Faysal Bank CC, Standard Chartered CC, and Meezan Bank Debit Parsers (Multi-Agent)

> ## ⚠ ARCHIVED — this plan was executed and shipped
>
> **Archived 2026-08-26.** It was built as specified and released as **v1.0.0** (merged in `2bd9f87`):
> all four banks parse, FBL/SCB/HBL credit cards share `ccTxns`, Meezan debits live in `debitTxns`,
> and the suite stands at 120 tests. Kept as the record of how that work was planned and validated —
> **not as a backlog.** Nothing here is still to do.
>
> The "not yet executed" status line below is the 2026-08-23 state, left as written.
>
> **For the code as it stands, read these instead:** `CLAUDE.md` for the architecture, the message
> formats, the reference transaction counts and the accepted limitations; `src/IMPROVEMENTS.md` for
> the open defects; `README.md` for usage.
>
> **Re-derived expectation counts no longer go here.** This doc holds the original 2026-08-26
> derivations, and `scripts/verify_against_backup.py` used to point at it. A new derivation belongs in
> CLAUDE.md's **Reference numbers** table, which is the live table the harness gates against.

> Status: **approved design, not yet executed** (planned 2026-08-23, validated against `sms-20251011130814.xml`;
> **re-audited and amended 2026-08-26** — every regex and count re-validated against the same backup, expected
> numbers corrected to post-dedup runtime values, the Phase A/B sequencing flaw fixed, the MEZN txn-signal made
> drift-proof, and the verification gates hardened with a stage-parameterized harness).
> Execution model (per user request): **multi-agent** — a main orchestrating agent coordinates; three builder
> sub-agents implement one bank each in parallel, in isolated git worktrees on separate branches, delivering via
> PRs. See "Multi-agent execution" at the end — the design/format/test sections below are the shared spec every
> builder agent receives.

## Context

The tool currently parses only HBL credit-card transaction SMS messages; Faysal Bank (FBL), Standard Chartered (SCB), and Meezan Bank (MEZN) messages are counted but never parsed. This plan adds full parsers + processors for all three, following the proven `HBLSmsParser` static-method pattern, so their transactions flow into the CLI's listing/summary commands.

Every message format below was **validated against the real backup `sms-20251011130814.xml`** and **re-validated 2026-08-26** during the pre-execution audit (corpus: 4,665 SMS + 54 MMS; 3,423 msgs from the four banks' senders). Raw-corpus match rates with the draft regexes: FBL 624/624, SCB 379/405 (26 unusable by design — see below), MEZN 879/879 debit txns. **Runtime numbers are lower — the parser dedups before parsing.** All gates in this plan use the post-dedup column of the ground-truth table below; the raw figures in the format sections are regex-validation evidence only and are never observed at runtime.

### User decisions (confirmed)
1. **Meezan scope = ALL account debits**: card purchases, ATM withdrawals, `is debited as` bill payments, and `sent to` funds transfers (879 raw / 875 post-dedup txns). Cheque-clearing debits (`Inward Clearing VIA CHEQUE`) are OUT of scope (risk of double-counting with the "cheque received" notice; cheque ≠ card/digital txn).
2. **CLI shape**: FBL + SCB CC txns merge into the existing three CC commands, each txn tagged with its bank, with a new `--bank` filter. Meezan debit txns get separate commands.
3. **SCB unusable msgs** (21 truncated mid-body + 5 literal `PKR .00` foreign-currency msgs): skip + log one warning line each + track a per-bank skipped count shown in the parse summary.

## Post-review amendment (2026-08-26): dedup identity gained the sender — msg counts re-derived

Review of PR-0 raised that global **body-only** dedup, applied *before* routing, can discard valid
msgs: an earlier msg from any sender suppresses a later bank msg that merely repeats its text. It
can, and it did — measured on `sms-20251011130814.xml`, **23 msgs** were being suppressed
cross-sender, **4 of them from bank short codes** (1 each HBL/FBL/SCB/MEZN). The plan's original
footnote described this as costing SCB exactly one msg; that undercounted it by a factor of 23.

**Fix:** the dedup identity is now `(sender short code, stripped body)` rather than the body alone.
Dedup still runs before routing, which is now harmless: the sender is part of the key, so a msg can
only be suppressed by an earlier msg from the same short code.

**Re-derived msg counts** (this is the recorded derivation the Anti-drift rule requires; the harness
and `CLAUDE.md` were updated to match, and nothing was adjusted to fit observed output):

| Bucket | body-only | (sender, body) |
|---|---|---|
| HBL | 797 | **798** |
| FBL | 673 | **674** |
| SCB | 613 | **614** |
| MEZN | 1,227 | **1,228** |
| OTHER | 1,097 | **1,116** |
| DUP | 258 | **235** |
| ALL | 4,665 | 4,665 |

Conservation still holds (798 + 674 + 614 + 1,228 + 1,116 + 235 = 4,665). **Every txn figure is
unchanged** — 1,678 CC txns (717/583/378), 875 debits (8/361/96/410), vendors 180/166/94/189, CC
union 357, FBL split 574/8/1, skipped 0/26/0 — because all 23 recovered msgs are non-txn msgs. SCB
returning to 614 also retires the plan's footnote ¹: that discrepancy *was* this bug, not an
inherent consequence of hashing.

**A received timestamp was deliberately NOT added to the identity**, though the review suggested it.
Measured, both candidates fail as a retransmission/distinct-txn discriminator and would fabricate
spending:
- `date` (received): the network redelivers the same alert as much as **2.9 hours** late. Two FBL
  retransmissions arrived 19 minutes and 2.9 hours after their originals — and FBL bodies carry the
  txn time **to the second**, so those are provably the same txn. Any "within N minutes" window
  short enough to be meaningful admits them as second purchases.
- `date_sent`: differs on **138 of the 145** repeated (sender, body) groups, including those same
  provably-identical FBL txns. In the identity it would disable dedup almost entirely.

**Residual, documented rather than guessed at:** FBL and Meezan bodies carry a time of day, so dedup
is exact for them. HBL and SCB bodies carry a date only, so a genuine second identical purchase on
the same day is indistinguishable from a retransmission — at most **3 msgs** on this corpus (2 HBL,
1 SCB, repeats 1.5–6 minutes apart; all other repeats are within 8 seconds). That is a limit of what
the SMS says, not of the dedup rule.

Two smaller review items fixed alongside: `pyproject.toml` was still at 0.2.0 while the CLI reported
0.3.0 (now pinned equal by a test that reads both), and SCB's last-4 extraction now handles any mask
with an interrupted digit run — a 6-digit BIN was accepted as a txn but recorded with card 0 — and
warns on a shape matching neither known form.

## Ground-truth numbers (re-validated 2026-08-26) — GATE ON THE POST-DEDUP COLUMN

The original draft gated builders on raw-corpus counts, but the runtime dedups first, so those numbers can never
be observed (the original exact "379 SCB txns" gate was strictly impossible — one SCB txn body is a
retransmission). Duplicate bodies per sender group: HBL 9, FBL 52, SCB 23, MEZN 29, OTHER 145 → `DUP` 258.

| Metric | Raw corpus | Runtime (post-dedup) |
|---|---|---|
| ALL (`<sms>` elements; dups still count; 54 `<mms>` skipped) | 4,665 | 4,665 |
| HBL msgs | 806 | 797 |
| FBL msgs | 725 | 673 |
| SCB msgs | 636 | 613 ¹ |
| MEZN msgs | 1,256 | 1,227 |
| OTHER msgs | 1,242 | 1,097 |
| DUP | — | 258 |
| HBL CC txns | 717 | 717 |
| FBL CC txns (FBL_SKIPPED) | 624 (0) | 583 (0) |
| SCB CC txns (SCB_SKIPPED) | 379 (26) | 378 (26) |
| MEZN debit txns (MEZN_SKIPPED) | 879 (0) | 875 (0) |
| — card_purchase | 8 | 8 |
| — atm_withdrawal | 363 | 361 |
| — account_debit | 97 | 96 |
| — funds_transfer (incl. uppercase family: 370+41 / 369+41) | 411 | 410 |
| Combined `ccTxns` (HBL+FBL+SCB) | — | 1,678 |
| Unique HBL vendors | — | 180 |
| Unique FBL vendors ² | — | 166 |
| Unique SCB vendors ² | — | 94 |
| Unique MEZN debit vendors ² | — | 189 |
| Combined CC vendor union (HBL ∪ FBL ∪ SCB) ² | — | 357 |
| FBL currency split PKR / USD / CAD | 615 / 8 / 1 | 574 / 8 / 1 |

¹ One SCB body is a byte-identical copy of an earlier non-bank msg. Hashing is body-only and therefore
cross-sender, so global dedup-first counts it as `DUP`; current `main` reports SCB 614 only because it never
hashes OTHER-sender msgs. Expected consequence of the pre-existing body-only-hash limitation, not a mis-count.

² Vendor-extraction tripwires, measured 2026-08-26 with the exact rules in the format sections (FBL:
`parts[0].strip()` or `rest[:22].strip()`; SCB/MEZN: the stripped regex capture). Their purpose: a build can
hit every *txn-count* gate while extracting garbage vendors (all-empty strings would pass the counts) — these
counts catch that. If the extraction rule is ever deliberately changed, re-derive them per the Anti-drift rule.
Also measured corpus-wide: **zero empty vendor strings and zero non-positive amounts** — the harness asserts
both as invariants on every txn.

**Conservation identity (must hold for any backup):** `ALL == HBL + FBL + SCB + MEZN + OTHER + DUP`
(real backup: 797 + 673 + 613 + 1,227 + 1,097 + 258 = 4,665). Pinned by a unit test and asserted by the
verification harness on every real-backup run.

**Baseline captured from current `main` (2026-08-26):** summary HBL 797 / FBL 673 / SCB 614 / MEZN 1227 /
OTHER 1354 / ALL 4665; `Found 717 HBL CC transactions`; `Found 180 unique Vendors`. This is the regression
reference: HBL txn count, vendor count, and monthly totals must be reproducible from the finished build via
`--bank HBL`.

## Validated message formats (ground truth from the real backup)

### Faysal Bank — sender `8756`, 624 txn msgs raw (583 post-dedup)
```
Dear {NAME}, your FBL Card  has been charged for {CUR} {amt} on {date} at {vendor:22col} {city:14col} {CC}.[ Available Limit: {x}]
```
- `Dear SADAF SIDDIQUI, your FBL Card  has been charged for PKR 25170.49 on 20-Sep-23 01:17:16 PM at IMTIAZ SUPER MARKET    KARACHI        PK.`
- Whitespace wobble: `your FBL Card  has` vs `your  FBL Card has` (match with `your\s+FBL Card\s+has`).
- Amount has **NO thousands separators** (`8100`, `18298.9`) — the HBL amount regex (which *requires* comma grouping) cannot be reused.
- **Two date formats**: `%d-%b-%y %I:%M:%S %p` (`20-Sep-23 01:17:16 PM`) and `%d-%m-%Y %H:%M:%S` (`04-02-2024 21:11:41`). Try both.
- Currencies seen: PKR (615), USD (8), CAD (1) — all already in the `Currency` enum.
- **No card digits anywhere in the msg** → `ccLastFourDigits` stays 0.
- Vendor is a fixed-width 22-char column, then city (14), then 2-letter country, then `.`, then optional ` Available Limit: {n}`. Extraction: capture everything between `at ` and the terminator; remove `\` escape chars (bodies contain `M&M\'S PHARMACY`); split on 2+ spaces → if ≥3 parts, vendor = first part; else vendor = `rest[:22].strip()` (handles vendors that exactly fill the column, e.g. `SHUJAAT FILLING STATIO KARACHI        PK`). Validated: 624/624 regex matches; both split paths are real (523 msgs take the ≥3-parts path, 60 the `rest[:22]` path; 8 bodies contain the backslash escape). Note 624/624 validates the *regex match*, not vendor-field correctness — a vendor name with an internal double space would mis-split; no such case exists in the corpus, and the split rule is pinned by unit tests on the known shapes.

### Standard Chartered — sender `7220`, 405 txn-signal msgs raw / 404 post-dedup (378 parseable + 26 skipped at runtime)
```
Dear Client, PKR {amt} have been paid at {vendor}{city} {CTY} on {DD-MM-YY} using Credit Card no {mask}. Avail Limit PKR{x}. SCBPL
```
- `Dear Client, PKR 12,450.90 have been paid at PSO SERVICE STATION 7Karachi PAK on 29-09-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit PKR59563.45. SCBPL`
- Amount: comma-grouped OR plain, always 2 decimals → `[\d,]+\.\d{2}`.
- Date: `%d-%m-%y` (validated: middle token spans 1–12 = month).
- Card mask: `5452xxxxxxxx1280` → last4 = `1280`; bare `5495` / `5974` (BIN only, no last-4) → last4 = 0. Extract via `^\d{4}x+(\d{4})$`, else 0. (Corpus: 295 full masks, 83 BIN-only, 0 other shapes — the two-form assumption is exhaustive.)
- Vendor: keep the raw captured text before ` on ` (city is sometimes glued to the vendor with no space — `SOUTH CITY HOSPITALKarachi PAK` — so no reliable split exists; document as known limitation).
- Currency: always PKR in data; still parse the currency token.
- **26 unusable**: 21 truncated (`'Dear Client, PKR 281.00 have been paid at NECOS NATURAL STORE '` — body ends, no date/card) and 5 with literal `PKR .00` amount (foreign-currency txns, real amount absent). Both → return `None` + warn + count as skipped.

### Meezan Bank — senders `8079`, `9779`, 879 debit txns raw (875 post-dedup), all PKR
Five template families (all validated; counts are raw → post-dedup):
| Type | Template head | Count |
|---|---|---|
| CARD_PURCHASE | `PKR {amt} charged at {VENDOR} for card used, from A/C {mask} ({branch}) on {date} at {HH:MM}…` | 8 → 8 |
| ATM_WITHDRAWAL | `PKR {amt} cash withdrawn from {ATM} from A/C {mask} {branch} on {date} at {HH:MM}…` | 363 → 361 |
| ACCOUNT_DEBIT | `PKR {amt} is debited as {DESC} from your A/C {mask} of {branch} on {date} at {HH:MM}…` | 97 → 96 |
| FUNDS_TRANSFER | `PKR {amt} sent to {PAYEE} from your A/C {mask}[ of {branch}] on {date} at {HH:MM}…` — also `AC#` spelling (RAAST) and a `(MBL AC #xxx#)` payee variant with **no** `of {branch}` clause | 370 → 369 |
| FUNDS_TRANSFER (uppercase) | `PKR {amt} SENT TO {PAYEE} on {date} at {HH:MM} Bal:…` — no account clause at all | 41 → 41 |

(Re-audit correction: the original draft estimated the lowercase transfer family at "~331" and the total at
"~840" — stale figures from before the `AC#`/`(MBL AC #…)` variants were folded into the regex. Re-validated
totals: 879 raw / 875 post-dedup. Both FUNDS_TRANSFER families map to the single `funds_transfer` enum value:
410 post-dedup.)
- Date: `%d-%b-%y` **and** `%d-%b-%Y` both occur (format changed to 4-digit years around Apr 2025) + time `%H:%M`. Day may be 1 or 2 digits.
- Trailing junk varies (`TID:`, `UAN`, `Fee: Rs.X`, `Bal:`, bare ` .`) — do not anchor the regex end.
- Credits (`is credited as Rev …`, `received from`), cheque clearing, e-statement/bill-reminder msgs must NOT match.

## Design

### 1. Data model
**`src/cc_txn.py`** — add `bank: str = "HBL"` field to `CreditCardTxnDC` (default keeps every existing call site and test valid); include bank in `__repr__`: `CC Txn [HBL]: PKR 25170.49 at … on …`.

**New `src/debit_txn.py`** — following the `cc_txn.py` pattern:
```python
class DebitTxnType(StrEnum):        # in this new file
    CARD_PURCHASE = auto()
    ATM_WITHDRAWAL = auto()
    ACCOUNT_DEBIT = auto()
    FUNDS_TRANSFER = auto()

@dataclass
class DebitTxnDC:
    amountTuple: CurrencyAmountTuple   # reuse from cc_txn.py
    date: datetime
    vendor: str            # merchant / ATM location / bill desc / payee
    txnType: DebitTxnType
    acctMask: str = ""     # e.g. "xxxxxx5602"; "" when msg has none
    bank: str = "MEZN"
```
Custom `__repr__` mirroring `CreditCardTxnDC`'s.

### 2. Parsers — one module per bank in `src/parser/`, mirroring `HBLSmsParser`'s active (static-method) surface only. No legacy instance methods.

Shared conventions (identical to HBL parser): pre-compiled class-level regex constants; `_convertToDateTime()` **stamps** `DEFAULT_TZ` via `.replace(tzinfo=…)` — never `astimezone()`; parse failures return `None` and `print()` a warning (no exceptions); import `DEFAULT_TZ` from `common.py` (not the `cc_txn.py` duplicate).

**`src/parser/fbl_sms_parser.py` — `FBLSmsParser`**
- `ID = "FBL"`, `FBL_SHORT_CODES = ["8756"]` (moves ownership of the code list from `SmsBackupFileParser` into the parser, like HBL).
- `isSmsFromFBL(sms)`, `isMsgCreditCardTxn(sms)` (checks `"FBL Card"` and `"has been charged for"`), `extractDetailsFromTxnMsg(sms) -> CreditCardTxnDC | None`.
- Main regex (validated 624/624):
  `r"Dear .+?, your\s+FBL Card\s+has been charged for (?P<currency>[A-Z]{3}) (?P<amount>\d+(?:\.\d+)?) on (?P<txndate>\d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2} [AP]M|\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}) at (?P<rest>.+?)\.(?:\s*Available Limit: .*)?$"`
- Date: try `%d-%b-%y %I:%M:%S %p`, fall back to `%d-%m-%Y %H:%M:%S`.
- Vendor from `rest` per the validated split/slice rule above (constant `VENDOR_COL_WIDTH = 22`).
- Returns txn with `bank="FBL"`, `ccLastFourDigits=0`.

**`src/parser/scb_sms_parser.py` — `SCBSmsParser`**
- `ID = "SCB"`, `SCB_SHORT_CODES = ["7220"]`.
- `isSmsFromSCB(sms)`, `isMsgCreditCardTxn(sms)` (checks `"have been paid at"` **only** — this is the "txn attempt" signal the orchestrator gates on. The 21 truncated msgs pass it but fail extraction; the 5 `PKR .00` msgs pass it and fail extraction. Both therefore flow extract → `None` → `SCB_SKIPPED`, giving the required skipped count of 26. Do NOT also require `"using Credit Card no"` in this check — that would silently drop the truncated msgs from the skip accounting), `extractDetailsFromTxnMsg(sms) -> CreditCardTxnDC | None`.
- Main regex (validated 379/379 of well-formed):
  `r"Dear Client, (?P<currency>[A-Z]{3}) (?P<amount>[\d,]+\.\d{2}) have been paid at (?P<vendor>.+?) on (?P<txndate>\d{2}-\d{2}-\d{2}) using Credit Card no (?P<cardmask>[\dx]+)\. Avail Limit"` — `[\d,]+\.\d{2}` inherently rejects the `PKR .00` msgs (no digits before the dot), and truncated msgs simply don't match; both return `None`.
- Date `%d-%m-%y`; last4 via `re.fullmatch(r"\d{4}x+(\d{4})", mask)` → int(group) else 0; `bank="SCB"`.

**`src/parser/mezn_sms_parser.py` — `MeznSmsParser`**
- `ID = "MEZN"`, `MEZN_SHORT_CODES = ["8079", "9779"]`.
- `isSmsFromMezn(sms)`, `isMsgDebitTxn(sms)`, `extractDetailsFromTxnMsg(sms) -> DebitTxnDC | None`.
- Ordered list of `(DebitTxnType, compiled_regex)`; first match wins (corpus check: no body matches two families, so ordering is a tiebreak that never fires today). Building blocks (validated 879/879 raw, 875 post-dedup):
  - `AMT = r"(?P<currency>[A-Z]{3}) (?P<amount>[\d,]+\.\d{2})"`
  - `DATE = r"on (?P<txndate>\d{1,2}-[A-Za-z]{3}-(?:\d{4}|\d{2})) at (?P<txntime>\d{1,2}:\d{2})"`
  - CARD_PURCHASE: `AMT + r" charged at (?P<vendor>.+?) for card used, from A/C (?P<acmask>\S+) \((?P<branch>.+?)\) " + DATE`
  - ATM_WITHDRAWAL: `AMT + r" cash withdrawn from (?P<vendor>.+?) from A/C (?P<acmask>\S+) .+? " + DATE`
  - ACCOUNT_DEBIT: `AMT + r" is debited as (?P<vendor>.+?) from your A/C (?P<acmask>\S+) of .+? " + DATE`
  - FUNDS_TRANSFER: `AMT + r" sent to (?P<vendor>.+?) from your A/?C#? (?P<acmask>\S+)(?: of .+?)? " + DATE` (case-sensitive `sent to`)
  - FUNDS_TRANSFER uppercase: `AMT + r" SENT TO (?P<vendor>.+?) " + DATE` (no account clause; `acmask=""`)
  - Normalize runs of whitespace in the body to single spaces before matching. (Re-audit note: 0 of the 875 matched bodies actually require this today — double spaces occur only inside `.+?`-captured fields — but keep it: it makes the signal check and regexes robust at no cost. The §3 hash-vs-match whitespace limitation note still applies.)
- `isMsgDebitTxn(sms)` — an **independent keyword signal, deliberately looser than the extraction regexes** (mirrors the FBL/SCB two-tier design). The original draft defined it as "any of the five regexes matches", which makes signal ≡ extraction: a changed Meezan template would then be silently counted as a plain MEZN msg with **no warning** — exactly the silent-gap failure mode CLAUDE.md documents from the HBL-14250 migration. Instead, on the whitespace-normalized body: `re.match(r"[A-Z]{3} [\d,]+\.\d{2} ", …)` (amount head) **AND** casefold-contains one of `"cash withdrawn"` / `"is debited as"` / `"sent to"` / `"for card used"` **AND** casefold-contains none of `"credited"` / `"received from"` / `"cheque"` / `"reversal"`. **Corpus-validated 2026-08-26 on the deduped stream: signal ∩ matched = 875, signal-without-match = 0, match-without-signal = 0** — a perfect partition, so no txn is dropped by the signal and `MEZN_SKIPPED` starts at 0. The amount-head anchor is load-bearing: a card-dispatch notice on these short codes reads "…has been sent to your [address]" and must not trip the signal.
- Date: try `%d-%b-%y %H:%M`, fall back to `%d-%b-%Y %H:%M` (combine date+time before `strptime`, then stamp `DEFAULT_TZ`).

### 3. Orchestrator — `src/sms_backup_file_parser.py`
Restructure `parseMessages()` (this is the one intentional behavior change beyond addition):
1. Skip `<mms>`.
2. `msgCounts["ALL"] += 1` — **before** the duplicate check (duplicates still count toward ALL, preserving the existing `ALL == 2` pin in `test_duplicate_msg_parsed_once` and the CLI's `Parsed {msgCount} SMS messages` output).
3. Compute the msg hash **once**; if duplicate → `msgCounts["DUP"] += 1`, `continue`. This fixes two documented known issues (hash recomputed per elif branch; duplicate HBL msgs miscounted as OTHER) — necessary now because four parse branches would otherwise each recompute/re-check. **Two pinned tests change on purpose** (see Tests). Also **silence `_isSmsDuplicate`'s per-duplicate printing** (today: a six-line original-vs-duplicate body dump per hit): with global dedup the real backup produces 258 dups — 145 of them retransmitted promo msgs from non-bank senders — i.e. ~1,500 lines of noise ahead of any command output. Keep the hash bookkeeping, drop the prints; the `DUP` summary line and unit tests carry the signal.
4. Route by sender via each parser's `isSmsFrom*()`:
   - HBL branch: unchanged (incl. its asserts) — *except* that its `and (not self._isSmsDuplicate(child))` condition disappears, since the dup check now happens once before routing (step 3).
   - FBL branch: `msgCounts["FBL"] += 1`; if `isMsgCreditCardTxn` → extract; `None` ⇒ `msgCounts["FBL_SKIPPED"] += 1` + warn; else append to `self.ccTxns`, vendor into `self.ccVendors`.
   - SCB branch: same shape with `"SCB"` / `"SCB_SKIPPED"` (this is where the 26 unusable msgs land).
   - MEZN branch: `msgCounts["MEZN"] += 1`; if `isMsgDebitTxn` → extract into `self.debitTxns` (new list), vendor into `self.debitVendors` (new set); `None` ⇒ `"MEZN_SKIPPED"` + warn.
   - No asserts on the new branches — skip+warn+count instead (the new banks have known-malformed real msgs; an `assert` would abort the whole run, which is exactly the HBL brittleness documented in IMPROVEMENTS.md).
   - Note: `MEZN_SKIPPED` is reachable two ways — (a) the keyword signal passes but no template regex matches (future Meezan template drift: the whole point of the independent signal), and (b) the date regex accepts tokens `strptime` rejects (e.g. `31-Feb-25`). Both end as extraction `None` → skip+warn+count. Add a one-line comment saying so. On today's corpus `MEZN_SKIPPED == 0` (the signal was validated to select exactly the 875 parseable debits).
5. `__init__` additions: `self.debitTxns = []`, `self.debitVendors = set()`.
6. Extend the parse summary print with FBL/SCB CC txn counts, MEZN debit txn count, per-bank skipped counts, and DUP count.
7. Remove `FBL_SHORT_CODES` / `SCB_SHORT_CODES` / `MEZN_SHORT_CODES` from `SmsBackupFileParser` (ownership moves to the parsers, mirroring HBL). **Timing: this is the FINAL state, not a Phase A step** — Phase A keeps all three constants and their count-only branches (so the base branch stays green); each builder deletes exactly their own constant when they replace their branch in Phase B.
8. Known limitation to document (not fix): `calcSmsMsgHash` hashes the raw stripped body while the Meezan parser normalizes internal whitespace only for regex matching — two Meezan bodies differing only in internal double-vs-single spaces hash differently and both parse. Real duplicate msgs are byte-identical retransmissions, so this is acceptable; record it next to the existing body-only-dedup note.
9. **Global-dedup semantic changes** (all verified against the real backup — see the ground-truth table): (a) identical bodies from non-bank senders now count as `DUP` instead of `OTHER` (OTHER: 1354 → 1097); (b) hashing is body-only and therefore cross-sender, so one SCB msg whose body duplicates an earlier non-bank msg now lands in `DUP` (SCB msg count: 614 → 613) — an expected consequence of the pre-existing limitation, not a new bug; (c) `ALL` is unchanged at 4,665. **Conservation identity:** `ALL == HBL + FBL + SCB + MEZN + OTHER + DUP` must hold for any backup — pin it with a unit test (synthetic mixed backup incl. a duplicate, an unknown sender, and an MMS) and assert it in the verification harness on every real-backup run.

### 4. CLI — `src/hbl_sms_query_tool.py`
- Existing three commands now naturally show HBL+FBL+SCB combined (they read `smsParser.ccTxns`). Update the echo text from "HBL CC transactions" to "CC transactions" (`list_all_cc_txns`), the `Found {n} unique Vendors from parsed HBL SMS messages` line in `list_all_vendors` (same "HBL" removal — this command reads the filtered txn list, not the `ccVendors` set, so `--bank`/date filters compose correctly as-is), and the group callback's `Parsed {msgCount} SMS messages from HBL` line (it's the ALL count, not HBL) to `Parsed {msgCount} SMS messages`.
- Add `--bank` option (`type=click.Choice(["HBL", "FBL", "SCB"], case_sensitive=False)`, default None = all) to all three CC commands. New helper `_filterTxnsByBank(txns, bank)` next to `_filterTxnsByDateRange` (which is reused untouched — it only needs `.date`): **must start with `if bank is None: return txns`**, then `return [t for t in txns if t.bank == bank]` — no `.upper()` needed (Click 8.4.2's case-insensitive Choice returns the original choice string, e.g. `"FBL"` for input `fbl`).
- New commands (explicit name strings — Click ≥8.2 dash-conversion gotcha):
  - `@cli.command("list_all_debit_txns")` — `--from-date/--to-date` + `--txn-type` filter: **`click.Choice([t.value for t in DebitTxnType])`**, NOT `click.Choice(DebitTxnType)` — verified 2026-08-26 on the pinned click 8.4.2: `click.Choice(DebitTxnType).convert("atm_withdrawal", …)` raises `BadParameter`, so the enum-typed Choice rejects the documented lowercase `--txn-type atm_withdrawal`. With `.value` strings, `txn.txnType == txn_type` compares fine via StrEnum str-equality. Lists `smsParser.debitTxns`.
  - `@cli.command("monthly_debit_spending_summary")` — same month/currency aggregation reusing `_updateMonthlyTotals` (it only needs `.date` + `.amountTuple`, both present on `DebitTxnDC`).
- Harden `_updateMonthlyTotals` against non-seeded currencies. **There are TWO `+=` sites** (`hbl_sms_query_tool.py:166`, existing-month branch, and `:174`, new-month branch — a first-seen EUR txn that also opens a new month hits the `KeyError` in the *else* branch too, so the original "one line before the `+=`" fix was insufficient). Add `monthlyTotals[monthKey].setdefault(currencyKey, 0.00)` before **both** sites, or restructure to seed-the-month-then-single-`+=`. The dict pre-seeds only pkr/usd/cad; the FBL regex captures any `[A-Z]{3}` code. Covered by two tests: EUR into an existing month AND EUR as the first txn of a new month.
- Bump `@click.version_option` to `0.3.0`.
- Explicitly OUT of scope (deliberate, not an omission): a `list_all_debit_vendors` command — user decision 2 named exactly two debit commands. Trivial follow-up if wanted later (`debitVendors` is already populated by the MEZN branch).

### 5. Docs
- Update `CLAUDE.md` + `README.md`: bank table (all four parsed), new commands, new modules, updated known-issues list (mark the dedup/OTHER quirk and per-branch hashing as fixed).
- Update `src/IMPROVEMENTS.md` accordingly.

## Tests (unittest, programmatic ET.Element construction, run from `src/`)

New files follow `test_hbl_sms_parser.py` / `test_sms_backup_file_parser.py` style. All sample bodies below are drawn verbatim (structure-wise) from the validated real corpus.

**`tests/test_fbl_sms_parser.py`**
- identification: short code 8756 yes / 4250 no; txn-msg check accepts charge msgs, rejects statement/payment-received/OTP msgs (real non-txn templates exist on 8756: `"Payment of PKR … has been received on your FBL Card"`, Noor Card statement notices — pin that these do NOT match).
- extraction: 12-hour date format; 24-hour numeric date format; amount without separators incl. decimals (`18298.9`); USD and CAD msgs; trailing ` Available Limit: …` variant; vendor from 3-part split; vendor exactly filling the 22-char column (`SHUJAAT FILLING STATIO`); backslash-apostrophe vendor (`M&M\'S PHARMACY`, `AUNTIE MANAVER\'S DESSE`); `ccLastFourDigits == 0`; `bank == "FBL"`; tz stamped `Asia/Karachi` (compare `.tzinfo` + exact datetime); unparseable body → `None`.
- name-wobble: both `your FBL Card  has` and `your  FBL Card has` spacings parse.
- skip path: a body that passes `isMsgCreditCardTxn` but fails the regex (e.g. a comma-grouped amount `PKR 25,170.49` — FBL never groups) → `None`.
- end-to-end (in THIS file, self-contained backup-writing helper — builders do NOT extend the shared `tests/test_sms_backup_file_parser.py`; keeping builders out of shared test files removes a guaranteed merge-conflict site): backup with one good FBL charge msg + one malformed charge-signal msg → `ccTxns` has 1 txn with `bank == "FBL"`, `msgCounts["FBL"] == 2`, `msgCounts["FBL_SKIPPED"] == 1`, vendor in `ccVendors`; identical FBL bodies collapse to one txn with `msgCounts["DUP"] == 1`.

**`tests/test_scb_sms_parser.py`**
- identification incl. rejecting the account/OTP/statement templates that also start `Dear Client`/`Dear Customer` (`"a transaction of PKR … using Online Banking"`, `"payment of Rs … has been received"`).
- extraction: comma amount; no-comma amount; full mask → last4 1280; BIN-only mask (`5495`) → last4 0; date `29-09-23` → 2023-09-29 tz-stamped; `bank == "SCB"`.
- skip paths: truncated body → `None`; `PKR .00` body → `None`.
- end-to-end (in THIS file, same rule as FBL): backup with one good paid msg + one truncated msg → 1 txn with `bank == "SCB"`, `msgCounts["SCB"] == 2`, `msgCounts["SCB_SKIPPED"] == 1`, no second txn.

**`tests/test_mezn_sms_parser.py`**
- one test per template family asserting `txnType`, vendor/payee, amount, acmask, tz-stamped datetime.
- date variants: 2-digit year, 4-digit year, 1-digit day/hour.
- transfer variants: `of {branch}` present, absent (`(MBL AC #…)` form), `AC#` RAAST form, uppercase `SENT TO`.
- trailing-junk tolerance: `TID:`, `UAN`, `Fee:`, `Bal:`, bare ` .`.
- negative: credit (`is credited as Rev …`), `received from`, cheque inward-clearing, e-statement, bill-reminder msgs → `isMsgDebitTxn` False; a card-dispatch notice ("…has been sent to your [address]", no amount head) → False (pins the amount-head anchor of the signal).
- signal-vs-extraction split (pins the drift tripwire): a drifted-template body (amount head + `sent to` + an unmatchable tail) → `isMsgDebitTxn` True, `extractDetailsFromTxnMsg` → `None`; a `31-Feb-25` date (regex-valid, strptime-invalid) → same split.
- end-to-end (in THIS file, same rule as FBL/SCB): one msg per template family lands in `debitTxns` with the right `txnType`; the drifted-template msg → `msgCounts["MEZN_SKIPPED"] == 1` and nothing appended to `debitTxns`.

**`tests/test_sms_backup_file_parser.py`** — extended in TWO stages, **by the main agent only, never by builders** (the original draft had all three builders extending this file — a guaranteed second merge-conflict site; their end-to-end cases now live in each builder's own parser test file, see above):
- **Phase A (main agent):** **update** `test_duplicate_msg_parsed_once` — dup now counted as `DUP`, not `OTHER`; `msgCounts["ALL"] == 2` stays valid because ALL increments before the dup check. Add `test_msg_count_conservation`: synthetic backup with an HBL txn, one msg per bank sender, an unknown-sender msg, a duplicate, and an MMS → asserts `ALL == HBL + FBL + SCB + MEZN + OTHER + DUP` and that the MMS is excluded from every counter. Add a test pinning the new semantics that a duplicate from an *unknown* sender counts as `DUP` (not `OTHER`).
- **Phase C (main agent, after all three builder merges — these need all parsers to exist):** mixed-bank backup (per-bank counts, `ccTxns` length, `debitTxns` length, vendor sets, conservation identity with all banks live); cross-bank non-collision: same-shaped msgs from different banks don't interfere.
- `test_bank_routing_counts` needs NO pin change at any stage: Phase A keeps the count-only branches alive, and after builders replace them its placeholder bodies match no txn signal, so all existing assertions still hold.
- monthly-totals hardening tests (two EUR cases) live in `test_cli_commands.py` — see below.

**`tests/test_cli_commands.py`** — **intentional pin update**: `test_subcommand_names_use_underscores` asserts strict set equality over `cli.commands` — the expected set must grow by `list_all_debit_txns` and `monthly_debit_spending_summary`. Also extend: `--bank` exists on all three CC commands (including `list_all_vendors`) and `--txn-type` on `list_all_debit_txns`; `_filterTxnsByBank` returns everything for `None` and filters exactly for `"FBL"`; monthly-totals hardening — EUR into an existing month AND EUR opening a new month both aggregate instead of raising `KeyError` (pins both `+=` sites, see Design §4).

**`tests/test_date_range_filter.py`** — add a case filtering `DebitTxnDC` objects (proves the shared filter works for both dataclasses), including one debit txn carrying a real time-of-day near midnight on the `--to-date` boundary (FBL/MEZN dates carry times, unlike HBL's midnight-stamped dates; `_filterTxnsByDateRange` compares `.date.date()` so inclusivity holds — pin it). **Owned by Phase A, not Agent MEZN** — `DebitTxnDC` exists as of Phase A item 2, and moving this here removes Agent MEZN's only other shared-file edit.

## Files touched
| File | Change |
|---|---|
| `src/parser/fbl_sms_parser.py` | new |
| `src/parser/scb_sms_parser.py` | new |
| `src/parser/mezn_sms_parser.py` | new |
| `src/debit_txn.py` | new |
| `src/cc_txn.py` | add `bank` field + repr |
| `src/sms_backup_file_parser.py` | dedup-first routing (per-dup print silenced), 3 count-only branches replaced by parsing branches, new stores, summary print |
| `src/hbl_sms_query_tool.py` | `--bank`, 2 new commands, `--txn-type`, wording, version 0.3.0 |
| `tests/test_{fbl,scb,mezn}_sms_parser.py` | new — each self-contains its bank's end-to-end backup tests |
| `tests/test_sms_backup_file_parser.py`, `test_cli_commands.py`, `test_date_range_filter.py` | extend (main agent only: Phase A + Phase C) + 2 intentional pin updates (`test_duplicate_msg_parsed_once`, `test_subcommand_names_use_underscores`) |
| `CLAUDE.md`, `README.md`, `src/IMPROVEMENTS.md` | doc refresh |
| scratchpad (NOT committed): `baseline_main.txt`, `verify_against_backup.py` | regression baseline + stage-parameterized verification harness |

## Verification & gates (dataset: `D:\wa_git\sms_msgs_scraper\sms-20251011130814.xml` — read-only, gitignored, never committed)

All expected numbers are the **post-dedup** column of the ground-truth table. The stage-parameterized harness
`verify_against_backup.py` (written in Phase A, lives in the scratchpad, never committed) parses the backup and
asserts every metric for the requested stage, printing expected-vs-actual per metric on failure — **counts
only, never message bodies** (the backup is personal data). A stage passes only if EVERY assertion holds,
including the conservation identity `ALL == HBL + FBL + SCB + MEZN + OTHER + DUP`.

**Which checkout it verifies (critical in a multi-worktree build):** the harness takes the repo root to verify
as a CLI argument and inserts *that checkout's* `src/` at the front of `sys.path` before importing
`SmsBackupFileParser`; it echoes the resolved path in its report. Each builder runs it against **their own
worktree** — a harness that silently imported the main checkout would verify the wrong tree and either fail the
builder's gate spuriously or, worse, pass the wrong stage.

**Per-txn invariants (asserted at every stage, implementation-independent):** every txn in `ccTxns` and
`debitTxns` has a non-empty `vendor`, `amountTuple.amount > 0`, a timezone-aware `date` stamped
`Asia/Karachi` (catches a stray `astimezone()` or missing stamp), `bank` in `{"HBL","FBL","SCB"}` /
`== "MEZN"` respectively, and (debits) a `txnType` among the four enum values. These catch content-level
regressions that pure counts cannot — 583 FBL txns with empty vendors or naive dates would otherwise pass.
Exception: the `main` baseline stage must not touch `.bank`/`debitTxns` — neither exists on `main`.

### Stage gates
| Stage (run on) | `ccTxns` | `debitTxns` | msgCounts / other assertions |
|---|---|---|---|
| `main` baseline (pre-change) | 717 (all HBL) | n/a | HBL 797, FBL 673, SCB 614, MEZN 1227, OTHER 1354, ALL 4665; 180 unique vendors |
| Foundation (base branch after Phase A) | 717 | 0 | HBL 797, FBL 673, SCB **613**, MEZN 1227, OTHER 1097, DUP 258, ALL 4665; conservation |
| Agent FBL worktree | **1300** (717+583) | 0 | FBL 673, FBL_SKIPPED 0, DUP 258; FBL vendors 166, currency split 574/8/1; conservation + invariants |
| Agent SCB worktree | **1095** (717+378) | 0 | SCB 613, SCB_SKIPPED 26, DUP 258; SCB vendors 94; conservation + invariants |
| Agent MEZN worktree | 717 | **875** | MEZN 1227, MEZN_SKIPPED 0, DUP 258; families 8 / 361 / 96 / 410; MEZN vendors 189; conservation + invariants |
| Integrated (base after all 3 merges) | **1678** (717+583+378) | **875** | HBL 797, FBL 673, SCB 613, MEZN 1227, OTHER 1097, DUP 258, ALL 4665; FBL_SKIPPED 0, SCB_SKIPPED 26, MEZN_SKIPPED 0; vendors FBL 166 / SCB 94 / MEZN 189 / CC union 357; conservation + invariants |

(`funds_transfer` 410 = 369 lowercase + 41 uppercase family. SCB 613 vs the baseline's 614 is the documented
cross-sender dedup effect — see ground-truth footnote — not a lost message: it moves to DUP and conservation
still balances.)

### Procedure
1. **Baseline capture (before Phase A, on `main`)**: run `list_all_cc_txns`, `list_all_vendors`, and
   `monthly_cc_spending_summary` against the backup; save the summary/`Found …` lines and the full monthly
   totals dict to scratchpad `baseline_main.txt`; confirm the "main baseline" row (717 / 180 / counts). If it
   does not match, STOP — the corpus or environment differs from what this plan validated against.
2. **Unit suite at every gate**: `cd src && uv run python -m unittest discover -s ../tests/ -v` — zero
   failures (existing 28, minus the 2 intentional pin updates' old expectations, plus ~45 new).
3. **Harness at the stage matching the branch** (table above) — builders run their own stage before opening
   their PR; the main agent runs Foundation and Integrated.
4. **HBL regression (Integrated only)**: `list_all_cc_txns --bank HBL` → exactly **717** txns;
   `list_all_vendors --bank HBL` → exactly **180** vendors; `monthly_cc_spending_summary --bank HBL` totals
   dict **numerically equal** to the baseline dict (wording may differ; numbers may not).
5. **CLI end-to-end spot checks (Integrated)**:
   - `list_all_cc_txns` → 1678; `--bank FBL` → 583; `--bank SCB` → 378; lowercase `--bank fbl` → 583
     (pins case-insensitive Choice).
   - `list_all_vendors` → **357** unique vendors (combined CC union); `list_all_vendors --bank SCB` → 94.
   - `list_all_debit_txns` → 875; `--txn-type atm_withdrawal` → 361; `--txn-type card_purchase` → 8.
   - `monthly_debit_spending_summary` completes without `KeyError`; `monthly_cc_spending_summary --from-date
     2024-01-01 --to-date 2024-12-31` → sane per-currency totals; hand-spot-check one month of
     `monthly_cc_spending_summary --bank FBL` against 2–3 raw msgs.
6. **Mis-count tripwires (unit-level, run at all gates)**: conservation test; unknown-sender-dup→DUP test;
   FBL_SKIPPED / SCB_SKIPPED / MEZN_SKIPPED accounting tests; MEZN drift (signal-pass/extract-fail) test; the
   two EUR monthly-totals tests; `test_bank_routing_counts` untouched and green.

**Anti-drift rule:** if a harness metric misses, the fix lives in the code — or in a re-derivation of the
expectation from the corpus, recorded in this file with the derivation. **Never** quietly edit the expected
table to match observed output.

---

# Multi-agent execution

## Roles and branch/PR topology (stacked PRs — main is never touched without the user)

```
main
 └── feat/multi-bank-base          (PR-0 → main; built by the MAIN agent)
      ├── feat/fbl-cc-parser       (PR-1 → feat/multi-bank-base; Agent FBL)
      ├── feat/scb-cc-parser       (PR-2 → feat/multi-bank-base; Agent SCB)
      └── feat/mezn-debit-parser   (PR-3 → feat/multi-bank-base; Agent MEZN)
```

- **Main agent** builds the foundation (Phase A), spawns the three builders in parallel, monitors their progress, reviews each PR with a fresh-context review, merges PR-1/2/3 into the base branch in a fixed order, runs the integrated end-to-end verification, then hands the user **one final integrated PR-0 into main** for approval. The main agent merges only into the feature base branch — never into `main`.
- Git specifics for every agent: origin is HTTPS with the `gh` credential helper (SSH is broken on this machine — do not switch remotes); commits/pushes/PRs are explicitly authorized by the user for this task; commit messages explain *why*; stage specific files only; the backup XML is gitignored personal data — **never commit it**, reference it read-only at its absolute path `D:\wa_git\sms_msgs_scraper\sms-20251011130814.xml`.

## Phase A — Foundation (main agent, sequential, must merge to the base branch before builders start their orchestrator edits)

Everything that more than one workstream would otherwise touch, so the parallel builders' footprints become near-disjoint:

0. **Baseline capture on `main`, before branching**: run the Verification Procedure step 1. If the "main
   baseline" numbers (717 txns / 180 vendors / per-bank counts) don't reproduce, STOP and surface to the user.
1. `src/cc_txn.py`: `bank: str = "HBL"` field + repr change.
2. `src/debit_txn.py`: new `DebitTxnType` + `DebitTxnDC` (MEZN needs it; CLI debit commands need it; creating it here avoids a cross-PR dependency).
3. `src/sms_backup_file_parser.py`: the dedup-first restructure ONLY (§3 steps 1–3 + DUP count + **silence the per-dup body printing** + `debitTxns`/`debitVendors` stores + summary print extended with DUP). **KEEP the three existing count-only `elif` branches AND their short-code constants**, rewritten as plain address checks (their per-branch `_isSmsDuplicate` calls move up into the shared dedup step). This keeps `test_bank_routing_counts` green on the base branch — the original phasing ("branches NOT added here, each builder adds their own `elif`") left the base red and made every builder's full-suite-green gate **unsatisfiable**: that test asserts all three banks' counts at once, and no single builder restores the other two. Builders REPLACE their branch in Phase B instead. Update the `test_duplicate_msg_parsed_once` pin; add the conservation-identity and unknown-sender-dup tests.
4. `src/hbl_sms_query_tool.py`: all CLI changes (`--bank` + `_filterTxnsByBank` on all three CC commands, both debit commands with `--txn-type`, `_updateMonthlyTotals` **two-site** hardening, wording incl. `list_all_vendors`, version 0.3.0). The debit commands simply show 0 txns until PR-3 merges — harmless and testable. Update `test_subcommand_names_use_underscores` pin; add the CLI/monthly-totals (two EUR cases) tests; add the `test_date_range_filter.py` `DebitTxnDC` case (moved here from Agent MEZN — `DebitTxnDC` exists as of item 2).
5. Write `verify_against_backup.py` in the scratchpad (never committed): stage-parameterized assertions per the Verification table, **takes the repo root to verify as an argument** (builders point it at their worktree — see "Which checkout it verifies" in Verification), asserts the per-txn invariants, expected-vs-actual per metric, counts only — never bodies.
6. **Phase A gate**: full suite green + harness passes the **Foundation** stage (ccTxns 717, debitTxns 0, SCB 613, OTHER 1097, DUP 258, conservation).
7. Push `feat/multi-bank-base`, open PR-0 → main (initially foundation-only; it grows as builder PRs merge into it).

## Phase B — Three builder agents in parallel (spawned in ONE message; each in an isolated worktree branched from `feat/multi-bank-base`)

Each builder's prompt embeds: its bank's full validated-format section, its parser design section, its test list, the shared conventions (tz stamping rule, static-method pattern, camelCase, unittest style, run-tests-from-`src/` command), the exact orchestrator wiring contract below, its **post-dedup** stage-gate numbers, and the path to `verify_against_backup.py`.

| | Agent FBL | Agent SCB | Agent MEZN |
|---|---|---|---|
| New files | `src/parser/fbl_sms_parser.py`, `tests/test_fbl_sms_parser.py` | `src/parser/scb_sms_parser.py`, `tests/test_scb_sms_parser.py` | `src/parser/mezn_sms_parser.py`, `tests/test_mezn_sms_parser.py` |
| Shared-file edit (the ONLY one) | in `parseMessages()`: **REPLACE** the FBL count-only `elif` branch with the parsing branch (+ `FBL_SKIPPED` count + summary lines) and delete `FBL_SHORT_CODES` from `SmsBackupFileParser` — end-to-end tests live in the builder's own test file, NOT in shared test files | same, for SCB | same, for MEZN |
| Wiring contract | replace your bank's existing branch **in place** (chain order stays HBL → FBL → SCB → MEZN → `else`; a left-behind count-only branch would double-count silently); per-bank skipped counter named `<ID>_SKIPPED`; no asserts; skip+warn+count | same | same |
| Must-pass gate before opening PR | full suite green from `src/` + harness "Agent FBL" stage: ccTxns **1300** (717 HBL + 583 FBL), FBL_SKIPPED **0**, conservation | suite green + harness "Agent SCB" stage: ccTxns **1095** (717 + 378), SCB_SKIPPED **26**, conservation | suite green + harness "Agent MEZN" stage: debitTxns **875** (8 card_purchase / 361 atm_withdrawal / 96 account_debit / 410 funds_transfer), MEZN_SKIPPED **0**, conservation |

All gate numbers are **post-dedup** — the runtime dedups before parsing. The raw-corpus figures in the format
sections (624 FBL / 379 SCB / 879 MEZN) are regex-validation evidence and will never be observed at runtime;
the original draft gated on them, which could never pass (SCB's exact "379" was strictly impossible).

Each builder: creates its branch off `feat/multi-bank-base`, implements, runs `cd src && uv run python -m unittest discover -s ../tests/ -v` (uv auto-creates the worktree's venv from `uv.lock`), runs `verify_against_backup.py` at its own stage **with its worktree root as the repo-root argument** against the real backup, commits, pushes, opens its PR with `gh pr create --base feat/multi-bank-base`, and reports its PR URL + verification numbers back.

## Phase C — Coordination & integration (main agent)

1. **Monitor**: builders report via task notifications; if one stalls or its numbers miss the expected counts, message that agent with the discrepancy rather than fixing it in-place (keeps ownership clear).
2. **Review**: for each PR, run a fresh-context code review (per user's global guidelines: fresh context catches what the writing session misses) checking the wiring contract — **specifically that the count-only branch was REPLACED, not duplicated (a leftover branch double-counts silently), and that the short-code constant was removed** — tz stamping (`replace`, never `astimezone`), no asserts on new branches, and test quality.
3. **Merge order into the base branch — fixed: PR-1 (FBL) → PR-2 (SCB) → PR-3 (MEZN).** All three replace their branch in the `parseMessages()` elif chain — the only shared file now (builder end-to-end tests were moved into each builder's own file, eliminating the second conflict site) — so the 2nd and 3rd merges may conflict there; the main agent asks the affected builder agent to rebase (or resolves directly — the conflicts are in-place branch replacements with a deterministic order: FBL above SCB above MEZN, matching the existing chain order).
4. **Integrated tests + verification** on `feat/multi-bank-base` after all three merges: main agent adds the Phase C shared-file tests (mixed-bank, cross-bank non-collision — they need all parsers, so they're written once, here), then runs the full Verification section: suite + harness **Integrated** stage + HBL regression against `baseline_main.txt` (717 txns / 180 vendors / equal monthly totals via `--bank HBL`) + the CLI spot checks.
5. **Docs**: main agent commits the CLAUDE.md / README.md / IMPROVEMENTS.md refresh onto the base branch (docs describe the integrated whole, so they're written once, last). The doc refresh must carry the ground-truth table's post-dedup numbers and the global-dedup semantic notes (§3 step 9), so future sessions gate on the right figures.
6. **Handoff**: PR-0 now contains foundation + all three parsers + docs, fully verified. Report PR URLs and verification numbers to the user; **the user merges PR-0 into main**.

## Failure handling
- A builder whose gate fails re-plans within its worktree; after two failed correction rounds, the main agent respawns that workstream fresh with a sharpened prompt (per user's context-pollution rule) — the spec is self-contained, so a restart is cheap.
- If a rebase conflict exceeds the in-place branch-replacement pattern (unexpected overlap), stop and surface to the user rather than force-resolving.
- A harness mismatch is a gate failure, full stop. The harness prints expected-vs-actual per metric; diagnose in the code, or re-derive the expectation from the corpus and record the derivation in this plan — never adjust the expected table to match observed output without that record (see the Anti-drift rule in Verification).
