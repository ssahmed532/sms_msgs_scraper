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


def monthlyTotals(txns) -> dict[str, dict[str, Money]]:
    """Exact totals per month, per currency.

    Only currencies actually spent in a month appear in that month's mapping;
    the renderer decides which columns to show and what an absent cell looks
    like. Seeding every month with every currency here would make "nothing was
    spent" and "zero was spent" the same value.
    """
    perMonth: dict[str, dict[str, Money]] = defaultdict(dict)

    for txn in txns:
        monthTotals = perMonth[monthKeyFor(txn)]
        currency = txn.money.currency
        running = monthTotals.get(currency)
        monthTotals[currency] = txn.money if running is None else running + txn.money

    return dict(perMonth)


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


def grandTotals(perMonth: dict[str, dict[str, Money]]) -> dict[str, Money]:
    """Fold a month-by-month breakdown back into one total per currency."""
    totals: dict[str, Money] = {}

    for monthTotals in perMonth.values():
        for currency, money in monthTotals.items():
            running = totals.get(currency)
            totals[currency] = money if running is None else running + money

    return totals
