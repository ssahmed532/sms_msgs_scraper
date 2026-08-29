"""Tests for the one place a bank's identity is written down.

The registry exists because that identity used to live in five places -- the
parser's short-code constant, the routing chain, the CLI's `--bank` choices, the
summary rows and the documentation -- and Standard Chartered's second sender was
written in four of them. These tests are mostly about that: everything the tool
knows about banks must be *derived* from these objects, so there is nowhere left
for a fifth copy to disagree.
"""

import unittest

from sms_msgs_scraper.domain.bank import (
    BankRegistry,
    BankSpec,
    Capability,
    DuplicateSenderError,
    TxnKind,
)
from sms_msgs_scraper.parser.registry import BANK_SPECS, REGISTRY


class TestSenderRouting(unittest.TestCase):
    def test_every_known_sender_resolves_to_its_bank(self):
        expected = {
            "4250": "HBL",
            "14250": "HBL",
            "8756": "FBL",
            "7220": "SCB",
            "9220": "SCB",
            "8079": "MEZN",
            "9779": "MEZN",
        }

        for sender, bankId in expected.items():
            with self.subTest(sender=sender):
                self.assertEqual(REGISTRY.specFor(sender).id, bankId)

    def test_scb_declares_both_of_its_senders(self):
        """H1. `9220` sent 18 transactions that were never counted."""
        self.assertEqual(set(REGISTRY.byId("SCB").senderCodes), {"7220", "9220"})

    def test_hbl_declares_both_of_its_senders(self):
        """HBL re-homed its alerts from 4250 to 14250 in mid-January 2025.

        The body format was byte-for-byte unchanged, so dropping either code
        silently loses part of the history with nothing logged.
        """
        self.assertEqual(set(REGISTRY.byId("HBL").senderCodes), {"4250", "14250"})

    def test_an_unregistered_sender_resolves_to_nothing(self):
        self.assertIsNone(REGISTRY.specFor("99999"))
        self.assertFalse(REGISTRY.isRegistered("99999"))


class TestRegistryIntegrity(unittest.TestCase):
    def test_two_banks_claiming_one_sender_fails_loudly(self):
        """Otherwise routing is decided by declaration order, silently."""
        clashing = (
            BankSpec(
                id="AAA",
                name="Bank A",
                senderCodes=("1234",),
                txnKind=TxnKind.CREDIT_CARD,
                signal=lambda record: False,
                extract=lambda record: None,
            ),
            BankSpec(
                id="BBB",
                name="Bank B",
                senderCodes=("1234",),
                txnKind=TxnKind.CREDIT_CARD,
                signal=lambda record: False,
                extract=lambda record: None,
            ),
        )

        with self.assertRaises(DuplicateSenderError):
            BankRegistry(clashing)

    def test_a_bank_declared_twice_fails_loudly(self):
        spec = BankSpec(
            id="AAA",
            name="Bank A",
            senderCodes=("1234",),
            txnKind=TxnKind.CREDIT_CARD,
            signal=lambda record: False,
            extract=lambda record: None,
        )

        with self.assertRaises(DuplicateSenderError):
            BankRegistry((spec, spec))

    def test_no_sender_is_declared_twice_in_the_real_registry(self):
        declared = [code for spec in BANK_SPECS for code in spec.senderCodes]

        self.assertEqual(len(declared), len(set(declared)))

    def test_every_spec_carries_a_signal_and_an_extractor(self):
        for spec in REGISTRY:
            with self.subTest(bank=spec.id):
                self.assertTrue(callable(spec.signal))
                self.assertTrue(callable(spec.extract))


class TestDerivedViews(unittest.TestCase):
    """Everything downstream must come from here rather than restate it."""

    def test_the_cc_bank_choices_are_derived(self):
        self.assertEqual(REGISTRY.ccBankIds, ("HBL", "FBL", "SCB"))

    def test_the_debit_banks_are_derived(self):
        self.assertEqual(REGISTRY.idsForKind(TxnKind.ACCOUNT_DEBIT), ("MEZN",))

    def test_the_full_sender_set_is_derived(self):
        self.assertEqual(
            REGISTRY.senderCodes,
            frozenset({"4250", "14250", "8756", "7220", "9220", "8079", "9779"}),
        )

    def test_the_skipped_bucket_name_is_derived(self):
        self.assertEqual(REGISTRY.byId("SCB").skippedBucket, "SCB_SKIPPED")


class TestCapabilities(unittest.TestCase):
    """Capabilities are observations about the message format, and each has a
    consumer."""

    def test_only_the_date_only_banks_lack_a_txn_time(self):
        """This is what decides whether a suppressed duplicate is ambiguous.

        HBL and SCB alerts carry a date but no time of day, so a second genuine
        identical purchase that day cannot be told from a retransmission. FBL
        and Meezan timestamp their alerts, so an identical body provably
        describes the same transaction.
        """
        withoutTime = {
            spec.id for spec in REGISTRY if not spec.has(Capability.TXN_TIME)
        }

        self.assertEqual(withoutTime, {"HBL", "SCB"})

    def test_only_the_banks_that_send_card_digits_declare_them(self):
        withDigits = {
            spec.id for spec in REGISTRY if spec.has(Capability.CARD_DIGITS)
        }

        self.assertEqual(withDigits, {"HBL", "SCB"})

    def test_meezan_is_the_only_bank_carrying_an_account_mask(self):
        withMask = {
            spec.id for spec in REGISTRY if spec.has(Capability.ACCOUNT_MASK)
        }

        self.assertEqual(withMask, {"MEZN"})


if __name__ == "__main__":
    unittest.main()
