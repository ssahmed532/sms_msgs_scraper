"""Verify the parsers against a real SMS backup file.

The unit suite proves the parsers behave correctly on hand-built messages. This
script proves they still behave correctly on ~4,700 real ones — which is where
a regex change that passes every unit test quietly loses 40 transactions shows
up. Run it after any change to a parser, to the routing in
`SmsBackupFileParser`, or to the dedup identity.

    uv run scripts/verify_against_backup.py [backup.xml]

With no argument it looks for the reference backup next to the repo root. It
imports the parsers from the `src/` directory beside this script, so running it
inside a git worktree verifies *that* worktree.

Two kinds of check, and the difference matters:

  * **Invariants** hold for any backup, and are always asserted. Every counted
    msg lands in exactly one bucket (the conservation identity), and every txn
    has a non-empty vendor, a positive amount, a timezone-aware Asia/Karachi
    date, a known bank and — for debits — a known txn type. These catch the
    failures that counts cannot: 583 FBL txns with empty vendors or naive dates
    would satisfy every expected total below.

  * **Expected counts** are specific to one backup file, identified by its
    SHA-256. Run against that file, they are asserted exactly. Run against any
    other backup they cannot mean anything, so the script reports the metrics
    instead and asserts only the invariants — still the useful half when you
    take a fresh backup.

It prints counts only, never message bodies, and the parsers' own per-msg
warnings are captured and reported as a line count: an SMS backup is personal
financial data, and this output is the kind of thing that ends up pasted into a
bug report.

**Anti-drift rule.** If an expected count misses, the fix belongs in the code —
or in a re-derivation of the expectation from the corpus, recorded in
`MULTI_BANK_PARSERS_PLAN.md` with the derivation that produced it. Never edit a
number here to match what the code happened to print; that turns the one check
that would have caught a regression into a rubber stamp.
"""

import hashlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The backup the expected counts below were derived from. Identified by content
# rather than filename so that a renamed copy is still recognized, and a
# different backup is never silently measured against the wrong numbers.
REFERENCE_BACKUP_NAME = "sms-20251011130814.xml"
REFERENCE_BACKUP_SHA256 = (
    "c0792da646c9f06b1d83bf02f80930ac8e6e27b16d616e84a762537eb30668f3"
)

# Derived 2026-08-26 from the reference backup. The msg counts are *post-dedup*
# runtime values: the parser dedups before parsing, so grepping the raw XML
# gives higher numbers for every bank and is not comparable.
EXPECTED = {
    # msg routing
    "ALL": 4665,
    "HBL": 798,
    "FBL": 674,
    "SCB": 614,
    "MEZN": 1228,
    "OTHER": 1116,
    "DUP": 235,
    # msgs carrying a txn signal that could not be parsed. SCB's 26 are the
    # bank's own malformed msgs: 21 truncated mid-body, 5 carrying a literal
    # "PKR .00" with no amount. A change here means something moved.
    "FBL_SKIPPED": 0,
    "SCB_SKIPPED": 26,
    "MEZN_SKIPPED": 0,
    # credit card txns
    "ccTxns": 1678,
    "ccTxns_HBL": 717,
    "ccTxns_FBL": 583,
    "ccTxns_SCB": 378,
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
    "vendors_SCB": 94,
    "vendors_MEZN": 189,
    "vendors_cc_all": 357,
    # FBL is the only bank in the corpus sending more than one currency
    "fbl_pkr": 574,
    "fbl_usd": 8,
    "fbl_cad": 1,
}

MSG_BUCKETS = ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")
DEBIT_TXN_TYPES = ("card_purchase", "atm_withdrawal", "account_debit", "funds_transfer")
CC_BANKS = ("HBL", "FBL", "SCB")


def fileSha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def parseBackup(backupPath: Path):
    """Import the parsers from the src/ beside this script, and run them over
    the backup.

    Resolving src/ relative to this file (rather than the working directory)
    means running the script from inside a git worktree verifies that
    worktree's code, not whichever checkout happens to be on sys.path.
    """
    srcDir = REPO_ROOT / "src"
    if not srcDir.is_dir():
        raise SystemExit(f"FATAL: no src/ directory found at {srcDir}")

    sys.path.insert(0, str(srcDir))
    from sms_backup_file_parser import SmsBackupFileParser

    smsParser = SmsBackupFileParser()
    smsParser.loadFromSmsBackupFile(backupPath)

    # Swallow the parsers' own output: their per-msg warnings can name the msg,
    # and this script reports counts only.
    captured = io.StringIO()
    with redirect_stdout(captured):
        smsParser.parseMessages()

    return smsParser, len(captured.getvalue().splitlines())


def collectMetrics(smsParser) -> dict:
    metrics = {
        bucket: smsParser.msgCounts[bucket]
        for bucket in ("ALL",) + MSG_BUCKETS + ("FBL_SKIPPED", "SCB_SKIPPED", "MEZN_SKIPPED")
    }

    metrics["ccTxns"] = len(smsParser.ccTxns)
    metrics["vendors_cc_all"] = len({txn.vendor for txn in smsParser.ccTxns})
    metrics["debitTxns"] = len(smsParser.debitTxns)
    metrics["vendors_MEZN"] = len({txn.vendor for txn in smsParser.debitTxns})

    for bank in CC_BANKS:
        bankTxns = [txn for txn in smsParser.ccTxns if txn.bank == bank]
        metrics[f"ccTxns_{bank}"] = len(bankTxns)
        metrics[f"vendors_{bank}"] = len({txn.vendor for txn in bankTxns})

    for txnType in DEBIT_TXN_TYPES:
        metrics[f"debit_{txnType}"] = sum(
            1 for txn in smsParser.debitTxns if str(txn.txnType) == txnType
        )

    fblTxns = [txn for txn in smsParser.ccTxns if txn.bank == "FBL"]
    for currency in ("PKR", "USD", "CAD"):
        metrics[f"fbl_{currency.lower()}"] = sum(
            1 for txn in fblTxns if txn.amountTuple.currency == currency
        )

    return metrics


def checkInvariants(smsParser, metrics: dict) -> list:
    """Assert the properties that hold for *any* backup, whatever its counts."""
    failures = []

    conserved = sum(metrics[bucket] for bucket in MSG_BUCKETS)
    if conserved != metrics["ALL"]:
        failures.append(
            f"conservation identity: ALL={metrics['ALL']} but "
            f"HBL+FBL+SCB+MEZN+OTHER+DUP={conserved} - a routing branch is "
            f"counting twice or not at all"
        )

    def check(label, predicate, txns):
        violations = sum(1 for txn in txns if not predicate(txn))
        if violations:
            failures.append(f"{label}: {violations} of {len(txns)} txns violate this")

    for label, txns in (("cc", smsParser.ccTxns), ("debit", smsParser.debitTxns)):
        check(f"{label} vendor non-empty", lambda t: bool(t.vendor and t.vendor.strip()), txns)
        check(f"{label} amount > 0", lambda t: t.amountTuple.amount > 0, txns)
        check(f"{label} currency non-empty", lambda t: bool(t.amountTuple.currency), txns)
        # a stray astimezone(), or a missing tz stamp, shows up here
        check(
            f"{label} date tz-aware Asia/Karachi",
            lambda t: t.date is not None
            and t.date.tzinfo is not None
            and str(t.date.tzinfo) == "Asia/Karachi",
            txns,
        )

    check("cc bank is a known CC bank", lambda t: t.bank in CC_BANKS, smsParser.ccTxns)
    check("debit bank == MEZN", lambda t: t.bank == "MEZN", smsParser.debitTxns)
    check(
        "debit txnType is a known type",
        lambda t: str(t.txnType) in DEBIT_TXN_TYPES,
        smsParser.debitTxns,
    )

    return failures


def main(argv: list) -> int:
    backupPath = (
        Path(argv[1]).resolve()
        if len(argv) > 1
        else REPO_ROOT / REFERENCE_BACKUP_NAME
    )
    if not backupPath.is_file():
        print(f"FATAL: backup file not found: {backupPath}")
        print(
            "       pass one as an argument, or place the reference backup "
            f"({REFERENCE_BACKUP_NAME}) in the repo root"
        )
        return 2

    isReference = fileSha256(backupPath) == REFERENCE_BACKUP_SHA256
    smsParser, warningLines = parseBackup(backupPath)
    metrics = collectMetrics(smsParser)

    print("=" * 72)
    print(f"Repo verified   : {REPO_ROOT}")
    print(f"Backup file     : {backupPath.name}")
    print(
        f"Backup identity : {'reference backup - expected counts asserted' if isReference else 'NOT the reference backup - counts reported, invariants asserted'}"
    )
    print(f"Parser output lines suppressed (may name msgs): {warningLines}")
    print("=" * 72)

    failures = []

    if isReference:
        print(f"{'metric':<26}{'expected':>10}{'actual':>10}   result")
        for name, expectedValue in EXPECTED.items():
            actualValue = metrics.get(name)
            ok = actualValue == expectedValue
            if not ok:
                failures.append(f"{name}: expected {expectedValue}, got {actualValue}")
            print(
                f"{name:<26}{expectedValue:>10}{str(actualValue):>10}"
                f"   {'ok' if ok else 'MISS'}"
            )
    else:
        print(f"{'metric':<26}{'value':>10}")
        for name in EXPECTED:
            print(f"{name:<26}{str(metrics.get(name)):>10}")
        print()
        print("These are this backup's numbers, not a pass/fail. To gate on them,")
        print("record the derivation in MULTI_BANK_PARSERS_PLAN.md and update both")
        print("EXPECTED and REFERENCE_BACKUP_SHA256 in this file.")

    invariantFailures = checkInvariants(smsParser, metrics)
    failures.extend(invariantFailures)
    print()
    print(f"invariants (hold for any backup): {'ok' if not invariantFailures else 'MISS'}")

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
