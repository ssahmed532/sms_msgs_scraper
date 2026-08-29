"""Corpus-level verification CI can actually run.

`scripts/verify_against_backup.py` is the real instrument, but it needs the
reference backup -- one person's actual financial history, which cannot be
committed and must not go near CI. That left the only corpus-level check in the
project runnable on exactly one machine, which is part of how a stable blind
spot stayed certified for so long.

This runs the same shape of check against `fixtures/synthetic_backup.xml`:
entirely invented data, real message templates, every skip path, both short
codes of every bank that has two, and one merchant spelled four ways so
canonicalization has something to collapse. It cannot catch a regression that only shows
up at scale -- that is still the private-corpus job -- but it does catch a
template, a route or an amount rule that has stopped working at all.

Regenerate the fixture with `tests/fixtures/build_synthetic_backup.py`, then
update the expectations here from what it produces.

**This is the one file that pins numbers about the fixture**, and it should
stay that way. `test_adversarial_cli.py` reads the same file but asserts only
that there was something to compare; keeping the counts in one place is what
makes a fixture edit a one-file change.
"""

import unittest
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sms_msgs_scraper.domain.message import SMS_TAG, SmsRecord
from sms_msgs_scraper.domain.vendors import VendorAliasMap
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

        self.assertEqual(envelope.declared, 31)
        self.assertEqual(envelope.actual, 31)
        self.assertTrue(envelope.matchesDeclared)

    def test_records_are_accounted_for_exactly_once(self):
        envelope = self.report.envelope

        self.assertEqual(envelope.sms, 30)
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
        self.assertEqual(self.report.envelope.actual, 31)


class TestRoutingAndConservation(SyntheticCorpusTestCase):
    def test_the_conservation_identity_holds(self):
        self.assertEqual(
            self.report.count("ALL"),
            sum(self.report.count(bucket) for bucket in MSG_BUCKETS),
        )

    def test_the_per_bank_message_counts(self):
        expected = {"HBL": 7, "FBL": 6, "SCB": 6, "MEZN": 8, "OTHER": 1, "DUP": 2}

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
        self.assertEqual(len(self.report.ccTxns), 14)
        self.assertEqual(
            Counter(txn.bank for txn in self.report.ccTxns),
            Counter({"HBL": 6, "FBL": 4, "SCB": 4}),
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
            ("HBL", "PKR"): Decimal("55340.98"),
            ("HBL", "USD"): Decimal("4.02"),
            ("FBL", "PKR"): Decimal("26398.90"),
            ("FBL", "USD"): Decimal("39.99"),
            ("FBL", "CAD"): Decimal("12.50"),
            ("SCB", "PKR"): Decimal("16550.90"),
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


class TestCanonicalVendorsAtCorpusScale(SyntheticCorpusTestCase):
    """One merchant, four spellings, three of the ways a real one varies.

    The fixture spells a single service station four ways across three sender
    codes: with a station number, with a trailing city, with that city glued
    straight on with no space, and truncated mid-word by the issuer. Those are
    the real failure modes from the reference corpus, reproduced in data that
    can be committed.

    The table used here is local to the test rather than the one shipped in
    `data/vendor_aliases.json`. That table was derived from the private corpus
    and asserts against it in `scripts/verify_against_backup.py`; wiring it to
    this fixture as well would couple two corpora that have no merchants in
    common. What is checked here is the *mechanism* at corpus scale.
    """

    # The truncated spelling is the shortest, so it is also the only prefix
    # that can claim all four.
    ALIAS_MAP = {
        "schemaVersion": 1,
        "canonicalVendors": {
            "SYNTHETIC SERVICE STATION": {
                "note": "Four spellings across HBL 4250, HBL 14250, SCB 7220 and SCB 9220.",
                "prefix": ["SYNTHETIC SERVICE STAT"],
            }
        },
    }

    def setUp(self):
        self.aliases = VendorAliasMap.fromDict(self.ALIAS_MAP)

    def _spellings(self):
        return {
            txn.vendor
            for txn in self.report.ccTxns
            if txn.vendor.upper().startswith("SYNTHETIC SERVICE STAT")
        }

    def test_the_fixture_really_does_hold_four_spellings_of_one_merchant(self):
        """Asserted separately, so a fixture edit that removes a spelling fails
        here rather than quietly weakening every test below it."""
        self.assertEqual(
            self._spellings(),
            {
                "SYNTHETIC SERVICE STATION 7",
                "SYNTHETIC SERVICE STATION 7 Karachi PAK",
                "SYNTHETIC SERVICE STATIONKarachi PAK",
                "SYNTHETIC SERVICE STAT",
            },
        )

    def test_all_four_collapse_to_one_canonical_name(self):
        self.assertEqual(
            {self.aliases.canonicalFor(vendor) for vendor in self._spellings()},
            {"SYNTHETIC SERVICE STATION"},
        )

    def test_they_span_more_than_one_bank_and_more_than_one_short_code(self):
        """The grouping is worth having precisely because it crosses issuers:
        no single bank's messages show the problem on their own."""
        banks = {
            txn.bank
            for txn in self.report.ccTxns
            if txn.vendor.upper().startswith("SYNTHETIC SERVICE STAT")
        }

        self.assertEqual(banks, {"HBL", "SCB"})

    def test_canonicalization_leaves_every_other_vendor_untouched(self):
        others = {
            txn.vendor
            for txn in self.report.ccTxns
            if not txn.vendor.upper().startswith("SYNTHETIC SERVICE STAT")
        }

        for vendor in others:
            with self.subTest(vendor=vendor):
                self.assertEqual(self.aliases.canonicalFor(vendor), vendor)

    def test_it_regroups_spending_without_changing_any_of_it(self):
        """The invariant that matters at corpus scale.

        Renaming vendors must move no money. The rewrite is done the way the
        CLI does it -- `replace` on a frozen transaction -- and the totals are
        taken per currency on both sides, so this compares two genuinely
        different objects rather than one value with itself.
        """
        canonical = [
            replace(txn, vendor=self.aliases.canonicalFor(txn.vendor))
            for txn in self.report.ccTxns
        ]

        # the rewrite really did happen: four spellings became one name
        self.assertEqual(len({txn.vendor for txn in self.report.ccTxns}), 14)
        self.assertEqual(len({txn.vendor for txn in canonical}), 11)

        before = Counter()
        for txn in self.report.ccTxns:
            before[txn.money.currency] += txn.money.amount

        after = Counter()
        for txn in canonical:
            after[txn.money.currency] += txn.money.amount

        self.assertEqual(before, after)
        # HBL 55,340.98 + FBL 26,398.90 + SCB 16,550.90
        self.assertEqual(after["PKR"], Decimal("98290.78"))
        self.assertEqual(len(canonical), len(self.report.ccTxns))

    def test_the_shipped_table_says_nothing_about_this_fixture(self):
        """Which is the designed behaviour for any backup it was not derived
        from: an unclaimed vendor passes through exactly as the bank sent it."""
        shipped = VendorAliasMap.loadDefault()

        for txn in self.report.ccTxns:
            with self.subTest(vendor=txn.vendor):
                self.assertEqual(shipped.canonicalFor(txn.vendor), txn.vendor)


class TestReportRoundTrip(SyntheticCorpusTestCase):
    def test_the_whole_report_serialises_and_reads_back(self):
        from sms_msgs_scraper.domain.report import ParseReport

        restored = ParseReport.fromDict(self.report.toDict())

        self.assertEqual(restored.ccTxns, self.report.ccTxns)
        self.assertEqual(restored.debitTxns, self.report.debitTxns)
        self.assertEqual(restored.envelope, self.report.envelope)


if __name__ == "__main__":
    unittest.main()
