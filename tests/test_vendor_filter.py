"""Tests for `--vendor`, `--canonical-vendors` and `--vendor-map` at the CLI.

The unit tests in `test_vendors.py` prove the lookup table means what it says.
These prove the commands use it the way the documentation promises: that a
search sees both the string the bank sent *and* the canonical name, that
canonicalization changes how spending is grouped and never how much of it there
is, and that a run which never mentions vendors never reads the table at all.
"""

import csv
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from click.testing import CliRunner

from sms_msgs_scraper.sms_txn_query_tool import (
    cc_spend_for_month,
    cli,
    list_all_cc_txns,
    list_all_debit_txns,
    list_all_vendors,
    monthly_cc_spending_summary,
    monthly_debit_spending_summary,
)

# One merchant, four spellings, three banks. The differences are the real ones
# from the reference corpus: a station number, a trailing city, that city's
# case, and the city glued straight on with no space.
HBL_PSO_7 = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at PSO SERVICE STATION 7 for PKR-1,000.00 on 01/Oct/2023."
)
HBL_PSO_25 = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at PSO SERVICE STATION 25 KARACHI PAK for PKR-2,000.00 on 02/Oct/2023."
)
SCB_PSO_GLUED = (
    "Dear Client, PKR 4,000.00 have been paid at PSO SERVICE STATION 7Karachi "
    "PAK on 03-10-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
    "PKR59563.45. SCBPL"
)
# The case for searching by canonical name: nothing in this string contains
# the word a person would search for.
FBL_AMZN = (
    "Dear JOHN DOE, your FBL Card  has been charged for PKR 500.00 on "
    "04-Oct-23 01:17:16 PM at AMZN MKTP US             SEATTLE        US."
)
# An unmapped merchant, to prove canonicalization leaves it alone.
HBL_UNMAPPED = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-3,000.00 on 05/Oct/2023."
)
MEZN_PAYEE_A = (
    "PKR 5,000.00 sent to EXAMPLE PAYEE BAH-01130100000267 from your A/C "
    "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 06-Oct-23 at 10:01 "
    "Bal: PKR 1,000.00"
)
MEZN_PAYEE_B = (
    "PKR 6,000.00 sent to EXAMPLE PAYEE BAH-xxx9988 from your A/C "
    "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 07-Oct-23 at 11:01 "
    "Bal: PKR 1,000.00"
)

# Written against the *packaged* table, which ships worked examples and
# nothing derived from anyone's spending. Proving that table is loaded needs a
# vendor its examples actually claim.
HBL_EXAMPLE_FUEL = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at EXAMPLE FUEL SERVICE STATION 7 for PKR-1,000.00 on 01/Oct/2023."
)
HBL_EXAMPLE_FUEL_GLUED = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at EXAMPLE FUEL SERVICE STATION 7Karachi PAK for PKR-2,000.00 on "
    "02/Oct/2023."
)

TEST_MAP = {
    "schemaVersion": 1,
    "canonicalVendors": {
        "PSO": {"prefix": ["PSO SERVICE STATION"]},
        "AMAZON": {"prefix": ["AMZN MKTP US"]},
        "EXAMPLE PAYEE": {"prefix": ["EXAMPLE PAYEE BAH-"]},
    },
}


class VendorCliTestCase(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner(env={"COLUMNS": "240"})

    def _tmpDir(self) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)

        return Path(tmpDir.name)

    def _sms(self, address, body, readableDate="Oct 2, 2023 9:57:06 PM"):
        sms = ET.Element("sms")
        sms.set("address", address)
        sms.set("body", body)
        sms.set("readable_date", readableDate)

        return sms

    def _backup(self, msgElements) -> Path:
        backupPath = self._tmpDir() / "sms_backup.xml"
        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)
        ET.ElementTree(root).write(backupPath, encoding="utf-8")

        return backupPath

    def _standardBackup(self) -> Path:
        return self._backup(
            [
                self._sms("4250", HBL_PSO_7),
                self._sms("4250", HBL_PSO_25),
                self._sms("7220", SCB_PSO_GLUED),
                self._sms("8756", FBL_AMZN),
                self._sms("4250", HBL_UNMAPPED),
                self._sms("8079", MEZN_PAYEE_A),
                self._sms("8079", MEZN_PAYEE_B),
            ]
        )

    def _packagedExampleBackup(self) -> Path:
        """Two spellings of one merchant the packaged example table claims."""
        return self._backup(
            [
                self._sms("4250", HBL_EXAMPLE_FUEL),
                self._sms("4250", HBL_EXAMPLE_FUEL_GLUED),
            ]
        )

    def _mapFile(self, payload=None) -> Path:
        mapPath = self._tmpDir() / "vendor_aliases.json"
        mapPath.write_text(
            json.dumps(TEST_MAP if payload is None else payload), encoding="utf-8"
        )

        return mapPath

    def run_cli(self, args):
        return self.runner.invoke(cli, args, catch_exceptions=False)

    def rowsFrom(self, result) -> list:
        return list(csv.DictReader(io.StringIO(result.stdout)))

    def csvRun(self, command, extraArgs=(), backupPath=None, mapPath=None):
        """One command's rows, as CSV, with the test alias table in force."""
        backupPath = backupPath or self._standardBackup()
        mapPath = mapPath or self._mapFile()
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "csv",
                "--vendor-map",
                str(mapPath),
                str(backupPath),
                command,
                *extraArgs,
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)

        return self.rowsFrom(result)


class TestVendorOptionRegistration(VendorCliTestCase):
    def test_every_command_accepts_both_vendor_options(self):
        commands = {
            "list_all_vendors": list_all_vendors,
            "list_all_cc_txns": list_all_cc_txns,
            "monthly_cc_spending_summary": monthly_cc_spending_summary,
            "cc_spend_for_month": cc_spend_for_month,
            "list_all_debit_txns": list_all_debit_txns,
            "monthly_debit_spending_summary": monthly_debit_spending_summary,
        }

        for name, command in commands.items():
            names = {param.name for param in command.params}
            with self.subTest(command=name):
                self.assertIn("vendor", names)
                self.assertIn("canonical_vendors", names)


class TestVendorSearch(VendorCliTestCase):
    def test_it_finds_every_spelling_across_every_bank(self):
        rows = self.csvRun("list_all_cc_txns", ["--vendor", "PSO"])

        self.assertEqual(len(rows), 3)
        self.assertEqual({row["bank"] for row in rows}, {"HBL", "SCB"})

    def test_it_is_case_insensitive(self):
        self.assertEqual(
            len(self.csvRun("list_all_cc_txns", ["--vendor", "pso service"])),
            3,
        )

    def test_it_matches_the_canonical_name_the_raw_vendor_never_contains(self):
        """The point of consulting the table during a search.

        Nothing in "AMZN MKTP US SEATTLE US" contains the word Amazon, so
        without the alias table this search returns nothing at all.
        """
        rows = self.csvRun("list_all_cc_txns", ["--vendor", "amazon"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vendor"], "AMZN MKTP US")

    def test_it_still_matches_a_vendor_the_table_says_nothing_about(self):
        rows = self.csvRun("list_all_cc_txns", ["--vendor", "IMTIAZ"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vendor"], "IMTIAZ SUPER MARKET")

    def test_it_composes_with_the_bank_filter(self):
        rows = self.csvRun("list_all_cc_txns", ["--vendor", "PSO", "--bank", "SCB"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bank"], "SCB")

    def test_it_composes_with_the_date_range(self):
        rows = self.csvRun(
            "list_all_cc_txns",
            ["--vendor", "PSO", "--from-date", "2023-10-02", "--to-date", "2023-10-02"],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"][:10], "2023-10-02")

    def test_it_narrows_the_unique_vendor_listing(self):
        rows = self.csvRun("list_all_vendors", ["--vendor", "PSO"])

        self.assertEqual(
            {row["vendor"] for row in rows},
            {
                "PSO SERVICE STATION 7",
                "PSO SERVICE STATION 25 KARACHI PAK",
                "PSO SERVICE STATION 7Karachi PAK",
            },
        )

    def test_it_works_on_account_debits_too(self):
        rows = self.csvRun("list_all_debit_txns", ["--vendor", "example payee"])

        self.assertEqual(len(rows), 2)

    def test_it_narrows_a_monthly_summary(self):
        rows = self.csvRun("monthly_cc_spending_summary", ["--vendor", "PSO"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["month"], "2023-10")
        self.assertEqual(rows[0]["total"], "7000.00")
        self.assertEqual(rows[0]["txns"], "3")

    def test_it_narrows_one_months_total(self):
        rows = self.csvRun(
            "cc_spend_for_month", ["--month", "2023-10", "--vendor", "PSO"]
        )

        self.assertEqual(rows[0]["total"], "7000.00")

    def test_a_search_that_matches_nothing_is_an_ordinary_empty_answer(self):
        backupPath = self._standardBackup()
        result = self.run_cli(
            [
                "--vendor-map",
                str(self._mapFile()),
                str(backupPath),
                "list_all_cc_txns",
                "--vendor",
                "NO SUCH MERCHANT",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No credit card transactions match", result.stderr)

    def test_the_notice_says_what_was_searched_for(self):
        result = self.run_cli(
            [
                "--vendor-map",
                str(self._mapFile()),
                str(self._standardBackup()),
                "list_all_cc_txns",
                "--vendor",
                "PSO",
            ]
        )

        self.assertIn('vendor matching "PSO"', result.stderr)

    def test_an_empty_search_string_is_a_usage_error(self):
        """An empty needle is a substring of everything, so it would silently
        be the same as no filter at all."""
        result = self.run_cli(
            [
                "--vendor-map",
                str(self._mapFile()),
                str(self._standardBackup()),
                "list_all_cc_txns",
                "--vendor",
                "   ",
            ]
        )

        self.assertEqual(result.exit_code, 2)


class TestCanonicalVendors(VendorCliTestCase):
    def test_it_collapses_the_spellings_in_the_vendor_listing(self):
        rows = self.csvRun("list_all_vendors", ["--canonical-vendors"])

        self.assertEqual(
            {row["vendor"] for row in rows},
            {"PSO", "AMAZON", "IMTIAZ SUPER MARKET"},
        )

    def test_it_rewrites_the_vendor_on_each_transaction(self):
        rows = self.csvRun("list_all_cc_txns", ["--canonical-vendors"])

        self.assertEqual(
            [row["vendor"] for row in rows if row["vendor"] == "PSO"].count("PSO"), 3
        )

    def test_a_vendor_the_table_says_nothing_about_is_left_exactly_as_sent(self):
        rows = self.csvRun("list_all_cc_txns", ["--canonical-vendors"])

        self.assertIn("IMTIAZ SUPER MARKET", {row["vendor"] for row in rows})

    def test_it_is_off_by_default(self):
        rows = self.csvRun("list_all_vendors")

        self.assertIn("PSO SERVICE STATION 7", {row["vendor"] for row in rows})
        self.assertNotIn("PSO", {row["vendor"] for row in rows})

    def test_it_changes_how_spending_is_grouped_and_not_how_much_there_is(self):
        """The invariant that matters. Canonicalization is a renaming, and a
        renaming that moved a total would be a bug that looked like a feature.
        """
        plain = self.csvRun("monthly_cc_spending_summary")
        canonical = self.csvRun("monthly_cc_spending_summary", ["--canonical-vendors"])

        self.assertEqual(plain, canonical)

    def test_it_does_not_change_how_many_transactions_there_are(self):
        plain = self.csvRun("list_all_cc_txns")
        canonical = self.csvRun("list_all_cc_txns", ["--canonical-vendors"])

        self.assertEqual(len(plain), len(canonical))
        self.assertEqual(
            sorted(row["amount"] for row in plain),
            sorted(row["amount"] for row in canonical),
        )

    def test_the_listing_is_re_sorted_into_the_documented_order(self):
        """Vendor is a tie-breaker in the output order, so renaming vendors can
        reorder a listing -- and leaving it unsorted would break the contract
        the tool documents.

        Both of these are HBL on the same date, so vendor alone decides. The
        renaming reverses their alphabetical order.
        """
        backupPath = self._backup(
            [
                self._sms(
                    "4250",
                    "Dear Customer, Your HBL CreditCard (ending with 8526) has "
                    "been charged at AAA STORE for PKR-1,000.00 on 01/Oct/2023.",
                ),
                self._sms(
                    "4250",
                    "Dear Customer, Your HBL CreditCard (ending with 8526) has "
                    "been charged at ZZZ SHOP for PKR-2,000.00 on 01/Oct/2023.",
                ),
            ]
        )
        mapPath = self._mapFile(
            {
                "schemaVersion": 1,
                "canonicalVendors": {"AAA CANON": {"exact": ["ZZZ SHOP"]}},
            }
        )

        plain = self.csvRun("list_all_cc_txns", backupPath=backupPath, mapPath=mapPath)
        canonical = self.csvRun(
            "list_all_cc_txns",
            ["--canonical-vendors"],
            backupPath=backupPath,
            mapPath=mapPath,
        )

        self.assertEqual([row["vendor"] for row in plain], ["AAA STORE", "ZZZ SHOP"])
        self.assertEqual([row["vendor"] for row in canonical], ["AAA CANON", "AAA STORE"])

    def test_it_works_on_account_debits_too(self):
        rows = self.csvRun("list_all_debit_txns", ["--canonical-vendors"])

        self.assertEqual({row["vendor"] for row in rows}, {"EXAMPLE PAYEE"})

    def test_it_composes_with_a_vendor_search(self):
        rows = self.csvRun(
            "list_all_cc_txns", ["--vendor", "PSO", "--canonical-vendors"]
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual({row["vendor"] for row in rows}, {"PSO"})

    def test_the_notice_says_the_names_were_canonicalized(self):
        result = self.run_cli(
            [
                "--vendor-map",
                str(self._mapFile()),
                str(self._standardBackup()),
                "list_all_vendors",
                "--canonical-vendors",
            ]
        )

        self.assertIn("canonical vendors", result.stderr)


class TestTheVendorMapOption(VendorCliTestCase):
    def test_the_packaged_table_is_used_when_no_map_is_given(self):
        """No --vendor-map at all: the table inside the package is loaded."""
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "csv",
                str(self._packagedExampleBackup()),
                "list_all_vendors",
                "--canonical-vendors",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            {row["vendor"] for row in self.rowsFrom(result)}, {"EXAMPLE FUEL"}
        )

    def test_the_packaged_table_claims_no_real_merchant(self):
        """It ships the mechanism, not anyone's data.

        Run over real-looking vendors with no --vendor-map, every name comes
        back exactly as sent -- which is the whole reason a real table is a
        private file passed to --vendor-map.
        """
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "csv",
                str(self._standardBackup()),
                "list_all_vendors",
                "--canonical-vendors",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "PSO SERVICE STATION 7", {row["vendor"] for row in self.rowsFrom(result)}
        )

    def test_a_given_map_replaces_the_packaged_one_rather_than_adding_to_it(self):
        """TEST_MAP says nothing about the example merchant.

        If the two tables were merged, the packaged entry would still collapse
        these two spellings. They stay as sent, so the given map replaced it.
        """
        rows = self.csvRun(
            "list_all_vendors",
            ["--canonical-vendors"],
            backupPath=self._packagedExampleBackup(),
        )

        self.assertEqual(
            {row["vendor"] for row in rows},
            {
                "EXAMPLE FUEL SERVICE STATION 7",
                "EXAMPLE FUEL SERVICE STATION 7Karachi PAK",
            },
        )

    def test_a_malformed_map_fails_the_run_rather_than_being_ignored(self):
        mapPath = self._tmpDir() / "broken.json"
        mapPath.write_text("{not json", encoding="utf-8")

        result = self.run_cli(
            [
                "--vendor-map",
                str(mapPath),
                str(self._standardBackup()),
                "list_all_cc_txns",
                "--vendor",
                "PSO",
            ]
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("not valid JSON", result.output)

    def test_a_map_that_does_not_exist_is_a_usage_error(self):
        result = self.run_cli(
            [
                "--vendor-map",
                str(self._tmpDir() / "absent.json"),
                str(self._standardBackup()),
                "list_all_cc_txns",
            ]
        )

        self.assertEqual(result.exit_code, 2)

    def test_the_map_is_not_read_at_all_unless_a_command_asks_about_vendors(self):
        """Deferred the same way the backup is.

        Proved with a map that could not possibly load: the run succeeds, which
        it could only do by never opening the file.
        """
        mapPath = self._tmpDir() / "broken.json"
        mapPath.write_text("{not json", encoding="utf-8")

        result = self.run_cli(
            [
                "--quiet",
                "--vendor-map",
                str(mapPath),
                str(self._standardBackup()),
                "list_all_cc_txns",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)


if __name__ == "__main__":
    unittest.main()
