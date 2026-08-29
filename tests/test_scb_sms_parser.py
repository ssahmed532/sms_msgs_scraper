import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sms_msgs_scraper.common import DEFAULT_TZ
from sms_msgs_scraper.domain.diagnostics import SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.registry import REGISTRY
from sms_msgs_scraper.domain.types import CardReference
from sms_msgs_scraper.parser.scb_sms_parser import SCBSmsParser
from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

# A well-formed SCB CC txn msg, matching the validated real format:
#   Dear Client, PKR {amt} have been paid at {vendor}{city} {CTY} on {DD-MM-YY}
#   using Credit Card no {mask}. Avail Limit PKR{x}. SCBPL
SCB_TXN_MSG_TEMPLATE = (
    "Dear Client, PKR {amount} have been paid at {vendor} on {txnDate} "
    "using Credit Card no {cardMask}. Avail Limit PKR59563.45. SCBPL"
)

# The real truncated shape: the body simply ends after (part of) the vendor
# name, before the date and card ever appear.
SCB_TRUNCATED_MSG = "Dear Client, PKR 281.00 have been paid at NECOS NATURAL STORE "

# The real "no amount" shape: a foreign-currency txn whose PKR amount is
# absent, leaving a literal "PKR .00" in an otherwise well-formed body.
SCB_NO_AMOUNT_MSG = (
    "Dear Client, PKR .00 have been paid at PASSPORT/PASSEPORT CAN OTTAWA CAN "
    "on 14-06-24 using Credit Card no 5495. Avail Limit PKR181962.55. SCBPL"
)

SCB_SENDER = "7220"
# The second short code. It carries the identical template, and its messages
# were silently routed to OTHER for the whole life of the tool before it was
# declared in the registry.
SCB_SECOND_SENDER = "9220"


def txnBody(
    vendor: str = "PSO SERVICE STATION 7Karachi PAK",
    amount: str = "12,450.90",
    txnDate: str = "29-09-23",
    cardMask: str = "5452xxxxxxxx1280",
) -> str:
    return SCB_TXN_MSG_TEMPLATE.format(
        amount=amount, vendor=vendor, txnDate=txnDate, cardMask=cardMask
    )


def record(body: str, sender: str = SCB_SENDER) -> SmsRecord:
    return SmsRecord(
        sender=sender, body=body, receivedAt="Sep 29, 2023 6:12:00 PM", index=0
    )


class TestSCBSenders(unittest.TestCase):
    """H1: SCB sends from two short codes, and only one was ever declared."""

    def test_both_scb_short_codes_route_to_scb(self):
        for sender in (SCB_SENDER, SCB_SECOND_SENDER):
            with self.subTest(sender=sender):
                spec = REGISTRY.specFor(sender)

                self.assertIsNotNone(
                    spec, f"sender {sender} is not registered to any bank"
                )
                self.assertEqual(spec.id, "SCB")

    def test_a_msg_from_the_second_sender_parses_identically(self):
        """The template is the same; only the sender differed."""
        fromFirst = SCBSmsParser.extract(record(txnBody(), SCB_SENDER))
        fromSecond = SCBSmsParser.extract(record(txnBody(), SCB_SECOND_SENDER))

        self.assertTrue(fromFirst.succeeded)
        self.assertTrue(fromSecond.succeeded)
        self.assertEqual(fromFirst.txn.money, fromSecond.txn.money)
        self.assertEqual(fromFirst.txn.vendor, fromSecond.txn.vendor)
        self.assertEqual(fromFirst.txn.date, fromSecond.txn.date)


class TestSCBTxnSignal(unittest.TestCase):
    def test_a_txn_msg_carries_the_signal(self):
        self.assertTrue(SCBSmsParser.isTxnMsg(record(txnBody())))

    def test_the_real_non_txn_templates_do_not(self):
        """Both open with the same salutation, and one even carries an amount."""
        nonTxnBodies = [
            "Dear Client, a transaction of PKR 15000.00 has been completed on "
            "Account No. 01-21***71-01 on 05-08-24 using Online Banking.  "
            "Avail. Bal: PKR 32145.67.",
            "Dear Customer, Thank you. Your MASTERCARD STANDARD Card "
            "5452xxxxxxxx1280 payment of Rs 750.00 has been received. For "
            "assistance, please call 111-002-002. Standard Chartered",
        ]

        for body in nonTxnBodies:
            with self.subTest(body=body[:40]):
                self.assertFalse(SCBSmsParser.isTxnMsg(record(body)))

    def test_the_unusable_but_real_txn_shapes_still_register_as_attempts(self):
        """Load-bearing for the skip accounting.

        These must pass the signal and then fail extraction, so they are counted
        as skipped. Tightening the signal -- by also requiring "using Credit Card
        no" -- would drop the truncated messages out of the count entirely
        instead of reporting them.
        """
        for body in (SCB_TRUNCATED_MSG, SCB_NO_AMOUNT_MSG):
            with self.subTest(body=body[:40]):
                self.assertTrue(SCBSmsParser.isTxnMsg(record(body)))


class TestSCBExtraction(unittest.TestCase):
    def _txn(self, body):
        result = SCBSmsParser.extract(record(body))
        self.assertTrue(result.succeeded, result.diagnostic)

        return result.txn

    def test_a_comma_grouped_amount(self):
        txn = self._txn(txnBody(amount="12,450.90"))

        self.assertEqual(txn.money, Money(Decimal("12450.90"), "PKR"))

    def test_a_plain_amount(self):
        txn = self._txn(txnBody(amount="15134.00"))

        self.assertEqual(txn.money, Money(Decimal("15134.00"), "PKR"))

    def test_malformed_grouping_is_rejected_not_repaired(self):
        """"1,2,3.00" used to have its commas stripped and be read as 123.00."""
        result = SCBSmsParser.extract(record(txnBody(amount="1,2,3.00")))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.NO_TEMPLATE_MATCH)

    def test_the_digit_length_is_bounded(self):
        """No input can push the amount toward floating-point infinity."""
        result = SCBSmsParser.extract(record(txnBody(amount="9" * 40 + ".00")))

        self.assertFalse(result.succeeded)

    def test_a_full_card_mask_yields_the_last_four(self):
        txn = self._txn(txnBody(cardMask="5452xxxxxxxx1280"))

        self.assertEqual(txn.card, CardReference.of("1280"))
        self.assertTrue(txn.card.known)

    def test_a_bin_only_mask_carries_no_card_digits(self):
        """Absent, not a card ending 0000 -- a distinction the old int could
        not make."""
        txn = self._txn(txnBody(cardMask="5495"))

        self.assertFalse(txn.card.known)
        self.assertEqual(txn.card, CardReference.absent())

    def test_a_longer_bin_still_yields_the_real_last_four(self):
        """A 6-digit BIN is the industry-standard next step.

        Hard-coding the 4-digit BIN would have recorded these as having no card
        digits while the digits sat in the message.
        """
        for cardMask in ("545221xxxxxx1280", "5452xxxxxxxx1280"):
            with self.subTest(cardMask=cardMask):
                txn = self._txn(txnBody(cardMask=cardMask))

                self.assertEqual(txn.card, CardReference.of("1280"))

    def test_an_unrecognized_mask_keeps_the_txn_and_reports_the_mask(self):
        """The spending is real; only the card field is unusable.

        Discarding the transaction would lose money data over a cosmetic field,
        but silently recording no card is exactly what a change in the bank's
        masking would look like -- so it is reported either way.
        """
        result = SCBSmsParser.extract(record(txnBody(cardMask="5452xxxx12")))

        self.assertTrue(result.succeeded)
        self.assertFalse(result.txn.card.known)
        self.assertEqual(result.txn.money, Money(Decimal("12450.90"), "PKR"))
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(
            result.diagnostic.reason, SkipReason.UNRECOGNIZED_CARD_MASK
        )

    def test_both_known_mask_shapes_report_nothing(self):
        for cardMask in ("5452xxxxxxxx1280", "5495"):
            with self.subTest(cardMask=cardMask):
                result = SCBSmsParser.extract(record(txnBody(cardMask=cardMask)))

                self.assertTrue(result.succeeded)
                self.assertIsNone(result.diagnostic)

    def test_the_txn_date_is_stamped_with_karachi_tz(self):
        """DD-MM-YY read day-first; wall clock survives; +05:00 attached."""
        txn = self._txn(txnBody(txnDate="29-09-23"))

        self.assertEqual(txn.date, datetime(2023, 9, 29, tzinfo=DEFAULT_TZ))
        self.assertEqual(txn.date.utcoffset(), timedelta(hours=5))
        self.assertEqual(txn.date.replace(tzinfo=None), datetime(2023, 9, 29))

    def test_the_vendor_is_kept_verbatim_city_included(self):
        """SCB glues the city on with no separator, so no split is attempted."""
        vendors = [
            "SOUTH CITY HOSPITALKarachi PAK",
            "Amazon.caAMAZON.CA CAN",
            "SHELL (SUNSET BOULEVAR KARACHI PAK",
            # a vendor containing the " on " that separates vendor from date
            "CAFE on THE GO Karachi PAK",
        ]

        for vendor in vendors:
            with self.subTest(vendor=vendor):
                self.assertEqual(self._txn(txnBody(vendor=vendor)).vendor, vendor)

    def test_a_concatenated_body_yields_the_first_txn(self):
        """The vendor capture is deliberately lazy.

        A greedy capture would pair the first amount with the *second* message's
        vendor, date and card -- inventing a transaction that never happened.
        The SMS pipeline demonstrably mangles these messages, as the truncated
        bodies show.
        """
        firstTxn = txnBody(
            amount="100.00", vendor="FIRST SHOP Karachi PAK", txnDate="01-02-24"
        )
        secondTxn = txnBody(
            amount="200.00",
            vendor="SECOND SHOP Lahore PAK",
            txnDate="03-04-24",
            cardMask="5452xxxxxxxx9999",
        )

        txn = self._txn(firstTxn + " " + secondTxn)

        self.assertEqual(txn.money, Money(Decimal("100.00"), "PKR"))
        self.assertEqual(txn.vendor, "FIRST SHOP Karachi PAK")
        self.assertEqual(txn.date, datetime(2024, 2, 1, tzinfo=DEFAULT_TZ))
        self.assertEqual(txn.card, CardReference.of("1280"))


class TestSCBSkipPaths(unittest.TestCase):
    def test_a_truncated_body_is_skipped(self):
        result = SCBSmsParser.extract(record(SCB_TRUNCATED_MSG))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.diagnostic.reason, SkipReason.NO_TEMPLATE_MATCH)

    def test_a_body_carrying_no_amount_is_skipped(self):
        result = SCBSmsParser.extract(record(SCB_NO_AMOUNT_MSG))

        self.assertFalse(result.succeeded)

    def test_a_malformed_date_yields_none_rather_than_raising(self):
        self.assertIsNone(SCBSmsParser._convertToDateTime("2023-10-01"))

    def test_a_diagnostic_never_carries_the_message_body(self):
        """A real run skips 26 of these; that is 26 real messages not dumped."""
        result = SCBSmsParser.extract(
            record("Dear Client, PKR 281.00 have been paid at A PRIVATE VENDOR ")
        )

        line = result.diagnostic.message()
        self.assertNotIn("PRIVATE", line)
        self.assertIn("7220", line)


class TestSCBEndToEnd(unittest.TestCase):
    def _sms(self, body: str, sender: str = SCB_SENDER) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("address", sender)
        sms.set("body", body)
        sms.set("readable_date", "Sep 29, 2023 6:12:00 PM")

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

    def test_a_txn_parses_and_an_unusable_msg_is_counted_as_skipped(self):
        report = self._parseBackup(
            [
                self._sms(txnBody()),
                self._sms(SCB_TRUNCATED_MSG),
                self._sms(SCB_NO_AMOUNT_MSG),
            ]
        )

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.ccTxns[0].bank, "SCB")
        self.assertEqual(report.count("SCB"), 3)
        self.assertEqual(report.count("SCB_SKIPPED"), 2)
        self.assertEqual(report.count("OTHER"), 0)

    def test_a_msg_from_the_second_sender_reaches_the_report(self):
        """The end-to-end form of H1.

        Before `9220` was declared, this message was counted in OTHER and its
        spending never appeared in any total.
        """
        report = self._parseBackup([self._sms(txnBody(), SCB_SECOND_SENDER)])

        self.assertEqual(len(report.ccTxns), 1)
        self.assertEqual(report.ccTxns[0].bank, "SCB")
        self.assertEqual(report.count("SCB"), 1)
        self.assertEqual(report.count("OTHER"), 0)

    def test_a_non_txn_msg_is_counted_but_not_parsed(self):
        body = (
            "Dear Customer, Thank you. Your MASTERCARD STANDARD Card "
            "5452xxxxxxxx1280 payment of Rs 750.00 has been received."
        )

        report = self._parseBackup([self._sms(body)])

        self.assertEqual(len(report.ccTxns), 0)
        self.assertEqual(report.count("SCB"), 1)
        self.assertEqual(report.count("SCB_SKIPPED"), 0)

    def test_an_scb_duplicate_is_flagged_ambiguous(self):
        """SCB alerts carry a date but no time of day.

        A second genuine identical purchase that day is indistinguishable from a
        retransmission, so the collapse is reported rather than assumed correct.
        """
        report = self._parseBackup([self._sms(txnBody()), self._sms(txnBody())])

        self.assertEqual(report.count("DUP"), 1)
        self.assertEqual(report.ambiguousDuplicates, 1)


if __name__ == "__main__":
    unittest.main()
