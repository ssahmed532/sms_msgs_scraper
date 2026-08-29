"""Meezan Bank account debit alerts.

Five template families, matched in order, mapping onto four kinds of debit.
"""

import re
from datetime import datetime

from sms_msgs_scraper.domain.debit_txn import DebitTxnDC, DebitTxnType
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, ParseResult, SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import AMOUNT_TOKEN_2DP_RE, Money, MoneyError
from sms_msgs_scraper.domain.tz import DEFAULT_TZ


class MeznSmsParser:
    ID = "MEZN"

    # Shared building blocks of the debit txn templates. Every Meezan debit
    # alert opens with the amount, and every one of them carries the txn
    # date/time in the same "on DD-Mon-YY at HH:MM" shape.
    #
    # The amount uses the shared two-decimal grammar, so malformed grouping is
    # rejected rather than repaired by stripping the commas out, and the digit
    # length is bounded.
    MEZN_AMOUNT_RE = rf"(?P<currency>[A-Z]{{3}}) (?P<amount>{AMOUNT_TOKEN_2DP_RE})"
    MEZN_DATE_RE = (
        r"on (?P<txndate>\d{1,2}-[A-Za-z]{3}-(?:\d{4}|\d{2})) "
        r"at (?P<txntime>\d{1,2}:\d{2})"
    )

    # The five validated debit templates, as (txn type, pattern) pairs matched
    # in order -- first match wins. The trailing junk after the date varies a
    # lot between msgs (TID:, UAN, "Fee: Rs.X", "Bal:", or a bare " ."), so
    # none of these patterns anchor the end of the body.
    #
    # No body in the validated corpus matches two families, so the ordering is
    # a tiebreak that never fires today; it is fixed so that a future template
    # overlap resolves deterministically instead of by dict iteration luck.
    MEZN_DEBIT_TXN_PTTRNS = [
        (
            DebitTxnType.CARD_PURCHASE,
            re.compile(
                MEZN_AMOUNT_RE
                + r" charged at (?P<vendor>.+?) for card used, "
                + r"from A/C (?P<acmask>\S+) \((?P<branch>.+?)\) "
                + MEZN_DATE_RE
            ),
        ),
        (
            DebitTxnType.ATM_WITHDRAWAL,
            re.compile(
                MEZN_AMOUNT_RE
                + r" cash withdrawn from (?P<vendor>.+?) "
                + r"from A/C (?P<acmask>\S+) .+? "
                + MEZN_DATE_RE
            ),
        ),
        (
            DebitTxnType.ACCOUNT_DEBIT,
            re.compile(
                MEZN_AMOUNT_RE
                + r" is debited as (?P<vendor>.+?) "
                + r"from your A/C (?P<acmask>\S+) of .+? "
                + MEZN_DATE_RE
            ),
        ),
        (
            # "sent to" is matched case-insensitively: the "(MBL AC ...)" payee
            # form is sent with an uppercase "SENT TO" but still carries an
            # account clause, so a case-sensitive match here would push those
            # bodies down to the account-less family below, which would swallow
            # the account clause into the payee and leave acctMask empty. The
            # A/?C#? spelling covers both the regular "A/C" and the RAAST "AC#"
            # variants, and the " of {branch}" clause is optional because the
            # "(MBL AC ...)" form omits it.
            DebitTxnType.FUNDS_TRANSFER,
            re.compile(
                MEZN_AMOUNT_RE
                + r"(?i: sent to )(?P<vendor>.+?) "
                + r"from your A/?C#? (?P<acmask>\S+)(?: of .+?)? "
                + MEZN_DATE_RE
            ),
        ),
        (
            # The fallback for transfer notices that carry no account clause at
            # all (uppercase only), so acctMask stays empty for these.
            DebitTxnType.FUNDS_TRANSFER,
            re.compile(MEZN_AMOUNT_RE + r" SENT TO (?P<vendor>.+?) " + MEZN_DATE_RE),
        ),
    ]

    # The amount head every debit alert opens with. Load-bearing part of the
    # txn signal: a card-dispatch notice on these short codes reads "...has been
    # sent to your [address]" and must not be mistaken for a funds transfer.
    #
    # Deliberately the *permissive* amount shape rather than the shared grammar
    # the templates use. The signal has to stay looser than extraction: a body
    # whose amount is malformed must still be recognised as an attempted debit,
    # so that it fails extraction and is counted and reported. Tighten this and
    # such a message becomes an ordinary Meezan message with nothing logged --
    # precisely the silent gap the independent signal exists to prevent.
    MEZN_AMOUNT_HEAD_PTTRN = re.compile(r"[A-Z]{3} [\d,]+\.\d{2} ")

    # Keywords that identify a debit, and keywords that rule one out (credits,
    # reversals, cheque clearing).
    MEZN_DEBIT_KEYWORDS = (
        "cash withdrawn",
        "is debited as",
        "sent to",
        "for card used",
    )
    MEZN_NON_DEBIT_KEYWORDS = ("credited", "received from", "cheque", "reversal")

    # The formats of the txn date+time in Meezan debit SMS msgs:
    #   28-Sep-23 19:42     (2-digit year; the original format)
    #   28-Apr-2025 9:05    (4-digit year; appeared around Apr 2025)
    # Either order works: strptime's %Y matches exactly 4 digits and %y
    # exactly 2, so the two formats are mutually exclusive -- neither can
    # mis-read the other's year.
    MEZN_TXN_DATETIME_FMTS = ("%d-%b-%y %H:%M", "%d-%b-%Y %H:%M")

    @staticmethod
    def _normalizeWhitespace(strValue: str) -> str:
        """Collapse runs of whitespace in a msg body into single spaces.

        Note: the duplicate identity hashes the raw *stripped* body, while this
        normalizes internal whitespace too -- so two Meezan bodies differing
        only in internal spacing hash differently and both parse. Real
        duplicates are byte-identical retransmissions, so that is acceptable.
        Normalizing costs nothing and keeps both the txn signal and the template
        regexes robust against spacing wobble.
        """
        return re.sub(r"\s+", " ", strValue).strip()

    @staticmethod
    def isTxnMsg(record: SmsRecord) -> bool:
        """Report whether this msg looks like an account debit notification.

        This is an independent keyword signal, deliberately looser than the
        template regexes in MEZN_DEBIT_TXN_PTTRNS. If the signal were simply
        "one of the templates matches", then a changed Meezan template would
        be counted as a plain Meezan msg and *nothing would be logged* -- the
        same silent-gap failure this app hit when HBL re-homed its CC alerts
        to a new short code. Kept separate, template drift shows up as a
        skipped-msg diagnostic instead.
        """
        msgBody = MeznSmsParser._normalizeWhitespace(record.body)

        if not MeznSmsParser.MEZN_AMOUNT_HEAD_PTTRN.match(msgBody):
            return False

        foldedBody = msgBody.casefold()

        if not any(
            keyword in foldedBody for keyword in MeznSmsParser.MEZN_DEBIT_KEYWORDS
        ):
            return False

        return not any(
            keyword in foldedBody for keyword in MeznSmsParser.MEZN_NON_DEBIT_KEYWORDS
        )

    @staticmethod
    def _skip(record: SmsRecord, reason: SkipReason, detail: str = "") -> ParseResult:
        return ParseResult.failed(
            ParseDiagnostic.forRecord(MeznSmsParser.ID, record, reason, detail)
        )

    @staticmethod
    def _convertToDateTime(dateValue: str, timeValue: str) -> datetime | None:
        strValue = f"{dateValue} {timeValue}"

        for datetimeFmt in MeznSmsParser.MEZN_TXN_DATETIME_FMTS:
            try:
                # All timestamps in an SMS backup file are Karachi local time,
                # so the parsed value is *stamped* with that zone, not converted
                # into it. astimezone() would instead read the naive value as
                # the host machine's local time and shift it -- enough to move a
                # txn across a day boundary on any machine not set to +05:00.
                return datetime.strptime(strValue, datetimeFmt).replace(
                    tzinfo=DEFAULT_TZ
                )
            except ValueError:
                continue

        return None

    @staticmethod
    def extract(record: SmsRecord) -> ParseResult:
        """Extract the debit txn details out of a Meezan debit SMS msg.

        Returns a diagnostic, never an exception, when the msg matches none of
        the known templates or carries an unparseable date or amount.
        """
        msgBody = MeznSmsParser._normalizeWhitespace(record.body)

        for txnType, pattern in MeznSmsParser.MEZN_DEBIT_TXN_PTTRNS:
            m = pattern.match(msgBody)
            if not m:
                continue

            try:
                money = Money.parse(m.group("currency"), m.group("amount"))
            except MoneyError:
                return MeznSmsParser._skip(record, SkipReason.BAD_AMOUNT)

            if not money.isPositive:
                return MeznSmsParser._skip(
                    record, SkipReason.BAD_AMOUNT, "amount is not positive"
                )

            txnDate = MeznSmsParser._convertToDateTime(
                m.group("txndate"), m.group("txntime")
            )
            if txnDate is None:
                return MeznSmsParser._skip(record, SkipReason.BAD_DATE)

            vendor = m.group("vendor").strip()
            if not vendor:
                return MeznSmsParser._skip(record, SkipReason.MISSING_VENDOR)

            # The uppercase transfer template has no account clause at all.
            acctMask = m.group("acmask").strip() if "acmask" in m.groupdict() else ""

            return ParseResult.ok(
                DebitTxnDC(
                    money=money,
                    date=txnDate,
                    vendor=vendor,
                    txnType=txnType,
                    acctMask=acctMask,
                )
            )

        return MeznSmsParser._skip(
            record,
            SkipReason.NO_TEMPLATE_MATCH,
            "matched no known Meezan debit template",
        )
