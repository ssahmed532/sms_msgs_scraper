import hashlib
import xml
import xml.etree.ElementTree as ET
from collections import defaultdict
from parser.fbl_sms_parser import FBLSmsParser
from parser.hbl_sms_parser import HBLSmsParser
from parser.mezn_sms_parser import MeznSmsParser
from parser.scb_sms_parser import SCBSmsParser


class SmsBackupFileParser:

    @staticmethod
    def calcSmsMsgHash(sms: xml.etree.ElementTree.Element) -> str:
        """Calculate a cryptographic hash for an SMS msg

        Args:
            sms (xml.etree.ElementTree.Element): the SMS msg

        Returns:
            str: the cryptographic hash of the SMS msg in hex format
        """
        # The identity is (sender short code, stripped body). The sender is
        # part of it because dedup would otherwise reach across senders: an
        # unrelated msg could suppress a later bank msg that merely happened to
        # repeat its text. On the reference backup that silently discarded 23
        # msgs, 4 of them from bank short codes.
        #
        # A received timestamp is deliberately NOT part of the identity, even
        # though a duplicate is by definition received later. Measured on the
        # reference backup, both candidate timestamps fail as a
        # retransmission/distinct-txn discriminator:
        #   - `date` (received): the network redelivers the *same* alert as much
        #     as 2.9 hours late. Two FBL retransmissions arrived 19 minutes and
        #     2.9 hours after their originals, and their bodies carry the txn
        #     time to the second — so they are provably the same txn. Any
        #     "within N minutes" window short enough to be meaningful would have
        #     admitted them as second purchases and inflated the totals.
        #   - `date_sent`: differs on 138 of the 145 repeated (sender, body)
        #     groups, including those same provably-identical FBL txns. Adding
        #     it to the identity would disable dedup almost entirely.
        # Fabricating spending is worse than the residual below, so the identity
        # stays with what the msg *says* rather than when it arrived.
        #
        # Residual limitations, in order of how much they matter:
        #   - FBL and Meezan bodies carry a time of day (to the second and to
        #     the minute respectively), so an identical body means the same txn
        #     and dedup is exact for them.
        #   - HBL and SCB bodies carry a *date only*. Two genuinely distinct
        #     identical purchases (same card, vendor, amount and day) are
        #     therefore indistinguishable from a retransmission, and collapse
        #     into one txn. On the reference backup this affects at most 3 msgs
        #     (2 HBL, 1 SCB) whose repeats arrived 1.5-6 minutes apart; the
        #     other repeats arrived within 8 seconds and are plainly
        #     retransmissions. This is a limit of what the SMS says, not of the
        #     dedup rule.
        #   - only leading/trailing whitespace is normalized here, while the
        #     Meezan parser also collapses *internal* whitespace runs before
        #     matching: two Meezan bodies differing only in internal spacing
        #     hash differently and both parse. Real duplicates are byte-identical
        #     retransmissions, so this is acceptable in practice.
        msgSender = sms.attrib["address"]
        msgBody = sms.attrib["body"].strip()
        # the separator cannot occur in a short code, so no (sender, body) pair
        # can be confused with another by concatenation
        msgIdentity = f"{msgSender}\x00{msgBody}"
        sha256Hash = hashlib.sha3_512(msgIdentity.encode("utf-8")).hexdigest()
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
        """Report whether this msg has already been seen *from this sender*,
        recording it if not.

        Because the sender is part of the identity (see `calcSmsMsgHash`), a msg
        can only ever be suppressed by an earlier msg from the same short code —
        checking before routing cannot discard another sender's msg.

        Deliberately silent: a real backup yields hundreds of duplicates —
        mostly retransmitted promotional msgs from non-bank senders — and
        dumping the original/duplicate body pair for each one buried the actual
        command output under ~1,500 lines of noise. The DUP summary line carries
        the signal instead.
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
            elif SCBSmsParser.isSmsFromSCB(child):
                self.msgCounts[SCBSmsParser.ID] += 1

                if SCBSmsParser.isMsgCreditCardTxn(child):
                    # No asserts here, unlike the HBL branch: SCB really does
                    # send unusable txn msgs (truncated mid-body, or carrying
                    # no amount at all), and an assert would abort the whole
                    # run over them. Skip + warn + count instead.
                    ccTxn = SCBSmsParser.extractDetailsFromTxnMsg(child)
                    if ccTxn is None:
                        # the parser already printed the one warning line
                        # identifying this msg and why it was skipped
                        self.msgCounts["SCB_SKIPPED"] += 1
                    else:
                        self.ccVendors.add(ccTxn.vendor)
                        self.ccTxns.append(ccTxn)
            elif MeznSmsParser.isSmsFromMezn(child):
                self.msgCounts[MeznSmsParser.ID] += 1

                if MeznSmsParser.isMsgDebitTxn(child):
                    debitTxn = MeznSmsParser.extractDetailsFromTxnMsg(child)
                    if debitTxn:
                        self.debitVendors.add(debitTxn.vendor)
                        self.debitTxns.append(debitTxn)
                    else:
                        # MEZN_SKIPPED is reachable two ways, both ending as an
                        # extraction of None: (a) the keyword signal passes but
                        # no template regex matches — i.e. Meezan changed a
                        # template, which is exactly what the independent
                        # signal exists to surface; and (b) the date regex
                        # accepts a token strptime rejects (e.g. 31-Feb-25).
                        # The parser has already printed one warning line
                        # naming the reason and the msg's received date, so
                        # counting is all that is left to do here — and never
                        # asserting: an assert would abort the whole run over
                        # one bad msg.
                        self.msgCounts["MEZN_SKIPPED"] += 1
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
