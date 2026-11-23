"""Shared fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from initiative_hub.intake import load_submissions, load_truth
from initiative_hub.models import Submission

DATA = Path(__file__).resolve().parents[1] / "data"


def submission(
    *,
    submission_id: str = "S-1",
    team: str = "alpha",
    title: str = "An initiative",
    target_date: str = "01/02/2026",
    annual_saving: str = "1000",
) -> Submission:
    return Submission(
        submission_id=submission_id,
        team=team,
        title=title,
        raw_fields={"target_date": target_date, "annual_saving": annual_saving},
    )


def dated(values: list[str], team: str = "alpha") -> list[Submission]:
    """Submissions differing only in their date string."""
    return [
        submission(submission_id=f"S-{index}", team=team, title=f"Item {index}", target_date=value)
        for index, value in enumerate(values, start=1)
    ]


@pytest.fixture(scope="session")
def corpus() -> list[Submission]:
    submissions, problems = load_submissions(DATA / "submissions.jsonl")
    # Exactly one row is deliberately missing its id, so this asserts the count.
    assert len(problems) == 1, problems
    return submissions


@pytest.fixture(scope="session")
def truth() -> dict[str, date]:
    known, problems = load_truth(DATA / "truth.jsonl")
    assert not problems, problems
    return known
