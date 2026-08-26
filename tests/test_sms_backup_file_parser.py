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
        # duplicates are now detected once, up front, for every sender: a
        # duplicate HBL msg counts as DUP instead of falling through the elif
        # chain into OTHER (the routing quirk this pin used to record)
        self.assertEqual(parser.msgCounts["DUP"], 1)
        self.assertEqual(parser.msgCounts["OTHER"], 0)
        # ALL counts <sms> elements, and is incremented before the dup check
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

    def test_msg_count_conservation(self):
        """Test method to verify the conservation identity that must hold for
        any backup: every counted msg lands in exactly one bucket, so
        ALL == HBL + FBL + SCB + MEZN + OTHER + DUP. Also verifies that MMS
        elements are excluded from every counter.
        """
        parser = self._parseBackup(
            [
                self._createHblTxnSms(),
                self._createSms("8756", "FBL msg body"),
                self._createSms("7220", "SCB msg body"),
                self._createSms("8079", "Meezan msg body"),
                self._createSms("1234", "msg from an unrecognized sender"),
                # a duplicate of the FBL msg above
                self._createSms("8756", "FBL msg body"),
                ET.Element("mms"),
            ]
        )

        perBucket = sum(
            parser.msgCounts[bucket]
            for bucket in ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")
        )
        self.assertEqual(parser.msgCounts["ALL"], perBucket)
        # 6 <sms> elements; the MMS is not counted anywhere
        self.assertEqual(parser.msgCounts["ALL"], 6)
        self.assertEqual(parser.msgCounts["DUP"], 1)

    def test_duplicate_from_unknown_sender_counted_as_dup(self):
        """Test method to verify that dedup applies to every sender, not just
        the recognized banks: a repeated msg from an unknown sender counts as
        DUP rather than being counted twice as OTHER.
        """
        parser = self._parseBackup(
            [
                self._createSms("1234", "a promotional msg, retransmitted"),
                self._createSms("1234", "a promotional msg, retransmitted"),
            ]
        )

        self.assertEqual(parser.msgCounts["OTHER"], 1)
        self.assertEqual(parser.msgCounts["DUP"], 1)
        self.assertEqual(parser.msgCounts["ALL"], 2)

    def test_cross_sender_duplicate_counted_as_dup(self):
        """Test method to verify (and pin) the cross-sender consequence of
        body-only hashing: an identical body relayed by a second, different
        sender counts as DUP rather than as a msg from that sender.
        """
        parser = self._parseBackup(
            [
                self._createSms("1234", "identical body from two senders"),
                self._createSms("7220", "identical body from two senders"),
            ]
        )

        self.assertEqual(parser.msgCounts["OTHER"], 1)
        self.assertEqual(parser.msgCounts["SCB"], 0)
        self.assertEqual(parser.msgCounts["DUP"], 1)

    # ------------------------------------------------------------------
    # Mixed-bank integration cases. These need all four parsers to exist,
    # so they live here rather than in any single bank's test file.
    # ------------------------------------------------------------------

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
    ) -> ET.Element:
        body = (
            f"Dear Client, PKR {amount} have been paid at {vendor} on "
            f"{txnDate} using Credit Card no {cardMask}. Avail Limit "
            f"PKR59563.45. SCBPL"
        )

        return self._createSms("7220", body)

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

    def test_mixed_bank_backup(self):
        """Test method to verify that a backup carrying one txn from each of
        the four banks routes every msg to the right store: the three CC banks
        share ccTxns/ccVendors, Meezan debits go to debitTxns/debitVendors, and
        the per-bank msg counts and the conservation identity all hold at once.
        """
        parser = self._parseBackup(
            [
                self._createHblTxnSms(),
                self._createFblTxnSms(),
                self._createScbTxnSms(),
                self._createMeznAtmSms(),
                self._createSms("1234", "msg from an unrecognized sender"),
            ]
        )

        self.assertEqual(len(parser.ccTxns), 3)
        self.assertEqual(
            {txn.bank for txn in parser.ccTxns}, {"HBL", "FBL", "SCB"}
        )
        self.assertEqual(len(parser.debitTxns), 1)
        self.assertEqual(parser.debitTxns[0].bank, "MEZN")

        # a debit must not leak into the CC stores, nor a CC txn into the debit
        # stores — the two are reported by different commands
        self.assertEqual(len(parser.ccVendors), 3)
        self.assertEqual(len(parser.debitVendors), 1)
        self.assertTrue(parser.ccVendors.isdisjoint(parser.debitVendors))

        for bank in ("HBL", "FBL", "SCB", "MEZN"):
            with self.subTest(bank=bank):
                self.assertEqual(parser.msgCounts[bank], 1)
        self.assertEqual(parser.msgCounts["OTHER"], 1)
        self.assertEqual(parser.msgCounts["DUP"], 0)
        self.assertEqual(parser.msgCounts["ALL"], 5)

        perBucket = sum(
            parser.msgCounts[bucket]
            for bucket in ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")
        )
        self.assertEqual(parser.msgCounts["ALL"], perBucket)

    def test_no_skips_on_well_formed_mixed_backup(self):
        """Test method to verify that a backup of well-formed msgs produces no
        skips at all: a skipped counter climbing on good input would mean a
        bank's txn signal and its extraction regex disagree.
        """
        parser = self._parseBackup(
            [
                self._createFblTxnSms(),
                self._createScbTxnSms(),
                self._createMeznAtmSms(),
            ]
        )

        for counter in ("FBL_SKIPPED", "SCB_SKIPPED", "MEZN_SKIPPED"):
            with self.subTest(counter=counter):
                self.assertEqual(parser.msgCounts[counter], 0)

    def test_banks_do_not_claim_each_others_msgs(self):
        """Test method to verify that the sender short code alone decides the
        bank: each bank's txn body, sent from *another* bank's short code, is
        counted against the sending bank and never parsed by the bank whose
        template it matches.
        """
        fblBodyOnScbCode = self._createSms(
            "7220", self._createFblTxnSms().attrib["body"]
        )
        scbBodyOnMeznCode = self._createSms(
            "8079", self._createScbTxnSms().attrib["body"]
        )
        meznBodyOnFblCode = self._createSms(
            "8756", self._createMeznAtmSms().attrib["body"]
        )

        parser = self._parseBackup(
            [fblBodyOnScbCode, scbBodyOnMeznCode, meznBodyOnFblCode]
        )

        # every msg is attributed to its sender, and none of the three parses:
        # a bank only ever sees bodies from its own short codes
        self.assertEqual(parser.msgCounts["SCB"], 1)
        self.assertEqual(parser.msgCounts["MEZN"], 1)
        self.assertEqual(parser.msgCounts["FBL"], 1)
        self.assertEqual(parser.ccTxns, [])
        self.assertEqual(parser.debitTxns, [])

    def test_identical_bodies_across_banks_collapse_to_one_txn(self):
        """Test method to verify (and pin) the cross-sender reach of body-only
        hashing where it actually costs a txn: the same FBL charge body sent
        twice yields one txn, with the second counted as DUP.
        """
        parser = self._parseBackup(
            [self._createFblTxnSms(), self._createFblTxnSms()]
        )

        self.assertEqual(len(parser.ccTxns), 1)
        self.assertEqual(parser.msgCounts["FBL"], 1)
        self.assertEqual(parser.msgCounts["DUP"], 1)
        self.assertEqual(parser.msgCounts["ALL"], 2)

if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
