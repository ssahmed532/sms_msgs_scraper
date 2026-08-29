"""SMS transaction scraper: parse bank alert messages out of an Android SMS
backup and report on what they say.

The version is read from the installed package metadata rather than written
here. It used to be declared in three places -- this module's CLI decorator,
`pyproject.toml` and `uv.lock` -- which had already drifted apart once. Two of
them remain, and both are pinned by tests.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sms-msgs-scraper")
except PackageNotFoundError:  # pragma: no cover - only when run uninstalled
    # Running straight from a source tree that was never installed. The tool
    # still works; it just cannot know its own release number.
    __version__ = "0.0.0+source"
