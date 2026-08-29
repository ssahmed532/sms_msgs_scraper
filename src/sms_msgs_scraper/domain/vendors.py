"""Canonical vendor names, and the static table that produces them.

A bank's alert carries whatever string the acquirer put in the transaction, and
that string is not stable for one merchant. The reference corpus holds ten
distinct spellings of what a person would call "the PSO station"::

    PSO SERVICE STATION 7            PSO SERVICE STATION 25
    PSO SERVICE STATION 7 A          PSO SERVICE STATION 25 KARACHI PAK
    PSO SERVICE STATION 7 Karachi PAK    PSO SERVICE STATION 25 Karachi PAK
    PSO SERVICE STATION 7Karachi PAK     PSO SERVICE STATION 23 Karachi PAK
    PSO SERVICE STATION              PSO SERVICE STATION KARACHI PAK

Three separate things vary in there, and each one defeats a different repair:

  * a trailing city and country, sometimes present and sometimes not
  * that suffix's case (`KARACHI` against `Karachi`), and sometimes no space
    before it at all (`7Karachi PAK`, `TOTAL PARCOKARACHI PAK`)
  * truncation by the bank, which cuts mid-word (`SHELL (CREEK SERVICE S`)

**This module does not guess at any of that.** No suffix stripping, no fuzzy
distance, no "looks like a city" rule -- every one of those silently
mis-attributes spending the first time a real merchant name happens to end in
something that looks like a city. Grouping happens only where a human wrote
down that two names are one merchant, in a static table that can be read and
argued with. What is normalized is only what cannot carry meaning: case, and
runs of internal whitespace.

The table maps one canonical name to two or more aliases, each of which is
either an `exact` full vendor string or a `prefix` of one. The prefix form is
not a convenience: an SSGC bill embeds its own consumer number and a truncated
name has no fixed ending, so neither can be enumerated exhaustively.

Nothing here rewrites a transaction on its own. `canonicalFor` returns the raw
vendor unchanged when no alias claims it, and the CLI applies the mapping only
when asked to -- so a run that does not ask for canonical vendors sees exactly
the strings the banks sent.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

# The alias file's schema version. Carried in the file and checked on load, so
# a map written against a different shape is refused rather than half-read.
MAP_SCHEMA_VERSION = 1

# The shipped table, inside the package so an installed copy of the tool has
# one without being pointed at a file. Addressed as a resource *directory*
# rather than a subpackage, so `data/` needs no `__init__.py` pretending that a
# JSON file is importable code.
DEFAULT_MAP_PACKAGE = "sms_msgs_scraper"
DEFAULT_MAP_DIR = "data"
DEFAULT_MAP_FILENAME = "vendor_aliases.json"

# The keys an entry may carry. `note` is why: JSON has no comments, and an
# entry that groups two spellings on a human judgement should be able to say on
# what grounds.
_ENTRY_KEYS = frozenset({"exact", "prefix", "note"})

_WHITESPACE_RUN = re.compile(r"\s+")


class VendorMapError(Exception):
    """The vendor alias map could not be read, or does not mean anything.

    Raised rather than returned because there is no useful partial answer: a
    map that half-loaded would group some spending and not the rest, and the
    totals would be wrong in a way nothing downstream could detect.
    """


def normalizeVendor(vendor: str) -> str:
    """The form two vendor strings are compared in.

    Case and internal spacing vary between one merchant's own messages, so
    neither can be part of the identity. Nothing else is normalized away: a
    trailing city, a glued-on suffix and a truncation are exactly what an alias
    is for, and stripping them here would be the guessing this module refuses
    to do.
    """
    return _WHITESPACE_RUN.sub(" ", vendor).strip().casefold()


@dataclass(frozen=True, slots=True)
class VendorAliasMap:
    """A loaded, validated alias table.

    Held in the two shapes a lookup needs rather than the shape the file is
    written in. `prefixAliases` is ordered longest-first, which is what makes a
    refinement possible: a map may claim `PSO SERVICE STATION` for `PSO` and
    `PSO SERVICE STATION 25` for a single station, and the longer alias wins
    deterministically instead of the two being a conflict.
    """

    # normalized full vendor string -> canonical name
    exactAliases: Mapping[str, str] = MappingProxyType({})
    # (normalized prefix, canonical name), longest prefix first
    prefixAliases: tuple[tuple[str, str], ...] = ()
    # every canonical name the table declares, in file order
    canonicalNames: tuple[str, ...] = ()

    @property
    def isEmpty(self) -> bool:
        return not self.exactAliases and not self.prefixAliases

    @property
    def aliasCount(self) -> int:
        return len(self.exactAliases) + len(self.prefixAliases)

    def canonicalFor(self, vendor: str) -> str:
        """The canonical name for one raw vendor string, or the string itself.

        Returning the raw vendor unchanged -- not an empty string, not an
        uppercased form -- is what keeps an unmapped merchant readable and
        keeps two unmapped spellings distinct. The table is the only thing that
        merges anything.
        """
        key = normalizeVendor(vendor)

        canonical = self.exactAliases.get(key)
        if canonical is not None:
            return canonical

        for prefix, prefixCanonical in self.prefixAliases:
            if key.startswith(prefix):
                return prefixCanonical

        return vendor

    @classmethod
    def empty(cls) -> VendorAliasMap:
        return cls()

    @classmethod
    def fromDict(cls, data) -> VendorAliasMap:
        """Build a map from the parsed file, refusing anything ambiguous.

        Every check here exists because the failure it prevents is silent. A
        misspelled key, an alias claimed by two canonical names, an entry with
        nothing in it -- none of those would raise on their own; they would
        just quietly group less spending than the file appears to say, and the
        totals would look plausible.
        """
        if not isinstance(data, dict):
            raise VendorMapError(
                f"the vendor map must be a JSON object, not {type(data).__name__}"
            )

        version = data.get("schemaVersion")
        if version != MAP_SCHEMA_VERSION:
            raise VendorMapError(
                f"vendor map schema version {version!r} cannot be read by this "
                f"build, which reads version {MAP_SCHEMA_VERSION}"
            )

        entries = data.get("canonicalVendors")
        if not isinstance(entries, dict):
            raise VendorMapError(
                "the vendor map must carry a 'canonicalVendors' object"
            )

        exact: dict[str, str] = {}
        prefixes: dict[str, str] = {}
        canonicalNames: list[str] = []
        seenCanonical: dict[str, str] = {}

        for canonical, entry in entries.items():
            if not isinstance(canonical, str) or not canonical.strip():
                raise VendorMapError(
                    f"a canonical vendor name must be a non-empty string, "
                    f"got {canonical!r}"
                )

            canonicalKey = normalizeVendor(canonical)
            previous = seenCanonical.get(canonicalKey)
            if previous is not None:
                raise VendorMapError(
                    f"canonical vendors {previous!r} and {canonical!r} differ "
                    f"only in case or spacing, so a lookup could not tell them "
                    f"apart"
                )
            seenCanonical[canonicalKey] = canonical

            _readEntry(canonical, entry, exact, prefixes)
            canonicalNames.append(canonical)

        return cls(
            exactAliases=MappingProxyType(dict(exact)),
            # Longest first, so a more specific alias beats a broader one. Ties
            # are broken alphabetically only to make the order total: two
            # equal-length prefixes cannot both match the same vendor.
            prefixAliases=tuple(
                sorted(prefixes.items(), key=lambda item: (-len(item[0]), item[0]))
            ),
            canonicalNames=tuple(canonicalNames),
        )

    @classmethod
    def loadFromPath(cls, path: Path) -> VendorAliasMap:
        """Load a map from a file the caller named."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as error:
            raise VendorMapError(
                f"cannot read the vendor map: {path} ({error.strerror})"
            ) from error

        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise VendorMapError(
                f"the vendor map is not valid JSON: {path} "
                f"(line {error.lineno}, column {error.colno}: {error.msg})"
            ) from error

        return cls.fromDict(data)

    @classmethod
    def loadDefault(cls) -> VendorAliasMap:
        """Load the table shipped inside the package.

        Read through `importlib.resources` rather than a path built from
        `__file__`, so it resolves the same way from a source checkout and from
        an installed wheel.
        """
        resource = files(DEFAULT_MAP_PACKAGE) / DEFAULT_MAP_DIR / DEFAULT_MAP_FILENAME

        try:
            text = resource.read_text(encoding="utf-8")
        except (OSError, ModuleNotFoundError) as error:
            raise VendorMapError(
                f"the packaged vendor map is missing or unreadable: "
                f"{DEFAULT_MAP_DIR}/{DEFAULT_MAP_FILENAME}"
            ) from error

        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise VendorMapError(
                f"the packaged vendor map is not valid JSON "
                f"(line {error.lineno}, column {error.colno}: {error.msg})"
            ) from error

        return cls.fromDict(data)


def _readEntry(canonical: str, entry, exact: dict, prefixes: dict) -> None:
    """Validate one canonical name's entry and fold its aliases into the tables."""
    if not isinstance(entry, dict):
        raise VendorMapError(
            f"the entry for {canonical!r} must be an object with 'exact' "
            f"and/or 'prefix' lists, not {type(entry).__name__}"
        )

    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        # A misspelled key would otherwise be read as "this entry has no
        # aliases", which is a grouping that silently does not happen.
        raise VendorMapError(
            f"the entry for {canonical!r} has unknown key(s) "
            f"{', '.join(repr(key) for key in unknown)}; "
            f"allowed keys are {', '.join(sorted(_ENTRY_KEYS))}"
        )

    exactAliases = _readAliasList(canonical, entry, "exact")
    prefixAliases = _readAliasList(canonical, entry, "prefix")

    if not exactAliases and not prefixAliases:
        raise VendorMapError(
            f"the entry for {canonical!r} lists no aliases, so it groups "
            f"nothing"
        )

    for alias in exactAliases:
        _claim(exact, alias, canonical, "exact alias")

    for alias in prefixAliases:
        _claim(prefixes, alias, canonical, "prefix")


def _readAliasList(canonical: str, entry: dict, key: str) -> list[str]:
    """One entry's `exact` or `prefix` list, normalized."""
    raw = entry.get(key, [])

    if not isinstance(raw, list):
        raise VendorMapError(
            f"{canonical!r}: '{key}' must be a list of strings, not "
            f"{type(raw).__name__}"
        )

    aliases = []
    for alias in raw:
        if not isinstance(alias, str):
            raise VendorMapError(
                f"{canonical!r}: every '{key}' alias must be a string, got "
                f"{alias!r}"
            )

        normalized = normalizeVendor(alias)
        if not normalized:
            raise VendorMapError(
                f"{canonical!r}: an empty '{key}' alias would claim every "
                f"vendor"
            )

        aliases.append(normalized)

    return aliases


def _claim(table: dict, alias: str, canonical: str, kind: str) -> None:
    """Record one alias, refusing a second claim on it.

    Two canonical names claiming one alias is not a preference to resolve by
    ordering -- it is a question the file does not answer, and picking either
    answer would move real money into the wrong bucket.
    """
    previous = table.get(alias)

    if previous is not None and previous != canonical:
        raise VendorMapError(
            f"the {kind} {alias!r} is claimed by both {previous!r} and "
            f"{canonical!r}"
        )

    table[alias] = canonical
