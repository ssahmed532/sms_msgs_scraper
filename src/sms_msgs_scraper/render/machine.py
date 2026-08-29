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

from sms_msgs_scraper.render.console_ui import sanitizeField

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
