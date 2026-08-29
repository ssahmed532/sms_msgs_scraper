"""Faysal Bank (FBL) credit card transaction alerts."""

import re
from datetime import datetime

from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, ParseResult, SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import AMOUNT_TOKEN_PLAIN_RE, Money, MoneyError
from sms_msgs_scraper.domain.tz import DEFAULT_TZ


class FBLSmsParser:
    ID = "FBL"

    # The format of an FBL CC txn SMS msg:
    #   Dear CUSTOMER NAME, your FBL Card  has been charged for PKR 25170.49 on
    #   20-Sep-23 01:17:16 PM at IMTIAZ SUPER MARKET    KARACHI        PK.
    #
    # Two format quirks the RE has to absorb:
    #   - the spacing of the card clause wobbles between msgs: all three of
    #     "your FBL Card  has", "your  FBL Card has" and "your FBL Card has"
    #     occur, hence the \s+ on both sides of "FBL Card"
    #   - the txn amount carries NO thousands separators (8100, 18298.9), so
    #     this uses the shared grammar's separator-free variant rather than a
    #     pattern of its own. FBL has never sent a comma in an amount, so a
    #     comma-grouped body here is a template change worth reporting, not an
    #     amount worth trusting.
    FBL_CC_TXN_RE = (
        r"Dear .+?, your\s+FBL Card\s+has been charged for "
        rf"(?P<currency>[A-Z]{{3}}) (?P<amount>{AMOUNT_TOKEN_PLAIN_RE}) on "
        r"(?P<txndate>\d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2} [AP]M"
        r"|\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}) at (?P<rest>.+?)\."
        r"(?:\s*Available Limit: .*)?$"
    )
    FBL_CC_TXN_PTTRN = re.compile(FBL_CC_TXN_RE)

    # Two txn date formats occur in the corpus:
    #   20-Sep-23 01:17:16 PM   (12-hour, abbreviated month)
    #   04-02-2024 21:11:41     (24-hour, numeric month, 4-digit year)
    FBL_TXN_DATE_FMTS = [r"%d-%b-%y %I:%M:%S %p", r"%d-%m-%Y %H:%M:%S"]

    # The txn detail tail is a fixed-width layout: a 22-char vendor field, one
    # separator space, then the city field, then a 2-letter country code.
    VENDOR_COL_WIDTH = 22
    # Runs of padding spaces are what separates the fixed-width fields.
    VENDOR_COL_SEP_PTTRN = re.compile(r" {2,}")

    @staticmethod
    def isTxnMsg(record: SmsRecord) -> bool:
        body = record.body

        return ("FBL Card" in body) and ("has been charged for" in body)

    @staticmethod
    def _skip(record: SmsRecord, reason: SkipReason, detail: str = "") -> ParseResult:
        return ParseResult.failed(
            ParseDiagnostic.forRecord(FBLSmsParser.ID, record, reason, detail)
        )

    @staticmethod
    def _extractVendor(rest: str) -> str:
        """Pull the vendor name out of the fixed-width vendor/city/country tail
        of an FBL txn msg.

        The escape chars go FIRST, and the ordering is load-bearing: some bodies
        escape an apostrophe with a backslash (M&M\\'S PHARMACY), and that
        backslash is an *extra* character on top of the 22-char vendor field
        rather than one occupying a column position -- measured over the corpus,
        the city field starts at index 23 of an unescaped tail but at index 24 of
        an escaped one. Slicing before unescaping would therefore chop the last
        letter off any escaped vendor that fills its column.

        The fields are normally separated by runs of padding spaces, but a vendor
        that exactly fills its 22-char field leaves only the single separator
        space (SHUJAAT FILLING STATIO KARACHI        PK) -- that case is
        recovered by slicing the field width instead of splitting.
        """
        rest = rest.replace("\\", "")

        parts = FBLSmsParser.VENDOR_COL_SEP_PTTRN.split(rest)
        if len(parts) >= 3:
            return parts[0].strip()

        return rest[: FBLSmsParser.VENDOR_COL_WIDTH].strip()

    @staticmethod
    def _convertToDateTime(strValue: str) -> datetime | None:
        for dateFmt in FBLSmsParser.FBL_TXN_DATE_FMTS:
            try:
                # All timestamps in an SMS backup file are Karachi local time, so
                # the parsed value is *stamped* with that zone, not converted into
                # it. astimezone() would instead read the naive value as the host
                # machine's local time and shift it -- wrong on any machine not
                # set to +05:00, and enough to move a txn across a day boundary.
                return datetime.strptime(strValue, dateFmt).replace(tzinfo=DEFAULT_TZ)
            except ValueError:
                continue

        return None

    @staticmethod
    def extract(record: SmsRecord) -> ParseResult:
        """Extract the txn details out of an FBL CC txn msg.

        Returns a diagnostic, never an exception, for any message that carries
        the txn signal but cannot be parsed; the caller counts those as skipped.
        """
        m = FBLSmsParser.FBL_CC_TXN_PTTRN.match(record.body.strip())
        if not m:
            return FBLSmsParser._skip(
                record,
                SkipReason.NO_TEMPLATE_MATCH,
                "body does not match the FBL txn format",
            )

        try:
            # An amount carrying more decimal places than the currency has minor
            # units is refused here rather than rounded away at render time --
            # which is what a `,.2f` on a float silently did.
            money = Money.parse(m.group("currency"), m.group("amount"))
        except MoneyError:
            return FBLSmsParser._skip(record, SkipReason.BAD_AMOUNT)

        if not money.isPositive:
            return FBLSmsParser._skip(
                record, SkipReason.BAD_AMOUNT, "amount is not positive"
            )

        txnDate = FBLSmsParser._convertToDateTime(m.group("txndate").strip())
        if txnDate is None:
            return FBLSmsParser._skip(record, SkipReason.BAD_DATE)

        vendor = FBLSmsParser._extractVendor(m.group("rest"))
        if not vendor:
            return FBLSmsParser._skip(record, SkipReason.MISSING_VENDOR)

        # FBL txn msgs carry no card digits at all, so the card reference stays
        # explicitly absent rather than being recorded as a card ending 0000.
        return ParseResult.ok(
            CreditCardTxnDC(
                money=money,
                date=txnDate,
                vendor=vendor,
                bank=FBLSmsParser.ID,
            )
        )
