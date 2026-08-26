import re
import xml.etree.ElementTree
from datetime import datetime

from cc_txn import CurrencyAmountTuple
from common import DEFAULT_TZ
from debit_txn import DebitTxnDC, DebitTxnType


class MeznSmsParser:
    ID = "MEZN"
    # SMS messages from any one of these short codes will be assumed to be
    # from Meezan Bank. Both codes are live: they carry the same message
    # templates, so history spans both.
    MEZN_SHORT_CODES = ["8079", "9779"]

    # Shared building blocks of the debit txn templates. Every Meezan debit
    # alert opens with the amount, and every one of them carries the txn
    # date/time in the same "on DD-Mon-YY at HH:MM" shape.
    MEZN_AMOUNT_RE = r"(?P<currency>[A-Z]{3}) (?P<amount>[\d,]+\.\d{2})"
    MEZN_DATE_RE = (
        r"on (?P<txndate>\d{1,2}-[A-Za-z]{3}-(?:\d{4}|\d{2})) "
        r"at (?P<txntime>\d{1,2}:\d{2})"
    )

    # The five validated debit templates, as (txn type, pattern) pairs matched
    # in order — first match wins. The trailing junk after the date varies a
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
            # "sent to" is deliberately case-sensitive here: the uppercase
            # "SENT TO" family below carries no account clause at all and is
            # matched by its own pattern. The A/?C#? spelling covers both the
            # regular "A/C" and the RAAST "AC#" variants, and the optional
            # " of {branch}" clause is absent in the "(MBL AC #xxx#)" payee
            # variant.
            DebitTxnType.FUNDS_TRANSFER,
            re.compile(
                MEZN_AMOUNT_RE
                + r" sent to (?P<vendor>.+?) "
                + r"from your A/?C#? (?P<acmask>\S+)(?: of .+?)? "
                + MEZN_DATE_RE
            ),
        ),
        (
            # Uppercase transfer notices carry no account number, so acctMask
            # stays empty for these.
            DebitTxnType.FUNDS_TRANSFER,
            re.compile(
                MEZN_AMOUNT_RE + r" SENT TO (?P<vendor>.+?) " + MEZN_DATE_RE
            ),
        ),
    ]

    # The amount head every debit alert opens with. Load-bearing part of the
    # txn signal: a card-dispatch notice on these short codes reads "…has been
    # sent to your [address]" and must not be mistaken for a funds transfer.
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
    # The 2-digit format must be tried first: strptime's %Y also accepts a
    # 2-digit year, and would silently read "25-Aug-25" as the year 25.
    MEZN_TXN_DATETIME_FMTS = ("%d-%b-%y %H:%M", "%d-%b-%Y %H:%M")

    @staticmethod
    def _normalizeWhitespace(strValue: str) -> str:
        """Collapse runs of whitespace in a msg body into single spaces.

        Note: SmsBackupFileParser.calcSmsMsgHash hashes the raw *stripped*
        body, while this normalizes internal whitespace too — so two Meezan
        bodies differing only in internal spacing hash differently and both
        parse. Real duplicates are byte-identical retransmissions, so that is
        acceptable. Normalizing costs nothing and keeps both the txn signal
        and the template regexes robust against spacing wobble.
        """
        return re.sub(r"\s+", " ", strValue).strip()

    @staticmethod
    def isSmsFromMezn(sms: xml.etree.ElementTree.Element) -> bool:
        return sms.attrib["address"] in MeznSmsParser.MEZN_SHORT_CODES

    @staticmethod
    def isMsgDebitTxn(sms: xml.etree.ElementTree.Element) -> bool:
        """Report whether this msg looks like an account debit notification.

        This is an independent keyword signal, deliberately looser than the
        template regexes in MEZN_DEBIT_TXN_PTTRNS. If the signal were simply
        "one of the templates matches", then a changed Meezan template would
        be counted as a plain Meezan msg and *nothing would be logged* — the
        same silent-gap failure this app hit when HBL re-homed its CC alerts
        to a new short code. Kept separate, template drift shows up as a
        skipped-msg warning instead.
        """
        msgBody = MeznSmsParser._normalizeWhitespace(sms.attrib["body"])

        if not MeznSmsParser.MEZN_AMOUNT_HEAD_PTTRN.match(msgBody):
            return False

        foldedBody = msgBody.casefold()

        if not any(
            keyword in foldedBody for keyword in MeznSmsParser.MEZN_DEBIT_KEYWORDS
        ):
            return False

        if any(
            keyword in foldedBody for keyword in MeznSmsParser.MEZN_NON_DEBIT_KEYWORDS
        ):
            return False

        return True

    @staticmethod
    def _extractCurrencyAndAmount(
        currencyValue: str, amountValue: str
    ) -> CurrencyAmountTuple:
        try:
            return CurrencyAmountTuple(
                currencyValue.strip(), float(amountValue.strip().replace(",", ""))
            )
        except ValueError:
            print(
                f"ERROR: unable to parse Meezan txn amount into float value: "
                f"{amountValue}"
            )

        return CurrencyAmountTuple(None, -1.2345)

    @staticmethod
    def _convertToDateTime(dateValue: str, timeValue: str) -> datetime:
        strValue = f"{dateValue} {timeValue}"

        for datetimeFmt in MeznSmsParser.MEZN_TXN_DATETIME_FMTS:
            try:
                # All timestamps in an SMS backup file are Karachi local time,
                # so the parsed value is *stamped* with that zone, not
                # converted into it. astimezone() would instead read the naive
                # value as the host machine's local time and shift it — enough
                # to move a txn across a day boundary on any machine not set
                # to +05:00.
                return datetime.strptime(strValue, datetimeFmt).replace(
                    tzinfo=DEFAULT_TZ
                )
            except ValueError:
                continue

        print(f"ERROR: unable to parse Meezan txn date/time: {strValue}")

        return None

    @staticmethod
    def extractDetailsFromTxnMsg(sms: xml.etree.ElementTree.Element) -> DebitTxnDC:
        """Extract the debit txn details out of a Meezan debit SMS msg.

        Returns:
            DebitTxnDC: the parsed txn, or None when the msg matches none of
                        the known templates or carries an unparseable
                        date/amount. Failures are reported and skipped, never
                        raised — a malformed msg must not abort a whole run.
        """
        msgBody = MeznSmsParser._normalizeWhitespace(sms.attrib["body"])

        for txnType, pattern in MeznSmsParser.MEZN_DEBIT_TXN_PTTRNS:
            m = pattern.match(msgBody)
            if not m:
                continue

            currencyAndAmount = MeznSmsParser._extractCurrencyAndAmount(
                m.group("currency"), m.group("amount")
            )
            if (not currencyAndAmount.currency) or (currencyAndAmount.amount <= 0):
                print(f"ERROR: bad amount in Meezan debit msg: {msgBody}")
                return None

            datetimeObj = MeznSmsParser._convertToDateTime(
                m.group("txndate"), m.group("txntime")
            )
            if not datetimeObj:
                return None

            vendor = m.group("vendor").strip()
            if not vendor:
                print(f"ERROR: empty vendor in Meezan debit msg: {msgBody}")
                return None

            # The uppercase transfer template has no account clause at all.
            acctMask = (
                m.group("acmask").strip() if "acmask" in m.groupdict() else ""
            )

            return DebitTxnDC(
                amountTuple=currencyAndAmount,
                date=datetimeObj,
                vendor=vendor,
                txnType=txnType,
                acctMask=acctMask,
            )

        print(f"ERROR: unable to match any Meezan debit template against msg: {msgBody}")

        return None
