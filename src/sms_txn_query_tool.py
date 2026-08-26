import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from time import perf_counter

# rich_click is a drop-in re-export of click that renders --help and usage
# errors through Rich. Every decorator, type and exception below is click's own.
import rich_click as click

from cc_txn import CreditCardTxnDC
from common import Currency
from console_ui import (
    acctText,
    amountText,
    bankText,
    cardText,
    console,
    countText,
    currencyText,
    dataTable,
    dateText,
    indexText,
    labelText,
    printEmptyState,
    printHeader,
    printNotice,
    printRule,
    statusSpinner,
    summaryTable,
    totalText,
    txnTypeText,
    vendorText,
)
from debit_txn import DebitTxnDC, DebitTxnType
from sms_backup_file_parser import SmsBackupFileParser

# the global instance of the SMS backup msgs file parser
smsParser = None

# --- how rich_click renders --help and usage errors -------------------------
#
# Keyed on "*" so that the command groups apply whatever prog name the tool is
# invoked under: it is run as a script path (`uv run src/sms_txn_query_tool.py`)
# rather than as an installed console script, so its command path is whatever
# argv[0] happened to be.
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.MAX_WIDTH = 100
click.rich_click.STYLE_OPTION = "bold cyan"
click.rich_click.STYLE_SWITCH = "bold green"
click.rich_click.STYLE_METAVAR = "magenta"
click.rich_click.STYLE_USAGE = "bold yellow"
click.rich_click.STYLE_HEADER_TEXT = "bold cyan"
click.rich_click.STYLE_OPTION_DEFAULT = "dim"
click.rich_click.STYLE_OPTIONS_PANEL_BORDER = "dim cyan"
click.rich_click.STYLE_COMMANDS_PANEL_BORDER = "dim cyan"
click.rich_click.STYLE_ERRORS_PANEL_BORDER = "red"
click.rich_click.COMMAND_GROUPS = {
    "*": [
        {
            "name": "Credit card commands (HBL + FBL + SCB)",
            "commands": [
                "list_all_cc_txns",
                "list_all_vendors",
                "monthly_cc_spending_summary",
            ],
        },
        {
            "name": "Meezan account debit commands",
            "commands": [
                "list_all_debit_txns",
                "monthly_debit_spending_summary",
            ],
        },
    ]
}


@click.group()
@click.version_option("1.1.0", prog_name="sms_txn_query_tool")
@click.argument(
    "filepath",
    type=click.Path(
        exists=True,
        file_okay=True,
        # A directory would satisfy exists=True and then fail deep inside
        # ET.parse with a bare OSError; rejecting it here reports it as the bad
        # argument it is.
        dir_okay=False,
        readable=True,
        path_type=Path,
    ),
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    # A Click group stops parsing its own options at the first positional
    # argument, so this only takes effect written before FILEPATH. It is a group
    # option rather than a per-command one because the header and the parse
    # summary are already on screen by the time a subcommand is reached.
    help="Render without colour. Must be written [bold]before[/] FILEPATH. "
    "Colour is dropped automatically when output is piped, or when NO_COLOR is "
    "set in the environment.",
)
def cli(filepath, no_color):
    """Parse and analyze bank transaction SMS messages from an Android SMS
    backup file.

    FILEPATH is the XML backup produced by the [italic]SMS Backup & Restore[/]
    app. It is read and parsed once, up front, and every subcommand then
    reports on what was found.
    """
    global smsParser

    if no_color:
        console.no_color = True

    printHeader(
        "sms_txn_query_tool",
        {"Backup": filepath.name, "Folder": filepath.parent},
    )

    smsParser = SmsBackupFileParser()
    timeStart = perf_counter()

    try:
        with statusSpinner("Reading the backup file..."):
            smsParser.loadFromSmsBackupFile(filepath)
    except PermissionError:
        raise click.BadParameter(
            f"cannot read the SMS backup file: {filepath}", param_hint="FILEPATH"
        )
    except ET.ParseError as parseError:
        raise click.BadParameter(
            f"not a well-formed XML backup file ({parseError}): {filepath}",
            param_hint="FILEPATH",
        )

    msgCount = smsParser.parseMessages()
    timeEnd = perf_counter()

    console.print()
    printNotice(
        f"Parsed {msgCount:,} SMS messages in {timeEnd - timeStart:.2f} seconds"
    )


# The date format accepted by the --from-date / --to-date options
DATE_RANGE_FMT = "%Y-%m-%d"

# The banks whose Credit Card txns are parsed, and hence can be filtered on
CC_BANKS = ["HBL", "FBL", "SCB"]

# The order currency columns appear in a monthly summary. Anything the parsers
# turn up outside this list is appended, sorted, after it — a bank parser will
# accept any 3-letter code.
CURRENCY_COLUMN_ORDER = [
    Currency.PKR.value,
    Currency.USD.value,
    Currency.CAD.value,
]


def _filterTxnsByDateRange(txns: list, fromDate, toDate) -> list:
    """Filter txns down to an inclusive [fromDate, toDate] range.

    Both bounds are optional; either may be None to leave that end open.

    Comparison is on the calendar date only. Txn dates are timezone-aware
    while the CLI options are not, so comparing the datetimes directly would
    raise TypeError on the naive/aware mismatch. Comparing dates also keeps
    both bounds inclusive for txns that carry a real time of day (FBL and
    Meezan msgs do; HBL msgs are midnight-stamped).

    Args:
        txns (list): the CreditCardTxnDC / DebitTxnDC transactions to filter
        fromDate (datetime | None): inclusive lower bound
        toDate (datetime | None): inclusive upper bound

    Returns:
        list: the transactions falling inside the range
    """
    if fromDate is None and toDate is None:
        return txns

    if fromDate is not None and toDate is not None and fromDate > toDate:
        raise click.BadParameter(
            f"--from-date ({fromDate.date()}) is after --to-date ({toDate.date()})"
        )

    filtered = txns
    if fromDate is not None:
        filtered = [txn for txn in filtered if txn.date.date() >= fromDate.date()]
    if toDate is not None:
        filtered = [txn for txn in filtered if txn.date.date() <= toDate.date()]

    return filtered


def _filterTxnsByBank(txns: list, bank) -> list:
    """Filter CC txns down to a single issuing bank.

    Args:
        txns (list): the CreditCardTxnDC transactions to filter
        bank (str | None): the bank ID to keep, or None to keep every bank

    Returns:
        list: the transactions from the requested bank
    """
    if bank is None:
        return txns

    # click.Choice(case_sensitive=False) hands back the declared choice string
    # (e.g. "FBL" for an input of "fbl"), so no case normalization is needed.
    return [txn for txn in txns if txn.bank == bank]


def _dateRangeLabel(fromDate, toDate) -> str:
    """Build a human-readable suffix describing the active date range.

    Returns:
        str: e.g. " (from 2023-01-01 to 2023-12-31)", or "" if unfiltered
    """
    if fromDate is None and toDate is None:
        return ""

    if fromDate is None:
        return f" (up to {toDate.date()})"

    if toDate is None:
        return f" (from {fromDate.date()})"

    return f" (from {fromDate.date()} to {toDate.date()})"


def _bankLabel(bank) -> str:
    """Build a human-readable suffix describing the active --bank filter.

    Returns:
        str: e.g. " (bank FBL)", or "" if unfiltered
    """
    if bank is None:
        return ""

    return f" (bank {bank})"


def _txnTypeLabel(txnType) -> str:
    """Build a human-readable suffix describing the active --txn-type filter.

    Returns:
        str: e.g. " (type atm_withdrawal)", or "" if unfiltered
    """
    if txnType is None:
        return ""

    return f" (type {txnType})"


def _breakdownCaption(total: int, noun: str, counts: dict, keys) -> str:
    """Build the dim line under a listing: the total, then the non-zero
    per-group counts that make it up.

    Args:
        total (int): how many rows the table holds
        noun (str): what the rows are, e.g. "transactions"
        counts (dict): group key -> count
        keys (iterable): the group keys, in the order they should be shown

    Returns:
        str: e.g. "1,678 transactions  ·  HBL 717  ·  FBL 583  ·  SCB 378"
    """
    parts = [f"{total:,} {noun}"]
    parts.extend(f"{key} {counts[key]:,}" for key in keys if counts[key])

    return "  ·  ".join(parts)


def _ccTxnsTable(txns: list):
    """Render CC txns as a table, one row per txn.

    Args:
        txns (list): the CreditCardTxnDC transactions to show

    Returns:
        Table: ready to print
    """
    txnsPerBank = defaultdict(int)
    for txn in txns:
        txnsPerBank[txn.bank] += 1

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
        caption=_breakdownCaption(len(txns), "transactions", txnsPerBank, CC_BANKS),
    )

    for index, txn in enumerate(txns, start=1):
        table.add_row(
            indexText(index),
            dateText(txn.date),
            bankText(txn.bank),
            cardText(txn.ccLastFourDigits),
            vendorText(txn.vendor),
            currencyText(txn.amountTuple.currency),
            amountText(txn.amountTuple.amount, txn.amountTuple.currency),
        )

    return table


def _debitTxnsTable(txns: list):
    """Render account debit txns as a table, one row per txn.

    Args:
        txns (list): the DebitTxnDC transactions to show

    Returns:
        Table: ready to print
    """
    txnsPerType = defaultdict(int)
    for txn in txns:
        txnsPerType[str(txn.txnType)] += 1

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
            txnsPerType,
            [txnType.value for txnType in DebitTxnType],
        ),
    )

    for index, txn in enumerate(txns, start=1):
        table.add_row(
            indexText(index),
            dateText(txn.date),
            txnTypeText(txn.txnType),
            acctText(txn.acctMask),
            vendorText(txn.vendor),
            currencyText(txn.amountTuple.currency),
            amountText(txn.amountTuple.amount, txn.amountTuple.currency),
        )

    return table


def _monthlySummaryTable(txnsPerMonth: dict, monthlyTotals: dict):
    """Render a month-by-month spending summary: one row per month, one column
    per currency, and a footer row carrying the grand totals.

    Only currencies that actually carry spending get a column. Every month is
    pre-seeded with PKR/USD/CAD (see _updateMonthlyTotals), so without that
    filter a PKR-only backup would show two columns of nothing but dashes.

    Args:
        txnsPerMonth (dict): "YYYY_MM" -> number of txns
        monthlyTotals (dict): "YYYY_MM" -> {currency code -> total}

    Returns:
        Table: ready to print
    """
    grandTotals = defaultdict(float)
    for totals in monthlyTotals.values():
        for currency, amount in totals.items():
            grandTotals[currency] += amount

    spentCurrencies = [
        currency for currency in CURRENCY_COLUMN_ORDER if grandTotals.get(currency)
    ]
    spentCurrencies.extend(
        sorted(
            currency
            for currency, total in grandTotals.items()
            if total and currency not in CURRENCY_COLUMN_ORDER
        )
    )

    table = summaryTable(showFooter=True)
    table.add_column("Month", footer=labelText("TOTAL", style="column.total"))
    table.add_column(
        "Txns",
        justify="right",
        footer=countText(sum(txnsPerMonth.values()), style="column.total"),
    )
    for currency in spentCurrencies:
        table.add_column(
            currency.upper(),
            justify="right",
            footer=labelText(f"{grandTotals[currency]:,.2f}", style="column.total"),
        )

    for monthKey in sorted(monthlyTotals):
        row = [
            labelText(monthKey.replace("_", "-"), style="column.date"),
            countText(txnsPerMonth[monthKey]),
        ]
        row.extend(
            totalText(monthlyTotals[monthKey].get(currency, 0.00), currency.upper())
            for currency in spentCurrencies
        )
        table.add_row(*row)

    return table


@cli.command("list_all_vendors")
@click.option(
    "--from-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or before this date (YYYY-MM-DD).",
)
@click.option(
    "--bank",
    type=click.Choice(CC_BANKS, case_sensitive=False),
    default=None,
    help="Only include transactions from this bank (default: all banks).",
)
def list_all_vendors(from_date, to_date, bank):
    """List the unique vendors seen in credit card transactions, sorted
    alphabetically.
    """
    txns = _filterTxnsByBank(smsParser.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    vendors = sorted({txn.vendor for txn in txns})

    printRule("Credit card vendors")

    if not vendors:
        printEmptyState("No vendors match this filter.")
        return

    printNotice(
        f"Found {len(vendors):,} unique vendors"
        f"{_bankLabel(bank)}{_dateRangeLabel(from_date, to_date)}:"
    )

    table = dataTable(
        [
            ("#", {"justify": "right"}),
            # min_width so that the caption below has room to sit on one line
            # even when every vendor name is short
            ("Vendor", {"overflow": "fold", "min_width": 32}),
        ],
        caption=f"{len(vendors):,} vendors  ·  from {len(txns):,} transactions",
    )
    for index, vendor in enumerate(vendors, start=1):
        table.add_row(indexText(index), vendorText(vendor))

    console.print(table)


@cli.command("list_all_cc_txns")
@click.option(
    "--from-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or before this date (YYYY-MM-DD).",
)
@click.option(
    "--bank",
    type=click.Choice(CC_BANKS, case_sensitive=False),
    default=None,
    help="Only include transactions from this bank (default: all banks).",
)
def list_all_cc_txns(from_date, to_date, bank):
    """List every credit card transaction, from HBL, Faysal Bank and Standard
    Chartered together.
    """
    txns = _filterTxnsByBank(smsParser.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)

    printRule("Credit card transactions")

    if not txns:
        printEmptyState("No credit card transactions match this filter.")
        return

    printNotice(
        f"Found {len(txns):,} CC transactions"
        f"{_bankLabel(bank)}{_dateRangeLabel(from_date, to_date)}:"
    )
    console.print(_ccTxnsTable(txns))


#
# monthlyTotals["2023_12"] -> {"PKR": 0.00, "CAD": 0.00, "USD": 0.00}
#
def _updateMonthlyTotals(
    txn: CreditCardTxnDC | DebitTxnDC, monthlyTotals: dict
) -> None:
    monthKey = txn.date.strftime("%Y_%m")
    currencyKey = txn.amountTuple.currency.lower()

    if monthKey not in monthlyTotals:
        monthlyTotals[monthKey] = {
            Currency.CAD.value.lower(): 0.00,
            Currency.PKR.value.lower(): 0.00,
            Currency.USD.value.lower(): 0.00
        }

    # A month is pre-seeded with pkr/cad/usd only, but the bank parsers accept
    # any 3-letter currency code. Seeding an unseen currency here — rather than
    # next to a += inside each branch — covers both paths at once: a first-seen
    # currency that also opens a new month needs the seed just as much as one
    # landing in an existing month.
    monthlyTotals[monthKey].setdefault(currencyKey, 0.00)
    monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount


def _accumulateMonthlyTotals(txns: list):
    """Fold txns into the per-month txn counts and per-month currency totals.

    Returns:
        tuple: (txnsPerMonth, monthlySpendingTotals)
    """
    txnsPerMonth = defaultdict(int)
    monthlySpendingTotals = {}

    for txn in txns:
        txnsPerMonth[txn.date.strftime("%Y_%m")] += 1
        _updateMonthlyTotals(txn, monthlySpendingTotals)

    return txnsPerMonth, monthlySpendingTotals


@cli.command("monthly_cc_spending_summary")
@click.option(
    "--from-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or before this date (YYYY-MM-DD).",
)
@click.option(
    "--bank",
    type=click.Choice(CC_BANKS, case_sensitive=False),
    default=None,
    help="Only include transactions from this bank (default: all banks).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
def monthly_cc_spending_summary(from_date, to_date, bank, verbose):
    """Summarize credit card spending month by month, one column per currency."""
    txns = _filterTxnsByBank(smsParser.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)

    printRule("Month-wise CC spending")

    if not txns:
        printEmptyState("No credit card transactions match this filter.")
        return

    printNotice(
        f"Summarizing {len(txns):,} CC transactions"
        f"{_bankLabel(bank)}{_dateRangeLabel(from_date, to_date)}:"
    )

    if verbose:
        console.print(_ccTxnsTable(txns))

    txnsPerMonth, monthlySpendingTotals = _accumulateMonthlyTotals(txns)

    console.print()
    console.print(_monthlySummaryTable(txnsPerMonth, monthlySpendingTotals))


@cli.command("list_all_debit_txns")
@click.option(
    "--from-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or before this date (YYYY-MM-DD).",
)
@click.option(
    "--txn-type",
    # The Choice is built from the enum *values*: click 8.4.2's Choice does not
    # accept the StrEnum class itself for the documented lowercase inputs
    # (click.Choice(DebitTxnType).convert("atm_withdrawal") raises BadParameter).
    type=click.Choice([txnType.value for txnType in DebitTxnType]),
    default=None,
    help="Only include debit transactions of this type (default: all types).",
)
def list_all_debit_txns(from_date, to_date, txn_type):
    """List every Meezan account debit — card purchases, ATM withdrawals, bill
    payments and funds transfers.
    """
    txns = _filterTxnsByDateRange(smsParser.debitTxns, from_date, to_date)
    if txn_type is not None:
        # DebitTxnType is a StrEnum, so it compares equal to the Choice string
        txns = [txn for txn in txns if txn.txnType == txn_type]

    printRule("Account debit transactions")

    if not txns:
        printEmptyState("No account debit transactions match this filter.")
        return

    printNotice(
        f"Found {len(txns):,} debit transactions"
        f"{_txnTypeLabel(txn_type)}{_dateRangeLabel(from_date, to_date)}:"
    )
    console.print(_debitTxnsTable(txns))


@cli.command("monthly_debit_spending_summary")
@click.option(
    "--from-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=[DATE_RANGE_FMT]),
    default=None,
    help="Only include transactions on or before this date (YYYY-MM-DD).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
def monthly_debit_spending_summary(from_date, to_date, verbose):
    """Summarize Meezan account debit spending month by month, one column per
    currency.
    """
    txns = _filterTxnsByDateRange(smsParser.debitTxns, from_date, to_date)

    printRule("Month-wise debit spending")

    if not txns:
        printEmptyState("No account debit transactions match this filter.")
        return

    printNotice(
        f"Summarizing {len(txns):,} debit transactions"
        f"{_dateRangeLabel(from_date, to_date)}:"
    )

    if verbose:
        console.print(_debitTxnsTable(txns))

    txnsPerMonth, monthlySpendingTotals = _accumulateMonthlyTotals(txns)

    console.print()
    console.print(_monthlySummaryTable(txnsPerMonth, monthlySpendingTotals))


# TODO:
#   - a command that shows a graphical distribution of # of CC txns by month
#   - a command that shows all txns matching a specified vendor
#   - a command that lists the sum (aggregate) total of spending for a specified category

if __name__ == "__main__":
    cli()
