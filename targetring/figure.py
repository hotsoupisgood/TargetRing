"""The Circos figure: pycirclize -> SVG.

Design rules that are not cosmetic:

* `no_data` is hatched grey drawn from a palette that is *provably* disjoint
  from the value colormap. Provably means checked, twice: numerically before
  drawing (`assert_fills_disjoint`) and again in the rendered SVG by invariant 2.
* Every cell carries an SVG `id` (`cell|<ring>|<index>|<gene>`). That is what
  makes cell-count identity and label correspondence checkable in the artefact
  rather than only in the code that produced it (QA S1 checks 1 and 4).
* k, the gene and dropped counts, the default weights, the region and the
  denominator tissue set are stamped on the figure, because the morning review
  is a screenshot (QA B7 item 4).
* A failed invariant draws a banner across the figure. The overnight run's job
  is to be honest, not to produce output (QA §5 item 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import Normalize, to_hex  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from pycirclize import Circos  # noqa: E402

from .rank import DEFAULT_WEIGHTS, EXCLUDED_FROM_SCORE, GeneRanking  # noqa: E402
from .states import Cell  # noqa: E402

VALUE_CMAP = "viridis"

# See the comment at the rect() call: anything but exactly 1.0 keeps each cell a
# separate SVG element with its own id.
CELL_ZORDER = 2.0

# Deliberately neutral / achromatic. Viridis is saturated across its whole
# range, so these cannot collide with a value colour -- and that claim is
# asserted, not assumed.
STATE_STYLE: dict[str, dict[str, Any]] = {
    "no_data": {"fc": "#b3b3b3", "hatch": "////", "ec": "#4d4d4d",
                "label": "no data — never measured here"},
    "not_applicable": {"fc": "#ffffff", "hatch": "....", "ec": "#808080",
                       "label": "not applicable to this gene"},
    "failed": {"fc": "#000000", "hatch": "xxxx", "ec": "#ff0000",
               "label": "call FAILED — not the same as absent"},
}


@dataclass(frozen=True)
class RingSpec:
    key: str
    label: str
    source: str


# PLAN.md §2. Order is outermost-first.
RING_SPECS: tuple[RingSpec, ...] = (
    RingSpec("logfc", "logFC (yours)", "submitted DE table"),
    RingSpec("neg_log10_padj", "-log10 padj (yours)", "submitted DE table"),
    RingSpec("druggability", "Druggability (prior effort + tractability)", "OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder, hasHighQualityChemicalProbes"),
    RingSpec("safety", "Safety flags & constraint — NOT evidence of safety", "OT prioritisation: hasSafetyEvent, geneEssentiality, geneticConstraint, isCancerDriverGene"),
    RingSpec("regional_specificity", "Regional specificity", "GTEx v8 median TPM ratio"),
    RingSpec("clinical_phase", "Pipeline: furthest phase (attributable indications only)", "OT drugAndClinicalCandidates, gated on OT target-disease mechanistic evidence"),
    RingSpec("clinical_activity", "Pipeline: current activity", "OT clinicalReports (AACT rows only)"),
    RingSpec("rodent", "Rodent phenotype lead", "OT mousePhenotypes, 'no abnormal phenotype' excluded"),
    RingSpec("literature", "Literature volume", "PubTator3, gene-id resolved, log10(1+n)"),
    RingSpec("completeness", "k — rings available", "derived"),
)


def colormap_hexes(n: int = 256) -> set[str]:
    cmap = plt.get_cmap(VALUE_CMAP)
    return {to_hex(cmap(i / (n - 1))) for i in range(n)}


def assert_fills_disjoint() -> None:
    """PLAN §6 invariant 2, checked before a single cell is drawn.

    The SVG check is the artefact-level proof; this is the one that fires during
    a palette change, at the moment someone edits STATE_STYLE.
    """
    values = colormap_hexes()
    for state, style in STATE_STYLE.items():
        fill = to_hex(style["fc"])
        if fill in values:
            raise AssertionError(
                f"the {state!r} fill {fill} is also produced by the {VALUE_CMAP} colormap; "
                f"missing would be indistinguishable from a value"
            )


def _norm_for(ring: str, cells: dict[str, Cell]) -> Normalize | None:
    vals = [c.value for c in cells.values() if c.state == "value"]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    return Normalize(vmin=lo, vmax=hi)


@dataclass
class FigureResult:
    svg_path: Path
    csv_rows: list[dict[str, Any]]
    cell_ids: list[str]
    n_cells: int


def render(
    *,
    genes: Sequence[str],
    cells_by_ring: dict[str, dict[str, Cell]],
    rankings: dict[str, GeneRanking],
    region: str,
    out_svg: Path,
    out_png: Path | None = None,
    input_count: int,
    rendered_count: int,
    ledger_counts: dict[str, int],
    invariant_failures: Sequence[str] = (),
    weights: dict[str, float] | None = None,
    ranked_rings: Sequence[str] = (),
    gene_flags: dict[str, list[str]] | None = None,
    subtitle: str = "",
    provenance: str = "",
) -> FigureResult:
    assert_fills_disjoint()
    weights = weights or DEFAULT_WEIGHTS
    assert_footer_is_true(weights, ranked_rings or ())
    genes = list(genes)
    if not genes:
        raise ValueError("no genes to render")

    rings = [r for r in RING_SPECS if r.key in cells_by_ring]
    cmap = plt.get_cmap(VALUE_CMAP)
    norms = {r.key: _norm_for(r.key, cells_by_ring[r.key]) for r in rings}

    circos = Circos({g: 1 for g in genes}, space=2 if len(genes) > 2 else 8)

    r_outer, r_inner = 97.0, 33.0
    band = (r_outer - r_inner) / len(rings)
    csv_rows: list[dict[str, Any]] = []
    cell_ids: list[str] = []

    for ring_i, spec in enumerate(rings):
        hi = r_outer - ring_i * band
        lo = hi - band * 0.86
        cells = cells_by_ring[spec.key]
        norm = norms[spec.key]

        for gene_i, gene in enumerate(genes):
            sector = circos.get_sector(gene)
            track = sector.add_track((lo, hi))
            cell = cells.get(gene)
            if cell is None:
                cell = Cell("no_data", None, f"{spec.key} was not computed for this gene")

            if cell.state == "value" and norm is not None:
                fill = to_hex(cmap(norm(cell.value)))
                style = {"fc": fill, "ec": "#ffffff", "lw": 0.25}
            else:
                st = STATE_STYLE[cell.state if cell.state in STATE_STYLE else "no_data"]
                fill = st["fc"]
                style = {"fc": st["fc"], "hatch": st["hatch"], "ec": st["ec"], "lw": 0.4}

            # zorder must not be exactly 1: pycirclize merges default-zorder,
            # un-hatched patches into one PatchCollection, which discards the
            # per-patch gid and with it the whole SVG audit trail. Hatched cells
            # were surviving and plain ones were not, which would have made
            # invariants 1 and 3 silently partial.
            track.rect(0, 1, zorder=CELL_ZORDER, **style)
            # The id is the join between the artefact and the CSV. Index first
            # so label correspondence (QA S1 check 4) is positional *and* named.
            gid = f"cell|{spec.key}|{gene_i}|{gene}"
            track.patches[-1].set_gid(gid)
            cell_ids.append(gid)

            csv_rows.append({
                "cell_id": gid,
                "spoke_index": gene_i,
                "gene": gene,
                "ring": spec.key,
                "ring_label": spec.label,
                "state": cell.state,
                "value": cell.value if cell.state == "value" else "",
                "fill": fill,
                "detail": cell.detail,
                "source": spec.source,
            })

    # Spoke labels carry k, and mark Pareto members and unstable genes.
    for gene_i, gene in enumerate(genes):
        sector = circos.get_sector(gene)
        gr = rankings.get(gene)
        k = gr.k if gr else 0
        mark = "◆ " if (gr and gr.pareto) else ""
        # COUNCIL V1 (ii). Open Targets has no anti-target axis, so hERG reads as
        # druggable, clinically validated and actively trialled while its own
        # safetyLiabilities are Torsades de Pointes and Increased Mortality. The
        # marker sits where the eye already is, next to k.
        liab = "⚠ " if gene in (gene_flags or {}) else ""
        warn = " *" if (gr and gr.unstable) else ""
        label = f"{mark}{liab}{gene} (k={k}){warn}"
        sector.text(label, r=r_outer + 3, size=6.5 if len(genes) > 18 else 8,
                    orientation="vertical", color="#111111")

    fig = circos.plotfig(figsize=(17.5, 14.0), dpi=100)
    ax = fig.axes[0]
    # pycirclize gives the polar axes the whole figure. Shrink it so the ring
    # index on the left and the methods stamps at the bottom have somewhere to
    # live that is not on top of the data.
    ax.set_position((0.245, 0.175, 0.59, 0.785))

    _draw_centre(ax, region, len(genes), input_count, rendered_count, ledger_counts)
    _draw_legend(fig, rings, norms, cmap)
    _draw_stamps(fig, weights, region, subtitle, provenance, cells_by_ring, gene_flags)
    if invariant_failures:
        _draw_banner(fig, invariant_failures)

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_svg, format="svg")
    if out_png is not None:
        # Convenience only. SVG plus the CSV is the export that matters for v1
        # (PLAN §5); this exists so a run can be eyeballed without a browser.
        fig.savefig(out_png, format="png", dpi=110)
    plt.close(fig)
    return FigureResult(out_svg, csv_rows, cell_ids, len(cell_ids))


def _draw_centre(ax, region, n_genes, input_count, rendered_count, ledger_counts) -> None:
    dropped = input_count - rendered_count
    parts = [f"{input_count} genes in", f"{rendered_count} rendered", f"{dropped} not shown"]
    detail = ", ".join(f"{v} {k}" for k, v in sorted(ledger_counts.items())) or "—"
    ax.text(0.5, 0.545, "targetring v1", ha="center", va="center", size=15, weight="bold",
            transform=ax.transAxes)
    ax.text(0.5, 0.505, region, ha="center", va="center", size=11,
            transform=ax.transAxes, color="#333333")
    ax.text(0.5, 0.472, " · ".join(parts), ha="center", va="center", size=9.5,
            transform=ax.transAxes, color="#111111")
    ax.text(0.5, 0.448, f"not shown: {detail}", ha="center", va="center", size=7.5,
            transform=ax.transAxes, color="#555555", wrap=True)
    ax.text(0.5, 0.424, "ranking is provisional and not validated",
            ha="center", va="center", size=7.5, transform=ax.transAxes,
            color="#8a4b00", style="italic")


def _draw_legend(fig, rings, norms, cmap) -> None:
    handles = [
        Patch(facecolor=s["fc"], hatch=s["hatch"], edgecolor=s["ec"], label=s["label"])
        for s in STATE_STYLE.values()
    ]
    handles.append(Patch(facecolor=cmap(0.85), edgecolor="#ffffff",
                         label=f"value (per-ring scale, {VALUE_CMAP})"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.006, 0.992),
               fontsize=7.5, frameon=True, title="cell states", title_fontsize=8)

    lines = []
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = f"[{n.vmin:.3g}, {n.vmax:.3g}]" if n else "no values"
        lines.append(f"{i}. {spec.label} — {rng}\n     {spec.source}")
    fig.text(0.006, 0.80, "rings, outermost first\n" + "\n".join(lines),
             fontsize=6.6, va="top", ha="left", color="#222222", family="monospace")


def assert_footer_is_true(weights: dict[str, float], ranked_rings: Sequence[str]) -> None:
    """COUNCIL V5: the figure must not make a false claim about its own method.

    v1's footer read "Rodent ring: … Never folded into any score" one line below
    "Default weights … rodent=0.5", and `rodent` was in RANKED_RINGS too, so it
    was in the weighted score *and* the median available rank. Rather than trust
    a comment to keep the two in step, the claim is checked against the data
    structures it describes, every time the figure is drawn.
    """
    for ring, why in EXCLUDED_FROM_SCORE.items():
        if ring in weights:
            raise AssertionError(
                f"the figure's footer says {ring!r} is never folded into any score, but it "
                f"is in the default weights ({ring}={weights[ring]:g}). {why}. "
                f"Either remove the weight or delete the footer sentence."
            )
        if ring in ranked_rings and ring == "rodent":
            raise AssertionError(
                f"{ring!r} is in RANKED_RINGS, so it contributes to the median available "
                f"rank, while the footer says it is never folded into any score. {why}."
            )


def _draw_stamps(fig, weights, region, subtitle, provenance, cells_by_ring,
                 gene_flags=None) -> None:
    w = ", ".join(f"{k}={v:g}" for k, v in sorted(weights.items()))
    n_tissues = ""
    for c in cells_by_ring.get("regional_specificity", {}).values():
        if c.state == "value" and isinstance(c.raw, dict) and c.raw.get("n_tissues"):
            n_tissues = f"{c.raw['n_tissues']} GTEx tissues"
            break
    excluded = ", ".join(sorted(EXCLUDED_FROM_SCORE))
    notes = [
        "HEADLINE = Pareto non-dominated set (◆), over druggability, safety, regional "
        "specificity, furthest clinical phase. A gene needs a real value on ALL FOUR to be "
        "eligible; genes with fewer are neither in the set nor able to remove anything from it.",
        f"Weighted score is a labelled CONVENIENCE, not the headline. Default weights "
        f"(provisional, unsigned-off): {w}. Excluded from every score: {excluded}.",
        f"Regional specificity = {region} TPM ÷ median across {n_tissues or 'the GTEx panel'}.",
        "Rodent ring: abstract/model evidence is a LEAD, not a verdict, and is ranked nowhere "
        "and weighted nowhere. Asserted against the weights at draw time, not just claimed.",
        "Missing is never imputed. k = rings with a real measurement for that gene.",
        "* = this gene's top-10 place does not survive reweighting.",
        "Pipeline: furthest phase counts only drugs indicated for a disease with mechanistic "
        "evidence linking it to THIS target. A drug that hits the target for an unrelated "
        "indication sets no phase; those genes are drawn 'not applicable', not 'low'.",
    ]
    if gene_flags:
        notes.append(
            "⚠ = Open Targets records a cardiac-conduction, cardiotoxicity or mortality "
            f"safety liability for this target ({', '.join(sorted(gene_flags))}). The "
            "clinical rings are a pipeline statement, never a safety one."
        )
    if subtitle:
        notes.insert(0, subtitle)
    fig.text(0.5, 0.008, "\n".join(notes), fontsize=7.5, ha="center", va="bottom",
             color="#222222")
    if provenance:
        fig.text(0.995, 0.995, provenance, fontsize=6, ha="right", va="top",
                 color="#555555", family="monospace")


def _draw_banner(fig, failures: Sequence[str]) -> None:
    """A failed invariant must be visible on the morning screenshot."""
    text = "INVARIANT FAILURE — DO NOT TRUST THIS FIGURE\n" + "\n".join(
        f"• {f}" for f in list(failures)[:6]
    )
    fig.text(0.5, 0.5, text, fontsize=15, ha="center", va="center", color="#ffffff",
             weight="bold", zorder=1000,
             bbox={"facecolor": "#b00020", "alpha": 0.95, "pad": 16, "edgecolor": "#000000"})


__all__ = ["render", "RING_SPECS", "STATE_STYLE", "VALUE_CMAP", "FigureResult",
           "assert_fills_disjoint", "assert_footer_is_true", "colormap_hexes"]
