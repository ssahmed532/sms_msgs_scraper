from enum import StrEnum
from time import perf_counter
import sys

from hbl_sms_parser import HBLSmsParser


class Command(StrEnum):
    LIST_ALL_VENDORS = "--list-all-vendors"
    PRINT_CC_TXNS = "--print-cc-txns"


USAGE_MSG = "Usage: python hbl_sms_tool.py </path/to/SMS backup file.xml> [--list-all-vendors | --print-cc-txns]"

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
    else:
        print("Ooops!", file=sys.stderr)
        sys.exit(2)
