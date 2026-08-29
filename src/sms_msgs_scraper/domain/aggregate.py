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


def countsByAttribute(txns, attribute: str) -> dict[str, int]:
    """How many transactions carry each value of one attribute.

    Used for the per-bank and per-type breakdowns under a listing.
    """
    counts: dict[str, int] = defaultdict(int)

    for txn in txns:
        counts[str(getattr(txn, attribute))] += 1

    return dict(counts)


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
