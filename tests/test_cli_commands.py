import re
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

from cc_txn import CreditCardTxnDC, CurrencyAmountTuple
from common import DEFAULT_TZ
from sms_txn_query_tool import _filterTxnsByBank, _updateMonthlyTotals, cli


class TestCliCommandRegistration(unittest.TestCase):

    def test_subcommand_names_use_underscores(self):
        """Test method to verify that the documented subcommand names are
        registered verbatim. Click >=8.2 derives command names by
        replacing underscores with dashes, so without the explicit name
        string in @cli.command(...) the documented list_all_vendors
        invocation would silently become list-all-vendors.
        """
        self.assertEqual(
            set(cli.commands),
            {
                "list_all_vendors",
                "list_all_cc_txns",
                "monthly_cc_spending_summary",
                "list_all_debit_txns",
                "monthly_debit_spending_summary",
            },
        )

    def test_cli_version_matches_project_metadata(self):
        """Test method to verify that --version and the packaging metadata
        report the same version. They are declared in two different files
        (@click.version_option in sms_txn_query_tool.py, and [project].version
        in pyproject.toml) with nothing tying them together, so they drift
        apart silently — and then a release identifies itself differently
        depending on who is asking.
        """
        # --version is an eager option, so it prints and exits before the
        # required filepath argument is parsed
        result = CliRunner().invoke(cli, ["--version"])
        self.assertEqual(result.exit_code, 0)

        reported = re.search(r"version\s+(\S+)", result.output)
        self.assertIsNotNone(reported, f"unexpected --version output: {result.output!r}")

        pyprojectPath = Path(__file__).parent.parent / "pyproject.toml"
        projectVersion = tomllib.loads(pyprojectPath.read_text(encoding="utf-8"))[
            "project"
        ]["version"]

        self.assertEqual(reported.group(1), projectVersion)

    def _optionNames(self, commandName: str) -> set:
        return {param.name for param in cli.commands[commandName].params}

    def test_cc_commands_accept_bank_option(self):
        """Test method to verify that --bank is offered by every CC command,
        so that HBL/FBL/SCB txns can be looked at one bank at a time.
        """
        for commandName in (
            "list_all_vendors",
            "list_all_cc_txns",
            "monthly_cc_spending_summary",
        ):
            with self.subTest(command=commandName):
                self.assertIn("bank", self._optionNames(commandName))

    def test_bank_option_choices(self):
        """Test method to verify that --bank offers exactly the banks whose
        CC txns are parsed, case-insensitively.
        """
        bankOption = next(
            param
            for param in cli.commands["list_all_cc_txns"].params
            if param.name == "bank"
        )

        self.assertEqual(list(bankOption.type.choices), ["HBL", "FBL", "SCB"])
        self.assertFalse(bankOption.type.case_sensitive)

    def test_debit_txns_command_accepts_txn_type_option(self):
        """Test method to verify that list_all_debit_txns offers --txn-type,
        and that its choices are the enum *values* (lowercase). click 8.4.2
        rejects the documented lowercase input if the Choice is built from the
        StrEnum class itself.
        """
        options = self._optionNames("list_all_debit_txns")
        self.assertIn("txn_type", options)

        txnTypeOption = next(
            param
            for param in cli.commands["list_all_debit_txns"].params
            if param.name == "txn_type"
        )
        self.assertEqual(
            list(txnTypeOption.type.choices),
            ["card_purchase", "atm_withdrawal", "account_debit", "funds_transfer"],
        )


class TestFilterTxnsByBank(unittest.TestCase):

    def _createTxn(self, bank: str) -> CreditCardTxnDC:
        return CreditCardTxnDC(
            amountTuple=CurrencyAmountTuple("PKR", 100.00),
            date=datetime(2024, 6, 15, tzinfo=DEFAULT_TZ),
            vendor=f"{bank} VENDOR",
            ccLastFourDigits=0,
            bank=bank,
        )

    def setUp(self):
        self.txns = [
            self._createTxn("HBL"),
            self._createTxn("FBL"),
            self._createTxn("SCB"),
            self._createTxn("FBL"),
        ]

    def test_no_bank_returns_everything(self):
        """Test method to verify that an absent --bank leaves the txn list
        untouched (rather than filtering everything out).
        """
        self.assertEqual(_filterTxnsByBank(self.txns, None), self.txns)

    def test_filters_to_the_requested_bank(self):
        """Test method to verify that --bank keeps only that bank's txns."""
        filtered = _filterTxnsByBank(self.txns, "FBL")

        self.assertEqual(len(filtered), 2)
        self.assertEqual({txn.bank for txn in filtered}, {"FBL"})

    def test_bank_matching_nothing(self):
        """Test method to verify that a bank with no txns yields an empty list
        rather than an error.
        """
        parsedTxns = [self._createTxn("HBL")]

        self.assertEqual(_filterTxnsByBank(parsedTxns, "SCB"), [])


class TestUpdateMonthlyTotals(unittest.TestCase):
    """The monthly totals dict pre-seeds pkr/cad/usd only, but the bank parsers
    accept any 3-letter currency code — so a first-seen currency must not raise
    a KeyError on either the new-month or the existing-month path.
    """

    def _createTxn(self, currency: str, isoDate: str) -> CreditCardTxnDC:
        return CreditCardTxnDC(
            amountTuple=CurrencyAmountTuple(currency, 250.00),
            date=datetime.strptime(isoDate, "%Y-%m-%d").replace(tzinfo=DEFAULT_TZ),
            vendor="A EUROPEAN VENDOR",
            ccLastFourDigits=0,
            bank="FBL",
        )

    def test_seeded_currencies_aggregate(self):
        """Test method to verify the ordinary case: two PKR txns in the same
        month add up.
        """
        totals = {}
        _updateMonthlyTotals(self._createTxn("PKR", "2024-06-15"), totals)
        _updateMonthlyTotals(self._createTxn("PKR", "2024-06-20"), totals)

        self.assertEqual(totals["2024_06"]["pkr"], 500.00)

    def test_unseeded_currency_in_existing_month(self):
        """Test method to verify that a first-seen currency landing in an
        already-created month is seeded instead of raising KeyError.
        """
        totals = {}
        _updateMonthlyTotals(self._createTxn("PKR", "2024-06-15"), totals)
        _updateMonthlyTotals(self._createTxn("EUR", "2024-06-16"), totals)

        self.assertEqual(totals["2024_06"]["eur"], 250.00)
        self.assertEqual(totals["2024_06"]["pkr"], 250.00)

    def test_unseeded_currency_opening_a_new_month(self):
        """Test method to verify that a first-seen currency that is also the
        first txn of a new month is seeded — the new-month path needs the seed
        just as much as the existing-month path.
        """
        totals = {}
        _updateMonthlyTotals(self._createTxn("EUR", "2024-07-01"), totals)

        self.assertEqual(totals["2024_07"]["eur"], 250.00)
        # the pre-seeded currencies are still present and untouched
        self.assertEqual(totals["2024_07"]["pkr"], 0.00)


# One msg per shape the commands have to render: two HBL CC txns in different
# months, an FBL txn in a second currency, an SCB txn on a BIN-only card mask
# (so its Card cell is the empty placeholder), a Meezan ATM withdrawal, and a
# msg from a sender nothing parses.
BACKUP_MSGS = [
    (
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023.",
    ),
    (
        "14250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at AGHA SUPER MARKET for PKR-3,120.75 on 02/Feb/2024.",
    ),
    (
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for USD 42.99 on "
        "11-Jan-24 09:05:31 AM at AMAZON.COM            SEATTLE       US.",
    ),
    (
        "7220",
        "Dear Client, PKR 6,780.00 have been paid at METRO CASH CARRYKarachi PAK "
        "on 15-01-24 using Credit Card no 5495. Avail Limit PKR51000.00. SCBPL",
    ),
    (
        "8079",
        "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from A/C "
        "xxxxxx5602 KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 19:42 "
        "Balance: PKR 13,776,380.62",
    ),
    ("JAZZ", "Enjoy 5GB internet for Rs.150. Dial *117*14#"),
]


class TestCliCommandOutput(unittest.TestCase):
    """End-to-end runs of every subcommand over a small synthetic backup.

    These assert on what the commands *render*, which nothing else covers: the
    unit tests above reach the filter helpers directly, and the parser suites
    stop at the txn objects. A themed table that raises a MissingStyle, or a
    filter that matches nothing and prints a header with no rows under it, only
    shows up here.

    The runner pins COLUMNS so that assertions do not depend on the width of
    whatever terminal the suite happens to run in — Rich would otherwise size
    the tables to it and ellipsize the vendor names being asserted on.
    """

    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.backupPath = Path(self.tempDir.name) / "sms-backup.xml"

        root = ET.Element("smses")
        root.set("count", str(len(BACKUP_MSGS)))
        for address, body in BACKUP_MSGS:
            sms = ET.SubElement(root, "sms")
            sms.set("protocol", "0")
            sms.set("address", address)
            sms.set("date", "1695197836791")
            sms.set("type", "1")
            sms.set("body", body)
            sms.set("readable_date", "Sep 20, 2023 1:17:16 PM")
        ET.ElementTree(root).write(self.backupPath, encoding="utf-8")

        self.runner = CliRunner(env={"COLUMNS": "200"})

    def tearDown(self):
        self.tempDir.cleanup()

    def _run(self, *args):
        result = self.runner.invoke(cli, [str(self.backupPath), *args])
        self.assertEqual(
            result.exit_code, 0, f"command failed: {result.output}{result.exception!r}"
        )

        return result.output

    def test_parse_summary_is_rendered(self):
        """Test method to verify that loading a backup reports both summary
        tables, and that the msg counts land in the right buckets.
        """
        output = self._run("list_all_vendors")

        self.assertIn("Messages parsed", output)
        self.assertIn("Transactions extracted", output)
        self.assertIn("Parsed 6 SMS messages", output)

    def test_output_is_plain_text_when_not_a_terminal(self):
        """Test method to verify that nothing emits ANSI escapes into a pipe.

        Rich decides this from the stream, so a console accidentally built with
        force_terminal (or a stray print of an already-rendered string) would
        put escape codes into a redirected run.
        """
        output = self._run("list_all_cc_txns")

        self.assertNotIn("\x1b[", output)

    def test_list_all_cc_txns_renders_every_bank(self):
        """Test method to verify that the CC listing shows all three banks'
        txns, with the vendor, currency and grouped amount of each.
        """
        output = self._run("list_all_cc_txns")

        self.assertIn("Credit card transactions", output)
        self.assertIn("Found 4 CC transactions", output)
        for vendor in ("IMTIAZ SUPER MARKET", "AMAZON.COM", "METRO CASH CARRY"):
            self.assertIn(vendor, output)
        # thousands-grouped to 2dp, and the second currency kept apart
        self.assertIn("25,170.49", output)
        self.assertIn("42.99", output)
        # the caption breaks the total down by bank
        self.assertIn("HBL 2", output)

    def test_list_all_cc_txns_honours_the_bank_filter(self):
        """Test method to verify that --bank narrows the rendered table, not
        just the count in the header.
        """
        output = self._run("list_all_cc_txns", "--bank", "fbl")

        self.assertIn("Found 1 CC transactions (bank FBL)", output)
        self.assertIn("AMAZON.COM", output)
        self.assertNotIn("IMTIAZ SUPER MARKET", output)

    def test_list_all_vendors_renders_sorted_unique_vendors(self):
        """Test method to verify that the vendor listing is deduplicated and
        alphabetical.
        """
        output = self._run("list_all_vendors", "--bank", "HBL")

        self.assertIn("Found 2 unique vendors (bank HBL)", output)
        self.assertLess(
            output.index("AGHA SUPER MARKET"), output.index("IMTIAZ SUPER MARKET")
        )

    def test_monthly_cc_summary_renders_months_and_totals(self):
        """Test method to verify the monthly table: a row per month, a column
        per currency actually spent, and a grand total footer.
        """
        output = self._run("monthly_cc_spending_summary")

        self.assertIn("2023-10", output)
        self.assertIn("2024-01", output)
        self.assertIn("PKR", output)
        self.assertIn("USD", output)
        # nothing was spent in CAD, so it gets no column at all
        self.assertNotIn("CAD", output)
        self.assertIn("TOTAL", output)
        self.assertIn("35,071.24", output)

    def test_monthly_cc_summary_verbose_also_lists_the_txns(self):
        """Test method to verify that --verbose adds the txn listing the
        summary was built from, which is otherwise not shown.
        """
        quiet = self._run("monthly_cc_spending_summary")
        verbose = self._run("monthly_cc_spending_summary", "--verbose")

        self.assertNotIn("IMTIAZ SUPER MARKET", quiet)
        self.assertIn("IMTIAZ SUPER MARKET", verbose)
        self.assertIn("TOTAL", verbose)

    def test_list_all_debit_txns_renders_type_and_account(self):
        """Test method to verify that the debit listing carries the txn type
        and the account mask, which the CC listing has no equivalent of.
        """
        output = self._run("list_all_debit_txns", "--txn-type", "atm_withdrawal")

        self.assertIn("Found 1 debit transactions (type atm_withdrawal)", output)
        self.assertIn("atm_withdrawal", output)
        self.assertIn("xxxxxx5602", output)
        self.assertIn("35,000.00", output)

    def test_monthly_debit_summary_renders_totals(self):
        """Test method to verify that the debit summary renders its own monthly
        table rather than reporting on CC txns.
        """
        output = self._run("monthly_debit_spending_summary")

        self.assertIn("Summarizing 1 debit transactions", output)
        self.assertIn("2023-09", output)
        self.assertIn("35,000.00", output)

    def test_a_filter_matching_nothing_says_so(self):
        """Test method to verify that an empty result renders the explicit
        'nothing matched' state.

        A table header with no rows under it reads as a bug in the tool rather
        than as an answer, and every command has to say the same thing.
        """
        commands = (
            ("list_all_cc_txns", "No credit card transactions match this filter."),
            ("list_all_vendors", "No vendors match this filter."),
            (
                "monthly_cc_spending_summary",
                "No credit card transactions match this filter.",
            ),
            (
                "list_all_debit_txns",
                "No account debit transactions match this filter.",
            ),
            (
                "monthly_debit_spending_summary",
                "No account debit transactions match this filter.",
            ),
        )

        for commandName, expected in commands:
            with self.subTest(command=commandName):
                output = self._run(commandName, "--from-date", "2030-01-01")
                self.assertIn(expected, output)

    def test_no_color_flag_is_accepted_before_the_filepath(self):
        """Test method to verify that --no-color is a group option taking
        effect for the whole run.

        It has to be written before FILEPATH: a Click group stops parsing its
        own options at the first positional argument, so the same flag after
        the path is read as the FILEPATH value itself. Both orderings are
        pinned here so that the documented invocation cannot quietly stop
        working.
        """
        accepted = self.runner.invoke(
            cli, ["--no-color", str(self.backupPath), "list_all_vendors"]
        )
        self.assertEqual(accepted.exit_code, 0, accepted.output)

        rejected = self.runner.invoke(
            cli, [str(self.backupPath), "--no-color", "list_all_vendors"]
        )
        self.assertEqual(rejected.exit_code, 2)

    def test_a_directory_is_rejected_as_the_backup_path(self):
        """Test method to verify that a directory is reported as a bad
        FILEPATH argument instead of failing later inside the XML parse.
        """
        result = self.runner.invoke(cli, [self.tempDir.name, "list_all_vendors"])

        self.assertEqual(result.exit_code, 2)


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
