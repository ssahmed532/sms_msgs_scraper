import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from parser.hbl_sms_parser import HBLSmsParser
from parser.scb_sms_parser import SCBSmsParser
from pathlib import Path

from cc_txn import CurrencyAmountTuple
from common import DEFAULT_TZ
from sms_backup_file_parser import SmsBackupFileParser

# A well-formed SCB CC txn msg, matching the validated real format:
#   Dear Client, PKR {amt} have been paid at {vendor}{city} {CTY} on {DD-MM-YY}
#   using Credit Card no {mask}. Avail Limit PKR{x}. SCBPL
SCB_TXN_MSG_TEMPLATE = (
    "Dear Client, PKR {amount} have been paid at {vendor} on {txnDate} "
    "using Credit Card no {cardMask}. Avail Limit PKR59563.45. SCBPL"
)

# The real truncated shape: the body simply ends after (part of) the vendor
# name, before the date and card ever appear.
SCB_TRUNCATED_MSG = "Dear Client, PKR 281.00 have been paid at NECOS NATURAL STORE "

# The real "no amount" shape: a foreign-currency txn whose PKR amount is
# absent, leaving a literal "PKR .00" in an otherwise well-formed body.
SCB_NO_AMOUNT_MSG = (
    "Dear Client, PKR .00 have been paid at PASSPORT/PASSEPORT CAN OTTAWA CAN "
    "on 14-06-24 using Credit Card no 5495. Avail Limit PKR181962.55. SCBPL"
)


class TestSCBSmsParser(unittest.TestCase):

    def _createSms(self, address: str, body: str) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("protocol", "0")
        sms.set("address", address)
        sms.set("date", "1696265826791")
        sms.set("type", "1")
        sms.set("subject", "null")
        sms.set("read", "1")
        sms.set("status", "-1")
        sms.set("body", body)
        sms.set("service_center", "+92308984321")
        sms.set("readable_date", "Oct 2, 2023 9:57:06 PM")

        return sms

    def _createScbSms(self, body: str) -> ET.Element:
        return self._createSms(SCBSmsParser.SCB_SHORT_CODES[0], body)

    def _createScbTxnSms(
        self,
        vendor: str = "PSO SERVICE STATION 7Karachi PAK",
        amount: str = "12,450.90",
        txnDate: str = "29-09-23",
        cardMask: str = "5452xxxxxxxx1280",
    ) -> ET.Element:
        return self._createScbSms(
            SCB_TXN_MSG_TEMPLATE.format(
                amount=amount, vendor=vendor, txnDate=txnDate, cardMask=cardMask
            )
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

    # ---------------------------------------------------------------- identification

    def test_scb_sms_msg(self):
        """Test method to verify that a msg from an SCB short code is
        identified as an SCB msg.
        """
        for shortCode in SCBSmsParser.SCB_SHORT_CODES:
            with self.subTest(shortCode=shortCode):
                sms = self._createSms(shortCode, "any body")

                self.assertTrue(SCBSmsParser.isSmsFromSCB(sms))

    def test_non_scb_sms_msg(self):
        """Test method to verify that a msg from another bank's short code
        (HBL here) is not identified as an SCB msg.
        """
        sms = self._createSms(
            HBLSmsParser.HBL_SHORT_CODES[0],
            "Dear Customer, Your HBL CreditCard (ending with 8526) has been "
            "charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023.",
        )

        self.assertFalse(SCBSmsParser.isSmsFromSCB(sms))

    def test_isMsgCreditCardTxn_txn_msg(self):
        """Test method to verify that a well-formed CC txn msg is recognized
        as a CC txn msg.
        """
        self.assertTrue(SCBSmsParser.isMsgCreditCardTxn(self._createScbTxnSms()))

    def test_isMsgCreditCardTxn_rejects_non_txn_msgs(self):
        """Test method to verify that the real non-txn templates SCB sends on
        this same short code are not mistaken for CC txns. Both open with the
        same 'Dear Client'/'Dear Customer' salutation as a txn msg, and the
        account-transaction one even carries a PKR amount, so the txn check
        has to key on the 'have been paid at' phrase to tell them apart.
        """
        nonTxnBodies = [
            "Dear Client, a transaction of PKR 15000.00 has been completed on "
            "Account No. 01-21***71-01 on 05-08-24 using Online Banking.  "
            "Avail. Bal: PKR 32145.67.",
            "Dear Customer, Thank you. Your MASTERCARD STANDARD Card "
            "5452xxxxxxxx1280 payment of Rs 750.00 has been received. For "
            "assistance, please call 111-002-002. Standard Chartered",
        ]

        for body in nonTxnBodies:
            with self.subTest(body=body[:40]):
                self.assertFalse(
                    SCBSmsParser.isMsgCreditCardTxn(self._createScbSms(body))
                )

    def test_isMsgCreditCardTxn_accepts_unusable_txn_msgs(self):
        """Test method to verify that the two unusable-but-real txn shapes
        still register as txn *attempts*. This is load-bearing for the skip
        accounting: they have to pass this check and then fail extraction so
        the orchestrator counts them as SCB_SKIPPED. Tightening the check
        (e.g. also requiring 'using Credit Card no') would silently drop the
        truncated msgs out of that count.
        """
        for body in [SCB_TRUNCATED_MSG, SCB_NO_AMOUNT_MSG]:
            with self.subTest(body=body[:40]):
                self.assertTrue(
                    SCBSmsParser.isMsgCreditCardTxn(self._createScbSms(body))
                )

    # ------------------------------------------------------------------- extraction

    def test_extractDetailsFromTxnMsg_comma_grouped_amount(self):
        """Test method to verify that every txn field is extracted off a msg
        carrying a comma-grouped amount.
        """
        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(self._createScbTxnSms())

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple, CurrencyAmountTuple("PKR", 12450.90))
        self.assertEqual(ccTxn.vendor, "PSO SERVICE STATION 7Karachi PAK")
        self.assertEqual(ccTxn.ccLastFourDigits, 1280)
        self.assertEqual(ccTxn.date, datetime(2023, 9, 29, tzinfo=DEFAULT_TZ))
        self.assertEqual(ccTxn.bank, "SCB")

    def test_extractDetailsFromTxnMsg_plain_amount(self):
        """Test method to verify that an amount written without thousands
        separators parses too — SCB uses both forms (141 of the 379 validated
        txn msgs carry no comma).
        """
        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
            self._createScbTxnSms(amount="15134.00")
        )

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple, CurrencyAmountTuple("PKR", 15134.00))

    def test_extractDetailsFromTxnMsg_currency_token_is_parsed(self):
        """Test method to verify that the currency comes from the msg rather
        than being hardcoded. Every SCB txn msg in the validated corpus is
        PKR, but the token is captured, so a future non-PKR msg carries its
        own currency instead of being mislabelled.
        """
        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
            self._createScbSms(
                "Dear Client, USD 42.75 have been paid at AMAZON.CA CAN on "
                "12-03-24 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
                "PKR59563.45. SCBPL"
            )
        )

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple, CurrencyAmountTuple("USD", 42.75))

    def test_extractDetailsFromTxnMsg_full_card_mask(self):
        """Test method to verify that the last 4 card digits are pulled out of
        the full card mask form.
        """
        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
            self._createScbTxnSms(cardMask="5452xxxxxxxx1280")
        )

        self.assertEqual(ccTxn.ccLastFourDigits, 1280)

    def test_extractDetailsFromTxnMsg_bin_only_card_mask(self):
        """Test method to verify that a BIN-only card mask — which carries no
        last-4 digits at all — yields 0 rather than a wrong (or negative)
        value. 83 of the 378 runtime SCB txns come in this shape.
        """
        for cardMask in ["5495", "5974"]:
            with self.subTest(cardMask=cardMask):
                ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
                    self._createScbTxnSms(cardMask=cardMask)
                )

                self.assertIsNotNone(ccTxn)
                self.assertEqual(ccTxn.ccLastFourDigits, 0)

    def test_extractDetailsFromTxnMsg_stamps_karachi_tz(self):
        """Test method to verify that the txn date is *stamped* as Karachi
        local time: DD-MM-YY is read day-first (the middle token is the
        month), the wall-clock date survives untouched, and the +05:00 offset
        is attached with no shift applied. A converting implementation
        (astimezone) would move the date on any host not set to +05:00.
        """
        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
            self._createScbTxnSms(txnDate="29-09-23")
        )

        self.assertEqual(ccTxn.date, datetime(2023, 9, 29, tzinfo=DEFAULT_TZ))
        self.assertEqual(ccTxn.date.tzinfo, DEFAULT_TZ)
        self.assertEqual(ccTxn.date.utcoffset(), timedelta(hours=5))
        self.assertEqual(ccTxn.date.replace(tzinfo=None), datetime(2023, 9, 29))

    def test_extractDetailsFromTxnMsg_vendor_kept_verbatim(self):
        """Test method to verify that the vendor text is kept exactly as
        captured, city included. SCB sometimes glues the city onto the
        merchant name with no separating space, so there is no reliable
        vendor/city split — a known limitation this pins deliberately.
        """
        for vendor in [
            "SOUTH CITY HOSPITALKarachi PAK",
            "Amazon.caAMAZON.CA CAN",
            "SHELL (SUNSET BOULEVAR KARACHI PAK",
            # a vendor containing the " on " that separates the vendor from
            # the txn date: the whole vendor must survive, not just the text
            # ahead of the first " on "
            "CAFE on THE GO Karachi PAK",
        ]:
            with self.subTest(vendor=vendor):
                ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
                    self._createScbTxnSms(vendor=vendor)
                )

                self.assertEqual(ccTxn.vendor, vendor)

    def test_extractDetailsFromTxnMsg_takes_the_first_txn_in_the_body(self):
        """Test method to verify that a body carrying two concatenated txn
        alerts yields the *first* txn, i.e. the one the leading amount
        belongs to.

        The vendor capture is deliberately lazy. On every well-formed body in
        the validated corpus a greedy capture behaves identically (there is
        only one date/card tail to anchor on), so this concatenated shape —
        which the SMS pipeline can produce, as the 21 truncated bodies show
        it already mangles these msgs — is what pins the choice: a greedy
        capture would pair the first amount with the *second* msg's vendor,
        date and card, inventing a txn that never happened.
        """
        firstTxn = SCB_TXN_MSG_TEMPLATE.format(
            amount="100.00",
            vendor="FIRST SHOP Karachi PAK",
            txnDate="01-02-24",
            cardMask="5452xxxxxxxx1280",
        )
        secondTxn = SCB_TXN_MSG_TEMPLATE.format(
            amount="200.00",
            vendor="SECOND SHOP Lahore PAK",
            txnDate="03-04-24",
            cardMask="5452xxxxxxxx9999",
        )

        ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(
            self._createScbSms(firstTxn + " " + secondTxn)
        )

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.amountTuple, CurrencyAmountTuple("PKR", 100.00))
        self.assertEqual(ccTxn.vendor, "FIRST SHOP Karachi PAK")
        self.assertEqual(ccTxn.date, datetime(2024, 2, 1, tzinfo=DEFAULT_TZ))
        self.assertEqual(ccTxn.ccLastFourDigits, 1280)

    # ------------------------------------------------------------------ skip paths

    def test_extractDetailsFromTxnMsg_truncated_body(self):
        """Test method to verify that a body truncated mid-vendor (the date
        and card never arrive) yields None rather than a partial txn. 21 real
        msgs come in this shape.
        """
        self.assertIsNone(
            SCBSmsParser.extractDetailsFromTxnMsg(
                self._createScbSms(SCB_TRUNCATED_MSG)
            )
        )

    def test_extractDetailsFromTxnMsg_no_amount_body(self):
        """Test method to verify that a body carrying a literal 'PKR .00'
        amount yields None. These are foreign-currency txns whose real amount
        is absent from the msg; extracting them would record a 0.00 txn. 5
        real msgs come in this shape, and the amount regex rejects them by
        requiring at least one digit before the decimal point.
        """
        self.assertIsNone(
            SCBSmsParser.extractDetailsFromTxnMsg(
                self._createScbSms(SCB_NO_AMOUNT_MSG)
            )
        )

    def test_convertToDateTime_malformed_date(self):
        """Test method to verify that an unparseable txn date yields None
        rather than raising.
        """
        self.assertIsNone(SCBSmsParser._convertToDateTime("2023-09-29"))

    # ----------------------------------------------------------------- end-to-end

    def test_scb_txn_parsed_and_unusable_msg_skipped(self):
        """Test method to verify the whole SCB path through the orchestrator:
        a good txn msg becomes a CC txn tagged SCB, both msgs count as SCB
        msgs, and the unusable one is counted as skipped instead of aborting
        the run (an assert on the new branch would) or silently vanishing.
        """
        parser = self._parseBackup(
            [
                self._createScbTxnSms(),
                self._createScbSms(SCB_TRUNCATED_MSG),
            ]
        )

        self.assertEqual(len(parser.ccTxns), 1)
        self.assertEqual(parser.ccTxns[0].bank, "SCB")
        self.assertEqual(parser.ccTxns[0].vendor, "PSO SERVICE STATION 7Karachi PAK")
        self.assertEqual(parser.ccVendors, {"PSO SERVICE STATION 7Karachi PAK"})
        self.assertEqual(parser.msgCounts["SCB"], 2)
        self.assertEqual(parser.msgCounts["SCB_SKIPPED"], 1)
        self.assertEqual(parser.msgCounts["OTHER"], 0)
        self.assertEqual(parser.msgCounts["ALL"], 2)

    def test_scb_non_txn_msg_counted_only(self):
        """Test method to verify that an SCB msg which is not a CC txn is
        counted as an SCB msg but produces neither a txn nor a skip — only
        txn *attempts* may count as skipped, otherwise SCB_SKIPPED would fill
        up with statements and payment receipts.
        """
        parser = self._parseBackup(
            [
                self._createScbSms(
                    "Dear Customer, your statement for SCBPL Credit Card "
                    "ending 1280 is now available."
                )
            ]
        )

        self.assertEqual(parser.msgCounts["SCB"], 1)
        self.assertEqual(parser.msgCounts["SCB_SKIPPED"], 0)
        self.assertEqual(parser.ccTxns, [])


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
