import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from parser.mezn_sms_parser import MeznSmsParser
from pathlib import Path

from common import DEFAULT_TZ
from debit_txn import DebitTxnType
from sms_backup_file_parser import SmsBackupFileParser

# Real Meezan debit templates, with personal names replaced by synthetic ones.
# Merchant / ATM / branch names are kept as they appear in the corpus.
CARD_PURCHASE_MSG = (
    "PKR 3,251.00 charged at AL KARAM STUDIO for card used, from A/C "
    "xxxxxx5602 (KHAYABAN-E-SEHAR KHI) on 11-Dec-23 at 14:58 "
    "Balance: 13,171,500.59"
)
ATM_WITHDRAWAL_MSG = (
    "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from A/C "
    "xxxxxx5602 KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 19:42 "
    "Balance: PKR 13,776,380.62"
)
ACCOUNT_DEBIT_MSG = (
    "PKR 1,000.00 is debited as NAYAPAY - Load Wallet from your A/C "
    "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 20-Sep-23 at 09:45 "
    "Bal: PKR 13,775,380.62"
)
FUNDS_TRANSFER_MSG = (
    "PKR 63,100.00 sent to KARACHI GRAMMAR SCHOOL SCB-5504000000015496 "
    "from your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 19-Sep-23 at 10:01 "
    "Fee: Rs.63.10 Bal: PKR 13,874,889.12"
)
UPPERCASE_FUNDS_TRANSFER_MSG = (
    "PKR 5,000.00 SENT TO JOHN DOE MBL- 01130100000267 on 19-Sep-23 at 11:36 "
    "Bal: PKR 13,811,380.62"
)


class TestMeznSmsParser(unittest.TestCase):

    def _createMeznSms(
        self, body: str, shortCode: str = MeznSmsParser.MEZN_SHORT_CODES[0]
    ) -> ET.Element:
        sms = ET.Element("sms")
        sms.set("protocol", "0")
        sms.set("address", shortCode)
        sms.set("date", "1695138720000")
        sms.set("type", "1")
        sms.set("read", "1")
        sms.set("body", body)
        sms.set("readable_date", "Sep 19, 2023 7:42:00 PM")
        sms.set("contact_name", "Meezan shortcode")

        return sms

    def _parseBackup(self, msgElements: list) -> SmsBackupFileParser:
        """Write the given msg elements out as a backup XML file and run the
        parser over it end-to-end (loadFromSmsBackupFile + parseMessages).
        """
        root = ET.Element("smses")
        root.set("count", str(len(msgElements)))
        root.extend(msgElements)

        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupFilepath = Path(tmpDir.name) / "sms_backup.xml"
        ET.ElementTree(root).write(backupFilepath, encoding="utf-8")

        parser = SmsBackupFileParser()
        parser.loadFromSmsBackupFile(backupFilepath)
        parser.parseMessages()

        return parser

    #
    # identification
    #
    def test_mezn_sms_msg_identified(self):
        """Test method to verify that msgs from both Meezan short codes are
        identified as being from Meezan.
        """
        for shortCode in ("8079", "9779"):
            with self.subTest(shortCode=shortCode):
                sms = self._createMeznSms(ATM_WITHDRAWAL_MSG, shortCode=shortCode)
                self.assertTrue(MeznSmsParser.isSmsFromMezn(sms))

    def test_non_mezn_sms_msg_not_identified(self):
        """Test method to verify that a msg from another bank's short code is
        not mistaken for a Meezan msg.
        """
        sms = self._createMeznSms(ATM_WITHDRAWAL_MSG, shortCode="4250")

        self.assertFalse(MeznSmsParser.isSmsFromMezn(sms))

    def test_every_template_family_trips_the_txn_signal(self):
        """Test method to verify that the keyword signal accepts all five
        debit templates.
        """
        for body in (
            CARD_PURCHASE_MSG,
            ATM_WITHDRAWAL_MSG,
            ACCOUNT_DEBIT_MSG,
            FUNDS_TRANSFER_MSG,
            UPPERCASE_FUNDS_TRANSFER_MSG,
        ):
            with self.subTest(body=body[:40]):
                self.assertTrue(
                    MeznSmsParser.isMsgDebitTxn(self._createMeznSms(body))
                )

    #
    # extraction: one test per template family
    #
    def test_card_purchase_extraction(self):
        """Test method to verify extraction of a card purchase debit msg."""
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(CARD_PURCHASE_MSG)
        )

        self.assertEqual(txn.txnType, DebitTxnType.CARD_PURCHASE)
        self.assertEqual(txn.vendor, "AL KARAM STUDIO")
        self.assertEqual(txn.amountTuple.currency, "PKR")
        self.assertEqual(txn.amountTuple.amount, 3251.00)
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(txn.bank, "MEZN")
        self.assertEqual(
            txn.date, datetime(2023, 12, 11, 14, 58, tzinfo=DEFAULT_TZ)
        )

    def test_atm_withdrawal_extraction(self):
        """Test method to verify extraction of an ATM cash withdrawal msg."""
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(ATM_WITHDRAWAL_MSG)
        )

        self.assertEqual(txn.txnType, DebitTxnType.ATM_WITHDRAWAL)
        self.assertEqual(txn.vendor, "KHAYABAN-E-SEHAR KHI")
        self.assertEqual(txn.amountTuple.amount, 35000.00)
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(
            txn.date, datetime(2023, 9, 19, 19, 42, tzinfo=DEFAULT_TZ)
        )

    def test_account_debit_extraction(self):
        """Test method to verify extraction of an 'is debited as' bill
        payment msg.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(ACCOUNT_DEBIT_MSG)
        )

        self.assertEqual(txn.txnType, DebitTxnType.ACCOUNT_DEBIT)
        self.assertEqual(txn.vendor, "NAYAPAY - Load Wallet")
        self.assertEqual(txn.amountTuple.amount, 1000.00)
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(
            txn.date, datetime(2023, 9, 20, 9, 45, tzinfo=DEFAULT_TZ)
        )

    def test_funds_transfer_extraction(self):
        """Test method to verify extraction of a lowercase 'sent to' funds
        transfer msg carrying an 'of {branch}' clause.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(FUNDS_TRANSFER_MSG)
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(
            txn.vendor, "KARACHI GRAMMAR SCHOOL SCB-5504000000015496"
        )
        self.assertEqual(txn.amountTuple.amount, 63100.00)
        self.assertEqual(txn.acctMask, "xxxxxx5602")
        self.assertEqual(
            txn.date, datetime(2023, 9, 19, 10, 1, tzinfo=DEFAULT_TZ)
        )

    def test_uppercase_funds_transfer_extraction(self):
        """Test method to verify extraction of an uppercase 'SENT TO' funds
        transfer msg, which carries no account clause at all.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(UPPERCASE_FUNDS_TRANSFER_MSG)
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "JOHN DOE MBL- 01130100000267")
        self.assertEqual(txn.amountTuple.amount, 5000.00)
        self.assertEqual(txn.acctMask, "")
        self.assertEqual(
            txn.date, datetime(2023, 9, 19, 11, 36, tzinfo=DEFAULT_TZ)
        )

    #
    # date variants
    #
    def test_two_digit_year_date(self):
        """Test method to verify that the original 2-digit-year date format
        parses (and is not read as year 24).
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from "
                "A/C xxxxxx5602 KHAYABAN-E-SEHAR KHI on 18-Feb-24 at 10:21 "
                "TID:720564"
            )
        )

        self.assertEqual(
            txn.date, datetime(2024, 2, 18, 10, 21, tzinfo=DEFAULT_TZ)
        )

    def test_four_digit_year_date(self):
        """Test method to verify that the 4-digit-year date format (which
        appeared around Apr 2025) parses.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 80,000.00 cash withdrawn from KHAYABAN-E-SEHAR KHI from "
                "A/C xxx5602 KHAYABAN-E-SEHAR KHI on 24-Apr-2025 at 16:14 "
                "TID:026128 UAN 021111331331"
            )
        )

        self.assertEqual(
            txn.date, datetime(2025, 4, 24, 16, 14, tzinfo=DEFAULT_TZ)
        )
        self.assertEqual(txn.acctMask, "xxx5602")

    def test_single_digit_day_and_hour_date(self):
        """Test method to verify that a single-digit day and a single-digit
        hour both parse.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 5,000.00 sent to JOHN DOE NAYAPAY-PK68NAYA12345032 from "
                "your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 3-Apr-24 at "
                "9:05 Bal: PKR 13,770,375.62"
            )
        )

        self.assertEqual(
            txn.date, datetime(2024, 4, 3, 9, 5, tzinfo=DEFAULT_TZ)
        )

    #
    # funds transfer variants
    #
    def test_funds_transfer_without_branch_clause(self):
        """Test method to verify that a lowercase transfer msg with no
        'of {branch}' clause still yields the payee and the account mask.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 18,000.00 sent to JOHN DOE (MBL AC 0113xxxxxx0267) from "
                "your A/C xxxxxx5602 on 16-Nov-23 at 22:15 "
                "Bal: PKR 13,114,544.35"
            )
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "JOHN DOE (MBL AC 0113xxxxxx0267)")
        self.assertEqual(txn.acctMask, "xxxxxx5602")

    def test_uppercase_funds_transfer_with_mbl_payee(self):
        """Test method to verify the real '(MBL AC …)' payee form, which the
        bank sends with an uppercase 'SENT TO'.

        Because the lowercase transfer template is deliberately
        case-sensitive, this body falls through to the uppercase template,
        whose payee capture runs all the way to the date — so the trailing
        account clause ends up inside the payee and acctMask stays empty.
        That is the behaviour the corpus ground truth (410 transfers /
        189 unique vendors) was measured against, so it is pinned here rather
        than silently "fixed".
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 18,000.00 SENT TO JOHN DOE (MBL AC 0113xxxxxx0267) from "
                "your A/C xxxxxx5602 on 16-Nov-23 at 22:15 "
                "Bal: PKR 13,114,544.35"
            )
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(
            txn.vendor,
            "JOHN DOE (MBL AC 0113xxxxxx0267) from your A/C xxxxxx5602",
        )
        self.assertEqual(txn.acctMask, "")

    def test_raast_funds_transfer_account_spelling(self):
        """Test method to verify the RAAST 'AC#' spelling of the account
        clause parses like the regular 'A/C' one.
        """
        txn = MeznSmsParser.extractDetailsFromTxnMsg(
            self._createMeznSms(
                "PKR 20,000.00 sent to L.FOODS AC# PK......2413 as RAAST "
                "payment from your AC# xxxxxx5602 of KHAYABAN-E-SEHAR KHI on "
                "03-Oct-23 at 20:16 Bal: PKR 14,446,787.21"
            )
        )

        self.assertEqual(txn.txnType, DebitTxnType.FUNDS_TRANSFER)
        self.assertEqual(txn.vendor, "L.FOODS AC# PK......2413 as RAAST payment")
        self.assertEqual(txn.acctMask, "xxxxxx5602")

    #
    # trailing junk after the date varies a lot and must not matter
    #
    def test_trailing_junk_tolerated(self):
        """Test method to verify that the assorted tails Meezan appends after
        the txn date/time do not prevent a match.
        """
        for tail in (
            "TID:720564",
            "TID:633644 UAN 021111331331",
            "Fee: Rs.5.00 Bal: PKR 13,770,375.62",
            "Bal: PKR 13,770,375.62",
            ".",
        ):
            with self.subTest(tail=tail):
                txn = MeznSmsParser.extractDetailsFromTxnMsg(
                    self._createMeznSms(
                        "PKR 35,000.00 cash withdrawn from KHAYABAN-E-SEHAR "
                        "KHI from A/C xxxxxx5602 KHAYABAN-E-SEHAR KHI on "
                        f"19-Sep-23 at 19:42 {tail}"
                    )
                )

                self.assertIsNotNone(txn)
                self.assertEqual(txn.txnType, DebitTxnType.ATM_WITHDRAWAL)
                self.assertEqual(txn.vendor, "KHAYABAN-E-SEHAR KHI")
                self.assertEqual(txn.amountTuple.amount, 35000.00)

    #
    # negatives: msgs that must NOT be treated as debit txns
    #
    def test_credit_msg_not_debit_txn(self):
        """Test method to verify that a credit (reversal) msg is rejected."""
        sms = self._createMeznSms(
            "PKR 250.00 is credited as Rev of Late Pymnt chrg Jun23 SSGC in "
            "your A/C xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 14-Nov-23 at "
            "17:43. Bal: PKR 13,312,669.35"
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    def test_funds_received_msg_not_debit_txn(self):
        """Test method to verify that an incoming transfer is rejected."""
        sms = self._createMeznSms(
            "PKR 3,500.00 received from FINCA-xxx2801 to A/C xxx5602 of "
            "KHAYABAN-E-SEHAR KHI on 31-Aug-2025 at 12:44"
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    def test_cheque_clearing_msg_not_debit_txn(self):
        """Test method to verify that cheque inward clearing — deliberately
        out of scope, to avoid double counting against the separate 'cheque
        received' notice — is rejected.
        """
        sms = self._createMeznSms(
            "PKR 12,000.00 INWARD CLEARING VIA CHEQUE NO: 64181500 at "
            "KHAYABAN-E-SEHAR KHI against A/C xxxxxx5602 on 28-Sep-23 at "
            "11:27 Bal: PKR 13,541,842.12"
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    def test_estatement_notice_not_debit_txn(self):
        """Test method to verify that the e-statement notice is rejected even
        though it contains the words 'sent to'.
        """
        sms = self._createMeznSms(
            "Dear JOHN DOE, your passcode protected Meezan Bank e-statement "
            "has been sent to your registered email address: "
            "john.doe@example.com"
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    def test_bill_reminder_not_debit_txn(self):
        """Test method to verify that a bill-available reminder is rejected."""
        sms = self._createMeznSms(
            "Your KE bill saved as KE upstairs bill for Rs 30,018.00 is "
            "available for payment. Pay now via Meezan Mobile App: "
            "bit.ly/2NzudXf"
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    def test_card_dispatch_notice_not_debit_txn(self):
        """Test method to verify that a card dispatch notice is rejected.

        Pins the amount-head anchor of the txn signal: the body contains
        'sent to' but no leading amount.
        """
        sms = self._createMeznSms(
            "Dear JOHN DOE, your Meezan Bank Debit Card has been sent to your "
            "registered mailing address."
        )

        self.assertFalse(MeznSmsParser.isMsgDebitTxn(sms))

    #
    # the signal is deliberately looser than the templates: drift tripwire
    #
    def test_drifted_template_signals_but_does_not_extract(self):
        """Test method to verify that a changed Meezan template still trips
        the keyword signal (so it gets counted+warned as skipped) while
        failing extraction.
        """
        sms = self._createMeznSms(
            "PKR 5,000.00 sent to JOHN DOE via SomeNewChannel ref 12345 "
            "dated 19/Sep/2023 20:15"
        )

        self.assertTrue(MeznSmsParser.isMsgDebitTxn(sms))
        self.assertIsNone(MeznSmsParser.extractDetailsFromTxnMsg(sms))

    def test_impossible_date_signals_but_does_not_extract(self):
        """Test method to verify that a date the regex accepts but strptime
        rejects (31-Feb-25) is skipped rather than raising.
        """
        sms = self._createMeznSms(
            "PKR 5,000.00 sent to JOHN DOE MBL-01130100000267 from your A/C "
            "xxxxxx5602 of KHAYABAN-E-SEHAR KHI on 31-Feb-25 at 10:01 "
            "Bal: PKR 1,000.00"
        )

        self.assertTrue(MeznSmsParser.isMsgDebitTxn(sms))
        self.assertIsNone(MeznSmsParser.extractDetailsFromTxnMsg(sms))

    #
    # end-to-end, through SmsBackupFileParser
    #
    def test_all_template_families_parsed_end_to_end(self):
        """Test method to verify that one msg per template family makes it
        from the backup file into debitTxns with the right txn type.
        """
        parser = self._parseBackup(
            [
                self._createMeznSms(CARD_PURCHASE_MSG),
                self._createMeznSms(ATM_WITHDRAWAL_MSG),
                self._createMeznSms(ACCOUNT_DEBIT_MSG),
                self._createMeznSms(FUNDS_TRANSFER_MSG),
                self._createMeznSms(UPPERCASE_FUNDS_TRANSFER_MSG),
            ]
        )

        self.assertEqual(len(parser.debitTxns), 5)
        self.assertEqual(
            Counter(txn.txnType for txn in parser.debitTxns),
            Counter(
                {
                    DebitTxnType.CARD_PURCHASE: 1,
                    DebitTxnType.ATM_WITHDRAWAL: 1,
                    DebitTxnType.ACCOUNT_DEBIT: 1,
                    DebitTxnType.FUNDS_TRANSFER: 2,
                }
            ),
        )
        self.assertEqual(parser.msgCounts["MEZN"], 5)
        self.assertEqual(parser.msgCounts["MEZN_SKIPPED"], 0)
        # debits must not leak into the credit card stores
        self.assertEqual(parser.ccTxns, [])
        self.assertEqual(parser.ccVendors, set())
        self.assertIn("AL KARAM STUDIO", parser.debitVendors)

    def test_drifted_template_msg_counted_as_skipped_end_to_end(self):
        """Test method to verify that a msg that trips the signal but matches
        no template is counted as skipped instead of vanishing silently.
        """
        parser = self._parseBackup(
            [
                self._createMeznSms(
                    "PKR 5,000.00 sent to JOHN DOE via SomeNewChannel ref "
                    "12345 dated 19/Sep/2023 20:15"
                )
            ]
        )

        self.assertEqual(parser.debitTxns, [])
        self.assertEqual(parser.debitVendors, set())
        self.assertEqual(parser.msgCounts["MEZN"], 1)
        self.assertEqual(parser.msgCounts["MEZN_SKIPPED"], 1)


if __name__ == "__main__":
    unittest.main()
