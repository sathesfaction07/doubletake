"""CLI.

Exit codes gate on what is demonstrable, and specifically NOT on ambiguity:

  `check`   1 on a CONFLICTING date column, or an unreadable row. A conflicting column has no
            consistent order, so parsing it as a unit is guaranteed wrong for some rows.
            NOT on an unknowable column. Unknowability is a property of the data a submitter
            produced, and a gate that fires whenever a team happens to submit on the first of the
            month would be switched off in a week. It is reported loudly instead.

  `report`  1 if the dataset is not aggregatable, so a pipeline computing a total has to opt in
            rather than inherit a number with an unstated caveat.

  `audit`   1 only if INFERENCE gets a resolved row wrong. The fixed-locale baseline is wrong on
            many by design -- that is the finding, not a regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .columns import infer_order
from .config import Settings
from .dates import ambiguity_probability, informative_rows_needed, read, unknowable_probability
from .intake import (
    amount_verdicts,
    analyse,
    date_readings,
    load_submissions,
    load_truth,
    slice_by,
)
from .measure import compare_column, rollup
from .models import ColumnOrder
from .render import (
    duplicates_csv,
    probability_table,
    readings_csv,
    render_comparison,
    render_fields,
    render_readings,
    render_report,
    render_slices,
    report_json,
)


def _load(settings: Settings):
    submissions, problems = load_submissions(settings.submissions)
    return submissions, problems


@click.group()
@click.version_option()
def cli() -> None:
    """Validate initiative submissions, starting from dates that read two ways."""


@cli.command()
def check() -> None:
    """Report what the dataset establishes about its own date format."""
    settings = Settings()
    submissions, problems = _load(settings)
    report = analyse(
        submissions,
        date_field=settings.date_field,
        amount_field=settings.amount_field,
        problems=problems,
    )
    click.echo(render_report(report))

    order = report.date_verdict.order
    if order is ColumnOrder.CONFLICTING or problems:
        click.echo("\nFAIL: a conflicting date column has no consistent order, so parsing it as a")
        click.echo("  unit is guaranteed wrong for some rows; an unreadable row is a submission we")
        click.echo("  know nothing about.")
        sys.exit(1)
    if order is ColumnOrder.UNKNOWABLE:
        click.echo("\nThe date order is UNKNOWABLE and that does NOT fail this check. It is a property")
        click.echo("  of what submitters typed, not a defect in the pipeline, and a gate that fires")
        click.echo("  whenever a team submits on the first of the month gets switched off.")


@cli.command()
@click.option("--sliced", is_flag=True, help="Report knowability per slice as well as overall")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Write JSON and CSV here")
def report(sliced: bool, output: Path | None) -> None:
    """Full intake report, optionally per slice."""
    settings = Settings()
    submissions, problems = _load(settings)
    dataset = analyse(
        submissions,
        date_field=settings.date_field,
        amount_field=settings.amount_field,
        problems=problems,
    )
    click.echo(render_report(dataset))

    comparisons = []
    if sliced:
        truth, _ = load_truth(settings.truth)
        verdicts = []
        for name, group in sorted(slice_by(submissions, settings.slice_by).items()):
            readings = date_readings(group, settings.date_field)
            verdicts.append(infer_order(name, readings))
            comparisons.append(
                compare_column(name, readings, [truth.get(item.submission_id) for item in group])
            )
        click.echo(f"\nsliced by {settings.slice_by}\n")
        click.echo(render_slices(verdicts))

    if output:
        output.mkdir(parents=True, exist_ok=True)
        totals = rollup(comparisons)
        (output / "report.json").write_text(
            report_json(dataset, comparisons, totals), encoding="utf-8"
        )
        (output / "readings.csv").write_text(
            readings_csv(date_readings(submissions, settings.date_field)), encoding="utf-8"
        )
        (output / "duplicates.csv").write_text(
            duplicates_csv(dataset.duplicates), encoding="utf-8"
        )
        click.echo(f"\nwrote {output / 'report.json'}, readings.csv and duplicates.csv")

    if not dataset.is_aggregatable:
        click.echo("\nFAIL: this dataset is not aggregatable as it stands, so a total computed from")
        click.echo("  it needs a caveat travelling with it. Fix the reported problems or opt in")
        click.echo("  deliberately.")
        sys.exit(1)


@cli.command()
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Write JSON here")
def audit(output: Path | None) -> None:
    """Score a fixed-locale parser against inference, using known true dates."""
    settings = Settings()
    submissions, problems = _load(settings)
    truth, truth_problems = load_truth(settings.truth)

    comparisons = []
    for name, group in sorted(slice_by(submissions, settings.slice_by).items()):
        readings = date_readings(group, settings.date_field)
        comparisons.append(
            compare_column(name, readings, [truth.get(item.submission_id) for item in group])
        )
    totals = rollup(comparisons)

    click.echo(f"{len(submissions)} submissions, {len(truth)} with a known true date\n")
    click.echo(render_comparison(comparisons, totals))

    if output:
        output.mkdir(parents=True, exist_ok=True)
        dataset = analyse(
            submissions,
            date_field=settings.date_field,
            amount_field=settings.amount_field,
            problems=problems,
        )
        (output / "audit.json").write_text(
            report_json(dataset, comparisons, totals), encoding="utf-8"
        )
        click.echo(f"\nwrote {output / 'audit.json'}")

    # Checked BEFORE conclusions are drawn. An unreadable truth file makes every row unscoreable, and
    # reporting that as "inference was wrong" would blame the code for a broken fixture.
    if truth_problems:
        click.echo(f"\nFAIL: {len(truth_problems)} unreadable row(s) in the known-dates file, so")
        click.echo("  nothing above can be scored against it")
        sys.exit(1)

    if totals.fixed_silently_wrong:
        click.echo(f"\nA fixed-locale parser is silently wrong on {totals.fixed_silently_wrong} row(s)"
                   f" here. Not a failure of\n  this run -- it IS the finding.")

    # Gate on the approach actually shipped. Failing because the baseline is bad would fire on every
    # run, and a check that always fires carries no information.
    if totals.inference_wrong:
        click.echo(f"\nFAIL: inference produced {totals.inference_wrong} wrong date(s) among the rows")
        click.echo("  it was willing to resolve")
        sys.exit(1)


@cli.command()
@click.argument("value")
def explain(value: str) -> None:
    """Read one date string under both orders and say what it establishes."""
    reading = read(value)
    click.echo(f"{value!r}")
    click.echo(f"  ambiguity      {reading.ambiguity.value}")
    click.echo(f"  as day-first   {reading.day_first or 'n/a'}")
    click.echo(f"  as month-first {reading.month_first or 'n/a'}")
    click.echo(f"  divergence     {reading.divergence_days if reading.divergence_days is not None else 'n/a'}"
               f"{' days' if reading.divergence_days else ''}")
    click.echo(f"  informative    {reading.is_informative}")
    click.echo(f"  implies order  {reading.implied_order.value if reading.implied_order else 'n/a'}")

    if reading.ambiguity.value == "ambiguous":
        click.echo("\n  Both readings are real dates and they differ. A fixed-locale parser returns")
        click.echo("  one of them with no error and no warning, and the wrong answer is exactly as")
        click.echo("  plausible as the right one.")
    elif reading.ambiguity.value == "identical":
        click.echo("\n  Both orders give the same date, so this value cannot be read wrongly -- and it")
        click.echo("  also tells you nothing about the order of the values beside it.")
    elif reading.ambiguity.value == "unparseable":
        click.echo("\n  The SAFE failure. Loud, attributable to a row, and impossible to aggregate by")
        click.echo("  accident.")
    elif not reading.is_informative:
        click.echo("\n  Unambiguous in itself and NOT evidence about the column: an ISO date or a")
        click.echo("  spelled-out month is a different format, so it rescues none of its neighbours.")


@cli.command(name="rows-needed")
def rows_needed() -> None:
    """The arithmetic: how many rows before a column's date order is knowable."""
    settings = Settings()
    single = ambiguity_probability()
    click.echo("A date reads two ways when its DAY field is 12 or less, because the month field")
    click.echo("always is. So the count is 12 months x 12 days, over the days in the year.\n")
    click.echo(f"  P(one date is ambiguous) = 144/365 = {single:.4f}")
    click.echo(f"  leap year                = 144/366 = {ambiguity_probability(year=2024):.4f}\n")
    click.echo(probability_table([1, 2, 3, 5, 8, 12, 20], unknowable_probability))
    click.echo(f"rows needed for {settings.confidence:.0%} confidence the order is knowable: "
               f"{informative_rows_needed(settings.confidence)}")
    click.echo("\nThe assumption doing the work is UNIFORMITY, and it is often false in the")
    click.echo("direction that hurts. Submissions clustered on a monthly reporting deadline all")
    click.echo("land in days 1-12, which drives the single-value probability to 1.0 and makes")
    click.echo("EVERY column unknowable regardless of how many rows it has.")
    click.echo("\nSo the risk is set by when people submit, not by how much data you have.")


@cli.command()
def fields() -> None:
    """Every non-clean value in the amount column, with how it was interpreted."""
    settings = Settings()
    submissions, _ = _load(settings)
    click.echo(render_fields(amount_verdicts(submissions, settings.amount_field)))


@cli.command()
def dates() -> None:
    """Every date reading, under both orders."""
    settings = Settings()
    submissions, _ = _load(settings)
    click.echo(render_readings(date_readings(submissions, settings.date_field)))


@cli.command()
def doctor() -> None:
    """Config, inputs, and how to read the vocabulary."""
    settings = Settings()
    click.echo("configuration")
    for field in ("submissions", "truth"):
        path = getattr(settings, field)
        click.echo(f"  {field:<14}{path!s:<32}{'ok' if path.exists() else 'MISSING'}")
    click.echo(f"  {'date field':<14}{settings.date_field}")
    click.echo(f"  {'amount field':<14}{settings.amount_field}")
    click.echo(f"  {'slice by':<14}{settings.slice_by}")
    click.echo(f"  {'confidence':<14}{settings.confidence}")

    click.echo("\nno credentials, no network, no model. The accuracy problem here is arithmetic on")
    click.echo("calendars and a decision about what to do when a value cannot be resolved.")

    click.echo("\nambiguity, per value")
    click.echo("  unambiguous   cannot be misread. NOT the same as informative -- an ISO date and a")
    click.echo("                spelled-out month imply nothing about the numeric values beside them")
    click.echo("  ambiguous     both orders give real dates, and they DIFFER. The dangerous case")
    click.echo("  identical     both orders give the SAME date, so harmless and uninformative")
    click.echo("  unparseable   neither order works. The SAFE failure: loud and attributable")

    click.echo("\norder, per column")
    click.echo("  day_first / month_first   established, and the proving rows can be named")
    click.echo("  unknowable               nothing disambiguates, so any parse is a guess")
    click.echo("  conflicting              rows prove BOTH orders, so no single order fits")
    click.echo("  empty                    nothing parseable to reason from")

    click.echo("\nthe asymmetry this tool is built on")
    click.echo("  A value that FAILS to parse is safe, because it is loud: someone fixes the row.")
    click.echo("  A value that parses TWO WAYS is dangerous, because it is silent: it lands in the")
    click.echo("  database, gets aggregated, and nothing ever says which reading was taken.")

    click.echo("\nnot built here, on purpose")
    click.echo("  Fuzzy dedup across near-identical titles -- Kinship does blocking, similarity")
    click.echo("  and clustering, and shows that single linkage chains across any threshold below 1.")
    click.echo("  Only exact-key dedup lives here.")

    missing = [f for f in ("submissions", "truth") if not getattr(settings, f).exists()]
    if missing:
        click.echo(f"\nFAIL: missing input(s): {', '.join(missing)}")
        click.echo("Run: python data/generate.py")
        sys.exit(1)
