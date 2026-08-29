"""Small shared value types that more than one layer needs to agree on.

Currently one: a reference to the card a transaction was made on.
"""

import re
from dataclasses import dataclass

LAST_FOUR_PTTRN = re.compile(r"\d{4}")


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
