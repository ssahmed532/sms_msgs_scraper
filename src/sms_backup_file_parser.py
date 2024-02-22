import xml.etree.ElementTree as ET
from collections import defaultdict
from parser.hbl_sms_parser import HBLSmsParser


class SmsBackupFileParser:

    # SMS messages from these short codes will be assumed to be from
    # Faysal Bank
    FBL_SHORT_CODES = ["8756"]

    # SMS messages from these short codes will be assumed to be from
    # Standard Chartered Bank
    SCB_SHORT_CODES = ["7220"]

    # SMS messages from these short codes will be assumed to be from
    # Meezan Bank
    MEZN_SHORT_CODES = ["8079", "9779"]

    def __init__(self):
        self.xmlTree = None
        self.xmlRoot = None
        self.ccVendors = set()
        self.ccTxns = list()
        self.expectedMsgs = 0
        self.msgsParsed = 0
        self.msgCounts = defaultdict(int)
        self.hblParser = HBLSmsParser()

    def loadFromSmsBackupFile(self, filepath):
        self.xmlTree = ET.parse(filepath)
        self.xmlRoot = self.xmlTree.getroot()
        self.expectedMsgs = int(self.xmlRoot.attrib["count"])
        assert self.expectedMsgs > 0

    def parseMessages(self):
        """Parse all SMS messages from the XML tree and build an internal
           'representation' (store) of the all the messages.

        Returns:
            int: number of SMS messages parsed successfully
        """
        self.msgCounts["ALL"] = 0

        for child in self.xmlRoot:
            if child.tag == "mms":
                # skip over MMS messages
                continue

            self.msgCounts["ALL"] += 1

            if HBLSmsParser.isSmsFromHBL(child):
                self.msgCounts[HBLSmsParser.ID] += 1

                if HBLSmsParser.isMsgCreditCardTxn(child):
                    ccTxn = HBLSmsParser.extractDetailsFromTxnMsg(child)
                    assert ccTxn is not None
                    assert ccTxn.amountTuple.currency
                    assert ccTxn.amountTuple.amount > 0
                    assert ccTxn.date
                    assert ccTxn.vendor
                    assert ccTxn.ccLastFourDigits > 0

                    self.ccVendors.add(ccTxn.vendor)
                    self.ccTxns.append(ccTxn)
            elif child.attrib["address"] in self.FBL_SHORT_CODES:
                self.msgCounts["FBL"] += 1
            elif child.attrib["address"] in self.SCB_SHORT_CODES:
                self.msgCounts["SCB"] += 1
            elif child.attrib["address"] in self.MEZN_SHORT_CODES:
                self.msgCounts["MEZN"] += 1
            else:
                self.msgCounts["OTHER"] += 1

        print("Parsed messages summary:")
        print(f"\tMessages from HBL:    {self.msgCounts[HBLSmsParser.ID]}")
        print(f"\tMessages from FBL:    {self.msgCounts['FBL']}")
        print(f"\tMessages from SCB:    {self.msgCounts['SCB']}")
        print(f"\tMessages from Meezan: {self.msgCounts['MEZN']}")
        print(f"\tOther SMS Messages:   {self.msgCounts['OTHER']}")
        print(f"\tAll msgs count:       {self.msgCounts['ALL']}")

        return self.msgCounts["ALL"]


if __name__ == "__main__":
    SMS_BACKUP_FILE_PATH = r"D:\TBD\sms-20231223185422.xml"
    parser = SmsBackupFileParser()

    parser.loadFromSmsBackupFile(SMS_BACKUP_FILE_PATH)
    msgsCount = parser.parseMessages()
