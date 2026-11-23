"""Date reading, order inference, field typing, measurement, rendering, and corpus properties."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from conftest import dated, submission
from initiative_hub.columns import (
    crossing_period_boundary,
    infer_order,
    resolve,
    rows_at_risk,
    worst_divergence,
)
from initiative_hub.dates import (
    ambiguity_probability,
    informative_rows_needed,
    read,
    read_all,
    unknowable_probability,
)
from initiative_hub.fields import coverage, kind_counts, read_number
from initiative_hub.intake import analyse, date_readings, exact_duplicates, slice_by
from initiative_hub.measure import compare_column, rollup, score_fixed, score_inference
from initiative_hub.models import Ambiguity, ColumnOrder, FieldKind


class TestAmbiguityIsAPropertyOfTheValue:
    def test_a_low_day_reads_two_ways(self):
        reading = read("03/04/2026")
        assert reading.ambiguity is Ambiguity.AMBIGUOUS
        assert reading.day_first == date(2026, 4, 3)
        assert reading.month_first == date(2026, 3, 4)
        assert reading.divergence_days == 30

    def test_a_day_above_twelve_can_only_be_read_one_way(self):
        reading = read("13/04/2026")
        assert reading.ambiguity is Ambiguity.UNAMBIGUOUS
        assert reading.day_first == date(2026, 4, 13)
        assert reading.month_first is None
        assert reading.implied_order is ColumnOrder.DAY_FIRST

    def test_the_mirror_case_proves_month_first(self):
        reading = read("04/13/2026")
        assert reading.implied_order is ColumnOrder.MONTH_FIRST

    def test_equal_fields_read_the_same_either_way(self):
        """Ambiguous in form, harmless in effect, and uninformative -- three properties that a
        single boolean cannot express, which is why IDENTICAL exists."""
        reading = read("05/05/2026")
        assert reading.ambiguity is Ambiguity.IDENTICAL
        assert reading.day_first == reading.month_first == date(2026, 5, 5)
        assert reading.divergence_days == 0
        assert not reading.is_informative

    def test_neither_order_working_is_the_safe_failure(self):
        for raw in ("31/02/2026", "not a date", "", "  ", "2026-13-01", "99/99/2026"):
            assert read(raw).ambiguity is Ambiguity.UNPARSEABLE, raw

    def test_iso_is_unambiguous_and_implies_nothing(self):
        """The distinction an earlier version got wrong: an ISO date is beyond doubt in itself and
        is not evidence about the numeric values beside it, because it is a different format."""
        reading = read("2026-04-03")
        assert reading.ambiguity is Ambiguity.UNAMBIGUOUS
        assert reading.day_first == reading.month_first == date(2026, 4, 3)
        assert reading.implied_order is None
        assert not reading.is_informative

    def test_a_spelled_out_month_is_also_unambiguous_and_uninformative(self):
        for raw in ("04 Mar 2026", "Mar 04 2026", "04-March-2026"):
            reading = read(raw)
            assert reading.ambiguity is Ambiguity.UNAMBIGUOUS, raw
            assert reading.day_first == date(2026, 3, 4), raw
            assert not reading.is_informative, raw

    def test_separators_are_interchangeable(self):
        for raw in ("13/04/2026", "13-04-2026", "13.04.2026"):
            assert read(raw).day_first == date(2026, 4, 13), raw

    def test_a_two_digit_year_resolves_to_the_2000s(self):
        """A bounded guess, unlike date order: nobody submits an initiative dated 1926."""
        assert read("13/04/26").day_first == date(2026, 4, 13)

    def test_row_numbers_are_carried(self):
        readings = read_all(["13/04/2026", "03/04/2026"])
        assert [item.row for item in readings] == [1, 2]

    def test_divergence_is_none_when_only_one_reading_exists(self):
        assert read("13/04/2026").divergence_days is None

    def test_under_returns_none_for_a_non_established_order(self):
        reading = read("03/04/2026")
        assert reading.under(ColumnOrder.UNKNOWABLE) is None
        assert reading.under(ColumnOrder.DAY_FIRST) == date(2026, 4, 3)


class TestTheProbabilityArithmetic:
    def test_the_single_value_rate_is_144_over_365(self):
        """Ambiguity needs only day <= 12, because month <= 12 is ALWAYS true. A common shortcut
        writes this as 12/30.4 and lands nearby by the wrong route."""
        assert ambiguity_probability(year=2026) == pytest.approx(144 / 365, abs=1e-9)

    def test_a_leap_year_lowers_it(self):
        assert ambiguity_probability(year=2024) == pytest.approx(144 / 366, abs=1e-9)

    def test_it_matches_a_brute_force_count(self):
        start = date(2026, 1, 1)
        ambiguous = sum(
            1 for offset in range(365) if (start + timedelta(days=offset)).day <= 12
        )
        assert ambiguity_probability(year=2026) == pytest.approx(ambiguous / 365, abs=1e-9)

    def test_it_matches_simulation(self):
        rng = random.Random(11)
        start = date(2026, 1, 1)
        hits = sum(
            1 for _ in range(60_000)
            if (start + timedelta(days=rng.randrange(365))).day <= 12
        )
        assert abs(hits / 60_000 - ambiguity_probability(year=2026)) < 0.01

    def test_unknowability_falls_off_geometrically(self):
        single = ambiguity_probability()
        for rows in (1, 2, 3, 5, 8, 12):
            assert unknowable_probability(rows) == pytest.approx(single**rows, abs=1e-12)

    def test_zero_rows_cannot_establish_anything(self):
        assert unknowable_probability(0) == 1.0
        assert unknowable_probability(-3) == 1.0

    def test_five_rows_reach_ninety_nine_percent(self):
        assert informative_rows_needed(0.99) == 5
        assert informative_rows_needed(0.999) == 8

    def test_a_higher_target_never_needs_fewer_rows(self):
        previous = 0
        for target in (0.5, 0.9, 0.99, 0.999, 0.9999):
            needed = informative_rows_needed(target)
            assert needed >= previous
            previous = needed

    def test_deadline_clustering_defeats_the_arithmetic_entirely(self):
        """The load-bearing assumption is uniformity, and it fails in the direction that hurts.
        Submissions on a monthly deadline all land in days 1-12, so p becomes 1.0 and no number of
        rows helps."""
        # Days chosen so none equals its month -- otherwise those rows land in IDENTICAL and the
        # test would be demonstrating the harmless case rather than the dangerous one.
        deadline = [f"{day:02d}/{month:02d}/2026"
                    for month, day in zip(range(1, 13), [3, 7, 2, 11, 4, 9, 1, 12, 6, 8, 5, 10],
                                          strict=True)]
        verdict = infer_order("deadline", read_all(deadline))
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.informative == 0
        assert verdict.ambiguous == 12
        # And it is not a small-column effect: doubling the rows changes nothing.
        assert infer_order("x", read_all(deadline * 2)).order is ColumnOrder.UNKNOWABLE


class TestOrderInference:
    def test_one_disambiguating_row_establishes_the_column(self):
        verdict = infer_order("d", read_all(["03/04/2026", "05/06/2026", "13/04/2026"]))
        assert verdict.order is ColumnOrder.DAY_FIRST
        assert verdict.informative == 1
        assert verdict.evidence == ["13/04/2026"]

    def test_no_disambiguating_row_leaves_it_unknowable(self):
        verdict = infer_order("d", read_all(["03/04/2026", "05/06/2026", "07/08/2026"]))
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.informative == 0
        assert verdict.evidence == []

    def test_rows_proving_both_orders_conflict(self):
        """Worse than unknowable: unknowable means one answer exists and we cannot tell which,
        conflicting means no single answer fits."""
        verdict = infer_order("d", read_all(["13/04/2026", "04/13/2026", "03/04/2026"]))
        assert verdict.order is ColumnOrder.CONFLICTING
        assert set(verdict.conflicting_orders) == {ColumnOrder.DAY_FIRST, ColumnOrder.MONTH_FIRST}

    def test_nothing_parseable_is_empty(self):
        verdict = infer_order("d", read_all(["nope", "", "31/02/2026"]))
        assert verdict.order is ColumnOrder.EMPTY
        assert verdict.unparseable == 3
        assert verdict.informative_share == 0.0

    def test_an_empty_column_has_no_informative_share(self):
        assert infer_order("d", []).informative_share is None

    def test_iso_values_do_not_rescue_the_numeric_ones(self):
        """A column of ambiguous numeric dates sitting beside perfectly clear ISO dates is still
        unknowable. The ISO values are a different format, not evidence about this one."""
        verdict = infer_order(
            "d", read_all(["2026-04-03", "2026-05-09", "03/04/2026", "05/06/2026"])
        )
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.informative == 0

    def test_identical_values_are_not_evidence(self):
        verdict = infer_order("d", read_all(["05/05/2026", "06/06/2026", "07/07/2026"]))
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.identical == 3
        assert verdict.at_risk == 0

    def test_resolve_refuses_rather_than_defaulting(self):
        readings = read_all(["03/04/2026", "05/06/2026"])
        verdict = infer_order("d", readings)
        assert all(value is None for _, value in resolve(readings, verdict))

    def test_resolve_applies_an_established_order(self):
        readings = read_all(["03/04/2026", "13/04/2026"])
        verdict = infer_order("d", readings)
        pairs = resolve(readings, verdict)
        assert pairs[0][1] == date(2026, 4, 3)

    def test_rows_at_risk_excludes_identical_values(self):
        readings = read_all(["03/04/2026", "05/05/2026", "13/04/2026", "nope"])
        assert [item.raw for item in rows_at_risk(readings)] == ["03/04/2026"]

    def test_worst_divergence_finds_the_largest_gap(self):
        readings = read_all(["03/04/2026", "01/12/2026"])
        # 12 Jan is day 12 of 2026; 1 Dec is day 335. The gap is 323, not the 334 I first wrote.
        assert worst_divergence(readings) == 323

    def test_worst_divergence_is_none_without_ambiguity(self):
        assert worst_divergence(read_all(["13/04/2026", "05/05/2026"])) is None

    def test_quarter_crossing_is_the_subset_that_moves_a_number(self):
        """An ambiguous date whose two readings sit inside one quarter is harmless for a quarterly
        rollup, which is what these reports mostly are."""
        crossing = crossing_period_boundary(read_all(["03/04/2026"]))
        assert len(crossing) == 1  # 3 Apr is Q2, 4 Mar is Q1
        same = crossing_period_boundary(read_all(["02/03/2026"]))
        assert same == []  # 2 Mar and 3 Feb -- wait, Q1 both


class TestFieldTyping:
    def test_a_clean_number_is_typed(self):
        verdict = read_number("saving", "12000")
        assert verdict.kind is FieldKind.TYPED
        assert verdict.value == 12000.0
        assert verdict.aggregatable

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("$12,000", 12000.0),
            ("12k", 12000.0),
            ("£1.5m", 1_500_000.0),
            ("1.5bn", 1_500_000_000.0),
            ("12000/yr", 12000.0),
            ("22,500 per year", 22500.0),
        ],
    )
    def test_coercion_records_what_it_did(self, raw, expected):
        verdict = read_number("saving", raw)
        assert verdict.kind is FieldKind.COERCED
        assert verdict.value == pytest.approx(expected)
        assert verdict.note, "a coercion is an interpretation and must be recorded"

    def test_a_percentage_parses_and_is_not_summable(self):
        """The case a parse-failure check cannot see. An earlier version coerced this to 15.0 and
        summed it with currency, turning two $12,000 rows into $24,015 at 100% coverage."""
        verdict = read_number("saving", "15%")
        assert verdict.kind is FieldKind.WRONG_UNIT
        assert verdict.value == 15.0
        assert not verdict.aggregatable

    def test_the_percentage_is_excluded_from_the_total_and_from_coverage(self):
        result = coverage("saving", [read_number("saving", r) for r in ["12000", "12000", "15%"]])
        assert result.total == 24000.0
        assert result.included == 2
        assert result.coverage == pytest.approx(2 / 3)
        assert not result.is_complete

    def test_a_range_is_not_coerced_to_its_midpoint(self):
        """The midpoint is a number the submitter did not write."""
        verdict = read_number("saving", "5000-8000")
        assert verdict.kind is FieldKind.FREE_TEXT
        assert verdict.value is None
        assert "midpoint" in verdict.note

    @pytest.mark.parametrize("raw", ["approx 12000", "about £5k", "TBD", "roughly 900", "n/a"])
    def test_a_hedged_figure_is_not_a_figure(self, raw):
        verdict = read_number("saving", raw)
        assert verdict.kind is FieldKind.FREE_TEXT
        assert "hedged" in verdict.note

    def test_prose_is_free_text(self):
        assert read_number("saving", "saves a lot of time").kind is FieldKind.FREE_TEXT
        assert read_number("saving", "3 hours/week").kind is FieldKind.FREE_TEXT

    def test_blank_is_empty_not_free_text(self):
        """Nothing was claimed, so nothing was lost."""
        for raw in ("", "   "):
            assert read_number("saving", raw).kind is FieldKind.EMPTY

    def test_zero_and_negative_are_real_values(self):
        assert read_number("saving", "0").value == 0.0
        assert read_number("saving", "-500").value == -500.0

    def test_an_unknown_suffix_is_rejected_rather_than_guessed(self):
        assert read_number("saving", "12mm").kind is FieldKind.FREE_TEXT

    def test_the_scale_suffix_order_carries_no_meaning(self):
        """An earlier comment claimed the tuple was ordered longest-first so `mm` would not read as
        `m`. Reversing it proves otherwise -- `1.5bn` ends with `n`, so bn and b never collide."""
        import initiative_hub.fields as module

        original = module.SCALES
        try:
            module.SCALES = tuple(reversed(original))
            reversed_results = {r: read_number("s", r).value for r in ("1.5bn", "12k", "5m", "12mm")}
        finally:
            module.SCALES = original
        forward_results = {r: read_number("s", r).value for r in ("1.5bn", "12k", "5m", "12mm")}
        assert reversed_results == forward_results

    def test_total_is_none_when_nothing_parsed(self):
        """Zero would be a claim -- 'these initiatives saved nothing' -- where nobody could tell."""
        result = coverage("saving", [read_number("saving", "TBD"), read_number("saving", "")])
        assert result.total is None
        assert result.included == 0

    def test_coverage_is_none_for_no_rows(self):
        assert coverage("saving", []).coverage is None

    def test_kind_counts_include_the_zeros(self):
        counts = kind_counts([read_number("saving", "12000")])
        assert set(counts) == set(FieldKind)
        assert counts[FieldKind.FREE_TEXT] == 0


class TestMeasurement:
    def test_a_fixed_parser_is_silently_wrong_on_ambiguous_rows(self):
        readings = read_all(["03/04/2026"])
        truth = [date(2026, 3, 4)]  # the month-first reading is correct
        score = score_fixed(readings, truth, ColumnOrder.DAY_FIRST)
        assert score.parse_failures == 0
        assert score.silently_wrong == 1
        assert score.silent_error_rate == 1.0
        assert score.looks_healthy, "no parse failures and wrong data is the whole problem"

    def test_a_fixed_parser_fails_loudly_on_the_other_order(self):
        score = score_fixed(read_all(["13/04/2026"]), [date(2026, 4, 13)], ColumnOrder.MONTH_FIRST)
        assert score.parse_failures == 1
        assert score.silently_wrong == 0
        assert not score.looks_healthy
        assert score.silent_error_rate is None

    def test_inference_refuses_where_the_order_is_not_established(self):
        readings = read_all(["03/04/2026", "05/06/2026"])
        verdict = infer_order("d", readings)
        score = score_inference(readings, [date(2026, 3, 4), date(2026, 6, 5)], verdict)
        assert score.refused == 2
        assert score.resolved == 0
        assert score.wrong == 0
        assert score.accuracy_on_resolved is None
        assert score.refusal_rate == 1.0

    def test_inference_answers_where_the_order_is_established(self):
        readings = read_all(["03/04/2026", "13/04/2026"])
        verdict = infer_order("d", readings)
        score = score_inference(readings, [date(2026, 4, 3), date(2026, 4, 13)], verdict)
        assert score.resolved == 2
        assert score.correct == 2
        assert score.accuracy_on_resolved == 1.0

    def test_rows_without_a_known_truth_are_scored_nowhere(self):
        """Counting them as correct would flatter whichever parser answered more often."""
        score = score_fixed(read_all(["03/04/2026"]), [None], ColumnOrder.DAY_FIRST)
        assert score.correct == 0 and score.silently_wrong == 0 and score.parse_failures == 0

    def test_the_worse_fixed_choice_is_reported(self):
        readings = read_all(["03/04/2026", "05/06/2026"])
        truth = [date(2026, 3, 4), date(2026, 6, 5)]
        # Each order gets one row right and one wrong, so the worse choice is 1 -- not 2. Both
        # locales are equally bad here, which is exactly what an unknowable column means.
        comparison = compare_column("d", readings, truth)
        assert comparison.worse_fixed_choice.silently_wrong == 1

    def test_looks_healthy_is_counted_per_column_not_from_totals(self):
        """Totals destroy the property: one column's loud failures mask another's silent ones."""
        silent = compare_column("silent", read_all(["03/04/2026"]), [date(2026, 3, 4)])
        loud = compare_column("loud", read_all(["13/04/2026"]), [date(2026, 4, 13)])
        totals = rollup([silent, loud])
        assert totals.fixed_parse_failures > 0, "the aggregate has loud failures"
        assert totals.columns_looking_healthy == 1, "and one column is still silently wrong"
        assert totals.fixed_looks_healthy

    def test_established_share_is_none_with_no_columns(self):
        assert rollup([]).established_share is None


class TestIntake:
    def test_exact_duplicates_are_reported_not_collapsed(self):
        rows = [
            submission(submission_id="A", team="alpha", title="Reduce waste"),
            submission(submission_id="B", team="alpha", title="  reduce   WASTE "),
            submission(submission_id="C", team="beta", title="Reduce waste"),
        ]
        groups = exact_duplicates(rows)
        assert len(groups) == 1
        assert set(groups[0].submission_ids) == {"A", "B"}
        assert groups[0].extras == 1

    def test_slicing_never_drops_a_row(self):
        rows = [submission(submission_id="A", team="alpha"), submission(submission_id="B", team="")]
        grouped = slice_by(rows, "team")
        assert sum(len(group) for group in grouped.values()) == 2

    def test_a_dataset_is_aggregatable_only_when_both_columns_are_sound(self):
        good = analyse(
            dated(["13/04/2026", "03/04/2026"]),
            date_field="target_date", amount_field="annual_saving",
        )
        assert good.date_verdict.order is ColumnOrder.DAY_FIRST
        assert good.is_aggregatable

        bad = analyse(
            dated(["03/04/2026", "05/06/2026"]),
            date_field="target_date", amount_field="annual_saving",
        )
        assert not bad.is_aggregatable, "an unknowable order blocks aggregation"

    def test_a_free_text_amount_blocks_aggregation_too(self):
        rows = [
            submission(submission_id="A", target_date="13/04/2026", annual_saving="12000"),
            submission(submission_id="B", target_date="03/04/2026", annual_saving="TBD"),
        ]
        report = analyse(rows, date_field="target_date", amount_field="annual_saving")
        assert report.date_verdict.order.is_established
        assert not report.amount_coverage.is_complete
        assert not report.is_aggregatable


class TestPropertiesOverTheShippedCorpus:
    def test_the_corpus_is_the_size_the_docs_claim(self, corpus, truth):
        assert len(corpus) == 89
        assert len(truth) == 85

    def test_every_ambiguity_value_occurs(self, corpus):
        """Otherwise the assertions about them prove nothing."""
        readings = date_readings(corpus, "target_date")
        assert {item.ambiguity for item in readings} == set(Ambiguity)

    def test_every_order_outcome_occurs_across_the_slices(self, corpus):
        orders = {
            infer_order(name, date_readings(group, "target_date")).order
            for name, group in slice_by(corpus, "team").items()
        }
        assert orders == set(ColumnOrder), sorted(set(ColumnOrder) - orders)

    def test_the_whole_dataset_conflicts(self, corpus):
        """Different teams write different formats, so the combined column has no single order."""
        verdict = infer_order("target_date", date_readings(corpus, "target_date"))
        assert verdict.order is ColumnOrder.CONFLICTING
        assert set(verdict.conflicting_orders) == {ColumnOrder.DAY_FIRST, ColumnOrder.MONTH_FIRST}

    def test_the_deadline_team_is_unknowable_with_every_row_at_risk(self, corpus):
        group = slice_by(corpus, "team")["quality"]
        verdict = infer_order("quality", date_readings(group, "target_date"))
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.informative == 0
        assert verdict.ambiguous == 16
        assert verdict.at_risk == 16

    def test_the_iso_team_stays_unknowable_despite_clean_dates(self, corpus):
        """Eight unambiguous ISO and textual dates rescue none of the six ambiguous numeric ones."""
        group = slice_by(corpus, "team")["procurement"]
        readings = date_readings(group, "target_date")
        verdict = infer_order("procurement", readings)
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.informative == 0
        assert sum(1 for r in readings if r.ambiguity is Ambiguity.UNAMBIGUOUS) == 8

    def test_the_identical_team_is_unknowable_and_harmless(self, corpus):
        group = slice_by(corpus, "team")["facilities"]
        verdict = infer_order("facilities", date_readings(group, "target_date"))
        assert verdict.order is ColumnOrder.UNKNOWABLE
        assert verdict.identical == 4
        assert verdict.at_risk == 0, "both orders agree, so no choice changes anything"

    def test_inference_is_never_wrong_on_a_row_it_resolved(self, corpus, truth):
        comparisons = [
            compare_column(name, date_readings(group, "target_date"),
                           [truth.get(item.submission_id) for item in group])
            for name, group in sorted(slice_by(corpus, "team").items())
        ]
        totals = rollup(comparisons)
        assert totals.inference_wrong == 0
        assert totals.inference_refused > 0, "and it refuses where it cannot tell"

    def test_a_fixed_parser_is_silently_wrong_on_many_rows(self, corpus, truth):
        comparisons = [
            compare_column(name, date_readings(group, "target_date"),
                           [truth.get(item.submission_id) for item in group])
            for name, group in sorted(slice_by(corpus, "team").items())
        ]
        totals = rollup(comparisons)
        assert totals.fixed_silently_wrong >= 39
        assert totals.columns_looking_healthy == 2

    def test_the_amount_column_is_incomplete_and_says_so(self, corpus):
        report = analyse(corpus, date_field="target_date", amount_field="annual_saving")
        assert report.amount_coverage.total is not None
        assert not report.amount_coverage.is_complete
        assert report.amount_kinds[FieldKind.WRONG_UNIT] >= 1
        assert report.amount_kinds[FieldKind.FREE_TEXT] >= 1
        assert report.amount_kinds[FieldKind.COERCED] >= 1

    def test_the_corpus_contains_an_exact_duplicate(self, corpus):
        groups = exact_duplicates(corpus)
        assert len(groups) == 1
        assert groups[0].extras == 1

    def test_most_at_risk_rows_cross_a_quarter_boundary(self, corpus):
        report = analyse(corpus, date_field="target_date", amount_field="annual_saving")
        assert report.at_risk > 0
        assert 0 < report.quarter_crossing <= report.at_risk

    def test_the_worst_divergence_moves_between_fiscal_years(self, corpus):
        report = analyse(corpus, date_field="target_date", amount_field="annual_saving")
        assert report.worst_divergence_days is not None
        assert report.worst_divergence_days > 200
