"""Exact money, and the one amount grammar every bank parser is checked against.

Money in this tool is a `Decimal` captured **straight from the matched token**.
It never passes through a `float` on the way in, because that is where the loss
happens and it is unrecoverable afterwards: `float("0.1") + float("0.2")` is
already wrong before anything has been summed, and `,.2f` at render time hides
the drift rather than removing it. Aggregating a year of transactions as binary
floating point drifted 21 of 93 bank/month/currency buckets in the reference
corpus while every rendered figure still *looked* right.

Two other things live here because they belong to the same contract:

  * **The amount grammar.** Each bank writes amounts differently — HBL groups
    thousands with commas, FBL never does, SCB and Meezan always carry two
    decimals — but "what counts as a well-formed amount" must be one rule, or
    each parser invents its own and they disagree about `1,2,3.00`. The parsers
    embed one of the two token patterns below, and `Money.parse` re-validates
    whatever it is handed, so a token can never enter the system by a path that
    skipped the check.

  * **The scale bound.** A currency's minor units decide how many decimal places
    an amount may carry. Rejecting an over-scaled amount at construction is what
    makes `,.2f` a *formatting* choice rather than a silent rounding one.

There is deliberately no invalid `Money`. The `-1.2345` sentinel this type
replaces was worse than merely invalid — it was *negative*, so a failed parse
that reached a monthly total silently **reduced** reported spending.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

# A currency code is exactly three uppercase ASCII letters. Anything else is a
# parse failure, not a currency.
CURRENCY_PTTRN = re.compile(r"[A-Z]{3}")

# How many decimal places each currency may carry. A code that is well-formed
# but absent from this table is *accepted* at the default scale rather than
# rejected -- see UNKNOWN_CURRENCY_POLICY below.
MINOR_UNITS = {
    "PKR": 2,
    "USD": 2,
    "CAD": 2,
}

DEFAULT_MINOR_UNITS = 2

UNKNOWN_CURRENCY_POLICY = (
    "A well-formed three-letter code that is not in MINOR_UNITS is accepted and "
    "assumed to have DEFAULT_MINOR_UNITS decimal places. The banks parsed here "
    "can send any currency their cards are used in, and a report must not crash "
    "the first time someone spends in one this table has never seen."
)

# The widest integer part any amount may carry. The reference corpus tops out at
# 8 digits; 12 leaves generous headroom while making it impossible for a
# malformed or hostile token to grow without bound.
MAX_INTEGER_DIGITS = 12

# The amount grammar, as a token that can be embedded directly in a bank's own
# transaction pattern.
#
# The integer part is *either* comma-grouped in strict threes *or* an unbroken
# run of digits -- never a mixture. That single alternation is what rejects
# "1,2,3.00" and "1,00", which a permissive "[\d,]+" accepts and then silently
# repairs by stripping the commas out.
AMOUNT_TOKEN_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

# The same grammar with a mandatory two-decimal fraction, for the banks whose
# alerts always carry one. SCB needs this specifically: it sends foreign-currency
# transactions as a literal "PKR .00" with no digits at all before the dot, and
# those must fail extraction and be counted as skipped rather than parsed as
# some other number.
AMOUNT_TOKEN_2DP_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"

# The separator-free variant, for a bank that never groups its thousands.
# Faysal Bank writes 8100 and 18298.9, and has never sent a comma in an amount.
# Using the general grammar there would widen what that parser accepts for no
# reason -- and a comma-grouped body on FBL's short code is far more likely to
# be a template change worth reporting than an amount worth trusting.
AMOUNT_TOKEN_PLAIN_RE = r"\d+(?:\.\d+)?"

AMOUNT_TOKEN_PTTRN = re.compile(AMOUNT_TOKEN_RE)


class MoneyError(ValueError):
    """A value that cannot be a well-formed amount of money."""


class CurrencyMismatchError(MoneyError):
    """An attempt to combine two amounts denominated in different currencies."""


def minorUnitsFor(currency: str) -> int:
    """How many decimal places this currency may carry.

    Falls back to DEFAULT_MINOR_UNITS for a well-formed but unlisted code, per
    UNKNOWN_CURRENCY_POLICY.
    """
    return MINOR_UNITS.get(currency, DEFAULT_MINOR_UNITS)


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount in a single currency.

    Frozen and slotted: a transaction's amount is a value, and nothing
    downstream may adjust one in place. Construction validates, so possessing a
    `Money` is proof the amount is well-formed -- there is no invalid state for
    a caller to check for.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise MoneyError(
                f"amount must be a Decimal, not {type(self.amount).__name__} -- "
                "capturing it as a float has already lost precision by this point"
            )

        if not self.amount.is_finite():
            raise MoneyError(f"amount must be finite, got {self.amount}")

        if not CURRENCY_PTTRN.fullmatch(self.currency):
            raise MoneyError(
                f"currency must be three uppercase letters, got {self.currency!r}"
            )

        # is_finite() above rules out the 'n' / 'N' / 'F' exponents that a
        # NaN or an infinity would carry, so this is an integer.
        scale = -int(self.amount.as_tuple().exponent)
        allowed = minorUnitsFor(self.currency)
        if scale > allowed:
            raise MoneyError(
                f"{self.currency} carries {allowed} decimal places, but "
                f"{self.amount} has {scale} -- rejecting rather than rounding it "
                "away silently at render time"
            )

    @classmethod
    def parse(cls, currency: str, token: str) -> Money:
        """Build a Money from a currency code and an amount token lifted
        verbatim out of an SMS body.

        The token is validated against the shared grammar before anything is
        stripped from it, so a malformed grouping is a failure rather than
        something quietly repaired.

        Raises:
            MoneyError: if the code or the token is not well-formed.
        """
        currency = currency.strip()
        token = token.strip()

        if not AMOUNT_TOKEN_PTTRN.fullmatch(token):
            raise MoneyError(f"not a well-formed amount token: {token!r}")

        digits = token.replace(",", "")
        integerDigits = digits.split(".")[0]
        if len(integerDigits) > MAX_INTEGER_DIGITS:
            raise MoneyError(
                f"amount has {len(integerDigits)} integer digits, more than the "
                f"{MAX_INTEGER_DIGITS} any real transaction carries: {token!r}"
            )

        return cls(Decimal(digits), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        """The additive identity in a given currency."""
        return cls(Decimal(0).scaleb(-minorUnitsFor(currency)), currency)

    @property
    def isPositive(self) -> bool:
        return self.amount > 0

    def _requireSameCurrency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} and {other.currency} -- an "
                "exchange rate is a decision this tool does not have"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented

        self._requireSameCurrency(other)

        return Money(self.amount + other.amount, self.currency)

    def __radd__(self, other):
        """Support `sum(...)`, whose start value is the integer 0.

        Only a literal zero is accepted; summing a Money into anything else is
        the currency mistake `__add__` already refuses.
        """
        if other == 0:
            return self

        return NotImplemented

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented

        self._requireSameCurrency(other)

        return Money(self.amount - other.amount, self.currency)

    def formatted(self) -> str:
        """Thousands-grouped, at this currency's scale.

        Formatting is the only place a scale is imposed, and by this point it
        cannot lose anything: an amount too precise for its currency was
        rejected at construction.
        """
        return f"{self.amount:,.{minorUnitsFor(self.currency)}f}"

    def __str__(self) -> str:
        return f"{self.currency} {self.formatted()}"

    def __repr__(self) -> str:
        return f"Money({self.amount!r}, {self.currency!r})"
