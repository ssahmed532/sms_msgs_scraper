import unittest
import xml
import xml.etree.ElementTree as ET
from parser.hbl_sms_parser import HBLSmsParser


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

    def _create_dummy_hbl_sms_msg(self, body: str) -> ET.Element:
        smsMsg = self._createBaseSmsMsg()
        smsMsg.set("address", HBLSmsParser.HBL_SHORT_CODES[0])
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


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
