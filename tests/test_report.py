"""Tests for the typed diagnostics and the immutable parse report."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from sms_msgs_scraper.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.common import DEFAULT_TZ
from sms_msgs_scraper.debit_txn import DebitTxnDC, DebitTxnType
from sms_msgs_scraper.domain.diagnostics import ParseDiagnostic, ParseResult, SkipReason
from sms_msgs_scraper.domain.message import SmsRecord
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.report import (
    DuplicatePolicy,
    DuplicateRecord,
    EnvelopeCounts,
    ParseReport,
)
from sms_msgs_scraper.domain.types import CardReference


def aCcTxn(bank="HBL", amount="100.00", vendor="A VENDOR"):
    return CreditCardTxnDC(
        money=Money(Decimal(amount), "PKR"),
        date=datetime(2025, 3, 4, tzinfo=DEFAULT_TZ),
        vendor=vendor,
        bank=bank,
        card=CardReference.of("8526"),
    )


def aDebitTxn(amount="250.00"):
    return DebitTxnDC(
        money=Money(Decimal(amount), "PKR"),
        date=datetime(2025, 3, 5, 19, 42, tzinfo=DEFAULT_TZ),
        vendor="A PAYEE",
        txnType=DebitTxnType.FUNDS_TRANSFER,
        acctMask="xxxxxx5602",
    )


class TestSmsRecord(unittest.TestCase):
    def test_a_record_missing_address_or_body_is_rejected_not_raised_on(self):
        import xml.etree.ElementTree as ET

        noBody = ET.Element("sms", {"address": "4250"})
        noSender = ET.Element("sms", {"body": "hello"})

        self.assertIsNone(SmsRecord.fromElement(noBody, 0))
        self.assertIsNone(SmsRecord.fromElement(noSender, 1))

    def test_a_locator_carries_no_body(self):
        import xml.etree.ElementTree as ET

        element = ET.Element(
            "sms",
            {
                "address": "4250",
                "body": "SECRET VENDOR PKR-999.00",
                "readable_date": "Oct 2, 2023 9:57:06 PM",
            },
        )

        record = SmsRecord.fromElement(element, 7)

        self.assertNotIn("SECRET", record.locator())
        self.assertIn("4250", record.locator())
        self.assertIn("Oct 2, 2023", record.locator())


class TestParseDiagnostic(unittest.TestCase):
    def test_a_diagnostic_never_carries_the_message_body(self):
        """Structural, not advisory: there is no field a body would fit in."""
        fields = set(ParseDiagnostic.__dataclass_fields__)

        self.assertEqual(
            fields, {"bank", "reason", "sender", "receivedAt", "detail"}
        )

    def test_the_rendered_line_identifies_the_message_without_quoting_it(self):
        import xml.etree.ElementTree as ET

        element = ET.Element(
            "sms",
            {
                "address": "7220",
                "body": "Dear Client, PKR 1.00 have been paid at PRIVATE VENDOR",
                "readable_date": "Sep 7, 2025 1:02:03 PM",
            },
        )
        record = SmsRecord.fromElement(element, 3)

        diagnostic = ParseDiagnostic.forRecord(
            "SCB", record, SkipReason.NO_TEMPLATE_MATCH, "truncated body"
        )

        line = diagnostic.message()
        self.assertNotIn("PRIVATE VENDOR", line)
        self.assertIn("7220", line)
        self.assertIn("no_template_match", line)

    def test_a_result_carries_either_a_txn_or_a_reason(self):
        okResult = ParseResult.ok(aCcTxn())
        failedResult = ParseResult.failed(
            ParseDiagnostic("FBL", SkipReason.BAD_DATE, "8756", "?")
        )

        self.assertTrue(okResult.succeeded)
        self.assertIsNone(okResult.diagnostic)
        self.assertFalse(failedResult.succeeded)
        self.assertEqual(failedResult.diagnostic.reason, SkipReason.BAD_DATE)


class TestParseReport(unittest.TestCase):
    def _report(self):
        return ParseReport(
            envelope=EnvelopeCounts(declared=6, actual=6, sms=5, mms=1, invalid=0),
            counts=MappingProxyType({"ALL": 5, "HBL": 2, "SCB": 1, "DUP": 1, "OTHER": 1}),
            ccTxns=(aCcTxn(), aCcTxn(bank="SCB", vendor="OTHER VENDOR")),
            debitTxns=(aDebitTxn(),),
            diagnostics=(
                ParseDiagnostic("SCB", SkipReason.NO_TEMPLATE_MATCH, "7220", "?"),
            ),
            duplicates=(
                DuplicateRecord(
                    sender="4250", firstIndex=1, duplicateIndex=4, ambiguous=True
                ),
            ),
            duplicatePolicy=DuplicatePolicy.EXACT,
        )

    def test_a_report_serialises_and_reads_back_identically(self):
        report = self._report()

        restored = ParseReport.fromDict(report.toDict())

        self.assertEqual(restored, report)
        self.assertEqual(restored.ccTxns[0].money, report.ccTxns[0].money)
        self.assertEqual(restored.debitTxns[0].date, report.debitTxns[0].date)

    def test_serialised_amounts_stay_exact_strings_not_floats(self):
        report = ParseReport(
            envelope=EnvelopeCounts(1, 1, 1, 0, 0),
            ccTxns=(aCcTxn(amount="0.10"),),
        )

        payload = report.toDict()

        self.assertEqual(payload["ccTxns"][0]["amount"], "0.10")
        self.assertIsInstance(payload["ccTxns"][0]["amount"], str)

    def test_a_report_written_by_another_schema_version_is_refused(self):
        payload = self._report().toDict()
        payload["schemaVersion"] = 99

        with self.assertRaises(ValueError):
            ParseReport.fromDict(payload)

    def test_ambiguous_duplicates_are_counted_separately(self):
        report = self._report()

        self.assertEqual(len(report.duplicates), 1)
        self.assertEqual(report.ambiguousDuplicates, 1)

    def test_the_envelope_keeps_declared_and_actual_apart(self):
        envelope = EnvelopeCounts(declared=4719, actual=4719, sms=4665, mms=54, invalid=0)

        self.assertTrue(envelope.matchesDeclared)
        self.assertNotEqual(envelope.declared, envelope.sms)

    def test_a_report_is_immutable(self):
        report = self._report()

        with self.assertRaises(FrozenInstanceError):
            report.ccTxns = ()

    def test_counts_are_read_through_a_helper_that_defaults_to_zero(self):
        report = self._report()

        self.assertEqual(report.count("HBL"), 2)
        self.assertEqual(report.count("MEZN"), 0)


if __name__ == "__main__":
    unittest.main()
