"""A per-column dim filter for the table view. View state, and nothing else.

v3.8. Puck asked for "a filter, per column, that greys out genes that don't match
instead of removing them". The intent is the honest one — the evidence stays on
screen, so a reader can see what the filter excluded and disagree with it — and
this module is that filter's decision procedure. It is deliberately *not* §3.2.

**What this is not.** `filters.py` implements PLAN §3.2: a threshold there
**removes** a gene from the plot and **names it in the drop ledger**, and that is
what makes `input == rendered + ledgered` still hold with a filter active. This
module removes nothing, ledgers nothing, recomputes nothing and writes nothing.
It classifies genes that are all still rendered. The two mechanisms are kept
apart on purpose: two controls that look alike and do different things is its own
hazard, so the §3.2 path is untouched by this file and nothing here is reachable
from a run.

The one primitive the two share is the comparison itself — `filters.OPS` — so
that `>= 3` cannot come to mean two different things in one app. Sharing the
operator is not routing §3.2 through this module: no function here is called on
the render path.

**Three states, not two** (PLAN §3.2 rule 1, applied to dimming). A gene with no
value on the filtered ring cannot be compared at all, so it is neither a match
nor an exclusion:

  * ``MATCH``       — the ring has a value for this gene and it satisfies the bound.
  * ``DIMMED``      — the ring has a value for this gene and it misses the bound.
  * ``UNEVALUABLE`` — the ring has no value for this gene, so there is no
    comparison to make. Folding this into ``DIMMED`` would make dimming
    de-emphasis-by-missingness, which systematically fades out the under-studied
    genes rather than the ones that missed the bound — `COUNCIL-v0-plan.md` M3's
    bias arriving through a view control. There is no opt-out toggle here, unlike
    §3.2's `drop_no_data`: nothing is being removed, so there is nothing to
    authorise.

Thresholds are compared against the **ring's own raw value** — the number in
`values.csv`, in the units the ring's label states — never a rank and never a
scaled colour position. Same rule as §3.2, and it must not diverge from it.

**No score.** PLAN §3 deleted ranking. The one way a filter can smuggle one back
in is a per-gene "matches N of M" count, which is a composite by another name, so
`classify` returns a *state* per gene and never a tally, and `DimOutcome` has no
per-gene number for anything to sort on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .filters import OPS, SYMBOL_TO_OP
from .states import Cell

# The three treatments. Names, not numbers: a state that sorted would be an
# ordering, and there is no ranking in this project.
MATCH = "match"
DIMMED = "dimmed"
UNEVALUABLE = "unevaluable"
DIM_STATES: tuple[str, ...] = (MATCH, DIMMED, UNEVALUABLE)

# What the count line above the grid calls each one. "cannot be evaluated" is
# spelled out rather than abbreviated: it is the state a reader is most likely to
# misread as "excluded", and the whole point of the third treatment is that they
# are different facts.
DIM_STATE_WORD: dict[str, str] = {
    MATCH: "shown",
    DIMMED: "dimmed",
    UNEVALUABLE: "cannot be evaluated",
}

# Deliberately no `drop_no_data` tail, unlike `filters._LINE`. There is nothing
# to drop, and accepting the token would make this control read as §3.2's.
_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*(>=|=>|<=|=<|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

# Words, not symbols, in the UI. §3.2's control is a textarea of `ring >= n`
# lines; this one is three dropdowns, and the wording is one of the things that
# keeps them from being mistaken for each other.
OP_PHRASE: dict[str, str] = {"ge": "is at least", "le": "is at most"}

# Every sentence this module prints, as a named template.
#
# v4.0, and the reason is the **offline HTML export**. A dim threshold is a number
# Puck types, so it cannot be precomputed, so the export's JavaScript has to run
# the comparison and the three-way precedence with no Python to call. What it must
# not also do is author the wording: a count line phrased one way in the app and
# another in a file he reads on a different machine is one claim at two weights,
# which is `UX-v3-rounds.md` B14 with a network boundary in the middle. So the
# words live here, in one place, and both surfaces fill the same blanks —
# `{value}` and `{threshold}` are always `format(v, "g")`, in Python and in the
# export's `g()`, which the export's own self-check holds to equality.
PHRASE_T = "{label} {op_phrase} {threshold}"
MISSED_T = "{ring} = {value}, which is not {op_phrase} {threshold}"
SKIPPED_T = ("{ring} is {state} for this gene, so '{phrase}' cannot be evaluated")
SKIPPED_TAIL = (". Kept at full strength: no value is not a low value, and this "
                "filter has nothing to say about it.")
REASON_JOIN = "; "
ABSENT_STATE = "absent from this table"
NOTE_T = ("Dimming {named}. All {total} genes are still on the grid; nothing was "
          "removed and nothing was added to the drop ledger.")
NOTE_JOIN = " and "
COUNT_LINE_T = ("{match} shown · {dimmed} dimmed · {unevaluable} cannot be "
                "evaluated — {note}")
NOT_A_NUMBER_T = "{ring}: {raw} is not a number"


class DimRejected(ValueError):
    """The dim settings themselves are unusable. Never a per-gene verdict."""


@dataclass(frozen=True)
class DimFilter:
    ring: str
    op: str
    threshold: float

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise DimRejected(
                f"unknown comparison {self.op!r} on the {self.ring!r} dim filter; "
                f"use one of {sorted(OPS)}")
        if self.threshold != self.threshold or self.threshold in (
                float("inf"), float("-inf")):
            raise DimRejected(f"{self.ring!r}: {self.threshold!r} is not a threshold")

    @property
    def symbol(self) -> str:
        return OPS[self.op][0]

    def matches(self, v: float) -> bool:
        """The bound, against the raw value. The *only* comparison in this file,
        and it is `filters.OPS`' so the two mechanisms cannot come to disagree
        about what a threshold means."""
        return OPS[self.op][1](v, self.threshold)

    def phrase(self, ring_label: str | None = None) -> str:
        """What the count line says. Words rather than `>=`, and the ring's own
        label where there is one, so a reader sees the units."""
        return PHRASE_T.format(label=ring_label or self.ring,
                               op_phrase=OP_PHRASE[self.op],
                               threshold=format(self.threshold, "g"))

    def as_dict(self) -> dict[str, Any]:
        return {"ring": self.ring, "op": self.op, "symbol": self.symbol,
                "threshold": self.threshold,
                "phrase": self.phrase(),
                "expression": f"{self.ring} {self.symbol} {self.threshold:g}"}


def parse_dims(raw: Any, dimmable: Iterable[str] = ()) -> list[DimFilter]:
    """Dim filters from the page, as JSON or as one `ring >= n` per line.

    A filter naming a column this run does not have, or one this build refuses to
    filter on, is rejected outright rather than printed as active while doing
    nothing. `completeness` is refused by the caller's `dimmable` list for the
    same reason §3.2 refuses it: de-emphasising by k would de-emphasise by how
    much anybody has studied a gene (PLAN §2, M3), and doing it with opacity
    instead of with removal does not make it a different act.
    """
    if raw is None or raw == "":
        return []
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
            except json.JSONDecodeError as e:
                raise DimRejected(f"the dim filters are not valid JSON: {e}") from e
        else:
            items = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                m = _LINE.match(line)
                if not m:
                    raise DimRejected(
                        f"cannot read the dim filter {line.strip()!r}. One per line, "
                        f"as `column >= number` or `column <= number`. This control "
                        f"dims rows; it has no drop_no_data option because it removes "
                        f"nothing.")
                items.append({"ring": m.group(1), "op": SYMBOL_TO_OP[m.group(2)],
                              "threshold": float(m.group(3))})
    else:
        raise DimRejected(
            f"dim filters must be a list or text, not {type(raw).__name__}")

    known = set(dimmable)
    out: list[DimFilter] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise DimRejected(f"each dim filter must be an object, not {item!r}")
        ring = str(item.get("ring") or "").strip()
        op = str(item.get("op") or "ge").strip()
        op = SYMBOL_TO_OP.get(op, op)
        raw_threshold = item.get("threshold")
        if raw_threshold in (None, ""):
            continue  # an empty box is not a filter
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise DimRejected(NOT_A_NUMBER_T.format(
                ring=ring or "?", raw=repr(raw_threshold))) from e
        if known and ring not in known:
            raise DimRejected(
                f"there is no {ring!r} column to dim on in this run. Dimmable "
                f"columns: {', '.join(sorted(known))}. `completeness` is not one of "
                f"them: de-emphasising by k would de-emphasise by how much anybody "
                f"has studied a gene (PLAN §2).")
        if (ring, op) in seen:
            raise DimRejected(
                f"two {ring!r} {OPS[op][0] if op in OPS else op} dim filters were "
                f"sent; keep one so the count line can state what it did")
        seen.add((ring, op))
        out.append(DimFilter(ring=ring, op=op, threshold=threshold))
    return out


@dataclass
class DimOutcome:
    """Per gene: one of three states, and the sentence that state is true for.

    No list is removed from and no order is decided here. `state` covers exactly
    the genes handed in, so a caller that forgets to look at it renders an
    undimmed grid rather than a wrong one.

    There is deliberately **no** per-gene count of how many filters a gene
    matched. That number, sorted, is a composite score (PLAN §3), and the way to
    keep it out of the app is not to compute it.
    """

    state: dict[str, str] = field(default_factory=dict)
    reason: dict[str, str] = field(default_factory=dict)

    def genes_in(self, which: str) -> list[str]:
        return [g for g, s in self.state.items() if s == which]

    @property
    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in DIM_STATES}
        for s in self.state.values():
            c[s] += 1
        return c


def classify(genes: Sequence[str],
             cells_by_ring: dict[str, dict[str, Cell]],
             dims: Sequence[DimFilter]) -> DimOutcome:
    """Decide each gene's treatment. Nothing is mutated, moved or dropped.

    With several filters active the precedence is the one §3.2 already uses when
    it decides a gene's ledger status: a real measurement that misses a bound is
    the stronger fact, so a gene that fails any *evaluable* filter is `DIMMED`
    even if another filter could not be evaluated for it. A gene that fails none
    and cannot be evaluated on at least one is `UNEVALUABLE`. Everything else is
    a `MATCH`.
    """
    out = DimOutcome()
    if not dims:
        for g in genes:
            out.state[g] = MATCH
        return out

    for g in genes:
        missed: list[str] = []
        skipped: list[str] = []
        for f in dims:
            cell = cells_by_ring.get(f.ring, {}).get(g)
            if cell is None or cell.state != "value" or cell.value is None:
                state = ABSENT_STATE if cell is None else cell.state
                skipped.append(SKIPPED_T.format(ring=f.ring, state=state,
                                                phrase=f.phrase()))
                continue
            if not f.matches(cell.value):
                missed.append(MISSED_T.format(
                    ring=f.ring, value=format(cell.value, "g"),
                    op_phrase=OP_PHRASE[f.op],
                    threshold=format(f.threshold, "g")))
        if missed:
            out.state[g] = DIMMED
            out.reason[g] = REASON_JOIN.join(missed)
        elif skipped:
            out.state[g] = UNEVALUABLE
            out.reason[g] = REASON_JOIN.join(skipped) + SKIPPED_TAIL
        else:
            out.state[g] = MATCH
    return out


def count_note(outcome: DimOutcome, dims: Sequence[DimFilter],
               ring_labels: dict[str, str] | None = None) -> str:
    """Everything the count line says except the three counts themselves: which
    bound is active, and that the grid is still whole.

    Split out because the page prints the counts as its own headline, and a block
    that stated `4 shown · 1 dimmed · 9 cannot be evaluated` twice in four lines
    would be `UX-v3-rounds.md` B14 — the same claim carried at two weights — in a
    new place.
    """
    if not dims:
        return ""
    total = sum(outcome.counts.values())
    named = NOTE_JOIN.join(f.phrase((ring_labels or {}).get(f.ring)) for f in dims)
    return NOTE_T.format(named=named, total=total)


def count_line(outcome: DimOutcome, dims: Sequence[DimFilter],
               ring_labels: dict[str, str] | None = None) -> str:
    """The whole thing as ONE string, so a screenshot of a filtered table says what
    was filtered. A filtered grid that does not say it is filtered is a misleading
    artefact — the same principle as the manifest making a screenshot of the figure
    self-describing (AGENTS.md).

    It states the three counts *and* that their sum is still every rendered gene,
    because the thing a reader must not conclude from a dim filter is that rows
    left the table. The page splits it (`count_note`) rather than restating it.
    """
    if not dims:
        return ""
    c = outcome.counts
    return COUNT_LINE_T.format(
        match=c[MATCH], dimmed=c[DIMMED], unevaluable=c[UNEVALUABLE],
        note=_uncapitalise(count_note(outcome, dims, ring_labels)))


def _uncapitalise(s: str) -> str:
    """`Dimming …` after an em dash reads as a new sentence when it is a clause."""
    return s[:1].lower() + s[1:] if s else s


def assert_nothing_left(genes: Sequence[str], outcome: DimOutcome) -> None:
    """A dim filter is view state, so the gene set it was applied to is the gene
    set that comes out. Asserted rather than described, next to the code that
    could break it: if these counts ever stop adding up, the feature has become a
    removal and `input == rendered + ledgered` is being told a different story
    from the one the manifest holds.
    """
    if set(outcome.state) != set(genes) or len(outcome.state) != len(genes):
        raise AssertionError(
            f"the dim filter classified {len(outcome.state)} gene(s) out of "
            f"{len(genes)} rendered. Dimming is a view state: it must classify "
            f"every rendered gene and remove none.")
    if sum(outcome.counts.values()) != len(genes):
        raise AssertionError(
            f"the dim counts sum to {sum(outcome.counts.values())}, not the "
            f"{len(genes)} genes on the grid.")


__all__ = ["DimFilter", "DimOutcome", "DimRejected", "MATCH", "DIMMED",
           "UNEVALUABLE", "DIM_STATES", "DIM_STATE_WORD", "OP_PHRASE",
           "parse_dims", "classify", "count_line", "count_note",
           "assert_nothing_left",
           "PHRASE_T", "MISSED_T", "SKIPPED_T", "SKIPPED_TAIL", "REASON_JOIN",
           "ABSENT_STATE", "NOTE_T", "NOTE_JOIN", "COUNT_LINE_T",
           "NOT_A_NUMBER_T"]
