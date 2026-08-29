"""Tests for the --from-date / --to-date filter shared by every command."""

import unittest
from datetime import datetime
from decimal import Decimal

import click

from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.debit_txn import DebitTxnDC, DebitTxnType
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.types import CardReference
from sms_msgs_scraper.domain.tz import DEFAULT_TZ
from sms_msgs_scraper.sms_txn_query_tool import _filterLabel, _filterTxnsByDateRange


def bound(isoDate: str) -> datetime:
    """A naive datetime, the way click.DateTime hands one to a command."""
    return datetime.strptime(isoDate, "%Y-%m-%d")


class TestCcTxnDateRange(unittest.TestCase):
    def _createTxn(self, isoDate: str, vendor: str = "IMTIAZ SUPER MARKET"):
        """A CC txn dated at midnight, matching how the HBL parser dates them."""
        txnDate = datetime.strptime(isoDate, "%Y-%m-%d").replace(tzinfo=DEFAULT_TZ)

        return CreditCardTxnDC(
            money=Money(Decimal("100.00"), "PKR"),
            date=txnDate,
            vendor=vendor,
            bank="HBL",
            card=CardReference.of("8526"),
        )

    def setUp(self):
        self.txns = [
            self._createTxn("2023-12-31"),
            self._createTxn("2024-01-01"),
            self._createTxn("2024-06-15"),
            self._createTxn("2024-12-31"),
            self._createTxn("2025-01-01"),
        ]

    def _dates(self, txns):
        return [txn.date.strftime("%Y-%m-%d") for txn in txns]

    def test_no_bounds_returns_everything(self):
        self.assertEqual(len(_filterTxnsByDateRange(self.txns, None, None)), 5)

    def test_both_bounds_are_inclusive(self):
        filtered = _filterTxnsByDateRange(
            self.txns, bound("2024-01-01"), bound("2024-12-31")
        )

        self.assertEqual(
            self._dates(filtered), ["2024-01-01", "2024-06-15", "2024-12-31"]
        )

    def test_an_open_lower_bound(self):
        filtered = _filterTxnsByDateRange(self.txns, None, bound("2024-01-01"))

        self.assertEqual(self._dates(filtered), ["2023-12-31", "2024-01-01"])

    def test_an_open_upper_bound(self):
        filtered = _filterTxnsByDateRange(self.txns, bound("2024-12-31"), None)

        self.assertEqual(self._dates(filtered), ["2024-12-31", "2025-01-01"])

    def test_a_single_day_range(self):
        filtered = _filterTxnsByDateRange(
            self.txns, bound("2024-06-15"), bound("2024-06-15")
        )

        self.assertEqual(self._dates(filtered), ["2024-06-15"])

    def test_a_range_matching_nothing(self):
        filtered = _filterTxnsByDateRange(
            self.txns, bound("2026-01-01"), bound("2026-12-31")
        )

        self.assertEqual(filtered, [])

    def test_an_inverted_range_is_rejected(self):
        with self.assertRaises(click.BadParameter):
            _filterTxnsByDateRange(
                self.txns, bound("2024-12-31"), bound("2024-01-01")
            )


class TestDebitTxnDateRange(unittest.TestCase):
    """Debit txns carry a real time of day, where CC txns from HBL and SCB do
    not. Comparison is on the calendar date so both bounds stay inclusive."""

    def _createDebitTxn(self, isoDateTime: str):
        txnDate = datetime.strptime(isoDateTime, "%Y-%m-%d %H:%M").replace(
            tzinfo=DEFAULT_TZ
        )

        return DebitTxnDC(
            money=Money(Decimal("5000.00"), "PKR"),
            date=txnDate,
            vendor="MEEZAN ATM",
            txnType=DebitTxnType.ATM_WITHDRAWAL,
            acctMask="xxxxxx5602",
        )

    def test_debit_txns_are_filtered_by_date(self):
        txns = [
            self._createDebitTxn("2024-01-01 09:00"),
            self._createDebitTxn("2024-06-15 14:30"),
            self._createDebitTxn("2024-12-31 23:45"),
        ]

        filtered = _filterTxnsByDateRange(
            txns, bound("2024-06-01"), bound("2024-06-30")
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].date.day, 15)

    def test_to_date_stays_inclusive_for_a_late_evening_txn(self):
        """Comparing datetimes rather than dates would drop this one."""
        txns = [self._createDebitTxn("2024-06-15 23:59")]

        filtered = _filterTxnsByDateRange(txns, None, bound("2024-06-15"))

        self.assertEqual(len(filtered), 1)

    def test_from_date_stays_inclusive_for_a_late_evening_txn(self):
        txns = [self._createDebitTxn("2024-06-15 23:59")]

        filtered = _filterTxnsByDateRange(txns, bound("2024-06-15"), None)

        self.assertEqual(len(filtered), 1)


class TestFilterLabel(unittest.TestCase):
    def test_no_filters_produce_no_label(self):
        self.assertEqual(_filterLabel(None, None), "")

    def test_each_bound_alone(self):
        self.assertEqual(
            _filterLabel(bound("2024-01-01"), None), " (from 2024-01-01)"
        )
        self.assertEqual(
            _filterLabel(None, bound("2024-12-31")), " (up to 2024-12-31)"
        )

    def test_both_bounds(self):
        self.assertEqual(
            _filterLabel(bound("2024-01-01"), bound("2024-12-31")),
            " (from 2024-01-01 to 2024-12-31)",
        )

    def test_the_bank_and_type_filters_appear_too(self):
        self.assertEqual(_filterLabel(None, None, bank="FBL"), " (bank FBL)")
        self.assertEqual(
            _filterLabel(None, None, txnType="atm_withdrawal"),
            " (type atm_withdrawal)",
        )

    def test_filters_combine_in_a_stable_order(self):
        self.assertEqual(
            _filterLabel(bound("2024-01-01"), None, bank="HBL"),
            " (bank HBL, from 2024-01-01)",
        )


if __name__ == "__main__":
    unittest.main()
