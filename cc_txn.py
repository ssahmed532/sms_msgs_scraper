from dataclasses import dataclass


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
