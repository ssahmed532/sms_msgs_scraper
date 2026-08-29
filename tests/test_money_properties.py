"""Property tests for exact money.

`tests/test_money.py` pins the contract with worked examples. This file attacks
it: many amounts, many orderings, and the specific numeric shapes where binary
floating point is known to disagree with decimal arithmetic.

Deliberately no property-testing dependency. The generators are seeded and the
case lists are explicit, so a failure here reproduces exactly rather than
appearing once in someone's CI and never again.
"""

import random
import unittest
from decimal import Decimal

from sms_msgs_scraper.domain.money import Money, MoneyError

SEED = 20260829


class TestSummationIsExact(unittest.TestCase):
    def test_repeated_cents_never_drift(self):
        """The textbook float failure, at every length that matters."""
        for count in (3, 10, 100, 1000, 4665):
            with self.subTest(count=count):
                total = sum(Money.parse("PKR", "0.01") for _ in range(count))

                self.assertEqual(total.amount, Decimal(count) / 100)

    def test_the_classic_tenths_case(self):
        """0.1 + 0.2 == 0.3 is false in binary floating point."""
        total = Money.parse("PKR", "0.10") + Money.parse("PKR", "0.20")

        self.assertEqual(total.amount, Decimal("0.30"))
        self.assertEqual(total.formatted(), "0.30")

    def test_summation_is_order_independent(self):
        """Float addition is not associative; decimal addition at fixed scale is.

        A monthly total must not depend on the order transactions happened to
        sit in the file.
        """
        rng = random.Random(SEED)
        tokens = [
            f"{rng.randrange(1, 5_000_00) / 100:.2f}" for _ in range(500)
        ]
        amounts = [Money.parse("PKR", token) for token in tokens]

        forwards = sum(amounts)
        backwards = sum(reversed(amounts))
        shuffled = list(amounts)
        rng.shuffle(shuffled)

        self.assertEqual(forwards, backwards)
        self.assertEqual(forwards, sum(shuffled))

    def test_a_large_realistic_total_stays_exact(self):
        """The reference corpus totals ~37 million rupees across 875 debits."""
        rng = random.Random(SEED)
        tokens = [f"{rng.randrange(100, 10_000_000) / 100:.2f}" for _ in range(875)]

        expected = sum(Decimal(token) for token in tokens)
        total = sum(Money.parse("PKR", token) for token in tokens)

        self.assertEqual(total.amount, expected)

    def test_adding_zero_changes_nothing(self):
        rng = random.Random(SEED)

        for _ in range(200):
            token = f"{rng.randrange(1, 1_000_000) / 100:.2f}"
            money = Money.parse("PKR", token)

            self.assertEqual(money + Money.zero("PKR"), money)

    def test_subtracting_a_value_from_itself_is_zero(self):
        money = Money.parse("USD", "1234.56")

        self.assertEqual((money - money).amount, Decimal("0.00"))


class TestScaleBoundaries(unittest.TestCase):
    def test_the_maximum_allowed_scale_is_accepted(self):
        for token in ("1", "1.0", "1.00", "0.01", "0.10"):
            with self.subTest(token=token):
                self.assertIsNotNone(Money.parse("PKR", token))

    def test_one_place_beyond_the_scale_is_rejected(self):
        for token in ("1.000", "0.001", "12345.678", "0.005"):
            with self.subTest(token=token), self.assertRaises(MoneyError):
                Money.parse("PKR", token)

    def test_rejection_happens_rather_than_rounding(self):
        """A half-way value is where a silent round is most visible.

        `,.2f` on 0.005 would have produced either "0.00" or "0.01" depending on
        the rounding mode, and either way would have looked like a real amount.
        """
        with self.assertRaises(MoneyError):
            Money.parse("PKR", "0.005")

    def test_a_sum_never_grows_its_scale(self):
        rng = random.Random(SEED)

        for _ in range(200):
            left = Money.parse("PKR", f"{rng.randrange(0, 100_000) / 100:.2f}")
            right = Money.parse("PKR", f"{rng.randrange(0, 100_000) / 100:.2f}")

            total = left + right

            self.assertLessEqual(-total.amount.as_tuple().exponent, 2)


class TestFormattingIsLossless(unittest.TestCase):
    def test_formatting_and_reparsing_round_trips(self):
        """Formatting is presentation only; it must not be able to lose a value."""
        rng = random.Random(SEED)

        for _ in range(500):
            token = f"{rng.randrange(1, 100_000_000) / 100:.2f}"
            money = Money.parse("PKR", token)

            reparsed = Money.parse("PKR", money.formatted())

            self.assertEqual(reparsed.amount, money.amount)

    def test_thousands_grouping_round_trips_at_every_magnitude(self):
        for exponent in range(0, 10):
            token = f"{10 ** exponent}.99"
            with self.subTest(token=token):
                money = Money.parse("PKR", token)

                self.assertEqual(
                    Money.parse("PKR", money.formatted()).amount, money.amount
                )


class TestGrammarBoundaries(unittest.TestCase):
    def test_valid_and_invalid_groupings_are_separated_cleanly(self):
        valid = ["0", "1", "999", "1,000", "10,000", "100,000", "1,000,000",
                 "1,234,567.89", "999999999", "0.99"]
        invalid = ["1,0", "1,00", "1,0000", "1,2,3", "10,00,000", ",1", "1,",
                   "1,,000", "1 000", "1_000"]

        for token in valid:
            with self.subTest(valid=token):
                self.assertIsNotNone(Money.parse("PKR", token))

        for token in invalid:
            with self.subTest(invalid=token), self.assertRaises(MoneyError):
                Money.parse("PKR", token)

    def test_nothing_that_python_would_coerce_slips_through(self):
        """Decimal() itself accepts several of these; the grammar must not."""
        for token in ("1e5", "Infinity", "-Infinity", "NaN", "+12", "0x10", "1_0"):
            with self.subTest(token=token), self.assertRaises(MoneyError):
                Money.parse("PKR", token)

    def test_a_whitespace_padded_token_is_accepted_after_stripping(self):
        self.assertEqual(Money.parse("PKR", "  25.50  ").amount, Decimal("25.50"))


class TestCurrencyIsolation(unittest.TestCase):
    def test_currencies_never_mix_however_they_are_combined(self):
        rng = random.Random(SEED)
        currencies = ("PKR", "USD", "CAD", "AED")

        for _ in range(100):
            left, right = rng.sample(currencies, 2)
            a = Money.parse(left, "10.00")
            b = Money.parse(right, "10.00")

            with self.assertRaises(MoneyError):
                a + b

    def test_equal_amounts_in_different_currencies_are_not_equal(self):
        self.assertNotEqual(
            Money.parse("PKR", "100.00"), Money.parse("USD", "100.00")
        )


if __name__ == "__main__":
    unittest.main()
