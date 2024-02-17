import pprint
from collections import defaultdict
from parser.hbl_sms_parser import HBLSmsParser
from pathlib import Path
from pprint import PrettyPrinter
from time import perf_counter

import click

from cc_txn import CreditCardTxnDC
from common import Currency

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
        smsParser = HBLSmsParser()
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


@cli.command()
def list_all_vendors():
    click.echo("Listing all vendors from CC transactions ...")
    click.echo(
        f"Found {len(smsParser.all_vendors)} unique Vendors from parsed HBL SMS messages:"
    )

    sorted_vendors = sorted(smsParser.all_vendors)
    for index, vendor in enumerate(sorted_vendors, start=1):
        click.echo(f"Vendor {index}: [{vendor}]")


@cli.command()
def list_all_cc_txns():
    click.echo("Listing all CC transactions ...")
    click.echo(
        f"Found {len(smsParser.cc_txns)} HBL CC transactions from parsed HBL SMS messages:"
    )
    print()
    for index, txn in enumerate(smsParser.cc_txns, start=1):
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


@cli.command()
def month_wise_cc_spending_summary():
    txnsPerMonth = defaultdict(int)
    monthlySpendingTotals = {}

    # TODO:
    #   1) Parameterize this so that a year & month argument can be
    #      passed in via the CLI (eg "March 2023"), and all the txns for
    #      that month only should be summed up.
    for txn in smsParser.cc_txns:
        # TODO: move the following line to a verbose-enabled check
        print(f"{txn} => {txn.date.strftime("%Y_%m")}")
        monthKey = txn.date.strftime("%Y_%m")
        txnsPerMonth[monthKey] += 1

        _updateMonthlyTotals(txn, monthlySpendingTotals)
        print()

    print()
    print("Month-wise CC spending summary:")
    pprint.pprint(monthlySpendingTotals, indent=2, width=20, compact=True)


# TODO:
#   - a command that shows a graphical distribution of # of CC txns by month
#   - a command that shows all txns matching a specified vendor
#   - a command that lists the sum (aggregate) total of spending for a specified category

if __name__ == "__main__":
    cli()
