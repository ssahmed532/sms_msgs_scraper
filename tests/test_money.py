"""Tests for the exact-money contract and the shared amount grammar."""

import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from sms_msgs_scraper.domain.money import (
    AMOUNT_TOKEN_2DP_RE,
    AMOUNT_TOKEN_RE,
    MAX_INTEGER_DIGITS,
    CurrencyMismatchError,
    Money,
    MoneyError,
    minorUnitsFor,
)
from sms_msgs_scraper.domain.types import CardReference


class TestMoneyExactness(unittest.TestCase):
    """The reason this type exists: aggregation must not drift."""

    def test_summing_one_hundred_cents_is_exactly_one(self):
        """The canonical float failure, which `,.2f` would have hidden."""
        cents = [Money.parse("PKR", "0.01") for _ in range(100)]

        total = sum(cents)

        self.assertEqual(total.amount, Decimal("1.00"))
        self.assertEqual(total.formatted(), "1.00")

    def test_amount_is_captured_exactly_not_through_a_float(self):
        money = Money.parse("PKR", "25,170.49")

        self.assertEqual(money.amount, Decimal("25170.49"))
        # the float round-trip that used to happen loses this
        self.assertNotEqual(money.amount, Decimal(str(float("0.1") + float("0.2"))))

    def test_a_long_run_of_awkward_amounts_stays_exact(self):
        tokens = ["0.10", "0.20", "0.30", "1.15", "2.35"] * 40
        expected = Decimal("0.10") + Decimal("0.20") + Decimal("0.30")
        expected = (expected + Decimal("1.15") + Decimal("2.35")) * 40

        total = sum(Money.parse("PKR", token) for token in tokens)

        self.assertEqual(total.amount, expected)

    def test_a_float_amount_is_refused_at_construction(self):
        with self.assertRaises(MoneyError):
            Money(25170.49, "PKR")


class TestAmountGrammar(unittest.TestCase):
    def test_accepts_the_shapes_the_banks_actually_send(self):
        cases = [
            ("25,170.49", Decimal("25170.49")),   # HBL, grouped
            ("25170.49", Decimal("25170.49")),    # HBL ungrouped -- used to abort the run
            ("1,000", Decimal("1000")),
            ("8100", Decimal("8100")),            # FBL, no separators
            ("18298.9", Decimal("18298.9")),      # FBL, one decimal place
            ("450.00", Decimal("450.00")),
            ("1,234,567.89", Decimal("1234567.89")),
        ]

        for token, expected in cases:
            with self.subTest(token=token):
                self.assertEqual(Money.parse("PKR", token).amount, expected)

    def test_rejects_malformed_grouping(self):
        """A permissive `[\\d,]+` accepts these and silently strips the commas."""
        for token in ("1,2,3.00", "1,00", "1,0000.00", ",100", "100,"):
            with self.subTest(token=token), self.assertRaises(MoneyError):
                Money.parse("PKR", token)

    def test_rejects_a_token_that_is_not_an_amount(self):
        for token in ("", ".00", "abc", "12.34.56", "-5.00", "1e9"):
            with self.subTest(token=token), self.assertRaises(MoneyError):
                Money.parse("PKR", token)

    def test_bounds_the_digit_length(self):
        """No input can grow an amount without bound."""
        allowed = "9" * MAX_INTEGER_DIGITS
        self.assertEqual(Money.parse("PKR", allowed).amount, Decimal(allowed))

        with self.assertRaises(MoneyError):
            Money.parse("PKR", "9" * (MAX_INTEGER_DIGITS + 1))

    def test_the_two_token_patterns_agree_on_grouping(self):
        import re

        strict = re.compile(AMOUNT_TOKEN_RE)
        twoDp = re.compile(AMOUNT_TOKEN_2DP_RE)

        self.assertIsNone(strict.fullmatch("1,2,3.00"))
        self.assertIsNone(twoDp.fullmatch("1,2,3.00"))
        # the 2dp variant is what rejects SCB's amount-less "PKR .00"
        self.assertIsNone(twoDp.fullmatch(".00"))
        self.assertIsNone(twoDp.fullmatch("1000"))
        self.assertIsNotNone(twoDp.fullmatch("12,450.90"))


class TestCurrencyPolicy(unittest.TestCase):
    def test_an_unknown_three_letter_code_is_accepted_at_the_default_scale(self):
        """Declared policy, not a crash: a card can be used in any currency."""
        money = Money.parse("AED", "500.25")

        self.assertEqual(money.currency, "AED")
        self.assertEqual(minorUnitsFor("AED"), 2)

    def test_a_malformed_currency_code_is_rejected(self):
        for currency in ("", "P", "PK", "PKRR", "pkr", "12A"):
            with self.subTest(currency=currency), self.assertRaises(MoneyError):
                Money.parse(currency, "100.00")

    def test_scale_beyond_the_currency_minor_units_is_rejected(self):
        """Rejected, rather than rounded away invisibly at render time."""
        with self.assertRaises(MoneyError):
            Money.parse("PKR", "100.123")

        self.assertEqual(Money.parse("PKR", "100.12").amount, Decimal("100.12"))


class TestMoneyArithmetic(unittest.TestCase):
    def test_addition_within_a_currency(self):
        total = Money.parse("PKR", "100.50") + Money.parse("PKR", "0.50")

        self.assertEqual(total, Money(Decimal("101.00"), "PKR"))

    def test_addition_across_currencies_is_refused(self):
        with self.assertRaises(CurrencyMismatchError):
            Money.parse("PKR", "100.00") + Money.parse("USD", "1.00")

    def test_sum_starts_from_the_integer_zero(self):
        moneys = [Money.parse("USD", "1.50"), Money.parse("USD", "2.50")]

        self.assertEqual(sum(moneys), Money(Decimal("4.00"), "USD"))

    def test_zero_is_the_additive_identity(self):
        money = Money.parse("PKR", "42.00")

        self.assertEqual(Money.zero("PKR") + money, money)

    def test_money_is_immutable(self):
        money = Money.parse("PKR", "10.00")

        with self.assertRaises(FrozenInstanceError):
            money.amount = Decimal("20.00")

    def test_formatted_groups_thousands(self):
        self.assertEqual(Money.parse("PKR", "1234567.89").formatted(), "1,234,567.89")
        self.assertEqual(str(Money.parse("USD", "4.02")), "USD 4.02")


class TestCardReference(unittest.TestCase):
    def test_absent_is_distinct_from_a_card_ending_in_zeros(self):
        """The integer field this replaces could not tell these apart."""
        absent = CardReference.absent()
        allZeros = CardReference.of("0000")

        self.assertNotEqual(absent, allZeros)
        self.assertFalse(absent.known)
        self.assertTrue(allZeros.known)
        self.assertEqual(str(allZeros), "0000")
        self.assertEqual(str(absent), "")

    def test_rejects_a_mask_that_is_not_four_digits(self):
        for value in ("123", "12345", "12a4"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CardReference.of(value)


if __name__ == "__main__":
    unittest.main()
