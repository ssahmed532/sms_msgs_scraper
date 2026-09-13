"""The version must mean the same thing everywhere it is written down.

This tool follows semantic versioning, and the release number used to be
declared in **three** places that had to agree by hand -- the CLI's
`@click.version_option`, `[project].version` in `pyproject.toml`, and `uv.lock`.
They had already drifted apart once.

The CLI copy is now derived: it reads the installed package metadata rather than
restating a literal. That leaves two authorities, and both are pinned here. The
lockfile is the one that gets forgotten, because nothing about editing
`pyproject.toml` prompts you to re-run `uv lock`.
"""

import re
import tomllib
import unittest
from importlib.metadata import version as installedVersion
from pathlib import Path

from click.testing import CliRunner

from sms_msgs_scraper import __version__
from sms_msgs_scraper.sms_txn_query_tool import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LOCKFILE_PATH = REPO_ROOT / "uv.lock"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

PROJECT_NAME = "sms-msgs-scraper"

SEMVER_PTTRN = re.compile(r"\d+\.\d+\.\d+")

# A built wheel or sdist named in full, e.g. sms_msgs_scraper-2.2.0-py3-none-any.whl
DIST_FILENAME_PTTRN = re.compile(r"sms_msgs_scraper-\d+\.\d+\.\d+")


def projectVersion() -> str:
    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


class TestVersionSources(unittest.TestCase):
    def test_the_declared_version_is_semantic(self):
        self.assertRegex(projectVersion(), SEMVER_PTTRN)

    def test_the_package_metadata_matches_pyproject(self):
        """Fails when the environment is stale -- re-run `uv sync`."""
        self.assertEqual(installedVersion(PROJECT_NAME), projectVersion())

    def test_the_module_version_matches_pyproject(self):
        self.assertEqual(__version__, projectVersion())

    def test_the_cli_reports_the_same_version(self):
        # --version is an eager option, so it prints and exits before the
        # group callback runs and before anything would be parsed.
        result = CliRunner().invoke(cli, ["--version"])

        self.assertEqual(result.exit_code, 0)
        reported = re.search(r"version\s+(\S+)", result.output)
        self.assertIsNotNone(
            reported, f"unexpected --version output: {result.output!r}"
        )
        self.assertEqual(reported.group(1), projectVersion())

    def test_the_ci_workflow_names_no_version_of_its_own(self):
        """The third place the number used to drift, and the quietest.

        CI's installed-wheel smoke test named `sms_msgs_scraper-2.0.0-...whl`
        outright. Two releases later that file no longer existed, so the step
        could only fail -- and it would have failed complaining about a missing
        path rather than about a stale version, which is the kind of red build
        people learn to route around. The workflow now discovers the wheel it
        just built; this makes sure nobody writes the name back in.
        """
        workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertEqual(
            DIST_FILENAME_PTTRN.findall(workflow),
            [],
            "CI names a built artefact by version -- discover it instead",
        )

    def test_the_lockfile_matches_pyproject(self):
        """The one nothing prompts you to update.

        Editing `pyproject.toml` does not touch `uv.lock`, so a version bump
        that skipped `uv lock` leaves the lockfile pinning the previous release
        -- and `uv sync --locked` in CI would then refuse to install it.

        Only meaningful under `uv run --locked` (or `--frozen`, or with
        `UV_LOCKED=1` in the environment). A plain `uv run` re-locks a stale
        lockfile *before* launching the test suite, so this test then reads a
        lockfile that was repaired moments earlier and passes -- which is how
        it sat green in CI while being unable to fail. The test below pins the
        workflow to the form under which this one can.
        """
        with LOCKFILE_PATH.open("rb") as handle:
            lockfile = tomllib.load(handle)

        locked = [
            package
            for package in lockfile["package"]
            if package["name"] == PROJECT_NAME
        ]

        self.assertEqual(
            len(locked), 1, f"{PROJECT_NAME} should appear once in uv.lock"
        )
        self.assertEqual(
            locked[0]["version"],
            projectVersion(),
            "uv.lock is stale -- re-run `uv lock` after bumping the version",
        )


    def test_ci_runs_every_uv_command_with_the_lockfile_asserted(self):
        """Without this, the lockfile test above self-heals in CI.

        Reproduced on a scratch copy with `pyproject.toml` bumped and `uv.lock`
        left behind: `uv run --locked python -m unittest ...` fails the test as
        designed, while plain `uv run ...` rewrites `uv.lock` first and passes.
        `UV_LOCKED` on the job makes every `uv run` in it assert instead.
        """
        workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn('UV_LOCKED: "1"', workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertNotIn(
            "uv sync --frozen",
            workflow,
            "--frozen installs the lockfile without checking it against pyproject",
        )


class TestReleaseShape(unittest.TestCase):
    def test_this_release_is_a_minor_over_the_2_5_x_interface(self):
        """2.6.0, chosen by what the release does to an existing caller.

        2.0.0 was MAJOR because three things changed meaning: results moved to
        stdout while diagnostics moved to stderr, the tool gained an `sms-txn`
        entry point instead of a script path, and Standard Chartered's recovered
        transactions changed what every SCB total returns.

        2.1.0 was MINOR: it added `cc_spend_for_month` and took nothing away.

        2.1.1 was a PATCH: five modules moved into the subpackages they belong
        to and the bank registry split, with no command, option, default or
        output stream changed.

        2.2.0 was MINOR: vendor search (`--vendor`), canonical vendor names
        (`--canonical-vendors`) and the table behind them (`--vendor-map`),
        all opt-in.

        2.3.0 adds the aggregate-spend table under a *filtered* listing: when
        `list_all_cc_txns` or `list_all_debit_txns` is given a date range or a
        vendor needle, the table output also carries one exact total per
        currency for the matching transactions. MINOR rather than MAJOR because
        no existing invocation changes meaning: an unfiltered listing renders
        exactly what 2.2.0 rendered, no filter changes which rows come back,
        and the JSON and CSV row shapes -- the outputs a script would parse --
        are untouched in every case.

        2.4.0 adds `monthly_vendor_chart` and its `--group-by` option: stacked
        monthly bars over credit card transactions and account debits together.
        MINOR because it is purely additive -- a new command, reachable only by
        naming it. Every existing command, option, default and output stream is
        exactly what 2.3.0 shipped, and the new command's own JSON and CSV rows
        are a new shape rather than a changed one.

        2.5.0 adds `backup_info` and its `--verbose` flag: the file's size,
        digest and modification time, its envelope accounting, where its
        messages were routed, and -- under `--verbose` -- the per-sender,
        per-skip-reason and per-duplicate breakdowns behind those counts.
        MINOR for the same reason 2.4.0 was: purely additive, reachable only by
        naming it, with a JSON and CSV shape of its own that no existing
        consumer was reading.

        The `ParseReport` gained a `messageStats` field to carry the sender
        counts, and its schema version went 1 -> 2. That is not part of the
        release number: no command reads or writes a serialised report, so no
        caller of this tool can observe it.

        2.5.1 is a PATCH: five corrections and no new capability. Chart axis
        ticks under 10k keep a decimal so 1,500 is no longer labelled `2k`; a
        month equal to the one before shows `=` rather than `▼`; stacked bar
        segments are apportioned by largest remainder so they sum to the bar's
        length; the senders footer is labelled `ALL - DUP`, which is what it
        always was; and `backup_info` prints a resolved folder. The alias
        loader also now refuses one string held as an `exact` alias under one
        canonical name and as a `prefix` under another -- a narrowing of what
        a map file may say, but of a shape the loader's own documentation
        already promised to refuse, and one no table was relying on.

        2.5.2 is a PATCH: the P0 and P1 items of the adversarial review of
        2.1.0-2.5.1, and no new capability. Vendor and account fields mask
        any run of ten or more digits to its last four -- a Meezan payee's
        account number, a bill's consumer number, a phone number in an SCB
        descriptor -- so a listing no longer prints one. The vendor *strings*
        change for those transactions, but no invocation changes meaning, no
        amount or count moves, and a string that was a full account number
        was never an interface anyone should have been parsing. A duplicate from an
        unrecognised sender is recorded without naming it; a repeated
        non-transaction message no longer counts as an ambiguous duplicate;
        `--quiet` now silences the rules, notices and empty-state panels it
        left on stderr; and CI asserts the lockfile on every `uv` invocation.

        2.6.0 is MINOR: the P2 items of the same review, and one of them adds
        rows. `backup_info` now carries one message count per bank and one
        skipped count per bank in its default output, and the skipped counts
        come off the `<ID>_SKIPPED` buckets rather than the diagnostics -- a
        warning keeps its transaction and is not a skip. The `--verbose`
        section that counted every diagnostic is labelled `diagnostics`, which
        is what it always counted; it was called `parseFailures`. Everything
        else is a fix: axis ticks under ten keep a decimal, a series named
        "Other" keeps its own slot, a wide total widens its column instead of
        wrapping the row, a change past 999% is clamped, a bad date range or
        an empty `--vendor` or a malformed `--vendor-map` is refused before the
        backup is read, the alias loader refuses four more shapes it should
        have, the sort key carries the card or account so the documented order
        is total, an option written after FILEPATH is explained rather than
        misread as the file, and an empty backup is reported as empty. No
        existing invocation changes meaning; no amount, count or total moves.

        2.7.0 is MINOR: `list_all_debit_txns --txn-type` gains
        `cheque_clearing`, a Meezan account debit for a cheque presented for
        clearing against the account, in either of two wordings the bank has
        sent ("INWARD CLEARING VIA CHEQUE NO" and the older "DR.TRNFR
        chq#..."). It was previously excluded outright, to avoid double
        counting against the bank's own preceding "received in inward
        clearing" notice for the same cheque; that notice still never becomes
        a transaction; it never reaches a debit keyword and never matches the
        amount-head anchor every Meezan debit template requires. Purely
        additive: every existing invocation of `list_all_debit_txns` -- with
        or without a `--txn-type` filter -- returns exactly what 2.6.0
        returned, since the new type is reachable only by naming it.

        2.8.0 is MINOR: `list_all_debit_txns` gains `--verbose` / `-v`, which
        adds the cheque number -- empty for every txnType but
        `cheque_clearing` -- as a column in the table and a `chequeNumber`
        field in JSON and CSV. Unlike the monthly summaries' `--verbose`,
        which only adds rows a plain listing's JSON and CSV already carried
        either way, this one changes the row shape itself: a plain
        `list_all_debit_txns` invocation, with or without `--verbose`, until
        now always emitted the same six fields, and it still does without the
        flag. Reachable only by naming it, so no existing invocation changes
        meaning.
        """
        major, minor, patch = projectVersion().split(".")

        self.assertEqual((major, minor, patch), ("2", "8", "0"))

    def test_the_console_entry_point_is_declared(self):
        with PYPROJECT_PATH.open("rb") as handle:
            pyproject = tomllib.load(handle)

        self.assertEqual(
            pyproject["project"]["scripts"]["sms-txn"],
            "sms_msgs_scraper.sms_txn_query_tool:main",
        )

    def test_the_description_covers_both_halves_of_the_product(self):
        """It used to describe credit cards only, omitting account debits."""
        with PYPROJECT_PATH.open("rb") as handle:
            description = tomllib.load(handle)["project"]["description"].lower()

        for term in ("credit card", "debit", "atm", "transfer"):
            with self.subTest(term=term):
                self.assertIn(term, description)


if __name__ == "__main__":
    unittest.main()
