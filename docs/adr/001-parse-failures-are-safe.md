# ADR 001: A value that fails to parse is safe; a value that parses two ways is not

## Status

Accepted

## Context

The project brief names its own worst input:

> *"Users entering free-text in structured fields, dates in wrong format, duplicate submissions."*

The middle one is not a parsing problem in the ordinary sense. `03/04/2026` is 3 April or 4 March,
and **nothing in the value says which.** `13/04/2026` is unambiguous, because there is no thirteenth
month. So:

- **Ambiguity is a property of the VALUE, not of the format.** A parser with a fixed locale is right
  on the obvious values and silently wrong on exactly the ones where order matters.
- A value that *fails* to parse is **safe**, because it is loud: someone sees the error and fixes the
  row.
- A value that parses *two ways* is **dangerous**, because it is silent: it lands in the database,
  gets aggregated, and nothing ever records which reading was taken.

`dateutil`'s `dayfirst` flag is precisely the behaviour under examination. Set it once, globally, and
every ambiguous value is resolved in the direction you guessed. It cannot also be the implementation,
so it is not a dependency and `setup.sh` asserts its absence.

## Decision

**`Ambiguity` has four values, and the two middle ones exist because a boolean cannot hold them.**

| value | meaning |
|:--|:--|
| `UNAMBIGUOUS` | cannot be misread |
| `AMBIGUOUS` | both orders give real dates, and they **differ** |
| `IDENTICAL` | both orders give the **same** date, because day equals month |
| `UNPARSEABLE` | neither order works — **the safe failure** |

`IDENTICAL` earns its place by sitting on both sides of the obvious classification. `05/05/2026` is
ambiguous in form and harmless in effect, and it carries **no information** about the column's order.
Calling it unambiguous would let it look like evidence; calling it ambiguous would flag a value that
cannot be wrong.

### Unambiguous is not the same as informative

Three quite different situations land in `UNAMBIGUOUS`: a numeric date with a day above 12, an ISO
date, and a spelled-out month. **Only the first tells you anything about the numeric values beside
it.** An ISO date in a column of `03/04/2026` is beyond doubt in itself and rescues none of its
neighbours, because it is a different format rather than evidence about theirs.

So `is_informative` is defined as `implied_order is not None`, not as "unambiguous".

**An earlier version conflated the two**, and probing the parser is what exposed it: `read("2026-04-03")`
returned `is_informative=True` with `implied_order=None` — two properties that disagreed. A comment
in `dates.py` had also claimed that one `04 Mar 2026` "establishes the order for all of them", which
is false. Both are corrected, and the corpus now contains a team whose column stays unknowable
despite holding **eight** perfectly clean ISO and textual dates.

### The arithmetic is exact and the shortcut is named

Ambiguity requires only `day ≤ 12`, because `month ≤ 12` is always true. So:

```
exact, 2026 (365 days)   144/365 = 0.3945
exact, 2024 (366 days)   144/366 = 0.3934
the usual shortcut       12/30.4 = 0.3947   <- right number, wrong reason
```

The shortcut treats it as a per-month day fraction. It lands within 0.0002 for a whole year and is
wrong for any other span, so `ambiguity_probability` computes the exact form. Cross-checked three
ways — closed form, brute force over all 365 days, and 200,000 simulated draws.

### Free text in a structured field, and one case worse than free text

`FieldKind` has five values. Two of the splits carry weight:

- **`COERCED` vs `TYPED`** — a coercion is an interpretation someone should be able to object to, so
  what was interpreted is recorded in `note`. A currency symbol dropped, a `k` suffix applied, a
  `/yr` removed.
- **`WRONG_UNIT` vs `COERCED`** — a `15%` in a savings column **parses perfectly** and cannot be
  summed with a currency amount. It is invisible to any check that looks for parse failures.

That last one was a real bug in this codebase. An earlier version coerced `15%` to `15.0`, wrote
*"this is a percentage"* into the note field, and **then summed it with currency anyway** — turning
two $12,000 rows into **$24,015 at 100% "complete" coverage**. I flagged the problem in a note and
then ignored my own flag. It now reports 67% coverage, so the exclusion is visible.

A **range** (`5000-8000`) is deliberately not coerced to its midpoint: the midpoint is a number the
submitter did not write, and inventing it converts an honest range into false precision. A **hedged**
figure (`approx 12000`, `TBD`) is not a figure — averaging it with unhedged ones asserts a precision
the submitter explicitly declined to claim.

Every total is reported with its **coverage**, and `total` is `None` rather than `0.0` when nothing
parsed. Zero would be a claim — *"these initiatives saved nothing"* — where the truth is that nobody
could tell.

### Two-digit years are resolved, and date order is not

`26` becomes 2026. That is a guess, and it is a *bounded* one: nobody submits a continuous-improvement
initiative dated 1926, so the wrong reading is implausible rather than merely unlucky. Date order has
no such escape, which is why one gets resolved and the other does not.

### What is deliberately not built

**Fuzzy dedup.** Kinship, a sibling project, does blocking, similarity and clustering, and
demonstrates that single linkage chains transitively across any threshold below 1.0. Building a
weaker version here would be duplication. Only exact-key dedup lives here — same team, same folded
title — and duplicates are **reported rather than collapsed**, because which copy survives is the
owner's judgement.

**Streamlit, embedded charts and PDF export**, all of which the project brief asks for. Plumbline already
carries `render/chart.py`, and charts are the one thing CI cannot assert on. A text table
asserts in CI; a chart does not.

## Validation

- `TestAmbiguityIsAPropertyOfTheValue` — all four values; separators; two-digit years; ISO and
  textual dates unambiguous **and uninformative**
- `TestTheProbabilityArithmetic` — 144/365 against closed form, brute force and simulation; leap
  years; geometric falloff; monotonic row counts
- `TestFieldTyping` — six coercion cases each recording a note; the percentage excluded from both
  total and coverage; ranges and hedges refused; `total is None` when nothing parsed
- `TestFieldTyping::test_the_scale_suffix_order_carries_no_meaning` — reverses `SCALES` and asserts
  identical results, because a comment claimed an ordering that does nothing
- `TestPropertiesOverTheShippedCorpus::test_every_ambiguity_value_occurs` — so nothing above is
  vacuous
- `setup.sh` and CI re-derive the whole chain, and assert `dateutil` is absent

## Consequences

**Good**

- The dangerous case has a name, a count, and a divergence in days
- A percentage can no longer poison a currency total, and its exclusion shows up as reduced coverage
- Every coercion is inspectable, so a disputed figure can be traced to the interpretation that made it
- The arithmetic is published rather than asserted, and the usual shortcut is named as a shortcut

**Bad**

- `WRONG_UNIT` detection is **not general**. Only the percent sign is caught, because it is
  punctuation rather than a word. `3 hours/week` lands in `FREE_TEXT`, and a column mixing dollars
  with euros is not detected at all. One unit error is caught; unit checking is not solved
- Five `FieldKind` values and four `Ambiguity` values is a lot of vocabulary for what a submitter
  thinks of as "a number" and "a date"
- Refusing to coerce a range means a submitter who honestly wrote `5000-8000` contributes nothing to
  the total, while one who guessed `6500` contributes fully. The honest answer is penalised
- The hedge list is a hand-written English word list. It will miss hedges in other languages and in
  phrasings nobody thought of, and there is no measurement of what it misses
- Two-digit year expansion to the 2000s is a guess that this project otherwise refuses to make. The
  justification is that it is bounded, which is a judgement rather than a proof

**Neutral**

- `IDENTICAL` values are excluded from `at_risk` — both orders agree, so no inference changes them
- `EMPTY` is distinct from `FREE_TEXT`: nothing was claimed, so nothing was lost
- `LabelMap`-style problem collection is used for loading: a row missing its identity is reported,
  while a row with an unreadable *value* still loads, because classifying values is the whole job
