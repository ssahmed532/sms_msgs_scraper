"""Tests for the CLI boundary: command wiring, the stdout/stderr contract,
output formats and exit codes.
"""

import csv
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from sms_msgs_scraper.sms_txn_query_tool import (
    EXIT_STRICT_FAILURE,
    cli,
    list_all_cc_txns,
    list_all_debit_txns,
    list_all_vendors,
    monthly_cc_spending_summary,
    monthly_debit_spending_summary,
)

HBL_TXN_BODY = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
)
FBL_TXN_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for USD 39.99 on "
    "20-Sep-23 01:17:16 PM at AMAZON.COM             SEATTLE        US."
)
SCB_TXN_BODY = (
    "Dear Client, PKR 12,450.90 have been paid at PSO SERVICE STATION 7Karachi "
    "PAK on 29-09-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
    "PKR59563.45. SCBPL"
)
SCB_TRUNCATED_BODY = (
    "Dear Client, PKR 281.00 have been paid at NECOS NATURAL STORE "
)
MEZN_ATM_BODY = (
    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 from A/C "
    "xxxxxx5602 KARACHI BRANCH on 15-Jun-24 at 09:05 Bal: PKR 1,234.00"
)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        # A wide terminal so table cells are not wrapped mid-vendor. Rich reads
        # COLUMNS, and at the default 80 a name like "IMTIAZ SUPER MARKET" is
        # split across two rendered lines -- which says nothing about whether
        # the value reached the table.
        self.runner = CliRunner(env={"COLUMNS": "200"})

    def _sms(self, address, body, readableDate="Oct 2, 2023 9:57:06 PM"):
        sms = ET.Element("sms")
        sms.set("address", address)
        sms.set("body", body)
        sms.set("readable_date", readableDate)

        return sms

    def _backup(self, msgElements=None, raw=None) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupPath = Path(tmpDir.name) / "sms_backup.xml"

        if raw is not None:
            backupPath.write_text(raw, encoding="utf-8")

            return backupPath

        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)
        ET.ElementTree(root).write(backupPath, encoding="utf-8")

        return backupPath

    def _standardBackup(self) -> Path:
        return self._backup(
            [
                self._sms("4250", HBL_TXN_BODY),
                self._sms("8756", FBL_TXN_BODY),
                self._sms("7220", SCB_TXN_BODY),
                self._sms("8079", MEZN_ATM_BODY),
            ]
        )

    def run_cli(self, args):
        return self.runner.invoke(cli, args, catch_exceptions=False)


class TestCommandRegistration(CliTestCase):
    def test_the_documented_underscore_names_are_the_real_names(self):
        """Click >= 8.2 derives dashed names from the function name.

        Without the explicit name string, `list_all_vendors` would silently
        become `list-all-vendors` and every documented invocation would break.
        """
        expected = {
            "list_all_vendors": list_all_vendors,
            "list_all_cc_txns": list_all_cc_txns,
            "monthly_cc_spending_summary": monthly_cc_spending_summary,
            "list_all_debit_txns": list_all_debit_txns,
            "monthly_debit_spending_summary": monthly_debit_spending_summary,
        }

        self.assertEqual(set(cli.commands), set(expected))
        for name, command in expected.items():
            with self.subTest(command=name):
                self.assertIs(cli.commands[name], command)

    def test_the_bank_choices_come_from_the_registry(self):
        option = next(
            param
            for param in list_all_cc_txns.params
            if param.name == "bank"
        )

        self.assertEqual(tuple(option.type.choices), ("HBL", "FBL", "SCB"))

    def test_the_txn_type_choices_come_from_the_enum(self):
        option = next(
            param
            for param in list_all_debit_txns.params
            if param.name == "txn_type"
        )

        self.assertEqual(
            set(option.type.choices),
            {"card_purchase", "atm_withdrawal", "account_debit", "funds_transfer"},
        )


class TestHelpCostsNothing(CliTestCase):
    """M2: the group callback used to parse the file before help was resolved."""

    def test_subcommand_help_parses_nothing_and_reports_nothing(self):
        backupPath = self._standardBackup()

        result = self.run_cli([str(backupPath), "list_all_cc_txns", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("List every credit card transaction", result.stdout)
        # nothing about the run: no header, no parse summary, no warnings
        self.assertEqual(result.stderr, "")

    def test_help_works_even_when_the_backup_could_not_be_parsed(self):
        """Proof that the file is never opened: this one is not valid XML."""
        backupPath = self._backup(raw="<smses count='1'><sms")

        result = self.run_cli([str(backupPath), "list_all_cc_txns", "--help"])

        self.assertEqual(result.exit_code, 0)

    def test_group_help_parses_nothing(self):
        backupPath = self._backup(raw="<smses count='1'><sms")

        result = self.run_cli([str(backupPath), "--help"])

        self.assertEqual(result.exit_code, 0)


class TestStreamContract(CliTestCase):
    """Results on stdout; everything about the run on stderr."""

    def test_the_data_is_on_stdout_and_the_furniture_is_on_stderr(self):
        result = self.run_cli([str(self._standardBackup()), "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("IMTIAZ SUPER MARKET", result.stdout)
        self.assertNotIn("sms_txn_query_tool", result.stdout)
        self.assertNotIn("Messages parsed", result.stdout)
        self.assertIn("Messages parsed", result.stderr)
        self.assertIn("Transactions extracted", result.stderr)

    def test_parse_diagnostics_go_to_stderr_not_into_the_data(self):
        backupPath = self._backup(
            [
                self._sms("7220", SCB_TXN_BODY),
                self._sms("7220", SCB_TRUNCATED_BODY),
            ]
        )

        result = self.run_cli([str(backupPath), "list_all_cc_txns"])

        self.assertIn("WARNING", result.stderr)
        self.assertNotIn("WARNING", result.stdout)

    def test_quiet_suppresses_the_furniture_but_not_the_data(self):
        result = self.run_cli(
            ["--quiet", str(self._standardBackup()), "list_all_cc_txns"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("IMTIAZ SUPER MARKET", result.stdout)
        self.assertNotIn("Messages parsed", result.stderr)


class TestOutputFormats(CliTestCase):
    def test_csv_is_pure_data_with_a_header(self):
        result = self.run_cli(
            ["--format", "csv", str(self._standardBackup()), "list_all_cc_txns"]
        )

        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            set(rows[0]),
            {"date", "bank", "card", "vendor", "currency", "amount"},
        )

    def test_json_is_schema_versioned(self):
        result = self.run_cli(
            ["--format", "json", str(self._standardBackup()), "list_all_cc_txns"]
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["kind"], "cc_txns")
        self.assertEqual(payload["count"], 3)

    def test_amounts_are_exact_strings_not_json_numbers(self):
        """A JSON number is a float in almost every consumer."""
        result = self.run_cli(
            ["--format", "json", str(self._standardBackup()), "list_all_cc_txns"]
        )

        amounts = {row["amount"] for row in json.loads(result.stdout)["rows"]}
        for amount in amounts:
            self.assertIsInstance(amount, str)
        self.assertIn("25170.49", amounts)

    def test_all_three_formats_agree_on_the_amounts(self):
        backupPath = self._standardBackup()

        asJson = self.run_cli(
            ["--format", "json", str(backupPath), "list_all_cc_txns"]
        )
        asCsv = self.run_cli(["--format", "csv", str(backupPath), "list_all_cc_txns"])
        asTable = self.run_cli([str(backupPath), "list_all_cc_txns"])

        jsonAmounts = sorted(
            Decimal(row["amount"]) for row in json.loads(asJson.stdout)["rows"]
        )
        csvAmounts = sorted(
            Decimal(row["amount"])
            for row in csv.DictReader(io.StringIO(asCsv.stdout))
        )

        self.assertEqual(jsonAmounts, csvAmounts)
        self.assertIn("25,170.49", asTable.stdout)

    def test_an_empty_machine_result_is_an_ordinary_answer(self):
        """For a program, no rows is data -- not a panel saying so."""
        result = self.run_cli(
            [
                "--format",
                "csv",
                str(self._standardBackup()),
                "list_all_cc_txns",
                "--from-date",
                "2099-01-01",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "date,bank,card,vendor,currency,amount")

    def test_a_formula_leading_vendor_is_defused_in_csv(self):
        """A spreadsheet would otherwise treat this cell as a live formula."""
        body = HBL_TXN_BODY.replace("IMTIAZ SUPER MARKET", "=cmd|calc")
        backupPath = self._backup([self._sms("4250", body)])

        result = self.run_cli(["--format", "csv", str(backupPath), "list_all_cc_txns"])

        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(rows[0]["vendor"], "'=cmd|calc")


class TestFiltersAndTotals(CliTestCase):
    def test_the_bank_filter_is_case_insensitive(self):
        result = self.run_cli(
            ["--format", "csv", str(self._standardBackup()),
             "list_all_cc_txns", "--bank", "fbl"]
        )

        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual([row["bank"] for row in rows], ["FBL"])

    def test_the_txn_type_filter(self):
        result = self.run_cli(
            ["--format", "csv", str(self._standardBackup()),
             "list_all_debit_txns", "--txn-type", "atm_withdrawal"]
        )

        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["txnType"], "atm_withdrawal")

    def test_an_unknown_bank_is_a_usage_error(self):
        result = self.runner.invoke(
            cli, [str(self._standardBackup()), "list_all_cc_txns", "--bank", "XYZ"]
        )

        self.assertEqual(result.exit_code, 2)

    def test_monthly_totals_are_exact_and_split_by_currency(self):
        backupPath = self._backup(
            [
                self._sms("4250", HBL_TXN_BODY),
                self._sms("8756", FBL_TXN_BODY),
            ]
        )

        result = self.run_cli(
            ["--format", "json", str(backupPath), "monthly_cc_spending_summary"]
        )

        rows = {(row["month"], row["currency"]): row["total"]
                for row in json.loads(result.stdout)["rows"]}
        self.assertEqual(rows[("2023-10", "PKR")], "25170.49")
        self.assertEqual(rows[("2023-09", "USD")], "39.99")

    def test_a_hundred_small_txns_sum_exactly(self):
        """The float failure this whole change exists to remove."""
        elements = [
            self._sms(
                "4250",
                HBL_TXN_BODY.replace("PKR-25,170.49", "PKR-0.01").replace(
                    "IMTIAZ SUPER MARKET", f"SHOP {index:03d}"
                ),
            )
            for index in range(100)
        ]

        result = self.run_cli(
            ["--format", "json", str(self._backup(elements)),
             "monthly_cc_spending_summary"]
        )

        rows = json.loads(result.stdout)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]["total"]), Decimal("1.00"))
        self.assertEqual(rows[0]["txns"], 100)

    def test_vendors_are_listed_uniquely_and_sorted(self):
        result = self.run_cli(
            ["--format", "csv", str(self._standardBackup()), "list_all_vendors"]
        )

        vendors = [row["vendor"] for row in csv.DictReader(io.StringIO(result.stdout))]
        self.assertEqual(vendors, sorted(vendors))
        self.assertEqual(len(vendors), len(set(vendors)))


class TestTableRendering(CliTestCase):
    def test_a_filter_matching_nothing_says_so_in_words(self):
        result = self.run_cli(
            [str(self._standardBackup()), "list_all_cc_txns",
             "--from-date", "2099-01-01"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No credit card transactions match", result.stderr)

    def test_verbose_adds_the_underlying_txns_to_a_summary(self):
        backupPath = self._standardBackup()

        plain = self.run_cli([str(backupPath), "monthly_cc_spending_summary"])
        verbose = self.run_cli(
            [str(backupPath), "monthly_cc_spending_summary", "--verbose"]
        )

        self.assertNotIn("IMTIAZ SUPER MARKET", plain.stdout)
        self.assertIn("IMTIAZ SUPER MARKET", verbose.stdout)

    def test_no_color_must_precede_the_filepath(self):
        """A Click group stops parsing its own options at the first positional
        argument, so the flag after the path is read as the path itself."""
        backupPath = self._standardBackup()

        good = self.run_cli(["--no-color", str(backupPath), "list_all_cc_txns"])
        bad = self.runner.invoke(
            cli, [str(backupPath), "--no-color", "list_all_cc_txns"]
        )

        self.assertEqual(good.exit_code, 0)
        self.assertNotEqual(bad.exit_code, 0)

    def test_the_vendor_listing_renders(self):
        """The default format of a shipped command, which the CSV test above
        does not reach.
        """
        result = self.run_cli([str(self._standardBackup()), "list_all_vendors"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("IMTIAZ SUPER MARKET", result.stdout)
        self.assertIn("vendors", result.stdout)

    def test_the_debit_listing_renders(self):
        result = self.run_cli([str(self._standardBackup()), "list_all_debit_txns"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("MEEZAN ATM DHA PHASE 6", result.stdout)

    def test_the_monthly_debit_summary_renders(self):
        result = self.run_cli(
            [str(self._standardBackup()), "monthly_debit_spending_summary"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("2024-06", result.stdout)


class TestExitCodes(CliTestCase):
    def test_a_directory_is_rejected_as_the_filepath(self):
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)

        result = self.runner.invoke(cli, [tmpDir.name, "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 2)

    def test_a_malformed_backup_exits_one(self):
        backupPath = self._backup(raw="<smses count='1'><sms")

        result = self.runner.invoke(cli, [str(backupPath), "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 1)

    def test_a_wrong_root_element_exits_one(self):
        backupPath = self._backup(raw="<contacts count='0'></contacts>")

        result = self.runner.invoke(cli, [str(backupPath), "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 1)

    def test_strict_exits_three_when_something_was_skipped(self):
        backupPath = self._backup(
            [
                self._sms("7220", SCB_TXN_BODY),
                self._sms("7220", SCB_TRUNCATED_BODY),
            ]
        )

        result = self.runner.invoke(
            cli, ["--strict", str(backupPath), "list_all_cc_txns"]
        )

        self.assertEqual(result.exit_code, EXIT_STRICT_FAILURE)

    def test_strict_is_silent_on_a_clean_backup(self):
        result = self.runner.invoke(
            cli, ["--strict", str(self._standardBackup()), "list_all_cc_txns"]
        )

        self.assertEqual(result.exit_code, 0)

    def test_a_successful_run_exits_zero(self):
        result = self.run_cli([str(self._standardBackup()), "list_all_cc_txns"])

        self.assertEqual(result.exit_code, 0)


class TestDuplicatePolicyOption(CliTestCase):
    def test_none_keeps_both_copies_of_a_repeated_msg(self):
        backupPath = self._backup(
            [self._sms("4250", HBL_TXN_BODY), self._sms("4250", HBL_TXN_BODY)]
        )

        collapsed = self.run_cli(
            ["--format", "csv", str(backupPath), "list_all_cc_txns"]
        )
        kept = self.run_cli(
            ["--format", "csv", "--duplicates", "none",
             str(backupPath), "list_all_cc_txns"]
        )

        self.assertEqual(len(list(csv.DictReader(io.StringIO(collapsed.stdout)))), 1)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(kept.stdout)))), 2)

    def test_review_reports_the_suppressions_that_were_judgement_calls(self):
        """`exact` collapses silently; `review` says how many were ambiguous."""
        backupPath = self._backup(
            [self._sms("4250", HBL_TXN_BODY), self._sms("4250", HBL_TXN_BODY)]
        )

        quietly = self.run_cli([str(backupPath), "list_all_cc_txns"])
        reviewed = self.run_cli(
            ["--duplicates", "review", str(backupPath), "list_all_cc_txns"]
        )

        self.assertNotIn("REVIEW", quietly.stderr)
        self.assertIn("REVIEW", reviewed.stderr)
        self.assertIn("1 of 1", reviewed.stderr)
        # the data itself is unchanged -- only the reporting differs
        self.assertEqual(quietly.stdout, reviewed.stdout)

    def test_review_says_so_when_no_suppression_was_ambiguous(self):
        """FBL timestamps its alerts, so an identical body provably repeats."""
        backupPath = self._backup(
            [self._sms("8756", FBL_TXN_BODY), self._sms("8756", FBL_TXN_BODY)]
        )

        result = self.run_cli(
            ["--duplicates", "review", str(backupPath), "list_all_cc_txns"]
        )

        self.assertIn("provably repeats", result.stderr)


if __name__ == "__main__":
    unittest.main()
