"""Tests for the aggregate-spend-by-currency block under a filtered listing.

A search -- a date range, a vendor needle, or both -- answers "which
transactions?", and the aggregate answers the question that always follows:
"and how much was that?". These tests pin three things: the arithmetic is exact
`Decimal` per currency, the block appears exactly when one of those two filters
is active on a listing command, and the machine formats are untouched -- their
row shapes are a contract a consumer may be parsing.
"""

import csv
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from sms_msgs_scraper.domain.aggregate import totalsByCurrency
from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.types import CardReference
from sms_msgs_scraper.domain.tz import DEFAULT_TZ
from sms_msgs_scraper.sms_txn_query_tool import cli

AGGREGATE_TITLE = "Aggregate spend"

FBL_USD_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for USD 39.99 on "
    "20-Sep-23 01:17:16 PM at AMAZON.COM             SEATTLE        US."
)
MEZN_ATM_BODY = (
    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 from A/C "
    "xxxxxx5602 KARACHI BRANCH on 15-Jun-24 at 09:05 Bal: PKR 1,234.00"
)
MEZN_BILL_BODY = (
    "PKR 4,500.00 is debited as KE BILL PAYMENT from your A/C xxxxxx5602 "
    "of KARACHI BRANCH on 18-Jun-24 at 10:30 TID:12345"
)


def hblBody(vendor: str, amount: str, txnDate: str) -> str:
    """An HBL CC alert, dated DD/Mon/YYYY, in PKR."""
    return (
        f"Dear Customer, Your HBL CreditCard (ending with 8526) has been "
        f"charged at {vendor} for PKR-{amount} on {txnDate}."
    )


class TestTotalsByCurrency(unittest.TestCase):
    def _createTxn(self, amount: str, currency: str = "PKR"):
        return CreditCardTxnDC(
            money=Money(Decimal(amount), currency),
            date=datetime(2024, 6, 15, tzinfo=DEFAULT_TZ),
            vendor="IMTIAZ SUPER MARKET",
            bank="HBL",
            card=CardReference.of("8526"),
        )

    def test_no_txns_total_to_nothing(self):
        self.assertEqual(totalsByCurrency([]), {})

    def test_a_single_currency_sums_exactly(self):
        """25,170.49 + 0.01 lands on 25,170.50 to the cent -- the kind of sum
        binary floating point gets subtly wrong."""
        txns = [self._createTxn("25170.49"), self._createTxn("0.01")]

        totals = totalsByCurrency(txns)

        self.assertEqual(set(totals), {"PKR"})
        self.assertEqual(totals["PKR"], Money(Decimal("25170.50"), "PKR"))

    def test_currencies_are_never_mixed(self):
        txns = [
            self._createTxn("100.00", "PKR"),
            self._createTxn("39.99", "USD"),
            self._createTxn("200.00", "PKR"),
        ]

        totals = totalsByCurrency(txns)

        self.assertEqual(totals["PKR"], Money(Decimal("300.00"), "PKR"))
        self.assertEqual(totals["USD"], Money(Decimal("39.99"), "USD"))

    def test_each_total_carries_its_own_currency(self):
        totals = totalsByCurrency([self._createTxn("50.00", "CAD")])

        self.assertEqual(totals["CAD"].currency, "CAD")


class AggregateCliTestCase(unittest.TestCase):
    """The shared backup-building helpers, following the convention that each
    test file carries its own copy."""

    def setUp(self):
        # A wide terminal so table cells are not wrapped mid-value.
        self.runner = CliRunner(env={"COLUMNS": "200"})

    def _sms(self, address, body, readableDate="Oct 2, 2023 9:57:06 PM"):
        sms = ET.Element("sms")
        sms.set("address", address)
        sms.set("body", body)
        sms.set("readable_date", readableDate)

        return sms

    def _backup(self, msgElements) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupPath = Path(tmpDir.name) / "sms_backup.xml"

        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)
        ET.ElementTree(root).write(backupPath, encoding="utf-8")

        return backupPath

    def _ccBackup(self) -> Path:
        """Two HBL PKR txns a month apart, and one FBL USD txn."""
        return self._backup(
            [
                self._sms("4250", hblBody("IMTIAZ SUPER MARKET", "25,170.49", "01/Oct/2023")),
                self._sms("4250", hblBody("CHASE UP", "0.01", "05/Nov/2023")),
                self._sms("8756", FBL_USD_BODY),
            ]
        )

    def _debitBackup(self) -> Path:
        return self._backup(
            [
                self._sms("8079", MEZN_ATM_BODY),
                self._sms("8079", MEZN_BILL_BODY),
            ]
        )

    def run_cli(self, args):
        return self.runner.invoke(cli, args, catch_exceptions=False)


class TestAggregateAppearsUnderAFilteredListing(AggregateCliTestCase):
    def test_a_cc_date_range_carries_the_aggregate(self):
        result = self.run_cli(
            [
                str(self._ccBackup()),
                "list_all_cc_txns",
                "--from-date",
                "2023-10-01",
                "--to-date",
                "2023-10-31",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        # Only the October txn is in range, so its amount is the PKR total.
        self.assertIn("25,170.49", result.stdout)
        self.assertNotIn("CHASE UP", result.stdout)

    def test_a_cc_vendor_needle_carries_the_aggregate(self):
        result = self.run_cli(
            [str(self._ccBackup()), "list_all_cc_txns", "--vendor", "imtiaz"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        self.assertIn("25,170.49", result.stdout)

    def test_the_aggregate_sums_only_what_matched(self):
        """Both HBL txns are in range, so the PKR total is their exact sum --
        a number that appears nowhere in the listing rows themselves."""
        result = self.run_cli(
            [
                str(self._ccBackup()),
                "list_all_cc_txns",
                "--from-date",
                "2023-10-01",
                "--to-date",
                "2023-11-30",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("25,170.50", result.stdout)

    def test_currencies_stay_separate_in_the_aggregate(self):
        result = self.run_cli(
            [str(self._ccBackup()), "list_all_cc_txns", "--from-date", "2023-01-01"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        self.assertIn("25,170.50", result.stdout)
        self.assertIn("39.99", result.stdout)
        # 25,170.50 PKR + 39.99 USD summed as if money were unitless.
        self.assertNotIn("25,210.49", result.stdout)

    def test_currency_rows_appear_in_the_documented_order(self):
        """PKR before USD, matching the monthly summaries' column order."""
        result = self.run_cli(
            [str(self._ccBackup()), "list_all_cc_txns", "--from-date", "2023-01-01"]
        )

        aggregate = result.stdout[result.stdout.index(AGGREGATE_TITLE) :]
        self.assertLess(aggregate.index("PKR"), aggregate.index("USD"))

    def test_a_debit_vendor_needle_carries_the_aggregate(self):
        result = self.run_cli(
            [str(self._debitBackup()), "list_all_debit_txns", "--vendor", "KE BILL"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        self.assertIn("4,500.00", result.stdout)
        self.assertNotIn("MEEZAN ATM", result.stdout)

    def test_a_debit_date_range_carries_the_aggregate(self):
        result = self.run_cli(
            [
                str(self._debitBackup()),
                "list_all_debit_txns",
                "--from-date",
                "2024-06-01",
                "--to-date",
                "2024-06-30",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        # Both debits are in June, so the total is their exact sum.
        self.assertIn("24,500.00", result.stdout)

    def test_the_filters_compose(self):
        result = self.run_cli(
            [
                str(self._debitBackup()),
                "list_all_debit_txns",
                "--vendor",
                "KE",
                "--from-date",
                "2024-06-01",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(AGGREGATE_TITLE, result.stdout)
        self.assertIn("4,500.00", result.stdout)


class TestAggregateStaysAwayOtherwise(AggregateCliTestCase):
    def test_an_unfiltered_cc_listing_is_unchanged(self):
        result = self.run_cli([str(self._ccBackup()), "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)

    def test_an_unfiltered_debit_listing_is_unchanged(self):
        result = self.run_cli([str(self._debitBackup()), "list_all_debit_txns"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)

    def test_bank_alone_does_not_trigger_it(self):
        """--bank narrows a listing but is not one of the two search filters
        the aggregate is documented to follow."""
        result = self.run_cli(
            [str(self._ccBackup()), "list_all_cc_txns", "--bank", "HBL"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)

    def test_txn_type_alone_does_not_trigger_it(self):
        result = self.run_cli(
            [
                str(self._debitBackup()),
                "list_all_debit_txns",
                "--txn-type",
                "atm_withdrawal",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)

    def test_an_empty_filtered_result_shows_the_empty_state_instead(self):
        result = self.run_cli(
            [
                str(self._ccBackup()),
                "list_all_cc_txns",
                "--from-date",
                "2030-01-01",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)
        # The empty-state panel is commentary rather than a result, so it goes
        # to stderr like the rest of the run's narration.
        self.assertIn("No credit card transactions match this filter", result.stderr)


class TestMachineFormatsAreUntouched(AggregateCliTestCase):
    """JSON and CSV row shapes are a contract; the aggregate must not leak into
    either, filtered or not."""

    def test_filtered_json_keeps_its_shape(self):
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "json",
                str(self._ccBackup()),
                "list_all_cc_txns",
                "--from-date",
                "2023-01-01",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload), {"schemaVersion", "kind", "count", "rows"}
        )
        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["rows"]), 3)

    def test_filtered_csv_carries_rows_and_nothing_else(self):
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "csv",
                str(self._debitBackup()),
                "list_all_debit_txns",
                "--vendor",
                "KE BILL",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], "4500.00")
        self.assertNotIn(AGGREGATE_TITLE, result.stdout)


if __name__ == "__main__":
    unittest.main()
