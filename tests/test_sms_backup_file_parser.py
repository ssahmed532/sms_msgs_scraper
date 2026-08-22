import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from sms_backup_file_parser import SmsBackupFileParser


class TestSmsBackupFileParser(unittest.TestCase):

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

    def _parseBackup(self, msgElements: list) -> SmsBackupFileParser:
        """Write the given msg elements out as a backup XML file and run the
        parser over it end-to-end (loadFromSmsBackupFile + parseMessages).
        """
        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)

        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupFilepath = Path(tmpDir.name) / "sms_backup.xml"
        ET.ElementTree(root).write(backupFilepath, encoding="utf-8")

        parser = SmsBackupFileParser()
        parser.loadFromSmsBackupFile(backupFilepath)
        parser.parseMessages()

        return parser

    def test_hbl_cc_txn_parsed(self):
        """Test method to verify that a single HBL CC txn msg makes it all
        the way from the backup file into ccTxns and ccVendors.
        """
        parser = self._parseBackup([self._createHblTxnSms()])

        self.assertEqual(len(parser.ccTxns), 1)
        self.assertEqual(parser.ccVendors, {"IMTIAZ SUPER MARKET"})
        self.assertEqual(parser.msgCounts["HBL"], 1)
        self.assertEqual(parser.msgCounts["ALL"], 1)

    def test_distinct_txn_msgs_all_parsed(self):
        """Test method to verify that distinct txn msgs are not mistaken for
        duplicates of each other.
        """
        parser = self._parseBackup(
            [
                self._createHblTxnSms(vendor="IMTIAZ SUPER MARKET"),
                self._createHblTxnSms(
                    vendor="CHASE UP", amount="9,999.00", txnDate="02/Oct/2023"
                ),
            ]
        )

        self.assertEqual(len(parser.ccTxns), 2)
        self.assertEqual(parser.ccVendors, {"IMTIAZ SUPER MARKET", "CHASE UP"})

    def test_duplicate_msg_parsed_once(self):
        """Test method to verify that two msgs with an identical body are
        parsed as a single txn. The dedup hash covers the body only, so this
        also means two *legitimate* identical purchases (same vendor, amount
        and date — the SMS body carries no time of day) collapse into one
        txn: a known limitation of body-only dedup.
        """
        parser = self._parseBackup([self._createHblTxnSms(), self._createHblTxnSms()])

        self.assertEqual(len(parser.ccTxns), 1)
        self.assertEqual(parser.msgCounts["HBL"], 1)
        # current routing quirk: a duplicate HBL msg fails the HBL branch
        # condition and falls through the elif chain into OTHER
        self.assertEqual(parser.msgCounts["OTHER"], 1)
        self.assertEqual(parser.msgCounts["ALL"], 2)

    def test_whitespace_only_variant_is_duplicate(self):
        """Test method to verify that the body is stripped before hashing,
        so bodies differing only in leading/trailing whitespace count as the
        same msg.
        """
        first = self._createHblTxnSms()
        second = self._createHblTxnSms()
        second.set("body", "  " + second.attrib["body"] + "  ")

        parser = self._parseBackup([first, second])

        self.assertEqual(len(parser.ccTxns), 1)

    def test_mms_skipped(self):
        """Test method to verify that MMS elements are skipped entirely —
        not counted, not parsed.
        """
        parser = self._parseBackup([ET.Element("mms"), self._createHblTxnSms()])

        self.assertEqual(parser.msgCounts["ALL"], 1)
        self.assertEqual(len(parser.ccTxns), 1)

    def test_hbl_non_txn_msg_counted_only(self):
        """Test method to verify that an HBL msg that is not a CC txn (e.g.
        an OTP) is counted as an HBL msg but produces no txn.
        """
        otp = self._createSms(
            "4250",
            "648975 is your One Time Password (OTP) for the internet transaction on HBL Card ending with 0077. This OTP is valid for 10 mins. Do not share OTP with anyone.",
        )

        parser = self._parseBackup([otp])

        self.assertEqual(parser.msgCounts["HBL"], 1)
        self.assertEqual(parser.ccTxns, [])

    def test_bank_routing_counts(self):
        """Test method to verify that non-HBL bank msgs are counted per bank
        but never parsed into txns, and that an unrecognized sender falls
        into OTHER.
        """
        parser = self._parseBackup(
            [
                self._createSms("8756", "FBL msg body"),
                self._createSms("7220", "SCB msg body"),
                self._createSms("8079", "Meezan msg body 1"),
                self._createSms("9779", "Meezan msg body 2"),
                self._createSms("1234", "msg from an unrecognized sender"),
            ]
        )

        self.assertEqual(parser.msgCounts["FBL"], 1)
        self.assertEqual(parser.msgCounts["SCB"], 1)
        self.assertEqual(parser.msgCounts["MEZN"], 2)
        self.assertEqual(parser.msgCounts["OTHER"], 1)
        self.assertEqual(parser.msgCounts["ALL"], 5)
        self.assertEqual(parser.ccTxns, [])


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
