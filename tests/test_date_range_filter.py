import unittest
from datetime import datetime

import click

from cc_txn import CreditCardTxnDC, CurrencyAmountTuple
from common import DEFAULT_TZ
from hbl_sms_query_tool import _dateRangeLabel, _filterTxnsByDateRange


class TestDateRangeFilter(unittest.TestCase):

    def _createTxn(self, isoDate: str, vendor: str = "IMTIAZ SUPER MARKET") -> CreditCardTxnDC:
        """Build a CC txn dated at midnight on isoDate (YYYY-MM-DD) in the
        default (Asia/Karachi) timezone, matching how the HBL parser dates txns.
        """
        txnDate = datetime.strptime(isoDate, "%Y-%m-%d").replace(tzinfo=DEFAULT_TZ)

        return CreditCardTxnDC(
            amountTuple=CurrencyAmountTuple("PKR", 100.00),
            date=txnDate,
            vendor=vendor,
            ccLastFourDigits=8526,
        )

    def _bound(self, isoDate: str) -> datetime:
        """Build a naive datetime the way click.DateTime hands one to a command."""
        return datetime.strptime(isoDate, "%Y-%m-%d")

    def setUp(self):
        self.txns = [
            self._createTxn("2023-12-31"),
            self._createTxn("2024-01-01"),
            self._createTxn("2024-06-15"),
            self._createTxn("2024-12-31"),
            self._createTxn("2025-01-01"),
        ]

    def _dates(self, txns: list) -> list:
        return [txn.date.strftime("%Y-%m-%d") for txn in txns]

    def test_no_bounds_returns_everything(self):
        """Test method to verify that omitting both bounds leaves the txn
        list untouched.
        """
        self.assertEqual(_filterTxnsByDateRange(self.txns, None, None), self.txns)

    def test_both_bounds_are_inclusive(self):
        """Test method to verify that txns falling exactly on --from-date and
        on --to-date are kept, not dropped.
        """
        filtered = _filterTxnsByDateRange(
            self.txns, self._bound("2024-01-01"), self._bound("2024-12-31")
        )

        self.assertEqual(
            self._dates(filtered), ["2024-01-01", "2024-06-15", "2024-12-31"]
        )

    def test_open_lower_bound(self):
        """Test method to verify that supplying only --to-date leaves the
        lower end of the range open.
        """
        filtered = _filterTxnsByDateRange(self.txns, None, self._bound("2023-12-31"))

        self.assertEqual(self._dates(filtered), ["2023-12-31"])

    def test_open_upper_bound(self):
        """Test method to verify that supplying only --from-date leaves the
        upper end of the range open.
        """
        filtered = _filterTxnsByDateRange(self.txns, self._bound("2025-01-01"), None)

        self.assertEqual(self._dates(filtered), ["2025-01-01"])

    def test_single_day_range(self):
        """Test method to verify that an identical from/to date selects
        exactly that one day.
        """
        filtered = _filterTxnsByDateRange(
            self.txns, self._bound("2024-06-15"), self._bound("2024-06-15")
        )

        self.assertEqual(self._dates(filtered), ["2024-06-15"])

    def test_range_matching_nothing(self):
        """Test method to verify that a valid range covering no txns yields
        an empty list rather than an error.
        """
        filtered = _filterTxnsByDateRange(
            self.txns, self._bound("2024-07-01"), self._bound("2024-07-31")
        )

        self.assertEqual(filtered, [])

    def test_inverted_range_is_rejected(self):
        """Test method to verify that a --from-date later than --to-date is
        reported as a bad parameter instead of silently returning nothing.
        """
        with self.assertRaises(click.BadParameter):
            _filterTxnsByDateRange(
                self.txns, self._bound("2024-12-31"), self._bound("2024-01-01")
            )

    def test_dateRangeLabel(self):
        """Test method to verify the human-readable range suffix used in the
        command output headers.
        """
        self.assertEqual(_dateRangeLabel(None, None), "")
        self.assertEqual(
            _dateRangeLabel(self._bound("2024-01-01"), None), " (from 2024-01-01)"
        )
        self.assertEqual(
            _dateRangeLabel(None, self._bound("2024-12-31")), " (up to 2024-12-31)"
        )
        self.assertEqual(
            _dateRangeLabel(self._bound("2024-01-01"), self._bound("2024-12-31")),
            " (from 2024-01-01 to 2024-12-31)",
        )


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()
