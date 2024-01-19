import re
import xml.etree.ElementTree as ET

from datetime import datetime

from cc_txn import CreditCardTxnDC
from cc_txn import CurrencyAmountTuple


class HBLSmsParser:
    # SMS messages from these short codes will be assumed to be from HBL
    HBL_SHORT_CODES = ["4250"]

    DEFAULT_CURRENCY = "PKR"

    HBL_CC_TXN_RE = r"Dear Customer, Your HBL CreditCard \(ending with (?P<last4digits>\d{4})\) has been charged at (?P<vendor>.*) for (?P<txnamount>.*) on (?P<txndate>.*)"
    HBL_CC_TXN_PTTRN = re.compile(HBL_CC_TXN_RE)
    HBL_CC_TXN_AMOUNT_RE = (
        r"(?P<currency>.*)-(?P<amount>\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b)"
    )
    # The format of the transaction date in HBL CC txn SMS msgs:
    #   19/Sep/2023
    HBL_TXN_DATE_FMT = r"%d/%b/%Y"

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
    def _extractCurrencyAndAmount(strValue) -> CurrencyAmountTuple:
        currency = None
        amount = -1.2345

        pattern = re.compile(HBLSmsParser.HBL_CC_TXN_AMOUNT_RE)
        m = pattern.match(strValue)
        if m:
            # print(m.groupdict())
            # print(f'Parsed currency: [{m.group("currency").strip()}]')
            # print(f'Parsed amount:   [{m.group("amount").strip()}]')

            currency = m.group("currency").strip()
            try:
                amount = float(m.group("amount").strip().replace(",", ""))
                return CurrencyAmountTuple(currency, amount)
            except ValueError:
                print(
                    f'ERROR: unable to parse txn amount into float value: {m.group("amount").strip()}'
                )

        return CurrencyAmountTuple(currency, amount)

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime:
        datetimeObj = None
        try:
            datetimeObj = datetime.strptime(strValue, HBLSmsParser.HBL_TXN_DATE_FMT)
        except ValueError:
            print(f"ERROR: unable to parse string into datetime: {strValue}")

        return datetimeObj

    @staticmethod
    def _extractDetailsFromTxnMsg(sms) -> CreditCardTxnDC:
        ccTxn = None

        m = HBLSmsParser.HBL_CC_TXN_PTTRN.match(sms.attrib["body"])
        if m:
            assert len(m.groupdict()) == 4
            # print(m.groupdict())

            ccLast4Digits = -1

            try:
                strValue = m.group("last4digits").strip()
                ccLast4Digits = int(strValue)
            except ValueError:
                ccLast4Digits = -9999
                print(
                    f"ERROR: unable to parse the last 4 digits of the CC for txn: {strValue}"
                )

            amountValue = m.group("txnamount").strip()
            currencyAndAmount = HBLSmsParser._extractCurrencyAndAmount(amountValue)
            assert currencyAndAmount.currency and (currencyAndAmount.amount > 0)

            datetimeObj = HBLSmsParser._convertToDateTime(
                m.group("txndate").strip().rstrip(".")
            )
            assert datetimeObj

            ccTxn = CreditCardTxnDC(
                amountTuple=currencyAndAmount,
                date=datetimeObj,
                vendor=m.group("vendor").strip(),
                ccLastFourDigits=ccLast4Digits,
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
                    assert ccTxn.amountTuple.currency
                    assert ccTxn.amountTuple.amount > 0
                    assert ccTxn.date
                    assert ccTxn.vendor
                    assert ccTxn.ccLastFourDigits > 0

                    count += 1

                    self.all_vendors.add(ccTxn.vendor)
                    self.cc_txns.append(ccTxn)

        return count
