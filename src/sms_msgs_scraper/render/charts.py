"""Monthly spending charts, drawn with block characters.

The fourth peer renderer, alongside the tables and the JSON and CSV writers. It
is a separate module from `tables.py` rather than another function in it because
a chart is not a table: it has a scale, an axis and a legend, and none of the
table furniture in `console_ui` describes any of those.

Three decisions here are load-bearing rather than cosmetic.

**Every month between the first and the last is drawn, including the empty
ones.** Plotting only the months that carry transactions closes the gaps up, so
a bill that went unpaid in one month renders as the month before sitting next to
the month after. The shape of a time series is the entire reason to draw it, and
that shape would be a lie.

**Currencies are charted separately, never stacked together.** One bar cannot
mix PKR and USD without an exchange rate, which this tool does not have, so a
filter spanning three currencies produces three charts.

**Colour is never the only thing telling two series apart.** Each series also
carries its own block glyph and a fixed position in the stack, so the chart
survives a terminal with no colour, a redirect to a file, and a reader who
cannot distinguish two of the hues. The four series colours in `console_ui` are
computed to separate under simulated colour-blindness against both a near-black
and a near-white terminal background; the glyphs are what cover the case that
band is too narrow to solve on its own.
"""

from decimal import Decimal

from sms_msgs_scraper.domain.aggregate import monthKeysBetween
from sms_msgs_scraper.domain.money import Money
from sms_msgs_scraper.render.console_ui import (
    amountText,
    console,
    countText,
    labelText,
    sanitizeField,
    segmentsText,
    stackedGroup,
    summaryTable,
)
from sms_msgs_scraper.render.tables import CURRENCY_COLUMN_ORDER

# At most four named series, and then everything else together. The cap is not
# a layout preference: `console_ui` carries exactly four series colours because
# a fifth well-separated hue does not fit in the lightness band that works
# against both a dark and a light terminal. Cycling a colour back round would
# give two series the same identity, which is worse than saying "Other".
MAX_NAMED_SERIES = 4
OTHER_SERIES = "Other"

# One glyph per series, in the same order as the colours. Shade blocks rather
# than shapes: they are single-width in every terminal font that ships with
# Windows, which a geometric shape is not.
SERIES_GLYPHS = ("█", "▓", "▒", "░")
OTHER_GLYPH = "▚"

# A month with no transactions in it. Dotted rather than blank, so an empty
# month is visibly a month rather than a gap in the rendering.
GAP_GLYPH = "·"

# Bar widths outside this range stop being readable in either direction.
MIN_BAR_WIDTH = 20
MAX_BAR_WIDTH = 56

# What the fixed-width parts of a row cost, so the bar can have the rest.
_LABEL_WIDTH = 11
_TOTAL_WIDTH = 14
_DELTA_WIDTH = 10

_LEGEND_NAME_LIMIT = 28


def _styleForSlot(index: int) -> str:
    """The theme style for the series in position `index`, `Other` last."""
    if index >= MAX_NAMED_SERIES:
        return "series.other"

    return f"series.{index + 1}"


def _glyphForSlot(index: int) -> str:
    if index >= MAX_NAMED_SERIES:
        return OTHER_GLYPH

    return SERIES_GLYPHS[index]


def barWidthFor(consoleWidth: int) -> int:
    """How wide the bars may be in a terminal of this width."""
    available = consoleWidth - _LABEL_WIDTH - _TOTAL_WIDTH - _DELTA_WIDTH

    return max(MIN_BAR_WIDTH, min(MAX_BAR_WIDTH, available))


def currenciesIn(perMonth) -> list[str]:
    """Every currency appearing anywhere in a month/series/currency mapping."""
    currencies = set()

    for perSeries in perMonth.values():
        for perCurrency in perSeries.values():
            currencies.update(perCurrency)

    # Same order the summary tables put their currency columns in, so a chart
    # and a summary of the same transactions read left to right the same way.
    ordered = [code for code in CURRENCY_COLUMN_ORDER if code in currencies]
    ordered.extend(sorted(code for code in currencies
                          if code not in CURRENCY_COLUMN_ORDER))

    return ordered


def _seriesTotals(perMonth, currency) -> dict[str, Decimal]:
    """Each series' total in one currency, across every month."""
    totals: dict[str, Decimal] = {}

    for perSeries in perMonth.values():
        for name, perCurrency in perSeries.items():
            money = perCurrency.get(currency)
            if money is not None:
                totals[name] = totals.get(name, Decimal(0)) + money.amount

    return totals


def _seriesCounts(perMonth) -> dict[str, int]:
    """How many month/currency cells each series appears in.

    A currency-free measure of how much of the chart a series accounts for,
    which is what makes it usable for ranking when adding the currencies
    together is not allowed.
    """
    counts: dict[str, int] = {}

    for perSeries in perMonth.values():
        for name, perCurrency in perSeries.items():
            counts[name] = counts.get(name, 0) + len(perCurrency)

    return counts


def selectSeries(perMonth, orderedNames, currencies):
    """The series the whole chart names, in the caller's stable order.

    Chosen **once for the entire chart** rather than per currency, because the
    glyph and colour a series wears come from its position in this list. Making
    the choice per currency let the same bank be a solid block in one currency's
    chart and a shaded one in the next, which is precisely the repainting that
    makes two charts of the same run incomparable.

    Selection is by size when the chart has one currency, and by how much of
    the chart a series occupies when it has more than one. The second rule
    exists because the first is unavailable there: ranking series across
    currencies by amount would mean adding PKR to USD, and this tool has no
    exchange rate with which to do that.

    Everything past the fourth named series becomes `Other`, because there are
    four series colours -- see `console_ui` for why there cannot be a fifth.
    """
    present = [
        name
        for name in orderedNames
        if any(name in perSeries for perSeries in perMonth.values())
    ]
    # Anything the caller's order did not mention still has to be placed, or it
    # would be silently added to whichever series happened to be last.
    listed = set(orderedNames)
    seen = {name for perSeries in perMonth.values() for name in perSeries}
    present.extend(sorted(seen - listed))

    if len(present) <= MAX_NAMED_SERIES:
        return present

    if len(currencies) == 1:
        weight = _seriesTotals(perMonth, currencies[0])
    else:
        weight = _seriesCounts(perMonth)

    biggest = sorted(present, key=lambda name: (-weight.get(name, 0), name))
    named = set(biggest[:MAX_NAMED_SERIES])

    return [name for name in present if name in named] + [OTHER_SERIES]


def foldSeries(perMonth, currency, names):
    """One currency's month totals, laid out positionally against `names`.

    Returns `(monthlyAmounts, presentSlots)`: the amounts per month, and which
    slots this currency actually uses -- a currency spent on only one of the
    named series still draws that series in the colour the whole chart gave it,
    and lists only that one in its legend.
    """
    slots = {name: index for index, name in enumerate(names)}
    lastSlot = len(names) - 1
    monthlyAmounts: dict[str, list[Decimal]] = {}
    presentSlots = set()

    for monthKey, perSeries in perMonth.items():
        amounts = [Decimal(0)] * len(names)
        for name, perCurrency in perSeries.items():
            money = perCurrency.get(currency)
            if money is None:
                continue
            slot = slots.get(name, lastSlot)
            amounts[slot] += money.amount
            presentSlots.add(slot)
        if any(amounts):
            monthlyAmounts[monthKey] = amounts

    return monthlyAmounts, presentSlots


def segmentWidths(amounts, scale: Decimal, barWidth: int) -> list[int]:
    """Cell counts per segment that sum to exactly the bar's own cell count.

    Rounding each segment independently would leave bars a cell wider or
    narrower than their total says they are, and a reader comparing two bars by
    eye is reading exactly that. The last segment absorbs the rounding, so the
    bar length always encodes the total.
    """
    total = sum(amounts, Decimal(0))

    if total <= 0 or scale <= 0:
        return [0] * len(amounts)

    totalCells = max(1, round(float(total) / float(scale) * barWidth))

    cells = []
    used = 0
    for amount in amounts[:-1]:
        count = round(float(amount) / float(total) * totalCells)
        cells.append(count)
        used += count

    cells.append(max(0, totalCells - used))

    return cells


def _monthLabel(monthKey: str) -> str:
    """`2025-01` as `Jan 2025`, without going through a date."""
    year, month = monthKey.split("-")
    names = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )

    return f"{names[int(month) - 1]} {year}"


def _compactAmount(value: Decimal) -> str:
    """An axis tick: short enough to sit under a bar without colliding."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1000:,.0f}k"

    return f"{value:,.0f}"


def _axisLine(scale: Decimal, barWidth: int):
    """The baseline and its tick labels, as two renderables.

    A tick whose label would touch its neighbour is dropped rather than
    shortened: an axis that reads `030k60k` is worse than one with fewer ticks
    on it.
    """
    # The corner sits one column left of the bars; the rule then runs the
    # bar's full width, so the last tick lands under the last cell.
    baseline = " " * (_LABEL_WIDTH - 1) + "└" + "─" * barWidth

    cells = [" "] * (_LABEL_WIDTH + barWidth + 4)
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        label = _compactAmount(scale * Decimal(str(fraction)))
        position = _LABEL_WIDTH + round(fraction * (barWidth - 1))

        if fraction == 0.0:
            start = position
        elif fraction == 1.0:
            start = position - len(label) + 1
        else:
            start = position - len(label) // 2

        start = max(0, min(start, len(cells) - len(label)))
        window = slice(max(0, start - 1), start + len(label) + 1)

        if all(cell == " " for cell in cells[window]):
            cells[start : start + len(label)] = list(label)

    return (
        labelText(baseline, style="muted"),
        labelText("".join(cells).rstrip(), style="muted"),
    )


def _legendLine(names, slots):
    """The legend: each series' glyph in its own colour, then its name.

    Only the slots this currency actually spent in are listed, and each keeps
    the glyph and colour the chart gave it -- so a series absent from one
    currency leaves a gap in that legend rather than shuffling the rest along.

    Names come out of SMS message bodies, so they are sanitised and truncated
    here rather than trusted -- this is the one line of a chart that carries
    data rather than furniture.
    """
    parts: list[tuple[str, str | None]] = [(" " * (_LABEL_WIDTH - 2), None)]

    for index in sorted(slots):
        label = sanitizeField(names[index])
        if len(label) > _LEGEND_NAME_LIMIT:
            label = label[: _LEGEND_NAME_LIMIT - 1] + "…"
        parts.append((_glyphForSlot(index) * 2, _styleForSlot(index)))
        parts.append((f" {label}   ", "muted"))

    return segmentsText(parts)


def _chartRow(monthKey, amounts, names, scale, currency, barWidth, previous):
    """One month: its label, its stacked bar, its exact total, its change."""
    parts: list[tuple[str, str | None]] = [
        ("  ", None),
        (f"{_monthLabel(monthKey):<9}", "column.date"),
    ]

    if amounts is None:
        parts.append((GAP_GLYPH * barWidth, "column.empty"))
        parts.append(("   no transactions", "column.empty"))

        return segmentsText(parts), None

    widths = segmentWidths(amounts, scale, barWidth)
    for index, width in enumerate(widths):
        if width:
            parts.append((_glyphForSlot(index) * width, _styleForSlot(index)))

    total = sum(amounts, Decimal(0))
    parts.append((" " * max(0, barWidth - sum(widths)), None))
    parts.append((f"  {Money(total, currency).formatted():>11}", "column.total"))

    # A change is only shown against the month immediately before. After a gap
    # there is nothing honest to compare against, so the column stays empty
    # rather than quietly spanning the missing month.
    if previous is not None and previous > 0:
        change = (total - previous) / previous * 100
        arrow = "▲" if change > 0 else "▼"
        parts.append(
            (f"  {arrow} {abs(change):5.1f}%", "warning" if change > 0 else "info")
        )

    return segmentsText(parts), total


def _seriesTable(names, slots, monthlyAmounts, currency, counts, seriesHeader):
    """The exact numbers behind one chart, one row per series.

    Present for the same reason a web chart ships a table view: the bars encode
    proportion, and a reader who needs the figure should not have to measure it
    off the axis. It is also what makes the chart legible to someone who cannot
    tell two of the series colours apart.
    """
    table = summaryTable(f"{currency} totals")
    table.add_column(seriesHeader)
    table.add_column("Txns", justify="right")
    table.add_column("Total", justify="right")

    totals = [Decimal(0)] * len(names)
    for amounts in monthlyAmounts.values():
        for index, amount in enumerate(amounts):
            totals[index] += amount

    for index in sorted(slots):
        table.add_row(
            segmentsText(
                [
                    (_glyphForSlot(index), _styleForSlot(index)),
                    (f" {sanitizeField(names[index])}", "column.vendor"),
                ]
            ),
            countText(counts.get(names[index], 0)),
            amountText(Money(totals[index], currency)),
        )

    table.add_section()
    table.add_row(
        labelText("TOTAL", style="column.total"),
        countText(
            sum(counts.get(names[index], 0) for index in slots),
            style="column.total",
        ),
        amountText(
            Money(sum((totals[index] for index in slots), Decimal(0)), currency)
        ),
    )

    return table


def monthlyChart(perMonth, orderedNames, counts, seriesHeader, barWidth=None):
    """One chart per currency, stacked into a single renderable.

    `perMonth` is what `seriesTotalsByMonth` returns; `orderedNames` is the
    stable series order the colours are assigned from; `counts` maps a
    `(series, currency)` pair to how many transactions it holds.
    """
    if barWidth is None:
        barWidth = barWidthFor(console.width)

    monthKeys = sorted(perMonth)
    everyMonth = monthKeysBetween(monthKeys[0], monthKeys[-1])

    currencies = currenciesIn(perMonth)
    names = selectSeries(perMonth, orderedNames, currencies)

    blocks = []

    for currency in currencies:
        monthlyAmounts, slots = foldSeries(perMonth, currency, names)
        if not monthlyAmounts:
            continue

        scale = max(
            sum(amounts, Decimal(0)) for amounts in monthlyAmounts.values()
        )

        rows: list = [segmentsText([(" ", None)])]
        if len(slots) > 1:
            rows.append(_legendLine(names, slots))
            rows.append(segmentsText([(" ", None)]))

        previous = None
        for monthKey in everyMonth:
            row, total = _chartRow(
                monthKey,
                monthlyAmounts.get(monthKey),
                names,
                scale,
                currency,
                barWidth,
                previous,
            )
            rows.append(row)
            previous = total

        rows.extend(_axisLine(scale, barWidth))
        rows.append(segmentsText([(" ", None)]))

        # Counts are keyed by (series, currency), so a chart of one currency
        # never reports another currency's transactions in its totals table.
        thisCurrency = {
            name: count for (name, code), count in counts.items() if code == currency
        }
        rows.append(
            _seriesTable(
                names, slots, monthlyAmounts, currency,
                _foldCounts(names, thisCurrency), seriesHeader,
            )
        )
        blocks.append(stackedGroup(*rows))

    return stackedGroup(*blocks)


def _foldCounts(names, counts) -> dict[str, int]:
    """Transaction counts against the folded series, `Other` absorbing the rest."""
    if OTHER_SERIES not in names:
        return dict(counts)

    named = set(names) - {OTHER_SERIES}
    folded = {name: counts.get(name, 0) for name in named}
    folded[OTHER_SERIES] = sum(
        count for name, count in counts.items() if name not in named
    )

    return folded
