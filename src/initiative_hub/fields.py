"""Typing a submitted field, and distinguishing prose from a number nobody formatted.

The brief names "users entering free-text in structured fields". The failure is not that the value
cannot be read -- that part is obvious. It is that an aggregate over the column **still computes**,
over whatever subset happened to parse, and then gets reported as though it covered the column.

Five outcomes. Two of the splits carry the weight:

  * COERCED vs TYPED -- a coercion is an interpretation someone should be able to object to, so it
    is recorded rather than blended into a clean parse.
  * WRONG_UNIT vs COERCED -- a `15%` in a savings column parses perfectly and cannot be summed with
    a currency amount, so it is invisible to any check that only looks for parse failures.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import AggregateCoverage, FieldKind, FieldVerdict

CURRENCY = "$£€¥₹"

# Suffixes that scale a number.
#
# An earlier comment here claimed the tuple was "ordered longest-first so 'mm' is not read as 'm'".
# That was false and reversing the tuple proves it -- results are identical, because `1.5bn` ends
# with `n` rather than `b`, so `bn` and `b` never collide. `12mm` is rejected not by ordering but
# because stripping one `m` leaves `12m`, which is still not numeric. The order carries no meaning.
SCALES: tuple[tuple[str, float], ...] = (
    ("bn", 1_000_000_000.0),
    ("k", 1_000.0),
    ("m", 1_000_000.0),
    ("b", 1_000_000_000.0),
)

# Words that signal the submitter was estimating rather than reporting. Their presence is the point:
# a hedged number is not a number, and averaging it with unhedged ones asserts a precision the
# submitter explicitly declined to claim.
HEDGES = frozenset({
    "approx", "approximately", "about", "around", "circa", "maybe", "perhaps", "roughly",
    "estimate", "estimated", "guess", "tbd", "tbc", "unknown", "unsure", "varies", "various",
    "n/a", "na", "none", "nil", "pending", "ongoing", "ish",
})

NUMERIC_CORE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
# The en and em dashes are deliberate: people genuinely type "5000–8000" with one, and
# rejecting those would push a real range into FREE_TEXT for the wrong reason.
RANGE = re.compile(
    r"^\s*([\d.,]+)\s*(?:-|to|–|—)\s*([\d.,]+)\s*$", re.IGNORECASE
)


def _strip_scale(text: str) -> tuple[str, float]:
    lowered = text.lower()
    for suffix, factor in SCALES:
        if lowered.endswith(suffix):
            return text[: -len(suffix)].strip(), factor
    return text, 1.0


def read_number(field: str, raw: str) -> FieldVerdict:
    """Read a field declared as numeric.

    Coercion covers what people actually type into a savings box: currency symbols, thousands
    separators, a `k` or `m` suffix, a trailing `/yr`, surrounding whitespace. Each is recorded in
    `note` so the interpretation is visible rather than implied.

    A RANGE (`5000-8000`) is deliberately NOT coerced to its midpoint. The midpoint is a number the
    submitter did not write, and inventing it converts an honest range into a false precision that
    every downstream sum then treats as fact.
    """
    text = raw.strip()
    if not text:
        return FieldVerdict(field=field, raw=raw, kind=FieldKind.EMPTY)

    lowered = text.lower()
    stripped_words = re.sub(r"[^a-z/]", " ", lowered).split()
    if any(word in HEDGES for word in stripped_words):
        return FieldVerdict(
            field=field, raw=raw, kind=FieldKind.FREE_TEXT,
            note="hedged: the submitter declined to claim a precise figure",
        )

    if NUMERIC_CORE.match(text):
        return FieldVerdict(field=field, raw=raw, kind=FieldKind.TYPED, value=float(text))

    ranged = RANGE.match(text)
    if ranged:
        return FieldVerdict(
            field=field, raw=raw, kind=FieldKind.FREE_TEXT,
            note="a range, not a value -- its midpoint is a number nobody wrote",
        )

    working = text
    notes: list[str] = []

    if any(symbol in working for symbol in CURRENCY):
        working = "".join(character for character in working if character not in CURRENCY)
        notes.append("dropped a currency symbol")

    for trailing in ("/yr", "/year", "per year", "pa", "p.a.", "annually"):
        if working.lower().endswith(trailing):
            working = working[: -len(trailing)].strip()
            notes.append(f"dropped {trailing!r}")
            break

    if "%" in working:
        # A percentage is a well-formed number in the WRONG UNIT, which is a category error rather
        # than a formatting one. Coercing it and then summing it with currency amounts produces a
        # figure that means nothing -- and an earlier version of this did exactly that, noting the
        # problem in `note` and then counting the value as aggregatable anyway.
        candidate = working.replace("%", "").strip()
        if NUMERIC_CORE.match(candidate):
            return FieldVerdict(
                field=field, raw=raw, kind=FieldKind.WRONG_UNIT, value=float(candidate),
                note="a percentage, not an amount -- parses cleanly and is not summable with one",
            )

    if "," in working:
        working = working.replace(",", "")
        notes.append("removed thousands separators")

    working, factor = _strip_scale(working.strip())
    if factor != 1.0:
        notes.append(f"applied a x{factor:,.0f} suffix")

    working = working.strip()
    if NUMERIC_CORE.match(working):
        return FieldVerdict(
            field=field, raw=raw, kind=FieldKind.COERCED,
            value=float(working) * factor, note="; ".join(notes) or None,
        )

    return FieldVerdict(
        field=field, raw=raw, kind=FieldKind.FREE_TEXT,
        note="prose in a field declared numeric",
    )


def read_column(field: str, values: Sequence[str]) -> list[FieldVerdict]:
    return [read_number(field, value) for value in values]


def coverage(field: str, verdicts: Sequence[FieldVerdict]) -> AggregateCoverage:
    """Sum a column, and report what fraction of it the sum actually covered.

    The total and the coverage are returned together and cannot be separated, which is the point: a
    sum over a column that skipped a third of its rows is not wrong, it is unlabelled, and the label
    is the only thing standing between it and a leadership slide.

    `total` is None when nothing parsed. Zero would be a claim -- "these initiatives saved nothing" --
    where the truth is that nobody could tell.
    """
    usable = [item for item in verdicts if item.aggregatable and item.value is not None]
    return AggregateCoverage(
        field=field,
        rows=len(verdicts),
        included=len(usable),
        total=sum(item.value for item in usable) if usable else None,
    )


def kind_counts(verdicts: Sequence[FieldVerdict]) -> dict[FieldKind, int]:
    """Counts per outcome, including the zeros.

    Zeros are kept so "considered and found none" reads differently from "not examined".
    """
    counts = dict.fromkeys(FieldKind, 0)
    for item in verdicts:
        counts[item.kind] += 1
    return counts
