# Doubletake

Is `03/04/2026` the 3rd of April or the 4th of March? A validator with a fixed locale picks one
and moves on, and nothing records that a choice was made. Doubletake validates initiative
submissions by treating values like that as what they are: readable two ways. It refuses to
guess, works out which columns their other rows can resolve, and shows that whether a column is
resolvable depends on *when* people submit, not on how many rows there are.

The hard part is calendar arithmetic, plus one policy decision: what to do with a value that
cannot be resolved.

---

## The Asymmetry This Is Built On

**A value that fails to parse is safe.** It is loud — someone sees the error and fixes the row.

**A value that parses two ways is dangerous.** It is silent. It lands in the database, gets
aggregated, and nothing ever records which reading was taken.

`03/04/2026` is 3 April or 4 March. `13/04/2026` is unambiguous, because there is no thirteenth month.
So **ambiguity is a property of the value, not of the format** — and a parser with a fixed locale is
right on the obvious values and silently wrong on exactly the ones where order matters.

`dateutil`'s `dayfirst` flag *is* that failure mode. It is not a dependency, and `setup.sh` asserts
its absence.

---

## The Headline: Knowability Is Set by Submission Timing, Not Data Volume

Ambiguity needs only `day ≤ 12`, because `month ≤ 12` always is. So `p = 144/365 = 0.3945`, and a
column of `n` dates carries no disambiguating value with probability `p**n`:

```
  rows   closed form   simulated    knowable
     1     39.4521%    39.4600%    60.5479%
     3      6.1406%     6.3150%    93.8594%
     5      0.9558%     0.9350%    99.0442%
    12      0.0014%     0.0000%    99.9986%
```

Five rows for 99% confidence. Which suggests "small columns are risky" — **and that reading is wrong,
because the assumption is uniformity:**

| submission pattern | 12-row columns unknowable |
|:--|--:|
| days 1–12 (monthly deadline) | **100.0%** |
| days 1–5 (tight deadline) | **100.0%** |
| days 13–28 (mid-month) | 0.0% |
| days 1–28 (no pattern) | 0.0% |

A monthly reporting deadline puts every submission in days 1–12, so every value is ambiguous and
**every column is unknowable regardless of row count.** Quadrupling the rows changes nothing, and a
test asserts it.

**A team with 500 rows and a month-end deadline is worse off than a team with 6 rows spread through
the month.** The probability table ships with that caveat attached rather than as reassurance.

---

## Two Behaviours, Opposite Failure Signatures

| | fixed-locale parser | inference |
|:--|:--|:--|
| when it cannot tell | answers anyway | refuses |
| trace left behind | **none** | a counted refusal |
| on this corpus | **39 silently wrong** | **0 wrong, 48 refused** |

Both read the unambiguous values correctly, and neither can invent information a column does not
contain. The difference is what they do when they cannot tell.

**Refusals are not a cost paid for accuracy. They are the same uncertainty, made visible.**

### And the dangerous signature is per team, not fleet-wide

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

The fleet-wide view shows **34 parse failures**, so someone would investigate. But `quality` and
`procurement` see **zero failures in their own slices** and a wrong date in every ambiguous row.

An earlier version computed this from the rollup sums and reported `False` for a corpus containing two
columns that plainly qualify — because summing across teams lets one team's loud failures mask
another's silent ones.

---

## Five Column Outcomes, and Two Are Not Degrees of One Thing

| value | meaning |
|:--|:--|
| `day_first` / `month_first` | established, and the proving rows are named |
| `unknowable` | nothing disambiguates, so both orders fit every row |
| `conflicting` | different rows prove **different** orders, so no single order fits |
| `empty` | nothing parseable to reason from |

`unknowable` means one answer exists and we cannot tell which. `conflicting` means **no answer
exists**. Only the second fails `check`.

`resolve()` returns `None` for every row when the order is not established, rather than defaulting. A
default is the entire failure being examined.

---

## Unambiguous Is Not the Same as Informative

```
value           ambiguity     informative  implies order
13/04/2026      unambiguous   True         day_first
04/13/2026      unambiguous   True         month_first
03/04/2026      ambiguous     False        -
05/05/2026      identical     False        -
2026-04-03      unambiguous   False        -
04 Mar 2026     unambiguous   False        -
```

An ISO date is beyond doubt in itself and **rescues none of its neighbours**, because it is a
different format rather than evidence about theirs. A column of two clean ISO dates and two ambiguous
numeric ones is still unknowable.

**I got this wrong first time.** Probing the parser showed `read("2026-04-03")` returning
`is_informative=True` with `implied_order=None` — two properties that disagreed. A comment in
`dates.py` had also claimed one `04 Mar 2026` *"establishes the order for all of them"*, which is
false. The corpus now contains a team that stays unknowable while holding eight clean ISO and textual
dates.

`05/05/2026` gets its own value — `identical` — because it is ambiguous in form, harmless in effect,
and carries no order information. Three properties a boolean cannot hold.

---

## A Bug: The Total That Summed a Percentage With Currency

An earlier version coerced `15%` to `15.0`, wrote *"this is a percentage"* into the note field, and
**then summed it with currency anyway**:

```
$12,000 + $12,000 + 15%  =  $24,015   coverage 100%   complete=True
```

I flagged the problem in a note and then ignored my own flag. `WRONG_UNIT` now exists for it:

```
$12,000 + $12,000 + 15%  =  $24,000   coverage  67%   complete=False
```

It is the interesting case because it **parses perfectly** — invisible to any check that looks for
parse failures. Detection is not general, and the limit is stated: only the percent sign is caught,
because it is punctuation rather than a word. `3 hours/week` lands in `FREE_TEXT`, and dollars mixed
with euros is not detected at all.

---

## Running

```bash
bash setup.sh                  # derives the arithmetic against simulation, and what breaks it
source .venv/bin/activate

doubletake doctor
doubletake check                    # exits 1: the column conflicts, and a row is unreadable
doubletake report --sliced          # per-team knowability
doubletake audit                    # fixed-locale parser vs inference
doubletake rows-needed              # the arithmetic, and the assumption that breaks it
doubletake fields                   # how each messy amount was interpreted
doubletake explain 03/04/2026       # one value, both readings
doubletake explain 2026-04-03       # unambiguous, and not evidence
```

| Command | Purpose | Exit |
|:--|:--|:--|
| `check` | What the dataset establishes about its own format | **1** on a `conflicting` column or unreadable row |
| `report` | Full intake report, `--sliced` for per-team | **1** if not aggregatable |
| `audit` | Fixed-locale parser vs inference against known dates | **1** only if *inference* is wrong |
| `rows-needed` | The probability arithmetic | 0 |
| `explain` | One value, read both ways | 0 |
| `fields` / `dates` | Every non-clean value, with its interpretation | 0 |

`check` does **not** fail on an `unknowable` column. That is a property of what submitters typed, and
a gate that fires whenever a team submits on the first of the month gets switched off in a week.

`audit` does **not** fail because the fixed baseline is bad. That is the finding, not a regression.

---

## Testing

```bash
pytest                          # 111 tests, ~0.2s
pytest --cov=initiative_hub --cov-report=term-missing
```

The property tests run against `data/`, so editing the corpus and breaking an invariant fails the
build. Two exist to stop the others being vacuous: **every `Ambiguity` value and every `ColumnOrder`
value must occur.** Nothing is mocked, because nothing talks to a network.

One test reverses the `SCALES` tuple and asserts identical results, because a comment claimed an
ordering that does nothing.

---

## Scope: What This Deliberately Does Not Do

**Fuzzy dedup.** Kinship, a sibling project, does blocking, similarity and clustering, and
demonstrates that single linkage chains transitively across any threshold below 1.0. Only exact-key
dedup lives here — same team, same folded title — and duplicates are **reported rather than
collapsed**, because which copy survives is the owner's judgement.

**Streamlit, embedded charts and PDF export**, all of which the project brief asks for. Plumbline already
carries `render/chart.py`, and charts are the one thing CI cannot assert on. A text table
asserts in CI; a chart does not.

The project brief's verdict was *"Pydantic validation + dedup"*, which is the least novel line in the planning notes. A survey of the sibling projects found that dedup, timelines, status tracking and SQLite were all already
built — and that **nothing anywhere handled locale-ambiguous dates** (`dayfirst`/`monthfirst`: zero
hits; AsOf uses `date.fromisoformat`, unambiguous by construction). That gap is the whole
project, and the rest is scoped out with reasons.

---

## Tradeoffs and Decisions

**A range is not coerced to its midpoint.** The midpoint is a number the submitter did not write.
The cost is real and worth naming: someone who honestly wrote `5000-8000` contributes nothing to the
total, while someone who guessed `6500` contributes fully. **The honest answer is penalised.**

**A hedged figure is not a figure.** `approx 12000` averaged with unhedged values asserts a precision
the submitter explicitly declined to claim. The hedge list is a hand-written English word list and
there is no measurement of what it misses.

**Two-digit years are resolved to the 2000s; date order is not.** Both are guesses. The year is a
*bounded* guess — nobody submits an initiative dated 1926 — and date order has no such escape. That
is a judgement, not a proof.

**A total never appears without its coverage,** and `total` is `None` rather than `0.0` when nothing
parsed. Zero would be a claim.

**`rollup` uses the worse locale per column, not the average.** Averaging reports the performance of a
parser nobody runs; a team picks one order and lives with it.

**`worse_fixed_choice` breaks ties on parse failures.** When both orders give zero silent errors they
differ only in loudness, and keying on silent errors alone made `max` return whichever came first —
hiding a locale mismatch that was visible as a parse failure under the other order.

**Inference refuses on 48 of 89 rows here.** More than half. A consumer who needs a date for every row
gains nothing from being told the truth is unavailable, and that is the honest cost.

**Nothing here fixes an unknowable column.** The tool reports it; the fixes are upstream — an ISO date
picker, or a submission window that is not the first of the month.

**Quarter-crossing is hardcoded to calendar quarters.** An organisation on a non-calendar fiscal year
gets a number that does not match its reports.

---

## Configuration

```bash
IH_SUBMISSIONS=data/submissions.jsonl
IH_TRUTH=data/truth.jsonl
IH_DATE_FIELD=target_date
IH_AMOUNT_FIELD=annual_saving
IH_SLICE_BY=team
IH_CONFIDENCE=0.99
```

`confidence` is bounded strictly inside 0 and 1: 0 needs no rows, and 1 is unreachable because no
finite number of ambiguous dates guarantees a disambiguating one. No list-typed fields, since Pydantic
Settings parses those as JSON.

---

## Stack

| Concern | Choice |
|:--|:--|
| Language | Python 3.11+ |
| Models + config | Pydantic, Pydantic Settings |
| CLI | Click |
| Date parsing | `datetime.date` and hand-written regex — **not** `dateutil` |
| Output | text, JSON, CSV |
| Tests | pytest, Click's CliRunner |
| Lint | ruff |
| Models/AI | none, deliberately |

The corpus is synthetic and reproducible via `data/generate.py`: 89 submissions across 8 teams, 85
with a known true date. **The ambiguity is a consequence of how values are written, not planted row by
row** — each team writes its own format, and whether a column turns out knowable falls out of which
dates happen to carry a day above 12. Planted: a day-first team, a month-first team, a monthly-deadline
team that is unknowable at any size, a team mixing both formats, a team whose ISO dates rescue nothing,
a team whose dates all have day equal to month, messy amounts, an exact duplicate, and four unparseable
dates.
