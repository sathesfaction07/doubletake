"""Generate a reproducible corpus with known true dates.

The important design choice, as in the sibling projects: the ambiguity is a CONSEQUENCE of how the
values are written, not something planted row by row. Each submission has a true date, and each team
writes it in that team's own format. Whether a column turns out knowable then falls out of which
dates happen to have a day above 12 -- which is exactly the luck that makes this a real problem.

Run: python data/generate.py
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def day_first(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def month_first(value: date) -> str:
    return f"{value.month:02d}/{value.day:02d}/{value.year}"


def iso(value: date) -> str:
    return value.isoformat()


def textual(value: date) -> str:
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{value.day:02d} {months[value.month - 1]} {value.year}"


def build() -> tuple[list[dict], list[dict]]:
    rng = random.Random(20260317)
    rows: list[dict] = []
    truth: list[dict] = []
    counter = [0]

    def emit(team: str, title: str, true_date: date | None, written: str, amount: str) -> None:
        counter[0] += 1
        submission_id = f"S-{counter[0]:04d}"
        rows.append({
            "submission_id": submission_id,
            "team": team,
            "title": title,
            "target_date": written,
            "annual_saving": amount,
        })
        if true_date is not None:
            truth.append({"submission_id": submission_id, "target_date": true_date.isoformat()})

    clean_amounts = ["12000", "8500", "45000", "3200", "150000", "22000", "6400", "98000"]
    # What people actually type into a savings box.
    messy_amounts = [
        "$12,000", "12k", "£1.5m", "8500/yr", "approx 20000", "5000-8000", "TBD",
        "saves about two days a week", "15%", "", "22,500 per year", "3 hours/week",
    ]

    def amount(index: int, messy: bool) -> str:
        pool = messy_amounts if messy else clean_amounts
        return pool[index % len(pool)]

    # ------------------------------------------------------------------ knowable, day-first
    # A team writing DD/MM. Dates spread through the month, so some rows carry a day above 12 and
    # the column establishes its own order.
    base = date(2026, 1, 6)
    for index in range(14):
        true_date = base + timedelta(days=index * 11)
        emit("logistics", f"Reduce pick-path walking distance {index}", true_date,
             day_first(true_date), amount(index, messy=False))

    # ------------------------------------------------------------------ knowable, month-first
    base = date(2026, 2, 3)
    for index in range(14):
        true_date = base + timedelta(days=index * 13)
        emit("finance", f"Automate invoice matching step {index}", true_date,
             month_first(true_date), amount(index, messy=False))

    # ------------------------------------------------------------------ UNKNOWABLE by deadline
    # A team that submits against a monthly reporting deadline, so every target date lands in the
    # first twelve days of a month. Every value is ambiguous and nothing in the column disambiguates
    # -- not because the column is small, but because of WHEN people submit.
    #
    # Day and month must DIFFER, or every value lands in IDENTICAL instead -- both orders would give
    # the same date, and the column would be unknowable but harmless. A first version of this loop set
    # day and month from the same expression, producing 1/1, 2/2, 3/3 ... which demonstrated the wrong
    # thing: unknowable with ZERO rows actually at risk.
    deadline_days = [3, 7, 2, 11, 5, 9, 4, 12, 6, 10, 1, 8, 7, 3, 11, 2]
    for index in range(16):
        month = 1 + (index % 12)
        day = deadline_days[index]
        if day == month:
            day = 1 + (day % 12)
        true_date = date(2026, month, day)
        emit("quality", f"Standardise inspection checklist {index}", true_date,
             day_first(true_date), amount(index, messy=False))

    # ------------------------------------------------------------------ CONFLICTING formats
    # One team where half the submitters write DD/MM and half write MM/DD. Both orders are proven by
    # different rows, so no single order fits and the column must not be parsed as a unit.
    conflict = [date(2026, 3, 25), date(2026, 4, 18), date(2026, 5, 30), date(2026, 6, 14)]
    for index, true_date in enumerate(conflict):
        writer = day_first if index % 2 == 0 else month_first
        emit("engineering", f"Cut build queue wait {index}", true_date,
             writer(true_date), amount(index, messy=False))
    # Plus ambiguous rows that inherit the mess and cannot be resolved either way.
    for index in range(6):
        true_date = date(2026, 7, 1 + index)
        emit("engineering", f"Trim flaky test suite {index}", true_date,
             day_first(true_date), amount(index, messy=False))

    # ------------------------------------------------------------------ messy amounts
    # The date column here is knowable; the amounts are what people type. Separated from the date
    # cases so a reader can see the two failures are independent.
    base = date(2026, 1, 14)
    for index in range(12):
        true_date = base + timedelta(days=index * 17)
        emit("support", f"Shorten escalation handoff {index}", true_date,
             day_first(true_date), amount(index, messy=True))

    # ------------------------------------------------------------------ ISO and textual, mixed in
    # Unambiguous values that do NOT establish the numeric order -- the distinction that an earlier
    # version of this code got wrong. This team's numeric rows stay unknowable despite sitting beside
    # perfectly clear ISO dates.
    for index in range(4):
        true_date = date(2026, 8, 3 + index)
        emit("procurement", f"Consolidate supplier catalogue {index}", true_date,
             iso(true_date), amount(index, messy=False))
    for index in range(4):
        true_date = date(2026, 9, 2 + index)
        emit("procurement", f"Renegotiate freight terms {index}", true_date,
             textual(true_date), amount(index, messy=False))
    for index in range(6):
        true_date = date(2026, 10, 1 + index)
        emit("procurement", f"Batch small purchase orders {index}", true_date,
             day_first(true_date), amount(index, messy=False))

    # ------------------------------------------------------------------ IDENTICAL readings
    # Day equals month, so both orders give the same date. Safe to parse, and carrying no information
    # about the column's order -- which is why they get their own classification.
    for month_day in (4, 5, 6, 7):
        true_date = date(2026, month_day, month_day)
        emit("facilities", f"Reschedule cleaning rota {month_day}", true_date,
             day_first(true_date), amount(month_day, messy=False))

    # ------------------------------------------------------------------ exact duplicates
    # The same initiative submitted twice by the same team, once with a typo'd amount. Which copy
    # survives is a judgement, so the tool reports the pair rather than picking.
    # ONE extra copy. The logistics loop above already emitted this title at index 0, so emitting two
    # here would make three copies and the "submitted twice" description would be wrong.
    duplicate_date = date(2026, 11, 20)
    emit("logistics", "Reduce pick-path walking distance 0", duplicate_date,
         day_first(duplicate_date), "12,000")

    # ------------------------------------------------------------------ unparseable dates
    # The SAFE failure: loud, attributable, impossible to aggregate by accident.
    for bad in ("31/02/2026", "next quarter", "", "2026-13-01"):
        emit("marketing", f"Refresh campaign assets {bad[:6]}", None, bad, "5000")

    rng.shuffle(rows)
    return rows, truth


def main() -> None:
    rows, truth = build()

    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    # One row missing its identity fields, so the loader's problem reporting is exercised by the
    # shipped corpus rather than only by a unit test.
    lines.append(json.dumps({"team": "orphan", "title": "no id"}))
    (HERE / "submissions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (HERE / "truth.jsonl").write_text(
        "\n".join(json.dumps(row) for row in truth) + "\n", encoding="utf-8"
    )

    print(f"{len(rows)} submissions (+1 deliberately missing its id)")
    print(f"{len(truth)} with a known true date")
    teams = sorted({row['team'] for row in rows})
    print(f"{len(teams)} teams: {', '.join(teams)}")


if __name__ == "__main__":
    main()
