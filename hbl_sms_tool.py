import sys
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter

from cc_txn import CreditCardTxnDC
from hbl_sms_parser import HBLSmsParser


class Command(StrEnum):
    LIST_ALL_VENDORS = "--list-all-vendors"
    PRINT_CC_TXNS = "--print-cc-txns"
    SUM_ALL_CC_TXNS = "--sum-all-cc-txns"


USAGE_MSG = "Usage: python hbl_sms_tool.py </path/to/SMS backup file.xml> [--list-all-vendors | --print-cc-txns | --sum-all-cc-txns]"

ALL_COMMANDS = [cmd.value for cmd in Command]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(USAGE_MSG, file=sys.stderr)
        sys.exit(1)

    smsBackupFilepath = sys.argv[1]

    if sys.argv[2] not in ALL_COMMANDS:
        print(USAGE_MSG, file=sys.stderr)
        sys.exit(1)
    command = sys.argv[2]

    smsParser = HBLSmsParser()
    time_start = perf_counter()
    smsParser.loadFromSmsBackupFile(sys.argv[1])
    msgCount = smsParser.parseMessages()
    time_end = perf_counter()
    print(f"Total elapsed time: {time_end - time_start} seconds")
    print(f"Parsed {msgCount} SMS messages from HBL")
    if command == Command.LIST_ALL_VENDORS:
        smsParser.printAllVendors()
    elif command == Command.PRINT_CC_TXNS:
        smsParser.printCCTxns()
    elif command == Command.SUM_ALL_CC_TXNS:
        # Make sure to install the tzdata package:
        #   pip install tzdata
        #
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
    else:
        print("Ooops!", file=sys.stderr)
        sys.exit(2)
