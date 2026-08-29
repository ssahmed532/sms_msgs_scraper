"""The frozen result of parsing one backup file.

A parse used to leave its findings scattered across mutable attributes on a
long-lived parser object, which meant the answer to "what did this file
contain?" depended on what had been done to that object since. Parsing the same
file twice through one instance drove the duplicate count from 235 to 4,900
while the message count stayed at 4,665 -- the conservation identity broke, and
nothing noticed, because there was no single value to check it against.

A `ParseReport` is that value. It is produced once, never mutated, and carries
everything a command, a verifier or a serialiser needs.

It also makes the deduplication decision visible. Collapsing two identical
messages is a financial judgement -- two genuine same-day purchases of the same
amount at the same vendor are indistinguishable from a retransmission when the
bank's alert carries a date but no time -- and a total that depends on that
judgement should say so. The policy and the provenance travel with the numbers.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum, auto
from types import MappingProxyType

from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.debit_txn import DebitTxnDC
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic


class DuplicatePolicy(StrEnum):
    """What to do with a message whose (sender, body) has already been seen.

    The default identity does not change under any of these -- the alternatives
    to (sender, body) were measured and are worse. The network redelivered one
    identical alert 2.9 hours late, so any "within N minutes" window would have
    admitted provable retransmissions as second purchases and *inflated*
    spending; and `date_sent` differs on 138 of 145 repeated groups, which would
    disable deduplication almost entirely. What changes here is whether the
    collapse happens and whether the caller is told when it was a close call.
    """

    # collapse repeats; the default, and what every previous version did
    EXACT = auto()
    # keep every message, collapsing nothing -- an upper bound on spending
    NONE = auto()
    # collapse, but count the ones where the bank's alert carries no time of
    # day and a genuine repeat purchase therefore cannot be ruled out
    REVIEW = auto()


@dataclass(frozen=True, slots=True)
class EnvelopeCounts:
    """What the file said it held, against what it actually held.

    `declared` is the root's own `count` attribute, and it counts *all* child
    elements -- the reference backup declares 4,719, being 4,665 `<sms>` plus 54
    `<mms>`. The tool reports on 4,665 of those. Keeping the two numbers apart
    is the point: a single "all" that silently meant one or the other is how a
    truncated backup would go unnoticed.
    """

    declared: int
    actual: int
    sms: int
    mms: int
    invalid: int

    @property
    def matchesDeclared(self) -> bool:
        return self.declared == self.actual

    def toDict(self) -> dict:
        return {
            "declared": self.declared,
            "actual": self.actual,
            "sms": self.sms,
            "mms": self.mms,
            "invalid": self.invalid,
        }

    @classmethod
    def fromDict(cls, data: dict) -> EnvelopeCounts:
        return cls(**{key: int(data[key]) for key in
                      ("declared", "actual", "sms", "mms", "invalid")})


@dataclass(frozen=True, slots=True)
class DuplicateRecord:
    """One suppressed message, and enough context to defend the suppression.

    `ambiguous` is the honest part. It is set when the sending bank's alerts
    carry a date but no time of day, so an identical body is equally consistent
    with a retransmission and with a second, genuine, identical purchase. For
    banks whose alerts carry a timestamp to the second, an identical body
    provably *is* the same transaction and this is False.
    """

    sender: str
    firstIndex: int
    duplicateIndex: int
    ambiguous: bool

    def toDict(self) -> dict:
        return {
            "sender": self.sender,
            "firstIndex": self.firstIndex,
            "duplicateIndex": self.duplicateIndex,
            "ambiguous": self.ambiguous,
        }

    @classmethod
    def fromDict(cls, data: dict) -> DuplicateRecord:
        return cls(
            sender=data["sender"],
            firstIndex=int(data["firstIndex"]),
            duplicateIndex=int(data["duplicateIndex"]),
            ambiguous=bool(data["ambiguous"]),
        )


# The report schema version, carried into machine-readable output so a script
# can pin the shape it was written against.
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ParseReport:
    """Everything one parse of one file found."""

    envelope: EnvelopeCounts
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    ccTxns: tuple[CreditCardTxnDC, ...] = ()
    debitTxns: tuple[DebitTxnDC, ...] = ()
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    duplicates: tuple[DuplicateRecord, ...] = ()
    duplicatePolicy: DuplicatePolicy = DuplicatePolicy.EXACT

    @property
    def ambiguousDuplicates(self) -> int:
        """How many suppressions could not be proved to be retransmissions."""
        return sum(1 for duplicate in self.duplicates if duplicate.ambiguous)

    @property
    def allVendors(self) -> set:
        return {txn.vendor for txn in self.ccTxns}

    @property
    def debitVendors(self) -> set:
        return {txn.vendor for txn in self.debitTxns}

    def count(self, bucket: str) -> int:
        """A routing bucket's count, or 0 for a bucket nothing landed in."""
        return self.counts.get(bucket, 0)

    def diagnosticsFor(self, bank: str) -> tuple:
        return tuple(entry for entry in self.diagnostics if entry.bank == bank)

    def toDict(self) -> dict:
        return {
            "schemaVersion": REPORT_SCHEMA_VERSION,
            "envelope": self.envelope.toDict(),
            "counts": dict(self.counts),
            "ccTxns": [txn.toDict() for txn in self.ccTxns],
            "debitTxns": [txn.toDict() for txn in self.debitTxns],
            "diagnostics": [entry.toDict() for entry in self.diagnostics],
            "duplicates": [entry.toDict() for entry in self.duplicates],
            "duplicatePolicy": str(self.duplicatePolicy),
        }

    @classmethod
    def fromDict(cls, data: dict) -> ParseReport:
        version = data.get("schemaVersion")
        if version != REPORT_SCHEMA_VERSION:
            raise ValueError(
                f"report schema version {version!r} cannot be read by this "
                f"build, which writes version {REPORT_SCHEMA_VERSION}"
            )

        return cls(
            envelope=EnvelopeCounts.fromDict(data["envelope"]),
            counts=MappingProxyType(dict(data["counts"])),
            ccTxns=tuple(CreditCardTxnDC.fromDict(item) for item in data["ccTxns"]),
            debitTxns=tuple(DebitTxnDC.fromDict(item) for item in data["debitTxns"]),
            diagnostics=tuple(
                ParseDiagnostic.fromDict(item) for item in data["diagnostics"]
            ),
            duplicates=tuple(
                DuplicateRecord.fromDict(item) for item in data["duplicates"]
            ),
            duplicatePolicy=DuplicatePolicy(data["duplicatePolicy"]),
        )
