"""Tests for `monthly_vendor_chart`.

Five properties, and each of them is something a chart gets wrong silently.

  * **Gaps are drawn.** A month with no transactions is a row of dots, not a
    missing row. Closing a gap up is the one failure a reader cannot detect,
    because the result still looks like a chart.
  * **Bar length encodes the total.** The segments of a stacked bar sum to
    exactly the bar's own cell count, so two bars are comparable by eye.
  * **A series keeps one identity for the whole chart.** Colour and glyph come
    from a selection made once across every currency, so the same bank is not a
    solid block in the PKR chart and a shaded one in the USD chart.
  * **Currencies are never added together**, here as everywhere else.
  * **The machine formats carry the unfolded series.** The four-series cap is a
    readability limit of a terminal bar; a program gets every series by name.
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

from sms_msgs_scraper.domain.aggregate import monthKeysBetween, seriesTotalsByMonth
from sms_msgs_scraper.domain.cc_txn import CreditCardTxnDC
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.types import CardReference
from sms_msgs_scraper.domain.tz import DEFAULT_TZ
from sms_msgs_scraper.render.charts import (
    MAX_NAMED_SERIES,
    OTHER_SERIES,
    _compactAmount,
    foldSeries,
    segmentWidths,
    selectSeries,
)
from sms_msgs_scraper.sms_txn_query_tool import cli

GAP_MARKER = "no transactions"

FBL_USD_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for USD 39.99 on "
    "20-Sep-23 01:17:16 PM at AMAZON.COM             SEATTLE        US."
)


def hblBody(vendor: str, amount: str, txnDate: str) -> str:
    """An HBL CC alert, dated DD/Mon/YYYY, in PKR."""
    return (
        f"Dear Customer, Your HBL CreditCard (ending with 8526) has been "
        f"charged at {vendor} for PKR-{amount} on {txnDate}."
    )


def ccTxn(vendor, amount, when, currency="PKR", bank="HBL"):
    return CreditCardTxnDC(
        money=Money(Decimal(amount), currency),
        date=when.replace(tzinfo=DEFAULT_TZ),
        vendor=vendor,
        bank=bank,
        card=CardReference.of("8526"),
    )


class TestMonthKeysBetween(unittest.TestCase):
    def test_it_includes_both_ends(self):
        self.assertEqual(
            monthKeysBetween("2025-01", "2025-03"), ["2025-01", "2025-02", "2025-03"]
        )

    def test_a_single_month_is_itself(self):
        self.assertEqual(monthKeysBetween("2025-07", "2025-07"), ["2025-07"])

    def test_it_steps_across_a_year_boundary(self):
        self.assertEqual(
            monthKeysBetween("2024-11", "2025-02"),
            ["2024-11", "2024-12", "2025-01", "2025-02"],
        )

    def test_it_names_the_months_nothing_was_spent_in(self):
        """The property the chart depends on: a July with no transactions still
        gets a row, so June does not render next to August."""
        keys = monthKeysBetween("2025-06", "2025-08")

        self.assertIn("2025-07", keys)


class TestSeriesTotalsByMonth(unittest.TestCase):
    def test_it_nests_month_then_series_then_currency(self):
        txns = [
            ccTxn("PSO", "100.00", datetime(2025, 1, 5)),
            ccTxn("PSO", "50.00", datetime(2025, 1, 20)),
            ccTxn("SHELL", "25.00", datetime(2025, 1, 9)),
            ccTxn("PSO", "10.00", datetime(2025, 2, 1)),
        ]

        perMonth = seriesTotalsByMonth(txns, lambda txn: txn.vendor)

        self.assertEqual(sorted(perMonth), ["2025-01", "2025-02"])
        self.assertEqual(
            perMonth["2025-01"]["PSO"]["PKR"], Money(Decimal("150.00"), "PKR")
        )
        self.assertEqual(
            perMonth["2025-01"]["SHELL"]["PKR"], Money(Decimal("25.00"), "PKR")
        )
        self.assertEqual(sorted(perMonth["2025-02"]), ["PSO"])

    def test_currencies_stay_apart_within_one_series(self):
        txns = [
            ccTxn("AMAZON", "100.00", datetime(2025, 1, 5)),
            ccTxn("AMAZON", "39.99", datetime(2025, 1, 6), currency="USD"),
        ]

        perMonth = seriesTotalsByMonth(txns, lambda txn: txn.vendor)

        self.assertEqual(
            perMonth["2025-01"]["AMAZON"]["PKR"], Money(Decimal("100.00"), "PKR")
        )
        self.assertEqual(
            perMonth["2025-01"]["AMAZON"]["USD"], Money(Decimal("39.99"), "USD")
        )

    def test_totals_are_exact(self):
        """25,170.49 + 0.01 lands on the cent, which binary floating point
        would not."""
        txns = [
            ccTxn("IMTIAZ", "25170.49", datetime(2025, 3, 2)),
            ccTxn("IMTIAZ", "0.01", datetime(2025, 3, 3)),
        ]

        perMonth = seriesTotalsByMonth(txns, lambda txn: txn.vendor)

        self.assertEqual(
            perMonth["2025-03"]["IMTIAZ"]["PKR"], Money(Decimal("25170.50"), "PKR")
        )


class TestSegmentWidths(unittest.TestCase):
    def test_the_segments_sum_to_the_bar_length(self):
        """Rounding each segment on its own would leave the bar a cell wider or
        narrower than its total says -- which is what a reader is measuring."""
        amounts = [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

        widths = segmentWidths(amounts, Decimal("100.00"), 40)

        self.assertEqual(sum(widths), 40)

    def test_a_bar_at_half_scale_is_half_as_long(self):
        widths = segmentWidths([Decimal("50")], Decimal("100"), 40)

        self.assertEqual(sum(widths), 20)

    def test_a_full_scale_bar_fills_the_width(self):
        widths = segmentWidths([Decimal("100")], Decimal("100"), 40)

        self.assertEqual(sum(widths), 40)

    def test_a_nonzero_total_never_renders_as_nothing(self):
        """A tiny month is one cell rather than an empty row, so it is not
        mistaken for the gap that means no transactions at all."""
        widths = segmentWidths([Decimal("0.01")], Decimal("100000"), 40)

        self.assertEqual(sum(widths), 1)

    def test_an_empty_bar_has_no_cells(self):
        self.assertEqual(segmentWidths([Decimal(0), Decimal(0)], Decimal("100"), 40),
                         [0, 0])

    def test_three_equal_segments_and_a_small_one_do_not_overflow(self):
        """The case the old rounding got wrong: 30/30/30/10 of a 5-cell bar
        rounded each 1.5 up to 2, used six cells, and clamped the last segment
        at zero -- a bar one cell longer than the axis it sat on."""
        amounts = [Decimal(30), Decimal(30), Decimal(30), Decimal(10)]

        widths = segmentWidths(amounts, Decimal(100), 5)

        self.assertEqual(sum(widths), 5)

    def test_the_segments_always_sum_to_the_bar_length(self):
        """Largest-remainder apportionment is exact by construction, so this
        holds for every mix of up to five segments at every bar width."""
        import random

        rng = random.Random(20250911)
        for _ in range(2000):
            amounts = [
                Decimal(rng.randint(1, 5000)) for _ in range(rng.choice((2, 3, 4, 5)))
            ]
            barWidth = rng.choice((20, 32, 40, 56))
            total = sum(amounts, Decimal(0))
            # the largest month, so the bar should fill the width exactly
            self.assertEqual(sum(segmentWidths(amounts, total, barWidth)), barWidth)
            # and a smaller month, whose bar is shorter than the width
            self.assertEqual(
                sum(segmentWidths(amounts, total * 3, barWidth)),
                round(barWidth / 3),
            )

    def test_no_segment_is_ever_negative(self):
        widths = segmentWidths(
            [Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(1)],
            Decimal(5),
            1,
        )

        self.assertEqual(sum(widths), 1)
        self.assertTrue(all(width >= 0 for width in widths))


class TestAxisTickLabels(unittest.TestCase):
    def test_a_tick_under_ten_thousand_keeps_one_decimal(self):
        """1,500 was labelled `2k`, which put the same label under two ticks:
        a 2,000-scale axis read `0  500  1k  2k  2k`."""
        self.assertEqual(_compactAmount(Decimal(1500)), "1.5k")
        self.assertEqual(_compactAmount(Decimal(2250)), "2.2k")

    def test_a_whole_thousand_drops_the_decimal(self):
        self.assertEqual(_compactAmount(Decimal(2000)), "2k")
        self.assertEqual(_compactAmount(Decimal(1_000_000)), "1M")

    def test_larger_ticks_are_whole_units(self):
        self.assertEqual(_compactAmount(Decimal(12_000)), "12k")
        self.assertEqual(_compactAmount(Decimal(375_000)), "375k")
        self.assertEqual(_compactAmount(Decimal(1_500_000)), "1.5M")
        self.assertEqual(_compactAmount(Decimal(12_500_000)), "12M")

    def test_under_a_thousand_is_the_number_itself(self):
        self.assertEqual(_compactAmount(Decimal(0)), "0")
        self.assertEqual(_compactAmount(Decimal(750)), "750")

    def test_a_tick_under_ten_keeps_one_decimal_too(self):
        """The 2.5.1 fix stopped at the thousands unit. A USD or CAD month is
        frequently single-digit, and a 3-scale axis read `0 1 2 2 3`."""
        self.assertEqual(_compactAmount(Decimal("2.25")), "2.2")
        self.assertEqual(_compactAmount(Decimal("1.5")), "1.5")
        self.assertEqual(_compactAmount(Decimal("0.75")), "0.8")
        self.assertEqual(_compactAmount(Decimal(3)), "3")
        self.assertEqual(_compactAmount(Decimal(12)), "12")

    def test_the_five_ticks_of_a_small_axis_are_all_distinct(self):
        for scale in (2, 3, 6, 7, 9, 2000, 3000, 4000, 6000):
            with self.subTest(scale=scale):
                labels = [
                    _compactAmount(Decimal(scale) * Decimal(fraction))
                    for fraction in ("0", "0.25", "0.5", "0.75", "1")
                ]
                self.assertEqual(len(set(labels)), 5, labels)


class TestSeriesSelection(unittest.TestCase):
    def _perMonth(self, vendorAmounts, currency="PKR"):
        txns = [
            ccTxn(vendor, amount, datetime(2025, 1, 5), currency=currency)
            for vendor, amount in vendorAmounts
        ]

        return seriesTotalsByMonth(txns, lambda txn: txn.vendor)

    def test_four_or_fewer_series_are_all_named(self):
        perMonth = self._perMonth(
            [("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")]
        )

        names = selectSeries(perMonth, ["A", "B", "C", "D"], ["PKR"])

        self.assertEqual(names, ["A", "B", "C", "D"])
        self.assertNotIn(OTHER_SERIES, names)

    def test_a_fifth_series_folds_the_smallest_into_other(self):
        perMonth = self._perMonth(
            [("A", "100"), ("B", "200"), ("C", "300"), ("D", "400"), ("E", "1")]
        )

        names = selectSeries(perMonth, ["A", "B", "C", "D", "E"], ["PKR"])

        self.assertEqual(names, ["A", "B", "C", "D", OTHER_SERIES])
        self.assertEqual(len(names), MAX_NAMED_SERIES + 1)

    def test_the_named_survivors_keep_the_callers_order_not_the_size_order(self):
        """Colour comes from position, so ordering by size would repaint every
        series the moment a filter changed which one was biggest."""
        perMonth = self._perMonth(
            [("A", "1"), ("B", "500"), ("C", "3"), ("D", "400"), ("E", "2")]
        )

        names = selectSeries(perMonth, ["A", "B", "C", "D", "E"], ["PKR"])

        # A is the smallest and folds away; the four survivors keep the order
        # they were given, not B, D, C, E -- which is what size would give.
        self.assertEqual(names, ["B", "C", "D", "E", OTHER_SERIES])

    def test_a_series_the_caller_did_not_list_is_still_placed(self):
        perMonth = self._perMonth([("A", "1"), ("Z", "2")])

        names = selectSeries(perMonth, ["A"], ["PKR"])

        self.assertIn("Z", names)

    def test_the_same_series_keeps_one_slot_across_currencies(self):
        """The bug this pins: selecting per currency gave FBL one glyph in the
        PKR chart and a different one in the USD chart."""
        txns = [
            ccTxn("A", "100", datetime(2025, 1, 5), bank="HBL"),
            ccTxn("B", "100", datetime(2025, 1, 5), bank="FBL"),
            ccTxn("B", "10", datetime(2025, 1, 6), currency="USD", bank="FBL"),
        ]
        perMonth = seriesTotalsByMonth(txns, lambda txn: txn.bank)

        names = selectSeries(perMonth, ["HBL", "FBL"], ["PKR", "USD"])
        _, pkrSlots = foldSeries(perMonth, "PKR", names)
        _, usdSlots = foldSeries(perMonth, "USD", names)

        fblSlot = names.index("FBL")
        self.assertEqual(usdSlots, {fblSlot})
        self.assertIn(fblSlot, pkrSlots)

    def test_a_currency_only_reports_the_series_it_holds(self):
        txns = [
            ccTxn("A", "100", datetime(2025, 1, 5)),
            ccTxn("B", "10", datetime(2025, 1, 6), currency="USD"),
        ]
        perMonth = seriesTotalsByMonth(txns, lambda txn: txn.vendor)
        names = selectSeries(perMonth, ["A", "B"], ["PKR", "USD"])

        amounts, slots = foldSeries(perMonth, "USD", names)

        self.assertEqual(slots, {names.index("B")})
        self.assertEqual(amounts["2025-01"][names.index("A")], Decimal(0))

    def test_a_series_literally_named_other_keeps_its_own_slot(self):
        """The fold bucket is an object, not the string "Other". A series is
        named by the data -- a vendor string, a canonical name -- and one of
        those can be "Other"; keyed on the string, it shared the bucket's
        slot, its amounts were added to the bucket and its glyph vanished."""
        perMonth = self._perMonth(
            [
                ("A", "500"), ("B", "400"), ("Other", "300"), ("D", "200"),
                ("E", "1"), ("F", "2"),
            ]
        )

        names = selectSeries(perMonth, ["A", "B", "D", "E", "F", "Other"], ["PKR"])
        amounts, slots = foldSeries(perMonth, "PKR", names)

        self.assertEqual(names, ["A", "B", "D", "Other", OTHER_SERIES])
        self.assertEqual(str(OTHER_SERIES), "Other")
        self.assertNotEqual(OTHER_SERIES, "Other")
        # the real series keeps its own amount, and the bucket holds E + F
        self.assertEqual(amounts["2025-01"][names.index("Other")], Decimal(300))
        self.assertEqual(amounts["2025-01"][MAX_NAMED_SERIES], Decimal(3))
        self.assertEqual(slots, {0, 1, 2, 3, 4})


class ChartCliTestCase(unittest.TestCase):
    """The shared backup-building helpers, following the convention that each
    test file carries its own copy."""

    def setUp(self):
        # A wide terminal so cells are not wrapped mid-value.
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

    def _gappedBackup(self) -> Path:
        """January and March spending, and deliberately nothing in February."""
        return self._backup(
            [
                self._sms("4250", hblBody("PSO", "1,000.00", "10/Jan/2025")),
                self._sms("4250", hblBody("SHELL", "500.00", "12/Jan/2025")),
                self._sms("4250", hblBody("PSO", "2,000.00", "10/Mar/2025")),
            ]
        )

    def _multiCurrencyBackup(self) -> Path:
        return self._backup(
            [
                self._sms("4250", hblBody("PSO", "1,000.00", "10/Jan/2025")),
                self._sms("8756", FBL_USD_BODY),
            ]
        )

    def run_cli(self, args):
        return self.runner.invoke(cli, args, catch_exceptions=False)


class TestTheChartCommand(ChartCliTestCase):
    def test_it_charts_both_stores_together(self):
        """The Meezan debit and the HBL card transaction land on one axis --
        the only command in the tool that reads both stores."""
        backup = self._backup(
            [
                self._sms("4250", hblBody("PSO", "1,000.00", "10/Jan/2025")),
                self._sms(
                    "8079",
                    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 "
                    "from A/C xxxxxx5602 KARACHI BRANCH on 15-Jan-25 at 09:05 "
                    "Bal: PKR 1,234.00",
                ),
            ]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Charting 2 transactions", result.stderr)
        self.assertIn("21,000.00", result.stdout)

    def test_a_month_with_nothing_in_it_is_still_drawn(self):
        result = self.run_cli([str(self._gappedBackup()), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Feb 2025", result.stdout)
        self.assertIn(GAP_MARKER, result.stdout)

    def test_the_gap_month_breaks_the_change_column(self):
        """March is not compared against January: there is no honest percentage
        to put across a month that has no value at all."""
        result = self.run_cli([str(self._gappedBackup()), "monthly_vendor_chart"])

        marchRow = next(
            line for line in result.stdout.splitlines() if "Mar 2025" in line
        )
        self.assertNotIn("%", marchRow)

    def test_the_change_column_distinguishes_a_rise_a_fall_and_no_change(self):
        """Three states. A month equal to the one before used to show `▼ 0.0%`,
        which said it had fallen."""
        backup = self._backup(
            [
                self._sms("4250", hblBody("PSO", "1,000.00", "10/Jan/2025")),
                self._sms("4250", hblBody("PSO", "2,000.00", "10/Feb/2025")),
                self._sms("4250", hblBody("PSO", "2,000.00", "10/Mar/2025")),
                self._sms("4250", hblBody("PSO", "500.00", "10/Apr/2025")),
            ]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        rows = {
            label: next(line for line in result.stdout.splitlines() if label in line)
            for label in ("Jan 2025", "Feb 2025", "Mar 2025", "Apr 2025")
        }
        self.assertNotIn("%", rows["Jan 2025"])
        self.assertIn("▲ 100.0%", rows["Feb 2025"])
        self.assertIn("=   0.0%", rows["Mar 2025"])
        self.assertNotIn("▼", rows["Mar 2025"])
        self.assertIn("▼  75.0%", rows["Apr 2025"])

    def test_the_axis_of_a_small_chart_has_no_repeated_tick(self):
        """A 2,000-scale axis read `0  500  1k  2k  2k`."""
        backup = self._backup(
            [self._sms("4250", hblBody("PSO", "2,000.00", "10/Jan/2025"))]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        axis = next(
            line for line in result.stdout.splitlines() if line.strip().startswith("0 ")
        )
        ticks = axis.split()
        self.assertEqual(ticks, ["0", "500", "1k", "1.5k", "2k"])

    def test_currencies_are_charted_separately(self):
        result = self.run_cli(
            [str(self._multiCurrencyBackup()), "monthly_vendor_chart"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("PKR totals", result.stdout)
        self.assertIn("USD totals", result.stdout)
        # 1,000.00 PKR + 39.99 USD added as though money were unitless.
        self.assertNotIn("1,039.99", result.stdout)

    def test_group_by_none_draws_one_series(self):
        result = self.run_cli(
            [str(self._gappedBackup()), "monthly_vendor_chart", "--group-by", "none"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("All transactions", result.stdout)
        self.assertNotIn("SHELL", result.stdout)

    def test_group_by_bank_splits_by_issuer(self):
        result = self.run_cli(
            [
                str(self._multiCurrencyBackup()),
                "monthly_vendor_chart",
                "--group-by",
                "bank",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("HBL", result.stdout)
        self.assertIn("FBL", result.stdout)

    def test_group_by_txn_type_names_credit_card_purchases(self):
        """A CC transaction carries no DebitTxnType. Giving it a name of its own
        is what lets both stores share one axis."""
        result = self.run_cli(
            [
                str(self._gappedBackup()),
                "monthly_vendor_chart",
                "--group-by",
                "txn-type",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("credit_card", result.stdout)

    def test_a_vendor_needle_narrows_the_chart(self):
        result = self.run_cli(
            [str(self._gappedBackup()), "monthly_vendor_chart", "--vendor", "PSO"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Charting 2 transactions", result.stderr)
        self.assertIn("3,000.00", result.stdout)

    def test_no_vendor_charts_everything(self):
        result = self.run_cli([str(self._gappedBackup()), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Charting 3 transactions", result.stderr)
        self.assertIn("3,500.00", result.stdout)

    def test_a_filter_matching_nothing_says_so(self):
        result = self.run_cli(
            [str(self._gappedBackup()), "monthly_vendor_chart", "--vendor", "NOSUCH"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No transactions match this filter.", result.stderr)

    def test_an_unknown_group_by_is_a_usage_error(self):
        result = self.runner.invoke(
            cli, [str(self._gappedBackup()), "monthly_vendor_chart", "--group-by", "x"]
        )

        self.assertEqual(result.exit_code, 2)

    def test_the_chart_goes_to_stdout_and_the_notice_to_stderr(self):
        result = self.run_cli([str(self._gappedBackup()), "monthly_vendor_chart"])

        self.assertIn("Charting", result.stderr)
        self.assertNotIn("Charting", result.stdout)


class TestTheMachineFormats(ChartCliTestCase):
    def _csvRows(self, args):
        result = self.run_cli(args)
        self.assertEqual(result.exit_code, 0)

        return list(csv.DictReader(io.StringIO(result.stdout)))

    def test_csv_carries_month_series_currency_amount(self):
        rows = self._csvRows(
            [
                "--format",
                "csv",
                str(self._gappedBackup()),
                "monthly_vendor_chart",
            ]
        )

        self.assertEqual(
            list(rows[0]), ["month", "series", "currency", "amount"]
        )

    def test_csv_amounts_are_exact_strings(self):
        rows = self._csvRows(
            ["--format", "csv", str(self._gappedBackup()), "monthly_vendor_chart"]
        )
        amounts = {(row["series"], row["amount"]) for row in rows}

        self.assertIn(("PSO", "1000.00"), amounts)
        self.assertIn(("SHELL", "500.00"), amounts)

    def test_a_gap_month_carries_no_rows(self):
        """A drawn gap and an asserted zero are different claims. February has
        no rows, rather than a row saying nothing was spent."""
        rows = self._csvRows(
            ["--format", "csv", str(self._gappedBackup()), "monthly_vendor_chart"]
        )

        self.assertEqual({row["month"] for row in rows}, {"2025-01", "2025-03"})

    def test_json_is_schema_versioned_and_names_its_kind(self):
        result = self.run_cli(
            ["--format", "json", str(self._gappedBackup()), "monthly_vendor_chart"]
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["kind"], "monthly_chart")
        self.assertEqual(payload["count"], len(payload["rows"]))

    def test_the_machine_formats_do_not_fold_into_other(self):
        """The four-series cap is a readability limit of a bar 56 cells wide.
        A consumer handed "Other" could never recover what was in it."""
        backup = self._backup(
            [
                self._sms("4250", hblBody(f"VENDOR {n}", f"{n}00.00", "10/Jan/2025"))
                for n in range(1, 7)
            ]
        )

        rows = self._csvRows(
            ["--format", "csv", str(backup), "monthly_vendor_chart"]
        )
        series = {row["series"] for row in rows}

        self.assertEqual(len(series), 6)
        self.assertNotIn(str(OTHER_SERIES), series)

    def test_the_rendered_chart_does_fold_into_other(self):
        """The counterpart of the test above: the *table* output folds, because
        there are four series colours and a fifth would have to reuse one."""
        backup = self._backup(
            [
                self._sms("4250", hblBody(f"VENDOR {n}", f"{n}00.00", "10/Jan/2025"))
                for n in range(1, 7)
            ]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn(str(OTHER_SERIES), result.stdout)
        # The two smallest fold together: 100.00 + 200.00.
        self.assertIn("300.00", result.stdout)
        # ... and the total is still every transaction.
        self.assertIn("2,100.00", result.stdout)

    def test_a_multi_currency_chart_ranks_without_adding_currencies(self):
        """With more than one currency, the four named series are chosen by how
        much of the chart they occupy -- ranking them by amount would mean
        adding PKR to USD."""
        backup = self._backup(
            [
                self._sms("4250", hblBody(f"VENDOR {n}", f"{n}00.00", "10/Jan/2025"))
                for n in range(1, 7)
            ]
            + [self._sms("8756", FBL_USD_BODY)]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("PKR totals", result.stdout)
        self.assertIn("USD totals", result.stdout)
        self.assertIn(str(OTHER_SERIES), result.stdout)
        self.assertIn("2,100.00", result.stdout)
        self.assertIn("39.99", result.stdout)

    def test_an_empty_result_is_a_header_only_csv(self):
        result = self.run_cli(
            [
                "--format",
                "csv",
                str(self._gappedBackup()),
                "monthly_vendor_chart",
                "--vendor",
                "NOSUCH",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "month,series,currency,amount")


class TestChartLayoutAndOptions(ChartCliTestCase):
    """The cases the review found untested: a total wider than its column, a
    change past 999%, a narrow terminal, and the chart under each option it
    shares with the other commands."""

    def _rowFor(self, result, label):
        return next(line for line in result.stdout.splitlines() if label in line)

    def test_a_wide_total_widens_its_column_rather_than_wrapping_the_row(self):
        """The total column used to be fixed at thirteen characters, so a
        total of thirteen or more ran past it and Rich wrapped the row --
        the month on one line and its total on the next. At 60 columns that
        is exactly what would happen here without sizing from the data."""
        backup = self._backup(
            [
                self._sms("4250", hblBody("PSO", "123,456,789.00", "10/Jan/2025")),
                self._sms("4250", hblBody("PSO", "246,913,578.00", "10/Feb/2025")),
            ]
        )
        runner = CliRunner(env={"COLUMNS": "60"})

        result = runner.invoke(cli, [str(backup), "monthly_vendor_chart"], catch_exceptions=False)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("123,456,789.00", self._rowFor(result, "Jan 2025"))
        febRow = self._rowFor(result, "Feb 2025")
        self.assertIn("246,913,578.00", febRow)
        self.assertIn("▲ 100.0%", febRow)

    def test_a_change_past_999_percent_is_clamped_rather_than_widening_the_row(self):
        backup = self._backup(
            [
                self._sms("4250", hblBody("PSO", "1.00", "10/Jan/2025")),
                self._sms("4250", hblBody("PSO", "50.00", "10/Feb/2025")),
            ]
        )

        result = self.run_cli([str(backup), "monthly_vendor_chart"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("▲  >999%", self._rowFor(result, "Feb 2025"))
        self.assertNotIn("4900", result.stdout)

    def test_a_narrow_terminal_still_gets_a_chart(self):
        """Below about 56 columns every row wraps; nothing is dropped."""
        runner = CliRunner(env={"COLUMNS": "40"})

        result = runner.invoke(
            cli, [str(self._gappedBackup()), "monthly_vendor_chart"], catch_exceptions=False
        )

        self.assertEqual(result.exit_code, 0)
        for label in ("Jan 2025", "Feb 2025", "Mar 2025", "3,500.00"):
            with self.subTest(label=label):
                self.assertIn(label, result.stdout)

    def test_no_color_renders_the_same_figures_without_escapes(self):
        result = self.run_cli(
            ["--no-color", str(self._gappedBackup()), "monthly_vendor_chart"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("\x1b[", result.stdout)
        self.assertIn("3,500.00", result.stdout)

    def test_the_chart_starts_at_the_first_transaction_month_not_the_from_date(self):
        """Documented rather than changed: the requested range is a filter,
        and the axis runs over the months that survived it."""
        result = self.run_cli(
            [
                str(self._gappedBackup()),
                "monthly_vendor_chart",
                "--from-date",
                "2025-02-01",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Charting 1 transactions", result.stderr)
        self.assertIn("Mar 2025", result.stdout)
        self.assertNotIn("Jan 2025", result.stdout)
        self.assertNotIn("Feb 2025", result.stdout)

    def test_canonical_vendors_regroup_the_series(self):
        mapPath = Path(tempfile.mkdtemp()) / "map.json"
        self.addCleanup(lambda: mapPath.unlink() if mapPath.exists() else None)
        mapPath.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "canonicalVendors": {"FUEL": {"prefix": ["PSO", "SHELL"]}},
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli(
            [
                "--vendor-map",
                str(mapPath),
                str(self._gappedBackup()),
                "monthly_vendor_chart",
                "--canonical-vendors",
            ]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("FUEL", result.stdout)
        self.assertNotIn("SHELL", result.stdout)
        # regrouped, not re-totalled
        self.assertIn("3,500.00", result.stdout)

    def test_group_by_txn_type_names_the_meezan_series_too(self):
        backup = self._backup(
            [
                self._sms("4250", hblBody("PSO", "1,000.00", "10/Jan/2025")),
                self._sms(
                    "8079",
                    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 "
                    "from A/C xxxxxx5602 KARACHI BRANCH on 15-Jan-25 at 09:05 "
                    "Bal: PKR 1,234.00",
                ),
            ]
        )

        result = self.run_cli(
            [str(backup), "monthly_vendor_chart", "--group-by", "txn-type"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("credit_card", result.stdout)
        self.assertIn("atm_withdrawal", result.stdout)
        self.assertIn("21,000.00", result.stdout)


if __name__ == "__main__":
    unittest.main()
