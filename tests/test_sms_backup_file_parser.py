"""Tests for the orchestrator: routing, deduplication, envelope accounting and
the conservation identity.
"""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path

from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.domain.report import DuplicatePolicy
from sms_msgs_scraper.sms_backup_file_parser import BackupFileError, SmsBackupFileParser

MSG_BUCKETS = ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")


class BackupTestCase(unittest.TestCase):
    """Builds a backup file out of message elements and parses it."""

    def _createSms(self, address: str, body: str) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("protocol", "0")
        sms.set("address", address)
        sms.set("date", "1696265826791")
        sms.set("type", "1")
        sms.set("read", "1")
        sms.set("body", body)
        sms.set("readable_date", "Oct 2, 2023 9:57:06 PM")

        return sms

    def _createHblTxnSms(
        self,
        vendor: str = "IMTIAZ SUPER MARKET",
        amount: str = "25,170.49",
        txnDate: str = "01/Oct/2023",
    ) -> ET.Element:
        body = (
            f"Dear Customer, Your HBL CreditCard (ending with 8526) has been "
            f"charged at {vendor} for PKR-{amount} on {txnDate}."
        )

        return self._createSms("4250", body)

    def _createFblTxnSms(
        self,
        # padded to the real 22-char vendor column, and deliberately a
        # different merchant from the HBL fixture's so that a vendor-set
        # assertion can tell the two banks' contributions apart
        vendor: str = "CHASE UP DEPT STORE   ",
        amount: str = "8100",
        currency: str = "PKR",
        txnDate: str = "20-Sep-23 01:17:16 PM",
    ) -> ET.Element:
        body = (
            f"Dear JOHN DOE, your FBL Card  has been charged for "
            f"{currency} {amount} on {txnDate} at {vendor} KARACHI        PK."
        )

        return self._createSms("8756", body)

    def _createScbTxnSms(
        self,
        vendor: str = "PSO SERVICE STATION 7Karachi PAK",
        amount: str = "12,450.90",
        txnDate: str = "29-09-23",
        cardMask: str = "5452xxxxxxxx1280",
        sender: str = "7220",
    ) -> ET.Element:
        body = (
            f"Dear Client, PKR {amount} have been paid at {vendor} on "
            f"{txnDate} using Credit Card no {cardMask}. Avail Limit "
            f"PKR59563.45. SCBPL"
        )

        return self._createSms(sender, body)

    def _createMeznAtmSms(
        self,
        vendor: str = "MEEZAN ATM DHA PHASE 6",
        amount: str = "20,000.00",
        txnDate: str = "15-Jun-24",
        txnTime: str = "09:05",
    ) -> ET.Element:
        body = (
            f"PKR {amount} cash withdrawn from {vendor} from A/C xxxxxx5602 "
            f"KARACHI BRANCH on {txnDate} at {txnTime} Bal: PKR 1,234.00"
        )

        return self._createSms("8079", body)

    def _writeBackup(self, msgElements: list, count=None, rootTag="smses") -> Path:
        root = ET.Element(rootTag)
        if count is not None:
            root.set("count", str(count))
        else:
            root.set("count", str(len(msgElements)))
        root.extend(msgElements)

        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupFilepath = Path(tmpDir.name) / "sms_backup.xml"
        ET.ElementTree(root).write(backupFilepath, encoding="utf-8")

        return backupFilepath

    def _parseBackup(self, msgElements: list, **kwargs):
        return SmsBackupFileParser(**kwargs).parse(self._writeBackup(msgElements))

    def assertConserved(self, report):
        """ALL == HBL + FBL + SCB + MEZN + OTHER + DUP.

        Every counted message lands in exactly one bucket. If this stops
        holding, a routing branch is counting twice or not at all.
        """
        self.assertEqual(
            report.count("ALL"),
            sum(report.count(bucket) for bucket in MSG_BUCKETS),
        )


class TestRoutingAndCounts(BackupTestCase):
    def test_an_hbl_txn_reaches_the_report(self):
        report = self._parseBackup([self._createHblTxnSms()])

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.allVendors, {"IMTIAZ SUPER MARKET"})
        self.assertEqual(report.count("HBL"), 1)
        self.assertEqual(report.count("ALL"), 1)

    def test_distinct_msgs_are_not_mistaken_for_duplicates(self):
        report = self._parseBackup(
            [
                self._createHblTxnSms(vendor="IMTIAZ SUPER MARKET"),
                self._createHblTxnSms(
                    vendor="CHASE UP", amount="9,999.00", txnDate="02/Oct/2023"
                ),
            ]
        )

        self.assertEqual(len(report.ccTxns), 2)
        self.assertEqual(report.allVendors, {"IMTIAZ SUPER MARKET", "CHASE UP"})

    def test_mms_elements_are_skipped_not_counted_as_messages(self):
        report = self._parseBackup([ET.Element("mms"), self._createHblTxnSms()])

        self.assertEqual(report.count("ALL"), 1)
        self.assertEqual(report.envelope.mms, 1)
        self.assertEqual(report.envelope.sms, 1)

    def test_a_non_txn_bank_msg_is_counted_but_not_parsed(self):
        otp = self._createSms(
            "4250",
            "648975 is your One Time Password (OTP) for the internet "
            "transaction on HBL Card ending with 0077.",
        )

        report = self._parseBackup([otp])

        self.assertEqual(report.count("HBL"), 1)
        self.assertEqual(len(report.ccTxns), 0)
        self.assertEqual(report.count("HBL_SKIPPED"), 0)

    def test_a_mixed_bank_backup_routes_every_msg_to_the_right_store(self):
        report = self._parseBackup(
            [
                self._createHblTxnSms(),
                self._createFblTxnSms(),
                self._createScbTxnSms(),
                self._createMeznAtmSms(),
            ]
        )

        self.assertEqual(len(report.ccTxns), 3)
        self.assertEqual(len(report.debitTxns), 1)
        self.assertEqual(
            Counter(txn.bank for txn in report.ccTxns),
            Counter({"HBL": 1, "FBL": 1, "SCB": 1}),
        )
        for bank in ("HBL", "FBL", "SCB", "MEZN"):
            self.assertEqual(report.count(bank), 1)
        self.assertEqual(report.count("OTHER"), 0)
        self.assertConserved(report)

    def test_banks_do_not_claim_each_others_msgs(self):
        """Each bank's body sent from each bank's short code.

        Only the matching pairing may produce a transaction; the rest must be
        counted and left alone.
        """
        report = self._parseBackup(
            [
                self._createHblTxnSms(),
                self._createFblTxnSms(),
                self._createScbTxnSms(),
                self._createMeznAtmSms(),
            ]
        )

        self.assertEqual(len(report.ccTxns) + len(report.debitTxns), 4)
        for bank in ("FBL", "SCB", "MEZN"):
            self.assertEqual(report.count(f"{bank}_SKIPPED"), 0)

    def test_an_unrecognized_sender_lands_in_other(self):
        report = self._parseBackup(
            [self._createSms("99999", "Buy two get one free!")]
        )

        self.assertEqual(report.count("OTHER"), 1)
        self.assertConserved(report)


class TestConservation(BackupTestCase):
    def test_the_identity_holds_across_a_varied_backup(self):
        report = self._parseBackup(
            [
                self._createHblTxnSms(),
                self._createHblTxnSms(),  # duplicate
                self._createFblTxnSms(),
                self._createScbTxnSms(),
                self._createMeznAtmSms(),
                self._createSms("99999", "Promo!"),
                self._createSms("99999", "Promo!"),  # duplicate
                ET.Element("mms"),
            ]
        )

        self.assertConserved(report)
        self.assertEqual(report.count("DUP"), 2)


class TestDeduplication(BackupTestCase):
    def test_two_identical_msgs_collapse_into_one_txn(self):
        report = self._parseBackup(
            [self._createHblTxnSms(), self._createHblTxnSms()]
        )

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.count("HBL"), 1)
        self.assertEqual(report.count("DUP"), 1)
        self.assertEqual(report.count("ALL"), 2)

    def test_a_whitespace_only_variant_is_still_a_duplicate(self):
        first = self._createHblTxnSms()
        second = self._createHblTxnSms()
        second.set("body", f"  {second.attrib['body']}  ")

        report = self._parseBackup([first, second])

        self.assertEqual(report.count("DUP"), 1)
        self.assertEqual(len(report.ccTxns), 1)

    def test_a_duplicate_from_an_unknown_sender_counts_as_dup_not_other(self):
        """It used to fail its branch condition and fall through into OTHER."""
        promo = self._createSms("99999", "Buy two get one free!")

        report = self._parseBackup([promo, self._createSms("99999", "Buy two get one free!")])

        self.assertEqual(report.count("DUP"), 1)
        self.assertEqual(report.count("OTHER"), 1)
        self.assertConserved(report)

    def test_an_identical_body_from_two_senders_is_not_a_duplicate(self):
        """The identity includes the sender.

        Without it, an unrelated message could suppress a later bank message
        that merely happened to repeat its text -- which silently discarded 23
        messages in the reference backup, 4 of them from bank short codes.
        """
        body = "Your verification code is 1234"

        report = self._parseBackup(
            [self._createSms("11111", body), self._createSms("22222", body)]
        )

        self.assertEqual(report.count("DUP"), 0)
        self.assertEqual(report.count("OTHER"), 2)

    def test_a_bank_txn_is_not_suppressed_by_an_unrelated_sender(self):
        hbl = self._createHblTxnSms()
        impostor = self._createSms("99999", hbl.attrib["body"])

        report = self._parseBackup([impostor, hbl])

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.count("HBL"), 1)
        self.assertEqual(report.count("DUP"), 0)


class TestDuplicatePolicy(BackupTestCase):
    """M5: collapsing two identical messages is a financial judgement, and a
    total that depends on one should say so."""

    def test_the_default_policy_collapses_and_records_provenance(self):
        report = self._parseBackup(
            [self._createHblTxnSms(), self._createHblTxnSms()]
        )

        self.assertEqual(report.duplicatePolicy, DuplicatePolicy.EXACT)
        self.assertEqual(len(report.duplicates), 1)
        self.assertEqual(report.duplicates[0].sender, "4250")
        self.assertEqual(report.duplicates[0].firstIndex, 0)
        self.assertEqual(report.duplicates[0].duplicateIndex, 1)

    def test_a_date_only_bank_duplicate_is_flagged_ambiguous(self):
        """HBL alerts carry no time of day, so a genuine repeat is possible."""
        report = self._parseBackup(
            [self._createHblTxnSms(), self._createHblTxnSms()]
        )

        self.assertTrue(report.duplicates[0].ambiguous)
        self.assertEqual(report.ambiguousDuplicates, 1)

    def test_a_timestamped_bank_duplicate_is_not_ambiguous(self):
        """FBL alerts carry a time to the second, so it provably is the same
        transaction."""
        report = self._parseBackup(
            [self._createFblTxnSms(), self._createFblTxnSms()]
        )

        self.assertFalse(report.duplicates[0].ambiguous)
        self.assertEqual(report.ambiguousDuplicates, 0)

    def test_the_none_policy_keeps_every_message(self):
        """An upper bound on spending, for someone who would rather have one."""
        report = self._parseBackup(
            [self._createHblTxnSms(), self._createHblTxnSms()],
            duplicatePolicy=DuplicatePolicy.NONE,
        )

        self.assertEqual(len(report.ccTxns), 2)
        self.assertEqual(report.count("DUP"), 0)
        self.assertEqual(report.count("HBL"), 2)
        self.assertConserved(report)

    def test_the_policy_travels_with_the_numbers(self):
        report = self._parseBackup(
            [self._createHblTxnSms()], duplicatePolicy=DuplicatePolicy.REVIEW
        )

        self.assertEqual(report.duplicatePolicy, DuplicatePolicy.REVIEW)


class TestOneShotParsing(BackupTestCase):
    """M3: the parser used to be reusable, and reuse broke the accounting."""

    def test_reusing_a_parser_is_refused(self):
        """Parsing twice drove DUP from 235 to 4,900 with ALL unchanged."""
        backupPath = self._writeBackup([self._createHblTxnSms()])
        parser = SmsBackupFileParser()
        parser.parse(backupPath)

        with self.assertRaises(BackupFileError):
            parser.parse(backupPath)

    def test_a_second_file_needs_a_second_parser_and_the_counts_are_clean(self):
        firstPath = self._writeBackup([self._createHblTxnSms()])
        secondPath = self._writeBackup([self._createHblTxnSms()])

        firstReport = SmsBackupFileParser().parse(firstPath)
        secondReport = SmsBackupFileParser().parse(secondPath)

        self.assertEqual(firstReport.count("DUP"), 0)
        self.assertEqual(secondReport.count("DUP"), 0)
        self.assertEqual(len(secondReport.ccTxns), 1)
        self.assertConserved(secondReport)

    def test_a_report_is_immutable(self):
        report = self._parseBackup([self._createHblTxnSms()])

        with self.assertRaises(FrozenInstanceError):
            report.ccTxns = ()


class TestEnvelopeAccounting(BackupTestCase):
    """M4: the declared count and the message count were conflated."""

    def test_declared_actual_sms_and_mms_are_tracked_separately(self):
        report = self._parseBackup(
            [self._createHblTxnSms(), ET.Element("mms"), ET.Element("mms")]
        )

        envelope = report.envelope
        self.assertEqual(envelope.declared, 3)
        self.assertEqual(envelope.actual, 3)
        self.assertEqual(envelope.sms, 1)
        self.assertEqual(envelope.mms, 2)
        self.assertEqual(envelope.invalid, 0)
        self.assertTrue(envelope.matchesDeclared)

    def test_a_declared_count_that_does_not_match_is_reported_not_hidden(self):
        backupPath = self._writeBackup([self._createHblTxnSms()], count=99)

        report = SmsBackupFileParser().parse(backupPath)

        self.assertEqual(report.envelope.declared, 99)
        self.assertEqual(report.envelope.actual, 1)
        self.assertFalse(report.envelope.matchesDeclared)

    def test_a_wrong_root_element_is_a_real_error(self):
        """Raised, not asserted -- an assert vanishes under `python -O`."""
        backupPath = self._writeBackup([], rootTag="contacts")

        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(backupPath)

    def test_a_non_numeric_declared_count_is_a_real_error(self):
        root = ET.Element("smses")
        root.set("count", "lots")
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupPath = Path(tmpDir.name) / "bad.xml"
        ET.ElementTree(root).write(backupPath, encoding="utf-8")

        with self.assertRaises(BackupFileError):
            SmsBackupFileParser().parse(backupPath)

    def test_an_empty_backup_parses_to_an_empty_report(self):
        report = self._parseBackup([])

        self.assertEqual(report.count("ALL"), 0)
        self.assertEqual(report.ccTxns, ())
        self.assertEqual(report.debitTxns, ())
        self.assertConserved(report)


class TestPerRecordIsolation(BackupTestCase):
    """M1: one bad element must not end the run."""

    def test_a_msg_with_no_body_is_counted_invalid_and_the_rest_still_parse(self):
        broken = ET.Element("sms")
        broken.set("address", "4250")

        report = self._parseBackup([broken, self._createHblTxnSms()])

        self.assertEqual(report.envelope.invalid, 1)
        self.assertEqual(report.envelope.sms, 1)
        self.assertEqual(len(report.ccTxns), 1)

    def test_a_msg_with_no_address_is_counted_invalid(self):
        broken = ET.Element("sms")
        broken.set("body", "hello")

        report = self._parseBackup([broken, self._createHblTxnSms()])

        self.assertEqual(report.envelope.invalid, 1)
        self.assertEqual(len(report.ccTxns), 1)

    def test_an_unexpected_child_element_is_counted_invalid(self):
        report = self._parseBackup(
            [ET.Element("contact"), self._createHblTxnSms()]
        )

        self.assertEqual(report.envelope.invalid, 1)
        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(
            report.diagnostics[0].reason, SkipReason.MALFORMED_RECORD
        )

    def test_invalid_records_do_not_enter_the_message_buckets(self):
        broken = ET.Element("sms")
        broken.set("address", "4250")

        report = self._parseBackup([broken, self._createHblTxnSms()])

        self.assertEqual(report.count("ALL"), 1)
        self.assertConserved(report)


class TestDeterministicOrder(BackupTestCase):
    """L6: output order used to be whatever order the XML happened to be in."""

    def test_txns_come_back_in_timestamp_order_regardless_of_file_order(self):
        later = self._createHblTxnSms(vendor="LATER SHOP", txnDate="05/Oct/2023")
        earlier = self._createHblTxnSms(vendor="EARLY SHOP", txnDate="01/Oct/2023")

        report = self._parseBackup([later, earlier])

        self.assertEqual(
            [txn.vendor for txn in report.ccTxns], ["EARLY SHOP", "LATER SHOP"]
        )

    def test_the_order_is_total_so_two_runs_agree_exactly(self):
        """Many transactions share a timestamp -- HBL and SCB alerts carry a
        date only, so a great many share midnight on the same day."""
        elements = [
            self._createHblTxnSms(vendor=f"SHOP {index:02d}", amount="1,000.00")
            for index in range(10, 0, -1)
        ]

        firstRun = self._parseBackup(elements)
        secondRun = self._parseBackup(list(reversed(elements)))

        self.assertEqual(
            [txn.vendor for txn in firstRun.ccTxns],
            [txn.vendor for txn in secondRun.ccTxns],
        )

    def test_debit_txns_are_ordered_too(self):
        later = self._createMeznAtmSms(vendor="LATER ATM", txnDate="20-Jun-24")
        earlier = self._createMeznAtmSms(vendor="EARLY ATM", txnDate="15-Jun-24")

        report = self._parseBackup([later, earlier])

        self.assertEqual(
            [txn.vendor for txn in report.debitTxns], ["EARLY ATM", "LATER ATM"]
        )


class TestRetention(BackupTestCase):
    """M13: the whole document used to stay reachable for the process lifetime."""

    def test_the_parser_retains_no_message_bodies_after_parsing(self):
        parser = SmsBackupFileParser()
        backupPath = self._writeBackup([self._createHblTxnSms()])

        parser.parse(backupPath)

        retained = repr(parser.__dict__)
        self.assertNotIn("IMTIAZ SUPER MARKET", retained)
        self.assertNotIn("CreditCard", retained)


if __name__ == "__main__":
    unittest.main()
