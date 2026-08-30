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
from dataclasses import replace
from pathlib import Path
from time import perf_counter

# rich_click is a drop-in re-export of click that renders --help and usage
# errors through Rich. Every decorator, type and exception below is click's own.
import rich_click as click

from sms_msgs_scraper import __version__
from sms_msgs_scraper.domain.aggregate import (
    MONTH_KEY_FMT,
    countsByGroup,
    monthKeyFor,
    monthlyTotals,
    seriesTotalsByMonth,
    txnCountsByMonth,
    txnSortKey,
)
from sms_msgs_scraper.domain.debit_txn import DebitTxnType
from sms_msgs_scraper.domain.report import DuplicatePolicy
from sms_msgs_scraper.domain.vendors import (
    VendorAliasMap,
    VendorMapError,
    normalizeVendor,
)
from sms_msgs_scraper.parser.registry import REGISTRY
from sms_msgs_scraper.render import machine
from sms_msgs_scraper.render.charts import monthlyChart
from sms_msgs_scraper.render.console_ui import (
    console,
    printEmptyState,
    printHeader,
    printNotice,
    printRule,
    printWarning,
    sanitizeField,
    setNoColor,
    statusSpinner,
)
from sms_msgs_scraper.render.tables import (
    aggregateSpendTable,
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

# What a bar in monthly_vendor_chart may be split by.
GROUP_BY_CHOICES = ("vendor", "bank", "txn-type", "none")

# The series name a credit card transaction takes under --group-by txn-type.
# A CC purchase carries no DebitTxnType, and it is not an absence: it is its
# own kind of transaction, and the chart is the one place both stores meet.
CC_SERIES = "credit_card"

# The single series --group-by none draws. Named rather than blank so the
# totals table under the chart still has something to put in its first column.
ALL_SERIES = "All transactions"


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
        {
            "name": "Across both (credit card + account debit)",
            "commands": [
                "monthly_vendor_chart",
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

    def __init__(
        self,
        filepath,
        quiet,
        strict,
        duplicatePolicy,
        outputFormat,
        vendorMapPath=None,
    ):
        self.filepath = filepath
        self.quiet = quiet
        self.strict = strict
        self.duplicatePolicy = duplicatePolicy
        self.outputFormat = outputFormat
        self.vendorMapPath = vendorMapPath
        self._report = None
        self._vendorMap = None

    @property
    def machineReadable(self) -> bool:
        return self.outputFormat != "table"

    def vendorMap(self):
        """The canonical-vendor table, loaded the first time one is wanted.

        Deferred for the same reason the backup is: a run that never
        mentions vendors should not read a file to answer. Memoised, so
        two accesses in one command do not parse the table twice.
        """
        if self._vendorMap is not None:
            return self._vendorMap

        try:
            if self.vendorMapPath is None:
                vendorMap = VendorAliasMap.loadDefault()
            else:
                vendorMap = VendorAliasMap.loadFromPath(self.vendorMapPath)
        except VendorMapError as error:
            raise click.ClickException(str(error)) from error

        self._vendorMap = vendorMap

        return vendorMap

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
@click.option(
    "--vendor-map",
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        path_type=Path,
    ),
    default=None,
    help="A JSON canonical-vendor table to use instead of the one shipped "
    "with the tool. Must be written [bold]before[/] FILEPATH. Read only when "
    "a command actually asks about vendors.",
)
@click.pass_context
def cli(
    ctx, filepath, no_color, quiet, strict, duplicates, output_format, vendor_map
):
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
        vendorMapPath=vendor_map,
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


def vendorOptions(command):
    """The --vendor / --canonical-vendors pair every command accepts.

    Both are subcommand options rather than group options, so they can be
    written after the filepath where they read naturally. `--vendor-map` is the
    exception and stays on the group, because it names a data source the way
    the backup file itself does.
    """
    command = click.option(
        "--canonical-vendors",
        is_flag=True,
        default=False,
        help="Report each vendor under its canonical name, collapsing the "
        "spellings the alias table groups together. Off by default, so output "
        "carries the strings the banks actually sent.",
    )(command)

    return click.option(
        "--vendor",
        default=None,
        help="Only include transactions whose vendor matches this text. "
        "Case-insensitive substring, tested against the vendor as sent "
        "[bold]and[/] against its canonical name -- so [bold]--vendor PSO[/] "
        "finds every spelling the table groups under PSO.",
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


def _applyVendorOptions(ctx, txns, vendor, canonicalVendors):
    """Filter by vendor, then optionally rewrite vendors to canonical names.

    In that order, and the order is the point. Matching happens against the
    strings the banks sent as well as against canonical names, so a search
    works whether or not the alias table has heard of the merchant; rewriting
    afterwards is presentation, and would have thrown away half of what the
    search needed to see.

    Nothing here loads the alias table unless one of the two options was
    given -- an ordinary listing never touches it.
    """
    if vendor is None and not canonicalVendors:
        return list(txns)

    aliases = ctx.obj.vendorMap()

    if vendor is not None:
        txns = _filterTxnsByVendor(txns, vendor, aliases)

    if canonicalVendors:
        txns = _canonicalizeVendors(txns, aliases)

    return list(txns)


def _filterTxnsByVendor(txns, vendor, aliases):
    """Transactions whose vendor, or whose canonical vendor, contains `vendor`.

    Substring rather than equality because the strings are what they are: the
    merchant a person means by "PSO" is spelled ten ways in the reference
    corpus, half of them with a city glued to the end. Case and internal
    spacing are folded by the same rule the alias table is matched with, so a
    search behaves the same way the grouping does.
    """
    needle = normalizeVendor(vendor)

    if not needle:
        raise click.BadParameter("--vendor needs some text to match on")

    return [
        txn
        for txn in txns
        if needle in normalizeVendor(txn.vendor)
        or needle in normalizeVendor(aliases.canonicalFor(txn.vendor))
    ]


def _canonicalizeVendors(txns, aliases):
    """Rewrite each transaction's vendor to its canonical name.

    Transactions are frozen, so this builds new ones, which re-runs their
    validation -- a canonical name was already checked to be non-empty when the
    table was loaded, so that check can only pass here.

    The re-sort is not cosmetic. Vendor is one of the tie-breakers in the
    documented output order, and a great many transactions share a date and a
    bank because HBL and SCB alerts carry no time of day. Rewriting vendors
    without re-sorting would leave a listing out of the order the tool promises.
    """
    rewritten = []

    for txn in txns:
        canonical = aliases.canonicalFor(txn.vendor)
        rewritten.append(
            txn if canonical == txn.vendor else replace(txn, vendor=canonical)
        )

    return sorted(rewritten, key=txnSortKey)


def _filterLabel(
    fromDate,
    toDate,
    bank=None,
    txnType=None,
    vendor=None,
    canonicalVendors=False,
) -> str:
    """A human-readable suffix describing the active filters.

    The vendor is sanitized because it is the one part of this line that came
    from outside: it is typed on the command line rather than chosen from a
    Choice, and a control character in it would otherwise reach the terminal.
    """
    parts = []

    if bank is not None:
        parts.append(f"bank {bank}")
    if txnType is not None:
        parts.append(f"type {txnType}")
    if vendor is not None:
        parts.append(f'vendor matching "{sanitizeField(vendor)}"')
    # A two-sided range reads as one phrase rather than two clauses.
    if fromDate is not None and toDate is not None:
        parts.append(f"from {fromDate.date()} to {toDate.date()}")
    elif fromDate is not None:
        parts.append(f"from {fromDate.date()}")
    elif toDate is not None:
        parts.append(f"up to {toDate.date()}")

    if canonicalVendors:
        parts.append("canonical vendors")

    return f" ({', '.join(parts)})" if parts else ""


def _spendAggregate(txns, fromDate, toDate, vendor):
    """The aggregate-spend table a filtered listing carries, or None.

    Only a search -- a date range, a vendor needle, or both -- earns one: the
    person filtering is asking "how much was that?", and making them sum a
    column by eye is the gap this closes. An unfiltered listing keeps its
    output exactly as it was, and totals over everything remain the summary
    commands' job.

    Table output only. The JSON and CSV row shapes are a contract a consumer
    may be parsing, and a totals row smuggled into them is precisely the kind
    of row that corrupts a naive reader.
    """
    if fromDate is None and toDate is None and vendor is None:
        return None

    return lambda: aggregateSpendTable(txns)


def _emit(ctx, kind, columns, rows, table, emptyMessage, notice, aggregate=None) -> None:
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

    if aggregate is not None:
        console.print(aggregate())


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
@vendorOptions
@click.pass_context
def list_all_vendors(ctx, from_date, to_date, bank, vendor, canonical_vendors):
    """List the unique vendors seen in credit card transactions, sorted
    alphabetically.

    With [bold]--canonical-vendors[/] this is the list to read before writing
    an alias entry: it is the set of distinct spellings the banks actually
    sent, which is what an alias has to match.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)
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
            f"{_filterLabel(
                from_date,
                to_date,
                bank=bank,
                vendor=vendor,
                canonicalVendors=canonical_vendors,
            )}:",
        ),
    )


@cli.command("list_all_cc_txns")
@dateRangeOptions
@bankOption
@vendorOptions
@click.pass_context
def list_all_cc_txns(ctx, from_date, to_date, bank, vendor, canonical_vendors):
    """List every credit card transaction, from HBL, Faysal Bank and Standard
    Chartered together.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    _emit(
        ctx,
        kind="cc_txns",
        columns=machine.CC_TXN_COLUMNS,
        rows=machine.ccTxnRows(txns),
        table=lambda: ccTxnsTable(txns),
        emptyMessage="No credit card transactions match this filter.",
        aggregate=_spendAggregate(txns, from_date, to_date, vendor),
        notice=_Notice(
            "Credit card transactions",
            f"Found {len(txns):,} CC transactions"
            f"{_filterLabel(
                from_date,
                to_date,
                bank=bank,
                vendor=vendor,
                canonicalVendors=canonical_vendors,
            )}:",
        ),
    )


@cli.command("monthly_cc_spending_summary")
@dateRangeOptions
@bankOption
@vendorOptions
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
@click.pass_context
def monthly_cc_spending_summary(
    ctx, from_date, to_date, bank, vendor, canonical_vendors, verbose
):
    """Summarize credit card spending month by month, one column per currency."""
    report = ctx.obj.report()
    txns = _filterTxnsByBank(report.ccTxns, bank)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title="Month-wise CC spending",
        emptyMessage="No credit card transactions match this filter.",
        line=f"Summarizing {len(txns):,} CC transactions"
        f"{_filterLabel(
            from_date,
            to_date,
            bank=bank,
            vendor=vendor,
            canonicalVendors=canonical_vendors,
        )}:",
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
@vendorOptions
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the total was built from.",
)
@click.pass_context
def cc_spend_for_month(ctx, month, vendor, canonical_vendors, verbose):
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
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title=f"CC spend for {monthKey}",
        emptyMessage=f"No credit card transactions in {monthKey}.",
        line=f"Totalling {len(txns):,} CC transactions in {monthKey}, "
        f"across all banks"
        f"{_filterLabel(
            None,
            None,
            vendor=vendor,
            canonicalVendors=canonical_vendors,
        )}:",
        detailTable=lambda: ccTxnsTable(txns),
        summaryTable=lambda: bankSpendTable(txns),
    )


@cli.command("list_all_debit_txns")
@dateRangeOptions
@vendorOptions
@click.option(
    "--txn-type",
    type=click.Choice(DEBIT_TXN_TYPES),
    default=None,
    help="Only include debit transactions of this type (default: all types).",
)
@click.pass_context
def list_all_debit_txns(ctx, from_date, to_date, vendor, canonical_vendors, txn_type):
    """List every Meezan account debit -- card purchases, ATM withdrawals, bill
    payments and funds transfers.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByDateRange(report.debitTxns, from_date, to_date)
    if txn_type is not None:
        # DebitTxnType is a StrEnum, so it compares equal to the Choice string
        txns = [txn for txn in txns if txn.txnType == txn_type]
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    _emit(
        ctx,
        kind="debit_txns",
        columns=machine.DEBIT_TXN_COLUMNS,
        rows=machine.debitTxnRows(txns),
        table=lambda: debitTxnsTable(txns, DEBIT_TXN_TYPES),
        emptyMessage="No account debit transactions match this filter.",
        aggregate=_spendAggregate(txns, from_date, to_date, vendor),
        notice=_Notice(
            "Account debit transactions",
            f"Found {len(txns):,} debit transactions"
            f"{_filterLabel(
                from_date,
                to_date,
                txnType=txn_type,
                vendor=vendor,
                canonicalVendors=canonical_vendors,
            )}:",
        ),
    )


@cli.command("monthly_debit_spending_summary")
@dateRangeOptions
@vendorOptions
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Also list the transactions the summary was built from.",
)
@click.pass_context
def monthly_debit_spending_summary(
    ctx, from_date, to_date, vendor, canonical_vendors, verbose
):
    """Summarize Meezan account debit spending month by month, one column per
    currency.
    """
    report = ctx.obj.report()
    txns = _filterTxnsByDateRange(report.debitTxns, from_date, to_date)
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    _emitMonthly(
        ctx,
        txns,
        verbose,
        title="Month-wise debit spending",
        emptyMessage="No account debit transactions match this filter.",
        line=f"Summarizing {len(txns):,} debit transactions"
        f"{_filterLabel(
            from_date,
            to_date,
            vendor=vendor,
            canonicalVendors=canonical_vendors,
        )}:",
        detailTable=lambda: debitTxnsTable(txns, DEBIT_TXN_TYPES),
        summaryTable=lambda: monthlySummaryTable(txns),
    )


@cli.command("monthly_vendor_chart")
@dateRangeOptions
@vendorOptions
@click.option(
    "--group-by",
    type=click.Choice(GROUP_BY_CHOICES),
    default="vendor",
    show_default=True,
    help="What each bar is split into: [bold]vendor[/] the distinct vendor "
    "strings, [bold]bank[/] the issuing or debiting bank, [bold]txn-type[/] "
    "the kind of transaction, [bold]none[/] one solid bar per month.",
)
@click.pass_context
def monthly_vendor_chart(ctx, from_date, to_date, vendor, canonical_vendors, group_by):
    """Chart spending month by month as stacked bars, split by vendor.

    The only command that reads [bold]both[/] stores at once: it matches credit
    card transactions and Meezan account debits together, because a merchant is
    a merchant and which of the two a bill was paid from is not something you
    should have to know before you can search for it.

    Every month between the first and the last is drawn, [bold]including the
    ones with nothing in them[/] -- a chart that closes its gaps up is a chart
    that lies about the shape of the series. Each currency gets its own chart:
    one bar cannot mix PKR and USD without an exchange rate.

    With [bold]--format json[/] or [bold]--format csv[/] this writes the series
    behind the chart -- month, series, currency, amount -- with every series
    named rather than folded into "Other".
    """
    report = ctx.obj.report()
    txns = sorted(report.ccTxns + report.debitTxns, key=txnSortKey)
    txns = _filterTxnsByDateRange(txns, from_date, to_date)
    txns = _applyVendorOptions(ctx, txns, vendor, canonical_vendors)

    seriesFor, orderedNames, seriesHeader = _seriesGrouping(group_by, txns)
    perMonth = seriesTotalsByMonth(txns, seriesFor)

    app = ctx.obj

    if app.machineReadable:
        _writeMachineOutput(
            app, "monthly_chart", machine.CHART_COLUMNS, machine.chartRows(perMonth)
        )
        return

    printRule("Monthly spending chart")

    if not txns:
        printEmptyState("No transactions match this filter.")
        return

    printNotice(
        f"Charting {len(txns):,} transactions by {group_by}"
        f"{_filterLabel(
            from_date,
            to_date,
            vendor=vendor,
            canonicalVendors=canonical_vendors,
        )}:"
    )

    counts = countsByGroup(txns, lambda txn: (seriesFor(txn), txn.money.currency))
    console.print(monthlyChart(perMonth, orderedNames, counts, seriesHeader))


def _seriesGrouping(groupBy, txns):
    """The series function, the stable series order, and the column header.

    The order is what the chart assigns colours and glyphs from, so it must
    depend only on *which* series exist and never on how large they are --
    otherwise narrowing a date range repaints the survivors and two runs stop
    being comparable by eye.

    Bank and transaction type keep the colours they wear in every table in this
    tool, which is why they are ordered by the registry and by the enum rather
    than alphabetically: those orders are the ones the rest of the output uses.
    Vendors have no identity of their own to colour by, so they take the four
    computed series colours in alphabetical order -- stable under any filter
    that does not change the set of vendors.
    """
    if groupBy == "none":
        return (lambda txn: ALL_SERIES), [ALL_SERIES], "Series"

    if groupBy == "bank":
        present = {txn.bank for txn in txns}
        ordered = [bankId for bankId in REGISTRY.bankIds if bankId in present]
        ordered.extend(sorted(present.difference(REGISTRY.bankIds)))

        return (lambda txn: txn.bank), ordered, "Bank"

    if groupBy == "txn-type":
        # A credit card transaction has no txnType field: a CC purchase is its
        # own kind, and naming it here is what lets the two stores be charted
        # on one axis at all.
        def seriesFor(txn):
            return str(getattr(txn, "txnType", CC_SERIES))

        present = {seriesFor(txn) for txn in txns}
        ordered = [
            name
            for name in (CC_SERIES, *DEBIT_TXN_TYPES)
            if name in present
        ]

        return seriesFor, ordered, "Type"

    return (lambda txn: txn.vendor), sorted({txn.vendor for txn in txns}), "Vendor"


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
