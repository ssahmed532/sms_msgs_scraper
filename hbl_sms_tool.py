import sys
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from time import perf_counter

import click

from cc_txn import CreditCardTxnDC
from hbl_sms_parser import HBLSmsParser


class Command(StrEnum):
    LIST_ALL_VENDORS = "--list-all-vendors"
    PRINT_CC_TXNS = "--print-cc-txns"
    SUM_ALL_CC_TXNS = "--sum-all-cc-txns"


USAGE_MSG = "Usage: python hbl_sms_tool.py </path/to/SMS backup file.xml> [--list-all-vendors | --print-cc-txns | --sum-all-cc-txns]"

ALL_COMMANDS = [cmd.value for cmd in Command]

smsParser = None


def sumAllTxns(smsParser: HBLSmsParser, month: str) -> None:
    pass


@click.group()
@click.version_option("0.1.0", prog_name="hbl_sms_tool")
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


@cli.command()
def sum_all_cc_txns():
    # Make sure to install the tzdata package:
    #   pip install tzdata
    #

    # TODO:
    #   1) Make this dynamic ie detect the month for each txn automatically.
    #   2) Sum & aggregate spend by different type of currency detected!
    #   3) Parameterize this so that a year & month argument can be
    #      passed in via the CLI (eg "March 2023"), and all the txns for
    #      that month should be summed up.
    dtMonthStart = datetime(2023, 11, 1, 0, 0, tzinfo=CreditCardTxnDC.DEFAULT_TZ)
    dtMonthEnd = (
        datetime(2023, 11, 30, 0, 0, tzinfo=CreditCardTxnDC.DEFAULT_TZ)
        + timedelta(days=1)
        - timedelta(seconds=1)
    )
    print(f"Month start: {dtMonthStart}")
    print(f"Month end:   {dtMonthEnd}")
    sumTotal = 0.00
    for txn in smsParser.cc_txns:
        if txn.date >= dtMonthStart and txn.date <= dtMonthEnd:
            print(txn)
            sumTotal += txn.amountTuple.amount

    print(f"Sum total of all CC transactions for the month = {sumTotal}")


if __name__ == "__main__":
    cli()
