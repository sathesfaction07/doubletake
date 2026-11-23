"""Types for intake validation, built around one asymmetry.

A value that FAILS to parse is safe, because it is loud: someone sees the error and fixes the row.
A value that parses TWO WAYS is dangerous, because it is silent: it lands in the database, gets
aggregated, and nothing ever says which reading was taken.

`03/04/2026` is 3 April or 4 March. Nothing in the value says which. `13/04/2026` is unambiguous,
because there is no thirteenth month -- so **ambiguity is a property of the VALUE, not of the
format**, and a parser with a fixed locale is silently wrong on exactly the values where order
matters while being right on the obvious ones.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Ambiguity(StrEnum):
    """What a single date-like string can be read as."""

    UNAMBIGUOUS = "unambiguous"
    """This value cannot be misread.

    Three quite different situations land here: a numeric date whose day field exceeds 12, an ISO
    date, and a date with a spelled-out month.

    They are NOT equally useful. Only the first tells you which order the column's *numeric* values
    use. An ISO date sitting in a column of `03/04/2026` is itself beyond doubt and rescues none of
    its neighbours, because it is a different format rather than evidence about theirs.

    So "unambiguous" and "informative" are separate questions, and `is_informative` answers the
    second one via `implied_order`. An earlier version of this conflated them and counted ISO values
    as evidence, which made columns look knowable when their numeric values were still unresolvable.
    """

    AMBIGUOUS = "ambiguous"
    """Both orders yield real dates, and they are DIFFERENT dates.

    The dangerous case. A fixed-locale parser produces one of them, silently, with no error and no
    warning, and the wrong answer is as plausible as the right one.
    """

    IDENTICAL = "identical"
    """Both orders yield the SAME date, because the day and month fields are equal.

    `05/05/2026` is ambiguous in form and harmless in effect. Worth its own value because it sits on
    both sides of the obvious classification: safe to parse, and yet carrying NO information about
    the column's order. Treating it as unambiguous would let it look like evidence; treating it as
    ambiguous would flag a value that cannot be wrong.
    """

    UNPARSEABLE = "unparseable"
    """Neither order yields a real date.

    The SAFE failure. Loud, attributable to a row, and impossible to aggregate by accident.
    """


class ColumnOrder(StrEnum):
    """What a whole column's date order can be established as."""

    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"

    UNKNOWABLE = "unknowable"
    """No value in the column disambiguates, so both orders fit every row.

    Any parse here is a guess dressed as a reading. This is not a rare edge case: submissions
    clustered on a monthly reporting deadline all fall in days 1-12, which makes EVERY value
    ambiguous and the whole column unknowable by construction.
    """

    CONFLICTING = "conflicting"
    """Different rows disambiguate to DIFFERENT orders, so the column mixes formats.

    Worse than unknowable. Unknowable means one consistent answer exists and we cannot tell which;
    conflicting means no single answer fits, so the column must not be parsed as a unit at all.
    """

    EMPTY = "empty"
    """No parseable values to reason from."""

    @property
    def is_established(self) -> bool:
        return self in {ColumnOrder.DAY_FIRST, ColumnOrder.MONTH_FIRST}


class DateReading(BaseModel):
    """One date-like string, read under both orders."""

    model_config = ConfigDict(frozen=True)

    raw: str
    ambiguity: Ambiguity
    day_first: date | None = None
    month_first: date | None = None
    row: int | None = None

    @property
    def is_informative(self) -> bool:
        """True when this value establishes which order the column's numeric values use.

        Defined as `implied_order is not None` rather than as "unambiguous", because the two are not
        the same. An ISO date and a spelled-out month are both unambiguous and both imply nothing
        about the numeric order beside them. `IDENTICAL` values parse cleanly and also imply nothing.

        Only a numeric value with a field above 12 is evidence, because only that value could not
        have been written the other way round.
        """
        return self.implied_order is not None

    @property
    def implied_order(self) -> ColumnOrder | None:
        """The order this value proves, if it proves one."""
        if self.ambiguity is not Ambiguity.UNAMBIGUOUS:
            return None
        if self.day_first is not None and self.month_first is None:
            return ColumnOrder.DAY_FIRST
        if self.month_first is not None and self.day_first is None:
            return ColumnOrder.MONTH_FIRST
        return None

    @property
    def divergence_days(self) -> int | None:
        """How far apart the two readings are, when both exist and differ.

        Reported because the size of the error matters for what it breaks. Two readings a day apart
        may not change a quarterly rollup; two readings ten months apart move an initiative between
        fiscal years.
        """
        if self.day_first is None or self.month_first is None:
            return None
        return abs((self.day_first - self.month_first).days)

    def under(self, order: ColumnOrder) -> date | None:
        if order is ColumnOrder.DAY_FIRST:
            return self.day_first
        if order is ColumnOrder.MONTH_FIRST:
            return self.month_first
        return None


class ColumnVerdict(BaseModel):
    """What a column of date strings establishes about its own format."""

    model_config = ConfigDict(frozen=True)

    field: str
    order: ColumnOrder
    total: int = Field(ge=0)
    informative: int = Field(ge=0)
    """Values that disambiguate. The count that decides whether the column is knowable at all."""

    ambiguous: int = Field(ge=0)
    identical: int = Field(ge=0)
    unparseable: int = Field(ge=0)
    evidence: list[str] = Field(default_factory=list)
    """The raw values that proved the order, or that conflict. Named so a reader can check."""

    conflicting_orders: list[ColumnOrder] = Field(default_factory=list)

    @property
    def informative_share(self) -> float | None:
        """Fraction of values that carry order information. None when the column is empty.

        Not clamped to look reassuring: 0.0 means nothing in this column can establish its format,
        which is the number worth seeing.
        """
        if self.total == 0:
            return None
        return self.informative / self.total

    @property
    def at_risk(self) -> int:
        """Values whose reading depends entirely on the inferred order.

        `IDENTICAL` values are excluded: they read the same either way, so no inference changes them.
        """
        return self.ambiguous


class FieldKind(StrEnum):
    """What a submitted field turned out to hold."""

    TYPED = "typed"
    """Parsed cleanly into the declared type."""

    COERCED = "coerced"
    """Parsed after normalisation -- stripped currency symbols, unit suffixes, thousands separators.

    Separate from TYPED because a coercion is an interpretation, and the record of what was
    interpreted is the only way anyone can later object to it.
    """

    WRONG_UNIT = "wrong_unit"
    """Parsed to a clean number, in a unit the column cannot sum.

    A `15%` in a savings column is well-formed and is not an amount. This is the case that earns its
    own value rather than folding into COERCED: it parses SUCCESSFULLY and still poisons the
    aggregate, so it is invisible to any check that only looks for parse failures. An earlier version
    coerced it to `15.0`, wrote "this is a percentage" into the note field, and then summed it with
    currency anyway -- turning two $12,000 entries into $24,015 and reporting 100% coverage.

    Detection is NOT general, and the limit is worth stating plainly. Only the percent sign is
    caught, because it is punctuation rather than a word; `3 hours/week` lands in FREE_TEXT instead,
    and a column mixing dollars with euros is not detected at all. One unit error is caught here.
    Unit checking in general is not solved.
    """

    FREE_TEXT = "free_text"
    """Did not parse, and holds prose in a field declared as structured.

    The failure the brief names. It cannot be aggregated -- and an aggregate over the column will
    still compute, over whatever subset happened to parse, reported as though it covered everything.
    """

    EMPTY = "empty"
    """Absent or blank. Distinct from FREE_TEXT: nothing was claimed, so nothing was lost."""


class FieldVerdict(BaseModel):
    """One submitted field."""

    model_config = ConfigDict(frozen=True)

    field: str
    raw: str
    kind: FieldKind
    value: float | None = None
    note: str | None = None

    @property
    def aggregatable(self) -> bool:
        return self.kind in {FieldKind.TYPED, FieldKind.COERCED}


class Submission(BaseModel):
    """One initiative submission, as received."""

    model_config = ConfigDict(frozen=True)

    submission_id: str
    team: str
    title: str
    raw_fields: dict[str, str] = Field(default_factory=dict)

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """Identity for exact-key dedup: team plus a folded title.

        Exact-key only, on purpose. Fuzzy matching across near-identical titles is a solved problem
        in this portfolio -- Kinship does blocking, similarity and clustering, and demonstrates
        that single linkage chains transitively across a threshold. Rebuilding a worse version of it
        here would be duplication, so this handles the case it can handle honestly and the docs point
        at the tool that handles the rest.
        """
        return (self.team.strip().casefold(), " ".join(self.title.split()).casefold())


class AggregateCoverage(BaseModel):
    """What fraction of a column an aggregate actually covered.

    Reported beside every total, because a sum over a column silently skips what it could not parse
    and then reads as a sum over the column.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    rows: int = Field(ge=0)
    included: int = Field(ge=0)
    total: float | None = None

    @property
    def coverage(self) -> float | None:
        if self.rows == 0:
            return None
        return self.included / self.rows

    @property
    def excluded(self) -> int:
        return self.rows - self.included

    @property
    def is_complete(self) -> bool:
        return self.rows > 0 and self.included == self.rows
