import unittest
import xml
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from parser.hbl_sms_parser import HBLSmsParser

from cc_txn import CurrencyAmountTuple
from common import DEFAULT_TZ


class TestHBLSmsParser(unittest.TestCase):

    def _createBaseSmsMsg(self) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("protocol", "0")
        sms.set("date", "1696265826791")
        sms.set("type", "1")
        sms.set("subject", "null")
        sms.set("toa", "null")
        sms.set("sc_toa", "null")
        sms.set("read", "1")
        sms.set("status", "-1")
        sms.set("locked", "0")
        sms.set("date_sent", "1696265824000")
        sms.set("sub_id", "1")
        sms.set("readable_date", "Oct 2, 2023 9:57:06 PM")

        return sms

    def _create_dummy_hbl_sms_msg(
        self, body: str, shortCode: str = HBLSmsParser.HBL_SHORT_CODES[0]
    ) -> ET.Element:
        smsMsg = self._createBaseSmsMsg()
        smsMsg.set("address", shortCode)
        smsMsg.set("body", body)
        smsMsg.set("service_center", "+92308984567")
        smsMsg.set("contact_name", "(Unknown)")

        # tree = ET.ElementTree(smsMsg)
        # tree.write("hbl_sms_msg.xml")

        return smsMsg

    def _create_dummy_non_hbl_sms_msg(self) -> ET.Element:
        smsMsg = self._createBaseSmsMsg()
        smsMsg.set("address", "7220")
        smsMsg.set(
            "body",
            "Dear Client, PKR 15134.00 have been paid at SHELL (SUNSET BOULEVAR KARACHI PAK on 02-10-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit PKR44429.45. SCBPL",
        )
        smsMsg.set("service_center", "+92308984321")
        smsMsg.set("contact_name", "SCB shortcode")

        # tree = ET.ElementTree(smsMsg)
        # tree.write("non-hbl_sms_msg.xml")

        return smsMsg

    def test_hbl_sms_msg(self):
        """Test method to verify that an HBL sms msg is identified
        as an HBL SMS msg by the HBLSmsParser.isSmsFromHBL() utility method.
        """
        body = "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
        sms = self._create_dummy_hbl_sms_msg(body)

        parser = HBLSmsParser()

        self.assertTrue(parser.isSmsFromHBL(sms))

    def test_non_hbl_sms_msg(self):
        """Test method to verify that a non-HBL sms msg is identified
        as a non-HBL SMS msg by the HBLSmsParser.isSmsFromHBL() utility method.
        """
        sms = self._create_dummy_non_hbl_sms_msg()

        parser = HBLSmsParser()

        self.assertFalse(parser.isSmsFromHBL(sms))

    def test_all_hbl_short_codes_recognized(self):
        """Test method to verify that every registered HBL short code is
        identified as HBL. HBL migrated CC txn alerts from 4250 to 14250 in
        mid-Jan 2025, so both codes must stay recognized — dropping either
        one silently loses part of the txn history.
        """
        body = "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."

        for shortCode in HBLSmsParser.HBL_SHORT_CODES:
            with self.subTest(shortCode=shortCode):
                sms = self._create_dummy_hbl_sms_msg(body, shortCode)

                self.assertTrue(HBLSmsParser.isSmsFromHBL(sms))

    def test_hbl_sms_isMsgCreditCardTxn(self):
        """Test method to verify that a valid HBL (CC Txn) sms msg is identified
        as a CC Txn msg by the HBLSmsParser.isMsgCreditCardTxn() utility method.
        """
        body = "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at A MOOSAJEE SONS for PKR-22,001.00 on 25/Sep/2023."
        sms = self._create_dummy_hbl_sms_msg(body)

        parser = HBLSmsParser()

        self.assertTrue(parser.isMsgCreditCardTxn(sms))

    def test_hbl_non_sms1_isMsgCreditCardTxn(self):
        """Test method to verify that a valid HBL (non CC Txn) sms msg is identified
        as a non-CC Txn msg by the HBLSmsParser.isMsgCreditCardTxn() utility method.
        """
        body = "648975 is your One Time Password (OTP) for the internet transaction on HBL Card ending with 0077. This OTP is valid for 10 mins. Do not share OTP with anyone."
        sms = self._create_dummy_hbl_sms_msg(body)

        parser = HBLSmsParser()

        self.assertFalse(parser.isMsgCreditCardTxn(sms))

    def test_extractDetailsFromTxnMsg(self):
        """Test method to verify that all txn fields are extracted from a
        valid HBL CC txn msg — including the trailing period being stripped
        off the txn date before it is parsed.
        """
        body = "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
        sms = self._create_dummy_hbl_sms_msg(body)

        ccTxn = HBLSmsParser.extractDetailsFromTxnMsg(sms)

        self.assertIsNotNone(ccTxn)
        self.assertEqual(ccTxn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(ccTxn.ccLastFourDigits, 8526)
        self.assertEqual(ccTxn.amountTuple, CurrencyAmountTuple("PKR", 25170.49))
        self.assertEqual(ccTxn.date, datetime(2023, 10, 1, tzinfo=DEFAULT_TZ))

    def test_extractDetailsFromTxnMsg_non_txn_msg(self):
        """Test method to verify that a body not matching the CC txn format
        yields None rather than a partially-populated txn.
        """
        body = "648975 is your One Time Password (OTP) for the internet transaction on HBL Card ending with 0077. This OTP is valid for 10 mins. Do not share OTP with anyone."
        sms = self._create_dummy_hbl_sms_msg(body)

        self.assertIsNone(HBLSmsParser.extractDetailsFromTxnMsg(sms))

    def test_extractCurrencyAndAmount_formats(self):
        """Test method to verify currency/amount parsing across the formats
        seen in HBL CC txn msgs: comma-grouped thousands, sub-1,000 amounts,
        amounts without decimals, and each supported currency.
        """
        for strValue, expected in [
            ("PKR-25,170.49", CurrencyAmountTuple("PKR", 25170.49)),
            ("PKR-450.00", CurrencyAmountTuple("PKR", 450.00)),
            ("PKR-1,000", CurrencyAmountTuple("PKR", 1000.00)),
            ("USD-4.02", CurrencyAmountTuple("USD", 4.02)),
            ("CAD-1,234.56", CurrencyAmountTuple("CAD", 1234.56)),
        ]:
            with self.subTest(txnAmount=strValue):
                self.assertEqual(
                    HBLSmsParser._extractCurrencyAndAmount(strValue), expected
                )

    def test_extractCurrencyAndAmount_ungrouped_thousands(self):
        """Test method pinning a known limitation: the amount regex requires
        comma-grouped thousands, so an amount >= 1,000 written without commas
        does not parse and yields the (None, -1.2345) failure sentinel. Via
        extractDetailsFromTxnMsg() that sentinel then trips the currency
        assert and aborts the whole parse run — if HBL ever drops the comma
        grouping, HBL_CC_TXN_AMOUNT_RE is the regex to relax.
        """
        result = HBLSmsParser._extractCurrencyAndAmount("PKR-25170.49")

        self.assertIsNone(result.currency)
        self.assertEqual(result.amount, -1.2345)

    def test_convertToDateTime_stamps_karachi_tz(self):
        """Test method to verify that a txn date is stamped as Karachi local
        time — the wall-clock date/time from the msg is preserved exactly and
        the +05:00 offset is attached, with no shift applied.
        """
        datetimeObj = HBLSmsParser._convertToDateTime("01/Oct/2023")

        self.assertEqual(datetimeObj, datetime(2023, 10, 1, tzinfo=DEFAULT_TZ))
        self.assertEqual(datetimeObj.utcoffset(), timedelta(hours=5))
        # the naive wall-clock must survive untouched: a converting (rather
        # than stamping) implementation moves these
        self.assertEqual(datetimeObj.replace(tzinfo=None), datetime(2023, 10, 1))

    def test_convertToDateTime_preserves_calendar_date(self):
        """Test method to verify that the calendar date is never shifted across
        a day boundary, including at year-end where a shift is most visible.
        """
        for strValue, expected in [
            ("01/Jan/2024", (2024, 1, 1)),
            ("31/Dec/2024", (2024, 12, 31)),
            ("29/Feb/2024", (2024, 2, 29)),
        ]:
            with self.subTest(txnDate=strValue):
                datetimeObj = HBLSmsParser._convertToDateTime(strValue)

                self.assertEqual(
                    (datetimeObj.year, datetimeObj.month, datetimeObj.day), expected
                )
                self.assertEqual((datetimeObj.hour, datetimeObj.minute), (0, 0))

    def test_convertToDateTime_malformed_date(self):
        """Test method to verify that an unparseable txn date yields None
        rather than raising.
        """
        self.assertIsNone(HBLSmsParser._convertToDateTime("2023-10-01"))


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
