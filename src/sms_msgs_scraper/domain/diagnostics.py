"""How a parser reports that it could not parse something.

A parse failure used to be a `print()` from inside the parser. That put three
unrelated decisions in one place: *that* something failed, *what* failed, and
*where the text about it goes*. Only the first two are the parser's business.

So a failure is now a value. The parser returns one, the orchestrator collects
them, and the presentation layer decides -- once -- whether they are rendered to
stderr, counted, or serialised into a report.

The privacy rule is structural rather than advisory: a `ParseDiagnostic` has no
field a message body would fit in. It carries a `SkipReason` from a closed
vocabulary and a locator built from the sender and the received timestamp. That
is enough to find the message in your own backup and not enough to reproduce it
in a bug report. A backup holds vendors, account masks, card digits and running
balances; the reference corpus skips 26 messages in a single run, and the old
HBL path printed the complete body of every one of its failures.
"""

from dataclasses import dataclass
from enum import StrEnum, auto

from sms_msgs_scraper.domain.message import SmsRecord


class SkipReason(StrEnum):
    """Why a message carrying a transaction signal produced no transaction.

    Categorised rather than merely counted: "26 SCB messages were skipped" is a
    number, while "21 truncated, 5 with no amount" is a diagnosis. A shift
    between categories at a constant total is exactly the kind of movement a
    bare count cannot show.
    """

    # the body carried the bank's transaction signal but matched none of its
    # templates -- i.e. the bank changed a template
    NO_TEMPLATE_MATCH = auto()
    # an amount that the shared grammar rejects, or that no currency can carry
    BAD_AMOUNT = auto()
    # a date token the format accepted but the calendar did not
    BAD_DATE = auto()
    # the template matched but the vendor field came out empty
    MISSING_VENDOR = auto()
    # a card mask in a shape neither known form covers
    UNRECOGNIZED_CARD_MASK = auto()
    # the XML element was not a usable message at all
    MALFORMED_RECORD = auto()


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    """One parse failure, described without reproducing the message."""

    bank: str
    reason: SkipReason
    sender: str
    receivedAt: str
    # A short, *static* clarification chosen by the parser -- never interpolated
    # from the message. "truncated body, or a foreign-currency amount" is fine;
    # anything derived from the body is not.
    detail: str = ""

    @classmethod
    def forRecord(
        cls,
        bank: str,
        record: SmsRecord,
        reason: SkipReason,
        detail: str = "",
    ) -> ParseDiagnostic:
        return cls(
            bank=bank,
            reason=reason,
            sender=record.sender,
            receivedAt=record.receivedAt,
            detail=detail,
        )

    def message(self) -> str:
        """The single line this failure renders as."""
        suffix = f" ({self.detail})" if self.detail else ""

        return (
            f"WARNING: skipped {self.bank} msg -- {self.reason}{suffix}; "
            f"sender {self.sender}, received {self.receivedAt}"
        )

    def toDict(self) -> dict:
        return {
            "bank": self.bank,
            "reason": str(self.reason),
            "sender": self.sender,
            "receivedAt": self.receivedAt,
            "detail": self.detail,
        }

    @classmethod
    def fromDict(cls, data: dict) -> ParseDiagnostic:
        return cls(
            bank=data["bank"],
            reason=SkipReason(data["reason"]),
            sender=data["sender"],
            receivedAt=data["receivedAt"],
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Either a transaction or the reason there isn't one.

    Deliberately not `None`-or-value: a bare `None` says a parse failed and
    nothing else, which is why the reason used to have to be printed at the
    point of failure to avoid being lost.
    """

    txn: object | None = None
    diagnostic: ParseDiagnostic | None = None

    @classmethod
    def ok(cls, txn) -> ParseResult:
        return cls(txn=txn)

    @classmethod
    def okWithWarning(cls, txn, diagnostic: ParseDiagnostic) -> ParseResult:
        """A transaction that parsed, with something about it worth reporting.

        The case this exists for is a card mask in a shape neither known form
        covers. The amount, date and vendor are all present and correct, so
        discarding the transaction would lose real spending over a cosmetic
        field -- but silently recording it as having no card digits is how a
        change in the bank's masking would go unnoticed. So the transaction is
        kept and the observation is still reported.
        """
        return cls(txn=txn, diagnostic=diagnostic)

    @classmethod
    def failed(cls, diagnostic: ParseDiagnostic) -> ParseResult:
        return cls(diagnostic=diagnostic)

    @property
    def succeeded(self) -> bool:
        return self.txn is not None
