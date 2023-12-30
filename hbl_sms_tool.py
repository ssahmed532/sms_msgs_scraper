from collections import namedtuple
import re
import sys
import xml.etree.ElementTree as ET


CreditCardTxn = namedtuple(
    "CreditCardTxn", ["txnAmount", "txnDate", "vendor", "ccLastFourDigits"]
)


class HBLSmsParser:
    # SMS messages from these short codes will be assumed to be from HBL
    HBL_SHORT_CODES = ["4250"]

    def __init__(self):
        self.xml_tree = None
        self.xml_root = None
        self.all_vendors = set()
        self.msgs_parsed = 0

    def _isSmsFromHBL(self, sms):
        return sms.attrib["address"] in HBLSmsParser.HBL_SHORT_CODES

    def _isMsgCreditCardTxn(self, sms):
        msg_body = sms.attrib["body"]

        if ("CreditCard" in msg_body) and ("has been charged at" in msg_body):
            return True

        return False

    def _extractDetailsFromTxnMsg(self, sms):
        ccTxnDetails = None

        ccLast4Digits = "N/A"
        vendor = "N/A"
        txnAmount = "N/A"
        txnDate = "N/A"

        RE_HBL_CC_TXN = r"Dear Customer, Your HBL CreditCard \(ending with (?P<last4digits>\d{4})\) has been charged at (?P<vendor>.*) for (?P<txnamount>.*) on (?P<txndate>.*)"

        p = re.compile(RE_HBL_CC_TXN)
        m = p.match(sms.attrib["body"])
        if m:
            assert len(m.groupdict()) == 4
            print(m.groupdict())

            ccTxnDetails = CreditCardTxn(
                txnAmount=m.group("txnamount").strip(),
                txnDate=m.group("txndate").strip(),
                vendor=m.group("vendor").strip(),
                ccLastFourDigits=m.group("last4digits").strip(),
            )
        else:
            print(f'ERROR: unable to match RE against SMS msg: {sms.attrib["body"]}')

        return ccTxnDetails

    def _printSmsBody(self, sms):
        print(sms.attrib["body"])
        print()

    def loadFromSmsBackupFile(self, backupFilepath):
        self.xml_tree = ET.parse(backupFilepath)
        self.xml_root = self.xml_tree.getroot()
        print(type(self.xml_tree))
        print(type(self.xml_root))

    def printAllVendors(self):
        print(f"Found {len(self.all_vendors)} unique Vendors from parsed SMS messages:")
        print()
        for index, vendor in enumerate(self.all_vendors):
            print(f"Vendor {index}: [{vendor}]")

    def parseMessages(self):
        count = 0
        for child in self.xml_root:
            # print(child.tag, child.attrib)
            if self._isSmsFromHBL(child) and self._isMsgCreditCardTxn(child):
                # print(f'Msg # {idx}, tag value: {child.tag}, attrib(s): {child.attrib}')
                # print()
                print(child)
                self._printSmsBody(child)
                ccTxnDetails = self._extractDetailsFromTxnMsg(child)
                if ccTxnDetails:
                    assert ccTxnDetails.txnAmount
                    assert ccTxnDetails.txnDate
                    assert ccTxnDetails.vendor

                    count += 1

                    self.all_vendors.add(ccTxnDetails.vendor)
                    print(ccTxnDetails)

        return count


if __name__ == "__main__":
    smsParser = HBLSmsParser()
    smsParser.loadFromSmsBackupFile(sys.argv[1])
    msgCount = smsParser.parseMessages()
    print(f"Parsed {msgCount} SMS messages from HBL")
    smsParser.printAllVendors()
