import re
import xml
from datetime import datetime

from cc_txn import CreditCardTxnDC, CurrencyAmountTuple
from common import DEFAULT_TZ


class SCBSmsParser:
    ID = "SCB"
    # SMS messages from any one of these short codes will be assumed to be
    # from Standard Chartered Bank (SCBPL).
    SCB_SHORT_CODES = ["7220"]

    # The SCB CC txn msg format:
    #   Dear Client, PKR 12,450.90 have been paid at PSO SERVICE STATION 7Karachi PAK
    #   on 29-09-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit PKR59563.45. SCBPL
    #
    # The amount is comma-grouped or plain, but always carries 2 decimals, so
    # [\d,]+\.\d{2} also rejects (by design) the handful of foreign-currency
    # msgs that carry a literal "PKR .00" amount — no digits before the dot.
    SCB_CC_TXN_RE = r"Dear Client, (?P<currency>[A-Z]{3}) (?P<amount>[\d,]+\.\d{2}) have been paid at (?P<vendor>.+?) on (?P<txndate>\d{2}-\d{2}-\d{2}) using Credit Card no (?P<cardmask>[\dx]+)\. Avail Limit"
    SCB_CC_TXN_PTTRN = re.compile(SCB_CC_TXN_RE)

    # The card is masked as either the full "5452xxxxxxxx1280" form (BIN + last
    # 4 digits) or a BIN-only "5495" form that carries no last-4 at all. Those
    # two shapes are exhaustive over the validated corpus.
    SCB_CARD_MASK_PTTRN = re.compile(r"\d{4}x+(\d{4})")

    # The format of the transaction date in SCB CC txn SMS msgs:
    #   29-09-23
    # (validated against the corpus: the middle token spans 1-12, i.e. it is
    # the month, not the day)
    SCB_TXN_DATE_FMT = r"%d-%m-%y"

    @staticmethod
    def isSmsFromSCB(sms: xml.etree.ElementTree.Element) -> bool:
        return sms.attrib["address"] in SCBSmsParser.SCB_SHORT_CODES

    @staticmethod
    def isMsgCreditCardTxn(sms: xml.etree.ElementTree.Element) -> bool:
        """Report whether this msg looks like a CC txn *attempt*.

        Deliberately looser than the extraction regex, and deliberately keyed
        on "have been paid at" alone: SCB sends 26 unusable txn msgs (21
        truncated mid-body, before the date/card ever appear, and 5 carrying a
        literal "PKR .00" amount for a foreign-currency txn). They must pass
        this check and then fail extraction so the orchestrator can count them
        as skipped. Also requiring "using Credit Card no" here would silently
        drop the truncated msgs out of that accounting instead.
        """
        return "have been paid at" in sms.attrib["body"]

    @staticmethod
    def _extractCurrencyAndAmount(currency: str, amount: str) -> CurrencyAmountTuple:
        try:
            return CurrencyAmountTuple(
                currency.strip(), float(amount.strip().replace(",", ""))
            )
        except ValueError:
            print(f"ERROR: unable to parse txn amount into float value: {amount}")

        return None

    @staticmethod
    def _extractCardLastFourDigits(cardMask: str) -> int:
        """Pull the last 4 card digits out of the msg's card mask.

        Returns 0 for the BIN-only mask form ("5495"), which carries no last-4
        digits at all.
        """
        m = SCBSmsParser.SCB_CARD_MASK_PTTRN.fullmatch(cardMask.strip())
        if m:
            return int(m.group(1))

        return 0

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime:
        datetimeObj = None
        try:
            # All timestamps in an SMS backup file are Karachi local time, so
            # the parsed value is *stamped* with that zone, not converted into
            # it. astimezone() would instead read the naive value as the host
            # machine's local time and shift it — wrong on any machine not set
            # to +05:00, and enough to move a txn across a day boundary.
            datetimeObj = datetime.strptime(
                strValue, SCBSmsParser.SCB_TXN_DATE_FMT
            ).replace(tzinfo=DEFAULT_TZ)
        except ValueError:
            print(f"ERROR: unable to parse string into datetime: {strValue}")

        return datetimeObj

    @staticmethod
    def extractDetailsFromTxnMsg(sms: xml.etree.ElementTree.Element) -> CreditCardTxnDC:
        """Extract the txn details out of an SCB CC txn msg.

        Returns None (after printing one warning line) for any msg the txn
        regex does not match — truncated bodies and "PKR .00" amounts land
        here by design, and the caller counts them as skipped.
        """
        m = SCBSmsParser.SCB_CC_TXN_PTTRN.match(sms.attrib["body"])
        if not m:
            print(
                "WARNING: unable to match the SCB CC txn RE against an SCB msg; "
                "skipping it (truncated body or absent amount)"
            )
            return None

        currencyAndAmount = SCBSmsParser._extractCurrencyAndAmount(
            m.group("currency"), m.group("amount")
        )
        if not currencyAndAmount:
            return None

        datetimeObj = SCBSmsParser._convertToDateTime(m.group("txndate").strip())
        if not datetimeObj:
            return None

        # Known limitation: the vendor capture is kept verbatim, city included.
        # SCB glues the city onto the merchant name without a separating space
        # in a good number of msgs (e.g. "SOUTH CITY HOSPITALKarachi PAK"), so
        # there is no reliable vendor/city split to make here — guessing one
        # would corrupt the merchant names it got wrong.
        return CreditCardTxnDC(
            amountTuple=currencyAndAmount,
            date=datetimeObj,
            vendor=m.group("vendor").strip(),
            ccLastFourDigits=SCBSmsParser._extractCardLastFourDigits(
                m.group("cardmask")
            ),
            bank=SCBSmsParser.ID,
        )
