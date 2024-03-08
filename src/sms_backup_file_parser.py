import hashlib
import xml
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

    @staticmethod
    def calcSmsMsgHash(sms: xml.etree.ElementTree.Element) -> str:
        """Calculate a cryptographic hash for an SMS msg

        Args:
            sms (xml.etree.ElementTree.Element): the SMS msg

        Returns:
            str: the cryptographic hash of the SMS msg in hex format
        """
        # to be completely correct, the hash of a msg *SHOULD* include:
        #   - the sender short code
        #   - the contents of the msg (body)
        #   - the date/timestamp it was received (or sent!)
        msgBody = sms.attrib["body"].strip()
        sha256Hash = hashlib.sha3_512(msgBody.encode("utf-8")).hexdigest()
        return sha256Hash

    def printSmsMsg(sms: xml.etree.ElementTree.Element) -> None:
        strValue = ET.tostring(sms, encoding="utf-8").decode("utf-8")
        print(strValue)

    def smsMsgToString(sms: xml.etree.ElementTree.Element) -> str:
        return ET.tostring(sms, encoding="utf-8").decode("utf-8")

    def __init__(self):
        self.xmlTree = None
        self.xmlRoot = None
        self.ccVendors = set()
        self.ccTxns = list()
        self.expectedMsgs = 0
        self.msgsParsed = 0
        self.msgCounts = defaultdict(int)
        # dict to keep track of duplicate SMS Msgs:
        #   hash -> msg body/content
        self.msgHashes = {}
        self.hblParser = HBLSmsParser()

    def loadFromSmsBackupFile(self, filepath: str):
        self.xmlTree = ET.parse(filepath)
        self.xmlRoot = self.xmlTree.getroot()
        self.expectedMsgs = int(self.xmlRoot.attrib["count"])
        assert self.expectedMsgs > 0

    def _isSmsDuplicate(self, sms: xml.etree.ElementTree.Element) -> bool:
        duplicate = False
        hash = SmsBackupFileParser.calcSmsMsgHash(sms)

        duplicate = hash in self.msgHashes

        if not duplicate:
            self.msgHashes[hash] = sms.attrib["body"]

        if duplicate:
            print("Original msg body:")
            print(f"\t{self.msgHashes[hash]}")
            print("Duplicate msg:")
            print(f"\tsender={sms.attrib["address"]}, {sms.attrib["body"]}")
            print()
            print()

        return duplicate

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

            # TODO:
            #   check for duplicate messages
            #   if an SMS msg is detected as duplicate, skip/ignore it
            #   e.g.
            #       2 identical SMS messages from Faysal Bank on Saturday 02/24/2024 @ 5:56pm PKT
            #       received 1 min apart!
            #

            self.msgCounts["ALL"] += 1

            if HBLSmsParser.isSmsFromHBL(child) and (not self._isSmsDuplicate(child)):
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
            elif (child.attrib["address"] in self.FBL_SHORT_CODES) and (not self._isSmsDuplicate(child)):
                self.msgCounts["FBL"] += 1
            elif (child.attrib["address"] in self.SCB_SHORT_CODES) and (not self._isSmsDuplicate(child)):
                self.msgCounts["SCB"] += 1
            elif (child.attrib["address"] in self.MEZN_SHORT_CODES) and (not self._isSmsDuplicate(child)):
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
    SMS_BACKUP_FILE_PATH1 = r"D:\TBD\sms-20231223185422.xml"
    SMS_BACKUP_FILE_PATH2 = r"D:\TBD\sms-20240107104401.xml"
    SMS_BACKUP_FILE_PATH3 = r"D:\TBD\sms-20240301215312.xml"
    parser = SmsBackupFileParser()

    parser.loadFromSmsBackupFile(SMS_BACKUP_FILE_PATH3)
    msgsCount = parser.parseMessages()
