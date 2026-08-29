import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sms_msgs_scraper.common import DEFAULT_TZ
from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.parser.fbl_sms_parser import FBLSmsParser
from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

# A single well-formed FBL CC txn msg body, reused across the tests. The
# cardholder name is synthetic; the merchant names and the fixed-width txn tail
# come from the real corpus, where the vendor field is 22 chars wide and is
# followed by a single separator space -- so the city starts at index 23 of the
# tail (index 24 when the vendor carries a backslash escape, which adds one
# character on top of the field).
FBL_TXN_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for PKR 25170.49 on "
    "20-Sep-23 01:17:16 PM at IMTIAZ SUPER MARKET    KARACHI        PK."
)

FBL_SENDER = "8756"


def txnBody(
    currency: str = "PKR",
    amount: str = "25170.49",
    txnDate: str = "20-Sep-23 01:17:16 PM",
    rest: str = "IMTIAZ SUPER MARKET    KARACHI        PK",
    cardClause: str = "your FBL Card  has",
    tail: str = "",
) -> str:
    return (
        f"Dear JOHN DOE, {cardClause} been charged for {currency} {amount} "
        f"on {txnDate} at {rest}.{tail}"
    )


def record(body: str, sender: str = FBL_SENDER) -> SmsRecord:
    return SmsRecord(
        sender=sender, body=body, receivedAt="Sep 20, 2023 1:17:16 PM", index=0
    )


class TestFBLTxnSignal(unittest.TestCase):
    def test_a_charge_msg_carries_the_txn_signal(self):
        self.assertTrue(FBLSmsParser.isTxnMsg(record(FBL_TXN_BODY)))

    def test_a_payment_received_msg_does_not(self):
        """A real non-txn template on FBL's short code that also says "FBL Card"."""
        body = (
            "Dear JOHN DOE, Payment of PKR 50,000.00 has been received on your "
            "FBL Card. Thank you for banking with Faysal Bank."
        )

        self.assertFalse(FBLSmsParser.isTxnMsg(record(body)))

    def test_a_statement_notice_does_not(self):
        body = (
            "Dear Customer, your FBL Noor Card e-statement for Sep-23 has been "
            "emailed to your registered email address."
        )

        self.assertFalse(FBLSmsParser.isTxnMsg(record(body)))


class TestFBLExtraction(unittest.TestCase):
    def _txn(self, body):
        result = FBLSmsParser.extract(record(body))
        self.assertTrue(result.succeeded, result.diagnostic)

        return result.txn

    def test_twelve_hour_date_format(self):
        txn = self._txn(FBL_TXN_BODY)

        self.assertEqual(txn.money, Money(Decimal("25170.49"), "PKR"))
        self.assertEqual(txn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(
            txn.date, datetime(2023, 9, 20, 13, 17, 16, tzinfo=DEFAULT_TZ)
        )

    def test_twenty_four_hour_numeric_date_format(self):
        txn = self._txn(
            txnBody(
                amount="8100",
                txnDate="04-02-2024 21:11:41",
                rest="K ELECTRIC BILL PAY    KARACHI        PK",
            )
        )

        self.assertEqual(txn.money, Money(Decimal("8100"), "PKR"))
        self.assertEqual(txn.vendor, "K ELECTRIC BILL PAY")
        self.assertEqual(
            txn.date, datetime(2024, 2, 4, 21, 11, 41, tzinfo=DEFAULT_TZ)
        )

    def test_an_amount_with_one_decimal_place(self):
        """FBL never comma-groups, and does not always carry two decimals."""
        txn = self._txn(txnBody(amount="18298.9"))

        self.assertEqual(txn.money.amount, Decimal("18298.9"))

    def test_a_usd_txn_keeps_its_currency(self):
        txn = self._txn(
            txnBody(
                currency="USD",
                amount="39.99",
                rest="AMAZON.COM             SEATTLE        US",
            )
        )

        self.assertEqual(txn.money, Money(Decimal("39.99"), "USD"))
        self.assertEqual(txn.vendor, "AMAZON.COM")

    def test_a_cad_txn_keeps_its_currency(self):
        txn = self._txn(
            txnBody(
                currency="CAD",
                amount="12.50",
                txnDate="15-Mar-24 09:05:00 AM",
                rest="TIM HORTONS 1234       TORONTO       CA",
            )
        )

        self.assertEqual(txn.money.currency, "CAD")
        self.assertEqual(txn.vendor, "TIM HORTONS 1234")

    def test_the_available_limit_tail_variant(self):
        txn = self._txn(txnBody(tail=" Available Limit: 245000.00"))

        self.assertEqual(txn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(txn.money.amount, Decimal("25170.49"))

    def test_the_card_clause_whitespace_variants_all_parse(self):
        """All three spacings found in the corpus."""
        for cardClause in (
            "your FBL Card  has",
            "your  FBL Card has",
            "your FBL Card has",
        ):
            with self.subTest(cardClause=cardClause):
                txn = self._txn(txnBody(cardClause=cardClause))

                self.assertEqual(txn.vendor, "IMTIAZ SUPER MARKET")
                self.assertEqual(txn.money.amount, Decimal("25170.49"))

    def test_no_card_digits_are_recorded_because_the_msg_carries_none(self):
        """Explicitly absent, not a card ending 0000."""
        txn = self._txn(FBL_TXN_BODY)

        self.assertFalse(txn.card.known)
        self.assertIsNone(txn.card.lastFour)

    def test_the_txn_is_tagged_with_its_bank(self):
        self.assertEqual(self._txn(FBL_TXN_BODY).bank, "FBL")

    def test_the_txn_date_is_stamped_with_karachi_tz(self):
        """Stamped, not converted from the host machine's local time."""
        txn = self._txn(FBL_TXN_BODY)

        self.assertEqual(str(txn.date.tzinfo), "Asia/Karachi")
        self.assertEqual(txn.date.hour, 13)
        self.assertEqual(txn.date.day, 20)


class TestFBLVendorColumn(unittest.TestCase):
    def _vendor(self, rest):
        result = FBLSmsParser.extract(record(txnBody(rest=rest)))
        self.assertTrue(result.succeeded, result.diagnostic)

        return result.txn.vendor

    def test_the_common_three_part_split(self):
        self.assertEqual(
            self._vendor("HYPERSTAR JOHAR TOWN   LAHORE        PK"),
            "HYPERSTAR JOHAR TOWN",
        )

    def test_a_vendor_filling_the_whole_column(self):
        """No padding separates it from the city, so the width slice recovers it."""
        self.assertEqual(
            self._vendor("SHUJAAT FILLING STATIO KARACHI        PK"),
            "SHUJAAT FILLING STATIO",
        )

    def test_a_backslash_escaped_vendor_comes_out_unescaped(self):
        self.assertEqual(
            self._vendor("M&M\\'S PHARMACY         KARACHI        PK"),
            "M&M'S PHARMACY",
        )

    def test_an_escaped_vendor_filling_the_column_pins_the_step_order(self):
        """The one shape that proves unescape must happen before the slice.

        The backslash is an extra character beyond the 22-char field, so slicing
        first would chop this vendor's last letter off. Two real corpus messages
        have this shape.
        """
        self.assertEqual(
            self._vendor("AUNTIE MANAVER\\'S DESSE KARACHI        PK"),
            "AUNTIE MANAVER'S DESSE",
        )

    def test_the_split_wins_over_the_column_slice(self):
        """Synthetic on purpose: no corpus body makes the two rules disagree.

        A vendor short enough that its city starts well before index 22 is where
        the split yields the vendor alone while the width slice would swallow the
        city too. This pins the documented rule rather than observed data.
        """
        self.assertEqual(self._vendor("QUICK MART   LAHORE   PK"), "QUICK MART")


class TestFBLSkipPath(unittest.TestCase):
    def test_an_unparseable_body_is_skipped_not_raised_on(self):
        body = "Dear JOHN DOE, your FBL Card has been charged for something odd."

        result = FBLSmsParser.extract(record(body))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.NO_TEMPLATE_MATCH)

    def test_a_date_the_regex_accepts_but_the_calendar_rejects(self):
        result = FBLSmsParser.extract(record(txnBody(txnDate="31-Feb-24 01:17:16 PM")))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.BAD_DATE)

    def test_a_comma_grouped_amount_is_skipped(self):
        """FBL has never sent one, so this is a template change, not an amount.

        It carries the txn signal and fails extraction, which is exactly what
        puts it in FBL_SKIPPED where someone will see it.
        """
        body = txnBody(amount="25,170.49")

        self.assertTrue(FBLSmsParser.isTxnMsg(record(body)))
        self.assertFalse(FBLSmsParser.extract(record(body)).succeeded)

    def test_an_over_scaled_amount_is_rejected_not_rounded(self):
        """Three decimal places in a two-decimal currency.

        Rejected here rather than silently rounded away by `,.2f` at render
        time, which is what made the float representation so hard to notice.
        """
        result = FBLSmsParser.extract(record(txnBody(amount="25170.499")))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.BAD_AMOUNT)

    def test_a_diagnostic_never_carries_the_message_body(self):
        body = txnBody(rest="A PRIVATE VENDOR       KARACHI        PK",
                       txnDate="31-Feb-24 01:17:16 PM")

        result = FBLSmsParser.extract(record(body))

        self.assertNotIn("PRIVATE", result.diagnostic.message())


class TestFBLEndToEnd(unittest.TestCase):
    """FBL messages all the way through the orchestrator."""

    def _sms(self, body: str, sender: str = FBL_SENDER) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("address", sender)
        sms.set("body", body)
        sms.set("readable_date", "Sep 20, 2023 1:17:16 PM")

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

    def test_a_txn_reaches_the_report_and_a_bad_msg_is_counted_not_fatal(self):
        report = self._parseBackup(
            [
                self._sms(FBL_TXN_BODY),
                self._sms(txnBody(amount="25,170.49")),
            ]
        )

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.ccTxns[0].bank, "FBL")
        self.assertIn("IMTIAZ SUPER MARKET", report.allVendors)
        self.assertEqual(report.count("FBL"), 2)
        self.assertEqual(report.count("FBL_SKIPPED"), 1)
        self.assertEqual(report.count("OTHER"), 0)
        self.assertEqual(len(report.diagnostics), 1)

    def test_two_identical_msgs_collapse_into_one_txn(self):
        report = self._parseBackup(
            [self._sms(FBL_TXN_BODY), self._sms(FBL_TXN_BODY)]
        )

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.count("FBL"), 1)
        self.assertEqual(report.count("DUP"), 1)
        self.assertEqual(report.count("ALL"), 2)

    def test_an_fbl_duplicate_is_never_ambiguous(self):
        """FBL alerts carry a timestamp to the second.

        An identical body therefore provably describes the same transaction, so
        collapsing it involves no judgement at all.
        """
        report = self._parseBackup(
            [self._sms(FBL_TXN_BODY), self._sms(FBL_TXN_BODY)]
        )

        self.assertEqual(len(report.duplicates), 1)
        self.assertFalse(report.duplicates[0].ambiguous)
        self.assertEqual(report.ambiguousDuplicates, 0)


if __name__ == "__main__":
    unittest.main()
