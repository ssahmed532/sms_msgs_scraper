import pprint
from collections import defaultdict
from pathlib import Path
from pprint import PrettyPrinter
from time import perf_counter

import click

from cc_txn import CreditCardTxnDC
from common import Currency
from sms_backup_file_parser import SmsBackupFileParser

# the global instance of the SMS backup msgs file parser
smsParser = None


@click.group()
@click.version_option("0.2.0", prog_name="hbl_sms_query_tool")
@click.argument(
    "filepath",
    type=click.Path(
        exists=True,
        file_okay=True,
        readable=True,
        path_type=Path,
    ),
)
def cli(filepath):
    # TODO:
    #   1) Properly handle the error case when filepath is a valid directory
    global smsParser

    try:
        smsParser = SmsBackupFileParser()
        time_start = perf_counter()
        click.echo(f"Loading SMS msgs from backup file path: {filepath}")
        smsParser.loadFromSmsBackupFile(filepath)
        msgCount = smsParser.parseMessages()
        time_end = perf_counter()
        print(f"Total elapsed time: {time_end - time_start} seconds")
        print(f"Parsed {msgCount} SMS messages from HBL")
    except PermissionError as e:
        raise click.BadParameter(
            f"ERROR: cannot load SMS messages from backup file: {filepath}"
        )


# The date format accepted by the --from-date / --to-date options
DATE_RANGE_FMT = "%Y-%m-%d"


def _filterTxnsByDateRange(txns: list, fromDate, toDate) -> list:
    """Filter CC txns down to an inclusive [fromDate, toDate] range.

    Both bounds are optional; either may be None to leave that end open.

    Comparison is on the calendar date only. Txn dates are timezone-aware
    while the CLI options are not, so comparing the datetimes directly would
    raise TypeError on the naive/aware mismatch.

    Args:
        txns (list): the CreditCardTxnDC transactions to filter
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
def list_all_vendors(from_date, to_date):
    txns = _filterTxnsByDateRange(smsParser.ccTxns, from_date, to_date)
    vendors = {txn.vendor for txn in txns}

    click.echo("Listing all vendors from CC transactions ...")
    click.echo(
        f"Found {len(vendors)} unique Vendors from parsed HBL SMS messages"
        f"{_dateRangeLabel(from_date, to_date)}:"
    )

    sorted_vendors = sorted(vendors)
    for index, vendor in enumerate(sorted_vendors, start=1):
        click.echo(f"Vendor {index}: [{vendor}]")


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
def list_all_cc_txns(from_date, to_date):
    txns = _filterTxnsByDateRange(smsParser.ccTxns, from_date, to_date)

    click.echo("Listing all CC transactions ...")
    click.echo(
        f"Found {len(txns)} HBL CC transactions from parsed HBL SMS messages"
        f"{_dateRangeLabel(from_date, to_date)}:"
    )
    print()
    for index, txn in enumerate(txns, start=1):
        click.echo(f"{index}: {txn}")


#
# monthlyTotals["2023_12"] -> {"PKR": 0.00, "CAD": 0.00, "USD": 0.00}
#
def _updateMonthlyTotals(txn: CreditCardTxnDC, monthlyTotals: dict) -> None:
    monthKey = txn.date.strftime("%Y_%m")
    currencyKey = txn.amountTuple.currency.lower()

    if monthKey in monthlyTotals:
        monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount
    else:
        monthlyTotals[monthKey] = {
            Currency.CAD.value.lower(): 0.00,
            Currency.PKR.value.lower(): 0.00,
            Currency.USD.value.lower(): 0.00
        }

        monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount


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
def monthly_cc_spending_summary(from_date, to_date):
    txnsPerMonth = defaultdict(int)
    monthlySpendingTotals = {}

    for txn in _filterTxnsByDateRange(smsParser.ccTxns, from_date, to_date):
        # TODO: move the following line to a verbose-enabled check
        print(f"{txn} => {txn.date.strftime("%Y_%m")}")
        monthKey = txn.date.strftime("%Y_%m")
        txnsPerMonth[monthKey] += 1

        _updateMonthlyTotals(txn, monthlySpendingTotals)
        print()

    print()
    print(f"Month-wise CC spending summary{_dateRangeLabel(from_date, to_date)}:")
    pprint.pprint(monthlySpendingTotals, indent=2, width=20, compact=True)


# TODO:
#   - a command that shows a graphical distribution of # of CC txns by month
#   - a command that shows all txns matching a specified vendor
#   - a command that lists the sum (aggregate) total of spending for a specified category

if __name__ == "__main__":
    cli()
