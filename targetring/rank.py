"""Ranking. PLAN.md §3 / COUNCIL M3.

No weighted sum as the headline. The inputs are ordinal, ratio and binary, so a
weighted sum asserts an exchangeability nothing supports; missingness is not
random, so any imputation systematically rewards under-studied genes; and
sliders over unnormalised scales do not mean what they appear to.

So:
  * rank-transform each ring **within the submitted gene set**, never against a
    global background;
  * headline = the Pareto non-dominated set over four axes;
  * secondary = median available rank, ignoring missing rings, with k shown;
  * the weighted score stays, labelled a convenience, weights printed;
  * a reweighting-stability flag on any gene whose top-10 place does not survive
    reweighting;
  * **never impute**.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from typing import Any

from .states import Cell

# Higher is better after orientation. `sign` flips a ring whose natural
# direction is "more is worse".
#
# These signs are assertions about desirability, not measurements. The
# prioritisation rings already arrive with sign = desirability (COUNCIL M2), so
# they are +1. `padj` is flipped because a smaller adjusted p is a stronger
# result.
RING_SIGN: dict[str, int] = {
    "druggability": +1,
    "safety": +1,
    "regional_specificity": +1,
    "clinical_phase": +1,
    "clinical_activity": +1,
    "rodent": +1,
    "literature": +1,
    "logfc": +1,
    "neg_log10_padj": +1,
}

# COUNCIL V13. k was counted over every ring, including `logfc` and
# `neg_log10_padj`, which come from the user's own table and are therefore never
# missing -- so every gene had k >= 2 for free, the figure's stamp read
# "k = rings with a real measurement for that gene", and TMEM229B, a gene about
# which essentially nothing is known, showed k=7 on a 10-ring figure whose key
# said the range was [7, 9]. k now counts only the rings a source has to answer
# for, and the maximum is stated on the figure.
EXTERNAL_RINGS: tuple[str, ...] = (
    "druggability", "safety", "regional_specificity", "clinical_phase",
    "clinical_activity", "rodent", "literature",
)
EXTERNAL_RING_COUNT = len(EXTERNAL_RINGS)

# PLAN §3: druggability, safety, regional specificity, and clinical/evidence.
PARETO_AXES: tuple[str, ...] = (
    "druggability", "safety", "regional_specificity", "clinical_phase",
)

# Provisional. AGENTS.md: the defaults are a scientific choice, not a QA item,
# and stay provisional until Puck signs them off. Printed on the figure.
#
# COUNCIL V5. Two rings were removed from this dict in v2:
#   * `rodent` — M4 says the rodent result is a lead, never folded into a score,
#     and the figure's own footer said so while `rodent=0.5` sat in the weights
#     one line above it. It moved TP53 two places and BRAF two places. The gene
#     it flattered most was TP53, whose 249 abnormal mouse-phenotype terms
#     measure how catastrophic its knockout is (V9).
#   * `clinical_phase` — D7 asked for the clinical ring to stay out of the
#     weighted sum. It is still a Pareto axis; it is no longer a score term.
# If either is ever put back, `_draw_stamps` must lose the sentence that denies
# it. A figure must not make a false claim about its own method.
DEFAULT_WEIGHTS: dict[str, float] = {
    "druggability": 1.0,
    "safety": 1.0,
    "regional_specificity": 1.0,
    "literature": 0.5,
}

# Rings that are drawn and ranked but deliberately excluded from every score.
# Kept as data so the figure and the tests read the same list.
EXCLUDED_FROM_SCORE: dict[str, str] = {
    "rodent": "COUNCIL M4/V5 — a lead, not a verdict; never folded into any score",
    "clinical_phase": "COUNCIL D7/V5 — a Pareto axis, deliberately not a score term",
}


@dataclass
class GeneRanking:
    gene: str
    ranks: dict[str, float] = field(default_factory=dict)      # ring -> rank in [0,1]
    k: int = 0                                                  # rings actually available
    median_rank: float | None = None
    weighted_score: float | None = None
    pareto: bool = False
    pareto_k: int = 0                                           # axes available for Pareto
    pareto_eligible: bool = False                               # has a value on ALL axes (V2)
    unstable: bool = False
    note: str = ""


def rank_within_set(cells: dict[str, Cell], ring: str) -> dict[str, float]:
    """Rank one ring across the submitted genes only.

    Returns {gene: rank in [0,1]}, higher = better after orientation. Genes
    without a value are simply absent from the result — they are not ranked
    last, and they are not imputed to the median.
    """
    scored = {g: c.value for g, c in cells.items() if c.state == "value"}
    if not scored:
        return {}
    sign = RING_SIGN.get(ring, +1)
    ordered = sorted(scored, key=lambda g: sign * scored[g])
    # Average ranks for ties, so a ring of identical values does not invent an
    # ordering out of dict insertion order.
    out: dict[str, float] = {}
    i = 0
    n = len(ordered)
    while i < n:
        j = i
        while j + 1 < n and scored[ordered[j + 1]] == scored[ordered[i]]:
            j += 1
        avg = (i + j) / 2
        for g in ordered[i:j + 1]:
            out[g] = avg / (n - 1) if n > 1 else 1.0
        i = j + 1
    return out


def pareto_eligible(ranks: dict[str, float], axes: tuple[str, ...] = PARETO_AXES) -> bool:
    """COUNCIL V2. A gene is eligible for the headline only with all axes.

    v1 compared genes on their *shared* axes. Fewer shared axes means fewer
    conditions to satisfy, so missingness made domination **easier** — a gene
    whose only Pareto axis was druggability=1.0 entered the front on one
    measurement and ejected two genes that had all four. That is M3's "do not
    reward being under-measured" reappearing inside the mechanism M3 prescribed.

    The v1 docstring claimed the opposite ("fewer axes make domination harder")
    and so did the note the app printed. Both were wrong; both are fixed here.
    """
    return all(ax in ranks for ax in axes)


def _dominates(a: dict[str, float], b: dict[str, float], axes: tuple[str, ...]) -> bool:
    """a dominates b iff a is >= on every axis and > on at least one.

    Only called for genes that are eligible on *every* axis, so there is no
    shared-axis subset to choose and no way to win an axis by lacking it.
    """
    return all(a[ax] >= b[ax] for ax in axes) and any(a[ax] > b[ax] for ax in axes)


def pareto_front(ranks: dict[str, dict[str, float]], axes: tuple[str, ...] = PARETO_AXES) -> set[str]:
    """The non-dominated set over the genes eligible on every axis.

    Ineligible genes are neither in the front nor able to evict anything from
    it. They are reported as "not eligible — k=n of N Pareto axes" and drawn
    without a diamond; see COUNCIL V2 and V3.
    """
    genes = [g for g in ranks if pareto_eligible(ranks[g], axes)]
    return {
        g for g in genes
        if not any(_dominates(ranks[o], ranks[g], axes) for o in genes if o != g)
    }


def weighted(ranks: dict[str, float], weights: dict[str, float]) -> float | None:
    """A convenience, labelled as such on the figure.

    Computed over available rings only and renormalised by the weight actually
    used, so a gene missing a ring is not penalised by a zero — but it is also
    not comparable to a gene with every ring, which is why k travels with it.
    """
    num = sum(weights[r] * v for r, v in ranks.items() if r in weights)
    den = sum(weights[r] for r in ranks if r in weights)
    return num / den if den > 0 else None


def _perturbations(weights: dict[str, float], n: int = 48) -> list[dict[str, float]]:
    """Deterministic reweightings: each ring's weight halved or doubled.

    Deterministic on purpose — a random sensitivity probe would make the
    stability flag differ between two runs of the same input.
    """
    keys = sorted(weights)
    out = []
    for combo in itertools.product((0.5, 1.0, 2.0), repeat=min(len(keys), 4)):
        w = dict(weights)
        for k, m in zip(keys[:4], combo):
            w[k] = weights[k] * m
        out.append(w)
        if len(out) >= n:
            break
    return out


def rank_genes(
    cells_by_ring: dict[str, dict[str, Cell]],
    weights: dict[str, float] | None = None,
    ranked_rings: tuple[str, ...] | None = None,
) -> dict[str, GeneRanking]:
    """cells_by_ring: {ring: {gene: Cell}} -> {gene: GeneRanking}."""
    weights = weights or DEFAULT_WEIGHTS
    rings = ranked_rings or tuple(cells_by_ring)
    genes = sorted({g for cells in cells_by_ring.values() for g in cells})

    per_ring = {r: rank_within_set(cells_by_ring[r], r) for r in rings if r in cells_by_ring}
    by_gene: dict[str, dict[str, float]] = {
        g: {r: v[g] for r, v in per_ring.items() if g in v} for g in genes
    }

    front = pareto_front(by_gene)
    out: dict[str, GeneRanking] = {}
    for g in genes:
        ranks = by_gene[g]
        gr = GeneRanking(gene=g, ranks=ranks)
        # COUNCIL V13: external rings only. `logfc` and `neg_log10_padj` are the
        # user's own numbers and are never missing; counting them inflated every
        # gene's k by two and made the ring's own maximum unreachable.
        gr.k = sum(1 for ring in EXTERNAL_RINGS
                   if ring in cells_by_ring and g in cells_by_ring[ring]
                   and cells_by_ring[ring][g].state == "value")
        gr.median_rank = statistics.median(ranks.values()) if ranks else None
        gr.weighted_score = weighted(ranks, weights)
        gr.pareto = g in front
        gr.pareto_k = sum(1 for ax in PARETO_AXES if ax in ranks)
        gr.pareto_eligible = pareto_eligible(ranks)
        if not ranks:
            gr.note = "no ranked ring has a value for this gene"
        elif not gr.pareto_eligible:
            missing = ", ".join(ax for ax in PARETO_AXES if ax not in ranks)
            gr.note = (
                f"not eligible for the headline — {gr.pareto_k} of {len(PARETO_AXES)} "
                f"Pareto axes (missing: {missing}). It is neither in the front nor "
                f"able to remove anything from it."
            )
        out[g] = gr

    _flag_instability(out, weights)
    return out


def _flag_instability(rankings: dict[str, GeneRanking], weights: dict[str, float]) -> None:
    """COUNCIL M3 / PLAN §3: flag any gene whose top-10 place does not survive
    reweighting. Kept because it is the honest caveat on a score the app already
    says is only a convenience."""
    def top10(w: dict[str, float]) -> set[str]:
        scored = [
            (g, s) for g, r in rankings.items()
            if (s := weighted(r.ranks, w)) is not None
        ]
        scored.sort(key=lambda t: -t[1])
        return {g for g, _ in scored[:10]}

    base = top10(weights)
    if not base:
        return
    for w in _perturbations(weights):
        moved = base.symmetric_difference(top10(w))
        for g in moved:
            if g in rankings:
                rankings[g].unstable = True


def pareto_axis_completeness(rankings: dict[str, GeneRanking]) -> dict[str, Any]:
    """COUNCIL V3: how much of the headline's input was actually retrieved.

    A gene missing a Pareto axis can no longer change another gene's membership
    (V2's eligibility rule), but it can still change the front by *withdrawing*
    a dominator, which only ever enlarges the front. That is the safe direction
    and it is not a data error — so it is reported as a run condition, counted
    here and carried into the manifest and the figure's conditions notice,
    rather than as an invariant failure. See `docs/V2-REPORT.md` §A1.
    """
    per_axis = {
        ax: sum(1 for r in rankings.values() if ax not in r.ranks) for ax in PARETO_AXES
    }
    eligible = [g for g, r in rankings.items() if r.pareto_eligible]
    return {
        "n_genes": len(rankings),
        "n_eligible": len(eligible),
        "n_ineligible": len(rankings) - len(eligible),
        "missing_cells_per_axis": per_axis,
        "missing_cells_total": sum(per_axis.values()),
        "complete": sum(per_axis.values()) == 0,
        "ineligible_genes": sorted(g for g, r in rankings.items() if not r.pareto_eligible),
    }


def order_for_figure(rankings: dict[str, GeneRanking]) -> list[str]:
    """Spoke order: Pareto members first, then by median available rank.

    Genes with no ranked value at all go last, in input order, so they are
    visibly a block of missing rather than scattered through the circle.
    """
    def key(g: str) -> tuple:
        r = rankings[g]
        return (
            0 if r.pareto else 1,
            -(r.median_rank if r.median_rank is not None else -1),
            -r.k,
            g,
        )
    ranked = [g for g in rankings if rankings[g].median_rank is not None]
    unranked = [g for g in rankings if rankings[g].median_rank is None]
    return sorted(ranked, key=key) + sorted(unranked)


__all__ = [
    "GeneRanking", "rank_genes", "rank_within_set", "pareto_front", "weighted",
    "order_for_figure", "DEFAULT_WEIGHTS", "PARETO_AXES", "RING_SIGN",
    "pareto_eligible", "pareto_axis_completeness",
    "EXTERNAL_RINGS", "EXTERNAL_RING_COUNT", "EXCLUDED_FROM_SCORE",
]
