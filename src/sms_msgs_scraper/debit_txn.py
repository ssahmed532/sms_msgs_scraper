"""The bank account debit record."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, auto

from sms_msgs_scraper.domain.money import Money


class DebitTxnType(StrEnum):
    """The kind of account debit a DebitTxnDC represents."""

    CARD_PURCHASE = auto()
    ATM_WITHDRAWAL = auto()
    ACCOUNT_DEBIT = auto()
    FUNDS_TRANSFER = auto()


@dataclass(frozen=True, slots=True)
class DebitTxnDC:
    """One account debit -- a card purchase, ATM withdrawal, bill payment or
    funds transfer.

    Frozen and validated on the same terms as a credit card transaction: a
    positive amount, a named counterparty, a timezone-aware date.
    """

    money: Money
    date: datetime
    # merchant name / ATM location / bill description / transfer payee,
    # depending on txnType
    vendor: str
    txnType: DebitTxnType
    # e.g. "xxxxxx5602"; empty when the message carries no account number
    acctMask: str = ""
    bank: str = "MEZN"

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
            f"Debit Txn [{self.bank}/{self.txnType}]: "
            f"{self.money.currency} {self.money.formatted()} "
            f"at {self.vendor} on {self.date}"
        )

    def toDict(self) -> dict:
        return {
            "bank": self.bank,
            "currency": self.money.currency,
            "amount": str(self.money.amount),
            "date": self.date.isoformat(),
            "vendor": self.vendor,
            "txnType": str(self.txnType),
            "acctMask": self.acctMask,
        }

    @classmethod
    def fromDict(cls, data: dict) -> DebitTxnDC:
        return cls(
            money=Money(Decimal(data["amount"]), data["currency"]),
            date=datetime.fromisoformat(data["date"]),
            vendor=data["vendor"],
            txnType=DebitTxnType(data["txnType"]),
            acctMask=data.get("acctMask", ""),
            bank=data.get("bank", "MEZN"),
        )
