"""JSON and CSV output, for the callers that are programs rather than people.

Both are peers of the table renderer, not afterthoughts bolted onto it. They
consume the same objects, and the amounts they emit are the same exact
`Decimal` values -- written as **strings**, never as JSON numbers, because a
JSON number is a float in almost every consumer and re-introducing binary
floating point on the way out would undo the entire point of parsing exactly.

Two hardening rules apply to every field that came out of an SMS message:

  * **Control characters are stripped.** A message body is untrusted input, and
    an escape sequence in a vendor name could reposition the cursor or recolour
    a terminal session when a CSV is later `cat`-ed.

  * **Formula injection is defused.** A spreadsheet treats a cell beginning
    `=`, `+`, `-`, `@`, tab or carriage return as a formula. A vendor named
    `=cmd|...` is a live payload in Excel, so such cells are prefixed with a
    single quote, which spreadsheets read as "this is text".
"""

import csv
import io
import json
from collections import Counter

from sms_msgs_scraper.domain.aggregate import txnCountsByMonth, txnDateSpan
from sms_msgs_scraper.parser.registry import REGISTRY
from sms_msgs_scraper.render.console_ui import humanBytes, sanitizeField

# The shape of the machine-readable output. A consumer can pin this and be told
# when it changes, rather than discovering it from a diff in their parser.
OUTPUT_SCHEMA_VERSION = 1

# Characters that make a spreadsheet treat a cell as a formula.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def csvSafe(value) -> str:
    """Make one field safe to write into a CSV a spreadsheet may open."""
    text = sanitizeField(value)

    if text.startswith(_FORMULA_LEADERS):
        return f"'{text}"

    return text


def _ccTxnRow(txn) -> dict:
    return {
        "date": txn.date.isoformat(),
        "bank": txn.bank,
        "card": txn.card.lastFour or "",
        "vendor": txn.vendor,
        "currency": txn.money.currency,
        "amount": str(txn.money.amount),
    }


def _debitTxnRow(txn) -> dict:
    return {
        "date": txn.date.isoformat(),
        "txnType": str(txn.txnType),
        "account": txn.acctMask,
        "vendor": txn.vendor,
        "currency": txn.money.currency,
        "amount": str(txn.money.amount),
    }


CC_TXN_COLUMNS = ("date", "bank", "card", "vendor", "currency", "amount")
DEBIT_TXN_COLUMNS = ("date", "txnType", "account", "vendor", "currency", "amount")


def ccTxnRows(txns) -> list:
    return [_ccTxnRow(txn) for txn in txns]


def debitTxnRows(txns) -> list:
    return [_debitTxnRow(txn) for txn in txns]


def monthlyRows(perMonth, perMonthCounts) -> list:
    """One row per month per currency -- long form, so a consumer never has to
    discover the currency columns by inspection.
    """
    rows = []

    for monthKey in sorted(perMonth):
        for currency in sorted(perMonth[monthKey]):
            rows.append(
                {
                    "month": monthKey,
                    "currency": currency,
                    "total": str(perMonth[monthKey][currency].amount),
                    "txns": perMonthCounts[monthKey],
                }
            )

    return rows


MONTHLY_COLUMNS = ("month", "currency", "total", "txns")


def chartRows(perMonth) -> list:
    """One row per month per series per currency -- the numbers a chart draws.

    Long form, and deliberately the *unfolded* series rather than what the
    chart shows: the terminal chart names at most four series and collects the
    rest under "Other", which is a readability limit of a bar 56 cells wide and
    has no business narrowing what a program is given. A consumer that wants
    the top four can rank four rows itself; one that was handed "Other" could
    never get back what was in it.

    Months with no transactions carry no rows. A gap has to be drawn in a
    chart, but emitting a zero row here would assert a zero total where what is
    true is that nothing was spent -- the same distinction `totalsByGroup`
    keeps by not seeding currencies.
    """
    rows = []

    for monthKey in sorted(perMonth):
        for series in sorted(perMonth[monthKey]):
            for currency in sorted(perMonth[monthKey][series]):
                rows.append(
                    {
                        "month": monthKey,
                        "series": series,
                        "currency": currency,
                        "amount": str(perMonth[monthKey][series][currency].amount),
                    }
                )

    return rows


CHART_COLUMNS = ("month", "series", "currency", "amount")


def _infoRow(section: str, field: str, value, note: str = "") -> dict:
    return {"section": section, "field": field, "value": value, "note": note}


def backupInfoRows(fileInfo, report, verbose: bool = False) -> list:
    """One row per fact about a backup file: which section it belongs to, what
    it is called, what it is, and a short note where the bare value would
    mislead.

    The peer of `tables.backupInfoTables`, and deliberately the same facts in
    the same order. A count is emitted as a number and never as a
    thousands-grouped string: the grouping is a reading aid for a person, and a
    consumer handed `"4,665"` has to undo it before it is a number again.
    """
    envelope = report.envelope
    allTxns = list(report.ccTxns) + list(report.debitTxns)
    first, last = txnDateSpan(allTxns)
    total = report.count("ALL")
    duplicates = report.count("DUP")
    other = report.count("OTHER")

    rows = [
        _infoRow("file", "name", fileInfo.path.name),
        _infoRow("file", "folder", str(fileInfo.path.parent)),
        _infoRow(
            "file", "sizeBytes", fileInfo.sizeBytes, humanBytes(fileInfo.sizeBytes)
        ),
        _infoRow("file", "modified", fileInfo.modifiedAt.isoformat()),
        _infoRow("file", "sha256", fileInfo.sha256),
        _infoRow("envelope", "declared", envelope.declared),
        _infoRow(
            "envelope",
            "actual",
            envelope.actual,
            "" if envelope.matchesDeclared else "does not match declared",
        ),
        _infoRow("envelope", "sms", envelope.sms),
        _infoRow("envelope", "mms", envelope.mms),
        _infoRow("envelope", "invalid", envelope.invalid),
        _infoRow("messages", "all", total),
        _infoRow("messages", "fromBank", total - duplicates - other),
        _infoRow("messages", "fromOtherSender", other),
        _infoRow(
            "messages",
            "duplicatesSuppressed",
            duplicates,
            f"policy {report.duplicatePolicy}",
        ),
        _infoRow("transactions", "ccTxns", len(report.ccTxns)),
        _infoRow("transactions", "debitTxns", len(report.debitTxns)),
        _infoRow("transactions", "ccVendors", len(report.allVendors)),
        _infoRow("transactions", "debitVendors", len(report.debitVendors)),
        # An empty string rather than a fabricated date: a backup with no
        # transactions in it has no first one, and any placeholder date would
        # be a value a consumer could accidentally compare against.
        _infoRow(
            "transactions",
            "firstTxnDate",
            first.isoformat() if first is not None else "",
        ),
        _infoRow(
            "transactions",
            "lastTxnDate",
            last.isoformat() if last is not None else "",
        ),
        _infoRow("transactions", "monthsWithTxns", len(txnCountsByMonth(allTxns))),
    ]

    if not verbose:
        return rows

    stats = report.messageStats

    for spec in REGISTRY:
        for code in spec.senderCodes:
            rows.append(
                _infoRow("senders", code, stats.senderCounts.get(code, 0), spec.id)
            )

    # Counted, never named -- an unrecognized sender is a personal phone
    # number, and the report does not carry the strings to name them with.
    noun = "sender" if stats.unknownSenders == 1 else "senders"
    rows.append(
        _infoRow(
            "senders",
            "unrecognized",
            stats.unknownSenderMsgs,
            f"{stats.unknownSenders} distinct {noun}",
        )
    )

    failures = Counter(
        (diagnostic.bank, str(diagnostic.reason))
        for diagnostic in report.diagnostics
    )
    for (bank, reason), count in sorted(failures.items()):
        rows.append(_infoRow("parseFailures", bank, count, reason))

    rows.append(_infoRow("duplicates", "policy", str(report.duplicatePolicy)))
    rows.append(_infoRow("duplicates", "suppressed", len(report.duplicates)))
    rows.append(
        _infoRow(
            "duplicates",
            "ambiguous",
            report.ambiguousDuplicates,
            "could not be proved to be retransmissions",
        )
    )

    return rows


INFO_COLUMNS = ("section", "field", "value", "note")


def toJson(payloadKind: str, rows: list, meta: dict | None = None) -> str:
    """Serialise rows as a schema-versioned JSON document."""
    document = {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "kind": payloadKind,
        "count": len(rows),
        "rows": [
            {key: sanitizeField(value) if isinstance(value, str) else value
             for key, value in row.items()}
            for row in rows
        ],
    }

    if meta:
        document["meta"] = meta

    return json.dumps(document, indent=2, ensure_ascii=False)


def toCsv(columns, rows: list) -> str:
    """Serialise rows as CSV, with every field guarded."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()

    for row in rows:
        writer.writerow({column: csvSafe(row[column]) for column in columns})

    return buffer.getvalue()
