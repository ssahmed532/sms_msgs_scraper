"""The one assumption every timestamp in this tool rests on."""

from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Karachi")
"""The timezone every timestamp in an SMS backup is assumed to be in.

This is an assumption the application makes, not something the backup declares
-- the XML carries no offset. So a naive datetime parsed out of a backup must be
**stamped** with this zone (`.replace(tzinfo=DEFAULT_TZ)`), never **converted**
into it (`.astimezone(DEFAULT_TZ)`). `astimezone()` on a naive value reads it as
the *host machine's* local time and shifts it, which silently moves
transactions across day boundaries on any machine not set to +05:00.

Defined here and only here. It used to be declared in two modules, with the HBL
parser reading one copy and the other three parsers the other.
"""
