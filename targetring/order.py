"""Spoke order, and k. What is left of `rank.py` after v2.5 deleted ranking.

v2.5 removed the Pareto non-dominated set, the weighted convenience score, the
weights, eligibility, rank-within-the-submitted-set and the reweighting-stability
flag. Nothing here ranks anything. Two things survive, because the figure needs
them and neither was ever part of the ranking:

  * **k** -- how many of the external rings have a real measurement for a gene.
    It tells a reader how much of a gene's row is real. It no longer feeds a
    median rank, a score or the spoke order: with nothing ranked, k orders
    nothing and selects nothing. It is a completeness gauge and is labelled as
    one, on the figure and in the ring key.
  * **the spoke order**, which still has to be *decided* rather than left to dict
    insertion order, and has to be *stated* on the figure. The default is the
    order of the submitted DE table -- Puck has usually already sorted it, and it
    is his measurement rather than a composite we invented. Alphabetical and
    single-ring orderings are offered beside it; a single ring is one source's
    own number, not a composite, which is the whole reason it is allowed here at
    all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .states import Cell

# COUNCIL V13. k was counted over every ring, including `logfc` and
# `neg_log10_padj`, which come from the user's own table and are therefore never
# missing -- so every gene had k >= 2 for free, the figure's stamp read
# "k = rings with a real measurement for that gene", and TMEM229B, a gene about
# which essentially nothing is known, showed k=7 on a 10-ring figure whose key
# said the range was [7, 9]. k counts only the rings a source has to answer for,
# and the maximum is stated on the figure.
EXTERNAL_RINGS: tuple[str, ...] = (
    "tractable", "chemical_probe", "safety", "regional_specificity",
    "clinical_phase", "clinical_activity", "tdl", "rodent", "literature",
)
EXTERNAL_RING_COUNT = len(EXTERNAL_RINGS)

# Which way is "better" when a single ring is used to order the spokes, so that
# "best first" means the same thing on a ring where more is worse. These are
# assertions about desirability, not measurements -- the prioritisation rings
# already arrive with sign = desirability (COUNCIL M2), so they are +1, and
# `padj` is already stored as -log10, so a bigger number is a stronger result.
#
# This is the *only* remaining use of an orientation in this build. It orders
# spokes on one ring at a time and combines nothing, so it cannot reintroduce the
# incommensurability M3 objected to.
RING_SIGN: dict[str, int] = {
    "tractable": +1,
    "chemical_probe": +1,
    "safety": +1,
    "regional_specificity": +1,
    "clinical_phase": +1,
    "clinical_activity": +1,
    "tdl": +1,
    "rodent": +1,
    "literature": +1,
    "logfc": +1,
    "neg_log10_padj": +1,
    "completeness": +1,
}

SUBMITTED = "submitted"
ALPHABETICAL = "alphabetical"
RING_PREFIX = "ring:"


@dataclass
class GeneStats:
    """Per-gene facts the figure and the drawer need. No rank, no score."""

    gene: str
    k: int = 0
    note: str = ""
    # Rings where a threshold filter was active and this gene had no value, so
    # the filter could not be evaluated and the gene was kept (see
    # `filters.apply`). Marked on the figure rather than silently kept.
    unfiltered_rings: list[str] = field(default_factory=list)


def gene_stats(cells_by_ring: dict[str, dict[str, Cell]]) -> dict[str, GeneStats]:
    genes = sorted({g for cells in cells_by_ring.values() for g in cells})
    out: dict[str, GeneStats] = {}
    for g in genes:
        k = sum(1 for ring in EXTERNAL_RINGS
                if ring in cells_by_ring and g in cells_by_ring[ring]
                and cells_by_ring[ring][g].state == "value")
        st = GeneStats(gene=g, k=k)
        if k == 0:
            st.note = ("no external ring has a real measurement for this gene: every "
                       "coloured cell on its spoke is your own logFC or adjusted p")
        out[g] = st
    return out


def is_ring_ordering(ordering: str) -> str | None:
    """The ring key a `ring:<key>` ordering names, or None."""
    return ordering[len(RING_PREFIX):] if ordering.startswith(RING_PREFIX) else None


def normalise_ordering(ordering: str | None, cells_by_ring: dict[str, dict[str, Cell]]) -> str:
    """Reject an ordering this figure cannot honour, rather than quietly
    falling back to one and printing the other on the figure."""
    if not ordering:
        return SUBMITTED
    ordering = ordering.strip()
    if ordering in (SUBMITTED, ALPHABETICAL):
        return ordering
    ring = is_ring_ordering(ordering)
    if ring is None:
        raise ValueError(
            f"unknown spoke ordering {ordering!r}. Use {SUBMITTED!r} (the order of your "
            f"submitted table), {ALPHABETICAL!r}, or {RING_PREFIX}<ring key>."
        )
    if ring not in cells_by_ring:
        raise ValueError(
            f"cannot order the spokes by {ring!r}: this figure has no such ring. "
            f"Rings: {', '.join(sorted(cells_by_ring))}."
        )
    return ordering


def order_for_figure(
    submitted: Sequence[str],
    cells_by_ring: dict[str, dict[str, Cell]],
    ordering: str = SUBMITTED,
) -> list[str]:
    """The spoke order, clockwise from 12 o'clock.

    `submitted` is the surviving genes in the order of the user's own table.
    Ties and no-data genes are resolved deterministically, so two runs of the
    same input draw the same circle.
    """
    genes = list(submitted)
    if ordering == SUBMITTED:
        return genes
    if ordering == ALPHABETICAL:
        return sorted(genes)
    ring = is_ring_ordering(ordering)
    cells = cells_by_ring.get(ring or "", {})
    sign = RING_SIGN.get(ring or "", +1)
    position = {g: i for i, g in enumerate(genes)}
    valued = [g for g in genes if (c := cells.get(g)) is not None and c.state == "value"]
    # Genes with nothing on that ring cannot be placed by it. They go last as a
    # visible block, in submitted order -- not interleaved, and never treated as
    # the lowest value.
    missing = [g for g in genes if g not in set(valued)]
    valued.sort(key=lambda g: (-sign * cells[g].value, position[g]))
    return valued + missing


# --------------------------------------------------------------------------
# v3.6. Multi-key sort, for the table view.
#
# The table is a second rendering of a run the circle was already drawn from, so
# "sorted by logFC" must mean exactly one thing. Everything below is built out of
# `order_for_figure`'s own rule rather than beside it, and
# `test_table.py::TestTheTableAndTheCircleCannotDisagree` holds the single-key
# case to equality with it.
#
# Lexicographic and nothing more (PLAN §3): keys are applied in turn, each key is
# one ring's own number, and no two keys are ever combined into a third number.
# There is no ranking in this project and a multi-key sort must not become one by
# accident -- see `RETIRED_CLAIM_WORDS` and COUNCIL V2/V10.


@dataclass(frozen=True)
class SortKey:
    """One key of a lexicographic sort.

    `ordering` is a value from `/api/orderings` -- `submitted`, `alphabetical` or
    `ring:<key>` -- so a control can never offer the table a sort the figure
    could not honour.

    `reverse` flips the *values*. It deliberately does NOT move the missing
    block: a gene with no value on this key is not the best one either, and
    letting a reverse toggle float it to the top would be "missing is low"
    arriving through the other door.
    """

    ordering: str
    reverse: bool = False


def sort_key_and_reverse(
    spec: SortKey,
    cells_by_ring: dict[str, dict[str, Cell]],
    position: dict[str, int],
) -> tuple[Callable[[str], Any], bool]:
    """One key of the sort, as `(key function, reverse)` — `sorted`'s two arguments.

    Split out of `_sorted_by_one` in v4.0 so that the **offline HTML export** can
    ask this build what a gene's position on a key IS, rather than working it out
    for itself. The export cannot call Python when Puck opens it from `file://`,
    so its JavaScript has to do the final compare; what it must not do is decide
    which way `tractable` runs, or that a `no_data` cell sorts last rather than
    lowest. Those are decisions, they are made here, and the export carries the
    numbers this function produced (`export.sort_grades`).

    `_sorted_by_one` is now `sorted(genes, *this)`, so there is exactly one
    definition of every key in the build and the export is grading against it.
    """
    if spec.ordering == SUBMITTED:
        return (lambda g: position[g], spec.reverse)
    if spec.ordering == ALPHABETICAL:
        return (lambda g: g, spec.reverse)
    ring = is_ring_ordering(spec.ordering)
    cells = cells_by_ring.get(ring or "", {})
    sign = RING_SIGN.get(ring or "", +1)
    # Default is best first, which is descending on a ring whose sign is
    # desirability; `reverse` asks for worst first.
    direction = +1 if spec.reverse else -1

    def key(g: str) -> tuple[int, float]:
        c = cells.get(g)
        if c is None or c.state != "value":
            # 1 sorts after 0 whichever way the values are running, so the
            # unmeasured genes are last in both directions. The second element
            # is a constant: a missing cell contributes no position at all.
            return (1, 0.0)
        return (0, direction * sign * float(c.value))

    # The direction is already inside the key, so this pass never reverses: a
    # `reverse=True` here would also reverse the missing block to the top.
    return (key, False)


def _sorted_by_one(genes: list[str], cells_by_ring: dict[str, dict[str, Cell]],
                   spec: SortKey, position: dict[str, int]) -> list[str]:
    """One pass of the lexicographic sort. Stable, so ties keep the order they
    arrived in -- which is what makes applying the keys right-to-left equal to
    sorting on the tuple of them."""
    key, reverse = sort_key_and_reverse(spec, cells_by_ring, position)
    return sorted(genes, key=key, reverse=reverse)


def order_by_keys(
    submitted: Sequence[str],
    cells_by_ring: dict[str, dict[str, Cell]],
    keys: Sequence[SortKey],
) -> list[str]:
    """Lexicographic multi-key order. With one key this is `order_for_figure`.

    Applied right-to-left over a stable sort, which is the same answer as
    sorting on the tuple `(k1, k2, ...)` and keeps each key's own missing rule
    intact at its own level. With no keys at all it is the order it was given.
    """
    genes = list(submitted)
    position = {g: i for i, g in enumerate(genes)}
    for spec in reversed(list(keys)):
        genes = _sorted_by_one(genes, cells_by_ring, spec, position)
    return genes


def missing_on(genes: Sequence[str], cells_by_ring: dict[str, dict[str, Cell]],
               ordering: str) -> list[str]:
    """The genes that cannot be placed by this key, in the order given.

    They are a block the table draws and names, not a tail a reader has to
    notice. `submitted` and `alphabetical` can place every gene, so the block is
    empty for both.
    """
    ring = is_ring_ordering(ordering)
    if ring is None:
        return []
    cells = cells_by_ring.get(ring, {})
    return [g for g in genes
            if (c := cells.get(g)) is None or c.state != "value"]


# The sentence above the table, in its four pieces. Constants rather than literals
# inside `sort_label` because the **offline export** assembles the same sentence in
# JavaScript from the same strings: its reader changes the sort with no server to
# ask, and a table whose row order is stated in words the app does not use is two
# claims about one order. The export carries these four and one `sort_phrase` per
# key it offers; nothing about the wording is written twice.
SORT_LABEL_UNSORTED = "row order: the order this run drew the spokes in"
SORT_LABEL_PREFIX = "row order: "
SORT_LABEL_JOIN = ", then "
SORT_LABEL_TAIL = (
    ". Lexicographic — the keys are applied in turn and never combined. "
    "Genes with no value on a key are last on it, never lowest.")


def sort_phrase(spec: SortKey, ring_labels: dict[str, str] | None = None,
                table_phrase: str = "") -> str:
    """What ONE key of the sort is called, in the sentence above the table.

    `table_phrase` is `sources.DESource.table_phrase` -- see `ordering_label`
    for why "your submitted" cannot be a constant."""
    labels = ring_labels or {}
    if spec.ordering == SUBMITTED:
        return ((table_phrase or "your submitted") + " order"
                + (", reversed" if spec.reverse else ""))
    if spec.ordering == ALPHABETICAL:
        return "gene symbol, Z–A" if spec.reverse else "gene symbol, A–Z"
    ring = is_ring_ordering(spec.ordering) or "?"
    return f"'{labels.get(ring, ring)}' {'worst' if spec.reverse else 'best'} first"


def sort_label(keys: Sequence[SortKey], ring_labels: dict[str, str] | None = None,
               table_phrase: str = "") -> str:
    """The sentence shown above the table. Same job as `ordering_label` on the
    figure: an order that carries meaning and is not stated is an unlabelled
    scale."""
    if not keys:
        return SORT_LABEL_UNSORTED
    return (SORT_LABEL_PREFIX
            + SORT_LABEL_JOIN.join(sort_phrase(s, ring_labels, table_phrase)
                                   for s in keys)
            + SORT_LABEL_TAIL)


def parse_sort_keys(spec: str | Sequence[str] | None,
                    cells_by_ring: dict[str, dict[str, Cell]]) -> list[SortKey]:
    """`ring:logfc:desc,tractable...` -> keys, validated against this run's rings.

    Each token is `<ordering>` or `<ordering>:desc` / `:asc`, and the ordering is
    put through `normalise_ordering` -- the same gate the figure uses -- so a key
    this run cannot honour is rejected rather than quietly dropped.
    """
    if spec is None:
        tokens: list[str] = []
    elif isinstance(spec, str):
        tokens = [t.strip() for t in spec.split(",") if t.strip()]
    else:
        tokens = [str(t).strip() for t in spec if str(t).strip()]

    keys: list[SortKey] = []
    seen: set[str] = set()
    for tok in tokens:
        reverse = False
        if tok.endswith(":asc"):
            tok, reverse = tok[:-4], True
        elif tok.endswith(":desc"):
            tok = tok[:-5]
        ordering = normalise_ordering(tok, cells_by_ring)
        if ordering in seen:
            raise ValueError(
                f"{ordering!r} is used twice as a sort key. A key that has already "
                f"decided the order cannot decide it again."
            )
        seen.add(ordering)
        keys.append(SortKey(ordering, reverse))
    return keys


def ordering_label(ordering: str, ring_label: str | None = None,
                   table_phrase: str = "") -> str:
    """The sentence printed on the figure. A spoke order that carries meaning and
    is not stated is the same defect as an unlabelled colour scale.

    `table_phrase` names the input table this run's genes actually arrived in
    (`sources.DESource.table_phrase`). Without it this says "your submitted DE
    table", which is true on the paste path and false on an Atlas run -- where
    the panel found it rendered prominently in the offline export's provenance
    block, under the heading "spoke order the circle used", on a run whose
    genes came from a contrast nobody submitted.
    """
    if ordering == SUBMITTED:
        return (f"spoke order: the order of "
                f"{table_phrase or 'your submitted DE table'} (unchanged)")
    if ordering == ALPHABETICAL:
        return "spoke order: alphabetical by gene symbol"
    ring = is_ring_ordering(ordering) or "?"
    what = ring_label or ring
    return (f"spoke order: by the '{what}' ring, best first; "
            f"genes with no value on it are last")


__all__ = [
    "GeneStats", "gene_stats", "order_for_figure", "ordering_label",
    "normalise_ordering", "is_ring_ordering", "EXTERNAL_RINGS", "EXTERNAL_RING_COUNT",
    "RING_SIGN", "SUBMITTED", "ALPHABETICAL", "RING_PREFIX",
    "SortKey", "order_by_keys", "parse_sort_keys", "missing_on", "sort_label",
    "sort_key_and_reverse", "sort_phrase", "SORT_LABEL_UNSORTED",
    "SORT_LABEL_PREFIX", "SORT_LABEL_JOIN", "SORT_LABEL_TAIL",
]
