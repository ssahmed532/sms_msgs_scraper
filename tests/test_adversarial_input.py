"""What the tool does when the file it is given is not what it expects.

A backup is input from outside the program. It is normally the user's own export
from their own phone, but "normally fine" is not a validation strategy, and the
failure mode that matters here is not a crash -- it is a run that keeps going and
quietly reports the wrong number.
"""

import ast
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.sms_backup_file_parser import (
    BackupFileError,
    BackupLimits,
    SmsBackupFileParser,
)

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

HBL_TXN_BODY = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
)
FBL_TXN_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for PKR 8100 on "
    "20-Sep-23 01:17:16 PM at CHASE UP DEPT STORE   KARACHI        PK."
)
SCB_TXN_BODY = (
    "Dear Client, PKR 12,450.90 have been paid at PSO SERVICE STATION 7Karachi "
    "PAK on 29-09-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
    "PKR59563.45. SCBPL"
)
MEZN_TXN_BODY = (
    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 from A/C "
    "xxxxxx5602 KARACHI BRANCH on 15-Jun-24 at 09:05 Bal: PKR 1,234.00"
)


class AdversarialTestCase(unittest.TestCase):
    def _writeRaw(self, text: str) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        path = Path(tmpDir.name) / "backup.xml"
        path.write_text(text, encoding="utf-8")

        return path

    def _sms(self, address, body):
        sms = ET.Element("sms")
        sms.set("address", address)
        sms.set("body", body)
        sms.set("readable_date", "Oct 2, 2023 9:57:06 PM")

        return sms

    def _writeBackup(self, elements) -> Path:
        root = ET.Element("smses")
        root.set("count", str(len(elements)))
        root.extend(elements)

        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        path = Path(tmpDir.name) / "backup.xml"
        ET.ElementTree(root).write(path, encoding="utf-8")

        return path


class TestHostileXml(AdversarialTestCase):
    def test_a_document_type_declaration_is_refused(self):
        """The billion-laughs family needs a DTD to set the entities up.

        An SMS Backup & Restore file never carries one, so refusing them closes
        the whole class rather than mitigating it.
        """
        hostile = (
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE smses [\n"
            '  <!ENTITY a "aaaaaaaaaa">\n'
            '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
            "]>\n"
            '<smses count="1"><sms address="4250" body="&b;"/></smses>'
        )

        with self.assertRaises(BackupFileError) as caught:
            SmsBackupFileParser().parse(self._writeRaw(hostile))

        self.assertIn("document type declaration", str(caught.exception))

    def test_a_file_over_the_size_limit_is_refused_before_being_read(self):
        path = self._writeBackup([self._sms("4250", HBL_TXN_BODY)])

        parser = SmsBackupFileParser(limits=BackupLimits(maxBytes=10))

        with self.assertRaises(BackupFileError) as caught:
            parser.parse(path)

        self.assertIn("byte limit", str(caught.exception))

    def test_a_file_over_the_element_limit_is_refused(self):
        path = self._writeBackup([self._sms("4250", HBL_TXN_BODY)] * 5)

        parser = SmsBackupFileParser(limits=BackupLimits(maxElements=2))

        with self.assertRaises(BackupFileError) as caught:
            parser.parse(path)

        self.assertIn("elements", str(caught.exception))

    def test_a_deeply_nested_document_is_refused(self):
        depth = 200
        nested = "".join(f"<n{index}>" for index in range(depth))
        closing = "".join(f"</n{index}>" for index in reversed(range(depth)))
        path = self._writeRaw(f'<smses count="1">{nested}{closing}</smses>')

        with self.assertRaises(BackupFileError) as caught:
            SmsBackupFileParser().parse(path)

        self.assertIn("deep", str(caught.exception))

    def test_truncated_xml_is_a_clear_error_not_a_partial_result(self):
        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(self._writeRaw('<smses count="1"><sms'))

    def test_an_empty_file_is_a_clear_error(self):
        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(self._writeRaw(""))

    def test_the_file_handle_is_released_even_when_parsing_fails(self):
        """On Windows a leaked handle locks the file for the process lifetime."""
        path = self._writeRaw('<contacts count="0"></contacts>')

        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(path)

        # if the handle leaked, this raises PermissionError on Windows
        path.unlink()
        self.assertFalse(path.exists())


class TestOneBadRecordCostsNothingElse(AdversarialTestCase):
    """H2, in its end-to-end form.

    One malformed HBL record used to abort the entire run through an assertion
    on external input.
    """

    def test_a_malformed_hbl_record_among_four_banks_loses_only_itself(self):
        malformed = self._sms(
            "4250",
            "Dear Customer, Your HBL CreditCard (ending with 8526) has been "
            "charged at BROKEN SHOP for PKR-1,2,3.00 on 01/Oct/2023.",
        )

        report = SmsBackupFileParser().parse(
            self._writeBackup(
                [
                    self._sms("4250", HBL_TXN_BODY),
                    malformed,
                    self._sms("8756", FBL_TXN_BODY),
                    self._sms("7220", SCB_TXN_BODY),
                    self._sms("8079", MEZN_TXN_BODY),
                ]
            )
        )

        self.assertEqual(len(report.ccTxns), 3)
        self.assertEqual(len(report.debitTxns), 1)
        self.assertEqual(report.count("HBL_SKIPPED"), 1)
        self.assertEqual(len(report.diagnostics), 1)

    def test_a_backup_of_nothing_but_malformed_records_still_completes(self):
        broken = [
            self._sms("4250", "Dear Customer, Your HBL CreditCard has been charged at"),
            self._sms("7220", "Dear Client, PKR .00 have been paid at NOWHERE "),
            self._sms("8079", "PKR 1.00 sent to SOMEONE via NewChannel dated x"),
        ]

        report = SmsBackupFileParser().parse(self._writeBackup(broken))

        self.assertEqual(len(report.ccTxns), 0)
        self.assertEqual(report.count("ALL"), 3)

    def test_an_element_with_no_attributes_at_all_is_isolated(self):
        report = SmsBackupFileParser().parse(
            self._writeBackup([ET.Element("sms"), self._sms("4250", HBL_TXN_BODY)])
        )

        self.assertEqual(report.envelope.invalid, 1)
        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(
            report.diagnostics[0].reason, SkipReason.MALFORMED_RECORD
        )


class TestUntrustedFieldContent(AdversarialTestCase):
    """A vendor name is attacker-influenced text from an SMS."""

    def test_control_characters_never_reach_a_rendered_cell(self):
        from sms_msgs_scraper.render.console_ui import vendorText

        hostile = "EVIL\x1b[31mSHOP\x07\x00"

        rendered = vendorText(hostile).plain

        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertNotIn("\x00", rendered)
        self.assertIn("SHOP", rendered)

    def test_control_characters_are_stripped_from_machine_output(self):
        from sms_msgs_scraper.render.machine import csvSafe

        self.assertNotIn("\x1b", csvSafe("EVIL\x1b[31mSHOP"))

    def test_a_formula_leading_field_is_defused_for_spreadsheets(self):
        from sms_msgs_scraper.render.machine import csvSafe

        for payload in ("=cmd|calc", "+1+1", "-1+1", "@SUM(A1)"):
            with self.subTest(payload=payload):
                self.assertTrue(csvSafe(payload).startswith("'"))

    def test_an_ordinary_vendor_is_left_alone(self):
        from sms_msgs_scraper.render.machine import csvSafe

        self.assertEqual(csvSafe("M&M'S PHARMACY"), "M&M'S PHARMACY")

    def test_xml_itself_blocks_a_raw_escape_byte_in_a_body(self):
        """The outer layer of the defence, recorded so it is not relied on.

        XML 1.0 forbids most control characters outright, so a raw escape byte
        cannot travel in an attribute value at all -- a backup carrying one is
        rejected as malformed before any field is read. The sanitising above is
        therefore defence in depth rather than the only barrier, and it is what
        would still hold if a field ever reached a renderer by another route.
        """
        body = HBL_TXN_BODY.replace("IMTIAZ SUPER MARKET", "EVIL\x1b[31mSHOP")

        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(self._writeBackup([self._sms("4250", body)]))

    def test_a_hostile_vendor_is_neutralised_at_the_render_boundary(self):
        from datetime import datetime
        from decimal import Decimal

        from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
        from sms_msgs_scraper.domain.money import Money
        from sms_msgs_scraper.domain.tz import DEFAULT_TZ
        from sms_msgs_scraper.render.tables import ccTxnsTable

        txn = CreditCardTxnDC(
            money=Money(Decimal("100.00"), "PKR"),
            date=datetime(2024, 1, 1, tzinfo=DEFAULT_TZ),
            vendor="EVIL\x1b[31mSHOP",
            bank="HBL",
        )

        table = ccTxnsTable([txn])
        cell = table.columns[4]._cells[0]

        self.assertNotIn("\x1b", cell.plain)
        self.assertIn("SHOP", cell.plain)


class TestNoAssertionsOnInput(unittest.TestCase):
    """H2's structural guard.

    An `assert` disappears under `python -O`. Any assertion whose truth depends
    on the contents of a message is therefore a behaviour that changes with an
    interpreter flag -- which is exactly how the old HBL path produced a
    negative amount instead of aborting.
    """

    def test_no_module_under_src_contains_an_assert_statement(self):
        offenders = []

        for path in sorted(SRC_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert):
                    offenders.append(f"{path.relative_to(SRC_DIR)}:{node.lineno}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
