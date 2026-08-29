"""Tests for the canonical vendor table.

Two things are being pinned here, and they pull in opposite directions.

The lookup has to be *forgiving enough* to be worth having: the same merchant
arrives with a city glued to its name, in a different case, or truncated
mid-word, and an alias has to catch all of those.

The loader has to be *unforgiving*, because every way a map can be subtly wrong
is silent. A mistyped key, an alias two canonical names both claim, an entry
with no aliases in it -- none of those raise on their own. They just group less
spending than the file appears to say, and the totals still look plausible.
"""

import json
import tempfile
import unittest
from pathlib import Path

from sms_msgs_scraper.domain.vendors import (
    MAP_SCHEMA_VERSION,
    VendorAliasMap,
    VendorMapError,
    normalizeVendor,
)


def aliasMap(entries: dict) -> VendorAliasMap:
    return VendorAliasMap.fromDict(
        {"schemaVersion": MAP_SCHEMA_VERSION, "canonicalVendors": entries}
    )


class TestNormalizeVendor(unittest.TestCase):
    def test_case_is_folded(self):
        self.assertEqual(
            normalizeVendor("PSO SERVICE STATION 25 KARACHI PAK"),
            normalizeVendor("PSO Service Station 25 Karachi Pak"),
        )

    def test_runs_of_whitespace_collapse_to_one_space(self):
        self.assertEqual(normalizeVendor("AMAZON.COM     SEATTLE"), "amazon.com seattle")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(normalizeVendor("  THE MART \n"), "the mart")

    def test_nothing_else_is_normalized_away(self):
        """A trailing city, a glued suffix and a truncation all survive.

        They have to: each one is a thing an alias exists to match, and a
        lookup that had already stripped them would be guessing rather than
        consulting the table.
        """
        self.assertEqual(
            normalizeVendor("TOTAL PARCOKARACHI PAK"), "total parcokarachi pak"
        )
        self.assertEqual(
            normalizeVendor("SHELL (CREEK SERVICE S"), "shell (creek service s"
        )


class TestLookup(unittest.TestCase):
    def test_an_exact_alias_maps_to_its_canonical_name(self):
        aliases = aliasMap({"PSO": {"exact": ["PSO AL ASKAR", "PSO SERVICE STATION"]}})

        self.assertEqual(aliases.canonicalFor("PSO AL ASKAR"), "PSO")

    def test_a_prefix_alias_claims_everything_beneath_it(self):
        aliases = aliasMap({"PSO": {"prefix": ["PSO SERVICE STATION"]}})

        for vendor in (
            "PSO SERVICE STATION",
            "PSO SERVICE STATION 7",
            "PSO SERVICE STATION 7 A",
            "PSO SERVICE STATION 25 KARACHI PAK",
            "PSO SERVICE STATION 25 Karachi PAK",
            # the city glued straight onto the station number, no space
            "PSO SERVICE STATION 7Karachi PAK",
        ):
            with self.subTest(vendor=vendor):
                self.assertEqual(aliases.canonicalFor(vendor), "PSO")

    def test_matching_ignores_case_and_internal_spacing(self):
        aliases = aliasMap({"OUTFITTERS": {"prefix": ["OUTFITTERS"]}})

        self.assertEqual(aliases.canonicalFor("Outfitters Karachi PAK"), "OUTFITTERS")
        self.assertEqual(aliases.canonicalFor("outfitters"), "OUTFITTERS")

    def test_an_unmapped_vendor_comes_back_exactly_as_it_arrived(self):
        """Not uppercased, not trimmed, not blanked.

        The table is the only thing that merges anything, so two unmapped
        spellings have to stay two spellings -- and the one the bank sent is
        the one worth showing.
        """
        aliases = aliasMap({"PSO": {"prefix": ["PSO SERVICE STATION"]}})

        self.assertEqual(
            aliases.canonicalFor("SOME NEW MERCHANT Karachi PAK"),
            "SOME NEW MERCHANT Karachi PAK",
        )

    def test_an_empty_map_maps_nothing(self):
        aliases = VendorAliasMap.empty()

        self.assertTrue(aliases.isEmpty)
        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 7"), "PSO SERVICE STATION 7")

    def test_an_exact_alias_beats_a_prefix_that_also_matches(self):
        """The more specific statement wins, whichever entry it sits in."""
        aliases = aliasMap(
            {
                "FUEL": {"prefix": ["PSO SERVICE STATION"]},
                "PSO STATION 25": {"exact": ["PSO SERVICE STATION 25"]},
            }
        )

        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 25"), "PSO STATION 25")
        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 7"), "FUEL")

    def test_the_longest_matching_prefix_wins(self):
        """Which is what lets one entry refine another instead of fighting it.

        A map may claim the whole brand and then carve one station out of it;
        without longest-first that would depend on dict ordering.
        """
        aliases = aliasMap(
            {
                "PSO": {"prefix": ["PSO SERVICE STATION"]},
                "PSO 25": {"prefix": ["PSO SERVICE STATION 25"]},
            }
        )

        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 25 Karachi PAK"), "PSO 25")
        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 7 Karachi PAK"), "PSO")

    def test_the_alias_count_covers_both_kinds(self):
        aliases = aliasMap(
            {"PSO": {"exact": ["PSO AL ASKAR"], "prefix": ["PSO SERVICE STATION"]}}
        )

        self.assertEqual(aliases.aliasCount, 2)
        self.assertEqual(aliases.canonicalNames, ("PSO",))
        self.assertFalse(aliases.isEmpty)


class TestLoaderRejectsAmbiguity(unittest.TestCase):
    """Every case here would otherwise group nothing, quietly."""

    def test_a_map_that_is_not_an_object_is_refused(self):
        with self.assertRaises(VendorMapError):
            VendorAliasMap.fromDict([{"PSO": {"prefix": ["PSO"]}}])

    def test_a_map_from_a_different_schema_version_is_refused(self):
        with self.assertRaises(VendorMapError) as caught:
            VendorAliasMap.fromDict(
                {"schemaVersion": 99, "canonicalVendors": {"PSO": {"prefix": ["PSO"]}}}
            )

        self.assertIn("99", str(caught.exception))

    def test_a_map_with_no_schema_version_is_refused(self):
        with self.assertRaises(VendorMapError):
            VendorAliasMap.fromDict({"canonicalVendors": {}})

    def test_a_map_with_no_canonical_vendors_object_is_refused(self):
        with self.assertRaises(VendorMapError):
            VendorAliasMap.fromDict({"schemaVersion": MAP_SCHEMA_VERSION})

    def test_an_entry_that_is_a_bare_list_is_refused(self):
        """The shape a person would most plausibly guess at, so it has to say so."""
        with self.assertRaises(VendorMapError) as caught:
            aliasMap({"PSO": ["PSO SERVICE STATION"]})

        self.assertIn("PSO", str(caught.exception))

    def test_a_misspelled_entry_key_is_refused(self):
        """It would otherwise read as 'this entry has no aliases'."""
        with self.assertRaises(VendorMapError) as caught:
            aliasMap({"PSO": {"prefixes": ["PSO SERVICE STATION"]}})

        self.assertIn("prefixes", str(caught.exception))

    def test_an_entry_with_no_aliases_is_refused(self):
        with self.assertRaises(VendorMapError):
            aliasMap({"PSO": {"note": "meant to fill this in"}})

    def test_an_entry_with_empty_alias_lists_is_refused(self):
        with self.assertRaises(VendorMapError):
            aliasMap({"PSO": {"exact": [], "prefix": []}})

    def test_an_alias_list_that_is_not_a_list_is_refused(self):
        with self.assertRaises(VendorMapError):
            aliasMap({"PSO": {"prefix": "PSO SERVICE STATION"}})

    def test_a_non_string_alias_is_refused(self):
        with self.assertRaises(VendorMapError):
            aliasMap({"PSO": {"exact": ["PSO AL ASKAR", 25]}})

    def test_an_empty_alias_is_refused(self):
        """An empty prefix matches every vendor there is."""
        with self.assertRaises(VendorMapError) as caught:
            aliasMap({"PSO": {"prefix": ["   "]}})

        self.assertIn("every vendor", str(caught.exception))

    def test_an_empty_canonical_name_is_refused(self):
        with self.assertRaises(VendorMapError):
            aliasMap({"  ": {"prefix": ["PSO"]}})

    def test_one_exact_alias_claimed_by_two_canonical_names_is_refused(self):
        with self.assertRaises(VendorMapError) as caught:
            aliasMap(
                {
                    "PSO": {"exact": ["PSO SERVICE STATION 7"]},
                    "FUEL": {"exact": ["PSO SERVICE STATION 7"]},
                }
            )

        self.assertIn("claimed by both", str(caught.exception))

    def test_one_prefix_claimed_by_two_canonical_names_is_refused(self):
        with self.assertRaises(VendorMapError) as caught:
            aliasMap(
                {
                    "PSO": {"prefix": ["PSO SERVICE"]},
                    "FUEL": {"prefix": ["pso service"]},
                }
            )

        self.assertIn("claimed by both", str(caught.exception))

    def test_two_canonical_names_differing_only_in_case_are_refused(self):
        """A lookup folds case, so these two could never be told apart."""
        with self.assertRaises(VendorMapError) as caught:
            aliasMap({"PSO": {"prefix": ["PSO SERVICE"]}, "pso": {"prefix": ["SHELL"]}})

        self.assertIn("case", str(caught.exception))

    def test_the_same_alias_twice_under_one_canonical_name_is_allowed(self):
        """Redundant, but it says the same thing twice rather than two things."""
        aliases = aliasMap(
            {"PSO": {"exact": ["PSO AL ASKAR", "pso al askar"]}}
        )

        self.assertEqual(aliases.canonicalFor("PSO AL ASKAR"), "PSO")


class TestLoadingFromAFile(unittest.TestCase):
    def _write(self, text: str) -> Path:
        tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpDir.cleanup)
        path = Path(tmpDir.name) / "vendor_aliases.json"
        path.write_text(text, encoding="utf-8")

        return path

    def test_a_well_formed_file_loads(self):
        path = self._write(
            json.dumps(
                {
                    "schemaVersion": MAP_SCHEMA_VERSION,
                    "canonicalVendors": {"PSO": {"prefix": ["PSO SERVICE STATION"]}},
                }
            )
        )

        aliases = VendorAliasMap.loadFromPath(path)

        self.assertEqual(aliases.canonicalFor("PSO SERVICE STATION 7"), "PSO")

    def test_a_file_that_is_not_json_names_the_line_it_failed_on(self):
        path = self._write('{"schemaVersion": 1,\n  "canonicalVendors": {,}}')

        with self.assertRaises(VendorMapError) as caught:
            VendorAliasMap.loadFromPath(path)

        self.assertIn("not valid JSON", str(caught.exception))

    def test_a_missing_file_is_reported_as_a_map_error(self):
        with self.assertRaises(VendorMapError):
            VendorAliasMap.loadFromPath(Path("no_such_vendor_map.json"))


class TestThePackagedMap(unittest.TestCase):
    """The table that ships inside the package.

    It ships the *mechanism*, not anyone's data. The entries are worked
    examples of the two alias forms, written so the file documents its own
    schema -- JSON has no comments, and a format nobody can see an example of
    gets guessed at. A real table is a private file passed to `--vendor-map`,
    kept out of this repository the way the reference backup is: a list of the
    merchants, schools, hospitals and utilities someone actually pays is a map
    of their life, and this repository is public.
    """

    def setUp(self):
        self.aliases = VendorAliasMap.loadDefault()

    def test_it_is_reachable_from_an_installed_package(self):
        """Read through importlib.resources, so this also proves the JSON was
        packaged rather than merely sitting in the source tree."""
        self.assertFalse(self.aliases.isEmpty)

    def test_it_carries_examples_and_nothing_else(self):
        """The privacy decision, enforced rather than remembered.

        Every canonical name and every alias in the packaged file must be
        marked EXAMPLE. That is what stops a table derived from someone's real
        spending being committed here later -- by them, or by an assistant
        being helpful. Real tables go in `--vendor-map`.
        """
        for canonical in self.aliases.canonicalNames:
            with self.subTest(canonical=canonical):
                self.assertTrue(
                    canonical.startswith("EXAMPLE"),
                    f"{canonical!r} is not an example -- the packaged table must "
                    f"carry no real merchant data",
                )

        aliases = list(self.aliases.exactAliases) + [
            prefix for prefix, _ in self.aliases.prefixAliases
        ]
        for alias in aliases:
            with self.subTest(alias=alias):
                self.assertTrue(alias.startswith("example"))

    def test_its_prefix_example_collapses_every_way_a_suffix_can_vary(self):
        """The shape the feature exists for, demonstrated on invented data:
        a station number, a trailing city, that city's case, and the city
        glued straight on with no space at all.
        """
        spellings = (
            "EXAMPLE FUEL SERVICE STATION",
            "EXAMPLE FUEL SERVICE STATION 7",
            "EXAMPLE FUEL SERVICE STATION 7 Karachi PAK",
            "EXAMPLE FUEL SERVICE STATION 7KARACHI PAK",
            "example fuel service station 25",
        )

        self.assertEqual(
            {self.aliases.canonicalFor(spelling) for spelling in spellings},
            {"EXAMPLE FUEL"},
        )

    def test_its_exact_example_collapses_two_spellings_no_prefix_could(self):
        self.assertEqual(
            {
                self.aliases.canonicalFor("EXAMPLE STORE AND BAKERY"),
                self.aliases.canonicalFor("EXAMPLE BAKERY AND STORE"),
            },
            {"EXAMPLE STORE"},
        )

    def test_a_merchant_it_says_nothing_about_is_left_alone(self):
        """Which, for the packaged table, is every real merchant there is."""
        for vendor in ("IMTIAZ SUPER MARKET", "PSO SERVICE STATION 7", "SSGC 123"):
            with self.subTest(vendor=vendor):
                self.assertEqual(self.aliases.canonicalFor(vendor), vendor)


if __name__ == "__main__":
    unittest.main()
