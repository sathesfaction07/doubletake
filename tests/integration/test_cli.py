"""CLI behaviour and exit codes. Nothing is mocked, because nothing talks to a network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from initiative_hub.cli import cli

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def env() -> dict[str, str]:
    return {
        "IH_SUBMISSIONS": str(DATA / "submissions.jsonl"),
        "IH_TRUTH": str(DATA / "truth.jsonl"),
    }


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestDoctor:
    def test_teaches_the_vocabulary_and_the_asymmetry(self, runner, env):
        result = runner.invoke(cli, ["doctor"], env=env)
        assert result.exit_code == 0
        assert "no credentials, no network, no model" in result.output
        for word in ("unambiguous", "ambiguous", "identical", "unparseable",
                    "unknowable", "conflicting", "empty"):
            assert word in result.output
        assert "FAILS to parse is safe" in result.output
        assert "parses TWO WAYS is dangerous" in result.output

    def test_names_what_is_deliberately_not_built(self, runner, env):
        result = runner.invoke(cli, ["doctor"], env=env)
        assert "CaseSearch" in result.output
        assert "exact-key dedup" in result.output

    def test_fails_on_missing_inputs(self, runner, tmp_path):
        result = runner.invoke(cli, ["doctor"], env={"IH_SUBMISSIONS": str(tmp_path / "no.jsonl")})
        assert result.exit_code == 1
        assert "FAIL: missing input(s)" in result.output


class TestCheck:
    def test_fails_on_a_conflicting_column(self, runner, env):
        result = runner.invoke(cli, ["check"], env=env)
        assert result.exit_code == 1
        assert "conflicting" in result.output
        assert "guaranteed wrong for some rows" in result.output

    def test_reports_the_unreadable_row(self, runner, env):
        result = runner.invoke(cli, ["check"], env=env)
        assert "unreadable row(s)" in result.output
        assert "submission_id" in result.output

    def test_explains_the_four_value_level_outcomes(self, runner, env):
        result = runner.invoke(cli, ["check"], env=env)
        assert "read two ways, and the two differ" in result.output
        assert "read two ways to the SAME date, so harmless" in result.output
        assert "the SAFE failure: loud and attributable" in result.output
        assert "could only have been written one way" in result.output

    def test_a_total_never_appears_without_its_coverage(self, runner, env):
        result = runner.invoke(cli, ["check"], env=env)
        assert "coverage" in result.output
        assert "never shown without its coverage" in result.output

    def test_reports_the_exact_duplicate_and_points_at_case_search(self, runner, env):
        result = runner.invoke(cli, ["check"], env=env)
        assert "exact duplicate group(s)" in result.output
        assert "owner's judgement" in result.output
        assert "CaseSearch" in result.output

    def test_an_unknowable_column_does_not_fail_the_check(self, runner, tmp_path):
        """A gate that fires whenever a team submits on the first of the month gets switched off."""
        rows = [
            {"submission_id": f"S-{i}", "team": "alpha", "title": f"Item {i}",
             "target_date": f"0{i}/0{i + 1}/2026", "annual_saving": "1000"}
            for i in range(1, 5)
        ]
        path = tmp_path / "s.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        result = runner.invoke(cli, ["check"], env={"IH_SUBMISSIONS": str(path)})
        assert result.exit_code == 0
        assert "UNKNOWABLE" in result.output
        assert "does NOT fail this check" in result.output


class TestReport:
    def test_fails_when_the_dataset_is_not_aggregatable(self, runner, env):
        result = runner.invoke(cli, ["report"], env=env)
        assert result.exit_code == 1
        assert "not aggregatable" in result.output
        assert "caveat travelling with it" in result.output

    def test_sliced_shows_per_team_knowability(self, runner, env):
        result = runner.invoke(cli, ["report", "--sliced"], env=env)
        assert "sliced by team" in result.output
        for team in ("quality", "procurement", "facilities", "logistics"):
            assert team in result.output

    def test_sliced_explains_that_the_whole_does_not_transfer_to_the_parts(self, runner, env):
        result = runner.invoke(cli, ["report", "--sliced"], env=env)
        assert "does not transfer to the parts" in result.output
        assert "rows at risk AND no way to resolve them" in result.output

    def test_every_order_outcome_is_listed_even_at_zero(self, runner, env):
        result = runner.invoke(cli, ["report", "--sliced"], env=env)
        for value in ("day_first", "month_first", "unknowable", "conflicting", "empty"):
            assert value in result.output

    def test_writes_json_and_csv(self, runner, env, tmp_path):
        out = tmp_path / "out"
        runner.invoke(cli, ["report", "--sliced", "-o", str(out)], env=env)
        payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert payload["date_column"]["order"] == "conflicting"
        assert payload["aggregatable"] is False
        assert payload["amount_column"]["complete"] is False
        assert payload["amount_column"]["kinds"]["wrong_unit"] >= 1

        rows = (out / "readings.csv").read_text(encoding="utf-8").strip().split("\n")
        assert rows[0].startswith("row,raw,ambiguity,day_first,month_first")
        # An absent second reading is an EMPTY cell, never a date and never a zero.
        assert any(row.split(",")[4] == "" for row in rows[1:])

        duplicates = (out / "duplicates.csv").read_text(encoding="utf-8").strip().split("\n")
        assert len(duplicates) == 2  # header plus the one planted group


class TestAudit:
    def test_scores_both_approaches_and_passes(self, runner, env):
        """Exit 0 even though the fixed baseline is badly wrong -- that is the finding."""
        result = runner.invoke(cli, ["audit"], env=env)
        assert result.exit_code == 0
        assert "silently wrong on" in result.output
        assert "it IS the finding" in result.output

    def test_reports_the_slices_that_would_pass_unnoticed(self, runner, env):
        result = runner.invoke(cli, ["audit"], env=env)
        assert "slices looking healthy    2" in result.output
        assert "ZERO parse failures and wrong dates" in result.output

    def test_explains_that_refusals_are_not_a_cost(self, runner, env):
        result = runner.invoke(cli, ["audit"], env=env)
        assert "Refusals are not a cost" in result.output
        assert "the same uncertainty, made visible" in result.output

    def test_json_carries_nulls_not_zeros(self, runner, env, tmp_path):
        out = tmp_path / "out"
        runner.invoke(cli, ["audit", "-o", str(out)], env=env)
        payload = json.loads((out / "audit.json").read_text(encoding="utf-8"))
        totals = payload["totals"]
        assert totals["inference_wrong"] == 0
        assert totals["fixed_silently_wrong"] >= 39
        assert totals["columns_looking_healthy"] == 2

        slices = {item["slice"]: item for item in payload["slices"]}
        # A slice that answered nothing has no error rate to report.
        assert slices["marketing"]["fixed_silent_error_rate"] is None
        assert slices["marketing"]["inference_accuracy_on_resolved"] is None
        # And the deadline team is silently wrong on every row it answered.
        assert slices["quality"]["fixed_silent_error_rate"] == 1.0
        assert slices["quality"]["fixed_looks_healthy"] is True

    def test_fails_when_the_truth_file_is_unreadable(self, runner, env, tmp_path):
        bad = tmp_path / "truth.jsonl"
        bad.write_text('{"submission_id": "S-1"}\n', encoding="utf-8")
        result = runner.invoke(cli, ["audit"], env={**env, "IH_TRUTH": str(bad)})
        assert result.exit_code == 1
        assert "unreadable row(s) in the known-dates file" in result.output
        # And it must not blame inference for a broken fixture.
        assert "FAIL: inference produced" not in result.output


class TestExplain:
    def test_an_ambiguous_value_is_named_as_dangerous(self, runner):
        result = runner.invoke(cli, ["explain", "03/04/2026"])
        assert result.exit_code == 0
        assert "ambiguity      ambiguous" in result.output
        assert "no error and no warning" in result.output

    def test_an_informative_value_names_the_order_it_proves(self, runner):
        result = runner.invoke(cli, ["explain", "13/04/2026"])
        assert "informative    True" in result.output
        assert "implies order  day_first" in result.output

    def test_an_identical_value_is_harmless_and_uninformative(self, runner):
        result = runner.invoke(cli, ["explain", "05/05/2026"])
        assert "ambiguity      identical" in result.output
        assert "cannot be read wrongly" in result.output
        assert "tells you nothing about the order" in result.output

    def test_an_iso_value_is_unambiguous_and_not_evidence(self, runner):
        result = runner.invoke(cli, ["explain", "2026-04-03"])
        assert "informative    False" in result.output
        assert "rescues none of its neighbours" in result.output

    def test_an_unparseable_value_is_the_safe_failure(self, runner):
        result = runner.invoke(cli, ["explain", "next quarter"])
        assert "unparseable" in result.output
        assert "SAFE failure" in result.output

    def test_divergence_renders_as_n_a_when_there_is_one_reading(self, runner):
        result = runner.invoke(cli, ["explain", "13/04/2026"])
        assert "as month-first n/a" in result.output


class TestRowsNeeded:
    def test_publishes_the_arithmetic(self, runner, env):
        result = runner.invoke(cli, ["rows-needed"], env=env)
        assert result.exit_code == 0
        assert "144/365" in result.output
        assert "0.3945" in result.output
        assert "rows needed for 99% confidence" in result.output
        assert "5" in result.output

    def test_names_the_assumption_that_breaks_it(self, runner, env):
        result = runner.invoke(cli, ["rows-needed"], env=env)
        assert "UNIFORMITY" in result.output
        assert "monthly reporting deadline" in result.output
        assert "regardless of how many rows" in result.output
        assert "when people submit, not by how much data" in result.output


class TestFieldsAndDates:
    def test_fields_shows_how_each_value_was_interpreted(self, runner, env):
        result = runner.invoke(cli, ["fields"], env=env)
        assert result.exit_code == 0
        assert "wrong_unit" in result.output
        assert "free_text" in result.output
        assert "coerced" in result.output
        assert "a check that only looks for parse failures never sees it" in result.output

    def test_dates_lists_both_readings_and_the_counts(self, runner, env):
        result = runner.invoke(cli, ["dates"], env=env)
        assert result.exit_code == 0
        assert "day-first" in result.output and "month-first" in result.output
        assert "not evidence about the numeric values beside them" in result.output
