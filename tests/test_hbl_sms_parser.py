import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sms_msgs_scraper.common import DEFAULT_TZ
from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.parser.hbl_sms_parser import HBLSmsParser

TXN_BODY = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
)

OTP_BODY = (
    "648975 is your One Time Password (OTP) for the internet transaction on "
    "HBL Card ending with 0077. This OTP is valid for 10 mins. Do not share "
    "OTP with anyone."
)


def record(body: str, sender: str = "4250") -> SmsRecord:
    return SmsRecord(
        sender=sender, body=body, receivedAt="Oct 2, 2023 9:57:06 PM", index=0
    )


class TestHBLTxnSignal(unittest.TestCase):
    def test_a_charge_msg_carries_the_txn_signal(self):
        self.assertTrue(HBLSmsParser.isTxnMsg(record(TXN_BODY)))

    def test_an_otp_msg_does_not(self):
        self.assertFalse(HBLSmsParser.isTxnMsg(record(OTP_BODY)))


class TestHBLExtraction(unittest.TestCase):
    def test_every_field_is_extracted(self):
        """Including the trailing period stripped off the date before parsing."""
        result = HBLSmsParser.extract(record(TXN_BODY))

        self.assertTrue(result.succeeded)
        txn = result.txn
        self.assertEqual(txn.vendor, "IMTIAZ SUPER MARKET")
        self.assertEqual(txn.card.lastFour, "8526")
        self.assertEqual(txn.money, Money(Decimal("25170.49"), "PKR"))
        self.assertEqual(txn.date, datetime(2023, 10, 1, tzinfo=DEFAULT_TZ))
        self.assertEqual(txn.bank, "HBL")

    def test_a_non_txn_body_yields_a_diagnostic_not_a_partial_txn(self):
        result = HBLSmsParser.extract(record(OTP_BODY))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.txn)
        self.assertEqual(result.diagnostic.reason, SkipReason.NO_TEMPLATE_MATCH)

    def test_amount_formats_seen_in_the_corpus(self):
        cases = [
            ("PKR-25,170.49", Money(Decimal("25170.49"), "PKR")),
            ("PKR-450.00", Money(Decimal("450.00"), "PKR")),
            ("PKR-1,000", Money(Decimal("1000"), "PKR")),
            ("USD-4.02", Money(Decimal("4.02"), "USD")),
            ("CAD-1,234.56", Money(Decimal("1234.56"), "CAD")),
        ]

        for clause, expected in cases:
            with self.subTest(txnAmount=clause):
                body = TXN_BODY.replace("PKR-25,170.49", clause)

                result = HBLSmsParser.extract(record(body))

                self.assertTrue(result.succeeded)
                self.assertEqual(result.txn.money, expected)

    def test_ungrouped_thousands_now_parse(self):
        """This test used to pin the bug rather than the behaviour.

        `PKR-25170.49` -- an amount over 1,000 written without comma grouping --
        failed the old amount pattern, which *required* the commas. The failure
        produced a `(None, -1.2345)` sentinel, and the assertion that followed
        aborted the entire run over one message. Under `python -O`, with the
        assertion stripped, it instead produced a transaction whose amount was
        negative, which would silently reduce a monthly total.

        It is now an ordinary amount.
        """
        body = TXN_BODY.replace("PKR-25,170.49", "PKR-25170.49")

        result = HBLSmsParser.extract(record(body))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.txn.money, Money(Decimal("25170.49"), "PKR"))

    def test_a_malformed_amount_is_skipped_rather_than_repaired(self):
        for clause in ("PKR-1,2,3.00", "PKR-.00", "XYZ~100.00"):
            with self.subTest(txnAmount=clause):
                body = TXN_BODY.replace("PKR-25,170.49", clause)

                result = HBLSmsParser.extract(record(body))

                self.assertFalse(result.succeeded)

    def test_nothing_in_this_parser_raises_on_bad_input(self):
        """The whole point of the de-assertion: no message aborts a run."""
        bodies = [
            TXN_BODY.replace("01/Oct/2023", "31/Feb/2023"),
            TXN_BODY.replace("IMTIAZ SUPER MARKET", ""),
            TXN_BODY.replace("PKR-25,170.49", "PKR-0.00"),
            "",
            "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged at",
        ]

        for body in bodies:
            with self.subTest(body=body[:40]):
                result = HBLSmsParser.extract(record(body))

                self.assertFalse(result.succeeded)
                self.assertIsNotNone(result.diagnostic)

    def test_a_diagnostic_never_carries_the_message_body(self):
        """HBL used to print the complete body of every failure."""
        body = TXN_BODY.replace("IMTIAZ SUPER MARKET", "A VERY PRIVATE VENDOR")
        body = body.replace("on 01/Oct/2023", "on NOT-A-DATE")

        result = HBLSmsParser.extract(record(body))

        self.assertFalse(result.succeeded)
        self.assertNotIn("PRIVATE", result.diagnostic.message())
        self.assertIn("4250", result.diagnostic.message())


class TestHBLDateHandling(unittest.TestCase):
    def test_a_txn_date_is_stamped_with_karachi_tz(self):
        """Stamped, not converted: the wall clock survives and +05:00 is added."""
        txnDate = HBLSmsParser._convertToDateTime("01/Oct/2023")

        self.assertEqual(txnDate, datetime(2023, 10, 1, tzinfo=DEFAULT_TZ))
        self.assertEqual(txnDate.utcoffset(), timedelta(hours=5))
        self.assertEqual(txnDate.replace(tzinfo=None), datetime(2023, 10, 1))

    def test_the_calendar_date_is_never_shifted(self):
        cases = [
            ("01/Jan/2024", (2024, 1, 1)),
            ("31/Dec/2024", (2024, 12, 31)),
            ("29/Feb/2024", (2024, 2, 29)),
        ]

        for strValue, expected in cases:
            with self.subTest(txnDate=strValue):
                txnDate = HBLSmsParser._convertToDateTime(strValue)

                self.assertEqual(
                    (txnDate.year, txnDate.month, txnDate.day), expected
                )
                self.assertEqual((txnDate.hour, txnDate.minute), (0, 0))

    def test_a_malformed_date_yields_none_rather_than_raising(self):
        self.assertIsNone(HBLSmsParser._convertToDateTime("2023-10-01"))

    def test_an_impossible_date_is_reported_as_a_bad_date(self):
        body = TXN_BODY.replace("01/Oct/2023", "31/Feb/2023")

        result = HBLSmsParser.extract(record(body))

        self.assertEqual(result.diagnostic.reason, SkipReason.BAD_DATE)


if __name__ == "__main__":
    unittest.main()
