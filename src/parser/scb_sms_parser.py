import re
import xml.etree.ElementTree as ET
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
    # two shapes are exhaustive over the validated corpus, but the txn regex
    # accepts any run of digits and x's, so a third shape can appear without
    # warning the day SCB changes its masking.
    #
    # The last-4 pattern therefore does not hard-code the 4-digit BIN: any mask
    # whose digits are interrupted by a masked section ends with its real last 4
    # (a 6-digit BIN, "545221xxxxxx1280", is the industry-standard next step and
    # would otherwise have been recorded as card 0 while the digits sat in the
    # msg). The BIN-only pattern matches an unmasked run of digits, which
    # genuinely carries no last-4. A mask matching neither is warned about
    # rather than silently reported as 0.
    SCB_CARD_MASK_PTTRN = re.compile(r"\d*x+(\d{4})")
    SCB_CARD_BIN_ONLY_PTTRN = re.compile(r"\d+")

    # The format of the transaction date in SCB CC txn SMS msgs:
    #   29-09-23
    # (validated against the corpus: the middle token spans 1-12, i.e. it is
    # the month, not the day)
    SCB_TXN_DATE_FMT = r"%d-%m-%y"

    @staticmethod
    def isSmsFromSCB(sms: ET.Element) -> bool:
        return sms.attrib["address"] in SCBSmsParser.SCB_SHORT_CODES

    @staticmethod
    def isMsgCreditCardTxn(sms: ET.Element) -> bool:
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
    def _warnSkippedMsg(sms: ET.Element, reason: str) -> None:
        """Print exactly one warning line for a msg that cannot be parsed.

        The msg is identified by its readable_date rather than by its body: a
        backup is personal financial data, and a real run skips 26 SCB msgs,
        which would mean dumping 26 real messages to stdout. The received
        date is enough to locate the msg in the backup file. .attrib.get() so
        that a backup missing the attribute cannot make the warning itself
        raise.
        """
        receivedAt = sms.attrib.get("readable_date", "?")
        print(f"WARNING: skipping SCB msg received {receivedAt}: {reason}")

    @staticmethod
    def _extractCurrencyAndAmount(currency: str, amount: str) -> CurrencyAmountTuple:
        try:
            return CurrencyAmountTuple(
                currency.strip(), float(amount.strip().replace(",", ""))
            )
        except ValueError:
            # the caller emits the single warning line for this msg
            return None

    @staticmethod
    def _extractCardLastFourDigits(cardMask: str) -> int:
        """Pull the last 4 card digits out of the msg's card mask.

        Returns 0 when the mask carries no last-4 digits at all — the BIN-only
        form ("5495") — and also, with a warning, for a mask shape that is
        neither form. The warning matters: returning a bare 0 for an
        unrecognized shape is indistinguishable from a legitimate BIN-only mask,
        so a masking change at the bank would quietly strip the card digits off
        every SCB txn.
        """
        mask = cardMask.strip()

        m = SCBSmsParser.SCB_CARD_MASK_PTTRN.fullmatch(mask)
        if m:
            return int(m.group(1))

        if not SCBSmsParser.SCB_CARD_BIN_ONLY_PTTRN.fullmatch(mask):
            print(
                "WARNING: unrecognized SCB card mask shape; recording the txn "
                "with no card digits"
            )

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
            # the caller emits the single warning line for this msg
            pass

        return datetimeObj

    @staticmethod
    def extractDetailsFromTxnMsg(sms: ET.Element) -> CreditCardTxnDC:
        """Extract the txn details out of an SCB CC txn msg.

        Returns None — after printing exactly one warning line naming the
        reason and the msg's received date — for any msg that cannot be
        parsed. Truncated bodies and "PKR .00" amounts land here by design,
        and the caller counts them as skipped.
        """
        m = SCBSmsParser.SCB_CC_TXN_PTTRN.match(sms.attrib["body"])
        if not m:
            SCBSmsParser._warnSkippedMsg(
                sms,
                "body does not match the SCB CC txn format (truncated msg, or"
                " a foreign-currency txn carrying no PKR amount)",
            )
            return None

        currencyAndAmount = SCBSmsParser._extractCurrencyAndAmount(
            m.group("currency"), m.group("amount")
        )
        if not currencyAndAmount:
            SCBSmsParser._warnSkippedMsg(
                sms, "unable to parse the txn amount into a float value"
            )
            return None

        datetimeObj = SCBSmsParser._convertToDateTime(m.group("txndate").strip())
        if not datetimeObj:
            SCBSmsParser._warnSkippedMsg(
                sms, f'unparseable txn date: {m.group("txndate").strip()}'
            )
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
