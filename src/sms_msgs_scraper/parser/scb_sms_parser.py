"""Standard Chartered (SCB) credit card transaction alerts.

SCB sends from two short codes. Which two, and the fact that there are two at
all, is now declared once in `domain/registry.py` rather than in a constant
here -- this parser deliberately owns no sender list, because a sender list
that lives next to the code that uses it is exactly what let `9220` go
undeclared while every other part of the tool kept agreeing with itself.
"""

import re
from datetime import datetime

from sms_msgs_scraper.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.common import DEFAULT_TZ
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, ParseResult, SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import AMOUNT_TOKEN_2DP_RE, Money, MoneyError
from sms_msgs_scraper.domain.types import CardReference


class SCBSmsParser:
    ID = "SCB"

    # The SCB CC txn msg format:
    #   Dear Client, PKR 12,450.90 have been paid at PSO SERVICE STATION 7Karachi PAK
    #   on 29-09-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit PKR59563.45. SCBPL
    #
    # The amount uses the shared two-decimal grammar. That does two things the
    # old "[\d,]+\.\d{2}" did not: it rejects malformed grouping such as
    # "1,2,3.00" instead of erasing the commas and reading it as 123.00, and it
    # bounds the digit length so no input can grow the amount without limit.
    # It still rejects the handful of foreign-currency msgs carrying a literal
    # "PKR .00" -- no digits at all before the dot -- which is what puts them in
    # SCB_SKIPPED rather than in the totals.
    SCB_CC_TXN_RE = (
        rf"Dear Client, (?P<currency>[A-Z]{{3}}) (?P<amount>{AMOUNT_TOKEN_2DP_RE}) "
        r"have been paid at (?P<vendor>.+?) on (?P<txndate>\d{2}-\d{2}-\d{2}) "
        r"using Credit Card no (?P<cardmask>[\dx]+)\. Avail Limit"
    )
    SCB_CC_TXN_PTTRN = re.compile(SCB_CC_TXN_RE)

    # The card is masked as either the full "5452xxxxxxxx1280" form (BIN + last
    # 4 digits) or a BIN-only "5495" form that carries no last-4 at all. Those
    # two shapes are exhaustive over the validated corpus, but the txn regex
    # accepts any run of digits and x's, so a third shape can appear.
    #
    # The last-4 pattern therefore does not hard-code the 4-digit BIN: any mask
    # whose digits are interrupted by a masked section ends with its real last 4
    # (a 6-digit BIN, "545221xxxxxx1280", is the industry-standard next step and
    # would otherwise have been recorded as no card at all while the digits sat
    # in the msg). The BIN-only pattern matches an unmasked run of digits, which
    # genuinely carries no last-4.
    SCB_CARD_MASK_PTTRN = re.compile(r"\d*x+(\d{4})")
    SCB_CARD_BIN_ONLY_PTTRN = re.compile(r"\d+")

    # The format of the transaction date in SCB CC txn SMS msgs:
    #   29-09-23
    # (validated against the corpus: the middle token spans 1-12, i.e. it is
    # the month, not the day)
    SCB_TXN_DATE_FMT = r"%d-%m-%y"

    @staticmethod
    def isTxnMsg(record: SmsRecord) -> bool:
        """Report whether this msg looks like a CC txn *attempt*.

        Deliberately looser than the extraction regex, and deliberately keyed
        on "have been paid at" alone: SCB sends unusable txn msgs (truncated
        mid-body, before the date or card ever appear, and some carrying a
        literal "PKR .00" amount for a foreign-currency txn). They must pass
        this check and then fail extraction so they are counted as skipped.
        Also requiring "using Credit Card no" here would silently drop the
        truncated msgs out of that accounting instead.
        """
        return "have been paid at" in record.body

    @staticmethod
    def _skip(record: SmsRecord, reason: SkipReason, detail: str = "") -> ParseResult:
        return ParseResult.failed(
            ParseDiagnostic.forRecord(SCBSmsParser.ID, record, reason, detail)
        )

    @staticmethod
    def _extractCard(cardMask: str) -> tuple[CardReference, bool]:
        """Pull the card reference out of the msg's mask.

        Returns the reference and whether the mask shape was recognised. An
        unrecognised shape is reported, because an absent card reference is
        otherwise indistinguishable from a legitimate BIN-only mask -- a
        masking change at the bank would quietly strip the card digits off
        every SCB transaction and look exactly like normal operation.
        """
        mask = cardMask.strip()

        m = SCBSmsParser.SCB_CARD_MASK_PTTRN.fullmatch(mask)
        if m:
            return CardReference.of(m.group(1)), True

        if SCBSmsParser.SCB_CARD_BIN_ONLY_PTTRN.fullmatch(mask):
            return CardReference.absent(), True

        return CardReference.absent(), False

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime | None:
        try:
            # All timestamps in an SMS backup file are Karachi local time, so the
            # parsed value is *stamped* with that zone, not converted into it.
            # astimezone() would instead read the naive value as the host
            # machine's local time and shift it -- wrong on any machine not set
            # to +05:00, and enough to move a txn across a day boundary.
            return datetime.strptime(strValue, SCBSmsParser.SCB_TXN_DATE_FMT).replace(
                tzinfo=DEFAULT_TZ
            )
        except ValueError:
            return None

    @staticmethod
    def extract(record: SmsRecord) -> ParseResult:
        """Extract the txn details out of an SCB CC txn msg.

        Truncated bodies and "PKR .00" amounts land in a diagnostic by design,
        and the caller counts them as skipped.
        """
        m = SCBSmsParser.SCB_CC_TXN_PTTRN.match(record.body)
        if not m:
            return SCBSmsParser._skip(
                record,
                SkipReason.NO_TEMPLATE_MATCH,
                "truncated body, or a foreign-currency txn carrying no amount",
            )

        try:
            money = Money.parse(m.group("currency"), m.group("amount"))
        except MoneyError:
            return SCBSmsParser._skip(record, SkipReason.BAD_AMOUNT)

        if not money.isPositive:
            return SCBSmsParser._skip(
                record, SkipReason.BAD_AMOUNT, "amount is not positive"
            )

        txnDate = SCBSmsParser._convertToDateTime(m.group("txndate").strip())
        if txnDate is None:
            return SCBSmsParser._skip(record, SkipReason.BAD_DATE)

        vendor = m.group("vendor").strip()
        if not vendor:
            return SCBSmsParser._skip(record, SkipReason.MISSING_VENDOR)

        card, maskRecognized = SCBSmsParser._extractCard(m.group("cardmask"))

        # Known limitation: the vendor capture is kept verbatim, city included.
        # SCB glues the city onto the merchant name without a separating space
        # in a good number of msgs (e.g. "SOUTH CITY HOSPITALKarachi PAK"), so
        # there is no reliable vendor/city split to make here -- guessing one
        # would corrupt the merchant names it got wrong.
        txn = CreditCardTxnDC(
            money=money,
            date=txnDate,
            vendor=vendor,
            bank=SCBSmsParser.ID,
            card=card,
        )

        if not maskRecognized:
            # The spending is real and stays in the totals; the mask shape is
            # still reported, because a silent absent-card is exactly what a
            # change in the bank's masking would look like.
            return ParseResult.okWithWarning(
                txn,
                ParseDiagnostic.forRecord(
                    SCBSmsParser.ID,
                    record,
                    SkipReason.UNRECOGNIZED_CARD_MASK,
                    "card mask matches neither known SCB shape",
                ),
            )

        return ParseResult.ok(txn)
