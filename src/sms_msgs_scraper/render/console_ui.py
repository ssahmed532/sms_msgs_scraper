"""The shared Rich consoles, the theme, and the cell helpers behind everything
this tool prints.

**There are two consoles, and which one a thing goes to is a contract.**

  * `console` writes to **stdout**, and carries *results* only -- the rows a
    command was asked for, and nothing else.
  * `errConsole` writes to **stderr**, and carries everything *about* the run:
    the header, the parse summary, progress, notices and parse diagnostics.

That split is what makes the tool usable from a script. Piping stdout gives the
data and only the data, while the reader still sees the diagnostics on their
terminal. Previously every one of those went to stdout together, so anything
consuming the output had to parse the furniture back out of the data -- and a
parse warning naming a skipped message landed in the middle of a CSV.

Two further choices are load-bearing:

  * **Neither console is built with a `file`.** Rich then resolves the stream at
    write time rather than capturing it at import, which is what keeps
    `redirect_stdout` / `redirect_stderr` and Click's `CliRunner` working, and
    what makes a non-tty render as plain, uncoloured text.

  * **Diagnostics render with `markup=False` and `soft_wrap=True`**, so a
    vendor name containing brackets is never read as console markup and one
    parse failure stays exactly one line.

Colour is dropped automatically when the stream is not a terminal, and honours
`NO_COLOR` and `TERM=dumb`; `--no-color` sets it off explicitly on both.
"""

import sys
from contextlib import nullcontext
from datetime import datetime

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.domain.types import CardReference

# Windows hands back a cp1252 stream the moment stdout or stderr is redirected
# to a file or a pipe, and Rich's box-drawing characters are not encodable
# there -- `... > out.txt` would die on a UnicodeEncodeError rather than render.
# stderr matters just as much and for a different reason: it defaults to
# errors="backslashreplace", so rich_click's usage-error panel came out as rows
# of literal escape text whenever it was redirected.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8")

APP_THEME = Theme(
    {
        # structure
        "heading": "bold cyan",
        "subheading": "bold white",
        "muted": "dim",
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        # table furniture
        "table.title": "bold cyan",
        "table.header": "bold white",
        "table.caption": "dim",
        "column.index": "dim cyan",
        "column.count": "bold white",
        "column.total": "bold white",
        "column.date": "cyan",
        "column.vendor": "white",
        "column.card": "magenta",
        "column.acct": "magenta",
        "column.empty": "dim",
        # the issuing / debiting bank
        "bank.HBL": "bold green",
        "bank.FBL": "bold blue",
        "bank.SCB": "bold magenta",
        "bank.MEZN": "bold yellow",
        "bank.unknown": "dim",
        # the txn currency -- amounts are coloured by it, so a USD figure never
        # reads as a PKR one at a glance
        "currency.PKR": "green",
        "currency.USD": "bright_cyan",
        "currency.CAD": "bright_magenta",
        "currency.unknown": "yellow",
        # the kind of account debit
        "txntype.card_purchase": "cyan",
        "txntype.atm_withdrawal": "yellow",
        "txntype.account_debit": "magenta",
        "txntype.funds_transfer": "blue",
        "txntype.unknown": "dim",
        # chart series, for a breakdown that has no identity of its own to
        # colour by -- a vendor. Bank and debit-type breakdowns keep the
        # `bank.*` and `txntype.*` styles above instead, so a bank is the same
        # colour in a chart that it is in every table.
        #
        # Four slots and no more, and the four are computed rather than
        # chosen. A terminal background may be near-black or near-white, so
        # each has to sit in the lightness band that works against both, and
        # that band is narrow enough that a fifth well-separated hue does not
        # fit in it. Measured in OKLab (dE x100, `--pairs all`, both surfaces):
        # worst protan/deutan separation 14.6 against a target of 8, worst
        # normal-vision separation 16.2 against a floor of 15.
        #
        # Colour is never the only encoding: each series also carries its own
        # block glyph and a fixed position in the stack, which is what keeps
        # the chart readable for a tritanope (worst tritan dE 3.3) and in a
        # terminal rendering without colour at all.
        "series.1": "#259cde",
        "series.2": "#995b00",
        "series.3": "#3da97b",
        "series.4": "#8c5587",
        "series.other": "dim",
        # msg-routing buckets in the parse summary
        "bucket.parsed": "bold white",
        "bucket.other": "dim",
        "bucket.dup": "yellow",
        "bucket.skipped": "yellow",
    }
)

console = Console(theme=APP_THEME, highlight=False)
"""Results, and only results. Written to stdout."""

errConsole = Console(theme=APP_THEME, highlight=False, stderr=True)
"""Everything about the run rather than in it. Written to stderr."""


def setNoColor() -> None:
    """Turn colour off explicitly on both consoles."""
    console.no_color = True
    errConsole.no_color = True


# Shown for a field the msg simply did not carry (an FBL txn has no card digits,
# an uppercase Meezan transfer has no account clause), and for a zero total in a
# monthly summary. A dash reads as "nothing here"; a bare 0 reads as a value.
EMPTY_VALUE = "—"

# Txn timestamps are rendered whole. HBL and SCB msgs carry a date only and so
# show 00:00:00, which is honest: that is exactly what the bank sent.
TXN_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# Control characters have no business in a terminal cell. A vendor name comes
# out of an SMS, and an SMS is not a trusted source: an escape sequence in one
# could reposition the cursor, recolour the rest of the session, or hide
# following output entirely. Stripped at the point of rendering, and also at the
# field boundary for machine output.
_CONTROL_CHARS = {code: None for code in range(0x20) if code not in (0x09,)}
_CONTROL_CHARS.update(dict.fromkeys(range(127, 160)))


def sanitizeField(value: str) -> str:
    """Strip control characters out of a string that came from an SMS."""
    return str(value).translate(_CONTROL_CHARS)


def _styleFor(prefix: str, value) -> str:
    """Resolve a themed style name for a data value, falling back to the
    prefix's `.unknown` entry.

    The fallback is not decorative. A bank parser accepts any 3-letter currency
    code, and a new bank or debit type can be added without touching this
    module -- none of those may raise a MissingStyle at render time.
    """
    styleName = f"{prefix}.{value}"

    return styleName if styleName in APP_THEME.styles else f"{prefix}.unknown"


def bankText(bank: str) -> Text:
    """Render a bank ID in that bank's colour."""
    return Text(str(bank), style=_styleFor("bank", bank))


def currencyText(currency: str) -> Text:
    """Render a currency code in that currency's colour."""
    return Text(str(currency), style=_styleFor("currency", currency))


def amountText(money: Money) -> Text:
    """Render a transaction amount, grouped to its currency's scale.

    Takes a `Money`, so the value being rendered is exact and the formatting is
    the only place a scale is applied -- an amount too precise for its currency
    was refused at construction rather than rounded away here.
    """
    return Text(money.formatted(), style=_styleFor("currency", money.currency))


def totalText(money: Money | None, currency: str) -> Text:
    """Render a monthly / grand total, showing nothing as the empty placeholder.

    A monthly summary pre-seeds every currency column for every month, so most
    cells are structurally absent rather than meaningfully zero.
    """
    if money is None or not money.amount:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(money.formatted(), style=_styleFor("currency", currency))


def txnTypeText(txnType) -> Text:
    """Render a DebitTxnType (or its string value) in that type's colour."""
    return Text(str(txnType), style=_styleFor("txntype", txnType))


def dateText(txnDate: datetime) -> Text:
    """Render a txn timestamp. Always Asia/Karachi, so the offset is not shown."""
    return Text(txnDate.strftime(TXN_DATETIME_FMT), style="column.date")


def cardText(card: CardReference) -> Text:
    """Render a masked card number, or the placeholder when there was none.

    FBL messages carry no card digits at all and SCB's BIN-only masks carry none
    either. Both are an *absent* card reference now, which is why a real card
    ending 0000 can be shown as one instead of being mistaken for absence.
    """
    if not card.known:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(f"••••{card.lastFour}", style="column.card")


def acctText(acctMask: str) -> Text:
    """Render an account mask, or the empty placeholder when the msg had none."""
    if not acctMask:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(sanitizeField(acctMask), style="column.acct")


def vendorText(vendor: str) -> Text:
    """Render a vendor name.

    Wrapped as Text rather than passed as a str so that Rich never reads a
    bracketed vendor name as console markup, and sanitised so that an escape
    sequence in a message body cannot reach the terminal.
    """
    return Text(sanitizeField(vendor), style="column.vendor")


def indexText(index: int) -> Text:
    """Render a 1-based row number for a listing."""
    return Text(str(index), style="column.index")


def countText(count: int, style: str = "column.count") -> Text:
    """Render a thousands-grouped count for a summary table."""
    return Text(f"{count:,}", style=style)


def labelText(label: str, style: str = "subheading") -> Text:
    """Render an arbitrary label in a themed style.

    Exists so that callers can style a cell without importing `rich.text`
    themselves -- Rich stays behind this module.
    """
    return Text(str(label), style=style)


def segmentsText(parts) -> Text:
    """Assemble one line out of (content, style) pairs.

    A chart bar is many differently-styled runs on one line, which no cell
    helper above can express. This is the general form of them, and it exists
    here rather than in the chart renderer so that Rich stays behind this
    module: a caller composes a line without importing `rich.text` itself.
    """
    line = Text()

    for content, style in parts:
        line.append(content, style=style)

    return line


def stackedGroup(*renderables):
    """Several renderables printed as one block, for the same reason."""
    return Group(*renderables)


def dataTable(columns: list, caption: str | None = None) -> Table:
    """Build the table used for a listing -- one row per txn or vendor.

    Deliberately light furniture: a listing can run to thousands of rows, and
    ruled cells turn that into a wall.
    """
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="table.header",
        caption=caption,
        caption_style="table.caption",
        caption_justify="right",
        pad_edge=False,
        expand=False,
    )

    for header, columnArgs in columns:
        table.add_column(header, **columnArgs)

    return table


def summaryTable(title: str | None = None, showFooter: bool = False) -> Table:
    """Build the table used for a summary -- a handful of rows worth boxing."""
    return Table(
        title=title,
        title_style="table.title",
        title_justify="left",
        box=box.ROUNDED,
        header_style="table.header",
        show_footer=showFooter,
        footer_style="column.total",
        expand=False,
    )


def printSideBySide(*renderables) -> None:
    """Print renderables in a row on stderr, wrapping if the terminal is too
    narrow to hold them side by side.

    Parse summaries describe the run rather than answer the question, so they
    belong on stderr with the rest of the furniture.
    """
    errConsole.print(Columns(renderables, padding=(0, 4)))


def printRule(title: str) -> None:
    """Open a command's output with a titled horizontal rule, on stderr."""
    errConsole.print()
    errConsole.print(Rule(Text(title, style="heading"), style="heading", align="left"))


def statusSpinner(message: str):
    """A spinner to show while a slow step runs -- and nothing at all when
    stderr is not a terminal.

    Rich renders a Live display once on stop when it has no terminal to animate,
    which would leave a stray progress line in every piped or redirected run.
    """
    if errConsole.is_terminal:
        return errConsole.status(message, spinner="dots")

    return nullcontext()


def printHeader(title: str, fields: dict) -> None:
    """Open the run with a panel naming the tool and what it is working on."""
    body = Table.grid(padding=(0, 3))
    body.add_column(style="muted")
    body.add_column(overflow="fold")

    for label, value in fields.items():
        body.add_row(Text(label, style="muted"), Text(str(value), style="subheading"))

    errConsole.print()
    errConsole.print(
        Panel(
            body,
            title=Text(title, style="heading"),
            title_align="left",
            border_style="heading",
            box=box.ROUNDED,
            expand=False,
        )
    )


def printNotice(message: str) -> None:
    """Print an informational line -- what is about to happen, or what was found."""
    errConsole.print(Text(message, style="info"))


def printEmptyState(message: str) -> None:
    """Print the 'nothing matched' state.

    A filter that matches nothing renders as a panel rather than as a table with
    a header and no rows, which reads as a bug.
    """
    errConsole.print()
    errConsole.print(
        Panel(
            Text(message, style="muted"),
            border_style="muted",
            box=box.ROUNDED,
            expand=False,
        )
    )


def printWarning(message: str) -> None:
    """Print one parse-failure line on stderr.

    `markup=False` so a bracketed value is never read as console markup, and
    `soft_wrap=True` so one failure stays exactly one line.
    """
    errConsole.print(message, style="warning", markup=False, soft_wrap=True)


def printError(message: str) -> None:
    """Print one error line. Same wrapping and markup rules as a warning."""
    errConsole.print(message, style="error", markup=False, soft_wrap=True)
