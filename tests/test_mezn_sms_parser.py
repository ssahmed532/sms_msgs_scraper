import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sms_msgs_scraper.domain.debit_txn import DebitTxnType
from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.tz import DEFAULT_TZ
from sms_msgs_scraper.parser.mezn_sms_parser import MeznSmsParser
from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

# Real Meezan debit templates, with personal names replaced by synthetic ones.
# Merchant / ATM / branch names are kept as they appear in the corpus.
CARD_PURCHASE_MSG = (
    "PKR 3,251.00 charged at AL KARAM STUDIO for card used, from A/C "
    "xxxxxx5602 (KHAYABAN-E-SEHAR KHI) on 11-Dec-23 at 14:58 "
    "Balance: 13,171,500.59"
)
ATM_WITHDRAWAL_MSG = (
    "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from A/C "
    "xxxxxx5602 KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 19:42 "
    "Balance: PKR 13,776,380.62"
)
ACCOUNT_DEBIT_MSG = (
    "PKR 1,000.00 is debited as NAYAPAY - Load Wallet from your A/C "
    "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 20-Sep-23 at 09:45 "
    "Bal: PKR 13,775,380.62"
)
FUNDS_TRANSFER_MSG = (
    "PKR 63,100.00 sent to KARACHI GRAMMAR SCHOOL SCB-5504000000015496 "
    "from your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 10:01 "
    "Fee: Rs.63.10 Bal: PKR 13,874,889.12"
)
UPPERCASE_FUNDS_TRANSFER_MSG = (
    "PKR 5,000.00 SENT TO JOHN DOE MBL- 01130100000267 on 19-Sep-23 at 11:36 "
    "Bal: PKR 13,811,380.62"
)

MEZN_SENDER = "8079"


def record(body: str, sender: str = MEZN_SENDER) -> SmsRecord:
    return SmsRecord(
        sender=sender, body=body, receivedAt="Sep 19, 2023 7:42:00 PM", index=0
    )


def extract(body: str):
    return MeznSmsParser.extract(record(body))


class TestMeznTxnSignal(unittest.TestCase):
    def test_every_template_family_trips_the_signal(self):
        bodies = [
            CARD_PURCHASE_MSG,
            ATM_WITHDRAWAL_MSG,
            ACCOUNT_DEBIT_MSG,
            FUNDS_TRANSFER_MSG,
            UPPERCASE_FUNDS_TRANSFER_MSG,
        ]

        for body in bodies:
            with self.subTest(body=body[:40]):
                self.assertTrue(MeznSmsParser.isTxnMsg(record(body)))

    def test_a_credit_reversal_is_not_a_debit(self):
        body = (
            "PKR 250.00 is credited as Rev of Late Pymnt chrg Jun23 SSGC in "
            "your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 14-Nov-23 at "
            "17:43. Bal: PKR 13,312,669.35"
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))

    def test_an_incoming_transfer_is_not_a_debit(self):
        body = (
            "PKR 3,500.00 received from FINCA-xxx2801 to A/C xxx5602 of "
            "KHAYABAN-E-SEHAR KHI on 31-Aug-2025 at 12:44"
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))

    def test_cheque_clearing_is_out_of_scope(self):
        """Deliberately excluded, to avoid double counting against the separate
        "cheque received" notice."""
        body = (
            "PKR 12,000.00 INWARD CLEARING VIA CHEQUE NO: 64181500 at "
            "KHAYABAN-E-SEHAR KHI against A/C xxxxxx5602 on 28-Sep-23 at "
            "11:27 Bal: PKR 13,541,842.12"
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))

    def test_an_estatement_notice_is_not_a_debit(self):
        """It contains the words "sent to"."""
        body = (
            "Dear JOHN DOE, your passcode protected Meezan Bank e-statement "
            "has been sent to your registered email address: "
            "john.doe@example.com"
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))

    def test_a_bill_reminder_is_not_a_debit(self):
        body = (
            "Your KE bill saved as KE upstairs bill for Rs 30,018.00 is "
            "available for payment. Pay now via Meezan Mobile App: "
            "bit.ly/2NzudXf"
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))

    def test_a_card_dispatch_notice_is_not_a_debit(self):
        """Pins the amount-head anchor: it says "sent to" but leads with no
        amount."""
        body = (
            "Dear JOHN DOE, your Meezan Bank Debit Card has been sent to your "
            "registered mailing address."
        )

        self.assertFalse(MeznSmsParser.isTxnMsg(record(body)))


class TestMeznTemplateFamilies(unittest.TestCase):
    def _txn(self, body):
        result = extract(body)
        self.assertTrue(result.succeeded, result.diagnostic)

        return result.txn

    def test_a_card_purchase(self):
        txn = self._txn(CARD_PURCHASE_MSG)

        self.assertEqual(txn.txnType, DebitTxnType.CARD_PURCHASE)
        self.assertEqual(txn.vendor, "AL KARAM STUDIO")
        self.assertEqual(txn.money, Money(Decimal("3251.00"), "PKR"))
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(txn.bank, "MEZN")
        self.assertEqual(txn.date, datetime(2023, 12, 11, 14, 58, tzinfo=DEFAULT_TZ))

    def test_an_atm_withdrawal(self):
        txn = self._txn(ATM_WITHDRAWAL_MSG)

        self.assertEqual(txn.txnType, DebitTxnType.ATM_WITHDRAWAL)
        self.assertEqual(txn.vendor, "KHAYABAN-E-SEHAR KHI")
        self.assertEqual(txn.money, Money(Decimal("35000.00"), "PKR"))
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(txn.date, datetime(2023, 9, 19, 19, 42, tzinfo=DEFAULT_TZ))

    def test_a_bill_payment(self):
        txn = self._txn(ACCOUNT_DEBIT_MSG)

        self.assertEqual(txn.txnType, DebitTxnType.ACCOUNT_DEBIT)
        self.assertEqual(txn.vendor, "NAYAPAY - Load Wallet")
        self.assertEqual(txn.money, Money(Decimal("1000.00"), "PKR"))
        self.assertEqual(txn.date, datetime(2023, 9, 20, 9, 45, tzinfo=DEFAULT_TZ))

    def test_a_funds_transfer_with_a_branch_clause(self):
        txn = self._txn(FUNDS_TRANSFER_MSG)

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "KARACHI GRAMMAR SCHOOL SCB-5504000000015496")
        self.assertEqual(txn.money, Money(Decimal("63100.00"), "PKR"))
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(txn.date, datetime(2023, 9, 19, 10, 1, tzinfo=DEFAULT_TZ))

    def test_an_uppercase_transfer_carries_no_account_clause(self):
        txn = self._txn(UPPERCASE_FUNDS_TRANSFER_MSG)

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "JOHN DOE MBL- 01130100000267")
        self.assertEqual(txn.money, Money(Decimal("5000.00"), "PKR"))
        self.assertEqual(txn.acctMask, "")
        self.assertEqual(txn.date, datetime(2023, 9, 19, 11, 36, tzinfo=DEFAULT_TZ))

    def test_a_transfer_without_a_branch_clause(self):
        txn = self._txn(
            "PKR 18,000.00 sent to JOHN DOE (MBL AC 0113xxxxxx0267) from "
            "your A/C xxxxxx5602 on 16-Nov-23 at 22:15 Bal: PKR 13,114,544.35"
        )

        self.assertEqual(txn.vendor, "JOHN DOE (MBL AC 0113xxxxxx0267)")
        self.assertEqual(txn.acctMask, "xxxxxx5602")

    def test_an_uppercase_transfer_that_does_carry_an_account_clause(self):
        """Why the transfer template matches "sent to" case-insensitively.

        The bank sends this "(MBL AC ...)" payee form with an uppercase "SENT
        TO" but it *does* carry an account clause. A case-sensitive match would
        push it down to the account-less fallback, whose payee capture would run
        to the date -- swallowing the account clause into the payee and leaving
        the mask empty.
        """
        txn = self._txn(
            "PKR 18,000.00 SENT TO JOHN DOE (MBL AC 0113xxxxxx0267) from "
            "your A/C xxxxxx5602 on 16-Nov-23 at 22:15 Bal: PKR 13,114,544.35"
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "JOHN DOE (MBL AC 0113xxxxxx0267)")
        self.assertEqual(txn.acctMask, "xxxxxx5602")

    def test_the_raast_account_spelling(self):
        txn = self._txn(
            "PKR 20,000.00 sent to L.FOODS AC# PK......2413 as RAAST "
            "payment from your AC# xxxxxx5602 of KHAYABAN-E-SEHAR KHI on "
            "03-Oct-23 at 20:16 Bal: PKR 14,446,787.21"
        )

        self.assertEqual(txn.vendor, "L.FOODS AC# PK......2413 as RAAST payment")
        self.assertEqual(txn.acctMask, "xxxxxx5602")


class TestMeznDatesAndWhitespace(unittest.TestCase):
    def _txn(self, body):
        result = extract(body)
        self.assertTrue(result.succeeded, result.diagnostic)

        return result.txn

    def test_a_two_digit_year(self):
        txn = self._txn(
            "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from "
            "A/C xxxxxx5602 KHAYABAN-E-SEHAR KHI on 18-Feb-24 at 10:21 "
            "TID:720564"
        )

        self.assertEqual(txn.date, datetime(2024, 2, 18, 10, 21, tzinfo=DEFAULT_TZ))

    def test_a_four_digit_year(self):
        """Appeared around April 2025."""
        txn = self._txn(
            "PKR 80,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from "
            "A/C xxx5602 KHAYABAN-E-SEHAR KHI on 24-Apr-2025 at 16:14 "
            "TID:026128 UAN 021111331331"
        )

        self.assertEqual(txn.date, datetime(2025, 4, 24, 16, 14, tzinfo=DEFAULT_TZ))
        self.assertEqual(txn.acctMask, "xxx5602")

    def test_a_single_digit_day_and_hour(self):
        txn = self._txn(
            "PKR 5,000.00 sent to JOHN DOE NAYAPAY-PK68NAYA12345032 from "
            "your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 3-Apr-24 at "
            "9:05 Bal: PKR 13,770,375.62"
        )

        self.assertEqual(txn.date, datetime(2024, 4, 3, 9, 5, tzinfo=DEFAULT_TZ))

    def test_internal_whitespace_is_normalized_before_matching(self):
        """Without it the same ATM shows up as two distinct vendors."""
        txn = self._txn(
            "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR  KHI from "
            "A/C xxxxxx5602 KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 19:42 "
            "TID:720564"
        )

        self.assertEqual(txn.vendor, "KHAYABAN-E-SEHAR KHI")

    def test_the_assorted_trailing_junk_does_not_prevent_a_match(self):
        tails = (
            "TID:720564",
            "TID:633644 UAN 021111331331",
            "Fee: Rs.5.00 Bal: PKR 13,770,375.62",
            "Bal: PKR 13,770,375.62",
            ".",
        )

        for tail in tails:
            with self.subTest(tail=tail):
                txn = self._txn(
                    "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR "
                    "KHI from A/C xxxxxx5602 KHAYABAN-E-SEHAR KHI on "
                    f"19-Sep-23 at 19:42 {tail}"
                )

                self.assertEqual(txn.txnType, DebitTxnType.ATM_WITHDRAWAL)
                self.assertEqual(txn.vendor, "KHAYABAN-E-SEHAR KHI")
                self.assertEqual(txn.money.amount, Decimal("35000.00"))


class TestMeznDriftTripwire(unittest.TestCase):
    """The signal is deliberately looser than the templates."""

    def test_a_drifted_template_signals_but_does_not_extract(self):
        """This is the whole reason the signal is independent.

        If the signal were "a template matched", a changed template would be
        counted as an ordinary Meezan message with nothing logged -- the same
        silent gap that hid a re-homed short code.
        """
        body = (
            "PKR 5,000.00 sent to JOHN DOE via SomeNewChannel ref 12345 "
            "dated 19/Sep/2023 20:15"
        )

        self.assertTrue(MeznSmsParser.isTxnMsg(record(body)))
        result = extract(body)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.NO_TEMPLATE_MATCH)

    def test_an_impossible_date_is_skipped_rather_than_raised_on(self):
        body = (
            "PKR 5,000.00 sent to JOHN DOE MBL-01130100000267 from your A/C "
            "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 31-Feb-25 at 10:01 "
            "Bal: PKR 1,000.00"
        )

        self.assertTrue(MeznSmsParser.isTxnMsg(record(body)))
        result = extract(body)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.BAD_DATE)

    def test_malformed_grouping_still_signals_but_is_not_repaired(self):
        """The signal stays permissive so this is reported, not swallowed."""
        body = (
            "PKR 1,2,3.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from A/C "
            "xxxxxx5602 KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 19:42 TID:1"
        )

        self.assertTrue(MeznSmsParser.isTxnMsg(record(body)))
        self.assertFalse(extract(body).succeeded)

    def test_a_diagnostic_never_carries_the_message_body(self):
        """A Meezan body carries the payee, the account mask and the balance."""
        body = (
            "PKR 5,000.00 sent to A VERY PRIVATE PAYEE via SomeNewChannel "
            "ref 12345 dated 19/Sep/2023 20:15"
        )

        line = extract(body).diagnostic.message()

        self.assertNotIn("PRIVATE", line)
        self.assertIn("8079", line)


class TestMeznEndToEnd(unittest.TestCase):
    def _sms(self, body: str, sender: str = MEZN_SENDER) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("address", sender)
        sms.set("body", body)
        sms.set("readable_date", "Sep 19, 2023 7:42:00 PM")

        return sms

    def _parseBackup(self, msgElements: list):
        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)

        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupFilepath = Path(tmpDir.name) / "sms_backup.xml"
        ET.ElementTree(root).write(backupFilepath, encoding="utf-8")

        return SmsBackupFileParser().parse(backupFilepath)

    def test_one_msg_per_family_reaches_the_report(self):
        report = self._parseBackup(
            [
                self._sms(CARD_PURCHASE_MSG),
                self._sms(ATM_WITHDRAWAL_MSG),
                self._sms(ACCOUNT_DEBIT_MSG),
                self._sms(FUNDS_TRANSFER_MSG),
                self._sms(UPPERCASE_FUNDS_TRANSFER_MSG),
            ]
        )

        self.assertEqual(len(report.debitTxns), 5)
        self.assertEqual(
            Counter(txn.txnType for txn in report.debitTxns),
            Counter(
                {
                    DebitTxnType.CARD_PURCHASE: 1,
                    DebitTxnType.ATM_WITHDRAWAL: 1,
                    DebitTxnType.ACCOUNT_DEBIT: 1,
                    DebitTxnType.FUNDS_TRANSFER: 2,
                }
            ),
        )
        self.assertEqual(report.count("MEZN"), 5)
        self.assertEqual(report.count("MEZN_SKIPPED"), 0)
        # debits must not leak into the credit card stores
        self.assertEqual(report.ccTxns, ())
        self.assertEqual(report.allVendors, set())
        self.assertIn("AL KARAM STUDIO", report.debitVendors)

    def test_a_drifted_template_is_counted_as_skipped(self):
        report = self._parseBackup(
            [
                self._sms(
                    "PKR 5,000.00 sent to JOHN DOE via SomeNewChannel ref "
                    "12345 dated 19/Sep/2023 20:15"
                )
            ]
        )

        self.assertEqual(report.debitTxns, ())
        self.assertEqual(report.count("MEZN"), 1)
        self.assertEqual(report.count("MEZN_SKIPPED"), 1)
        self.assertEqual(len(report.diagnostics), 1)

    def test_both_meezan_short_codes_route_to_meezan(self):
        report = self._parseBackup(
            [
                self._sms(ATM_WITHDRAWAL_MSG, "8079"),
                self._sms(CARD_PURCHASE_MSG, "9779"),
            ]
        )

        self.assertEqual(report.count("MEZN"), 2)
        self.assertEqual(report.count("OTHER"), 0)
        self.assertEqual(len(report.debitTxns), 2)


if __name__ == "__main__":
    unittest.main()
