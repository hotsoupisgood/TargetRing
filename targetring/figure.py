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

# Importing pycirclize mutates matplotlib's rcParams. Two of them matter enough
# to pin here rather than inherit:
#
# * `svg.fonttype`. matplotlib's own default is 'path', which turns every label
#   into outlines; pycirclize flips it to 'none', which is why the export is
#   editable in Illustrator at all. The UX review found that nothing in this
#   repo asserted it, so a dependency bump could silently turn all the text into
#   outlines. Pinned, and asserted in tests/test_presentation.py.
# * `savefig.bbox`/`pad_inches`. pycirclize sets 'tight' with 0.5 in of padding,
#   which grew the 17.5x14 canvas to the 18.3x15.2 the UX review measured -- and
#   would quietly make the 16:9 slide render 1.83:1.
SVG_FONTTYPE = "none"
matplotlib.rcParams["svg.fonttype"] = SVG_FONTTYPE
SAVE_RC = {"savefig.bbox": None, "savefig.pad_inches": 0.0,
           "svg.fonttype": SVG_FONTTYPE}

from .config import MAX_GENES  # noqa: E402
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


@dataclass(frozen=True)
class Layout:
    """UX A4. The audit render and the slide render are the same data drawn for
    two different readers, and v1 only had the first.

    The exported canvas was ~18.3 x 15.2 in. Fitted to the 7.5 in height of a
    16:9 slide that is a 0.49 scale factor, so 8 pt gene labels became 3.9 pt
    and the 6.6 pt monospace ring key became 3.3 pt. At full slide the only
    near-readable strings were "targetring" and the region name -- the two least
    useful on the figure. And at 1.20:1 on a 1.78:1 slide it filled the middle
    ~60% of the width, with the wasted columns exactly where the key lived.

    `audit` keeps every stamp: it is the right artefact for the morning review
    and a bad one for a slide. `slide` is 16:9, has type that survives
    projection, and moves the key and the caveats into a caption file you paste
    underneath.
    """

    name: str
    figsize: tuple[float, float]
    label_size: float
    axes_box: tuple[float, float, float, float]
    draw_key: bool
    draw_footer: bool
    footer_size: float
    centre_scale: float
    # The amber run-conditions notice belongs on the audit render and in the
    # caption. On a slide it would sit across the bottom spoke labels, and the
    # slide is the artefact you stand next to and explain. The RED banner is
    # drawn on both: you must never present a figure whose invariants failed.
    draw_conditions: bool
    cbar_x: float
    cbar_gap: float
    # Whether the centre carries the per-category drop counts and the list of
    # dropped gene names. The audit render does; the slide render gives the
    # count and points at the caption, because fitting nine names plus seven
    # category counts into the hole shrinks all of it past legibility -- which
    # is UX A4 reappearing in the middle of the figure.
    centre_detail: bool


AUDIT = Layout("audit", (17.5, 14.0), 8.0, (0.265, 0.205, 0.56, 0.755),
               True, True, 7.5, 1.0, True, 0.905, 0.040, True)
# 16:9. Labels at 11.5 pt in a 16 in canvas survive the 0.47 fit to a slide at
# ~5.4 pt, against v1's 3.9; the key and the caveats leave the canvas entirely.
SLIDE = Layout("slide", (16.0, 9.0), 11.5, (0.30, 0.075, 0.40, 0.86),
               False, False, 0.0, 1.15, False, 0.855, 0.038, False)
LAYOUTS = {"audit": AUDIT, "slide": SLIDE}


@dataclass
class FigureResult:
    svg_path: Path
    csv_rows: list[dict[str, Any]]
    cell_ids: list[str]
    n_cells: int
    caption: str = ""


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
    dropped_names: Sequence[str] = (),
    invariant_failures: Sequence[str] = (),
    run_conditions: Sequence[str] = (),
    weights: dict[str, float] | None = None,
    ranked_rings: Sequence[str] = (),
    gene_flags: dict[str, list[str]] | None = None,
    subtitle: str = "",
    provenance: str = "",
    layout: str = "audit",
) -> FigureResult:
    assert_fills_disjoint()
    lay = LAYOUTS[layout]
    weights = weights or DEFAULT_WEIGHTS
    assert_footer_is_true(weights, ranked_rings or ())
    genes = list(genes)
    if not genes:
        raise ValueError("no genes to render")

    rings = [r for r in RING_SPECS if r.key in cells_by_ring]
    cmap = plt.get_cmap(VALUE_CMAP)
    norms = {r.key: _norm_for(r.key, cells_by_ring[r.key]) for r in rings}

    # UX B1: pycirclize raises "Too large sector space size is set!!" at
    # n >= 181 with space=2, which reached the user as a raw traceback after
    # ~13 minutes of fetching. `MAX_GENES` stops it before any network work;
    # this stops a direct render() call with a message that says what happened.
    space = 2 if len(genes) > 2 else 8
    if len(genes) * space >= 360:
        raise ValueError(
            f"{len(genes)} genes x {space} degrees of spacing exceeds the 360 degrees of a "
            f"circle, so the figure cannot be drawn. The input cap is {MAX_GENES} genes "
            f"(config.MAX_GENES); above about 60 spokes the figure is unreadable anyway."
        )
    circos = Circos({g: 1 for g in genes}, space=space)

    # r_inner is the radius of the hole in the middle. It holds the counts, the
    # dropped-gene names (UX A3) and the "not validated" caveat, and v1's 33 was
    # too small for them: on the broken fixture the counts line ran straight out
    # over the coloured arcs. Enlarged, and then the block is fitted to it and
    # the fit asserted.
    r_outer, r_inner = 97.0, 46.0
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
        size = lay.label_size * (0.82 if len(genes) > 18 else 1.0)
        sector.text(label, r=r_outer + 3, size=size,
                    orientation="vertical", color="#111111")

    fig = circos.plotfig(figsize=lay.figsize, dpi=100)
    ax = fig.axes[0]
    # pycirclize leaves a tight layout engine attached, which re-lays the axes
    # out at draw time and silently undoes `set_position`. With it attached the
    # circle was 30% larger than the box asked for, which is what put the footer
    # under the bottom spoke labels (UX A6).
    fig.set_layout_engine("none")
    # pycirclize gives the polar axes the whole figure. Shrink it so the ring
    # index on the left and the methods stamps at the bottom have somewhere to
    # live that is not on top of the data. UX A6 measures the result rather than
    # trusting these numbers -- see `assert_no_text_over_the_rings`.
    ax.set_position(lay.axes_box)

    centre_blocks = _draw_centre(ax, region, len(genes), input_count, rendered_count,
                                 ledger_counts, dropped_names, lay)
    text_blocks = []
    text_blocks += _draw_legend(fig, rings, norms, cmap, lay)
    text_blocks += _draw_stamps(fig, weights, region, subtitle, provenance,
                                cells_by_ring, gene_flags, lay)
    if invariant_failures:
        _draw_banner(fig, invariant_failures)
    if run_conditions and lay.draw_conditions:
        _draw_conditions(fig, run_conditions)

    # UX A6, measured in the artefact. The ring key's longest line ran to x=429
    # while the circle's left edge at that height was x=262, so the key
    # overprinted the coloured arcs; and the footer block occupied y=983-1028
    # with `CDKN2A (k=8) *` sitting at y=995 inside it. Both are geometry, so
    # both are checked geometrically instead of by eye.
    assert_no_text_over_the_rings(fig, ax, text_blocks)
    # The innermost ring's inner edge, as a fraction of the plotted radius.
    hole = (r_outer - (len(rings) - 1) * band - band * 0.86) / (r_outer + 3)
    fit_centre_to_the_hole(fig, ax, centre_blocks, hole)
    assert_centre_fits_the_hole(fig, ax, centre_blocks, hole)

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    # `bbox_inches=None` is not enough: matplotlib resolves a None kwarg from
    # the rcParam, which pycirclize has already set to "tight". The canvas is now
    # exactly what the Layout declares.
    with plt.rc_context(SAVE_RC):
        fig.savefig(out_svg, format="svg")
    if out_png is not None:
        # Convenience only. SVG plus the CSV is the export that matters for v1
        # (PLAN §5); this exists so a run can be eyeballed without a browser.
        with plt.rc_context(SAVE_RC):
            fig.savefig(out_png, format="png", dpi=110)
    plt.close(fig)
    return FigureResult(out_svg, csv_rows, cell_ids, len(cell_ids),
                        caption=caption(rings, norms, weights, region,
                                        provenance, gene_flags, dropped_names))


def _wrap(names, per_line: int = 5) -> str:
    names = list(names)
    return "\n".join(
        ", ".join(names[i:i + per_line]) for i in range(0, len(names), per_line)
    )


def _draw_centre(ax, region, n_genes, input_count, rendered_count, ledger_counts,
                 dropped_names: Sequence[str] = (), lay: "Layout" = None) -> list:
    dropped = input_count - rendered_count
    parts = [f"{input_count} genes in", f"{rendered_count} rendered", f"{dropped} not shown"]
    # Wrapped and capped: this line sits in the hole at the centre of the rings
    # and a long one runs straight out over the arcs.
    cats = sorted(ledger_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown_cats = [f"{v} {k}" for k, v in cats[:4]]
    if len(cats) > 4:
        shown_cats.append(f"+{len(cats) - 4} more reasons")
    detail = _wrap(shown_cats, 2) or "\u2014"
    k = (lay.centre_scale if lay else 1.0)
    out = [
        ax.text(0.5, 0.575, "targetring v2", ha="center", va="center", size=15 * k,
                weight="bold", transform=ax.transAxes),
        ax.text(0.5, 0.542, region, ha="center", va="center", size=11 * k,
                transform=ax.transAxes, color="#333333"),
        ax.text(0.5, 0.512, _wrap(parts, 2), ha="center", va="center", size=9.5 * k,
                transform=ax.transAxes, color="#111111"),
    ]
    y0 = 0.492
    if lay is None or lay.centre_detail:
        out.append(ax.text(0.5, y0, detail, ha="center", va="top", size=6.8 * k,
                           transform=ax.transAxes, color="#555555"))
        y0 -= 0.016 * len(detail.split("\n")) + 0.008

    # UX A3. v1 printed counts by category only, so the slide could not answer
    # "where's TP53?" and neither could the CSV beside it. On the broken fixture
    # more than half the input vanished and the artefact was silent about which
    # half. The names now travel with the figure.
    y = y0
    if dropped_names and lay is not None and not lay.centre_detail:
        out.append(ax.text(0.5, y, f"the {len(dropped_names)} not shown are named "
                                   f"in the caption", ha="center", va="top",
                           size=7.5 * k, transform=ax.transAxes, color="#8a4b00"))
        y -= 0.024
    elif dropped_names:
        shown = list(dropped_names)[:9]
        more = len(dropped_names) - len(shown)
        text = _wrap(shown, 3) + (f"\n\u2026 and {more} more" if more else "")
        out.append(ax.text(0.5, y, "not shown: " + text, ha="center", va="top",
                           size=6.5 * k, transform=ax.transAxes, color="#8a4b00",
                           family="monospace"))
        n_lines = 1 + (len(shown) - 1) // 3 + (1 if more else 0)
        y -= 0.015 * n_lines + 0.008
    out.append(ax.text(0.5, y, "ranking is provisional and not validated",
                       ha="center", va="top", size=7.5 * k, transform=ax.transAxes,
                       color="#8a4b00", style="italic"))
    # The centre block lives in the hole at the middle of the rings by design,
    # so it is checked against the hole rather than against the circle.
    return out


def _draw_colourbar(fig, cmap, lay) -> None:
    """UX A5. v1 had no colour bar anywhere.

    Nothing told a reader whether purple or yellow was the high end, and — worse
    — because `_norm_for` min-max normalises within each ring, *every* ring
    always contains a full-saturation yellow and a full purple whatever the
    underlying values are. The natural reading of a Circos plot is radial, down
    a spoke, and that reading is invalid here: a gene brightest on literature
    volume looks exactly as good as a gene brightest on druggability. The bar
    says so, in the place the reader looks to decode a colour.
    """
    x = lay.cbar_x
    cax = fig.add_axes((x, 0.40, 0.012, 0.30))
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0, 1), cmap=cmap), cax=cax)
    cb.set_ticks([0, 1])
    cb.set_ticklabels(["low", "high"])
    cb.ax.tick_params(labelsize=7.5 if lay.name == "audit" else 9)
    cb.outline.set_linewidth(0.6)
    fig.text(x + lay.cbar_gap, 0.705,
             "RESCALED WITHIN EACH RING\nthe same colour on two rings\n"
             "means nothing in common",
             fontsize=7 if lay.name == "audit" else 8.5, va="top", ha="left",
             color="#8a4b00", weight="bold")


def _draw_legend(fig, rings, norms, cmap, lay) -> list:
    handles = [
        Patch(facecolor=s["fc"], hatch=s["hatch"], edgecolor=s["ec"], label=s["label"])
        for s in STATE_STYLE.values()
    ]
    handles.append(Patch(facecolor=cmap(0.85), edgecolor="#ffffff",
                         label="value (scale is per-ring — see the bar)"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.006, 0.992),
               fontsize=7.5 if lay.name == "audit" else 9,
               frameon=True, title="cell states", title_fontsize=8)
    _draw_colourbar(fig, cmap, lay)

    if not lay.draw_key:
        # UX A4: on the slide render the ring key goes into the caption.
        return []

    # UX A6 + the UX review's "noise" list: the API field lists
    # (`hasLigand, hasPocket, hasSmallMoleculeBinder, …`) are provenance, not a
    # reading aid, and they are what made this block 393 px wide and overprint
    # the coloured arcs. They live in the manifest and the caption now. The
    # numbered ring names and their ranges stay, set in the same sans as
    # everything else rather than monospace.
    lines = []
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = f"[{n.vmin:.3g}, {n.vmax:.3g}]" if n else "no values"
        lines.append(f"{i}. {_short(spec.label)}  {rng}")
    return [fig.text(0.006, 0.80,
                     "rings, outermost first\n(sources in the caption and manifest)\n\n"
                     + "\n".join(lines),
                     fontsize=7.4, va="top", ha="left", color="#222222")]


def _short(label: str, limit: int = 34) -> str:
    return label if len(label) <= limit else label[:limit - 1].rstrip(" ,-") + "\u2026"


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
                 gene_flags=None, lay=None) -> list:
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

    out = []
    if lay is not None and not lay.draw_footer:
        # UX A4 / the UX review's "noise" list: at 3.7 pt on a slide none of the
        # six footer lines is read by anyone. One line does the honest work and
        # the rest go into the caption you paste underneath.
        out.append(fig.text(
            0.5, 0.008,
            "ranking is provisional and not validated \u00b7 rings are scaled "
            "independently of one another \u00b7 see the caption",
            fontsize=10, ha="center", va="bottom", color="#8a4b00"))
    else:
        out.append(fig.text(0.5, 0.008, "\n".join(notes),
                            fontsize=(lay.footer_size if lay else 7.5),
                            ha="center", va="bottom", color="#222222"))
    if provenance:
        out.append(fig.text(0.995, 0.995, provenance,
                            fontsize=6 if (lay is None or lay.draw_key) else 8,
                            ha="right", va="top", color="#555555", family="monospace"))
    return out


def caption(rings, norms, weights, region, provenance, gene_flags=None,
            dropped_names=()) -> str:
    """UX A4. Everything the slide render drops, as text to paste underneath.

    The full audit stamps stay on the audit render, which is the right artefact
    for the morning review. This is what makes the slide render honest anyway.
    """
    lines = [f"targetring \u2014 {region}. {provenance}", ""]
    lines.append("Rings, outermost first (scale is per-ring; a colour means nothing "
                 "between two rings):")
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = f"[{n.vmin:.3g}, {n.vmax:.3g}]" if n else "no values"
        lines.append(f"  {i}. {spec.label} {rng} \u2014 {spec.source}")
    w = ", ".join(f"{k}={v:g}" for k, v in sorted(weights.items()))
    excluded = ", ".join(sorted(EXCLUDED_FROM_SCORE))
    lines += [
        "",
        "Headline (\u25c6) = the Pareto non-dominated set over druggability, safety, "
        "regional specificity and furthest clinical phase. A gene needs a real value on "
        "all four to be eligible; genes with fewer are neither in the set nor able to "
        "remove anything from it. Membership is a property of the submitted list, not "
        "of the target.",
        f"Weighted score is a labelled convenience, not the headline. Default weights "
        f"(provisional, not signed off): {w}. Excluded from every score: {excluded}.",
        "Pipeline: furthest phase counts only drugs indicated for a disease with human "
        "genetic evidence linking it to this target. A drug that hits the target for an "
        "unrelated indication sets no phase.",
        "Missing is never imputed. k = rings with a real measurement for that gene. "
        "* = this gene's top-10 place does not survive reweighting.",
        "This figure is internally consistent and auditable. It is not a validated "
        "ranking.",
    ]
    if dropped_names:
        lines += ["", f"Not shown ({len(dropped_names)}), every one named here and in "
                      f"ledger.csv with its reason:",
                  "  " + ", ".join(dropped_names)]
    if gene_flags:
        lines.append(
            "\u26a0 = Open Targets records a cardiac-conduction, cardiotoxicity or "
            f"mortality safety liability for this target ({', '.join(sorted(gene_flags))}). "
            "The clinical rings are a pipeline statement, never a safety one.")
    return "\n".join(lines)


def _hole_geometry(fig, ax, hole_fraction):
    fig.canvas.draw()
    box = ax.get_window_extent()
    return (
        fig.canvas.get_renderer(),
        (box.x0 + box.x1) / 2,
        (box.y0 + box.y1) / 2,
        min(box.width, box.height) / 2 * hole_fraction,
    )


def _reach(t, r, cx, cy) -> float:
    bb = t.get_window_extent(renderer=r)
    return max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
               for x, y in ((bb.x0, bb.y0), (bb.x0, bb.y1), (bb.x1, bb.y0), (bb.x1, bb.y1)))


CENTRE_MIN_SCALE = 0.62


def fit_centre_to_the_hole(fig, ax, blocks, hole_fraction: float) -> float:
    """Shrink the centre block until it fits in the hole, or hit the floor.

    The hole's size in pixels depends on the layout and on how many rings there
    are, and the block's size depends on the input -- how many drop categories
    the table produced, how many gene names are listed. Neither is knowable when
    the font sizes are chosen, so they are measured and fitted.

    The floor exists because shrinking without one would reproduce UX A4: text
    that is technically present and too small to read. If the floor is reached
    and the block still does not fit, `assert_centre_fits_the_hole` raises --
    which is the right outcome, because the alternative is a figure with the
    caveat printed across the data.
    """
    if not blocks:
        return 1.0
    original = [t.get_fontsize() for t in blocks]
    scale = 1.0
    for _ in range(14):
        r, cx, cy, hole = _hole_geometry(fig, ax, hole_fraction)
        if max(_reach(t, r, cx, cy) for t in blocks) <= hole:
            return scale
        scale *= 0.93
        if scale < CENTRE_MIN_SCALE:
            scale /= 0.93
            break
        for t, size in zip(blocks, original):
            t.set_fontsize(size * scale)
    return scale


def assert_centre_fits_the_hole(fig, ax, blocks, hole_fraction: float) -> None:
    """The counts, the caveat and the dropped-gene names live in the hole.

    A long line there runs straight out over the coloured arcs, which is the
    same defect as UX A6 from the inside. `ledger_counts` is user-driven -- the
    broken fixture produces seven categories -- so the width cannot be assumed.
    """
    if not blocks:
        return
    r, cx, cy, hole_r = _hole_geometry(fig, ax, hole_fraction)
    offenders = [
        f"{t.get_text()[:44]!r} reaches {_reach(t, r, cx, cy):.0f}px from the centre, "
        f"past the {hole_r:.0f}px hole"
        for t in blocks if _reach(t, r, cx, cy) > hole_r
    ]
    if offenders:
        raise AssertionError(
            "centre text spills out of the hole and over the rings: " + "; ".join(offenders))


def assert_no_text_over_the_rings(fig, ax, blocks) -> None:
    """UX A6, checked geometrically rather than by eye.

    Measured in v1's SVG: the monospace ring key started at x=36 and its longest
    line ran to x=429, while the circle's left edge at that height was x=262, so
    the key overprinted the coloured arcs. The footer block occupied
    y=983.3-1028.3 with the `CDKN2A (k=8) *` label at y=995.4 inside it.

    The axes box is the square that bounds the circle, so a text block may sit
    in its corner without touching any data. The check is therefore against the
    inscribed circle, not the box.
    """
    if not blocks:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    box = ax.get_window_extent()
    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    radius = min(box.width, box.height) / 2
    offenders = []
    for t in blocks:
        bb = t.get_window_extent(renderer=r)
        # Closest point of the text's bbox to the circle centre.
        dx = max(bb.x0 - cx, 0, cx - bb.x1)
        dy = max(bb.y0 - cy, 0, cy - bb.y1)
        if (dx * dx + dy * dy) ** 0.5 < radius:
            offenders.append(
                f"{t.get_text()[:40]!r} at ({bb.x0:.0f},{bb.y0:.0f})-({bb.x1:.0f},{bb.y1:.0f}) "
                f"overlaps the rings (centre {cx:.0f},{cy:.0f} radius {radius:.0f})")
    if offenders:
        raise AssertionError(
            "text is drawn over the coloured arcs (UX A6): " + "; ".join(offenders))


def _draw_banner(fig, failures: Sequence[str]) -> None:
    """An invariant that was checked and did not hold. The data is wrong.

    This is the loud one, and it is drawn across the rings on purpose: a figure
    whose invariants are violated must not be readable. It fires on FAIL only --
    see `_draw_conditions` and UX A1 for why.
    """
    text = "INVARIANT FAILURE — DO NOT TRUST THIS FIGURE\n" + "\n".join(
        f"• {f}" for f in list(failures)[:6]
    )
    fig.text(0.5, 0.5, text, fontsize=15, ha="center", va="center", color="#ffffff",
             weight="bold", zorder=1000,
             bbox={"facecolor": "#b00020", "alpha": 0.95, "pad": 16, "edgecolor": "#000000"})


def _draw_conditions(fig, conditions: Sequence[str]) -> None:
    """UX A1 / COUNCIL V3 / V11 -- what could not be checked, and what it affects.

    A source being down and an invariant being violated are different facts and
    must not render identically. v1 collapsed them: a Europe PMC outage -- a
    source that sets no ring value at all, as the app's own amber note said
    directly underneath -- stamped INVARIANT FAILURE / DO NOT TRUST THIS FIGURE
    across the middle of the export. Cry wolf twice and the banner stops working
    the day it is real.

    So this notice differs in every channel a reader uses: amber not red,
    "NOT CHECKED" not "FAILURE", below the rings rather than across them, and
    each line carries its own consequence.
    """
    text = (
        "RUN CONDITIONS - not an invariant failure. These checks could not be run; "
        "each line says what that affects.\n"
        + "\n".join(f"\u2022 {c}" for c in list(conditions)[:6])
    )
    fig.text(0.5, 0.125, text, fontsize=7.5, ha="center", va="top", color="#4a3000",
             zorder=900,
             bbox={"facecolor": "#ffe9b8", "alpha": 0.97, "pad": 7,
                   "edgecolor": "#c98a00", "linewidth": 1.2})


__all__ = ["render", "RING_SPECS", "STATE_STYLE", "VALUE_CMAP", "FigureResult",
           "assert_fills_disjoint", "assert_footer_is_true", "colormap_hexes",
           "assert_no_text_over_the_rings", "assert_centre_fits_the_hole", "fit_centre_to_the_hole", "caption", "Layout", "LAYOUTS", "AUDIT", "SLIDE"]
