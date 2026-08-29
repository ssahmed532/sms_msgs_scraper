"""The command line interface.

Two things about this module are contracts rather than implementation details.

**Nothing is parsed until a command actually needs it.** The backup used to be
read and parsed by the group callback, which runs before Click has resolved the
subcommand -- so `<command> --help` read 4,665 messages, printed a parse summary
and a page of warnings, and only then showed the help text. Parsing is now
deferred behind `AppContext.report()`, so help, usage errors and argument
validation all complete without touching the file.

**stdout carries results; stderr carries everything else.** Rows go to stdout,
and the header, parse summary, notices and diagnostics go to stderr. Piping the
tool into another program yields data with no decoration in it, while the person
running it still sees what happened.
"""

import sys
from pathlib import Path
from time import perf_counter

# rich_click is a drop-in re-export of click that renders --help and usage
# errors through Rich. Every decorator, type and exception below is click's own.
import rich_click as click

from sms_msgs_scraper import __version__
from sms_msgs_scraper.console_ui import (
    console,
    printEmptyState,
    printHeader,
    printNotice,
    printRule,
    printWarning,
    setNoColor,
    statusSpinner,
)
from sms_msgs_scraper.debit_txn import DebitTxnType
from sms_msgs_scraper.domain.aggregate import (
    MONTH_KEY_FMT,
    monthKeyFor,
    monthlyTotals,
    txnCountsByMonth,
)
from sms_msgs_scraper.domain.registry import REGISTRY
from sms_msgs_scraper.domain.report import DuplicatePolicy
from sms_msgs_scraper.render import machine
from sms_msgs_scraper.render.tables import (
    bankSpendTable,
    ccTxnsTable,
    debitTxnsTable,
    monthlySummaryTable,
    printParseSummary,
    vendorsTable,
)
from sms_msgs_scraper.sms_backup_file_parser import BackupFileError, SmsBackupFileParser

# Read from the installed package metadata rather than written here. The
# number used to live in three hand-synced places and had already drifted
# apart once; this retires one of them.
APP_VERSION = __version__

# Exit codes, fixed so a script can branch on them.
#   0  success
#   1  the backup file could not be read or parsed
#   2  usage error (Click's own, for a bad option or argument)
#   3  --strict was given and the run had something to report
EXIT_BACKUP_ERROR = 1
EXIT_STRICT_FAILURE = 3

# The date format accepted by the --from-date / --to-date options
DATE_RANGE_FMT = "%Y-%m-%d"

OUTPUT_FORMATS = ("table", "json", "csv")

DEBIT_TXN_TYPES = tuple(txnType.value for txnType in DebitTxnType)


# --- how rich_click renders --help and usage errors -------------------------
#
# Keyed on "*" so the command groups apply whatever prog name the tool is
# invoked under.
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.MAX_WIDTH = 100
click.rich_click.STYLE_OPTION = "bold cyan"
click.rich_click.STYLE_SWITCH = "bold green"
click.rich_click.STYLE_METAVAR = "magenta"
click.rich_click.STYLE_USAGE = "bold yellow"
click.rich_click.STYLE_HEADER_TEXT = "bold cyan"
click.rich_click.STYLE_OPTION_DEFAULT = "dim"
click.rich_click.STYLE_OPTIONS_PANEL_BORDER = "dim cyan"
click.rich_click.STYLE_COMMANDS_PANEL_BORDER = "dim cyan"
click.rich_click.STYLE_ERRORS_PANEL_BORDER = "red"
click.rich_click.COMMAND_GROUPS = {
    "*": [
        {
            "name": "Credit card commands (HBL + FBL + SCB)",
            "commands": [
                "list_all_cc_txns",
                "list_all_vendors",
                "monthly_cc_spending_summary",
                "cc_spend_for_month",
            ],
        },
        {
            "name": "Meezan account debit commands",
            "commands": [
                "list_all_debit_txns",
                "monthly_debit_spending_summary",
            ],
        },
    ]
}


class AppContext:
    """Everything a command needs, with the parse deferred until it is asked
    for.

    `report()` is what makes `--help` free. It also memoises, so two accesses
    inside one command do not parse twice -- which the parser would refuse
    anyway, being one-shot.
    """

    def __init__(self, filepath, quiet, strict, duplicatePolicy, outputFormat):
        self.filepath = filepath
        self.quiet = quiet
        self.strict = strict
        self.duplicatePolicy = duplicatePolicy
        self.outputFormat = outputFormat
        self._report = None

    @property
    def machineReadable(self) -> bool:
        return self.outputFormat != "table"

    def report(self):
        if self._report is not None:
            return self._report

        if not self.quiet:
            printHeader(
                "sms_txn_query_tool",
                {"Backup": self.filepath.name, "Folder": self.filepath.parent},
            )

        parser = SmsBackupFileParser(duplicatePolicy=self.duplicatePolicy)
        timeStart = perf_counter()

        try:
            with statusSpinner("Reading the backup file..."):
                report = parser.parse(self.filepath)
        except BackupFileError as error:
            raise click.ClickException(str(error)) from error
        except PermissionError as error:
            raise click.ClickException(
                f"cannot read the SMS backup file: {self.filepath}"
            ) from error

        elapsed = perf_counter() - timeStart

        if not self.quiet:
            printParseSummary(report)
            printNotice(
                f"Parsed {report.count('ALL'):,} SMS messages "
                f"in {elapsed:.2f} seconds"
            )
            self._reportDiagnostics(report)

        self._enforceStrict(report)

        self._report = report

        return report

    def _reportDiagnostics(self, report) -> None:
        """Print one line per parse failure, on stderr.

        Each line names the reason and locates the message by sender and
        received date. It never carries the body: a backup holds vendors,
        account masks, card digits and running balances, and this output is the
        kind of thing that ends up pasted into a bug report.
        """
        for diagnostic in report.diagnostics:
            printWarning(diagnostic.message())

        envelope = report.envelope
        if not envelope.matchesDeclared:
            printWarning(
                f"WARNING: the backup declares {envelope.declared:,} records "
                f"but holds {envelope.actual:,}"
            )

        # Under `review`, say how much of the deduplication was a judgement
        # call. `exact` collapses these silently, which is the right default but
        # is precisely the invisible financial decision worth being able to see:
        # these are repeats from a bank whose alerts carry no time of day, so a
        # second genuine identical purchase cannot be ruled out.
        if self.duplicatePolicy is DuplicatePolicy.REVIEW:
            ambiguous = report.ambiguousDuplicates
            if ambiguous:
                printWarning(
                    f"REVIEW: {ambiguous:,} of {len(report.duplicates):,} "
                    f"suppressed duplicates could not be proved to be "
                    f"retransmissions; --duplicates none reports the upper bound"
                )
            else:
                printNotice(
                    "REVIEW: every suppressed duplicate came from a bank that "
                    "timestamps its alerts, so each provably repeats a "
                    "transaction already counted"
                )

    def _enforceStrict(self, report) -> None:
        """Under --strict, anything the run had to report is a failure.

        The exit code is fixed and distinct from a usage error, so a caller can
        tell "you invoked me wrongly" from "the backup had messages I could not
        parse".
        """
        if not self.strict:
            return

        problems = len(report.diagnostics)
        if not report.envelope.matchesDeclared:
            problems += 1

        if problems:
            raise StrictFailure(
                f"--strict: the run had {problems} thing(s) to report"
            )


class StrictFailure(click.ClickException):
    """Raised when --strict is given and the run had something to report."""

    exit_code = EXIT_STRICT_FAILURE


@click.group()
@click.version_option(APP_VERSION, prog_name="sms_txn_query_tool")
@click.argument(
    "filepath",
    type=click.Path(
        exists=True,
        file_okay=True,
        # A directory would satisfy exists=True and then fail deep inside the
        # XML reader with a bare OSError; rejecting it here reports it as the
        # bad argument it is.
        dir_okay=False,
        readable=True,
        path_type=Path,
    ),
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="Render without colour. Must be written [bold]before[/] FILEPATH. "
    "Colour is dropped automatically when output is piped, or when NO_COLOR is "
    "set in the environment.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Suppress the header, parse summary and diagnostics on stderr. "
    "Results on stdout are unaffected.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero if any message could not be parsed, or if the backup's "
    "declared record count does not match what it holds.",
)
@click.option(
    "--duplicates",
    type=click.Choice([policy.value for policy in DuplicatePolicy]),
    default=DuplicatePolicy.EXACT.value,
    show_default=True,
    help="How to treat a repeated (sender, body): [bold]exact[/] collapses "
    "them, [bold]none[/] keeps every message, [bold]review[/] collapses them "
    "and reports how many could not be proved to be retransmissions.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(OUTPUT_FORMATS),
    default="table",
    show_default=True,
    help="How results are written to stdout.",
)
@click.pass_context
def cli(ctx, filepath, no_color, quiet, strict, duplicates, output_format):
    """Parse and analyze bank transaction SMS messages from an Android SMS
    backup file.

    FILEPATH is the XML backup produced by the [italic]SMS Backup & Restore[/]
    app. It is read once, the first time a subcommand needs it -- so `--help`
    costs nothing.

    Results go to [bold]stdout[/]; the header, parse summary and diagnostics go
    to [bold]stderr[/].
    """
    if no_color:
        setNoColor()

    ctx.obj = AppContext(
        filepath=filepath,
        quiet=quiet,
        strict=strict,
        duplicatePolicy=DuplicatePolicy(duplicates),
        outputFormat=output_format,
    )


def dateRangeOptions(command):
    """The --from-date / --to-date pair every command accepts."""
    command = click.option(
        "--to-date",
        type=click.DateTime(formats=[DATE_RANGE_FMT]),
        default=None,
        help="Only include transactions on or before this date (YYYY-MM-DD).",
    )(command)

    return click.option(
        "--from-date",
        type=click.DateTime(formats=[DATE_RANGE_FMT]),
        default=None,
        help="Only include transactions on or after this date (YYYY-MM-DD).",
    )(command)


def bankOption(command):
    return click.option(
        "--bank",
        type=click.Choice(REGISTRY.ccBankIds, case_sensitive=False),
        default=None,
        help="Only include transactions from this bank (default: all banks).",
    )(command)


def _filterTxnsByDateRange(txns, fromDate, toDate):
    """Filter transactions to an inclusive [fromDate, toDate] range.

    Comparison is on the calendar date only. Transaction dates are
    timezone-aware while the CLI options are not, so comparing the datetimes
    directly would raise on the naive/aware mismatch. Comparing dates also keeps
    both bounds inclusive for transactions that carry a real time of day.
    """
    if fromDate is None and toDate is None:
        return list(txns)

    if fromDate is not None and toDate is not None and fromDate > toDate:
        raise click.BadParameter(
            f"--from-date ({fromDate.date()}) is after --to-date ({toDate.date()})"
        )

    filtered = list(txns)
    if fromDate is not None:
        filtered = [txn for txn in filtered if txn.date.date() >= fromDate.date()]
    if toDate is not None:
        filtered = [txn for txn in filtered if txn.date.date() <= toDate.date()]

    return filtered


def _filterTxnsByBank(txns, bank):
    if bank is None:
        return list(txns)

    # click.Choice(case_sensitive=False) hands back the declared choice string
    # (e.g. "FBL" for an input of "fbl"), so no case normalization is needed.
    return [txn for txn in txns if txn.bank == bank]


def _filterLabel(fromDate, toDate, bank=None, txnType=None) -> str:
    """A human-readable suffix describing the active filters."""
    parts = []

    if bank is not None:
        parts.append(f"bank {bank}")
    if txnType is not None:
        parts.append(f"type {txnType}")
    # A two-sided range reads as one phrase rather than two clauses.
    if fromDate is not None and toDate is not None:
        parts.append(f"from {fromDate.date()} to {toDate.date()}")
    elif fromDate is not None:
        parts.append(f"from {fromDate.date()}")
    elif toDate is not None:
        parts.append(f"up to {toDate.date()}")

    return f" ({', '.join(parts)})" if parts else ""


def _emit(ctx, kind, columns, rows, table, emptyMessage, notice) -> None:
    """Write one command's results out in the requested format.

    The empty case is handled differently on purpose. For a person, a table
    header with no rows under it reads as a defect in the tool, so a panel says
    so in words. For a program, an empty result set is an ordinary answer, and
    a header-only CSV or an empty `rows` array is exactly right.
    """
    app = ctx.obj

    if app.machineReadable:
        _writeMachineOutput(app, kind, columns, rows)
        return

    printRule(notice.title)

    if not rows:
        printEmptyState(emptyMessage)
        return

    printNotice(notice.line)
    console.print(table())


def _writeMachineOutput(app, kind, columns, rows) -> None:
    """Write JSON or CSV straight to stdout, bypassing Rich entirely.

    Rich is a renderer for people. Sending machine output through a console
    would let terminal width decide where a CSV row wraps, which would corrupt
    the row -- the output has to be byte-exact whatever the terminal is.
    """
    if app.outputFormat == "json":
        payload = machine.toJson(kind, rows) + "\n"
    else:
        payload = machine.toCsv(columns, rows)

    sys.stdout.write(payload)


class _Notice:
    def __init__(self, title, line):
        self.title = title
        self.line = line


@cli.command("list_all_vendors")
@dateRangeOptions
@bankOption
@click.pass_context
def list_all_vendors(ctx, from_date, to_date, bank):
    """List the unique vendors seen in credit card transactions, sorted
    alphabetically.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    vendors = sorted({txn.vendor for txn in txns})

    _emit(
        ctx,
        kind="vendors",
        columns=("vendor",),
        rows=[{"vendor": vendor} for vendor in vendors],
        table=lambda: vendorsTable(vendors, len(txns)),
        emptyMessage="No vendors match this filter.",
        notice=_Notice(
            "Credit card vendors",
            f"Found {len(vendors):,} unique vendors"
            f"{_filterLabel(from_date, to_date, bank=bank)}:",
        ),
    )


@cli.command("list_all_cc_txns")
@dateRangeOptions
@bankOption
@click.pass_context
def list_all_cc_txns(ctx, from_date, to_date, bank):
    """List every credit card transaction, from HBL, Faysal Bank and Standard
    Chartered together.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)

    _emit(
        ctx,
        kind="cc_txns",
        columns=machine.CC_TXN_COLUMNS,
        rows=machine.ccTxnRows(txns),
        table=lambda: ccTxnsTable(txns),
        emptyMessage="No credit card transactions match this filter.",
        notice=_Notice(
            "Credit card transactions",
            f"Found {len(txns):,} CC transactions"
            f"{_filterLabel(from_date, to_date, bank=bank)}:",
        ),
    )


@cli.command("monthly_cc_spending_summary")
@dateRangeOptions
@bankOption
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
@click.pass_context
def monthly_cc_spending_summary(ctx, from_date, to_date, bank, verbose):
    """Summarize credit card spending month by month, one column per currency."""
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title="Month-wise CC spending",
        emptyMessage="No credit card transactions match this filter.",
        line=f"Summarizing {len(txns):,} CC transactions"
        f"{_filterLabel(from_date, to_date, bank=bank)}:",
        detailTable=lambda: ccTxnsTable(txns),
        summaryTable=lambda: monthlySummaryTable(txns),
    )


@cli.command("cc_spend_for_month")
@click.option(
    "--month",
    required=True,
    type=click.DateTime(formats=[MONTH_KEY_FMT]),
    help="The month to total, written as [bold]YYYY-MM[/].",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the total was built from.",
)
@click.pass_context
def cc_spend_for_month(ctx, month, verbose):
    """Total credit card spending for one month, across every card and all
    three banks at once.

    The table's TOTAL row is the answer -- one exact total per currency -- and
    the rows above it say which bank each part of it came from. Currencies are
    never added together: that would need an exchange rate this tool does not
    have.
    """
    monthKey = month.strftime(MONTH_KEY_FMT)
    report = ctx.obj.report()
    txns = [txn for txn in report.ccTxns if monthKeyFor(txn) == monthKey]

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title=f"CC spend for {monthKey}",
        emptyMessage=f"No credit card transactions in {monthKey}.",
        line=f"Totalling {len(txns):,} CC transactions in {monthKey}, "
        f"across all banks:",
        detailTable=lambda: ccTxnsTable(txns),
        summaryTable=lambda: bankSpendTable(txns),
    )


@cli.command("list_all_debit_txns")
@dateRangeOptions
@click.option(
    "--txn-type",
    type=click.Choice(DEBIT_TXN_TYPES),
    default=None,
    help="Only include debit transactions of this type (default: all types).",
)
@click.pass_context
def list_all_debit_txns(ctx, from_date, to_date, txn_type):
    """List every Meezan account debit -- card purchases, ATM withdrawals, bill
    payments and funds transfers.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByDateRange(report.debitTxns, from_date, to_date)
    if txn_type is not None:
        # DebitTxnType is a StrEnum, so it compares equal to the Choice string
        txns = [txn for txn in txns if txn.txnType == txn_type]

    _emit(
        ctx,
        kind="debit_txns",
        columns=machine.DEBIT_TXN_COLUMNS,
        rows=machine.debitTxnRows(txns),
        table=lambda: debitTxnsTable(txns, DEBIT_TXN_TYPES),
        emptyMessage="No account debit transactions match this filter.",
        notice=_Notice(
            "Account debit transactions",
            f"Found {len(txns):,} debit transactions"
            f"{_filterLabel(from_date, to_date, txnType=txn_type)}:",
        ),
    )


@cli.command("monthly_debit_spending_summary")
@dateRangeOptions
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
@click.pass_context
def monthly_debit_spending_summary(ctx, from_date, to_date, verbose):
    """Summarize Meezan account debit spending month by month, one column per
    currency.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByDateRange(report.debitTxns, from_date, to_date)

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title="Month-wise debit spending",
        emptyMessage="No account debit transactions match this filter.",
        line=f"Summarizing {len(txns):,} debit transactions"
        f"{_filterLabel(from_date, to_date)}:",
        detailTable=lambda: debitTxnsTable(txns, DEBIT_TXN_TYPES),
        summaryTable=lambda: monthlySummaryTable(txns),
    )


def _emitMonthly(
    ctx, txns, verbose, title, emptyMessage, line, detailTable, summaryTable
) -> None:
    """Render a monthly summary in whichever format was asked for.

    `summaryTable` is what varies: the same totals grouped by month for a
    summary over time, and by bank for a single month's spend. The machine
    output does not vary with it -- JSON and CSV carry the month-and-currency
    totals either way, so a consumer reads one shape from every command here.
    """
    app = ctx.obj
    perMonth = monthlyTotals(txns)
    perMonthCounts = txnCountsByMonth(txns)
    rows = machine.monthlyRows(perMonth, perMonthCounts)

    if app.machineReadable:
        _writeMachineOutput(app, "monthly_summary", machine.MONTHLY_COLUMNS, rows)
        return

    printRule(title)

    if not txns:
        printEmptyState(emptyMessage)
        return

    printNotice(line)

    if verbose:
        console.print(detailTable())

    console.print()
    console.print(summaryTable())


def main():
    """Console entry point.

    Click's standalone mode already prints a ClickException and exits with its
    `exit_code`, which is what gives `--strict` and a bad backup file their own
    distinct codes. There is nothing to add around it.
    """
    cli()


if __name__ == "__main__":
    main()
