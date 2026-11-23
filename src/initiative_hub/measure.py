"""Scoring a fixed-locale parser against inference, on data whose true dates are known.

The comparison is not about accuracy in the ordinary sense. Both approaches read the unambiguous
values correctly and neither can conjure information out of an unknowable column. The difference is
in **what they do when they cannot tell**, and the two behaviours have opposite failure signatures:

  * a fixed-locale parser always answers. It logs nothing, raises nothing, and is wrong on some
    fraction of rows with no trace anywhere.
  * inference refuses to answer when the column establishes no order. That is visible, countable,
    and someone can act on it.

So the headline number is not "how many were right" but **how many were wrong with no warning** --
and the fixed parser's parse-failure count sitting at zero while its silent error rate does not is
the whole finding.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from .columns import infer_order
from .models import Ambiguity, ColumnOrder, ColumnVerdict, DateReading


class FixedLocaleScore(BaseModel):
    """How a parser with one hard-coded date order performed."""

    model_config = ConfigDict(frozen=True)

    order: ColumnOrder
    rows: int = Field(ge=0)
    parse_failures: int = Field(ge=0)
    """Rows it could not read at all. The LOUD failures -- the only ones anyone would notice."""

    correct: int = Field(ge=0)
    silently_wrong: int = Field(ge=0)
    """Rows it read without complaint, to the wrong date.

    The number that matters. A dataset with zero parse failures and a nonzero count here looks
    perfectly healthy from every log, dashboard and error report.
    """

    @property
    def answered(self) -> int:
        return self.correct + self.silently_wrong

    @property
    def silent_error_rate(self) -> float | None:
        """Wrong answers as a fraction of answers GIVEN, not of rows.

        Denominated on answers because that is the population a consumer sees. None when it answered
        nothing, since a parser that produced no values has no error rate to report.
        """
        if self.answered == 0:
            return None
        return self.silently_wrong / self.answered

    @property
    def looks_healthy(self) -> bool:
        """True when nothing in the failure signature would prompt anyone to look.

        The condition under which this tool earns its existence: no parse failures, and wrong data.
        """
        return self.parse_failures == 0 and self.silently_wrong > 0


class InferenceScore(BaseModel):
    """How order inference performed on the same rows."""

    model_config = ConfigDict(frozen=True)

    rows: int = Field(ge=0)
    resolved: int = Field(ge=0)
    refused: int = Field(ge=0)
    """Rows left unresolved because the column established no order. VISIBLE by construction."""

    correct: int = Field(ge=0)
    wrong: int = Field(ge=0)

    @property
    def accuracy_on_resolved(self) -> float | None:
        """Correctness among rows it was willing to answer. None when it answered none."""
        if self.resolved == 0:
            return None
        return self.correct / self.resolved

    @property
    def refusal_rate(self) -> float | None:
        if self.rows == 0:
            return None
        return self.refused / self.rows

    @property
    def silently_wrong(self) -> int:
        """Wrong answers given without a warning.

        Inference can still be wrong: it infers an order from evidence, and a column whose
        disambiguating rows were themselves typed in the minority format will point the wrong way.
        The claim here is narrower than "always right" -- it is that where the evidence is absent, no
        answer is produced at all.
        """
        return self.wrong


class ColumnComparison(BaseModel):
    """Both approaches on one column, side by side."""

    model_config = ConfigDict(frozen=True)

    field: str
    verdict: ColumnVerdict
    day_first: FixedLocaleScore
    month_first: FixedLocaleScore
    inference: InferenceScore

    @property
    def worse_fixed_choice(self) -> FixedLocaleScore:
        """Whichever hard-coded order fared worse.

        Reported because choosing a locale is a coin flip when the column is unknowable, and a reader
        deciding whether to risk it should see the losing side rather than the average.

        Ranked on silent errors first, then on parse failures. The second key matters more than it
        looks: when both orders produce zero silent errors they differ only in loudness, and an
        earlier version keyed on silent errors alone -- so `max` returned whichever came first and
        the locale mismatch, visible as a parse failure under the other order, vanished from the
        report. Breaking the tie toward MORE failures surfaces the mismatch instead of hiding it.
        """
        return max(
            (self.day_first, self.month_first),
            key=lambda item: (item.silently_wrong, item.parse_failures),
        )


def score_fixed(
    readings: Sequence[DateReading], truth: Sequence[date | None], order: ColumnOrder
) -> FixedLocaleScore:
    """Score a parser that always reads in `order`, never asking whether the column agrees."""
    failures = correct = wrong = 0
    for reading, actual in zip(readings, truth, strict=True):
        produced = reading.under(order)
        if produced is None:
            failures += 1
            continue
        if actual is None:
            # No known truth for this row, so it cannot be scored either way. Counted nowhere rather
            # than assumed correct, which would flatter whichever parser answered more often.
            continue
        if produced == actual:
            correct += 1
        else:
            wrong += 1
    return FixedLocaleScore(
        order=order, rows=len(readings), parse_failures=failures,
        correct=correct, silently_wrong=wrong,
    )


def score_inference(
    readings: Sequence[DateReading], truth: Sequence[date | None], verdict: ColumnVerdict
) -> InferenceScore:
    """Score inference, which declines to answer when the column establishes no order."""
    if not verdict.order.is_established:
        return InferenceScore(
            rows=len(readings), resolved=0, refused=len(readings), correct=0, wrong=0
        )

    resolved = refused = correct = wrong = 0
    for reading, actual in zip(readings, truth, strict=True):
        produced = reading.under(verdict.order)
        if produced is None:
            refused += 1
            continue
        resolved += 1
        if actual is None:
            continue
        if produced == actual:
            correct += 1
        else:
            wrong += 1
    return InferenceScore(
        rows=len(readings), resolved=resolved, refused=refused, correct=correct, wrong=wrong
    )


def compare_column(
    field: str, readings: Sequence[DateReading], truth: Sequence[date | None]
) -> ColumnComparison:
    verdict = infer_order(field, readings)
    return ColumnComparison(
        field=field,
        verdict=verdict,
        day_first=score_fixed(readings, truth, ColumnOrder.DAY_FIRST),
        month_first=score_fixed(readings, truth, ColumnOrder.MONTH_FIRST),
        inference=score_inference(readings, truth, verdict),
    )


class Rollup(BaseModel):
    """Totals across every column examined."""

    model_config = ConfigDict(frozen=True)

    columns: int = Field(ge=0)
    rows: int = Field(ge=0)
    established: int = Field(ge=0)
    unknowable: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    fixed_silently_wrong: int = Field(ge=0)
    fixed_parse_failures: int = Field(ge=0)
    inference_refused: int = Field(ge=0)
    inference_wrong: int = Field(ge=0)

    columns_looking_healthy: int = Field(default=0, ge=0)
    """Columns that INDIVIDUALLY show zero parse failures and nonzero wrong dates.

    Counted per column rather than derived from the totals, because the totals destroy the property.
    A fleet-wide view sums parse failures across every team, so one team's LOUD failures mask another
    team's SILENT ones -- and the team owning the silently wrong column is looking at its own slice,
    where the failure count really is zero.

    An earlier version computed this from the rollup sums and reported False for a corpus containing a
    column that plainly qualifies.
    """

    @property
    def fixed_looks_healthy(self) -> bool:
        """Whether ANY column would pass unnoticed. Not the same as the aggregate looking clean."""
        return self.columns_looking_healthy > 0

    @property
    def established_share(self) -> float | None:
        if self.columns == 0:
            return None
        return self.established / self.columns


def rollup(comparisons: Sequence[ColumnComparison]) -> Rollup:
    """Aggregate, using the WORSE fixed choice per column.

    Averaging the two locales would report the performance of a parser nobody runs. In practice a
    team picks one order and lives with it, so the honest summary is what happens when the pick is
    wrong -- which on an unknowable column is half the time.
    """
    worse = [item.worse_fixed_choice for item in comparisons]
    return Rollup(
        columns=len(comparisons),
        rows=sum(item.verdict.total for item in comparisons),
        established=sum(1 for item in comparisons if item.verdict.order.is_established),
        unknowable=sum(
            1 for item in comparisons if item.verdict.order is ColumnOrder.UNKNOWABLE
        ),
        conflicting=sum(
            1 for item in comparisons if item.verdict.order is ColumnOrder.CONFLICTING
        ),
        fixed_silently_wrong=sum(item.silently_wrong for item in worse),
        fixed_parse_failures=sum(item.parse_failures for item in worse),
        inference_refused=sum(item.inference.refused for item in comparisons),
        inference_wrong=sum(item.inference.wrong for item in comparisons),
        columns_looking_healthy=sum(1 for item in worse if item.looks_healthy),
    )


def ambiguous_count(readings: Sequence[DateReading]) -> int:
    return sum(1 for item in readings if item.ambiguity is Ambiguity.AMBIGUOUS)
