"""What a bank is, as a value: identity, senders, capabilities, and the index.

These are the types. The banks themselves -- the sender short codes, and the
binding to each bank's parser -- are declared in `parser/registry.py`, because
a specification names the parser callables that fulfil it and the domain must
not depend on the parsing layer to describe a bank.

Registration fails loudly on a duplicate sender, because two banks claiming one
short code is a routing ambiguity that would otherwise be resolved silently by
whichever specification happened to be declared first.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto

from sms_msgs_scraper.domain.diagnostics import ParseResult
from sms_msgs_scraper.domain.message import SmsRecord


class TxnKind(StrEnum):
    """What kind of transaction a bank's alerts describe.

    Credit card transactions from every issuer share one store and are reported
    together; account debits are a different kind of event and have their own.
    """

    CREDIT_CARD = auto()
    ACCOUNT_DEBIT = auto()


class Capability(StrEnum):
    """What a bank's alerts actually contain.

    These are observations about the message format, not preferences, and each
    one has a consumer:

      * `TXN_TIME` decides whether a suppressed duplicate is ambiguous. When an
        alert carries a time of day, an identical body provably describes the
        same transaction. When it carries only a date, a second genuine
        identical purchase that day is indistinguishable from a retransmission,
        and the report says so rather than quietly picking one reading.
      * `CARD_DIGITS` and `ACCOUNT_MASK` decide whether an empty column means
        "absent from the message" or "failed to extract".
      * `MULTI_CURRENCY` records which issuers have been seen billing in
        something other than PKR.
    """

    CARD_DIGITS = auto()
    ACCOUNT_MASK = auto()
    TXN_TIME = auto()
    MULTI_CURRENCY = auto()


@dataclass(frozen=True, slots=True)
class BankSpec:
    """Everything the tool knows about one bank."""

    id: str
    name: str
    senderCodes: tuple[str, ...]
    txnKind: TxnKind
    # Deliberately looser than the extractor. If the signal were "the extractor
    # succeeded", a changed template would be counted as an ordinary message
    # from that bank with no warning at all -- the same silent gap that let a
    # re-homed short code go unnoticed. Kept separate, template drift surfaces
    # as a skipped message with a reason code.
    signal: Callable[[SmsRecord], bool]
    extract: Callable[[SmsRecord], ParseResult]
    capabilities: frozenset[Capability] = frozenset()
    notes: str = ""

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def skippedBucket(self) -> str:
        """The counting bucket for this bank's unparseable messages."""
        return f"{self.id}_SKIPPED"


class DuplicateSenderError(ValueError):
    """Two bank specifications claim the same sender short code."""


class BankRegistry:
    """The banks this build knows, indexed by sender."""

    def __init__(self, specs):
        self._specs = tuple(specs)
        self._bySender: dict[str, BankSpec] = {}
        self._byId: dict[str, BankSpec] = {}

        for spec in self._specs:
            if spec.id in self._byId:
                raise DuplicateSenderError(f"bank {spec.id} is declared twice")
            self._byId[spec.id] = spec

            for code in spec.senderCodes:
                existing = self._bySender.get(code)
                if existing is not None:
                    raise DuplicateSenderError(
                        f"sender {code} is claimed by both {existing.id} and "
                        f"{spec.id} -- routing would depend on declaration order"
                    )
                self._bySender[code] = spec

    def __iter__(self):
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def specFor(self, sender: str) -> BankSpec | None:
        """The bank that owns this sender, or None for an unrecognised one."""
        return self._bySender.get(sender)

    def byId(self, bankId: str) -> BankSpec:
        return self._byId[bankId]

    @property
    def bankIds(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self._specs)

    @property
    def senderCodes(self) -> frozenset[str]:
        return frozenset(self._bySender)

    def idsForKind(self, txnKind: TxnKind) -> tuple[str, ...]:
        return tuple(spec.id for spec in self._specs if spec.txnKind == txnKind)

    @property
    def ccBankIds(self) -> tuple[str, ...]:
        """The banks whose credit card transactions can be filtered on."""
        return self.idsForKind(TxnKind.CREDIT_CARD)

    def isRegistered(self, sender: str) -> bool:
        return sender in self._bySender

