"""Behaviour a script depends on, and the `python -O` release gate.

These read the shared synthetic fixture, and deliberately assert **no count
taken from it**. Exact fixture numbers belong in exactly one place,
`test_synthetic_corpus.py`. A second copy here bought no coverage and made
every fixture edit fail in a file about parity and stream contracts instead:
adding one merchant to the fixture turned into two red tests whose subject
was neither merchants nor vendors.

If a fixture change makes something here fail, the fix is to stop asserting
the number, not to update it.
"""

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "synthetic_backup.xml"

# Prints everything an assertion could plausibly change, as one JSON line.
PARITY_PROGRAM = textwrap.dedent(
    """
    import json
    from collections import Counter
    from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

    report = SmsBackupFileParser().parse(%r)

    print(json.dumps({
        "counts": dict(sorted(report.counts.items())),
        "envelope": report.envelope.toDict(),
        "ccTxns": [txn.toDict() for txn in report.ccTxns],
        "debitTxns": [txn.toDict() for txn in report.debitTxns],
        "diagnostics": sorted(
            f"{d.bank}/{d.reason}" for d in report.diagnostics
        ),
        "duplicates": len(report.duplicates),
        "ambiguous": report.ambiguousDuplicates,
    }, sort_keys=True))
    """
)


def runProgram(program: str, optimized: bool):
    args = [sys.executable]
    if optimized:
        args.append("-O")
    args.extend(["-c", program])

    return subprocess.run(
        args,
        cwd=SRC_DIR,
        capture_output=True,
        text=True,
        # Windows would otherwise decode the child's output as cp1252, and the
        # tool writes UTF-8 (box drawing, bullets) on stderr.
        encoding="utf-8",
        check=True,
    )


class TestOptimizedParity(unittest.TestCase):
    """H2's release gate.

    `python -O` strips every `assert`. When the HBL parser asserted on message
    contents, that flag changed what the program *computed*: normally a
    malformed message aborted the run with an AssertionError, while under `-O`
    the same message produced a transaction whose amount was the `-1.2345`
    sentinel -- a *negative* number, which summed into a monthly total and
    silently reduced reported spending.

    Identical output under both is the only way to be sure no behaviour is
    hiding inside an assertion.
    """

    def test_the_parse_is_byte_identical_with_assertions_stripped(self):
        program = PARITY_PROGRAM % str(FIXTURE)

        normal = runProgram(program, optimized=False)
        optimized = runProgram(program, optimized=True)

        self.assertEqual(normal.stdout, optimized.stdout)

    def test_the_parity_program_actually_produced_transactions(self):
        """Guards the test above from passing on two empty results.

        A lower bound and not an exact count, on purpose: "there was something
        to compare" is the whole job here, and how much of it there was is
        pinned in `test_synthetic_corpus.py`.
        """
        payload = json.loads(runProgram(PARITY_PROGRAM % str(FIXTURE), False).stdout)

        self.assertGreater(len(payload["ccTxns"]), 0)
        self.assertGreater(len(payload["debitTxns"]), 0)

    def test_no_amount_is_ever_negative_under_either_mode(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                payload = json.loads(
                    runProgram(PARITY_PROGRAM % str(FIXTURE), optimized).stdout
                )

                for txn in payload["ccTxns"] + payload["debitTxns"]:
                    self.assertFalse(txn["amount"].startswith("-"), txn)


class TestMalformedRecordParity(unittest.TestCase):
    def test_a_malformed_record_behaves_the_same_with_and_without_O(self):
        """The exact shape that used to diverge: an ungrouped-thousands amount
        was fine, but a malformed one aborted normally and produced a negative
        amount under -O."""
        program = textwrap.dedent(
            """
            import json, tempfile, pathlib
            import xml.etree.ElementTree as ET
            from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

            root = ET.Element("smses"); root.set("count", "2")
            good = ET.SubElement(root, "sms")
            good.set("address", "4250")
            good.set("readable_date", "Oct 1, 2023 1:00:00 PM")
            good.set("body", "Dear Customer, Your HBL CreditCard (ending with "
                             "8526) has been charged at GOOD SHOP for "
                             "PKR-100.00 on 01/Oct/2023.")
            bad = ET.SubElement(root, "sms")
            bad.set("address", "4250")
            bad.set("readable_date", "Oct 2, 2023 1:00:00 PM")
            bad.set("body", "Dear Customer, Your HBL CreditCard (ending with "
                            "8526) has been charged at BAD SHOP for "
                            "PKR-1,2,3.00 on 02/Oct/2023.")

            d = pathlib.Path(tempfile.mkdtemp()) / "b.xml"
            ET.ElementTree(root).write(d, encoding="utf-8")

            report = SmsBackupFileParser().parse(d)
            print(json.dumps({
                "txns": [t.toDict() for t in report.ccTxns],
                "skipped": report.count("HBL_SKIPPED"),
            }, sort_keys=True))
            """
        )

        normal = runProgram(program, optimized=False)
        optimized = runProgram(program, optimized=True)

        self.assertEqual(normal.stdout, optimized.stdout)

        payload = json.loads(normal.stdout)
        self.assertEqual(len(payload["txns"]), 1)
        self.assertEqual(payload["skipped"], 1)
        self.assertEqual(payload["txns"][0]["amount"], "100.00")


class TestStdoutIsAContract(unittest.TestCase):
    """Piping the tool must yield data and nothing else."""

    def _run(self, args):
        return subprocess.run(
            [sys.executable, "-m", "sms_msgs_scraper"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_json_on_stdout_parses_even_with_diagnostics_on_stderr(self):
        result = self._run(
            ["--format", "json", str(FIXTURE), "list_all_cc_txns"]
        )

        self.assertEqual(result.returncode, 0)
        # stderr carries the skipped-message warnings for this fixture
        self.assertIn("WARNING", result.stderr)
        payload = json.loads(result.stdout)
        # The envelope's own invariant, rather than a second copy of the
        # fixture's transaction count: `count` has to describe the rows it was
        # shipped with. That is the thing a consumer actually relies on, and
        # nothing checked it before.
        self.assertEqual(payload["count"], len(payload["rows"]))
        self.assertGreater(payload["count"], 0)

    def test_quiet_leaves_stderr_empty_on_a_clean_command(self):
        result = self._run(
            ["--quiet", "--format", "csv", str(FIXTURE), "list_all_vendors"]
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_help_reads_nothing_and_says_nothing_on_stderr(self):
        result = self._run([str(FIXTURE), "list_all_cc_txns", "--help"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_strict_exits_three_on_this_fixture(self):
        """The fixture deliberately contains four unparseable messages."""
        result = self._run(["--strict", str(FIXTURE), "list_all_cc_txns"])

        self.assertEqual(result.returncode, 3)

    def test_a_missing_file_is_a_usage_error(self):
        result = self._run([str(REPO_ROOT / "no-such-backup.xml"), "list_all_cc_txns"])

        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
