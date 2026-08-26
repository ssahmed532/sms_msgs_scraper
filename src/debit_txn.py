from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto

from cc_txn import CurrencyAmountTuple


class DebitTxnType(StrEnum):
    """The kind of account debit a DebitTxnDC represents."""

    CARD_PURCHASE = auto()
    ATM_WITHDRAWAL = auto()
    ACCOUNT_DEBIT = auto()
    FUNDS_TRANSFER = auto()


@dataclass
class DebitTxnDC:
    """A DataClass representing a bank account debit transaction (card
    purchase, ATM withdrawal, bill payment or funds transfer) and its
    associated details.
    """

    amountTuple: CurrencyAmountTuple
    date: datetime
    # merchant name / ATM location / bill description / transfer payee,
    # depending on txnType
    vendor: str
    txnType: DebitTxnType
    # e.g. "xxxxxx5602"; empty when the msg carries no account number
    acctMask: str = ""
    bank: str = "MEZN"

    def __repr__(self) -> str:
        """Custom/overridden __repr__ function for the DebitTxnDC data class

        Returns:
            str: human-friendly string representing this debit txn
        """
        return (
            f"Debit Txn [{self.bank}/{self.txnType}]: "
            f"{self.amountTuple.currency} {self.amountTuple.amount:.2f} "
            f"at {self.vendor} on {self.date}"
        )
