"""Aggregation over `Money`.

Totals are computed exactly and only then formatted. The distinction is the
whole point: summing a year of transactions as binary floating point drifted 21
of 93 bank/month/currency buckets in the reference corpus, and every one of
those drifts was invisible because `,.2f` rounded the display back to something
plausible. The arithmetic here is `Decimal` throughout, so what is formatted is
what was actually spent.

Currencies are never mixed. A month with PKR, USD and CAD spending holds three
separate totals, because adding them would require an exchange rate and this
tool does not have one.
"""

from collections import defaultdict

from sms_msgs_scraper.domain.money import Money

# The key a month is grouped under, and the order it sorts in.
MONTH_KEY_FMT = "%Y-%m"


def monthKeyFor(txn) -> str:
    return txn.date.strftime(MONTH_KEY_FMT)


def txnSortKey(txn):
    """The documented total order for a transaction listing.

    Output order used to be whatever order the messages happened to sit in the
    XML file, which is stable for one file and meaningless across two. Sorting
    on the transaction's own timestamp makes a listing comparable between runs,
    between merged backups and between exports.

    The tie-breakers exist to make the order *total*: many transactions share a
    timestamp, and HBL and SCB alerts carry a date only, so a great many share
    midnight on the same day. Bank, vendor, currency and amount resolve those
    deterministically.

    It lives here, rather than privately in the orchestrator that first sorts a
    report, because *vendor* is one of the tie-breakers: rewriting vendors to
    their canonical names can reorder a listing, so anything that rewrites them
    has to be able to restore the order this defines.
    """
    return (
        txn.date,
        txn.bank,
        txn.vendor,
        txn.money.currency,
        txn.money.amount,
    )


def totalsByGroup(txns, keyFor) -> dict[str, dict[str, Money]]:
    """Exact totals per group, per currency, grouping by whatever `keyFor`
    returns.

    Only currencies actually spent within a group appear in that group's
    mapping; the renderer decides which columns to show and what an absent cell
    looks like. Seeding every group with every currency here would make
    "nothing was spent" and "zero was spent" the same value.
    """
    perGroup: dict[str, dict[str, Money]] = defaultdict(dict)

    for txn in txns:
        groupTotals = perGroup[keyFor(txn)]
        currency = txn.money.currency
        running = groupTotals.get(currency)
        groupTotals[currency] = txn.money if running is None else running + txn.money

    return dict(perGroup)


def monthlyTotals(txns) -> dict[str, dict[str, Money]]:
    """Exact totals per month, per currency."""
    return totalsByGroup(txns, monthKeyFor)


def txnCountsByMonth(txns) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)

    for txn in txns:
        counts[monthKeyFor(txn)] += 1

    return dict(counts)


def countsByGroup(txns, keyFor) -> dict[str, int]:
    """How many transactions fall in each group, grouping by `keyFor`.

    The counterpart of `totalsByGroup` for the cases where a chart's series is
    computed rather than read off an attribute, so `countsByAttribute` below
    cannot express it.
    """
    counts: dict[str, int] = defaultdict(int)

    for txn in txns:
        counts[keyFor(txn)] += 1

    return dict(counts)


def countsByAttribute(txns, attribute: str) -> dict[str, int]:
    """How many transactions carry each value of one attribute.

    Used for the per-bank and per-type breakdowns under a listing.
    """
    counts: dict[str, int] = defaultdict(int)

    for txn in txns:
        counts[str(getattr(txn, attribute))] += 1

    return dict(counts)


def totalsByCurrency(txns) -> dict[str, Money]:
    """One exact total per currency across an entire listing, ungrouped.

    The flat counterpart of `grandTotals`: what a filtered listing adds up to,
    with no month or bank breakdown in between. Currencies stay separate here
    exactly as they do everywhere else in this module.
    """
    totals: dict[str, Money] = {}

    for txn in txns:
        currency = txn.money.currency
        running = totals.get(currency)
        totals[currency] = txn.money if running is None else running + txn.money

    return totals


def grandTotals(perGroup: dict[str, dict[str, Money]]) -> dict[str, Money]:
    """Fold a grouped breakdown -- by month, or by bank -- back into one total
    per currency.
    """
    totals: dict[str, Money] = {}

    for groupTotals in perGroup.values():
        for currency, money in groupTotals.items():
            running = totals.get(currency)
            totals[currency] = money if running is None else running + money

    return totals


def seriesTotalsByMonth(txns, seriesFor) -> dict[str, dict[str, dict[str, Money]]]:
    """Exact totals nested month -> series -> currency.

    The two-dimensional counterpart of `totalsByGroup`, for a chart that draws
    one bar per month and splits each bar by whatever `seriesFor` returns. It
    is a separate function rather than `totalsByGroup` over a composite key
    because a renderer needs the two dimensions apart: months decide the rows
    and the axis scale, series decide the segments and the legend, and a
    "2025-01\x00KE ..." key would have to be taken back apart to get either.

    Currencies stay separate at the innermost level for the same reason they do
    everywhere else in this module: a month holding PKR and USD spending has
    two answers, and a chart draws it as two charts.
    """
    perMonth: dict[str, dict[str, dict[str, Money]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    for txn in txns:
        seriesTotals = perMonth[monthKeyFor(txn)][seriesFor(txn)]
        currency = txn.money.currency
        running = seriesTotals.get(currency)
        seriesTotals[currency] = txn.money if running is None else running + txn.money

    return {
        monthKey: dict(perSeries) for monthKey, perSeries in perMonth.items()
    }


def monthKeysBetween(firstKey: str, lastKey: str) -> list[str]:
    """Every month key from `firstKey` to `lastKey` inclusive, gaps included.

    A chart has to draw the months nothing was spent in. Plotting only the
    months that carry transactions silently closes the gap up, so a bill that
    went unpaid in July renders as June sitting next to August -- which is the
    one thing a reader of a time series must not be shown, because the shape of
    the line is the whole point.

    Keys are `MONTH_KEY_FMT` strings, and arithmetic is done on the integers
    behind them rather than through a date, so no day-of-month has to be
    invented to step a month forward.
    """
    firstYear, firstMonth = (int(part) for part in firstKey.split("-"))
    lastYear, lastMonth = (int(part) for part in lastKey.split("-"))

    keys = []
    year, month = firstYear, firstMonth

    while (year, month) <= (lastYear, lastMonth):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return keys
