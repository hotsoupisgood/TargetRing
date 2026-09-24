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
from typing import Sequence

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


def ordering_label(ordering: str, ring_label: str | None = None) -> str:
    """The sentence printed on the figure. A spoke order that carries meaning and
    is not stated is the same defect as an unlabelled colour scale."""
    if ordering == SUBMITTED:
        return "spoke order: the order of your submitted DE table (unchanged)"
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
]
