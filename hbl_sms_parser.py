import re
import xml.etree.ElementTree as ET

from cc_txn import CreditCardTxnDC


class HBLSmsParser:
    # SMS messages from these short codes will be assumed to be from HBL
    HBL_SHORT_CODES = ["4250"]

    DEFAULT_CURRENCY = "PKR"

    HBL_CC_TXN_RE = r"Dear Customer, Your HBL CreditCard \(ending with (?P<last4digits>\d{4})\) has been charged at (?P<vendor>.*) for (?P<txnamount>.*) on (?P<txndate>.*)"
    HBL_CC_TXN_PTTRN = re.compile(HBL_CC_TXN_RE)

    def __init__(self):
        self.xml_tree = None
        self.xml_root = None
        self.all_vendors = set()
        self.cc_txns = list()
        self.msgs_parsed = 0

    @staticmethod
    def _isSmsFromHBL(sms):
        return sms.attrib["address"] in HBLSmsParser.HBL_SHORT_CODES

    @staticmethod
    def _isMsgCreditCardTxn(sms):
        msg_body = sms.attrib["body"]

        if ("CreditCard" in msg_body) and ("has been charged at" in msg_body):
            return True

        return False

    @staticmethod
    def _extractDetailsFromTxnMsg(sms) -> CreditCardTxnDC:
        ccTxn = None

        ccLast4Digits = "N/A"
        vendor = "N/A"
        txnAmount = "N/A"
        txnDate = "N/A"

        m = HBLSmsParser.HBL_CC_TXN_PTTRN.match(sms.attrib["body"])
        if m:
            assert len(m.groupdict()) == 4
            # print(m.groupdict())

            # TODO:
            # parse CC txn details into the correct types
            # after the necessary validation.

            ccTxn = CreditCardTxnDC(
                amount=m.group("txnamount").strip(),
                currency=HBLSmsParser.DEFAULT_CURRENCY,
                date=m.group("txndate").strip(),
                vendor=m.group("vendor").strip(),
                ccLastFourDigits=m.group("last4digits").strip(),
            )
        else:
            print(f'ERROR: unable to match RE against SMS msg: {sms.attrib["body"]}')

        return ccTxn

    @staticmethod
    def _printSmsBody(sms):
        print(sms.attrib["body"])
        print()

    def loadFromSmsBackupFile(self, backupFilepath):
        self.xml_tree = ET.parse(backupFilepath)
        self.xml_root = self.xml_tree.getroot()
        # print(type(self.xml_tree))
        # print(type(self.xml_root))

    def printAllVendors(self):
        print(f"Found {len(self.all_vendors)} unique Vendors from parsed SMS messages:")
        print()

        sorted_vendors = sorted(self.all_vendors)
        for index, vendor in enumerate(sorted_vendors, start=1):
            print(f"Vendor {index}: [{vendor}]")

    def printCCTxns(self):
        print(
            f"Found {len(self.cc_txns)} HBL CC transactions from parsed SMS messages:"
        )
        print()
        for index, txn in enumerate(self.cc_txns, start=1):
            print(f"CC Txn {index}: {txn}")

    def parseMessages(self):
        """Parse all SMS messages from the XML tree and build an internal
           'representation' (store) of the all the messages.

        Returns:
            int: number of SMS messages parsed successfully
        """
        count = 0
        for child in self.xml_root:
            # TODO:
            # add a check/filter for MMS; we aren't interested in
            # parsing & processing MMS messages at the moment
            # print(child.tag, child.attrib)
            # TODO:
            # parse *ALL* SMS Messages from HBL and store in different
            # data stores depending on the type of SMS msg!
            if self._isSmsFromHBL(child) and self._isMsgCreditCardTxn(child):
                # print(f'Msg # {idx}, tag value: {child.tag}, attrib(s): {child.attrib}')
                # print()
                # print(child)
                # self._printSmsBody(child)
                ccTxn = self._extractDetailsFromTxnMsg(child)
                if ccTxn:
                    assert ccTxn.amount
                    assert ccTxn.date
                    assert ccTxn.vendor

                    count += 1

                    self.all_vendors.add(ccTxn.vendor)
                    # print(ccTxnDetails)
                    self.cc_txns.append(ccTxn)

        return count
