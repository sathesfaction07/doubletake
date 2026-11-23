# ADR 002: Whether a column's date order is knowable is set by submission timing, not data volume

## Status

Accepted

## Context

A single ambiguous date tells you nothing. A *column* of them can still tell you everything, provided
at least one row happens to carry a day above 12 — and whether one does is luck rather than design.

That turns "which order is this column in?" into a question with three genuinely different answers,
and the obvious implementation collapses all three into a guess.

## The arithmetic

Ambiguity needs only `day ≤ 12`, so `p = 144/365 = 0.3945`. A column of `n` uniform dates contains no
disambiguating value with probability `p**n`:

```
  rows   closed form   simulated    knowable
     1     39.4521%    39.4600%    60.5479%
     2     15.5646%    15.2700%    84.4354%
     3      6.1406%     6.3150%    93.8594%
     5      0.9558%     0.9350%    99.0442%
     8      0.0587%     0.0350%    99.9413%
    12      0.0014%     0.0000%    99.9986%
```

**Five rows for 99% confidence, eight for 99.9%.** So the naive reading is "small columns are risky",
and slicing a report by team or by quarter is exactly how small columns get made.

### And that reading is wrong, because the assumption is wrong

The load-bearing assumption is **uniformity**, and it fails in the direction that hurts:

```
submission pattern               12-row columns unknowable
days 1-12  (monthly deadline)                      100.0%
days 1-5   (tight deadline)                        100.0%
days 13-28 (mid-month)                               0.0%
days 1-28  (no pattern)                              0.0%
```

A monthly reporting deadline puts every submission in days 1–12. Every value is then ambiguous, and
**every column is unknowable regardless of row count** — quadrupling the rows changes nothing, and a
test asserts that.

**So the risk is set by WHEN people submit, not by how much data you have.** A team with 500 rows and
a month-end deadline is worse off than a team with 6 rows spread through the month, which inverts the
intuition the probability table creates. The table ships with the caveat attached rather than as a
standalone reassurance.

## Decision

**`ColumnOrder` has five values, and `UNKNOWABLE` and `CONFLICTING` are not degrees of one thing.**

| value | meaning |
|:--|:--|
| `DAY_FIRST` / `MONTH_FIRST` | established, and the proving rows are named in `evidence` |
| `UNKNOWABLE` | nothing disambiguates, so both orders fit every row |
| `CONFLICTING` | different rows prove **different** orders, so no single order fits |
| `EMPTY` | nothing parseable to reason from |

`UNKNOWABLE` means one consistent answer exists and we cannot tell which. `CONFLICTING` means **no
consistent answer exists** — the column mixes formats and must not be parsed as a unit. The second is
worse, and it is the one that fails `check`.

`resolve()` returns `None` for every row when the order is not established, rather than falling back
to a default. A default here is the entire failure being examined: it would turn a column that
establishes nothing into a full set of plausible dates.

### The measurement: two behaviours with opposite failure signatures

Both approaches read the unambiguous values correctly, and neither can invent information a column
does not contain. The difference is **what they do when they cannot tell**.

| | fixed-locale parser | inference |
|:--|:--|:--|
| when it cannot tell | answers anyway | refuses |
| trace left behind | none | a counted refusal |
| corpus result | **39 silently wrong** | **0 wrong, 48 refused** |

Refusals are not a cost being paid for accuracy. They are **the same uncertainty, made visible.**

### The signature that matters is per column, not aggregate

`looks_healthy` is true when a column shows **zero parse failures and nonzero wrong dates** — nothing
in the failure signature would prompt anyone to look.

On the shipped corpus, two of eight teams qualify:

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

The fleet-wide view shows 34 parse failures, so someone *would* investigate. But `quality` and
`procurement` see **zero failures in their own slices** and wrong dates in every ambiguous row.

**An earlier version computed `looks_healthy` from the rollup sums** and reported `False` for a corpus
containing two columns that plainly qualify — because summing across teams lets one team's loud
failures mask another team's silent ones. It is now counted per column.

### A tie-break that was hiding a locale mismatch

`worse_fixed_choice` ranks on silent errors, then on parse failures. The second key was added after
probing: when both orders produce zero silent errors they differ only in *loudness*, and keying on
silent errors alone made `max` return whichever came first — so a locale mismatch visible as a parse
failure under the other order vanished from the report entirely.

### Slicing is a first-class concern

`report --sliced` exists because the whole-dataset verdict **does not transfer to the parts**. This
corpus is a sharp case: the combined column is `CONFLICTING` because different teams write different
formats, while three individual teams are `UNKNOWABLE` and three are cleanly established. Neither
figure describes the other.

## Validation

- `TestOrderInference` — one row establishing a column; none leaving it unknowable; rows proving both
  orders conflicting; nothing parseable as empty; ISO and `IDENTICAL` values excluded from evidence
- `TestOrderInference::test_resolve_refuses_rather_than_defaulting`
- `TestTheProbabilityArithmetic::test_deadline_clustering_defeats_the_arithmetic_entirely` — and
  doubling the rows does not help
- `TestMeasurement::test_looks_healthy_is_counted_per_column_not_from_totals` — asserts the aggregate
  has loud failures **and** one column is still silently wrong
- `TestMeasurement` — a fixed parser silently wrong with zero failures; loud on the other order;
  inference refusing; rows with no known truth scored nowhere
- `TestPropertiesOverTheShippedCorpus` — **every `ColumnOrder` value occurs across the slices**;
  inference is never wrong on a resolved row; the deadline team has 16 of 16 at risk; the ISO team
  stays unknowable despite 8 clean dates; the `IDENTICAL` team is unknowable **and harmless**
- `setup.sh` and CI re-derive the probability table against simulation and the deadline case at 100%

## Consequences

**Good**

- The three ways a column can fail to establish its order are distinguished, and only the one with no
  consistent answer fails the build
- The probability table ships with the assumption that breaks it, rather than as reassurance
- A silently-wrong slice is surfaced even though the aggregate looks investigable
- `report --sliced` makes the whole-versus-parts gap visible instead of leaving it implied

**Bad**

- Inference refuses on **48 of 89 rows** in this corpus. That is more than half, and a consumer who
  needs a date for every row gains nothing from being told the truth is unavailable
- `CONFLICTING` fails `check` while `UNKNOWABLE` does not, and the boundary is a judgement. A team
  could reasonably want the opposite: unknowable data is unusable, whereas a conflicting column might
  be split by submitter and salvaged
- Inference can still be wrong. It reads the order from evidence, so a column whose only
  disambiguating rows were typed in the minority format points the wrong way — and `inference_wrong`
  is zero on this corpus rather than provably zero in general
- Nothing here helps a column that is unknowable. The tool reports the problem and the only fixes are
  upstream: an ISO date picker, or a submission window that is not the first of the month
- `worst_divergence_days` is reported for the whole column, so one pathological row sets the headline
  number for rows that diverge by a day
- Quarter-crossing is hardcoded to calendar quarters. An organisation on a non-calendar fiscal year
  gets a number that does not match its reports

**Neutral**

- `audit` fails only when **inference** gets a resolved row wrong. The fixed baseline is wrong on many
  rows every run, and gating on that would make the check unconditional — a check that always fires
  carries no information. The fixed count is reported as a finding instead
- A bad known-dates file is checked **before** any conclusion is drawn from the scores, so an empty
  truth set cannot be reported as "inference was wrong"
- `rollup` uses the **worse** locale per column rather than averaging the two. Averaging would report
  the performance of a parser nobody runs; in practice a team picks one order and lives with it
