"""HBL (Habib Bank Limited) credit card transaction alerts.

This parser used to be the only one that asserted on its own output, and the
assertions were on *external input* -- the contents of a text message from a
bank. That made a single malformed message fatal to a run of thousands, and it
failed in two different ways depending on how Python was started:

  * normally, `AssertionError` aborted the whole parse;
  * under `python -O`, with assertions stripped, the same message produced a
    transaction carrying the `-1.2345` sentinel as its amount and `None` as its
    currency.

The second is the dangerous one. `-1.2345` is *negative*, so a failed parse that
reached a monthly total silently **reduced** reported spending -- the failure
mode nobody notices, because the number still looks like a number. Both are
gone: extraction returns a `ParseResult`, the sentinels no longer exist, and the
run behaves identically with or without `-O`.

The amount grammar is now the shared one, which also fixes the case that made
the assertion fire: HBL sends ungrouped thousands (`PKR-25170.49`) as well as
grouped ones, and the old pattern *required* comma grouping.
"""

import re
from datetime import datetime

from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, ParseResult, SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import AMOUNT_TOKEN_RE, Money, MoneyError
from sms_msgs_scraper.domain.types import CardReference
from sms_msgs_scraper.domain.tz import DEFAULT_TZ


class HBLSmsParser:
    ID = "HBL"

    HBL_CC_TXN_RE = (
        r"Dear Customer, Your HBL CreditCard \(ending with (?P<last4digits>\d{4})\) "
        r"has been charged at (?P<vendor>.*) for (?P<txnamount>.*) on (?P<txndate>.*)"
    )
    HBL_CC_TXN_PTTRN = re.compile(HBL_CC_TXN_RE)

    # The currency is three uppercase letters, not the old `.*` -- which matched
    # anything at all and then handed it on as a currency code. The amount uses
    # the shared grammar, so grouped and ungrouped thousands both parse and
    # "1,2,3.00" is rejected rather than repaired by stripping commas.
    HBL_CC_TXN_AMOUNT_RE = rf"(?P<currency>[A-Z]{{3}})-(?P<amount>{AMOUNT_TOKEN_RE})"
    HBL_CC_TXN_AMOUNT_PTTRN = re.compile(HBL_CC_TXN_AMOUNT_RE)

    # The format of the transaction date in HBL CC txn SMS msgs:
    #   19/Sep/2023
    HBL_TXN_DATE_FMT = r"%d/%b/%Y"

    @staticmethod
    def isTxnMsg(record: SmsRecord) -> bool:
        """Report whether this message looks like a credit card transaction.

        Deliberately looser than the extraction pattern, so that a changed
        template lands in HBL_SKIPPED with a reason rather than being counted as
        an ordinary HBL message and never looked at.
        """
        body = record.body

        return ("CreditCard" in body) and ("has been charged at" in body)

    @staticmethod
    def _skip(record: SmsRecord, reason: SkipReason, detail: str = "") -> ParseResult:
        return ParseResult.failed(
            ParseDiagnostic.forRecord(HBLSmsParser.ID, record, reason, detail)
        )

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime | None:
        try:
            # All timestamps in an SMS backup file are Karachi local time, so the
            # parsed value is *stamped* with that zone, not converted into it.
            # astimezone() would instead read the naive value as the host
            # machine's local time and shift it -- wrong on any machine not set
            # to +05:00, and enough to move a txn across a day boundary.
            return datetime.strptime(strValue, HBLSmsParser.HBL_TXN_DATE_FMT).replace(
                tzinfo=DEFAULT_TZ
            )
        except ValueError:
            return None

    @staticmethod
    def extract(record: SmsRecord) -> ParseResult:
        """Extract the transaction details out of an HBL CC transaction message.

        Never raises and never asserts: one malformed message becomes one
        diagnostic and costs nothing else in the run.
        """
        m = HBLSmsParser.HBL_CC_TXN_PTTRN.match(record.body)
        if not m:
            return HBLSmsParser._skip(
                record,
                SkipReason.NO_TEMPLATE_MATCH,
                "body does not match the HBL txn format",
            )

        amountMatch = HBLSmsParser.HBL_CC_TXN_AMOUNT_PTTRN.fullmatch(
            m.group("txnamount").strip()
        )
        if not amountMatch:
            return HBLSmsParser._skip(
                record, SkipReason.BAD_AMOUNT, "unrecognized currency/amount clause"
            )

        try:
            money = Money.parse(
                amountMatch.group("currency"), amountMatch.group("amount")
            )
        except MoneyError:
            return HBLSmsParser._skip(record, SkipReason.BAD_AMOUNT)

        if not money.isPositive:
            return HBLSmsParser._skip(
                record, SkipReason.BAD_AMOUNT, "amount is not positive"
            )

        txnDate = HBLSmsParser._convertToDateTime(m.group("txndate").strip().rstrip("."))
        if txnDate is None:
            return HBLSmsParser._skip(record, SkipReason.BAD_DATE)

        vendor = m.group("vendor").strip()
        if not vendor:
            return HBLSmsParser._skip(record, SkipReason.MISSING_VENDOR)

        return ParseResult.ok(
            CreditCardTxnDC(
                money=money,
                date=txnDate,
                vendor=vendor,
                bank=HBLSmsParser.ID,
                # the template pattern already guarantees exactly four digits
                card=CardReference.of(m.group("last4digits")),
            )
        )
