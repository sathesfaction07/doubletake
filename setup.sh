#!/usr/bin/env bash
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${BLUE}==>${NC} $1"; }
ok()   { echo -e "${GREEN} ok ${NC} $1"; }
fail() { echo -e "${RED}fail${NC} $1"; exit 1; }

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            cat <<'USAGE'
Usage: bash setup.sh

No options, no credentials, no network, no model. The accuracy problem here is arithmetic on
calendars, plus a decision about what to do when a value cannot be resolved.
USAGE
            exit 0 ;;
        *) fail "unknown option: $arg (try --help)" ;;
    esac
done

info "Checking Python"
command -v python3 >/dev/null 2>&1 || fail "python3 not found (macOS: brew install python@3.12)"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
[ "$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')" = "1" ] \
    || fail "Python 3.11+ required (StrEnum). Found $PY_VER."
ok "Python $PY_VER"

info "Creating virtual environment"
[ -d .venv ] || python3 -m venv .venv || fail "could not create .venv"
ok ".venv ready"

info "Installing dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]" || fail "pip install failed"
ok "dependencies installed"

info "Verifying imports"
.venv/bin/python - <<'PYCHECK' || fail "core imports failed"
import click                                        # noqa: F401
from pydantic_settings import BaseSettings          # noqa: F401
from initiative_hub.columns import infer_order      # noqa: F401
from initiative_hub.dates import read               # noqa: F401
from initiative_hub.measure import compare_column   # noqa: F401
PYCHECK
ok "imports resolve"

info "Confirming dateutil is NOT a dependency"
.venv/bin/python - <<'PYCHECK' || fail "dateutil is installed, which defeats the point"
import importlib.util

# `dateutil.parser` has a `dayfirst` flag that takes the exact decision this project exists to
# expose: set it once, globally, and every ambiguous value is silently resolved in the direction you
# guessed. The behaviour under examination cannot also be the implementation.
found = importlib.util.find_spec("dateutil")
assert found is None, "dateutil is installed; the point of this project is not to use its dayfirst"
print("     dateutil absent. Its `dayfirst` flag IS the failure mode being measured.")
PYCHECK
ok "no dateutil"

info "Generating the corpus"
[ -f data/submissions.jsonl ] || .venv/bin/python data/generate.py || fail "could not generate data"
ok "corpus present"

info "Deriving the ambiguity rate, and correcting the usual shortcut"
.venv/bin/python - <<'PYCHECK' || fail "the ambiguity arithmetic does not hold"
import random
from datetime import date, timedelta

from initiative_hub.dates import ambiguity_probability

exact = ambiguity_probability(year=2026)
print(f"     A date reads two ways when its DAY field is 12 or less -- the MONTH field always is.")
print(f"     So the count is 12 months x 12 days = 144, over the days in the year.")
print()
print(f"     exact, 2026 (365 days)   144/365 = {exact:.4f}")
print(f"     exact, 2024 (366 days)   144/366 = {ambiguity_probability(year=2024):.4f}")
print(f"     the usual shortcut       12/30.4 = {12 / 30.4:.4f}   <- right number, wrong reason")
print()
print("     12/30.4 treats it as a per-month day fraction. It lands within 0.0002 of the truth")
print("     for a whole year and is wrong for any other span, so the exact form is what ships.")

assert abs(exact - 144 / 365) < 1e-9

# Cross-checked against brute force and simulation, so the closed form is not taken on faith.
start = date(2026, 1, 1)
brute = sum(1 for offset in range(365) if (start + timedelta(days=offset)).day <= 12) / 365
assert abs(brute - exact) < 1e-9, (brute, exact)

rng = random.Random(11)
simulated = sum(
    1 for _ in range(200_000) if (start + timedelta(days=rng.randrange(365))).day <= 12
) / 200_000
assert abs(simulated - exact) < 0.005, (simulated, exact)
print(f"     brute force over all 365 days: {brute:.4f}   simulated over 200k draws: {simulated:.4f}")
PYCHECK
ok "the ambiguity rate is derived three ways and they agree"

info "Deriving how many rows a column needs before its order is knowable"
.venv/bin/python - <<'PYCHECK' || fail "the unknowability arithmetic does not hold"
import random
from datetime import date, timedelta

from initiative_hub.dates import informative_rows_needed, unknowable_probability

rng = random.Random(5)
start = date(2026, 1, 1)

def ambiguous_row() -> bool:
    return (start + timedelta(days=rng.randrange(365))).day <= 12

print(f"     {'rows':>6}{'closed form':>14}{'simulated':>12}{'knowable':>12}")
for rows in (1, 2, 3, 5, 8, 12):
    closed = unknowable_probability(rows)
    trials = 20_000
    hits = sum(1 for _ in range(trials) if all(ambiguous_row() for _ in range(rows)))
    simulated = hits / trials
    assert abs(simulated - closed) < 0.02, (rows, simulated, closed)
    print(f"     {rows:>6}{closed:>13.4%}{simulated:>12.4%}{1 - closed:>12.4%}")

assert informative_rows_needed(0.99) == 5
assert informative_rows_needed(0.999) == 8
print()
print(f"     rows needed for  99% confidence: {informative_rows_needed(0.99)}")
print(f"     rows needed for 99.9% confidence: {informative_rows_needed(0.999)}")
print()
print("     So a five-row slice is usually fine and a three-row slice is unknowable 6% of the")
print("     time. Slicing a report by team or by quarter is exactly how small slices get made.")
PYCHECK
ok "the row-count arithmetic matches simulation"

info "Deriving the case that defeats all of the above"
.venv/bin/python - <<'PYCHECK' || fail "the deadline case does not behave as documented"
import random
from datetime import date

from initiative_hub.columns import infer_order
from initiative_hub.dates import read_all

rng = random.Random(3)

def column(day_range, rows=12):
    values = []
    for _ in range(rows):
        month = rng.randrange(1, 13)
        day = rng.choice(list(day_range))
        values.append(f"{day:02d}/{month:02d}/{date(2026, month, day).year}")
    return values

print(f"     {'submission pattern':<30}{'12-row columns unknowable':>28}")
for label, day_range in (
    ("days 1-12  (monthly deadline)", range(1, 13)),
    ("days 1-5   (tight deadline)", range(1, 6)),
    ("days 13-28 (mid-month)", range(13, 29)),
    ("days 1-28  (no pattern)", range(1, 29)),
):
    unknowable = sum(
        1 for _ in range(400)
        if not infer_order("x", read_all(column(day_range))).order.is_established
    )
    print(f"     {label:<30}{unknowable / 400:>27.1%}")

# The deadline case is total, not marginal.
deadline = [f"{day:02d}/{month:02d}/2026"
            for month, day in zip(range(1, 13), [3, 7, 2, 11, 4, 9, 1, 12, 6, 8, 5, 10])]
verdict = infer_order("deadline", read_all(deadline))
assert not verdict.order.is_established
assert verdict.informative == 0
assert verdict.ambiguous == 12
# And it is not a small-column effect: more rows do not help.
assert not infer_order("x", read_all(deadline * 4)).order.is_established

print()
print("     A monthly reporting deadline puts every submission in days 1-12, which makes every")
print("     value ambiguous and EVERY column unknowable -- 100%, regardless of row count. Adding")
print("     four times the rows changes nothing.")
print()
print("     So the risk is set by WHEN people submit, not by how much data you have. That is the")
print("     assumption doing all the work in the table above, and it fails in the direction that")
print("     hurts.")
PYCHECK
ok "the deadline case derived, and it is total rather than marginal"

info "Deriving that unambiguous is not the same as informative"
.venv/bin/python - <<'PYCHECK' || fail "the informative distinction does not hold"
from initiative_hub.columns import infer_order
from initiative_hub.dates import read, read_all

print(f"     {'value':<16}{'ambiguity':<14}{'informative':<13}implies order")
for raw in ("13/04/2026", "04/13/2026", "03/04/2026", "05/05/2026", "2026-04-03", "04 Mar 2026"):
    item = read(raw)
    implied = item.implied_order.value if item.implied_order else "-"
    print(f"     {raw:<16}{item.ambiguity.value:<14}{str(item.is_informative):<13}{implied}")

# ISO and textual values are beyond doubt in themselves and are NOT evidence about the numeric
# values beside them, because they are a different format.
assert read("2026-04-03").ambiguity.value == "unambiguous"
assert not read("2026-04-03").is_informative
assert not read("04 Mar 2026").is_informative

verdict = infer_order("mixed", read_all(["2026-04-03", "2026-05-09", "03/04/2026", "05/06/2026"]))
assert verdict.order.value == "unknowable", verdict.order
assert verdict.informative == 0
print()
print("     A column of two clean ISO dates and two ambiguous numeric ones is still UNKNOWABLE.")
print("     The ISO values rescue none of their neighbours. An earlier version of this code")
print("     conflated the two questions and counted them as evidence, which made columns look")
print("     knowable while their numeric values stayed unresolvable.")
PYCHECK
ok "unambiguous and informative are kept apart"

info "Measuring both approaches against the shipped corpus"
.venv/bin/python - <<'PYCHECK' || fail "the corpus did not behave as documented"
from pathlib import Path

from initiative_hub.columns import infer_order
from initiative_hub.intake import analyse, date_readings, load_submissions, load_truth, slice_by
from initiative_hub.measure import compare_column, rollup
from initiative_hub.models import Ambiguity, ColumnOrder, FieldKind

submissions, problems = load_submissions(Path("data/submissions.jsonl"))
truth, truth_problems = load_truth(Path("data/truth.jsonl"))
assert not truth_problems
assert len(problems) == 1, problems
print(f"     {len(submissions)} submissions, {len(truth)} with a known true date")
print(f"     the one deliberately broken row is reported: {problems[0]}")

readings = date_readings(submissions, "target_date")
assert {item.ambiguity for item in readings} == set(Ambiguity)
print("     every ambiguity value occurs, so no assertion below is vacuous")

report = analyse(submissions, date_field="target_date", amount_field="annual_saving")
whole = report.date_verdict
assert whole.order is ColumnOrder.CONFLICTING
print()
print(f"     WHOLE DATASET order: {whole.order.value}")
print(f"       informative {whole.informative}/{whole.total}  ambiguous {whole.ambiguous}  "
      f"identical {whole.identical}  unparseable {whole.unparseable}")
print(f"       rows at risk {report.at_risk}, of which {report.quarter_crossing} cross a quarter")
print(f"       worst divergence between the two readings of one value: "
      f"{report.worst_divergence_days} days")
assert report.worst_divergence_days > 200
print("       Two readings that far apart move an initiative between fiscal years.")

comparisons = []
verdicts = []
for name, group in sorted(slice_by(submissions, "team").items()):
    group_readings = date_readings(group, "target_date")
    verdicts.append(infer_order(name, group_readings))
    comparisons.append(
        compare_column(name, group_readings, [truth.get(item.submission_id) for item in group])
    )

assert {item.order for item in verdicts} == set(ColumnOrder)
print()
print(f"     {'team':<14}{'rows':>5}{'order':>13}{'inform':>8}{'ambig':>7}"
      f"{'fixWrong':>10}{'fixFail':>9}{'looksOK':>9}{'refused':>9}")
for verdict, comparison in zip(verdicts, comparisons):
    worse = comparison.worse_fixed_choice
    print(f"     {verdict.field:<14}{verdict.total:>5}{verdict.order.value:>13}"
          f"{verdict.informative:>8}{verdict.ambiguous:>7}{worse.silently_wrong:>10}"
          f"{worse.parse_failures:>9}{str(worse.looks_healthy):>9}"
          f"{comparison.inference.refused:>9}")
print("     every column outcome occurs across these slices, including EMPTY and CONFLICTING")

totals = rollup(comparisons)
assert totals.inference_wrong == 0
assert totals.fixed_silently_wrong >= 39
assert totals.columns_looking_healthy == 2
print()
print(f"     fixed-locale parser:  {totals.fixed_silently_wrong} silently wrong, "
      f"{totals.fixed_parse_failures} parse failures")
print(f"     inference:            {totals.inference_wrong} wrong, "
      f"{totals.inference_refused} refused")
print()
print(f"     {totals.columns_looking_healthy} team(s) show ZERO parse failures AND wrong dates.")
print("     The fleet-wide view has loud failures, so someone would look -- but the team owning a")
print("     silent slice sees only their own numbers, where the failure count really is zero.")
print("     That is why this is counted per column rather than from the totals.")

coverage = report.amount_coverage
assert not coverage.is_complete
assert report.amount_kinds[FieldKind.WRONG_UNIT] >= 1
print()
print(f"     amount column: total {coverage.total:,.0f} at {coverage.coverage:.1%} coverage, "
      f"{coverage.excluded} row(s) excluded")
print(f"       kinds: " + "  ".join(
    f"{kind.value}={count}" for kind, count in report.amount_kinds.items() if count
))
print("       WRONG_UNIT is the one a parse-failure check cannot see: a `15%` in a savings column")
print("       parses perfectly and cannot be summed with a currency amount. An earlier version")
print("       coerced it and summed it anyway, turning two $12,000 rows into $24,015 at 100%")
print("       'complete' coverage.")

assert len(report.duplicates) == 1
print()
print(f"     {len(report.duplicates)} exact duplicate group, {report.duplicate_rows} extra row.")
print("     Exact keys only -- fuzzy matching over near-identical titles is CaseSearch's job and")
print("     rebuilding a weaker version here would be duplication.")
PYCHECK
ok "the corpus behaves as documented"

info "Running the test suite"
.venv/bin/python -m pytest -q || fail "tests failed"
ok "tests pass"

cat <<'DONE'

Setup complete.

  source .venv/bin/activate

  initiative-hub doctor
  initiative-hub check                    # exits 1: the column conflicts, and a row is unreadable
  initiative-hub report --sliced          # per-team knowability
  initiative-hub audit                    # fixed-locale parser vs inference
  initiative-hub rows-needed              # the arithmetic, and what breaks it
  initiative-hub fields                   # how each messy amount was interpreted
  initiative-hub explain 03/04/2026       # one value, both readings
  initiative-hub explain 2026-04-03       # unambiguous, and not evidence

`check` fails on a CONFLICTING column, because no single order fits it. It does NOT fail on an
unknowable one: that is a property of what submitters typed, and a gate that fires whenever a team
submits on the first of the month gets switched off.

`audit` fails only if INFERENCE gets a resolved row wrong. The fixed-locale baseline is wrong on
many rows by design -- that is the finding, not a regression.
DONE
