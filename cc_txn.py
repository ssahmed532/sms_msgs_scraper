from collections import namedtuple

CreditCardTxn = namedtuple(
    "CreditCardTxn", ["txnAmount", "txnDate", "vendor", "ccLastFourDigits"]
)
