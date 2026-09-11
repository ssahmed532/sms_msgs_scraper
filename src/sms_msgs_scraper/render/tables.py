"""Rich table rendering.

One of three peer renderers. This one is the default because a person is the
usual reader; it has no privileged access to anything the JSON and CSV
renderers cannot reach, and all three are handed the same objects.
"""

from collections import Counter

from sms_msgs_scraper.domain.aggregate import (
    countsByAttribute,
    grandTotals,
    monthlyTotals,
    totalsByCurrency,
    totalsByGroup,
    txnCountsByMonth,
    txnDateSpan,
)
from sms_msgs_scraper.domain.bank import TxnKind
from sms_msgs_scraper.domain.money import MINOR_UNITS
from sms_msgs_scraper.parser.registry import REGISTRY
from sms_msgs_scraper.render.console_ui import (
    EMPTY_VALUE,
    acctText,
    amountText,
    bankText,
    cardText,
    countText,
    currencyText,
    dataTable,
    dateText,
    humanBytes,
    indexText,
    labelText,
    printSideBySide,
    sanitizeField,
    summaryTable,
    totalText,
    txnTypeText,
    vendorText,
)

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


def _groupedTotalsTable(groupHeader, orderedKeys, perGroup, perGroupCounts, groupCell):
    """A spending summary grouped one way: one row per group, one column per
    currency actually spent, and a footer carrying the exact grand totals.

    The grouping is the only thing that varies -- by month for a summary over
    time, by bank for a single month's spend -- so the currency columns, the
    absent-cell rule and the totals row are decided in one place for both.
    """
    totals = grandTotals(perGroup)
    currencies = _currencyColumns(totals)

    table = summaryTable(showFooter=True)
    table.add_column(groupHeader, footer=labelText("TOTAL", style="column.total"))
    table.add_column(
        "Txns",
        justify="right",
        footer=countText(sum(perGroupCounts.values()), style="column.total"),
    )
    for currency in currencies:
        table.add_column(
            currency,
            justify="right",
            footer=labelText(totals[currency].formatted(), style="column.total"),
        )

    for key in orderedKeys:
        row = [groupCell(key), countText(perGroupCounts[key])]
        row.extend(
            totalText(perGroup[key].get(currency), currency)
            for currency in currencies
        )
        table.add_row(*row)

    return table


def aggregateSpendTable(txns):
    """What a filtered listing adds up to: one row per currency, each carrying
    that currency's transaction count and exact total.

    One row per currency rather than one row with a currency per column,
    because there is no TOTAL to put under such columns -- currencies are never
    added together, so a listing that spans three currencies has three answers,
    not one.
    """
    totals = totalsByCurrency(txns)

    counts: dict[str, int] = {}
    for txn in txns:
        currency = txn.money.currency
        counts[currency] = counts.get(currency, 0) + 1

    table = summaryTable("Aggregate spend")
    table.add_column("Cur")
    table.add_column("Txns", justify="right")
    table.add_column("Total", justify="right")

    for currency in _currencyColumns(totals):
        table.add_row(
            currencyText(currency),
            countText(counts[currency]),
            amountText(totals[currency]),
        )

    return table


def monthlySummaryTable(txns):
    """A month-by-month spending summary."""
    perMonth = monthlyTotals(txns)

    return _groupedTotalsTable(
        "Month",
        sorted(perMonth),
        perMonth,
        txnCountsByMonth(txns),
        lambda monthKey: labelText(monthKey, style="column.date"),
    )


def bankSpendTable(txns):
    """The same summary grouped by issuing bank instead of by month.

    The footer is the answer the caller asked for -- one exact total per
    currency across every bank -- and the rows above it say which cards it came
    from. Registered banks appear in registry order so the row order does not
    depend on which bank happened to be spent on first; anything else is
    appended, sorted, rather than dropped.
    """
    perBank = totalsByGroup(txns, lambda txn: txn.bank)
    ordered = [bankId for bankId in REGISTRY.ccBankIds if bankId in perBank]
    ordered.extend(
        sorted(bankId for bankId in perBank if bankId not in REGISTRY.ccBankIds)
    )

    return _groupedTotalsTable(
        "Bank",
        ordered,
        perBank,
        countsByAttribute(txns, "bank"),
        bankText,
    )


def _fieldTable(title: str, rows, caption: str | None = None):
    """The two-column Field/Value shape most of the backup metadata takes.

    Values arrive already rendered as `Text`, because what a value *is* --
    a count, a date, a bank, a digest -- decides how it is styled, and that is
    the caller's knowledge rather than this helper's.
    """
    table = summaryTable(title, caption=caption)
    # min_width so the caption below each table sits on one line. Rich wraps a
    # caption to the table's own width, and a four-column-wide table with a
    # forty-character caption under it is three lines of ragged text.
    table.add_column("Field", style="muted", min_width=21)
    table.add_column("Value", justify="right", overflow="fold", min_width=24)

    for label, value in rows:
        table.add_row(labelText(label, style="muted"), value)

    return table


def _fileTable(fileInfo):
    """The file as the filesystem describes it, before it was read.

    The name and the folder are sanitized for the same reason a vendor is: they
    are strings from outside the program, and a path is perfectly capable of
    carrying an escape sequence into a terminal.
    """
    return _fieldTable(
        "File",
        [
            ("Name", labelText(sanitizeField(fileInfo.path.name))),
            ("Folder", labelText(sanitizeField(str(fileInfo.path.parent)), style="muted")),
            (
                "Size",
                labelText(
                    f"{fileInfo.sizeBytes:,} bytes "
                    f"({humanBytes(fileInfo.sizeBytes)})"
                ),
            ),
            ("Modified", dateText(fileInfo.modifiedAt)),
            ("SHA-256", labelText(fileInfo.sha256, style="muted")),
        ],
        caption="times are Asia/Karachi",
    )


def _envelopeTable(report):
    """What the file said it held, against what was found in it."""
    envelope = report.envelope
    matches = envelope.matchesDeclared

    return _fieldTable(
        "Envelope",
        [
            ("Declared", countText(envelope.declared)),
            (
                "Found",
                countText(
                    envelope.actual,
                    style="column.count" if matches else "warning",
                ),
            ),
            ("<sms>", countText(envelope.sms)),
            ("<mms>", countText(envelope.mms)),
            (
                "Invalid",
                countText(
                    envelope.invalid,
                    style="bucket.skipped" if envelope.invalid else "column.empty",
                ),
            ),
        ],
        caption=(
            "declared counts <mms> elements too"
            if matches
            else "MISMATCH: this file does not hold what it declares"
        ),
    )


def _messagesTable(report):
    """Where the file's messages went, in the same buckets the parse summary
    uses -- on stdout this time, because here they are the answer rather than
    the furniture around one.
    """
    total = report.count("ALL")
    duplicates = report.count("DUP")
    other = report.count("OTHER")

    return _fieldTable(
        "Messages",
        [
            ("Messages", countText(total)),
            ("From a bank", countText(total - duplicates - other)),
            ("From another sender", countText(other, style="bucket.other")),
            (
                "Duplicates suppressed",
                countText(
                    duplicates,
                    style="bucket.dup" if duplicates else "column.empty",
                ),
            ),
        ],
        caption=f"duplicate policy: {report.duplicatePolicy}",
    )


def _transactionsTable(report):
    """What was extracted, and the window the file actually covers.

    The span is over transaction dates rather than message timestamps because a
    transaction date is what the bank *said* -- parsed, validated and stamped
    Asia/Karachi -- while a received timestamp is when the network got round to
    delivering the alert, which this project has measured at up to 2.9 hours
    later.
    """
    allTxns = list(report.ccTxns) + list(report.debitTxns)
    first, last = txnDateSpan(allTxns)

    def spanCell(moment):
        return (
            dateText(moment)
            if moment is not None
            else labelText(EMPTY_VALUE, style="column.empty")
        )

    return _fieldTable(
        "Transactions",
        [
            ("Credit card", countText(len(report.ccTxns))),
            ("Account debit", countText(len(report.debitTxns))),
            ("CC vendors", countText(len(report.allVendors))),
            ("Debit vendors", countText(len(report.debitVendors))),
            ("First transaction", spanCell(first)),
            ("Last transaction", spanCell(last)),
            ("Months with txns", countText(len(txnCountsByMonth(allTxns)))),
        ],
        caption="dates are the bank's, not the alert's",
    )


def _sendersTable(report):
    """One row per declared short code, and one for everyone else.

    Every registered code gets a row even when it sent nothing, because a zero
    here is a finding: it says a code this build routes has gone quiet, which is
    what a bank re-homing its alerts looks like from the outside. HBL's move
    from 4250 to 14250 in January 2025 is exactly this shape.

    Unrecognized senders are counted, never named. They are personal phone
    numbers, and a list of them is a contact list.
    """
    stats = report.messageStats
    total = sum(stats.senderCounts.values()) + stats.unknownSenderMsgs

    table = summaryTable(
        "Senders",
        showFooter=True,
        caption="refines the bank counts exactly",
    )
    table.add_column(
        "Sender", footer=labelText("ALL", style="column.total"), min_width=14
    )
    table.add_column("Bank", min_width=8)
    table.add_column(
        "Msgs",
        justify="right",
        footer=countText(total, style="column.total"),
    )

    for spec in REGISTRY:
        for code in spec.senderCodes:
            count = stats.senderCounts.get(code, 0)
            table.add_row(
                labelText(code, style="column.count" if count else "column.empty"),
                bankText(spec.id),
                countText(
                    count, style="column.count" if count else "column.empty"
                ),
            )

    table.add_section()
    noun = "sender" if stats.unknownSenders == 1 else "senders"
    table.add_row(
        labelText(f"{stats.unknownSenders:,} {noun}", style="bucket.other"),
        labelText("OTHER", style="bucket.other"),
        countText(stats.unknownSenderMsgs, style="bucket.other"),
    )

    return table


def _parseFailuresTable(report):
    """Skips by bank and reason. "26 skipped" is a number; "21 truncated, 5
    with no amount" is a diagnosis.
    """
    counts = Counter(
        (diagnostic.bank, str(diagnostic.reason))
        for diagnostic in report.diagnostics
    )

    table = summaryTable("Parse failures", showFooter=True)
    table.add_column("Bank", footer=labelText("ALL", style="column.total"))
    table.add_column("Reason")
    table.add_column(
        "Msgs",
        justify="right",
        footer=countText(sum(counts.values()), style="column.total"),
    )

    for (bank, reason), count in sorted(counts.items()):
        table.add_row(
            bankText(bank),
            labelText(reason, style="bucket.skipped"),
            countText(count),
        )

    return table


def _duplicatesTable(report):
    """What deduplication did, and how much of it was a judgement call."""
    ambiguous = report.ambiguousDuplicates

    return _fieldTable(
        "Duplicates",
        [
            ("Policy", labelText(str(report.duplicatePolicy))),
            ("Suppressed", countText(len(report.duplicates))),
            (
                "Ambiguous",
                countText(
                    ambiguous,
                    style="bucket.dup" if ambiguous else "column.empty",
                ),
            ),
        ],
        caption="ambiguous: no time of day in the alert",
    )


def backupInfoTables(fileInfo, report, verbose: bool = False):
    """Everything known about one backup file, as tables to print in order.

    The peer of `machine.backupInfoRows`, and the two must describe the same
    facts: a person reading the table and a script reading the JSON are asking
    the same question of the same file, and an answer that differed between
    them would be a defect in whichever one someone trusted.

    `verbose` adds the breakdowns *within* those counts -- which short code,
    which failure reason, which duplicates were a judgement call -- rather than
    any new fact about the file.
    """
    tables = [
        _fileTable(fileInfo),
        _envelopeTable(report),
        _messagesTable(report),
        _transactionsTable(report),
    ]

    if verbose:
        tables.append(_sendersTable(report))
        # Only when there were any. An empty failures table reads as a table
        # that failed to populate rather than as a clean run.
        if report.diagnostics:
            tables.append(_parseFailuresTable(report))
        tables.append(_duplicatesTable(report))

    return tables


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
