# Doubletake — Technical Architecture

## Overview

Validates initiative submissions, starting from the values that read two ways.

**Design principle:** a value that FAILS to parse is safe, because it is loud — someone sees the error
and fixes the row. A value that parses TWO WAYS is dangerous, because it is silent: it lands in the
database, gets aggregated, and nothing ever records which reading was taken.

No model. The accuracy problem is arithmetic on calendars, plus a decision about what to do when a
value cannot be resolved.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       CLI (doubletake)                                    │
│                                                                              │
│  check        what the dataset establishes about its own format               │
│               EXIT 1 on a CONFLICTING column or an unreadable row. NOT on an  │
│               UNKNOWABLE one -- that is a property of what submitters typed,   │
│               and a gate firing whenever a team submits on the 1st of the      │
│               month gets switched off in a week                               │
│  report       full intake report; --sliced adds per-team knowability          │
│               EXIT 1 if the dataset is not aggregatable, so a pipeline has to  │
│               opt in rather than inherit an unstated caveat                    │
│  audit        fixed-locale parser vs inference, against known true dates       │
│               EXIT 1 only if INFERENCE is wrong on a row it resolved. The      │
│               fixed baseline is wrong on many by design -- that is the finding  │
│  rows-needed  the probability arithmetic, and the assumption that breaks it     │
│  explain      one value, read both ways, and what it establishes               │
│  fields       every non-clean amount with how it was interpreted               │
│  dates        every date reading under both orders                            │
│  doctor       config, and how to read the vocabulary                          │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              dates.py                                         │
│                                                                              │
│  `dateutil` is NOT a dependency, and setup.sh asserts its absence. Its        │
│    `dayfirst` flag takes the exact decision this module exists to expose:      │
│    set it once, globally, and every ambiguous value is silently resolved in     │
│    the direction you guessed. The behaviour under examination cannot also be    │
│    the implementation.                                                        │
│                                                                              │
│  read() returns BOTH readings and a classification, choosing neither:          │
│                                                                              │
│    value          ambiguity     day-first     month-first   informative        │
│    13/04/2026     unambiguous   2026-04-13    -             YES  day_first     │
│    04/13/2026     unambiguous   -             2026-04-13    YES  month_first   │
│    03/04/2026     ambiguous     2026-04-03    2026-03-04    no                 │
│    05/05/2026     identical     2026-05-05    2026-05-05    no                 │
│    2026-04-03     unambiguous   2026-04-03    2026-04-03    no  <- NOT evidence│
│    04 Mar 2026    unambiguous   2026-03-04    2026-03-04    no  <- NOT evidence│
│    31/02/2026     unparseable   -             -             no                 │
│                                                                              │
│  UNAMBIGUOUS covers three different situations and only ONE is evidence about  │
│    the numeric values beside it. An ISO date is beyond doubt in itself and      │
│    rescues none of its neighbours, because it is a DIFFERENT FORMAT rather      │
│    than evidence about theirs. So `is_informative` is defined as               │
│    `implied_order is not None`, not as "unambiguous" -- an earlier version      │
│    conflated them and the two properties disagreed.                            │
│                                                                              │
│  ambiguity_probability()  144/365 = 0.3945, computed not hard-coded. Ambiguity │
│    needs only day <= 12 because month <= 12 ALWAYS is. The usual shortcut       │
│    12/30.4 = 0.3947 lands nearby by the wrong route and is wrong for any span   │
│    that is not a whole year.                                                   │
│                                                                              │
│  A two-digit year IS resolved to the 2000s; date order is NOT. Both are        │
│    guesses; the year is a BOUNDED one, since nobody submits an initiative       │
│    dated 1926. Date order has no such escape.                                  │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                             columns.py                                        │
│                                                                              │
│  infer_order() -- five outcomes, and two of them are NOT degrees of one thing  │
│                                                                              │
│    day_first / month_first   established, and the proving rows are NAMED       │
│    unknowable                nothing disambiguates, so both orders fit every   │
│                                row. One answer exists; we cannot tell which     │
│    conflicting               different rows prove DIFFERENT orders, so NO      │
│                                single answer exists. Worse, and it gates        │
│    empty                     nothing parseable to reason from                  │
│                                                                              │
│  Evidence is ONLY an unambiguous NUMERIC value. ISO and textual dates are      │
│    excluded despite parsing cleanly; IDENTICAL values are excluded because     │
│    they read the same either way.                                             │
│                                                                              │
│  resolve()  returns None for EVERY row when the order is not established,     │
│    rather than falling back to a default. A default here is the entire         │
│    failure being examined: it would turn a column that establishes nothing     │
│    into a full set of plausible dates.                                        │
│                                                                              │
│  rows_at_risk()             excludes IDENTICAL -- no choice changes them       │
│  worst_divergence()         how far apart the two readings get. 294 days on    │
│                               this corpus, which moves a fiscal year           │
│  crossing_period_boundary() the subset where the ambiguity actually MOVES a    │
│                               reported figure. Two readings inside one quarter  │
│                               are ambiguous and harmless for a quarterly roll- │
│                               up, which is what these reports mostly are        │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│              fields.py                          intake.py                     │
│                                                                              │
│  FIVE outcomes. Two splits carry      load_submissions()  a row missing its    │
│  the weight:                            IDENTITY is reported; a row with an    │
│                                         unreadable VALUE still loads, because  │
│    typed       clean                    classifying values IS the job          │
│    coerced     parsed after norm-                                             │
│                  alisation, SAME      exact_duplicates()  same team, same      │
│                  unit. The note         folded title. REPORTED, not collapsed: │
│                  records what was       which copy survives is the owner's     │
│                  interpreted            judgement.                             │
│    wrong_unit  parses PERFECTLY and                                           │
│                  cannot be summed      EXACT KEYS ONLY, deliberately. Fuzzy    │
│                  with the column.        matching over near-identical titles   │
│                  Invisible to any        is Kinship's job -- it does        │
│                  parse-failure check     blocking, similarity and clustering,  │
│    free_text   prose                     and shows single linkage chains       │
│    empty       nothing claimed, so       transitively below threshold 1.0.     │
│                  nothing lost            A weaker copy here would duplicate.   │
│                                                                              │
│  A RANGE is not coerced to its         slice_by()  a row missing the field     │
│    midpoint: the midpoint is a           lands under an explicit empty key     │
│    number nobody wrote.                  rather than being DROPPED, because    │
│  A HEDGE is not a figure: averaging      dropping rows during a data-quality   │
│    it asserts precision the              check is how the check lies.          │
│    submitter declined to claim.                                               │
│                                                                              │
│  coverage()  the total and the coverage are returned TOGETHER and cannot be   │
│    separated. `total` is None when nothing parsed -- zero would be a claim     │
│    ("these initiatives saved nothing") where nobody could tell.                │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                             measure.py                                        │
│                                                                              │
│  Two behaviours with OPPOSITE failure signatures:                             │
│                                                                              │
│                        fixed-locale parser        inference                    │
│    cannot tell         answers anyway             refuses                      │
│    trace left          NONE                       a counted refusal            │
│    this corpus         39 silently wrong          0 wrong, 48 refused          │
│                                                                              │
│  Refusals are NOT a cost paid for accuracy. They are the same uncertainty,     │
│    made visible.                                                              │
│                                                                              │
│  looks_healthy  zero parse failures AND nonzero wrong dates -- nothing in the │
│    signature would prompt anyone to look. Counted PER COLUMN, because the      │
│    totals destroy the property: summing across teams lets one team's LOUD      │
│    failures mask another team's SILENT ones, and the team owning a silent      │
│    slice sees only their own numbers. An earlier version computed this from     │
│    the rollup sums and reported False for a corpus with two qualifying columns. │
│                                                                              │
│  worse_fixed_choice  ranks on silent errors, THEN on parse failures. The       │
│    second key matters: on a tie the two orders differ only in loudness, and    │
│    keying on silent errors alone made max() return whichever came first --      │
│    hiding a locale mismatch visible as a parse failure under the other order.   │
│                                                                              │
│  rollup()  uses the WORSE locale per column, not the average. Averaging would  │
│    report the performance of a parser nobody runs; a team picks one and lives   │
│    with it.                                                                   │
│                                                                              │
│  A row with no known truth is scored NOWHERE. Counting it correct would        │
│    flatter whichever parser answered more often.                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## The Findings

### One: the arithmetic, verified three ways

```
exact, 2026 (365 days)   144/365 = 0.3945
exact, 2024 (366 days)   144/366 = 0.3934
the usual shortcut       12/30.4 = 0.3947   <- right number, wrong reason
brute force over all 365 days: 0.3945   simulated over 200k draws: 0.3919
```

```
  rows   closed form   simulated    knowable
     1     39.4521%    39.4600%    60.5479%
     3      6.1406%     6.3150%    93.8594%
     5      0.9558%     0.9350%    99.0442%
    12      0.0014%     0.0000%    99.9986%
```

**Five rows for 99% confidence, eight for 99.9%.**

### Two: and the assumption behind it fails in the direction that hurts

```
submission pattern               12-row columns unknowable
days 1-12  (monthly deadline)                      100.0%
days 1-5   (tight deadline)                        100.0%
days 13-28 (mid-month)                               0.0%
days 1-28  (no pattern)                              0.0%
```

**Knowability is set by WHEN people submit, not by how much data you have.** A team with 500 rows and
a month-end deadline is worse off than a team with 6 rows spread through the month. Quadrupling the
rows on the deadline pattern changes nothing, and a test asserts it.

### Three: the silent slice hides inside a loud aggregate

```
team           rows        order  inform  ambig  fixWrong  fixFail  looksOK  refused
engineering      10  conflicting       4      6         6        2    False       10
facilities        4   unknowable       0      0         0        0    False        4
finance          14  month_first       9      4         4        9    False        0
logistics        15    day_first      10      4         4       10    False        0
marketing         4        empty       0      0         0        4    False        4
procurement      14   unknowable       0      6         6        0     True       14
quality          16   unknowable       0     16        16        0     True       16
support          12    day_first       9      3         3        9    False        0
```

Fleet-wide: 34 parse failures, so someone would investigate. `quality` and `procurement`: **zero
failures in their own slices** and a wrong date in every ambiguous row.

### Four: unambiguous is not informative

A column of two clean ISO dates and two ambiguous numeric ones is still **unknowable**. The ISO values
rescue none of their neighbours. Demonstrated in the corpus by a team holding eight clean ISO and
textual dates whose numeric rows stay unresolvable.

---

## Data Flow

```
1.  doubletake report --sliced
2.  load 89 submissions, 85 known true dates
      1 row missing its identity      -> reported, and it fails `check`
3.  read the date column under BOTH orders, classify each value
      every Ambiguity value occurs, so no corpus assertion is vacuous
4.  infer the whole-dataset order  -> CONFLICTING (teams write different formats)
      33 informative, 39 ambiguous, 6 identical, 4 unparseable
      39 rows at risk, 30 of which cross a quarter boundary
      worst divergence 294 days -- enough to move a fiscal year
5.  read the amount column         -> total 4,412,700 at 92.1% coverage
      typed 76, coerced 6, wrong_unit 1, free_text 5, empty 1
      the total NEVER appears without the coverage
6.  slice by team -> 8 slices, and every ColumnOrder value occurs including EMPTY
      3 established, 3 unknowable, 1 conflicting, 1 empty
7.  score fixed-locale vs inference against the known dates
      fixed:     39 silently wrong, 34 parse failures, 2 slices look healthy
      inference:  0 wrong,          48 refused
8.  exit 1: not aggregatable, so a total needs a caveat travelling with it
```

---

## Testing

```bash
pytest                          # 111 tests, ~0.2s
pytest --cov=initiative_hub --cov-report=term-missing
```

| File | Covers |
|:--|:--|
| `conftest.py` | corpus as session fixtures; `dated()` builds submissions differing only in their date string |
| `test_dates_fields_measure.py` | all four `Ambiguity` values; separators; two-digit years; **ISO and textual dates unambiguous AND uninformative**; the probability arithmetic against closed form, brute force and simulation; **the deadline case at any size**; all five `ColumnOrder` outcomes; `resolve` refusing; six coercion cases; the percentage excluded from total and coverage; **the `SCALES` order proven meaningless**; `looks_healthy` per column; every `None` case; corpus properties |
| `integration/test_cli.py` | all eight commands, every exit-code reason, JSON and CSV contents |

The property tests run against `data/`, so editing the corpus and breaking an invariant fails the
build. Two exist to stop the others being vacuous: **every `Ambiguity` value and every `ColumnOrder`
value must occur.** Nothing is mocked, because nothing talks to a network.

---

## Configuration

`IH_`-prefixed environment variables or a `.env` file. No list-typed fields, since Pydantic Settings
parses those as JSON and `IH_X=a,b` would fail with a decode error reading like a bug in the tool.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IH_", env_file=".env", extra="ignore")

    submissions: Path = Path("data/submissions.jsonl")
    truth: Path = Path("data/truth.jsonl")
    date_field: str = "target_date"
    amount_field: str = "annual_saving"
    slice_by: str = "team"
    confidence: float = Field(default=0.99, gt=0.0, lt=1.0)
```

`confidence` is bounded strictly inside 0 and 1 because both endpoints are degenerate: 0 needs no rows,
and 1 is unreachable since no finite number of ambiguous dates guarantees a disambiguating one.
