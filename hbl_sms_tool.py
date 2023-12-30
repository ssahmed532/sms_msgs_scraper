from collections import namedtuple
import re
import sys
import xml.etree.ElementTree as ET

from hbl_sms_parser import HBLSmsParser


CreditCardTxn = namedtuple(
    "CreditCardTxn", ["txnAmount", "txnDate", "vendor", "ccLastFourDigits"]
)


if __name__ == "__main__":
    smsParser = HBLSmsParser()
    smsParser.loadFromSmsBackupFile(sys.argv[1])
    msgCount = smsParser.parseMessages()
    print(f"Parsed {msgCount} SMS messages from HBL")
    smsParser.printAllVendors()
