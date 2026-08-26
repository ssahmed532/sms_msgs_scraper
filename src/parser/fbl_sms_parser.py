import re
import xml
import xml.etree.ElementTree as ET
from datetime import datetime

from cc_txn import CreditCardTxnDC, CurrencyAmountTuple
from common import DEFAULT_TZ


class FBLSmsParser:
    ID = "FBL"
    # SMS messages from any one of these short codes will be assumed to
    # be from Faysal Bank (FBL).
    FBL_SHORT_CODES = ["8756"]

    # The format of an FBL CC txn SMS msg:
    #   Dear CUSTOMER NAME, your FBL Card  has been charged for PKR 25170.49 on
    #   20-Sep-23 01:17:16 PM at IMTIAZ SUPER MARKET    KARACHI        PK.
    #
    # Two format quirks the RE has to absorb:
    #   - the spacing around "FBL Card" wobbles between msgs ("your FBL Card  has"
    #     and "your  FBL Card has" both occur), hence the \s+ on both sides
    #   - the txn amount carries NO thousands separators (8100, 18298.9), so the
    #     HBL amount RE — which *requires* comma grouping — cannot be reused here
    FBL_CC_TXN_RE = r"Dear .+?, your\s+FBL Card\s+has been charged for (?P<currency>[A-Z]{3}) (?P<amount>\d+(?:\.\d+)?) on (?P<txndate>\d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2} [AP]M|\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}) at (?P<rest>.+?)\.(?:\s*Available Limit: .*)?$"
    FBL_CC_TXN_PTTRN = re.compile(FBL_CC_TXN_RE)

    # Two txn date formats occur in the corpus:
    #   20-Sep-23 01:17:16 PM   (12-hour, abbreviated month)
    #   04-02-2024 21:11:41     (24-hour, numeric month, 4-digit year)
    FBL_TXN_DATE_FMTS = [r"%d-%b-%y %I:%M:%S %p", r"%d-%m-%Y %H:%M:%S"]

    # The txn detail tail is a fixed-width layout: a 22-char vendor column, a
    # 14-char city column, then a 2-letter country code.
    VENDOR_COL_WIDTH = 22

    @staticmethod
    def isSmsFromFBL(sms: xml.etree.ElementTree.Element) -> bool:
        return sms.attrib["address"] in FBLSmsParser.FBL_SHORT_CODES

    @staticmethod
    def isMsgCreditCardTxn(sms: xml.etree.ElementTree.Element) -> bool:
        msgBody = sms.attrib["body"]

        if ("FBL Card" in msgBody) and ("has been charged for" in msgBody):
            return True

        return False

    @staticmethod
    def _extractVendor(rest: str) -> str:
        """Pull the vendor name out of the fixed-width vendor/city/country tail
        of an FBL txn msg.

        Some bodies escape an apostrophe with a backslash (M&M\\'S PHARMACY), so
        the escape chars go first. The columns are normally separated by runs of
        padding spaces, but a vendor that exactly fills its 22-char column leaves
        no separator at all (SHUJAAT FILLING STATIO KARACHI        PK) — that
        case is recovered by slicing the column width instead of splitting.
        """
        rest = rest.replace("\\", "")

        parts = re.split(r" {2,}", rest)
        if len(parts) >= 3:
            return parts[0].strip()

        return rest[: FBLSmsParser.VENDOR_COL_WIDTH].strip()

    @staticmethod
    def _extractCurrencyAndAmount(currency: str, amount: str) -> CurrencyAmountTuple:
        try:
            return CurrencyAmountTuple(currency.strip(), float(amount.strip()))
        except ValueError:
            print(f"ERROR: unable to parse FBL txn amount into float value: {amount}")

        return None

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime:
        for dateFmt in FBLSmsParser.FBL_TXN_DATE_FMTS:
            try:
                # All timestamps in an SMS backup file are Karachi local time, so
                # the parsed value is *stamped* with that zone, not converted into
                # it. astimezone() would instead read the naive value as the host
                # machine's local time and shift it — wrong on any machine not set
                # to +05:00, and enough to move a txn across a day boundary.
                return datetime.strptime(strValue, dateFmt).replace(tzinfo=DEFAULT_TZ)
            except ValueError:
                continue

        print(f"ERROR: unable to parse string into datetime: {strValue}")

        return None

    @staticmethod
    def extractDetailsFromTxnMsg(sms) -> CreditCardTxnDC | None:
        """Extract the txn details out of an FBL CC txn msg.

        Returns None (after printing a warning) for any msg that carries the
        txn signal but cannot be parsed — the caller counts those as skipped.
        No asserts: a single malformed real msg must not abort the whole run.
        """
        m = FBLSmsParser.FBL_CC_TXN_PTTRN.match(sms.attrib["body"].strip())
        if not m:
            print("ERROR: unable to match FBL CC txn RE against SMS msg")
            return None

        currencyAndAmount = FBLSmsParser._extractCurrencyAndAmount(
            m.group("currency"), m.group("amount")
        )
        if (currencyAndAmount is None) or (currencyAndAmount.amount <= 0):
            print("ERROR: unusable currency/amount in FBL CC txn msg")
            return None

        datetimeObj = FBLSmsParser._convertToDateTime(m.group("txndate").strip())
        if datetimeObj is None:
            return None

        vendor = FBLSmsParser._extractVendor(m.group("rest"))
        if not vendor:
            print("ERROR: no vendor name found in FBL CC txn msg")
            return None

        # FBL txn msgs carry no card digits at all, so the last-four field stays
        # at its 0 default.
        return CreditCardTxnDC(
            amountTuple=currencyAndAmount,
            date=datetimeObj,
            vendor=vendor,
            bank=FBLSmsParser.ID,
        )
