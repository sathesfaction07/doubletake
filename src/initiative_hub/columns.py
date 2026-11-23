"""Inferring a column's date order from the values that could only have been written one way.

A single ambiguous value tells you nothing. A column of them can still tell you everything, provided
at least one row happens to carry a field above 12 -- and whether one does is luck, not design.

Three outcomes matter and they are not degrees of the same thing:

  * an order is established, and the row that proved it can be named
  * nothing disambiguates, so both orders fit every row and any parse is a guess
  * different rows disambiguate to DIFFERENT orders, so no single order fits at all
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import Ambiguity, ColumnOrder, ColumnVerdict, DateReading


def infer_order(field: str, readings: Sequence[DateReading]) -> ColumnVerdict:
    """Establish a column's date order, or say why it cannot be established.

    Evidence is only ever an `UNAMBIGUOUS` numeric value -- one whose day field exceeds 12, so it
    could not have been written the other way round. ISO values and spelled-out months are excluded
    even though they parse cleanly, because they are a different format and therefore not evidence
    about the numeric one. `IDENTICAL` values are excluded because they read the same either way.
    """
    counts = dict.fromkeys(Ambiguity, 0)
    for reading in readings:
        counts[reading.ambiguity] += 1

    informative = [reading for reading in readings if reading.is_informative]
    orders = {reading.implied_order for reading in informative}
    orders.discard(None)

    parseable = [
        reading for reading in readings if reading.ambiguity is not Ambiguity.UNPARSEABLE
    ]

    if not parseable:
        order = ColumnOrder.EMPTY
        evidence: list[str] = []
        conflicting: list[ColumnOrder] = []
    elif len(orders) > 1:
        order = ColumnOrder.CONFLICTING
        evidence = [reading.raw for reading in informative]
        conflicting = sorted(orders, key=lambda item: item.value)
    elif len(orders) == 1:
        order = next(iter(orders))
        evidence = [reading.raw for reading in informative]
        conflicting = []
    else:
        order = ColumnOrder.UNKNOWABLE
        evidence = []
        conflicting = []

    return ColumnVerdict(
        field=field,
        order=order,
        total=len(readings),
        informative=len(informative),
        ambiguous=counts[Ambiguity.AMBIGUOUS],
        identical=counts[Ambiguity.IDENTICAL],
        unparseable=counts[Ambiguity.UNPARSEABLE],
        evidence=evidence[:5],
        conflicting_orders=conflicting,
    )


def resolve(readings: Sequence[DateReading], verdict: ColumnVerdict) -> list[tuple[DateReading, object]]:
    """Pair each reading with the date the inferred order gives it, or None.

    Returns None for every reading when the order is not established, rather than falling back to a
    default. A default here is the entire failure being examined: it would produce a full column of
    plausible dates from a column that establishes nothing.
    """
    if not verdict.order.is_established:
        return [(reading, None) for reading in readings]
    return [(reading, reading.under(verdict.order)) for reading in readings]


def rows_at_risk(readings: Sequence[DateReading]) -> list[DateReading]:
    """Readings whose value depends entirely on which order is chosen.

    `IDENTICAL` readings are excluded: both orders give the same date, so no choice affects them.
    """
    return [reading for reading in readings if reading.ambiguity is Ambiguity.AMBIGUOUS]


def worst_divergence(readings: Sequence[DateReading]) -> int | None:
    """The largest gap between the two possible readings in this column.

    Reported because the consequence scales with it. A one-day divergence rarely moves a quarterly
    rollup; the maximum possible here is large enough to move an initiative between fiscal years,
    and a reader deciding whether to care needs the number rather than the count.
    """
    gaps = [
        reading.divergence_days
        for reading in readings
        if reading.ambiguity is Ambiguity.AMBIGUOUS and reading.divergence_days is not None
    ]
    return max(gaps) if gaps else None


def crossing_period_boundary(readings: Sequence[DateReading]) -> list[DateReading]:
    """Ambiguous readings whose two interpretations fall in different calendar quarters.

    The subset where the ambiguity actually changes a reported figure. An ambiguous date that reads
    as 3 April or 4 March lands in Q2 or Q1, so it moves between two leadership reports; one that
    reads as 3 May or 5 March also crosses. Two readings inside one quarter are ambiguous and
    harmless for any quarterly aggregate, which is what most of these reports are.
    """
    def quarter(value) -> tuple[int, int]:
        return (value.year, (value.month - 1) // 3 + 1)

    crossing = []
    for reading in readings:
        if reading.ambiguity is not Ambiguity.AMBIGUOUS:
            continue
        if reading.day_first is None or reading.month_first is None:
            continue
        if quarter(reading.day_first) != quarter(reading.month_first):
            crossing.append(reading)
    return crossing
