import re
import tomllib
import unittest
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

from cc_txn import CreditCardTxnDC, CurrencyAmountTuple
from common import DEFAULT_TZ
from hbl_sms_query_tool import _filterTxnsByBank, _updateMonthlyTotals, cli


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
        (@click.version_option in hbl_sms_query_tool.py, and [project].version
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


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
