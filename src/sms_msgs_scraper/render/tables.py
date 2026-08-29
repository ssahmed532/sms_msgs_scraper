"""Rich table rendering.

One of three peer renderers. This one is the default because a person is the
usual reader; it has no privileged access to anything the JSON and CSV
renderers cannot reach, and all three are handed the same objects.
"""

from sms_msgs_scraper.console_ui import (
    EMPTY_VALUE,
    acctText,
    amountText,
    bankText,
    cardText,
    countText,
    currencyText,
    dataTable,
    dateText,
    indexText,
    labelText,
    printSideBySide,
    summaryTable,
    totalText,
    txnTypeText,
    vendorText,
)
from sms_msgs_scraper.domain.aggregate import (
    countsByAttribute,
    grandTotals,
    monthlyTotals,
    txnCountsByMonth,
)
from sms_msgs_scraper.domain.money import MINOR_UNITS
from sms_msgs_scraper.domain.registry import REGISTRY, TxnKind

# The order currency columns appear in a monthly summary, derived from the
# currency table rather than restated here -- there is one list of the
# currencies this tool knows about, and it lives with the money type. Anything
# outside it is appended, sorted, after it: a bank parser accepts any
# well-formed three-letter code.
CURRENCY_COLUMN_ORDER = tuple(MINOR_UNITS)


def _breakdownCaption(total: int, noun: str, counts: dict, keys) -> str:
    """The dim line under a listing: the total, then the non-zero per-group
    counts that make it up.
    """
    parts = [f"{total:,} {noun}"]
    parts.extend(f"{key} {counts[key]:,}" for key in keys if counts.get(key))

    return "  ·  ".join(parts)


def ccTxnsTable(txns):
    """Credit card transactions, one row per transaction."""
    table = dataTable(
        [
            ("#", {"justify": "right"}),
            ("Date", {"no_wrap": True}),
            ("Bank", {}),
            ("Card", {}),
            ("Vendor", {"overflow": "fold"}),
            ("Cur", {}),
            ("Amount", {"justify": "right"}),
        ],
        caption=_breakdownCaption(
            len(txns),
            "transactions",
            countsByAttribute(txns, "bank"),
            REGISTRY.ccBankIds,
        ),
    )

    for index, txn in enumerate(txns, start=1):
        table.add_row(
            indexText(index),
            dateText(txn.date),
            bankText(txn.bank),
            cardText(txn.card),
            vendorText(txn.vendor),
            currencyText(txn.money.currency),
            amountText(txn.money),
        )

    return table


def debitTxnsTable(txns, txnTypes):
    """Account debits, one row per transaction."""
    table = dataTable(
        [
            ("#", {"justify": "right"}),
            ("Date", {"no_wrap": True}),
            ("Type", {}),
            ("Account", {}),
            ("Vendor", {"overflow": "fold"}),
            ("Cur", {}),
            ("Amount", {"justify": "right"}),
        ],
        caption=_breakdownCaption(
            len(txns),
            "transactions",
            countsByAttribute(txns, "txnType"),
            txnTypes,
        ),
    )

    for index, txn in enumerate(txns, start=1):
        table.add_row(
            indexText(index),
            dateText(txn.date),
            txnTypeText(txn.txnType),
            acctText(txn.acctMask),
            vendorText(txn.vendor),
            currencyText(txn.money.currency),
            amountText(txn.money),
        )

    return table


def vendorsTable(vendors, txnCount: int):
    """Unique vendor names, one row each."""
    table = dataTable(
        [
            ("#", {"justify": "right"}),
            # min_width so the caption below sits on one line even when every
            # vendor name is short
            ("Vendor", {"overflow": "fold", "min_width": 32}),
        ],
        caption=f"{len(vendors):,} vendors  ·  from {txnCount:,} transactions",
    )

    for index, vendor in enumerate(vendors, start=1):
        table.add_row(indexText(index), vendorText(vendor))

    return table


def _currencyColumns(totals: dict) -> list:
    """The currency columns worth showing, in a stable order."""
    columns = [
        currency for currency in CURRENCY_COLUMN_ORDER if currency in totals
    ]
    columns.extend(
        sorted(
            currency for currency in totals if currency not in CURRENCY_COLUMN_ORDER
        )
    )

    return columns


def monthlySummaryTable(txns):
    """A month-by-month spending summary: one row per month, one column per
    currency actually spent, and a footer carrying the exact grand totals.
    """
    perMonth = monthlyTotals(txns)
    perMonthCounts = txnCountsByMonth(txns)
    totals = grandTotals(perMonth)
    currencies = _currencyColumns(totals)

    table = summaryTable(showFooter=True)
    table.add_column("Month", footer=labelText("TOTAL", style="column.total"))
    table.add_column(
        "Txns",
        justify="right",
        footer=countText(sum(perMonthCounts.values()), style="column.total"),
    )
    for currency in currencies:
        table.add_column(
            currency,
            justify="right",
            footer=labelText(totals[currency].formatted(), style="column.total"),
        )

    for monthKey in sorted(perMonth):
        row = [
            labelText(monthKey, style="column.date"),
            countText(perMonthCounts[monthKey]),
        ]
        row.extend(
            totalText(perMonth[monthKey].get(currency), currency)
            for currency in currencies
        )
        table.add_row(*row)

    return table


def parseSummaryTables(report):
    """The two tables describing the run: where the messages came from, and
    what was extracted from them.

    Printed side by side because they are read together -- the conservation
    identity is checked by eye against the left table, and the right one says
    how much of that turned into transactions.
    """
    msgTable = summaryTable("Messages parsed")
    msgTable.add_column("Source")
    msgTable.add_column("Msgs", justify="right")

    for spec in REGISTRY:
        msgTable.add_row(bankText(spec.id), countText(report.count(spec.id)))

    msgTable.add_row(
        labelText("OTHER", style="bucket.other"),
        countText(report.count("OTHER"), style="bucket.other"),
    )
    msgTable.add_row(
        labelText("DUP", style="bucket.dup"),
        countText(report.count("DUP"), style="bucket.dup"),
    )
    msgTable.add_section()
    msgTable.add_row(
        labelText("ALL", style="column.total"),
        countText(report.count("ALL"), style="column.total"),
    )

    txnTable = summaryTable("Transactions extracted")
    txnTable.add_column("Bank")
    txnTable.add_column("Kind")
    txnTable.add_column("Txns", justify="right")
    txnTable.add_column("Skipped", justify="right")

    ccPerBank = countsByAttribute(report.ccTxns, "bank")

    for spec in REGISTRY:
        if spec.txnKind is TxnKind.CREDIT_CARD:
            kind = "credit card"
            txnCount = ccPerBank.get(spec.id, 0)
        else:
            kind = "account debit"
            txnCount = len(report.debitTxns)

        skippedCount = report.count(spec.skippedBucket)
        txnTable.add_row(
            bankText(spec.id),
            labelText(kind, style="muted"),
            countText(txnCount),
            countText(
                skippedCount,
                style="bucket.skipped" if skippedCount else "column.empty",
            ),
        )

    txnTable.add_section()
    txnTable.add_row(
        labelText("ALL", style="column.total"),
        labelText("", style="column.total"),
        countText(len(report.ccTxns) + len(report.debitTxns), style="column.total"),
        labelText(EMPTY_VALUE, style="column.empty"),
    )

    return msgTable, txnTable


def printParseSummary(report) -> None:
    printSideBySide(*parseSummaryTables(report))
