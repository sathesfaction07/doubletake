"""Text, JSON and CSV output.

The same three rules the sibling projects use: an unknown value renders as `n/a` in text, `null` in
JSON and an empty cell in CSV -- never as zero. Every category is printed even at zero, so
"considered and found none" reads differently from "not examined". And a total never appears without
the coverage that produced it.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence

from .intake import DatasetReport, DuplicateGroup
from .measure import ColumnComparison, Rollup
from .models import Ambiguity, ColumnOrder, ColumnVerdict, DateReading, FieldKind, FieldVerdict

UNKNOWN_TEXT = "n/a"


def pct(value: float | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value:.1%}"


def money(value: float | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value:,.0f}"


def days(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}d"


def render_verdict(verdict: ColumnVerdict) -> str:
    out = io.StringIO()
    out.write(f"date column: {verdict.field}\n")
    out.write(f"  order            {verdict.order.value}\n")
    out.write(f"  rows             {verdict.total}\n")
    out.write(f"  informative      {verdict.informative}  ({pct(verdict.informative_share)})"
              f"   values that could only have been written one way\n")
    out.write(f"  ambiguous        {verdict.ambiguous}   read two ways, and the two differ\n")
    out.write(f"  identical        {verdict.identical}   read two ways to the SAME date, so harmless\n")
    out.write(f"  unparseable      {verdict.unparseable}   the SAFE failure: loud and attributable\n")

    if verdict.evidence:
        out.write(f"  evidence         {', '.join(verdict.evidence)}\n")
    if verdict.order is ColumnOrder.CONFLICTING:
        names = ", ".join(item.value for item in verdict.conflicting_orders)
        out.write(f"  CONFLICT         rows prove BOTH {names}, so no single order fits this column\n")
        out.write("                   Worse than unknowable: there is no consistent answer to guess.\n")
    elif verdict.order is ColumnOrder.UNKNOWABLE:
        out.write("  UNKNOWABLE       nothing in this column disambiguates, so both orders fit every\n")
        out.write("                   row and any parse is a guess dressed as a reading\n")
    elif verdict.order is ColumnOrder.EMPTY:
        out.write("  EMPTY            no parseable values to reason from\n")
    return out.getvalue()


def render_report(report: DatasetReport) -> str:
    out = io.StringIO()
    out.write(f"{report.rows} submission(s)\n\n")
    out.write(render_verdict(report.date_verdict))

    out.write(f"\n  at risk          {report.at_risk}   rows whose date depends on the order chosen\n")
    out.write(f"  quarter-crossing {report.quarter_crossing}   of those, the ones whose two readings fall in\n")
    out.write("                   DIFFERENT quarters, so the ambiguity moves a reported figure\n")
    out.write(f"  worst divergence {days(report.worst_divergence_days)}   between the two readings of one value\n")

    coverage = report.amount_coverage
    out.write(f"\namount column: {coverage.field}\n")
    out.write(f"  total            {money(coverage.total)}\n")
    out.write(f"  coverage         {pct(coverage.coverage)}  ({coverage.included} of {coverage.rows} rows)\n")
    out.write(f"  excluded         {coverage.excluded}\n")
    out.write("  A total is never shown without its coverage. A sum over a column that skipped rows\n")
    out.write("  is not wrong, it is unlabelled -- and the label is what keeps it off a slide.\n")

    out.write("\n  field kinds:\n")
    for kind, count in report.amount_kinds.items():
        out.write(f"    {kind.value:<12}{count:>5}\n")

    if report.duplicates:
        out.write(f"\n{len(report.duplicates)} exact duplicate group(s), "
                  f"{report.duplicate_rows} extra row(s):\n")
        for group in report.duplicates:
            out.write(f"  {group.team} / {group.title!r}: {', '.join(group.submission_ids)}\n")
        out.write("  Reported rather than collapsed: which copy survives is the owner's judgement.\n")
        out.write("  Exact keys only. Near-duplicate titles need the fuzzy matching Kinship does.\n")

    if report.problems:
        out.write(f"\n{len(report.problems)} unreadable row(s):\n")
        for problem in report.problems:
            out.write(f"  {problem}\n")

    out.write(f"\naggregatable: {report.is_aggregatable}\n")
    if not report.is_aggregatable:
        out.write("  A figure computed from this dataset needs a caveat travelling with it.\n")
    return out.getvalue()


def render_slices(verdicts: Sequence[ColumnVerdict]) -> str:
    out = io.StringIO()
    out.write(f"{'slice':<16}{'rows':>6}{'order':>14}{'informative':>13}"
              f"{'ambiguous':>11}{'identical':>11}{'at risk':>9}\n")
    out.write("-" * 80 + "\n")
    for verdict in verdicts:
        out.write(f"{verdict.field:<16}{verdict.total:>6}{verdict.order.value:>14}"
                  f"{verdict.informative:>13}{verdict.ambiguous:>11}"
                  f"{verdict.identical:>11}{verdict.at_risk:>9}\n")

    counts = dict.fromkeys(ColumnOrder, 0)
    for verdict in verdicts:
        counts[verdict.order] += 1
    out.write("\norder outcomes:\n")
    for value, count in counts.items():
        out.write(f"  {value.value:<14}{count:>4}\n")

    unresolvable = [v for v in verdicts if not v.order.is_established and v.ambiguous]
    if unresolvable:
        out.write(f"\n{len(unresolvable)} slice(s) have rows at risk AND no way to resolve them:\n")
        for verdict in unresolvable:
            out.write(f"  {verdict.field:<16}{verdict.ambiguous} row(s), order {verdict.order.value}\n")
        out.write("\n  Slicing is how this happens. A dataset can carry enough evidence to establish\n")
        out.write("  its order and still contain slices that do not -- and a per-team report is a\n")
        out.write("  slice. The whole-dataset verdict does not transfer to the parts.\n")
    return out.getvalue()


def render_comparison(comparisons: Sequence[ColumnComparison], totals: Rollup) -> str:
    out = io.StringIO()
    out.write(f"{'slice':<16}{'order':>14}{'fixed wrong':>13}{'fixed fails':>13}"
              f"{'silent rate':>13}{'looks ok':>10}{'refused':>9}{'infer wrong':>13}\n")
    out.write("-" * 101 + "\n")
    for item in comparisons:
        worse = item.worse_fixed_choice
        out.write(f"{item.field:<16}{item.verdict.order.value:>14}{worse.silently_wrong:>13}"
                  f"{worse.parse_failures:>13}{pct(worse.silent_error_rate):>13}"
                  f"{worse.looks_healthy!s:>10}{item.inference.refused:>9}"
                  f"{item.inference.wrong:>13}\n")

    out.write(f"\ntotals over {totals.columns} slice(s), {totals.rows} row(s)\n")
    out.write(f"  order established         {totals.established}  ({pct(totals.established_share)})\n")
    out.write(f"  unknowable                {totals.unknowable}\n")
    out.write(f"  conflicting               {totals.conflicting}\n")
    out.write(f"  fixed: silently wrong     {totals.fixed_silently_wrong}\n")
    out.write(f"  fixed: parse failures     {totals.fixed_parse_failures}\n")
    out.write(f"  inference: refused        {totals.inference_refused}\n")
    out.write(f"  inference: wrong          {totals.inference_wrong}\n")
    out.write(f"  slices looking healthy    {totals.columns_looking_healthy}\n")

    out.write("\nhow to read this\n")
    out.write("  Both approaches read the unambiguous values correctly, and neither can invent\n")
    out.write("  information a column does not contain. The difference is what they do when they\n")
    out.write("  cannot tell.\n")
    out.write("\n  A fixed-locale parser always answers, so its mistakes leave no trace. Inference\n")
    out.write("  refuses, which is countable and actionable. Refusals are not a cost being paid\n")
    out.write("  for accuracy -- they are the same uncertainty, made visible.\n")
    if totals.columns_looking_healthy:
        out.write(f"\n  {totals.columns_looking_healthy} slice(s) show ZERO parse failures and wrong dates. ")
        out.write("The aggregate here does\n  have loud failures, so a fleet-wide view would prompt")
        out.write(" someone to look -- but the team\n  owning a silent slice sees only their own,")
        out.write(" where the failure count really is zero.\n")
    return out.getvalue()


def render_readings(readings: Sequence[DateReading], limit: int = 40) -> str:
    out = io.StringIO()
    out.write(f"{'row':>5}{'raw':<18}{'ambiguity':<14}{'day-first':<13}"
              f"{'month-first':<13}{'diverge':>8}  order implied\n")
    out.write("-" * 88 + "\n")
    for reading in readings[:limit]:
        day = reading.day_first.isoformat() if reading.day_first else "-"
        month = reading.month_first.isoformat() if reading.month_first else "-"
        gap = days(reading.divergence_days) if reading.divergence_days is not None else "-"
        implied = reading.implied_order.value if reading.implied_order else "-"
        out.write(f"{reading.row or 0:>5}{reading.raw[:16]:<18}{reading.ambiguity.value:<14}"
                  f"{day:<13}{month:<13}{gap:>8}  {implied}\n")
    if len(readings) > limit:
        out.write(f"  ... and {len(readings) - limit} more\n")

    counts = dict.fromkeys(Ambiguity, 0)
    for reading in readings:
        counts[reading.ambiguity] += 1
    out.write("\nambiguity:\n")
    for value, count in counts.items():
        out.write(f"  {value.value:<14}{count:>5}\n")
    out.write("\n  An ISO date and a spelled-out month are UNAMBIGUOUS and imply no order: they are a\n")
    out.write("  different format, so they are not evidence about the numeric values beside them.\n")
    return out.getvalue()


def render_fields(verdicts: Sequence[FieldVerdict], limit: int = 30) -> str:
    out = io.StringIO()
    out.write(f"{'raw':<26}{'kind':<12}{'value':>14}{'aggregatable':>14}  note\n")
    out.write("-" * 100 + "\n")
    shown = [item for item in verdicts if item.kind is not FieldKind.TYPED][:limit]
    for item in shown:
        value = money(item.value)
        out.write(f"{item.raw[:24]!r:<26}{item.kind.value:<12}{value:>14}"
                  f"{item.aggregatable!s:>14}  {item.note or ''}\n")
    out.write(f"\n(showing {len(shown)} non-clean value(s) of {len(verdicts)} total)\n")
    out.write("\n  WRONG_UNIT is the interesting one: it parses perfectly and cannot be summed with\n")
    out.write("  the column it sits in, so a check that only looks for parse failures never sees it.\n")
    return out.getvalue()


def probability_table(rows: Sequence[int], probability) -> str:
    out = io.StringIO()
    out.write(f"{'rows':>6}{'unknowable':>14}{'knowable':>12}\n")
    out.write("-" * 32 + "\n")
    for count in rows:
        value = probability(count)
        out.write(f"{count:>6}{value:>13.4%}{1 - value:>12.4%}\n")
    return out.getvalue()


def report_json(report: DatasetReport, comparisons: Sequence[ColumnComparison],
                totals: Rollup) -> str:
    return json.dumps(
        {
            "rows": report.rows,
            "date_column": {
                "field": report.date_verdict.field,
                "order": report.date_verdict.order.value,
                "informative": report.date_verdict.informative,
                "informative_share": report.date_verdict.informative_share,
                "ambiguous": report.date_verdict.ambiguous,
                "identical": report.date_verdict.identical,
                "unparseable": report.date_verdict.unparseable,
                "evidence": report.date_verdict.evidence,
                "conflicting_orders": [o.value for o in report.date_verdict.conflicting_orders],
            },
            "at_risk": report.at_risk,
            "quarter_crossing": report.quarter_crossing,
            "worst_divergence_days": report.worst_divergence_days,
            "amount_column": {
                "field": report.amount_coverage.field,
                "total": report.amount_coverage.total,
                "coverage": report.amount_coverage.coverage,
                "included": report.amount_coverage.included,
                "excluded": report.amount_coverage.excluded,
                "complete": report.amount_coverage.is_complete,
                "kinds": {kind.value: count for kind, count in report.amount_kinds.items()},
            },
            "duplicates": [
                {"team": g.team, "title": g.title, "submission_ids": g.submission_ids}
                for g in report.duplicates
            ],
            "aggregatable": report.is_aggregatable,
            "slices": [
                {
                    "slice": item.field,
                    "order": item.verdict.order.value,
                    "rows": item.verdict.total,
                    "informative": item.verdict.informative,
                    "ambiguous": item.verdict.ambiguous,
                    "fixed_silently_wrong": item.worse_fixed_choice.silently_wrong,
                    "fixed_parse_failures": item.worse_fixed_choice.parse_failures,
                    "fixed_silent_error_rate": item.worse_fixed_choice.silent_error_rate,
                    "fixed_looks_healthy": item.worse_fixed_choice.looks_healthy,
                    "inference_refused": item.inference.refused,
                    "inference_wrong": item.inference.wrong,
                    "inference_accuracy_on_resolved": item.inference.accuracy_on_resolved,
                }
                for item in comparisons
            ],
            "totals": {
                "columns": totals.columns,
                "rows": totals.rows,
                "established": totals.established,
                "unknowable": totals.unknowable,
                "conflicting": totals.conflicting,
                "fixed_silently_wrong": totals.fixed_silently_wrong,
                "fixed_parse_failures": totals.fixed_parse_failures,
                "inference_refused": totals.inference_refused,
                "inference_wrong": totals.inference_wrong,
                "columns_looking_healthy": totals.columns_looking_healthy,
            },
            "problems": report.problems,
        },
        indent=2,
    )


def readings_csv(readings: Sequence[DateReading]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["row", "raw", "ambiguity", "day_first", "month_first",
                     "divergence_days", "implied_order"])
    for reading in readings:
        writer.writerow([
            reading.row or "",
            reading.raw,
            reading.ambiguity.value,
            reading.day_first.isoformat() if reading.day_first else "",
            reading.month_first.isoformat() if reading.month_first else "",
            reading.divergence_days if reading.divergence_days is not None else "",
            reading.implied_order.value if reading.implied_order else "",
        ])
    return buffer.getvalue()


def duplicates_csv(groups: Sequence[DuplicateGroup]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["team", "title", "submission_ids", "extra_rows"])
    for group in groups:
        writer.writerow([group.team, group.title, " ".join(group.submission_ids), group.extras])
    return buffer.getvalue()
