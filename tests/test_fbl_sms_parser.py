import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from parser.fbl_sms_parser import FBLSmsParser
from pathlib import Path

from common import DEFAULT_TZ
from sms_backup_file_parser import SmsBackupFileParser

# A single well-formed FBL CC txn msg body, reused across the tests. The
# cardholder name is synthetic; the merchant names and the fixed-width txn tail
# come from the real corpus, where the vendor field is 22 chars wide and is
# followed by a single separator space — so the city starts at index 23 of the
# tail (index 24 when the vendor carries a backslash escape, which adds one
# character on top of the field).
FBL_TXN_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for PKR 25170.49 on "
    "20-Sep-23 01:17:16 PM at IMTIAZ SUPER MARKET    KARACHI        PK."
)


class TestFBLSmsParser(unittest.TestCase):

    def _createSms(self, address: str, body: str) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("protocol", "0")
        sms.set("address", address)
        sms.set("date", "1695197836791")
        sms.set("type", "1")
        sms.set("read", "1")
        sms.set("status", "-1")
        sms.set("body", body)
        sms.set("readable_date", "Sep 20, 2023 1:17:16 PM")
        sms.set("contact_name", "(Unknown)")

        return sms

    def _createFblSms(self, body: str) -> ET.Element:
        return self._createSms(FBLSmsParser.FBL_SHORT_CODES[0], body)

    def _txnBody(
        self,
        currency: str = "PKR",
        amount: str = "25170.49",
        txnDate: str = "20-Sep-23 01:17:16 PM",
        rest: str = "IMTIAZ SUPER MARKET    KARACHI        PK",
        cardClause: str = "your FBL Card  has",
        tail: str = "",
    ) -> str:
        return (
            f"Dear JOHN DOE, {cardClause} been charged for {currency} {amount} "
            f"on {txnDate} at {rest}.{tail}"
        )

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

    #
    # identification
    #

    def test_sms_from_fbl_short_code(self):
        """Test method to verify that a msg from FBL's short code is
        identified as an FBL msg.
        """
        self.assertTrue(FBLSmsParser.isSmsFromFBL(self._createFblSms(FBL_TXN_BODY)))

    def test_sms_from_another_bank_not_fbl(self):
        """Test method to verify that a msg from another bank's short code is
        not identified as an FBL msg.
        """
        self.assertFalse(FBLSmsParser.isSmsFromFBL(self._createSms("4250", "HBL msg")))

    def test_charge_msg_is_cc_txn(self):
        """Test method to verify that an FBL charge msg is recognized as a CC
        txn msg.
        """
        self.assertTrue(
            FBLSmsParser.isMsgCreditCardTxn(self._createFblSms(FBL_TXN_BODY))
        )

    def test_payment_received_msg_is_not_cc_txn(self):
        """Test method to verify that the payment-received template — a real
        non-txn msg that also arrives on FBL's short code and also mentions
        "FBL Card" — is not mistaken for a txn.
        """
        sms = self._createFblSms(
            "Dear JOHN DOE, Payment of PKR 50,000.00 has been received on your "
            "FBL Card. Thank you for banking with Faysal Bank."
        )

        self.assertFalse(FBLSmsParser.isMsgCreditCardTxn(sms))

    def test_statement_notice_is_not_cc_txn(self):
        """Test method to verify that a Noor Card statement notice — another
        real non-txn template on FBL's short code — is not mistaken for a txn.
        """
        sms = self._createFblSms(
            "Dear Customer, your FBL Noor Card e-statement for Sep-23 has been "
            "emailed to your registered email address."
        )

        self.assertFalse(FBLSmsParser.isMsgCreditCardTxn(sms))

    #
    # extraction
    #

    def test_extract_12_hour_date_format(self):
        """Test method to verify extraction from a msg carrying the 12-hour
        (DD-Mon-YY hh:mm:ss AM/PM) txn date format.
        """
        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(self._createFblSms(FBL_TXN_BODY))

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple.currency, "PKR")
        self.assertEqual(ccTxn.amountTuple.amount, 25170.49)
        self.assertEqual(ccTxn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(
            ccTxn.date, datetime(2023, 9, 20, 13, 17, 16, tzinfo=DEFAULT_TZ)
        )

    def test_extract_24_hour_numeric_date_format(self):
        """Test method to verify extraction from a msg carrying the 24-hour
        (DD-MM-YYYY HH:MM:SS) txn date format.
        """
        sms = self._createFblSms(
            self._txnBody(
                amount="8100",
                txnDate="04-02-2024 21:11:41",
                rest="K ELECTRIC BILL PAY    KARACHI        PK",
            )
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple.amount, 8100.0)
        self.assertEqual(ccTxn.vendor, "K ELECTRIC BILL PAY")
        self.assertEqual(
            ccTxn.date, datetime(2024, 2, 4, 21, 11, 41, tzinfo=DEFAULT_TZ)
        )

    def test_extract_amount_without_thousands_separator(self):
        """Test method to verify that an amount with no thousands separator and
        a single decimal place parses — FBL never comma-groups its amounts, so
        the HBL amount RE cannot be reused here.
        """
        sms = self._createFblSms(self._txnBody(amount="18298.9"))

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple.amount, 18298.9)

    def test_extract_usd_txn(self):
        """Test method to verify that a USD txn keeps its currency."""
        sms = self._createFblSms(
            self._txnBody(
                currency="USD",
                amount="39.99",
                rest="AMAZON.COM             SEATTLE        US",
            )
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple.currency, "USD")
        self.assertEqual(ccTxn.amountTuple.amount, 39.99)
        self.assertEqual(ccTxn.vendor, "AMAZON.COM")

    def test_extract_cad_txn(self):
        """Test method to verify that a CAD txn keeps its currency."""
        sms = self._createFblSms(
            self._txnBody(
                currency="CAD",
                amount="12.50",
                txnDate="15-Mar-24 09:05:00 AM",
                rest="TIM HORTONS 1234       TORONTO       CA",
            )
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple.currency, "CAD")
        self.assertEqual(ccTxn.vendor, "TIM HORTONS 1234")

    def test_extract_with_available_limit_tail(self):
        """Test method to verify that the trailing " Available Limit: ..."
        variant of the msg still parses.
        """
        sms = self._createFblSms(self._txnBody(tail=" Available Limit: 245000.00"))

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(ccTxn.amountTuple.amount, 25170.49)

    def test_extract_vendor_from_three_part_split(self):
        """Test method to verify the common vendor case: padding spaces
        separate the vendor, city and country columns, so the vendor is the
        first of (at least) three parts.
        """
        sms = self._createFblSms(
            self._txnBody(rest="HYPERSTAR JOHAR TOWN   LAHORE        PK")
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertEqual(ccTxn.vendor, "HYPERSTAR JOHAR TOWN")

    def test_extract_vendor_filling_the_whole_column(self):
        """Test method to verify a vendor name that exactly fills its 22-char
        column: no padding separates it from the city, so the split yields
        fewer than 3 parts and the column-width slice recovers the vendor.
        """
        sms = self._createFblSms(
            self._txnBody(rest="SHUJAAT FILLING STATIO KARACHI        PK")
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertEqual(ccTxn.vendor, "SHUJAAT FILLING STATIO")

    def test_extract_vendor_with_backslash_escape(self):
        """Test method to verify that a vendor name whose apostrophe is
        backslash-escaped in the msg body comes out unescaped. The escape is an
        extra char on top of the 22-char vendor field, so this tail's city
        starts at index 24 rather than 23.
        """
        sms = self._createFblSms(
            self._txnBody(rest="M&M\\'S PHARMACY         KARACHI        PK")
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertEqual(ccTxn.vendor, "M&M'S PHARMACY")

    def test_extract_escaped_vendor_filling_the_whole_column(self):
        """Test method to verify the one shape that pins the ORDER of the two
        steps in _extractVendor: an escaped vendor name that also exactly fills
        its 22-char field takes the column-slice path, and since the backslash
        is an extra char beyond that field, slicing before unescaping would chop
        the vendor's last letter off. Two real corpus msgs have this shape.
        """
        sms = self._createFblSms(
            self._txnBody(rest="AUNTIE MANAVER\\'S DESSE KARACHI        PK")
        )

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertEqual(ccTxn.vendor, "AUNTIE MANAVER'S DESSE")

    def test_extract_vendor_split_wins_over_column_slice(self):
        """Test method to guard the padding-split branch itself: a vendor name
        short enough that its city field starts well before index 22 makes the
        two extraction rules disagree — the split yields the vendor alone, while
        the column-width slice would swallow the city too. Synthetic on purpose:
        no corpus body disagrees today (all 583 real tails start the city at
        index 23, or 24 when escaped), so this pins the documented rule rather
        than observed data.
        """
        sms = self._createFblSms(self._txnBody(rest="QUICK MART   LAHORE   PK"))

        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertEqual(ccTxn.vendor, "QUICK MART")

    def test_no_card_digits_in_msg(self):
        """Test method to verify that ccLastFourDigits stays 0: FBL txn msgs
        carry no card digits at all.
        """
        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(self._createFblSms(FBL_TXN_BODY))

        self.assertEqual(ccTxn.ccLastFourDigits, 0)

    def test_txn_tagged_with_fbl_bank(self):
        """Test method to verify that an extracted txn is tagged as an FBL
        txn.
        """
        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(self._createFblSms(FBL_TXN_BODY))

        self.assertEqual(ccTxn.bank, "FBL")

    def test_txn_date_is_stamped_with_karachi_tz(self):
        """Test method to verify that the txn date is *stamped* with
        Asia/Karachi (all backup timestamps are Karachi local time) rather
        than converted into it from the host machine's local time.
        """
        ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(self._createFblSms(FBL_TXN_BODY))

        self.assertIsNotNone(ccTxn.date.tzinfo)
        self.assertEqual(str(ccTxn.date.tzinfo), "Asia/Karachi")
        self.assertEqual(
            ccTxn.date, datetime(2023, 9, 20, 13, 17, 16, tzinfo=DEFAULT_TZ)
        )
        # the wall-clock reading must be untouched by the stamping
        self.assertEqual(ccTxn.date.hour, 13)
        self.assertEqual(ccTxn.date.day, 20)

    def test_unparseable_body_returns_none(self):
        """Test method to verify that a body that cannot be parsed yields None
        instead of raising.
        """
        sms = self._createFblSms(
            "Dear JOHN DOE, your FBL Card has been charged for something odd."
        )

        self.assertIsNone(FBLSmsParser.extractDetailsFromTxnMsg(sms))

    def test_unparseable_date_returns_none(self):
        """Test method to verify that a date token the RE accepts but strptime
        rejects yields None rather than raising.
        """
        sms = self._createFblSms(self._txnBody(txnDate="31-Feb-24 01:17:16 PM"))

        self.assertIsNone(FBLSmsParser.extractDetailsFromTxnMsg(sms))

    #
    # card-clause whitespace wobble
    #

    def test_card_clause_whitespace_variants_all_parse(self):
        """Test method to verify that all three card-clause spacings found in
        the corpus parse identically: "your FBL Card  has", "your  FBL Card has"
        and the single-spaced "your FBL Card has".
        """
        for cardClause in (
            "your FBL Card  has",
            "your  FBL Card has",
            "your FBL Card has",
        ):
            with self.subTest(cardClause=cardClause):
                sms = self._createFblSms(self._txnBody(cardClause=cardClause))

                ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(sms)

                self.assertIsNotNone(ccTxn)
                self.assertEqual(ccTxn.vendor, "IMTIAZ SUPER MARKET")
                self.assertEqual(ccTxn.amountTuple.amount, 25170.49)
                self.assertEqual(
                    ccTxn.date, datetime(2023, 9, 20, 13, 17, 16, tzinfo=DEFAULT_TZ)
                )

    #
    # skip path
    #

    def test_txn_signal_but_regex_miss_returns_none(self):
        """Test method to verify the skip path: a body that carries the txn
        signal but fails the extraction RE (here a comma-grouped amount, which
        FBL never produces) is skipped, not parsed and not raised on.
        """
        sms = self._createFblSms(self._txnBody(amount="25,170.49"))

        self.assertTrue(FBLSmsParser.isMsgCreditCardTxn(sms))
        self.assertIsNone(FBLSmsParser.extractDetailsFromTxnMsg(sms))

    #
    # end-to-end, through SmsBackupFileParser
    #

    def test_fbl_txn_parsed_end_to_end(self):
        """Test method to verify that an FBL charge msg makes it all the way
        from the backup file into ccTxns/ccVendors, and that a msg carrying the
        txn signal but failing extraction is counted as skipped instead of
        aborting the run.
        """
        parser = self._parseBackup(
            [
                self._createFblSms(FBL_TXN_BODY),
                self._createFblSms(self._txnBody(amount="25,170.49")),
            ]
        )

        self.assertEqual(len(parser.ccTxns), 1)
        self.assertEqual(parser.ccTxns[0].bank, "FBL")
        self.assertIn("IMTIAZ SUPER MARKET", parser.ccVendors)
        self.assertEqual(parser.msgCounts["FBL"], 2)
        self.assertEqual(parser.msgCounts["FBL_SKIPPED"], 1)
        self.assertEqual(parser.msgCounts["OTHER"], 0)

    def test_duplicate_fbl_msgs_parsed_once(self):
        """Test method to verify that two FBL msgs with an identical body
        collapse into a single txn and the second one counts as a duplicate.
        """
        parser = self._parseBackup(
            [self._createFblSms(FBL_TXN_BODY), self._createFblSms(FBL_TXN_BODY)]
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
