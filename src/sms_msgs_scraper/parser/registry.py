"""One declaration per bank, and the only place a sender short code appears.

Sender `9220` is why this module exists.

Standard Chartered sends its transaction alerts from two short codes. The tool
knew about one. The other's 18 messages -- PKR 94,294.32, all of which parse
perfectly through the existing SCB extractor -- fell through the routing chain
into `OTHER`, where they were counted as ordinary messages from an unrecognised
sender and never looked at again. Nothing failed. The unit suite was green, the
corpus verifier was green, and every SCB total the tool had ever printed was
short by those transactions.

The bug was not the missing string. It was that there were five places a bank's
identity was written down -- the parser's own short-code constant, the routing
chain, the CLI's `--bank` choices, the summary rows, and the documentation --
and adding a sender to one of them left the other four disagreeing without
saying so. A `BankSpec` is a bank's identity, and everything that needs to know
about banks derives from these objects instead of restating them.

This module lives in `parser/` rather than in `domain/` because binding a bank
to its extractor is what it does: it imports every parser by name. The types it
builds with -- `BankSpec`, `BankRegistry`, `Capability`, `TxnKind` -- are domain
values and live in `domain/bank.py`, so the domain stays free of any dependency
on the parsing layer.
"""

from sms_msgs_scraper.domain.bank import BankRegistry, BankSpec, Capability, TxnKind
from sms_msgs_scraper.parser.fbl_sms_parser import FBLSmsParser
from sms_msgs_scraper.parser.hbl_sms_parser import HBLSmsParser
from sms_msgs_scraper.parser.mezn_sms_parser import MeznSmsParser
from sms_msgs_scraper.parser.scb_sms_parser import SCBSmsParser

BANK_SPECS = (
    BankSpec(
        id="HBL",
        name="Habib Bank Limited",
        # HBL migrated its credit card alerts from 4250 to 14250 in mid-January
        # 2025 -- last 4250 alert 15/Jan/2025, first 14250 alert 18/Jan/2025 --
        # with the message body byte-for-byte unchanged. Both codes stay listed
        # to cover the full history.
        senderCodes=("4250", "14250"),
        txnKind=TxnKind.CREDIT_CARD,
        signal=HBLSmsParser.isTxnMsg,
        extract=HBLSmsParser.extract,
        capabilities=frozenset({Capability.CARD_DIGITS}),
        notes="Alerts carry a date only, so an identical body may be a genuine repeat.",
    ),
    BankSpec(
        id="FBL",
        name="Faysal Bank",
        senderCodes=("8756",),
        txnKind=TxnKind.CREDIT_CARD,
        signal=FBLSmsParser.isTxnMsg,
        extract=FBLSmsParser.extract,
        capabilities=frozenset({Capability.TXN_TIME, Capability.MULTI_CURRENCY}),
        notes="Carries no card digits at all; timestamps to the second.",
    ),
    BankSpec(
        id="SCB",
        name="Standard Chartered",
        # 9220 is the sender this registry was written for. It carries the same
        # message template as 7220 and its messages parse through the same
        # extractor with no warnings; it was simply never declared.
        senderCodes=("7220", "9220"),
        txnKind=TxnKind.CREDIT_CARD,
        signal=SCBSmsParser.isTxnMsg,
        extract=SCBSmsParser.extract,
        capabilities=frozenset({Capability.CARD_DIGITS}),
        notes="Alerts carry a date only, so an identical body may be a genuine repeat.",
    ),
    BankSpec(
        id="MEZN",
        name="Meezan Bank",
        senderCodes=("8079", "9779"),
        txnKind=TxnKind.ACCOUNT_DEBIT,
        signal=MeznSmsParser.isTxnMsg,
        extract=MeznSmsParser.extract,
        capabilities=frozenset({Capability.TXN_TIME, Capability.ACCOUNT_MASK}),
        notes="Account debits: card purchases, ATM withdrawals, bills, transfers.",
    ),
)

REGISTRY = BankRegistry(BANK_SPECS)
