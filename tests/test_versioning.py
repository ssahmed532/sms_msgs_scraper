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
        -- and `uv sync --frozen` in CI would then install it.
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


class TestReleaseShape(unittest.TestCase):
    def test_this_release_is_a_minor_one_over_the_2_1_1_interface(self):
        """2.2.0, chosen by what the release does to an existing caller.

        2.0.0 was MAJOR because three things changed meaning: results moved to
        stdout while diagnostics moved to stderr, the tool gained an `sms-txn`
        entry point instead of a script path, and Standard Chartered's recovered
        transactions changed what every SCB total returns.

        2.1.0 was MINOR: it added `cc_spend_for_month` and took nothing away.

        2.1.1 was a PATCH: five modules moved into the subpackages they belong
        to and the bank registry split, with no command, option, default or
        output stream changed.

        2.2.0 adds vendor search (`--vendor`), canonical vendor names
        (`--canonical-vendors`) and the table behind them (`--vendor-map`). It
        is MINOR rather than MAJOR precisely because canonicalization is opt-in:
        a run that does not ask for it sees the same vendor strings, the same
        row counts and the same totals it saw in 2.1.1, and the reference corpus
        reproduces every number in CLAUDE.md unchanged. Making it the default
        would have been the MAJOR: `list_all_vendors` would return 271 rows
        where it used to return 359, and every script counting them would break.
        """
        major, minor, patch = projectVersion().split(".")

        self.assertEqual((major, minor, patch), ("2", "2", "0"))

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
