"""Small shared value types that more than one layer needs to agree on.

A reference to the card a transaction was made on, and the one rule for what a
long run of digits inside a message field may become.
"""

import re
from dataclasses import dataclass

LAST_FOUR_PTTRN = re.compile(r"\d{4}")

# A run of ten or more digits inside a vendor or account field is a number that
# identifies an account rather than a merchant: a card PAN (13-19 digits), a
# bank account number (10-16), a utility consumer number (10-13). Nothing a
# merchant is *called* is that long -- a terminal id, a station number or a
# date all stop well short -- so the threshold masks the identifiers and
# leaves the names alone.
LONG_DIGIT_RUN_PTTRN = re.compile(r"\d{10,}")
MASK_KEEP_DIGITS = 4
MASK_CHAR = "x"


def maskAccountNumbers(text: str) -> str:
    """Replace every run of ten or more digits with `x`s and its last four.

    Meezan builds a transfer's payee out of the beneficiary's name *and their
    account number*, and a bill payment's description out of the biller and
    the consumer number, so the vendor field of an account debit carried full
    card and account numbers into every listing, chart and `--vendor` match.
    The report is the one thing every output is rendered from, so the masking
    happens before a value ever reaches it: nothing downstream has to remember
    to redact, because nothing downstream is ever handed the number.

    The last four survive because they are what tells two payees with the same
    name apart, and the run keeps its length in the mask so a card number still
    reads as a card number rather than as a consumer number. This is the same
    shape the bank itself uses for the paying account (`xxxxxx5602`).
    """
    return LONG_DIGIT_RUN_PTTRN.sub(
        lambda match: MASK_CHAR * (len(match.group()) - MASK_KEEP_DIGITS)
        + match.group()[-MASK_KEEP_DIGITS:],
        text,
    )


@dataclass(frozen=True, slots=True)
class CardReference:
    """The last four digits of a card, or the explicit absence of them.

    The distinction is the whole point of the type. The integer field this
    replaces used `0` for both "this bank does not put card digits in its
    alerts" (every FBL message) and "the mask carried no last four" (SCB's
    BIN-only form) -- and could not express a real card ending `0000` at all
    without claiming to be one of those. Absence is now a state, not a
    magic number.
    """

    lastFour: str | None = None

    def __post_init__(self) -> None:
        if self.lastFour is not None and not LAST_FOUR_PTTRN.fullmatch(self.lastFour):
            raise ValueError(
                f"card last four must be exactly four digits, got {self.lastFour!r}"
            )

    @classmethod
    def absent(cls) -> CardReference:
        """A transaction whose message carried no card digits."""
        return cls(None)

    @classmethod
    def of(cls, lastFour: str) -> CardReference:
        """A transaction on a known card, from its last four digits."""
        return cls(str(lastFour).strip())

    @property
    def known(self) -> bool:
        return self.lastFour is not None

    def __str__(self) -> str:
        return self.lastFour or ""
