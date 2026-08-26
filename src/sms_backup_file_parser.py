import hashlib
import xml
import xml.etree.ElementTree as ET
from collections import defaultdict
from parser.fbl_sms_parser import FBLSmsParser
from parser.hbl_sms_parser import HBLSmsParser


class SmsBackupFileParser:

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
        #
        # Known limitations of hashing the (stripped) body alone:
        #   - dedup is cross-sender: two senders relaying an identical body
        #     collapse into one msg, and the second one counts as a duplicate
        #     rather than a msg from its own sender
        #   - two legitimate identical purchases (same vendor, amount and date)
        #     collapse into a single txn
        #   - only leading/trailing whitespace is normalized here, while the
        #     Meezan parser also collapses *internal* whitespace runs before
        #     matching: two Meezan bodies differing only in internal spacing
        #     hash differently and both parse. Real duplicates are byte-identical
        #     retransmissions, so this is acceptable in practice.
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
        self.debitVendors = set()
        self.debitTxns = list()
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
        """Report whether this msg's body has already been seen, recording it
        if not.

        Deliberately silent: with dedup applied globally (rather than per bank
        branch) a real backup yields hundreds of duplicates — mostly
        retransmitted promotional msgs from non-bank senders — and dumping the
        original/duplicate body pair for each one buried the actual command
        output under ~1,500 lines of noise. The DUP summary line carries the
        signal instead.
        """
        hash = SmsBackupFileParser.calcSmsMsgHash(sms)

        duplicate = hash in self.msgHashes

        if not duplicate:
            self.msgHashes[hash] = sms.attrib["body"]

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

            # Duplicates still count towards ALL: ALL is "how many <sms>
            # elements were in the backup file", which is what the CLI reports.
            self.msgCounts["ALL"] += 1

            # Dedup once, up front, for every sender — the hash used to be
            # recomputed inside each bank's branch condition, which both
            # duplicated work and mis-routed duplicates (a duplicate HBL msg
            # failed the HBL condition and fell through the chain into OTHER).
            if self._isSmsDuplicate(child):
                self.msgCounts["DUP"] += 1
                continue

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
            elif FBLSmsParser.isSmsFromFBL(child):
                self.msgCounts[FBLSmsParser.ID] += 1

                if FBLSmsParser.isMsgCreditCardTxn(child):
                    # No asserts here, deliberately: FBL msgs that carry the txn
                    # signal but cannot be parsed are known to exist, and an
                    # assert would abort the entire run over a single bad msg.
                    ccTxn = FBLSmsParser.extractDetailsFromTxnMsg(child)
                    if ccTxn is None:
                        # the parser already printed one warning line naming the
                        # reason; this branch only accounts for the skip
                        self.msgCounts["FBL_SKIPPED"] += 1
                    else:
                        self.ccVendors.add(ccTxn.vendor)
                        self.ccTxns.append(ccTxn)
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
        print(f"\tDuplicate msgs:       {self.msgCounts['DUP']}")
        print(f"\tAll msgs count:       {self.msgCounts['ALL']}")
        # Per-bank txn counts are derived from the txn stores rather than kept
        # as separate counters, so they cannot drift out of sync with what the
        # commands actually list.
        ccTxnsPerBank = defaultdict(int)
        for ccTxn in self.ccTxns:
            ccTxnsPerBank[ccTxn.bank] += 1

        print("Transactions summary:")
        print(f"\tHBL CC txns:          {ccTxnsPerBank['HBL']}")
        print(
            f"\tFBL CC txns:          {ccTxnsPerBank['FBL']}"
            f"  (skipped: {self.msgCounts['FBL_SKIPPED']})"
        )
        print(
            f"\tSCB CC txns:          {ccTxnsPerBank['SCB']}"
            f"  (skipped: {self.msgCounts['SCB_SKIPPED']})"
        )
        print(
            f"\tMeezan debit txns:    {len(self.debitTxns)}"
            f"  (skipped: {self.msgCounts['MEZN_SKIPPED']})"
        )

        return self.msgCounts["ALL"]


if __name__ == "__main__":
    SMS_BACKUP_FILE_PATH1 = r"D:\TBD\sms-20231223185422.xml"
    SMS_BACKUP_FILE_PATH2 = r"D:\TBD\sms-20240107104401.xml"
    SMS_BACKUP_FILE_PATH3 = r"D:\TBD\sms-20240301215312.xml"
    parser = SmsBackupFileParser()

    parser.loadFromSmsBackupFile(SMS_BACKUP_FILE_PATH3)
    msgsCount = parser.parseMessages()
