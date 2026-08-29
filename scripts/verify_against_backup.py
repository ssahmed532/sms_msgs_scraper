"""Verify the parsers against a real SMS backup file.

The unit suite proves the parsers behave correctly on hand-built messages. This
proves they still behave correctly on ~4,700 real ones -- which is where a regex
change that passes every unit test quietly loses 40 transactions shows up. Run
it after any change to a parser, to the routing, or to the duplicate identity.

    uv run python scripts/verify_against_backup.py [backup.xml]

**Why this script was rewritten.** Its previous version was green while the tool
was silently dropping 18 Standard Chartered transactions, because it only ever
checked numbers it had been told to expect. It compared counts against counts,
and a sender nobody had registered produced no count to be wrong about. A green
verifier and a green suite certified a stable blind spot for the life of the
project.

So it now performs **independent discovery**. It scans every sender in the file
for the banks' own transaction signatures *without consulting the registry's
sender list*, and fails when a signature turns up on a sender no bank claims.
That check does not depend on anyone having thought to write an expectation for
it, which is the only kind of check that could have caught the original bug.

Three kinds of check, and the difference matters:

  * **Discovery** finds what routing missed. Always run, never expectation-based.
  * **Invariants** hold for any backup and are always asserted -- the
    conservation identity, exact envelope accounting, and the structural
    properties of every transaction.
  * **Expected values** are tied to one backup file, identified by its SHA-256.
    Asserted exactly against that file; merely reported against any other, since
    they cannot mean anything there.

It prints counts and totals only, never message bodies, vendors or card digits.
Parse diagnostics are summarised by reason code rather than printed: an SMS
backup is personal financial data, and this output is the kind of thing that
ends up pasted into a bug report.

**Anti-drift rule.** If an expected value misses, the fix belongs in the code --
or in a re-derivation of the expectation from the corpus, recorded in the
**Reference numbers** table in `CLAUDE.md` together with the derivation that
produced it. Never edit a number here to match what the code happened to print;
that turns the one check that would have caught a regression into a rubber stamp.
"""

import hashlib
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Resolving src/ relative to this file (rather than the working directory) means
# running the script from inside a git worktree verifies that worktree's code,
# not whichever checkout happens to be on sys.path.
SRC_DIR = REPO_ROOT / "src"
if not SRC_DIR.is_dir():
    raise SystemExit(f"FATAL: no src/ directory found at {SRC_DIR}")
sys.path.insert(0, str(SRC_DIR))

from sms_msgs_scraper.domain.message import SMS_TAG, SmsRecord  # noqa: E402
from sms_msgs_scraper.domain.registry import (  # noqa: E402
    REGISTRY,
    BankRegistry,
    BankSpec,
    TxnKind,
)
from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser  # noqa: E402

# The backup the expected values below were derived from. Identified by content
# rather than filename, so a renamed copy is still recognized and a different
# backup is never silently measured against the wrong numbers.
REFERENCE_BACKUP_NAME = "sms-20251011130814.xml"
REFERENCE_BACKUP_SHA256 = (
    "c0792da646c9f06b1d83bf02f80930ac8e6e27b16d616e84a762537eb30668f3"
)

# Senders that carry a transaction signature but are deliberately not parsed.
# Empty, and it should stay that way: an entry here is a documented decision to
# ignore real transactions, and needs a reason next to it.
DISCOVERY_ALLOWLIST: dict[str, str] = {}

# Derived 2026-08-29 from the reference backup, after Standard Chartered's
# second short code (9220) was declared. The message counts are *post-dedup*
# runtime values: the parser dedups before parsing, so grepping the raw XML
# gives higher numbers for every bank and is not comparable.
EXPECTED = {
    # envelope: what the file declared, against what it held
    "envelope_declared": 4719,
    "envelope_actual": 4719,
    "envelope_sms": 4665,
    "envelope_mms": 54,
    "envelope_invalid": 0,
    # message routing
    "ALL": 4665,
    "HBL": 798,
    "FBL": 674,
    "SCB": 641,
    "MEZN": 1228,
    "OTHER": 1089,
    "DUP": 235,
    # messages carrying a txn signal that could not be parsed. SCB's 26 are the
    # bank's own malformed messages: 21 truncated mid-body, 5 carrying a literal
    # "PKR .00" with no amount. A change here means something moved.
    "HBL_SKIPPED": 0,
    "FBL_SKIPPED": 0,
    "SCB_SKIPPED": 26,
    "MEZN_SKIPPED": 0,
    # credit card txns
    "ccTxns": 1696,
    "ccTxns_HBL": 717,
    "ccTxns_FBL": 583,
    "ccTxns_SCB": 396,
    # account debit txns
    "debitTxns": 875,
    "debit_card_purchase": 8,
    "debit_atm_withdrawal": 361,
    "debit_account_debit": 96,
    "debit_funds_transfer": 410,
    # Vendor counts are the tripwire for a broken extraction rule: a build can
    # hit every txn count above while extracting garbage vendors, since a set of
    # empty strings still counts as one vendor.
    "vendors_HBL": 180,
    "vendors_FBL": 166,
    "vendors_SCB": 96,
    "vendors_MEZN": 189,
    "vendors_cc_all": 359,
    # FBL is the only bank in the corpus sending more than one currency
    "fbl_pkr": 574,
    "fbl_usd": 8,
    "fbl_cad": 1,
    # deduplication, and how much of it involved a judgement call. Ambiguous
    # duplicates are those from a bank whose alerts carry no time of day, where
    # a second genuine identical purchase cannot be ruled out. A conservative
    # upper bound: it counts every such suppression.
    "duplicates": 235,
    "ambiguous_duplicates": 31,
    # diagnostics, by reason
    "diagnostics": 26,
    "diag_no_template_match": 26,
}

# Exact per-bank, per-currency totals. Counts alone cannot catch an amount
# parsed wrongly -- 1,696 transactions summing to the wrong number is still
# 1,696 transactions. These are Decimal, and compared as Decimal.
EXPECTED_TOTALS = {
    ("HBL", "PKR"): Decimal("4272661.09"),
    ("HBL", "USD"): Decimal("306.71"),
    ("HBL", "CAD"): Decimal("1034.00"),
    ("FBL", "PKR"): Decimal("4279414.69"),
    ("FBL", "USD"): Decimal("603.00"),
    ("FBL", "CAD"): Decimal("2.00"),
    ("SCB", "PKR"): Decimal("2918984.99"),
    ("MEZN", "PKR"): Decimal("37034319.58"),
}

# The spending that declaring SCB's second short code recovered. Independently
# measured: 18 messages, 18 unique bodies, all 18 parsing with no warnings.
EXPECTED_RECOVERED_SENDER = "9220"
EXPECTED_RECOVERED_TOTAL = Decimal("94294.32")
EXPECTED_RECOVERED_TXNS = 18

MSG_BUCKETS = ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")
DEBIT_TXN_TYPES = ("card_purchase", "atm_withdrawal", "account_debit", "funds_transfer")


def fileSha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def discoverSignatureHits(backupPath: Path) -> dict:
    """Scan every sender in the file for every bank's transaction signature.

    Deliberately does **not** consult the registry's sender list. It asks the
    opposite question from routing: not "what does this sender send?" but "who
    is sending something that looks like a transaction?" -- which is the only
    form of the question that can discover a sender nobody registered.
    """
    hits = defaultdict(Counter)

    with backupPath.open("rb") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != SMS_TAG:
                continue

            record = SmsRecord.fromElement(element, 0)
            if record is not None:
                for spec in REGISTRY:
                    if spec.signal(record):
                        hits[record.sender][spec.id] += 1

            element.clear()

    return hits


def checkDiscovery(hits: dict) -> list:
    """Fail on a transaction signature from a sender no bank claims.

    This is the check that would have caught the original omission, and the one
    that will catch the next one. Deleting a sender from the registry must make
    this fail -- if it does not, the check is decorative.
    """
    failures = []

    for sender, bankHits in sorted(hits.items()):
        if REGISTRY.isRegistered(sender):
            continue

        if sender in DISCOVERY_ALLOWLIST:
            continue

        detail = ", ".join(f"{bank} x{count}" for bank, count in bankHits.items())
        failures.append(
            f"DISCOVERY: sender {sender} carries a txn signature ({detail}) but "
            f"is registered to no bank -- its transactions are being counted as "
            f"OTHER and never parsed"
        )

    return failures


def collectMetrics(report) -> dict:
    metrics = {
        "envelope_declared": report.envelope.declared,
        "envelope_actual": report.envelope.actual,
        "envelope_sms": report.envelope.sms,
        "envelope_mms": report.envelope.mms,
        "envelope_invalid": report.envelope.invalid,
        "duplicates": len(report.duplicates),
        "ambiguous_duplicates": report.ambiguousDuplicates,
        "diagnostics": len(report.diagnostics),
    }

    for bucket in ("ALL",) + MSG_BUCKETS:
        metrics[bucket] = report.count(bucket)

    for spec in REGISTRY:
        metrics[spec.skippedBucket] = report.count(spec.skippedBucket)

    metrics["ccTxns"] = len(report.ccTxns)
    metrics["vendors_cc_all"] = len(report.allVendors)
    metrics["debitTxns"] = len(report.debitTxns)
    metrics["vendors_MEZN"] = len(report.debitVendors)

    for bankId in REGISTRY.ccBankIds:
        bankTxns = [txn for txn in report.ccTxns if txn.bank == bankId]
        metrics[f"ccTxns_{bankId}"] = len(bankTxns)
        metrics[f"vendors_{bankId}"] = len({txn.vendor for txn in bankTxns})

    for txnType in DEBIT_TXN_TYPES:
        metrics[f"debit_{txnType}"] = sum(
            1 for txn in report.debitTxns if str(txn.txnType) == txnType
        )

    fblTxns = [txn for txn in report.ccTxns if txn.bank == "FBL"]
    for currency in ("PKR", "USD", "CAD"):
        metrics[f"fbl_{currency.lower()}"] = sum(
            1 for txn in fblTxns if txn.money.currency == currency
        )

    # Skip reasons, categorised. "26 messages were skipped" is a number;
    # "21 truncated, 5 with no amount" is a diagnosis, and a shift between
    # categories at a constant total is something a bare count cannot show.
    for reason, count in Counter(
        str(diagnostic.reason) for diagnostic in report.diagnostics
    ).items():
        metrics[f"diag_{reason}"] = count

    return metrics


def collectTotals(report) -> dict:
    """Exact per-bank, per-currency totals."""
    totals = defaultdict(Decimal)

    for txn in report.ccTxns:
        totals[(txn.bank, txn.money.currency)] += txn.money.amount

    for txn in report.debitTxns:
        totals[(txn.bank, txn.money.currency)] += txn.money.amount

    return dict(totals)


def checkInvariants(report, metrics: dict) -> list:
    """Properties that hold for *any* backup, whatever its counts."""
    failures = []

    conserved = sum(metrics[bucket] for bucket in MSG_BUCKETS)
    if conserved != metrics["ALL"]:
        failures.append(
            f"conservation identity: ALL={metrics['ALL']} but "
            f"HBL+FBL+SCB+MEZN+OTHER+DUP={conserved} -- a routing branch is "
            f"counting twice or not at all"
        )

    envelope = report.envelope
    accounted = envelope.sms + envelope.mms + envelope.invalid
    if accounted != envelope.actual:
        failures.append(
            f"envelope accounting: {envelope.actual} elements but "
            f"sms+mms+invalid={accounted}"
        )

    if not envelope.matchesDeclared:
        failures.append(
            f"the backup declares {envelope.declared} records but holds "
            f"{envelope.actual}"
        )

    def check(label, predicate, txns):
        violations = sum(1 for txn in txns if not predicate(txn))
        if violations:
            failures.append(f"{label}: {violations} of {len(txns)} txns violate this")

    allTxns = list(report.ccTxns) + list(report.debitTxns)

    check("vendor non-empty", lambda t: bool(t.vendor and t.vendor.strip()), allTxns)
    check("amount positive", lambda t: t.money.isPositive, allTxns)
    check("amount is exact Decimal", lambda t: isinstance(t.money.amount, Decimal), allTxns)
    check("currency well-formed", lambda t: len(t.money.currency) == 3, allTxns)
    # a stray astimezone(), or a missing tz stamp, shows up here
    check(
        "date tz-aware Asia/Karachi",
        lambda t: t.date is not None
        and t.date.tzinfo is not None
        and str(t.date.tzinfo) == "Asia/Karachi",
        allTxns,
    )
    check(
        "cc bank is a registered CC bank",
        lambda t: t.bank in REGISTRY.ccBankIds,
        report.ccTxns,
    )
    check(
        "debit bank is a registered debit bank",
        lambda t: t.bank in REGISTRY.idsForKind(TxnKind.ACCOUNT_DEBIT),
        report.debitTxns,
    )
    check(
        "debit txnType is a known type",
        lambda t: str(t.txnType) in DEBIT_TXN_TYPES,
        report.debitTxns,
    )

    # No diagnostic may reproduce anything from a message body. The type has no
    # field for one, so this guards against a detail string being interpolated
    # from the body in some future parser.
    for diagnostic in report.diagnostics:
        if len(diagnostic.detail) > 120:
            failures.append(
                f"diagnostic detail for {diagnostic.bank} is suspiciously long "
                f"({len(diagnostic.detail)} chars) -- is it carrying a body?"
            )
            break

    return failures


def _registryWithout(sender: str) -> BankRegistry:
    """The registry as it would be if one sender had never been declared."""
    specs = []

    for spec in REGISTRY:
        codes = tuple(code for code in spec.senderCodes if code != sender)
        specs.append(
            BankSpec(
                id=spec.id,
                name=spec.name,
                senderCodes=codes,
                txnKind=spec.txnKind,
                signal=spec.signal,
                extract=spec.extract,
                capabilities=spec.capabilities,
                notes=spec.notes,
            )
        )

    return BankRegistry(specs)


def checkRecoveredSender(backupPath: Path, report) -> list:
    """Measure exactly what declaring SCB's second short code recovered.

    Parses the same file a second time with that sender removed, and asserts the
    difference. This pins the value of the fix rather than merely asserting a
    total that would move for any number of reasons.
    """
    failures = []

    withoutSender = SmsBackupFileParser(registry=_registryWithout(EXPECTED_RECOVERED_SENDER)).parse(
        backupPath
    )

    recoveredTxns = len(report.ccTxns) - len(withoutSender.ccTxns)
    recoveredTotal = sum(
        (txn.money.amount for txn in report.ccTxns if txn.bank == "SCB"), Decimal(0)
    ) - sum(
        (txn.money.amount for txn in withoutSender.ccTxns if txn.bank == "SCB"),
        Decimal(0),
    )

    if recoveredTxns != EXPECTED_RECOVERED_TXNS:
        failures.append(
            f"sender {EXPECTED_RECOVERED_SENDER} contributes {recoveredTxns} txns, "
            f"expected {EXPECTED_RECOVERED_TXNS}"
        )

    if recoveredTotal != EXPECTED_RECOVERED_TOTAL:
        failures.append(
            f"sender {EXPECTED_RECOVERED_SENDER} contributes {recoveredTotal}, "
            f"expected {EXPECTED_RECOVERED_TOTAL}"
        )

    return failures, recoveredTxns, recoveredTotal


def main(argv: list) -> int:
    backupPath = (
        Path(argv[1]).resolve() if len(argv) > 1 else REPO_ROOT / REFERENCE_BACKUP_NAME
    )
    if not backupPath.is_file():
        print(f"FATAL: backup file not found: {backupPath}")
        print(
            "       pass one as an argument, or place the reference backup "
            f"({REFERENCE_BACKUP_NAME}) in the repo root"
        )
        return 2

    isReference = fileSha256(backupPath) == REFERENCE_BACKUP_SHA256

    report = SmsBackupFileParser().parse(backupPath)
    metrics = collectMetrics(report)
    totals = collectTotals(report)
    hits = discoverSignatureHits(backupPath)

    print("=" * 78)
    print(f"Repo verified   : {REPO_ROOT}")
    print(f"Backup file     : {backupPath.name}")
    print(
        "Backup identity : "
        + (
            "reference backup - expected values asserted"
            if isReference
            else "NOT the reference backup - values reported, invariants asserted"
        )
    )
    print(f"Duplicate policy: {report.duplicatePolicy}")
    print("=" * 78)

    failures = []

    # ---------------------------------------------------------------- discovery
    print()
    print("-- discovery: every sender scanned for every bank's txn signature --")
    for sender, bankHits in sorted(hits.items(), key=lambda item: -sum(item[1].values())):
        registered = REGISTRY.isRegistered(sender)
        owner = REGISTRY.specFor(sender).id if registered else "UNREGISTERED"
        detail = ", ".join(f"{bank} x{count}" for bank, count in bankHits.items())
        print(f"  sender {sender:<8} {owner:<13} {detail}")

    discoveryFailures = checkDiscovery(hits)
    failures.extend(discoveryFailures)
    print(f"  result: {'ok' if not discoveryFailures else 'MISS'}")

    # ---------------------------------------------------------------- values
    print()
    if isReference:
        print(f"{'metric':<28}{'expected':>12}{'actual':>12}   result")
        for name, expectedValue in EXPECTED.items():
            actualValue = metrics.get(name, 0)
            ok = actualValue == expectedValue
            if not ok:
                failures.append(f"{name}: expected {expectedValue}, got {actualValue}")
            print(
                f"{name:<28}{expectedValue:>12}{str(actualValue):>12}"
                f"   {'ok' if ok else 'MISS'}"
            )

        print()
        print(f"{'exact total':<28}{'expected':>16}{'actual':>16}   result")
        for key, expectedTotal in EXPECTED_TOTALS.items():
            actualTotal = totals.get(key, Decimal(0))
            ok = actualTotal == expectedTotal
            if not ok:
                failures.append(
                    f"total {key[0]}/{key[1]}: expected {expectedTotal}, "
                    f"got {actualTotal}"
                )
            label = f"{key[0]}/{key[1]}"
            print(
                f"{label:<28}{str(expectedTotal):>16}{str(actualTotal):>16}"
                f"   {'ok' if ok else 'MISS'}"
            )

        # any bank/currency pair the parsers produced that nothing expects
        for key in totals:
            if key not in EXPECTED_TOTALS:
                failures.append(
                    f"total {key[0]}/{key[1]}: {totals[key]} appeared, but no "
                    f"expectation is recorded for it"
                )

        print()
        recoveryFailures, recoveredTxns, recoveredTotal = checkRecoveredSender(
            backupPath, report
        )
        failures.extend(recoveryFailures)
        print(
            f"sender {EXPECTED_RECOVERED_SENDER} recovers {recoveredTxns} txns "
            f"totalling PKR {recoveredTotal}   "
            f"{'ok' if not recoveryFailures else 'MISS'}"
        )
    else:
        print(f"{'metric':<28}{'value':>12}")
        for name in EXPECTED:
            print(f"{name:<28}{str(metrics.get(name, 0)):>12}")
        print()
        for key in sorted(totals):
            label = f"{key[0]}/{key[1]}"
            print(f"{label:<28}{str(totals[key]):>16}")
        print()
        print("These are this backup's numbers, not a pass/fail. To gate on them,")
        print("record the derivation in CLAUDE.md's Reference numbers table, then")
        print("update EXPECTED, EXPECTED_TOTALS and REFERENCE_BACKUP_SHA256 here.")

    # ---------------------------------------------------------------- invariants
    invariantFailures = checkInvariants(report, metrics)
    failures.extend(invariantFailures)
    print()
    print(
        f"invariants (hold for any backup): "
        f"{'ok' if not invariantFailures else 'MISS'}"
    )

    print()
    if failures:
        print(f"RESULT: FAIL - {len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
