"""Reads one backup file and returns one immutable report.

Three properties are load-bearing here, and each replaces something that used
to be a latent defect:

  * **One-shot.** A parse produces a `ParseReport` and the instance refuses to
    parse again. The old parser accumulated into its own attributes, so calling
    `parseMessages()` twice on one instance drove the duplicate count from 235
    to 4,900 while the message count stayed at 4,665 -- silently breaking the
    conservation identity that is supposed to prove nothing was miscounted.

  * **Registry-driven routing.** Senders are looked up in `parser/registry.py`
    rather than tested against an `elif` chain of per-parser constants. The
    chain is how Standard Chartered's second short code came to be missing: it
    had to be written down in five places, and it was written in four.

  * **Per-record isolation.** A malformed element becomes a diagnostic and the
    run continues. Nothing a backup file contains can raise out of this loop.

The file is streamed with `iterparse` and each element is released as soon as
its record has been taken, so a backup's message bodies -- personal financial
data -- are not held in memory for the lifetime of the process the way a
retained DOM held them.
"""

import hashlib
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from sms_msgs_scraper.domain.aggregate import txnSortKey
from sms_msgs_scraper.domain.bank import Capability, TxnKind
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, SkipReason
from sms_msgs_scraper.domain.message import MMS_TAG, SMS_TAG, SmsRecord
from sms_msgs_scraper.domain.report import (
    DuplicatePolicy,
    DuplicateRecord,
    EnvelopeCounts,
    ParseReport,
)
from sms_msgs_scraper.parser.registry import REGISTRY

ROOT_TAG = "smses"


class BackupFileError(Exception):
    """The file is not a usable SMS backup.

    Distinct from a parse failure inside a message: this says the file itself
    cannot be read as a backup, and the caller should report it against the
    path the user supplied rather than carrying on with partial results.
    """


@dataclass(frozen=True, slots=True)
class BackupLimits:
    """Bounds on what will be accepted from a backup file.

    The input is normally the user's own backup from their own phone, but it is
    still a file from outside the program, and "it is normally fine" is not a
    size limit. These make a hostile or corrupt file fail fast and clearly
    instead of exhausting memory.
    """

    # The reference backup is ~2.5 MB; 512 MB is far beyond any real backup.
    maxBytes: int = 512 * 1024 * 1024
    # The reference backup holds 4,719 elements.
    maxElements: int = 5_000_000
    # A real backup nests three deep at most: smses > mms > parts > part.
    # A document nested far beyond that is not a backup, whatever else it is.
    maxDepth: int = 32


# XML entity expansion -- the "billion laughs" family -- is the one XML attack
# that matters for a file like this, and it requires a document type
# declaration to set the entities up. Rather than take on a dependency to
# defuse DTDs, this rejects any document that has one: an SMS Backup & Restore
# file never carries a DOCTYPE, so refusing them costs nothing real and closes
# the entire class rather than mitigating it.
#
# Only the prolog is examined, which is where a DOCTYPE must appear.
PROLOG_SNIFF_BYTES = 8192

# One shared instance rather than a fresh default per call. BackupLimits is
# frozen, so there is nothing a caller could mutate through it.
DEFAULT_LIMITS = BackupLimits()


def _rejectDoctype(filepath: Path) -> None:
    with filepath.open("rb") as handle:
        prolog = handle.read(PROLOG_SNIFF_BYTES)

    if b"<!DOCTYPE" in prolog or b"<!doctype" in prolog:
        raise BackupFileError(
            "the file carries a document type declaration; SMS backups do not, "
            "and a DTD is how XML entity-expansion attacks are set up"
        )


class SmsBackupFileParser:
    """Parses one backup file, once."""

    def __init__(
        self,
        registry=REGISTRY,
        duplicatePolicy: DuplicatePolicy = DuplicatePolicy.EXACT,
        limits: BackupLimits = DEFAULT_LIMITS,
    ):
        self.registry = registry
        self.duplicatePolicy = duplicatePolicy
        self.limits = limits
        self._parsed = False

    @staticmethod
    def digestFor(record: SmsRecord) -> str:
        """The duplicate identity of a message: (sender, stripped body).

        The sender is part of it because deduplication would otherwise reach
        across senders -- an unrelated message could suppress a later bank
        message that merely happened to repeat its text. On the reference
        backup that silently discarded 23 messages, 4 of them from bank short
        codes.

        A received timestamp is deliberately NOT part of the identity, even
        though a duplicate is by definition received later. Both candidates were
        measured on the reference backup and both fail as a
        retransmission/distinct-transaction discriminator:

          * `date` (received): the network redelivers the *same* alert as much
            as 2.9 hours late. Two FBL retransmissions arrived 19 minutes and
            2.9 hours after their originals, and their bodies carry the
            transaction time to the second, so they are provably the same
            transaction. Any "within N minutes" window short enough to be
            meaningful would have admitted them as second purchases and inflated
            the totals.
          * `date_sent`: differs on 138 of the 145 repeated (sender, body)
            groups, including those same provably-identical transactions. Adding
            it would disable deduplication almost entirely.

        Fabricating spending is worse than collapsing a genuine repeat, so the
        identity stays with what the message *says* rather than when it arrived
        -- and the report records which suppressions were ambiguous rather than
        pretending the question does not arise.
        """
        # the separator cannot occur in a short code, so no (sender, body) pair
        # can be confused with another by concatenation
        identity = f"{record.sender}\x00{record.strippedBody}"

        return hashlib.sha3_512(identity.encode("utf-8")).hexdigest()

    def parse(self, filepath) -> ParseReport:
        """Read the backup at `filepath` and return what it contained.

        Raises:
            BackupFileError: the file is not a usable SMS backup.
        """
        if self._parsed:
            raise BackupFileError(
                "this parser has already consumed a file; build a new one. "
                "Reusing an instance silently double-counted duplicates and "
                "broke the conservation identity."
            )
        self._parsed = True

        filepath = Path(filepath)
        size = filepath.stat().st_size
        if size > self.limits.maxBytes:
            raise BackupFileError(
                f"backup file is {size:,} bytes, over the "
                f"{self.limits.maxBytes:,} byte limit"
            )

        _rejectDoctype(filepath)

        return self._parseStream(filepath)

    def _parseStream(self, filepath: Path) -> ParseReport:
        counts = defaultdict(int)
        ccTxns = []
        debitTxns = []
        diagnostics = []
        duplicates = []
        # digest -> index of the message that first carried it. Digests only:
        # the message bodies themselves are never retained.
        seenDigests: dict[str, int] = {}

        declared = None
        actual = 0
        smsCount = 0
        mmsCount = 0
        invalidCount = 0

        # The handle is opened here rather than left to iterparse. iterparse
        # closes the file it opened only when iteration runs to completion, so
        # any error raised mid-parse -- a wrong root element, a bad count --
        # leaked it. On Windows that leaves the backup file locked for the life
        # of the process.
        handle = filepath.open("rb")

        try:
            events = ET.iterparse(handle, events=("start", "end"))
            root = None
            # Depth is tracked explicitly because `iterparse` reports an end
            # event for *every* element, at any nesting level. An <mms> element
            # carries <parts>/<part>/<addrs>/<addr> children, so without this
            # the reference backup's 4,719 records read as 5,040, and its 321
            # nested MMS children were each counted as a malformed message.
            depth = 0

            for event, element in events:
                if event == "start":
                    depth += 1
                    if depth > self.limits.maxDepth:
                        raise BackupFileError(
                            f"backup file nests more than "
                            f"{self.limits.maxDepth} elements deep"
                        )
                    if depth == 1:
                        root = element
                        declared = self._readEnvelope(element)
                    continue

                depth -= 1

                if depth == 0:
                    # the root's own end event
                    continue

                if depth > 1:
                    # nested inside a record (MMS parts and addresses); release
                    # it, but it is not itself a record
                    element.clear()
                    continue

                actual += 1
                if actual > self.limits.maxElements:
                    raise BackupFileError(
                        f"backup file holds more than "
                        f"{self.limits.maxElements:,} elements"
                    )

                if element.tag == MMS_TAG:
                    mmsCount += 1
                elif element.tag != SMS_TAG:
                    invalidCount += 1
                    diagnostics.append(
                        ParseDiagnostic(
                            bank="-",
                            reason=SkipReason.MALFORMED_RECORD,
                            sender="-",
                            receivedAt="?",
                            detail=f"unexpected <{element.tag}> element",
                        )
                    )
                else:
                    record = SmsRecord.fromElement(element, actual - 1)
                    if record is None:
                        invalidCount += 1
                        diagnostics.append(
                            ParseDiagnostic(
                                bank="-",
                                reason=SkipReason.MALFORMED_RECORD,
                                sender="-",
                                receivedAt="?",
                                detail="message has no address or no body",
                            )
                        )
                    else:
                        smsCount += 1
                        self._routeRecord(
                            record,
                            counts,
                            ccTxns,
                            debitTxns,
                            diagnostics,
                            duplicates,
                            seenDigests,
                        )

                # release the element, and detach it from the root, so the
                # document does not accumulate in memory as it is read
                element.clear()
                if root is not None:
                    root.clear()
        except ET.ParseError as parseError:
            raise BackupFileError(
                f"not a well-formed XML backup file: {parseError}"
            ) from parseError
        finally:
            handle.close()

        if root is None:
            raise BackupFileError("the backup file is empty")

        envelope = EnvelopeCounts(
            declared=declared if declared is not None else actual,
            actual=actual,
            sms=smsCount,
            mms=mmsCount,
            invalid=invalidCount,
        )

        return ParseReport(
            envelope=envelope,
            counts=MappingProxyType(dict(counts)),
            ccTxns=tuple(sorted(ccTxns, key=txnSortKey)),
            debitTxns=tuple(sorted(debitTxns, key=txnSortKey)),
            diagnostics=tuple(diagnostics),
            duplicates=tuple(duplicates),
            duplicatePolicy=self.duplicatePolicy,
        )

    def _readEnvelope(self, root: ET.Element) -> int | None:
        """Validate the root element and read its declared message count.

        Raises a real error rather than asserting: an `assert` here vanishes
        under `python -O`, which would turn "this is not a backup file" into a
        confusing failure much further downstream.
        """
        if root.tag != ROOT_TAG:
            raise BackupFileError(
                f"root element is <{root.tag}>, not <{ROOT_TAG}> -- this does "
                "not look like an SMS Backup & Restore file"
            )

        declaredValue = root.attrib.get("count")
        if declaredValue is None:
            return None

        try:
            declared = int(declaredValue)
        except ValueError as error:
            raise BackupFileError(
                f"the root element's count attribute is not a number: "
                f"{declaredValue!r}"
            ) from error

        if declared < 0:
            raise BackupFileError(
                f"the root element declares a negative message count: {declared}"
            )

        return declared

    def _routeRecord(
        self,
        record: SmsRecord,
        counts,
        ccTxns,
        debitTxns,
        diagnostics,
        duplicates,
        seenDigests,
    ) -> None:
        """Send one message to its bank's parser, or count it as unrecognised.

        Every counted message lands in exactly one bucket, which is what makes
        `ALL == <banks> + OTHER + DUP` provable rather than hopeful.
        """
        counts["ALL"] += 1

        spec = self.registry.specFor(record.sender)

        # Deduplicate once, up front, for every sender. Doing it inside each
        # bank's branch both duplicated the work and misrouted the result: a
        # duplicate bank message failed its own branch condition and fell
        # through the chain into OTHER.
        if self.duplicatePolicy is not DuplicatePolicy.NONE:
            digest = SmsBackupFileParser.digestFor(record)
            firstIndex = seenDigests.get(digest)
            if firstIndex is not None:
                counts["DUP"] += 1
                duplicates.append(
                    DuplicateRecord(
                        sender=record.sender,
                        firstIndex=firstIndex,
                        duplicateIndex=record.index,
                        # A suppression is ambiguous when the bank's alerts
                        # carry no time of day, so a second genuine identical
                        # purchase that day cannot be told from a
                        # retransmission. This is a conservative upper bound:
                        # it flags every such suppression, not only the ones
                        # whose arrival gap makes a repeat plausible.
                        ambiguous=bool(
                            spec is not None and not spec.has(Capability.TXN_TIME)
                        ),
                    )
                )
                return
            seenDigests[digest] = record.index

        if spec is None:
            counts["OTHER"] += 1
            return

        counts[spec.id] += 1

        if not spec.signal(record):
            return

        result = spec.extract(record)

        if result.diagnostic is not None:
            diagnostics.append(result.diagnostic)

        if not result.succeeded:
            counts[spec.skippedBucket] += 1
            return

        if spec.txnKind is TxnKind.CREDIT_CARD:
            ccTxns.append(result.txn)
        else:
            debitTxns.append(result.txn)
