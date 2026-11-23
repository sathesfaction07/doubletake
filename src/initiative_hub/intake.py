"""Loading submissions, exact-key dedup, and analysing a dataset column by column.

Dedup here is EXACT-KEY ONLY, and that is a scope decision rather than an oversight. Fuzzy matching
across near-identical titles is already solved in this portfolio: CaseSearch does blocking,
similarity and clustering, and demonstrates that single linkage chains transitively across any
threshold below 1.0. Building a weaker version here would be duplication. What this does is the part
that is genuinely exact -- same team, same folded title -- and the docs point at the tool that does
the rest.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .columns import crossing_period_boundary, infer_order, rows_at_risk, worst_divergence
from .dates import read
from .fields import coverage, kind_counts, read_number
from .models import (
    AggregateCoverage,
    ColumnVerdict,
    DateReading,
    FieldKind,
    FieldVerdict,
    Submission,
)


def _rows(path: Path) -> Iterator[tuple[int, dict]]:
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if text:
                yield number, json.loads(text)


def load_submissions(path: Path) -> tuple[list[Submission], list[str]]:
    """Read submissions, collecting problems rather than raising.

    A row missing its identity fields is unusable and is reported. A row with unreadable *values* is
    perfectly loadable -- classifying those values is the whole job, so a free-text saving figure is
    data rather than an error.
    """
    submissions: list[Submission] = []
    problems: list[str] = []
    for number, row in _rows(path):
        try:
            submissions.append(
                Submission(
                    submission_id=row["submission_id"],
                    team=row["team"],
                    title=row["title"],
                    raw_fields={
                        key: ("" if value is None else str(value))
                        for key, value in row.items()
                        if key not in {"submission_id", "team", "title"}
                    },
                )
            )
        except (KeyError, TypeError) as error:
            problems.append(f"{path.name}:{number}: {error}")
    return submissions, problems


def load_truth(path: Path) -> tuple[dict[str, date], list[str]]:
    """Known true dates per submission id. Synthetic corpus only."""
    truth: dict[str, date] = {}
    problems: list[str] = []
    for number, row in _rows(path):
        try:
            truth[row["submission_id"]] = date.fromisoformat(row["target_date"])
        except (KeyError, ValueError) as error:
            problems.append(f"{path.name}:{number}: {error}")
    return truth, problems


class DuplicateGroup(BaseModel):
    """Submissions sharing an exact identity key."""

    model_config = ConfigDict(frozen=True)

    team: str
    title: str
    submission_ids: list[str]

    @property
    def extras(self) -> int:
        return len(self.submission_ids) - 1


def exact_duplicates(submissions: Sequence[Submission]) -> list[DuplicateGroup]:
    """Groups of submissions with the same team and the same folded title.

    Reported rather than silently collapsed. Which copy survives is a judgement -- the earliest
    submission, the one with the most fields filled in, the one a human prefers -- and making that
    choice inside a validation tool would hide a decision the owner should make.
    """
    grouped: dict[tuple[str, str], list[Submission]] = {}
    for item in submissions:
        grouped.setdefault(item.dedupe_key, []).append(item)
    return [
        DuplicateGroup(
            team=members[0].team,
            title=members[0].title,
            submission_ids=[member.submission_id for member in members],
        )
        for members in grouped.values()
        if len(members) > 1
    ]


class DatasetReport(BaseModel):
    """Everything one dataset establishes about itself."""

    model_config = ConfigDict(frozen=True)

    rows: int = Field(ge=0)
    date_field: str
    amount_field: str
    date_verdict: ColumnVerdict
    amount_coverage: AggregateCoverage
    amount_kinds: dict[FieldKind, int]
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    at_risk: int = Field(ge=0)
    quarter_crossing: int = Field(ge=0)
    """Ambiguous rows whose two readings land in different quarters.

    The subset where the ambiguity changes a reported number. An ambiguous date whose readings sit
    inside one quarter is harmless for a quarterly rollup, which is what these reports mostly are.
    """

    worst_divergence_days: int | None = None
    problems: list[str] = Field(default_factory=list)

    @property
    def duplicate_rows(self) -> int:
        return sum(group.extras for group in self.duplicates)

    @property
    def is_aggregatable(self) -> bool:
        """True when a total over this dataset would mean what it appears to mean.

        Requires the date order to be established AND every amount to have been usable. Anything
        less and a figure computed here needs a caveat travelling with it.
        """
        return self.date_verdict.order.is_established and self.amount_coverage.is_complete


def analyse(
    submissions: Sequence[Submission],
    *,
    date_field: str,
    amount_field: str,
    problems: Sequence[str] = (),
) -> DatasetReport:
    """Read every relevant column and report what the dataset can and cannot support."""
    readings = [
        read(item.raw_fields.get(date_field, ""), row=index)
        for index, item in enumerate(submissions, start=1)
    ]
    verdict = infer_order(date_field, readings)

    amounts = [read_number(amount_field, item.raw_fields.get(amount_field, ""))
               for item in submissions]

    return DatasetReport(
        rows=len(submissions),
        date_field=date_field,
        amount_field=amount_field,
        date_verdict=verdict,
        amount_coverage=coverage(amount_field, amounts),
        amount_kinds=kind_counts(amounts),
        duplicates=exact_duplicates(submissions),
        at_risk=len(rows_at_risk(readings)),
        quarter_crossing=len(crossing_period_boundary(readings)),
        worst_divergence_days=worst_divergence(readings),
        problems=list(problems),
    )


def date_readings(
    submissions: Sequence[Submission], date_field: str
) -> list[DateReading]:
    return [
        read(item.raw_fields.get(date_field, ""), row=index)
        for index, item in enumerate(submissions, start=1)
    ]


def amount_verdicts(
    submissions: Sequence[Submission], amount_field: str
) -> list[FieldVerdict]:
    return [read_number(amount_field, item.raw_fields.get(amount_field, ""))
            for item in submissions]


def slice_by(
    submissions: Sequence[Submission], field: str
) -> dict[str, list[Submission]]:
    """Group submissions by a field, for per-slice knowability.

    `team` and similar live in `raw_fields` or on the model itself, so both are checked. A submission
    missing the field lands under an explicit empty-string key rather than being dropped, because
    dropping rows during a data-quality check is how a data-quality check lies.
    """
    grouped: dict[str, list[Submission]] = {}
    for item in submissions:
        key = getattr(item, field, None) or item.raw_fields.get(field, "")
        grouped.setdefault(str(key), []).append(item)
    return grouped
