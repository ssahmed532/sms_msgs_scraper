"""Tests for `backup_info`: the file metadata, the message stats behind it,
and the parity between what a person is shown and what a program is handed.

Two properties here are worth more than the rest.

**The sender breakdown must refine the routing counts exactly.** It is counted
in the same pass, off the bucket each message was actually routed into, so a
short code that stops being claimed cannot show up as a bank total that still
adds up. That is the shape of both of this project's worst bugs -- Standard
Chartered's undeclared `9220` and HBL's move from `4250` to `14250` -- and the
identity is the thing that would surface the next one.

**An unrecognized sender is counted and never named.** Those strings are
personal phone numbers. The report has nowhere to put one, and no output format
can print one.
"""

import csv
import hashlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from click.testing import CliRunner

from sms_msgs_scraper.render import machine
from sms_msgs_scraper.render.console_ui import humanBytes
from sms_msgs_scraper.sms_backup_file_parser import (
    BackupFileInfo,
    SmsBackupFileParser,
)
from sms_msgs_scraper.sms_txn_query_tool import backup_info, cli

HBL_TXN_BODY = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-25,170.49 on 01/Oct/2023."
)
HBL_NEW_CODE_BODY = (
    "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
    "at IMTIAZ SUPER MARKET for PKR-1,500.00 on 20/Jan/2025."
)
FBL_TXN_BODY = (
    "Dear JOHN DOE, your FBL Card  has been charged for USD 39.99 on "
    "20-Sep-23 01:17:16 PM at AMAZON.COM             SEATTLE        US."
)
MEZN_ATM_BODY = (
    "PKR 20,000.00 cash withdrawn from MEEZAN ATM DHA PHASE 6 from A/C "
    "xxxxxx5602 KARACHI BRANCH on 15-Jun-24 at 09:05 Bal: PKR 1,234.00"
)
SCB_TRUNCATED_BODY = "Dear Client, PKR 281.00 have been paid at NECOS NATURAL STORE "

# A sender no bank claims, standing in for the personal phone number a real
# backup is mostly made of. No output may ever contain it.
PRIVATE_SENDER = "923001234567"


class BackupInfoTestCase(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner(env={"COLUMNS": "200"})

    def _sms(self, address, body, readableDate="Oct 2, 2023 9:57:06 PM"):
        sms = ET.Element("sms")
        sms.set("address", address)
        sms.set("body", body)
        sms.set("readable_date", readableDate)

        return sms

    def _backup(self, msgElements, declared=None) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        backupPath = Path(tmpDir.name) / "sms_backup.xml"

        root = ET.Element("smses")
        root.set(
            "count",
            str(len(msgElements) if declared is None else declared),
        )
        root.extend(msgElements)
        ET.ElementTree(root).write(backupPath, encoding="utf-8")

        return backupPath

    def _standardBackup(self) -> Path:
        """Two HBL short codes, one FBL, one Meezan, one skip, one duplicate,
        and one message from a sender nothing claims.
        """
        return self._backup(
            [
                self._sms("4250", HBL_TXN_BODY),
                self._sms("14250", HBL_NEW_CODE_BODY),
                self._sms("8756", FBL_TXN_BODY),
                self._sms("8079", MEZN_ATM_BODY),
                self._sms("7220", SCB_TRUNCATED_BODY),
                self._sms(PRIVATE_SENDER, "dinner at 8?"),
                # an exact repeat of the first: suppressed, and ambiguous
                # because HBL alerts carry no time of day
                self._sms("4250", HBL_TXN_BODY, "Oct 2, 2023 9:58:00 PM"),
            ]
        )

    def run_cli(self, args):
        return self.runner.invoke(cli, args, catch_exceptions=False)

    def rowsFrom(self, backupPath, extra=()):
        result = self.run_cli(
            ["--quiet", "--format", "json", str(backupPath), "backup_info", *extra]
        )
        self.assertEqual(result.exit_code, 0, result.output)

        return json.loads(result.stdout)["rows"]

    def valueOf(self, rows, section, field):
        for row in rows:
            if row["section"] == section and row["field"] == field:
                return row["value"]

        self.fail(f"no {section}/{field} row in {rows}")


class TestCommandWiring(BackupInfoTestCase):
    def test_the_documented_underscore_name_is_the_real_name(self):
        self.assertIs(cli.commands["backup_info"], backup_info)

    def test_it_needs_no_options_at_all(self):
        result = self.run_cli([str(self._standardBackup()), "backup_info"])

        self.assertEqual(result.exit_code, 0, result.output)

    def test_help_costs_no_parse(self):
        """The deferred-parse contract, checked on the newest command.

        The parse summary is printed by `AppContext.report()`, so its absence
        is the evidence that nothing was read to answer `--help`.
        """
        result = self.run_cli(
            [str(self._standardBackup()), "backup_info", "--help"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--verbose", result.stdout)
        self.assertNotIn("Messages parsed", result.stderr)


class TestFileMetadata(BackupInfoTestCase):
    def test_the_digest_is_the_sha256_of_the_file(self):
        """Not a digest of something else that happens to be 64 hex chars.

        This project pins its reference numbers to one backup by its SHA-256,
        so the number printed here has to be the one those were derived
        against.
        """
        backupPath = self._standardBackup()
        expected = hashlib.sha256(backupPath.read_bytes()).hexdigest()

        rows = self.rowsFrom(backupPath)

        self.assertEqual(self.valueOf(rows, "file", "sha256"), expected)

    def test_the_size_is_the_exact_byte_count_with_the_human_one_as_a_note(self):
        backupPath = self._standardBackup()

        rows = self.rowsFrom(backupPath)

        self.assertEqual(
            self.valueOf(rows, "file", "sizeBytes"), backupPath.stat().st_size
        )
        sizeRow = next(
            row for row in rows if row["field"] == "sizeBytes"
        )
        self.assertTrue(sizeRow["note"])

    def test_the_modified_time_is_stamped_asia_karachi(self):
        info = BackupFileInfo.forPath(self._standardBackup())

        self.assertIsNotNone(info.modifiedAt.tzinfo)
        self.assertEqual(str(info.modifiedAt.tzinfo), "Asia/Karachi")

    def test_a_count_reaches_a_program_as_a_number_not_a_grouped_string(self):
        rows = self.rowsFrom(self._standardBackup())

        self.assertIsInstance(self.valueOf(rows, "envelope", "declared"), int)
        self.assertIsInstance(self.valueOf(rows, "messages", "all"), int)


class TestHumanBytes(unittest.TestCase):
    """The gloss beside the exact byte count.

    Tested directly rather than through a file, because producing a gibibyte
    to check the GiB branch would mean writing one.
    """

    def test_a_small_file_keeps_its_exact_count(self):
        self.assertEqual(humanBytes(0), "0 bytes")
        self.assertEqual(humanBytes(1023), "1,023 bytes")

    def test_it_steps_up_a_unit_at_a_time(self):
        self.assertEqual(humanBytes(1024), "1.0 KiB")
        self.assertEqual(humanBytes(7802), "7.6 KiB")
        self.assertEqual(humanBytes(2_621_440), "2.5 MiB")
        self.assertEqual(humanBytes(3 * 1024**3), "3.0 GiB")

    def test_it_stops_at_gibibytes_rather_than_inventing_a_unit(self):
        """`BackupLimits.maxBytes` refuses anything over 512 MiB, so nothing
        past GiB can reach this from a real run.
        """
        self.assertEqual(humanBytes(5 * 1024**4), "5,120.0 GiB")


class TestEnvelopeAndMessages(BackupInfoTestCase):
    def test_the_envelope_counts_are_what_the_file_holds(self):
        rows = self.rowsFrom(self._standardBackup())

        self.assertEqual(self.valueOf(rows, "envelope", "declared"), 7)
        self.assertEqual(self.valueOf(rows, "envelope", "actual"), 7)
        self.assertEqual(self.valueOf(rows, "envelope", "sms"), 7)
        self.assertEqual(self.valueOf(rows, "envelope", "mms"), 0)
        self.assertEqual(self.valueOf(rows, "envelope", "invalid"), 0)

    def test_a_declared_count_that_does_not_match_is_noted_not_hidden(self):
        backupPath = self._backup(
            [self._sms("4250", HBL_TXN_BODY)], declared=99
        )

        rows = self.rowsFrom(backupPath)
        actualRow = next(
            row
            for row in rows
            if row["section"] == "envelope" and row["field"] == "actual"
        )

        self.assertEqual(self.valueOf(rows, "envelope", "declared"), 99)
        self.assertEqual(actualRow["value"], 1)
        self.assertIn("does not match", actualRow["note"])

    def test_the_message_rows_split_all_into_its_three_parts(self):
        """`all` is the population; the other three partition it, duplicates
        included. A backup whose parts do not add up has been miscounted.
        """
        rows = self.rowsFrom(self._standardBackup())

        total = self.valueOf(rows, "messages", "all")
        fromBank = self.valueOf(rows, "messages", "fromBank")
        fromOther = self.valueOf(rows, "messages", "fromOtherSender")
        duplicates = self.valueOf(rows, "messages", "duplicatesSuppressed")

        self.assertEqual(total, 7)
        self.assertEqual(fromOther, 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(fromBank + fromOther + duplicates, total)

    def test_the_transaction_span_is_the_first_and_last_txn_dates(self):
        rows = self.rowsFrom(self._standardBackup())

        self.assertTrue(
            self.valueOf(rows, "transactions", "firstTxnDate").startswith("2023-09-20")
        )
        self.assertTrue(
            self.valueOf(rows, "transactions", "lastTxnDate").startswith("2025-01-20")
        )
        self.assertEqual(self.valueOf(rows, "transactions", "monthsWithTxns"), 4)

    def test_a_backup_with_no_transactions_reports_no_span_rather_than_a_date(self):
        """An invented placeholder date is a value a consumer could compare
        against; an empty string is not.
        """
        backupPath = self._backup([self._sms(PRIVATE_SENDER, "dinner at 8?")])

        rows = self.rowsFrom(backupPath)

        self.assertEqual(self.valueOf(rows, "transactions", "firstTxnDate"), "")
        self.assertEqual(self.valueOf(rows, "transactions", "lastTxnDate"), "")
        self.assertEqual(self.valueOf(rows, "transactions", "monthsWithTxns"), 0)


class TestVerboseStats(BackupInfoTestCase):
    def test_the_sender_breakdown_is_verbose_only(self):
        backupPath = self._standardBackup()
        plain = self.rowsFrom(backupPath)
        verbose = self.rowsFrom(backupPath, extra=["--verbose"])

        self.assertEqual([row for row in plain if row["section"] == "senders"], [])
        self.assertTrue([row for row in verbose if row["section"] == "senders"])

    def test_verbose_adds_breakdowns_and_changes_no_shared_value(self):
        """`--verbose` refines; it never restates a count differently."""
        backupPath = self._standardBackup()
        plain = self.rowsFrom(backupPath)
        verbose = self.rowsFrom(backupPath, extra=["--verbose"])

        self.assertEqual(verbose[: len(plain)], plain)

    def test_every_declared_short_code_gets_a_row_even_when_it_sent_nothing(self):
        """A zero is a finding: it says a code this build routes has gone
        quiet, which is what a bank re-homing its alerts looks like.
        """
        rows = self.rowsFrom(self._standardBackup(), extra=["--verbose"])
        senders = {
            row["field"]: row["value"]
            for row in rows
            if row["section"] == "senders"
        }

        self.assertEqual(senders["4250"], 1)
        self.assertEqual(senders["14250"], 1)
        self.assertEqual(senders["8756"], 1)
        self.assertEqual(senders["8079"], 1)
        self.assertEqual(senders["7220"], 1)
        # declared, and silent in this backup
        self.assertEqual(senders["9220"], 0)
        self.assertEqual(senders["9779"], 0)

    def test_parse_failures_are_broken_down_by_bank_and_reason(self):
        rows = self.rowsFrom(self._standardBackup(), extra=["--verbose"])
        failures = [row for row in rows if row["section"] == "parseFailures"]

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["field"], "SCB")
        self.assertEqual(failures[0]["value"], 1)
        self.assertEqual(failures[0]["note"], "no_template_match")

    def test_a_clean_run_shows_no_parse_failures_table_at_all(self):
        """An empty failures table reads as a table that failed to populate
        rather than as a run with nothing to report.
        """
        clean = self._backup([self._sms("4250", HBL_TXN_BODY)])

        result = self.run_cli(["--quiet", str(clean), "backup_info", "--verbose"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Senders", result.stdout)
        self.assertIn("Duplicates", result.stdout)
        self.assertNotIn("Parse failures", result.stdout)

    def test_the_duplicate_rows_say_how_much_was_a_judgement_call(self):
        rows = self.rowsFrom(self._standardBackup(), extra=["--verbose"])

        self.assertEqual(self.valueOf(rows, "duplicates", "policy"), "exact")
        self.assertEqual(self.valueOf(rows, "duplicates", "suppressed"), 1)
        # the suppressed message is an HBL repeat, and HBL alerts carry a date
        # with no time of day
        self.assertEqual(self.valueOf(rows, "duplicates", "ambiguous"), 1)


class TestSenderCountsRefineTheRouting(BackupInfoTestCase):
    """The identity that makes the sender breakdown trustworthy.

    It is counted off the bucket each message was routed into rather than from
    a second registry lookup, so it cannot drift from the counts it breaks
    down. A build where it did would be one where a per-bank total no longer
    described the senders it claims to.
    """

    def _report(self):
        return SmsBackupFileParser().parse(self._standardBackup())

    def test_each_banks_sender_counts_sum_to_its_bucket(self):
        from sms_msgs_scraper.parser.registry import REGISTRY

        report = self._report()

        for spec in REGISTRY:
            with self.subTest(bank=spec.id):
                self.assertEqual(
                    report.messageStats.countsFor(spec.senderCodes),
                    report.count(spec.id),
                )

    def test_the_unknown_sender_messages_are_the_other_bucket(self):
        report = self._report()

        self.assertEqual(
            report.messageStats.unknownSenderMsgs, report.count("OTHER")
        )
        self.assertEqual(report.messageStats.unknownSenders, 1)

    def test_the_whole_breakdown_accounts_for_every_non_duplicate_message(self):
        report = self._report()
        stats = report.messageStats

        self.assertEqual(
            sum(stats.senderCounts.values()) + stats.unknownSenderMsgs,
            report.count("ALL") - report.count("DUP"),
        )

    def test_a_duplicate_is_counted_against_no_sender(self):
        """It was suppressed before routing, so it belongs to neither side of
        the breakdown -- which is why the identity above nets out `DUP`.
        """
        report = self._report()

        self.assertEqual(report.count("DUP"), 1)
        # both HBL codes sent one message each; the repeat added nothing
        self.assertEqual(report.messageStats.senderCounts["4250"], 1)


class TestPrivacy(BackupInfoTestCase):
    """An unrecognized sender is a personal phone number."""

    def test_the_report_never_carries_an_unrecognized_sender_string(self):
        report = SmsBackupFileParser().parse(self._standardBackup())

        self.assertNotIn(PRIVATE_SENDER, report.messageStats.senderCounts)
        self.assertEqual(report.messageStats.unknownSenders, 1)

    def test_no_output_format_prints_one(self):
        backupPath = self._standardBackup()

        for outputFormat in ("table", "json", "csv"):
            with self.subTest(format=outputFormat):
                result = self.run_cli(
                    [
                        "--format",
                        outputFormat,
                        str(backupPath),
                        "backup_info",
                        "--verbose",
                    ]
                )

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertNotIn(PRIVATE_SENDER, result.output)


class TestOutputContract(BackupInfoTestCase):
    def test_results_go_to_stdout_and_the_furniture_to_stderr(self):
        runner = CliRunner(env={"COLUMNS": "200"})

        result = runner.invoke(
            cli,
            [str(self._standardBackup()), "backup_info"],
            catch_exceptions=False,
        )

        self.assertIn("SHA-256", result.stdout)
        self.assertIn("sms_txn_query_tool", result.stderr)

    def test_the_csv_carries_the_declared_columns_and_nothing_else(self):
        result = self.run_cli(
            [
                "--quiet",
                "--format",
                "csv",
                str(self._standardBackup()),
                "backup_info",
                "--verbose",
            ]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(
            tuple(rows[0].keys()), machine.INFO_COLUMNS
        )
        self.assertTrue(rows)

    def test_the_table_and_the_machine_rows_describe_the_same_facts(self):
        """The two renderers are peers. An answer that differed between them
        would be a defect in whichever one someone happened to trust.
        """
        backupPath = self._standardBackup()
        rows = self.rowsFrom(backupPath, extra=["--verbose"])

        table = self.run_cli(
            ["--quiet", str(backupPath), "backup_info", "--verbose"]
        )
        self.assertEqual(table.exit_code, 0, table.output)

        for section, field in (
            ("file", "sha256"),
            ("envelope", "declared"),
            ("messages", "all"),
            ("transactions", "ccTxns"),
        ):
            with self.subTest(field=field):
                value = self.valueOf(rows, section, field)
                self.assertIn(str(value), table.stdout)


if __name__ == "__main__":
    unittest.main()
