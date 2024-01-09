from collections import namedtuple
from dataclasses import dataclass


TxnTuple = namedtuple("TxnTuple", ["currency", "amount"])
CurrencyAmountTuple = namedtuple("TxnTuple", ["currency", "amount"])


@dataclass
class CreditCardTxnDC:
    """A DataClass representing a Credit Card transaction and
    its associated details.
    """

    amount: str
    currency: str
    date: str
    vendor: str
    ccLastFourDigits: int = 0
