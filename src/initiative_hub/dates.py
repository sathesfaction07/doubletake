"""Reading a date-like string under both orders, and refusing to pick one.

`dateutil` is deliberately not used. Its `dayfirst` flag takes the decision this module exists to
expose: you set it once, globally, and it silently resolves every ambiguous value in the direction
you guessed. That is the behaviour under examination, so it cannot also be the implementation.

Everything here is `datetime.date` plus arithmetic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from .models import Ambiguity, ColumnOrder, DateReading

# Two numeric fields and a year, separated by / - or . Anchored, because a date embedded in prose is
# a different problem and silently pulling one out of a sentence would invent structure.
NUMERIC = re.compile(r"^\s*(\d{1,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})\s*$")

# A spelled-out month. Accepted because these values cannot be misread in themselves -- and NOT
# because they help with the numeric ones. An earlier comment here claimed that one `04 Mar 2026` in
# a sea of `03/04/2026` "establishes the order for all of them", which is false: it is a different
# format, so it is not evidence about theirs. `implied_order` returns None for these, and probing the
# parser is what exposed the claim.
MONTH_NAMES = {
    name: number
    for number, names in {
        1: ("jan", "january"), 2: ("feb", "february"), 3: ("mar", "march"),
        4: ("apr", "april"), 5: ("may",), 6: ("jun", "june"),
        7: ("jul", "july"), 8: ("aug", "august"), 9: ("sep", "sept", "september"),
        10: ("oct", "october"), 11: ("nov", "november"), 12: ("dec", "december"),
    }.items()
    for name in names
}

TEXTUAL = re.compile(
    r"^\s*(?:(\d{1,2})\s*[-\s/]\s*([A-Za-z]{3,9})|([A-Za-z]{3,9})\s*[-\s/]\s*(\d{1,2}))"
    r"\s*[,\-\s/]\s*(\d{2,4})\s*$"
)

ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def _year(raw: str) -> int | None:
    """Expand a two-digit year, and say so in the docs rather than in a comment nobody reads.

    A two-digit year is its own ambiguity -- `26` is 2026 or 1926 -- and this resolves it to the
    2000s. That is a guess. It is a *bounded* guess, unlike date order: nobody submits a
    continuous-improvement initiative dated 1926, so the wrong reading is implausible rather than
    merely unlucky. Date order has no such escape, which is why one gets resolved here and the other
    does not.
    """
    try:
        value = int(raw)
    except ValueError:
        return None
    if len(raw) <= 2:
        return 2000 + value
    return value


def _build(year: int | None, month: int, day: int) -> date | None:
    if year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def read(raw: str, *, row: int | None = None) -> DateReading:
    """Read a date string under both field orders without choosing between them.

    Returns both candidate dates and a classification. The classification is the whole point: it
    distinguishes a value that CANNOT be wrong from one that cannot be checked.
    """
    text = raw.strip()
    if not text:
        return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)

    # ISO first. Unambiguous by construction, which is the entire argument for using it.
    iso = ISO.match(text)
    if iso:
        parsed = _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if parsed is not None:
            return DateReading(
                raw=raw, ambiguity=Ambiguity.UNAMBIGUOUS,
                day_first=parsed, month_first=parsed, row=row,
            )
        return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)

    textual = TEXTUAL.match(text)
    if textual:
        day_text = textual.group(1) or textual.group(4)
        month_text = textual.group(2) or textual.group(3)
        month = MONTH_NAMES.get(month_text.lower())
        if month is None:
            return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)
        parsed = _build(_year(textual.group(5)), month, int(day_text))
        if parsed is None:
            return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)
        return DateReading(
            raw=raw, ambiguity=Ambiguity.UNAMBIGUOUS,
            day_first=parsed, month_first=parsed, row=row,
        )

    numeric = NUMERIC.match(text)
    if not numeric:
        return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)

    first, second, year_text = numeric.group(1), numeric.group(2), numeric.group(3)
    year = _year(year_text)
    as_day_first = _build(year, int(second), int(first))
    as_month_first = _build(year, int(first), int(second))

    if as_day_first is None and as_month_first is None:
        return DateReading(raw=raw, ambiguity=Ambiguity.UNPARSEABLE, row=row)

    if as_day_first is not None and as_month_first is not None:
        # Both readings are real dates. Whether that matters depends on whether they AGREE.
        ambiguity = (
            Ambiguity.IDENTICAL if as_day_first == as_month_first else Ambiguity.AMBIGUOUS
        )
        return DateReading(
            raw=raw, ambiguity=ambiguity,
            day_first=as_day_first, month_first=as_month_first, row=row,
        )

    # Exactly one reading is a real date, so this value PROVES the column's order.
    return DateReading(
        raw=raw, ambiguity=Ambiguity.UNAMBIGUOUS,
        day_first=as_day_first, month_first=as_month_first, row=row,
    )


def read_all(values: Iterable[str]) -> list[DateReading]:
    return [read(value, row=index) for index, value in enumerate(values, start=1)]


def ambiguity_probability(*, year: int = 2026) -> float:
    """The exact fraction of dates in a year that read two ways.

    Ambiguity requires the day field to be 12 or less, because the month field always is. So the
    count is 12 months x 12 days, and for a 365-day year that is 144/365.

    Computed rather than hard-coded so a leap year gives 144/366 without anyone having to remember.
    A common shortcut writes this as 12/30.4 -- the average month length -- which lands on almost
    the same number by the wrong route, and would be wrong for any span that is not a whole year.
    """
    start, end = date(year, 1, 1), date(year, 12, 31)
    days = (end - start).days + 1
    ambiguous = sum(1 for month in range(1, 13) for day in range(1, 13)
                    if _build(year, month, day) is not None)
    return ambiguous / days


def unknowable_probability(rows: int, *, year: int = 2026) -> float:
    """Chance a column of `rows` uniformly-spread dates contains no disambiguating value.

    `p ** rows`, where p is the single-value ambiguity rate. Falls off fast -- about 39% at one row,
    6% at three, 1% at five -- which makes small columns the risky ones, and slicing a dataset by
    team or by quarter is exactly how small columns get made.

    The uniformity assumption is the load-bearing one and it is often false in the direction that
    hurts. Submissions clustered on a monthly reporting deadline all land in days 1-12, which drives
    p to 1.0 and makes every column unknowable regardless of size. `setup.sh` demonstrates that case
    rather than leaving it as a caveat.
    """
    if rows <= 0:
        return 1.0
    return ambiguity_probability(year=year) ** rows


def informative_rows_needed(target: float = 0.99, *, year: int = 2026) -> int:
    """How many uniform dates you need before the column is knowable with probability `target`.

    Answers the operational question directly: how many rows must a slice have before its inferred
    date order can be trusted. Returns the smallest count, found by iteration rather than by logs,
    because the loop is three lines and cannot be off by a rounding step.
    """
    rows = 0
    while 1.0 - unknowable_probability(rows, year=year) < target:
        rows += 1
        if rows > 1000:
            break
    return rows


def divergence(reading: DateReading) -> int | None:
    return reading.divergence_days


def order_of(reading: DateReading) -> ColumnOrder | None:
    return reading.implied_order
