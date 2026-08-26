"""The shared Rich console, theme and formatting helpers behind everything this
tool prints.

Every module renders through the single `console` defined here rather than
through bare `print()`, so that one theme decides what a bank tag, a currency,
an amount, a txn type or a warning looks like — wherever it is rendered.

Three choices here are load-bearing, and each has something in the repo that
would break if it were reversed:

  * **All output goes to stdout, warnings included.**
    `scripts/verify_against_backup.py` swallows the parsers' per-msg warnings
    with `redirect_stdout` precisely because they can identify a msg in a
    personal backup, and `tests/test_scb_sms_parser.py` asserts on that same
    stream. Routing warnings to stderr would leak them past both.

  * **The console is built without a `file`.** Rich then resolves `sys.stdout`
    at write time rather than capturing it at import time, which is what keeps
    `redirect_stdout` working and what makes a non-tty (a pipe, a StringIO)
    render as plain, uncoloured, un-ANSI-escaped text.

  * **Warnings render with `markup=False` and `soft_wrap=True`.** The HBL
    parser's warnings still carry msg bodies, and a body containing `[...]`
    would otherwise be eaten as Rich console markup. Soft wrapping keeps the
    convention that one parse failure is exactly one line, which the
    verification harness counts.

Colour is turned off automatically when stdout is not a terminal, and honours
the `NO_COLOR` and `TERM=dumb` environment variables; `--no-color` on the CLI
sets it off explicitly.
"""

import sys
from contextlib import nullcontext
from datetime import datetime

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Windows hands back a cp1252 stream the moment stdout is redirected to a file
# or a pipe, and neither Rich's box-drawing characters nor the placeholder glyph
# below are encodable there — `... > out.txt` would die on a UnicodeEncodeError
# rather than render. The console itself is fine; the stream underneath is not.
#
# stderr matters just as much and for a different reason: it defaults to
# errors="backslashreplace", so rich_click's usage-error panel came out as rows
# of literal ┌-style escape text instead of a box whenever it was redirected.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

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
        # the txn currency — amounts are coloured by it, so a USD figure never
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
        # msg-routing buckets in the parse summary
        "bucket.parsed": "bold white",
        "bucket.other": "dim",
        "bucket.dup": "yellow",
        "bucket.skipped": "yellow",
    }
)

console = Console(theme=APP_THEME, highlight=False)
"""The one console every module writes through."""

# Shown for a field the msg simply did not carry (an FBL txn has no card digits,
# an uppercase Meezan transfer has no account clause), and for a zero total in a
# monthly summary. A dash reads as "nothing here"; a bare 0 reads as a value.
EMPTY_VALUE = "—"

# Txn timestamps are rendered whole. HBL and SCB msgs carry a date only and so
# show 00:00:00, which is honest: that is exactly what the bank sent.
TXN_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _styleFor(prefix: str, value) -> str:
    """Resolve a themed style name for a data value, falling back to the
    prefix's `.unknown` entry.

    The fallback is not decorative. A bank parser accepts any 3-letter currency
    code, and a new bank or debit type can be added without touching this
    module — none of those may raise a MissingStyle at render time.

    Args:
        prefix (str): the theme namespace, e.g. "bank" or "currency"
        value: the data value naming the style within that namespace

    Returns:
        str: a style name that exists in APP_THEME
    """
    styleName = f"{prefix}.{value}"

    return styleName if styleName in APP_THEME.styles else f"{prefix}.unknown"


def bankText(bank: str) -> Text:
    """Render a bank ID in that bank's colour."""
    return Text(str(bank), style=_styleFor("bank", bank))


def currencyText(currency: str) -> Text:
    """Render a currency code in that currency's colour."""
    return Text(str(currency), style=_styleFor("currency", currency))


def amountText(amount: float, currency: str) -> Text:
    """Render a txn amount, thousands-grouped to 2dp, in its currency's colour.

    Args:
        amount (float): the txn amount
        currency (str): the currency it is denominated in, which picks the colour

    Returns:
        Text: e.g. a green "25,170.49" for PKR
    """
    return Text(f"{amount:,.2f}", style=_styleFor("currency", currency))


def totalText(amount: float, currency: str) -> Text:
    """Render a monthly / grand total, showing a zero as the empty placeholder.

    A monthly summary pre-seeds PKR/USD/CAD for every month, so most cells in a
    currency column are structurally zero rather than meaningfully zero.
    """
    if not amount:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(f"{amount:,.2f}", style=_styleFor("currency", currency))


def txnTypeText(txnType) -> Text:
    """Render a DebitTxnType (or its string value) in that type's colour."""
    return Text(str(txnType), style=_styleFor("txntype", txnType))


def dateText(txnDate: datetime) -> Text:
    """Render a txn timestamp. Always Asia/Karachi, so the offset is not shown."""
    return Text(txnDate.strftime(TXN_DATETIME_FMT), style="column.date")


def cardText(lastFourDigits: int) -> Text:
    """Render a masked card number from its last 4 digits.

    FBL msgs carry no card digits at all and SCB's BIN-only masks carry none
    either, both of which reach here as 0 — shown as the empty placeholder
    rather than as a card ending 0000.
    """
    if lastFourDigits <= 0:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(f"••••{lastFourDigits:04d}", style="column.card")


def acctText(acctMask: str) -> Text:
    """Render an account mask, or the empty placeholder when the msg had none."""
    if not acctMask:
        return Text(EMPTY_VALUE, style="column.empty")

    return Text(acctMask, style="column.acct")


def vendorText(vendor: str) -> Text:
    """Render a vendor name.

    Wrapped as Text rather than passed as a str so that Rich never reads a
    bracketed vendor name as console markup.
    """
    return Text(vendor, style="column.vendor")


def indexText(index: int) -> Text:
    """Render a 1-based row number for a listing."""
    return Text(str(index), style="column.index")


def countText(count: int, style: str = "column.count") -> Text:
    """Render a thousands-grouped count for a summary table."""
    return Text(f"{count:,}", style=style)


def labelText(label: str, style: str = "subheading") -> Text:
    """Render an arbitrary label in a themed style.

    Exists so that callers can style a cell without importing `rich.text`
    themselves — Rich stays behind this module.
    """
    return Text(str(label), style=style)


def dataTable(columns: list, caption: str = None) -> Table:
    """Build the table used for a listing — one row per txn or vendor.

    Deliberately light furniture: a listing can run to thousands of rows, and
    ruled cells turn that into a wall.

    Args:
        columns (list): (header, kwargs) pairs passed straight to add_column
        caption (str | None): a dim line under the table, e.g. the breakdown

    Returns:
        Table: an empty table ready for add_row
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


def summaryTable(title: str = None, showFooter: bool = False) -> Table:
    """Build the table used for a summary — a handful of rows worth boxing.

    Args:
        title (str | None): the table's heading, or None when the caller has
                            already introduced it with a rule and a notice line
        showFooter (bool): whether a totals row is rendered under the columns

    Returns:
        Table: an empty table ready for add_column / add_row
    """
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
    """Print renderables in a row, wrapping to the next line if the terminal is
    too narrow to hold them side by side.
    """
    console.print(Columns(renderables, padding=(0, 4)))


def printRule(title: str) -> None:
    """Open a command's output with a titled horizontal rule."""
    console.print()
    console.print(Rule(Text(title, style="heading"), style="heading", align="left"))


def statusSpinner(message: str):
    """A spinner to show while a slow step runs — and nothing at all when
    output is not a terminal.

    Rich renders a Live display once on stop when it has no terminal to animate,
    which would leave a stray "Reading the backup file..." line in every piped
    or redirected run. A progress indicator has no meaning outside a terminal,
    so there simply isn't one.
    """
    if console.is_terminal:
        return console.status(message, spinner="dots")

    return nullcontext()


def printHeader(title: str, fields: dict) -> None:
    """Open the run with a panel naming the tool and what it is working on.

    Args:
        title (str): the panel title
        fields (dict): label -> value rows shown inside the panel
    """
    body = Table.grid(padding=(0, 3))
    body.add_column(style="muted")
    body.add_column(overflow="fold")

    for label, value in fields.items():
        body.add_row(Text(label, style="muted"), Text(str(value), style="subheading"))

    console.print()
    console.print(
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
    """Print an informational line — what is about to happen, or what was found."""
    console.print(Text(message, style="info"))


def printEmptyState(message: str) -> None:
    """Print the 'nothing matched' state.

    A filter that matches nothing renders as a panel rather than as a table with
    a header and no rows, which reads as a bug.
    """
    console.print()
    console.print(
        Panel(
            Text(message, style="muted"),
            border_style="muted",
            box=box.ROUNDED,
            expand=False,
        )
    )


def printWarning(message: str) -> None:
    """Print one parse-failure warning line.

    `markup=False` because the HBL parser's warnings still carry msg bodies, and
    `soft_wrap=True` so that one failure stays exactly one line however long the
    reason is — `scripts/verify_against_backup.py` counts these.
    """
    console.print(message, style="warning", markup=False, soft_wrap=True)


def printError(message: str) -> None:
    """Print one parse-error line. Same wrapping and markup rules as a warning."""
    console.print(message, style="error", markup=False, soft_wrap=True)
