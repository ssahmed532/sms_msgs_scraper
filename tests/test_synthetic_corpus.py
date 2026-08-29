"""Corpus-level verification CI can actually run.

`scripts/verify_against_backup.py` is the real instrument, but it needs the
reference backup -- one person's actual financial history, which cannot be
committed and must not go near CI. That left the only corpus-level check in the
project runnable on exactly one machine, which is part of how a stable blind
spot stayed certified for so long.

This runs the same shape of check against `fixtures/synthetic_backup.xml`:
entirely invented data, real message templates, every skip path, both short
codes of every bank that has two. It cannot catch a regression that only shows
up at scale -- that is still the private-corpus job -- but it does catch a
template, a route or an amount rule that has stopped working at all.

Regenerate the fixture with `tests/fixtures/build_synthetic_backup.py`, then
update the expectations here from what it produces.
"""

import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sms_msgs_scraper.domain.message import SMS_TAG, SmsRecord
from sms_msgs_scraper.parser.registry import REGISTRY
from sms_msgs_scraper.sms_backup_file_parser import SmsBackupFileParser

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic_backup.xml"

MSG_BUCKETS = ("HBL", "FBL", "SCB", "MEZN", "OTHER", "DUP")


class SyntheticCorpusTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = SmsBackupFileParser().parse(FIXTURE)


class TestEnvelope(SyntheticCorpusTestCase):
    def test_the_declared_count_matches_what_the_file_holds(self):
        envelope = self.report.envelope

        self.assertEqual(envelope.declared, 28)
        self.assertEqual(envelope.actual, 28)
        self.assertTrue(envelope.matchesDeclared)

    def test_records_are_accounted_for_exactly_once(self):
        envelope = self.report.envelope

        self.assertEqual(envelope.sms, 27)
        self.assertEqual(envelope.mms, 1)
        self.assertEqual(envelope.invalid, 0)
        self.assertEqual(
            envelope.sms + envelope.mms + envelope.invalid, envelope.actual
        )

    def test_nested_mms_children_are_not_counted_as_records(self):
        """The fixture's MMS carries parts and addrs children.

        Counting every end event rather than only top-level ones read the
        reference backup's 4,719 records as 5,040 and called its 321 nested MMS
        children malformed messages.
        """
        self.assertEqual(self.report.envelope.actual, 28)


class TestRoutingAndConservation(SyntheticCorpusTestCase):
    def test_the_conservation_identity_holds(self):
        self.assertEqual(
            self.report.count("ALL"),
            sum(self.report.count(bucket) for bucket in MSG_BUCKETS),
        )

    def test_the_per_bank_message_counts(self):
        expected = {"HBL": 5, "FBL": 6, "SCB": 5, "MEZN": 8, "OTHER": 1, "DUP": 2}

        for bucket, count in expected.items():
            with self.subTest(bucket=bucket):
                self.assertEqual(self.report.count(bucket), count)

    def test_both_short_codes_of_every_two_code_bank_are_routed(self):
        """HBL 4250/14250, SCB 7220/9220, Meezan 8079/9779.

        Each bank's second code contributes at least one transaction in the
        fixture, so a dropped code shows up as a changed count rather than as
        nothing at all.
        """
        for bankId in ("HBL", "SCB", "MEZN"):
            with self.subTest(bank=bankId):
                self.assertEqual(len(REGISTRY.byId(bankId).senderCodes), 2)


class TestDiscovery(SyntheticCorpusTestCase):
    """The check that finds what routing missed, run without the registry's
    sender list."""

    def test_no_unregistered_sender_carries_a_txn_signature(self):
        import xml.etree.ElementTree as ET

        offenders = {}

        with FIXTURE.open("rb") as handle:
            for _event, element in ET.iterparse(handle, events=("end",)):
                if element.tag != SMS_TAG:
                    continue

                record = SmsRecord.fromElement(element, 0)
                if record is not None and not REGISTRY.isRegistered(record.sender):
                    for spec in REGISTRY:
                        if spec.signal(record):
                            offenders[record.sender] = spec.id

                element.clear()

        self.assertEqual(offenders, {})


class TestExtraction(SyntheticCorpusTestCase):
    def test_the_credit_card_txn_counts(self):
        self.assertEqual(len(self.report.ccTxns), 11)
        self.assertEqual(
            Counter(txn.bank for txn in self.report.ccTxns),
            Counter({"HBL": 4, "FBL": 4, "SCB": 3}),
        )

    def test_every_debit_type_is_represented(self):
        self.assertEqual(len(self.report.debitTxns), 6)
        self.assertEqual(
            Counter(str(txn.txnType) for txn in self.report.debitTxns),
            Counter(
                {
                    "card_purchase": 1,
                    "atm_withdrawal": 2,
                    "account_debit": 1,
                    "funds_transfer": 2,
                }
            ),
        )

    def test_every_skip_path_is_exercised_and_counted(self):
        expected = {
            "HBL_SKIPPED": 0,
            "FBL_SKIPPED": 1,
            "SCB_SKIPPED": 2,
            "MEZN_SKIPPED": 1,
        }

        for bucket, count in expected.items():
            with self.subTest(bucket=bucket):
                self.assertEqual(self.report.count(bucket), count)

    def test_every_skip_produced_a_diagnostic(self):
        skipped = sum(
            self.report.count(spec.skippedBucket) for spec in REGISTRY
        )

        self.assertEqual(len(self.report.diagnostics), skipped)

    def test_an_ungrouped_thousands_amount_parses(self):
        """H2: this shape used to abort the whole run."""
        vendors = {txn.vendor for txn in self.report.ccTxns}

        self.assertIn("SYNTHETIC FUEL STOP", vendors)


class TestExactTotals(SyntheticCorpusTestCase):
    """Counts alone cannot catch an amount parsed wrongly."""

    def _totals(self):
        totals = {}

        for txn in list(self.report.ccTxns) + list(self.report.debitTxns):
            key = (txn.bank, txn.money.currency)
            totals[key] = totals.get(key, Decimal(0)) + txn.money.amount

        return totals

    def test_the_exact_per_bank_per_currency_totals(self):
        expected = {
            ("HBL", "PKR"): Decimal("51340.98"),
            ("HBL", "USD"): Decimal("4.02"),
            ("FBL", "PKR"): Decimal("26398.90"),
            ("FBL", "USD"): Decimal("39.99"),
            ("FBL", "CAD"): Decimal("12.50"),
            ("SCB", "PKR"): Decimal("13050.90"),
            ("MEZN", "PKR"): Decimal("187351.00"),
        }

        self.assertEqual(self._totals(), expected)

    def test_every_amount_is_an_exact_decimal(self):
        for txn in list(self.report.ccTxns) + list(self.report.debitTxns):
            with self.subTest(vendor=txn.vendor):
                self.assertIsInstance(txn.money.amount, Decimal)


class TestInvariants(SyntheticCorpusTestCase):
    def test_every_txn_is_structurally_sound(self):
        for txn in list(self.report.ccTxns) + list(self.report.debitTxns):
            with self.subTest(vendor=txn.vendor):
                self.assertTrue(txn.vendor.strip())
                self.assertTrue(txn.money.isPositive)
                self.assertEqual(len(txn.money.currency), 3)
                self.assertEqual(str(txn.date.tzinfo), "Asia/Karachi")

    def test_every_bank_tag_is_registered(self):
        for txn in self.report.ccTxns:
            with self.subTest(bank=txn.bank):
                self.assertIn(txn.bank, REGISTRY.ccBankIds)

    def test_no_diagnostic_carries_anything_from_a_body(self):
        """Every vendor in the fixture is prefixed SYNTHETIC, so a body leaking
        into a diagnostic is easy to detect."""
        for diagnostic in self.report.diagnostics:
            with self.subTest(bank=diagnostic.bank):
                self.assertNotIn("SYNTHETIC", diagnostic.message())
                self.assertNotIn("JOHN DOE", diagnostic.message())


class TestDuplicates(SyntheticCorpusTestCase):
    def test_duplicates_are_recorded_with_provenance(self):
        self.assertEqual(len(self.report.duplicates), 2)
        for duplicate in self.report.duplicates:
            with self.subTest(sender=duplicate.sender):
                self.assertLess(duplicate.firstIndex, duplicate.duplicateIndex)

    def test_only_the_date_only_bank_duplicate_is_ambiguous(self):
        """The HBL repeat is ambiguous; the promo repeat is not."""
        self.assertEqual(self.report.ambiguousDuplicates, 1)

        ambiguous = [dup for dup in self.report.duplicates if dup.ambiguous]
        self.assertEqual(ambiguous[0].sender, "4250")


class TestReportRoundTrip(SyntheticCorpusTestCase):
    def test_the_whole_report_serialises_and_reads_back(self):
        from sms_msgs_scraper.domain.report import ParseReport

        restored = ParseReport.fromDict(self.report.toDict())

        self.assertEqual(restored.ccTxns, self.report.ccTxns)
        self.assertEqual(restored.debitTxns, self.report.debitTxns)
        self.assertEqual(restored.envelope, self.report.envelope)


if __name__ == "__main__":
    unittest.main()
