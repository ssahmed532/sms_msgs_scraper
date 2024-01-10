from collections import namedtuple
from dataclasses import dataclass


"""A namedtuple to represent and combine the two important attributes
of a Credit Card transaction:

    currency: the currency of the transaction
    amount: the amount of the transaction (in the currency above)
"""
CurrencyAmountTuple = namedtuple("CurrencyAmountTuple", ["currency", "amount"])


@dataclass
class CreditCardTxnDC:
    """A DataClass representing a Credit Card transaction and
    its associated details.
    """

    amountTuple: CurrencyAmountTuple
    date: str
    vendor: str
    ccLastFourDigits: int = 0

    def __repr__(self) -> str:
        """Custom/overridden __repr__ function for the CreditCardTxnDC data class

        Returns:
            str: human-friendly string representing this Credit Card txn
        """
        return f"CC Txn: {self.amountTuple.currency} {self.amountTuple.amount:.2f} at {self.vendor} on {self.date}"
