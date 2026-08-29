"""The credit card transaction record."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.types import CardReference


@dataclass(frozen=True, slots=True)
class CreditCardTxnDC:
    """One credit card transaction, from any of the issuers this tool parses.

    Frozen, and validated on construction. Holding one of these is proof that
    it is a transaction someone could actually have made: a positive amount in
    a real currency, at a named vendor, on a timezone-aware date. The parsers
    are what decide whether a message becomes one of these; nothing downstream
    has to re-check, and nothing can quietly assemble a half-parsed transaction
    out of sentinels the way a `-1.2345` amount used to allow.
    """

    money: Money
    date: datetime
    vendor: str
    bank: str
    card: CardReference = field(default_factory=CardReference.absent)

    def __post_init__(self) -> None:
        if not self.money.isPositive:
            raise ValueError(f"a transaction amount must be positive, got {self.money}")

        if not self.vendor or not self.vendor.strip():
            raise ValueError("a transaction must name a vendor")

        if self.date.tzinfo is None:
            raise ValueError(
                "a transaction date must be timezone-aware -- a naive date here "
                "would be read as the host machine's local time and shifted, "
                "which moves transactions across day boundaries"
            )

    def __repr__(self) -> str:
        return (
            f"CC Txn [{self.bank}]: {self.money.currency} "
            f"{self.money.formatted()} at {self.vendor} on {self.date}"
        )

    def toDict(self) -> dict:
        return {
            "bank": self.bank,
            "currency": self.money.currency,
            "amount": str(self.money.amount),
            "date": self.date.isoformat(),
            "vendor": self.vendor,
            "card": self.card.lastFour,
        }

    @classmethod
    def fromDict(cls, data: dict) -> CreditCardTxnDC:
        lastFour = data.get("card")

        return cls(
            money=Money(Decimal(data["amount"]), data["currency"]),
            date=datetime.fromisoformat(data["date"]),
            vendor=data["vendor"],
            bank=data["bank"],
            card=CardReference(lastFour) if lastFour else CardReference.absent(),
        )
