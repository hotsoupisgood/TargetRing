"""The Circos figure: pycirclize -> SVG.

Design rules that are not cosmetic:

* `no_data` is hatched grey drawn from a palette that is *provably* disjoint
  from the value colormap. Provably means checked, twice: numerically before
  drawing (`assert_fills_disjoint`) and again in the rendered SVG by invariant 2.
* Every cell carries an SVG `id` (`cell|<ring>|<index>|<gene>`). That is what
  makes cell-count identity and label correspondence checkable in the artefact
  rather than only in the code that produced it (QA S1 checks 1 and 4).
* k, the gene and dropped counts, the spoke-ordering rule, any active threshold
  filter, the region and the denominator tissue set are stamped on the figure,
  because the morning review is a screenshot (QA B7 item 4).
* A failed invariant draws a banner across the figure. The overnight run's job
  is to be honest, not to produce output (QA §5 item 8).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_hex, to_rgb  # noqa: E402
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

from .adapters.opentargets import SOURCE_LABEL as ACTIVITY_SOURCE_LABEL  # noqa: E402
from .adapters.pharos import TDL_MAX  # noqa: E402
from .config import MAX_GENES  # noqa: E402
from .order import EXTERNAL_RING_COUNT, GeneStats  # noqa: E402
from .states import Cell  # noqa: E402

VALUE_CMAP = "viridis"

# The value ramp is viridis *truncated*, and the truncation is the fix for the
# greyscale finding in the UX review, not a taste decision.
#
# Measured: full viridis spans relative luminance 0.019 to 0.783. The `no_data`
# fill #b3b3b3 sits at 0.4508 and viridis step 192/255 (#5ec962, a real value at
# ~0.75 of the scale) at 0.4503 -- a greyscale contrast ratio of 1.001,
# identical, with 23 of 256 steps within 0.05 luminance of the grey. PLAN §6.2's
# "provably distinct from every value colour" was therefore true only of the
# colour render, and the surviving cue -- the hatch -- does not survive a washed
# out projector, a compressed raster paste into PowerPoint, or a photocopy. In
# any of those, "never measured here" read as a good score.
#
# Three reserved fills need three luminance bands outside the ramp, and a
# full-range ramp leaves room for at most two. Truncating to [0.16, 0.92] puts
# the ramp in [0.056, 0.663] and leaves black (0.0), near-white (0.807) and
# white (1.0) each clear of it by >= 0.055. It costs the brightest yellow and
# the darkest purple; it keeps the ordering, the perceptual uniformity and the
# deuteranopia safety the review liked. Asserted by `assert_greyscale_disjoint`.
CMAP_STOPS = (0.16, 0.92)
GREYSCALE_MIN_DELTA = 0.05

# v2.5. Words this figure must not use about itself any more, checked at draw
# time by `assert_footer_is_true`. Ranking is gone -- the Pareto set, the
# weighted score, the weights, eligibility and the stability flag with it -- and
# a stamp or a caption line that still promises one would be a false claim about
# the figure's own method, which is the defect COUNCIL V5 is about. A leftover
# sentence is as wrong as leftover code, and cheaper to leave behind.
RETIRED_CLAIM_WORDS: tuple[str, ...] = (
    "pareto", "non-dominated", "weighted score", "default weights",
    "eligible", "headline", "reweighting", "median available rank",
)

# See the comment at the rect() call: anything but exactly 1.0 keeps each cell a
# separate SVG element with its own id.
CELL_ZORDER = 2.0

# UX A6 / v2.3 item 1. `assert_no_text_over_the_rings` tested for strict overlap,
# so a 0.3 px near-miss passed and the same geometry failed on the next gene set
# -- which is exactly what happened when a twelfth ring changed the spoke order
# and put `⚠ KCNH2 (k=8/9) *` at the bottom of the slide render (measured: label
# bottom 21.2 px, footer top 21.5 px, on a 900 px canvas). A required clearance
# turns "we got away with it" into a failure you can see coming.
LABEL_CLEARANCE_PX = 4.0

# Deliberately neutral / achromatic. Viridis is saturated across its whole
# range, so these cannot collide with a value colour -- and that claim is
# asserted, not assumed.
STATE_STYLE: dict[str, dict[str, Any]] = {
    "no_data": {"fc": "#e8e8e8", "hatch": "////", "ec": "#4d4d4d",
                "label": "no data — never measured here"},
    "not_applicable": {"fc": "#ffffff", "hatch": "....", "ec": "#808080",
                       "label": "not applicable to this gene"},
    "failed": {"fc": "#000000", "hatch": "xxxx", "ec": "#ff0000",
               "label": "call FAILED — not the same as absent"},
}


@dataclass(frozen=True)
class RingSpec:
    """COUNCIL V6, V12, V17: how a ring is scaled is part of what it means.

    v1 min-max normalised every ring within the submitted set. On the
    regional-specificity ratio, which spans five orders of magnitude, one
    tissue-restricted gene collapsed the ring: 9 of 14 cells rendered as the
    identical darkest purple over a 41-fold range of ratios, so HMGCR at 0.93 --
    exactly average for liver -- was indistinguishable from KCNH2 at 0.023. On
    the k ring the same rule spent the entire colormap on a 7-to-9 difference.
    And on bounded rings a colour meant something different between two runs,
    because the ends moved with whatever genes were submitted.

    So:
      scale   "log"        for ratio rings spanning orders of magnitude
              "diverging"  for signed rings, anchored at zero (V17)
              "linear"     otherwise
      domain  a fixed (lo, hi) where the measure is bounded, so a colour means
              the same thing in every run; None to fall back to the submitted
              set's range, which is then stated in the key.
      categorical  a {value: colour} palette drawn outside the value colormap
              (V12), for a ring that is not continuous at all.
    """

    key: str
    label: str
    source: str
    scale: str = "linear"
    domain: tuple[float, float] | None = None
    categorical: dict[float, str] | None = None
    category_labels: dict[float, str] | None = None


# PLAN.md §2. Order is outermost-first.
# COUNCIL V12. Three discrete states rendered on a continuous colormap, whose
# colour depended on which *other* genes were submitted: in the liver run only
# {0, 1} occurred, so "dormant" rendered #440154 -- the same darkest purple that
# meant "0 drugs" on the phase ring one band away. Submit one gene with a
# stopped-for-cause history and the same dormant gene's cell turned mid-teal
# with no change to its data. A fixed palette, outside the value colormap.
ACTIVITY_PALETTE: dict[float, str] = {
    -1.0: "#8c2d04",   # stopped for cause
    0.0: "#fdbe6f",    # dormant
    1.0: "#08519c",    # active
}
ACTIVITY_LABELS: dict[float, str] = {
    # v2.4 restores the categorised state. v2.2 downgraded this to "stopped
    # early — cause NOT categorised by CT.gov" because the per-drug
    # ClinicalTrials.gov hop could only see the sponsor's free text. Open
    # Targets categorises the same stops and always did, so the ring says what
    # it means again: -1.0 is a stop Open Targets categorised Negative or
    # Safety_Sideeffects. COUNCIL D4's measurement is why the distinction
    # matters -- roughly 7 of 8 BRAF terminations say nothing about the target
    # (Insufficient_Enrollment 108, Business_Administrative 51 against Negative
    # 17 and Safety_Sideeffects 13), and those land on 0.0, not here.
    -1.0: "stopped for cause — OT category Negative / Safety_Sideeffects",
    0.0: "dormant — drugs exist, no live trial, no for-cause stop",
    1.0: "live trial — recruiting or ongoing",
}

# COUNCIL V4 / V2-REPORT §2.4, and QA-v2-retest §5. The four safety keys buy
# comparability across genes, not the ability to tell a clean target from an
# unstudied one: Open Targets not holding a safety-event record is a fact about
# Open Targets. This string is the caveat that says so, and it is checked onto
# both renders rather than trusted to a label that something downstream might
# truncate.
SAFETY_RING_CAVEAT = "NOT evidence of safety"

RING_SPECS: tuple[RingSpec, ...] = (
    # V17: logFC is signed and had no zero anchor, so GFAP at -3.3 rendered
    # darkest and TTR at +3.1 brightest -- a strongly down-regulated gene read
    # as "worst" on the one ring where dark does not mean worst.
    RingSpec("logfc", "logFC (yours)", "submitted DE table", scale="diverging"),
    RingSpec("neg_log10_padj", "-log10 padj (yours)", "submitted DE table"),
    # v2.3 item 1 / COUNCIL M7/V8. One ring became two, and the labels say which
    # claim each one makes: the dense three-key ring is not the sparse probe key,
    # and a hatched probe cell must not take tractability down with it.
    RingSpec("tractable", "Tractability (family suggests druggable)",
             "OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder — "
             "3 dense keys, present for every target with a prioritisation record",
             domain=(0.0, 1.0)),
    RingSpec("chemical_probe", "Chemical probe available (prior tool-compound effort)",
             "OT prioritisation: hasHighQualityChemicalProbes — SPARSE. Explicit 0.0 = "
             "looked, no probe; key absent = no_data (hatched)",
             domain=(0.0, 1.0)),
    RingSpec("safety", "Safety flags & constraint — NOT evidence of safety",
             "OT prioritisation: hasSafetyEvent, geneEssentiality, geneticConstraint, isCancerDriverGene",
             domain=(-1.0, 1.0)),
    # V6: a ratio over five orders of magnitude, so log10. Deliberately NOT a
    # fixed domain: the ratio is unbounded above (TTR reaches 5,204x in liver)
    # and a fixed domain would clip TTR, APOB and SERPINA1 to the same top
    # colour -- reintroducing at the top end exactly the collapse V6 is about.
    # The log transform is what fixes the collapse; the range is printed in the
    # key and in the caption.
    RingSpec("regional_specificity", "Regional specificity (log10 ratio)",
             "GTEx v8 median TPM ratio, log10, floor applied", scale="log"),
    RingSpec("clinical_phase", "Pipeline: furthest phase (attributable indications only)",
             "OT drugAndClinicalCandidates, gated on OT target-disease genetic evidence",
             domain=(0.0, 5.0)),
    # Puck's hard condition from v2.2 item 2 stands: the ring must state which
    # source it counted, on the figure. v2.4 changes the answer -- the per-drug
    # ClinicalTrials.gov hop is retired and these rows come from Open Targets'
    # own `clinicalReports`, in the same query as every other OT ring -- so the
    # label says that, and the cell detail names the `source` values actually
    # seen on the gene. The source is in the ring's own *label*, which reaches
    # the numbered key on the audit render and, through the categorical legend
    # entries, both renders.
    RingSpec("clinical_activity", f"Pipeline: current activity ({ACTIVITY_SOURCE_LABEL})",
             "OT drugAndClinicalCandidates.rows[].clinicalReports, selected by carrying a "
             "trialOverallStatus — NOT by a source literal (the AACT->ClinicalTrials.gov "
             "relabelling of 2026-09-24 is what a literal filter missed)",
             categorical=ACTIVITY_PALETTE, category_labels=ACTIVITY_LABELS),
    # v2.2 item 4 / COUNCIL S1. An explicit ordinal, never inferred.
    RingSpec("tdl", "Target development level (Pharos TDL — knowledge, not a pocket)",
             "Pharos GraphQL target(q:{sym}).tdl; Tdark 0 / Tbio 1 / Tchem 2 / Tclin 3",
             domain=(0.0, float(TDL_MAX))),
    # V9: reduced to three states, so there is no count left to scale.
    # COUNCIL V15: the source is OT `mousePhenotypes` -- mouse only. PLAN §2
    # retired the rodent source citing a QA finding about IMPC, but COUNCIL S6
    # had recommended Alliance of Genome Resources (MGI *and* RGD), which is
    # why the ring was called "rodent" in the first place. A finding about one
    # source was used to retire a different one. Renamed to what it is;
    # revisiting Alliance is still open.
    RingSpec("rodent", "Mouse phenotype lead (3 states)",
             "OT mousePhenotypes, 'no abnormal phenotype' excluded; mouse only, not rat",
             domain=(0.0, 1.0)),
    RingSpec("literature", "Literature volume", "PubTator3, gene-id resolved, log10(1+n)"),
    # V13: k over external rings only, on a fixed 0..K domain.
    RingSpec("completeness", "k — external rings with a measurement",
             "derived; maximum is the number of external rings",
             domain=(0.0, float(EXTERNAL_RING_COUNT))),
)


def value_cmap():
    """viridis over CMAP_STOPS. See the note at CMAP_STOPS for why it is cut."""
    base = plt.get_cmap(VALUE_CMAP)
    lo, hi = CMAP_STOPS
    return LinearSegmentedColormap.from_list(
        f"{VALUE_CMAP}_{lo}_{hi}",
        [base(lo + (hi - lo) * i / 255) for i in range(256)],
    )


def colormap_hexes(n: int = 256) -> set[str]:
    cmap = value_cmap()
    return {to_hex(cmap(i / (n - 1))) for i in range(n)}


def relative_luminance(colour) -> float:
    """WCAG relative luminance — what a colour becomes in greyscale."""
    def channel(u: float) -> float:
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = to_rgb(colour)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def assert_greyscale_disjoint(min_delta: float = GREYSCALE_MIN_DELTA) -> None:
    """PLAN §6.2, extended to the render people actually project and photocopy.

    `assert_fills_disjoint` proves the reserved fills are not *colours* the
    colormap produces. That proof does not survive desaturation, and the UX
    review measured the consequence: no_data and a real mid-scale value were
    identical in greyscale at a contrast ratio of 1.001. This asserts the same
    guarantee in luminance, against the whole ramp and between the states.
    """
    ramp = [relative_luminance(value_cmap()(i / 255)) for i in range(256)]
    states = {name: relative_luminance(st["fc"]) for name, st in STATE_STYLE.items()}
    for name, l in states.items():
        near = min(ramp, key=lambda x: abs(x - l))
        if abs(near - l) < min_delta:
            raise AssertionError(
                f"the {name!r} fill {STATE_STYLE[name]['fc']} has relative luminance "
                f"{l:.4f}, within {abs(near - l):.4f} of a value colour at {near:.4f}. "
                f"In greyscale -- a projector, a compressed paste, a photocopy -- "
                f"{name!r} would be indistinguishable from a real measurement. "
                f"Needs a gap of {min_delta}."
            )
    for a, b in itertools.combinations(states, 2):
        if abs(states[a] - states[b]) < min_delta:
            raise AssertionError(
                f"the {a!r} and {b!r} fills are {abs(states[a] - states[b]):.4f} apart in "
                f"luminance; they collapse into each other in greyscale"
            )


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


def assert_within_domain(spec: RingSpec, cells: dict[str, Cell]) -> None:
    """A value outside a declared fixed domain would be silently clipped.

    A clipped cell is drawn in a colour that means a different number, which is
    the plausible-wrong-figure failure mode. Fixed domains are declared only
    where the measure is provably bounded -- a mean of keys in [-1, 1], a stage
    ordinal, a count of rings -- so a value outside one means the source or the
    reduction changed shape, and that has to be loud.
    """
    if spec.domain is None:
        return
    lo, hi = spec.domain
    for gene, c in cells.items():
        if c.state != "value":
            continue
        v = _scaled(spec, c.value)
        if not (lo - 1e-9 <= v <= hi + 1e-9):
            raise AssertionError(
                f"{gene} has {spec.key}={c.value!r} (scaled {v:.4g}), outside this ring's "
                f"declared domain [{lo:g}, {hi:g}]. It would be clipped and drawn in a "
                f"colour that means a different number. The domain is declared because the "
                f"measure is bounded, so this means the reduction changed shape."
            )


def _scaled(spec: RingSpec, v: float) -> float:
    if spec.scale == "log":
        return math.log10(max(v, 1e-6))
    return v


def _norm_for(spec: RingSpec, cells: dict[str, Cell]) -> Normalize | None:
    """COUNCIL V6. A fixed domain where the measure has one, so a colour means
    the same thing between two runs; the submitted set's range otherwise, and
    then the range is printed in the key."""
    if spec.categorical:
        return None
    if spec.domain is not None:
        return Normalize(vmin=spec.domain[0], vmax=spec.domain[1])
    vals = [_scaled(spec, c.value) for c in cells.values() if c.state == "value"]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if spec.scale == "diverging":
        # V17: anchored on zero, so up- and down-regulation are symmetric and
        # the midpoint of the ramp is "no change".
        m = max(abs(lo), abs(hi)) or 0.5
        return Normalize(vmin=-m, vmax=m)
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


AUDIT = Layout("audit", (17.5, 14.0), 8.0, (0.250, 0.205, 0.53, 0.755),
               True, True, 7.5, 1.0, True, 0.872, 0.019, True)
# 16:9. A 9 in canvas fitted to a 7.5 in slide is a 0.83 scale, so an 11.5 pt
# label lands at 9.6 pt -- against v1's 3.9, where an 18.3 x 15.2 in canvas on
# the same slide was a 0.49 scale. The key and the caveats leave the canvas
# entirely and go into caption.txt.
# v2.3 item 1: the width was 0.38, which made the plotted square exactly 608 px
# on a 900 px canvas and left the bottom spoke label ending 0.3 px above the
# footer -- i.e. no margin at all, and a spoke reorder was enough to collide
# (docs/V2-REPORT.md §v2.2 "Still open" 6). At 0.36 the square is 576 px, which
# buys 16 px top and bottom. The circle is ~5% smaller on the slide and every
# label still reads at 11.5 pt; `LABEL_CLEARANCE_PX` is what stops it drifting
# back.
SLIDE = Layout("slide", (16.0, 9.0), 11.5, (0.295, 0.075, 0.36, 0.86),
               False, False, 0.0, 1.15, False, 0.838, 0.020, False)
LAYOUTS = {"audit": AUDIT, "slide": SLIDE}


@dataclass
class FigureResult:
    svg_path: Path
    csv_rows: list[dict[str, Any]]
    cell_ids: list[str]
    n_cells: int
    caption: str = ""
    # v2.5 item 2. The sentences the figure printed about its own spoke order and
    # its own active filters, returned so invariant 7 can check them against what
    # the run actually did rather than trusting the renderer.
    ordering_note: str = ""
    filter_notes: list[str] = field(default_factory=list)


def render(
    *,
    genes: Sequence[str],
    cells_by_ring: dict[str, dict[str, Cell]],
    stats: dict[str, GeneStats],
    region: str,
    out_svg: Path,
    out_png: Path | None = None,
    input_count: int,
    rendered_count: int,
    ledger_counts: dict[str, int],
    dropped_names: Sequence[str] = (),
    invariant_failures: Sequence[str] = (),
    run_conditions: Sequence[str] = (),
    ordering_note: str = "",
    filter_notes: Sequence[str] = (),
    gene_flags: dict[str, list[str]] | None = None,
    subtitle: str = "",
    provenance: str = "",
    layout: str = "audit",
) -> FigureResult:
    assert_fills_disjoint()
    assert_greyscale_disjoint()
    lay = LAYOUTS[layout]
    filter_notes = list(filter_notes)
    genes = list(genes)
    if not genes:
        raise ValueError("no genes to render")

    rings = [r for r in RING_SPECS if r.key in cells_by_ring]
    cmap = value_cmap()
    for r in rings:
        assert_within_domain(r, cells_by_ring[r.key])
    norms = {r.key: _norm_for(r, cells_by_ring[r.key]) for r in rings}

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

            if cell.state == "value" and spec.categorical:
                # V12: a fixed three-colour palette, outside the value colormap,
                # so the colour does not move with the rest of the submission.
                fill = spec.categorical.get(
                    round(cell.value, 6), spec.categorical[min(spec.categorical)])
                style = {"fc": fill, "ec": "#ffffff", "lw": 0.25}
            elif cell.state == "value" and norm is not None:
                fill = to_hex(cmap(norm(_scaled(spec, cell.value))))
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

    # v2.5 item 1. No diamond, no stability asterisk: there is no headline set to
    # mark membership of and no reweighting to be unstable under. What is left on
    # a spoke label is the gene, its k, and the liability marker.
    for gene_i, gene in enumerate(genes):
        sector = circos.get_sector(gene)
        st = stats.get(gene)
        k = st.k if st else 0
        # COUNCIL V1 (ii). Open Targets has no anti-target axis, so hERG reads as
        # druggable, clinically validated and actively trialled while its own
        # safetyLiabilities are Torsades de Pointes and Increased Mortality. The
        # marker sits where the eye already is, next to k.
        liab = "⚠ " if gene in (gene_flags or {}) else ""
        # v2.5 item 2, rule 1. A gene kept on the figure because a threshold
        # could not be evaluated against a cell it has no data for is marked
        # here, not silently mixed in with the genes that passed the filter.
        unf = " ~" if (st and st.unfiltered_rings) else ""
        label = f"{liab}{gene} (k={k}/{EXTERNAL_RING_COUNT}){unf}"
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

    # COUNCIL V5, re-pointed by v2.5: the figure must not make a false claim
    # about its own method. Every sentence it is about to draw is checked against
    # what this render is actually doing, before any of it reaches the canvas.
    notes = _stamp_notes(ordering_note, filter_notes, region, cells_by_ring,
                         gene_flags, subtitle)
    cap = caption(rings, norms, region, provenance, ordering_note, filter_notes,
                  gene_flags, dropped_names)
    assert_footer_is_true(notes + cap.splitlines(), ordering_note, filter_notes)

    centre_blocks, next_y = _draw_centre(ax, region, len(genes), input_count,
                                         rendered_count, ledger_counts,
                                         dropped_names, lay)
    centre_blocks += _draw_ordering_and_filters(ax, ordering_note, filter_notes,
                                                stats, lay, next_y)
    text_blocks = []
    text_blocks += _draw_legend(fig, rings, norms, cmap, lay)
    text_blocks += _draw_stamps(fig, notes, provenance, lay,
                                slide_line(ordering_note, filter_notes))
    if invariant_failures:
        _draw_banner(fig, invariant_failures)
    if run_conditions and lay.draw_conditions:
        _draw_conditions(fig, run_conditions)

    # UX A6, measured in the artefact. The ring key's longest line ran to x=429
    # while the circle's left edge at that height was x=262, so the key
    # overprinted the coloured arcs; and the footer block occupied y=983-1028
    # with `CDKN2A (k=8) *` sitting at y=995 inside it. Both are geometry, so
    # both are checked geometrically instead of by eye.
    assert_text_within_canvas(fig, text_blocks)
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
                        caption=cap,
                        ordering_note=ordering_note,
                        filter_notes=list(filter_notes))


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
    # v2.5 item 1: "ranking is provisional and not validated" is gone from here,
    # because nothing is ranked. The caveat existed only to qualify the headline
    # and the score, and a caveat about a thing the figure no longer does is not
    # honesty, it is noise. What replaces it is the ordering rule, which is a
    # statement about what this circle *is*.
    #
    # The centre block lives in the hole at the middle of the rings by design, so
    # it is checked against the hole rather than against the circle. The next
    # free y goes back to the caller for the ordering and filter lines.
    return out, y


def _draw_colourbar(fig, cmap, lay) -> list:
    """UX A5. v1 had no colour bar anywhere.

    Nothing told a reader whether purple or yellow was the high end, and — worse
    — because `_norm_for` min-max normalises within each ring, *every* ring
    always contains a full-saturation yellow and a full purple whatever the
    underlying values are. The natural reading of a Circos plot is radial, down
    a spoke, and that reading is invalid here: a gene brightest on literature
    volume looks exactly as good as a gene brightest on tractability. The bar
    says so, in the place the reader looks to decode a colour.
    """
    x = lay.cbar_x
    cax = fig.add_axes((x, 0.40, 0.012, 0.30))
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0, 1), cmap=cmap), cax=cax)
    cb.set_ticks([0, 1])
    cb.set_ticklabels(["low", "high"])
    cb.ax.tick_params(labelsize=7.5 if lay.name == "audit" else 9)
    cb.outline.set_linewidth(0.6)
    # Just below the bar's top tick, so the caption does not sit on "high".
    return [fig.text(x + lay.cbar_gap, 0.688,
                     "RESCALED WITHIN EACH RING\nsame colour on two rings\n"
                     "means nothing in common",
                     fontsize=7 if lay.name == "audit" else 8.5, va="top", ha="left",
                     color="#8a4b00", weight="bold")]


def _draw_ordering_and_filters(ax, ordering_note: str, filter_notes: Sequence[str],
                               stats, lay, y: float) -> list:
    """v2.5. Two claims about the figure that a reader cannot recover by looking.

    The spoke order carries meaning -- it always did -- and an unstated ordering
    rule is the same defect as an unlabelled colour scale. And a filtered plot
    that does not say it is filtered is a plausible wrong figure: it looks like
    the whole submitted set. Both go in the hole, where the counts already are.
    """
    k = (lay.centre_scale if lay else 1.0)
    out = []
    if ordering_note:
        out.append(ax.text(0.5, y, ordering_note, ha="center", va="top", size=6.8 * k,
                           transform=ax.transAxes, color="#444444", wrap=False))
        y -= 0.020 * k
    if filter_notes:
        n_marked = sum(1 for s in (stats or {}).values() if s.unfiltered_rings)
        text = (f"FILTERED \u2014 {len(filter_notes)} active:\n" + _wrap(filter_notes, 1)
                + (f"\n~ {n_marked} gene(s) kept unfiltered: no data on a filtered ring"
                   if n_marked else ""))
        out.append(ax.text(0.5, y, text, ha="center", va="top", size=6.8 * k,
                           transform=ax.transAxes, color="#8a4b00", weight="bold"))
    return out


def _draw_legend(fig, rings, norms, cmap, lay) -> list:
    handles = [
        Patch(facecolor=s["fc"], hatch=s["hatch"], edgecolor=s["ec"], label=s["label"])
        for s in STATE_STYLE.values()
    ]
    handles.append(Patch(facecolor=cmap(0.85), edgecolor="#ffffff",
                         label="value (scale is per-ring — see the bar)"))
    # COUNCIL V12: the categorical ring is off the value colormap, so its three
    # colours need naming or they read as values.
    for spec in rings:
        if not spec.categorical:
            continue
        for v, colour in sorted(spec.categorical.items()):
            label = (spec.category_labels or {}).get(v, str(v))
            handles.append(Patch(facecolor=colour, edgecolor="#ffffff",
                                 label=f"{spec.label}: {label}"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.006, 0.992),
               fontsize=7.5 if lay.name == "audit" else 9,
               frameon=True, title="cell states", title_fontsize=8)
    blocks = _draw_colourbar(fig, cmap, lay)

    if not lay.draw_key:
        # UX A4: on the slide render the ring key goes into the caption.
        return blocks

    # UX A6 + the UX review's "noise" list: the API field lists
    # (`hasLigand, hasPocket, hasSmallMoleculeBinder, …`) are provenance, not a
    # reading aid, and they are what made this block 393 px wide and overprint
    # the coloured arcs. They live in the manifest and the caption now. The
    # numbered ring names and their ranges stay, set in the same sans as
    # everything else rather than monospace.
    lines = []
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = _range_label(spec, n)
        lines.extend(_key_lines(f"{i}. ", spec.label, rng))
    return blocks + [fig.text(0.006, 0.80,
                              "rings, outermost first\n"
                              "(sources in the caption and manifest)\n\n"
                              + "\n".join(lines),
                              fontsize=7.4, va="top", ha="left", color="#222222")]


def _range_label(spec: RingSpec, n) -> str:
    """What the numbers beside a ring name mean.

    A categorical ring has no `Normalize`, and printing "no values" for it is a
    false statement about the figure's own data: the activity ring has three
    stated states and they are named in the legend. Caught on the v2.2 retake,
    where the CT.gov ring read "Pipeline: current activity
    (ClinicalTrials.gov v2) no values" beside a ring full of drawn cells.
    """
    if n is not None:
        return f"[{n.vmin:.3g}, {n.vmax:.3g}]"
    if spec.categorical:
        return f"{len(spec.categorical)} states — see the legend"
    return "no values"


def _short(label: str, limit: int = 34) -> str:
    return label if len(label) <= limit else label[:limit - 1].rstrip(" ,-") + "\u2026"


def _key_lines(prefix: str, label: str, rng: str, limit: int = 34) -> list[str]:
    """QA-v2-retest \u00a75. The ring key elided the half of a label that does the
    honest work.

    `_short` truncated at 34 characters, so V2 \u00a72.4's central move -- renaming
    the safety ring to "Safety flags & constraint \u2014 NOT evidence of safety" --
    reached `values.csv` and `caption.txt` and arrived on the audit figure as
    "Safety flags & constraint \u2014 NOT e\u2026". The caveat qualifies the ring; a
    figure that drops it overstates its data, which is the one thing AGENTS.md
    forbids ("Say this on the figure, not only in the report").

    Wrapped onto an indented continuation line instead of truncated: the block
    stays inside the width `assert_no_text_over_the_rings` measures, and nothing
    is lost. Truncation is never the answer for a caveat.
    """
    words, lines, cur = label.split(" "), [], prefix
    for w in words:
        candidate = f"{cur}{w}" if cur.endswith(" ") or not cur.strip() else f"{cur} {w}"
        if len(candidate) > limit + len(prefix) and cur.strip() != prefix.strip():
            lines.append(cur)
            cur = " " * len(prefix) + w
        else:
            cur = candidate
    lines.append(f"{cur}  {rng}")
    return lines


def assert_footer_is_true(lines: Sequence[str], ordering_note: str,
                          filter_notes: Sequence[str]) -> None:
    """COUNCIL V5: the figure must not make a false claim about its own method.

    v1's footer read "Rodent ring: … Never folded into any score" one line below
    "Default weights … rodent=0.5", and `rodent` was in RANKED_RINGS too, so it
    was in the weighted score *and* the median available rank. The claim was
    checked against the data structures it described, every time the figure was
    drawn, rather than trusted to a comment.

    v2.5 keeps the assertion and re-points it, because the defect it exists to
    catch is not about weights -- it is about a figure asserting something its
    own code contradicts. There are no weights now, so the three claims left to
    check are:

      1. **Nothing claims a ranking.** No stamp or caption line may use the
         vocabulary of the retired headline. A sentence promising a Pareto set
         on a figure that computes none is the V5 defect exactly.
      2. **The stated spoke order is stated.** A circle whose order carries
         meaning must say what the order is.
      3. **Every active filter is printed.** A reader must not mistake a
         filtered plot for the whole submitted set, so each active filter's own
         label has to appear in the text the figure draws.
    """
    body = "\n".join(lines)
    low = body.lower()
    for word in RETIRED_CLAIM_WORDS:
        if word in low:
            line = next((l for l in lines if word in l.lower()), "")
            raise AssertionError(
                f"the figure still says {word!r} — {line.strip()[:160]!r} — but v2.5 removed "
                f"ranking: there is no Pareto set, no weighted score, no eligibility and no "
                f"stability flag to describe. Delete the sentence; do not leave the claim "
                f"behind the missing feature."
            )
    if not ordering_note:
        raise AssertionError(
            "the spoke order carries meaning and this figure does not state it. An "
            "unstated ordering rule is the same defect as an unlabelled colour scale."
        )
    if ordering_note not in body:
        raise AssertionError(
            f"the figure orders its spokes by {ordering_note!r} and does not print it "
            f"anywhere in its own stamps or caption."
        )
    for note in filter_notes:
        if note not in body:
            raise AssertionError(
                f"the filter {note!r} removed genes from this plot and the figure does not "
                f"say so. A filtered figure that does not name its filters reads as the "
                f"whole submitted set."
            )


def _stamp_notes(ordering_note: str, filter_notes: Sequence[str], region: str,
                 cells_by_ring, gene_flags=None, subtitle: str = "") -> list[str]:
    """The footer's sentences, built once so `assert_footer_is_true` checks
    exactly the text that gets drawn rather than a copy of it."""
    n_tissues = ""
    for c in cells_by_ring.get("regional_specificity", {}).values():
        if c.state == "value" and isinstance(c.raw, dict) and c.raw.get("n_tissues"):
            n_tissues = f"{c.raw['n_tissues']} GTEx tissues"
            break
    notes = [
        # v2.5 item 1. The two lines that were here -- the Pareto headline and
        # the weighted convenience score with its printed weights -- are gone
        # with the thing they described. Nothing replaces them: this figure is a
        # gene set, rings of evidence and a drawer, and it now says so by saying
        # nothing it cannot do.
        f"Regional specificity = {region} TPM ÷ median across {n_tissues or 'the GTEx panel'}.",
        "Mouse-phenotype ring: a LEAD, not a verdict. Embryonic-lethal lines drop out of "
        "adult phenotyping, which biases exactly the essential genes the safety ring flags "
        "as risky (COUNCIL M5).",
        f"Missing is never imputed. k = how many of the {EXTERNAL_RING_COUNT} EXTERNAL rings "
        f"have a real measurement for that gene; your own logFC and padj are not counted. It "
        f"is a completeness gauge: it orders nothing and selects nothing.",
        f"The circle starts at 12 o'clock and runs clockwise — {ordering_note}. Region "
        f"affects exactly one ring (regional specificity).",
        "Pipeline: furthest phase counts only drugs indicated for a disease with mechanistic "
        "evidence linking it to THIS target. A drug that hits the target for an unrelated "
        "indication sets no phase; those genes are drawn 'not applicable', not 'low'.",
    ]
    if filter_notes:
        # v2.5 item 2. On the figure, in the footer, and in the caption: three
        # places, because a reader who has only one of them must still know the
        # plot is not the whole submitted set.
        notes.append(
            "THRESHOLD FILTERS ARE ACTIVE and this is not your whole submitted set: "
            + " · ".join(filter_notes)
            + ". A gene with no data on a filtered ring is KEPT and marked ~, never dropped "
              "for being unmeasured; every filtered gene is in ledger.csv with its reason."
        )
    if gene_flags:
        notes.append(
            "⚠ = Open Targets records a cardiac-conduction, cardiotoxicity or mortality "
            f"safety liability for this target ({', '.join(sorted(gene_flags))}). The "
            "clinical rings are a pipeline statement, never a safety one."
        )
    if subtitle:
        notes.insert(0, subtitle)
    return notes


def slide_line(ordering_note: str, filter_notes: Sequence[str]) -> str:
    """UX A4: the one line the slide render carries instead of the footer block.

    The filters are on it, first, when there are any. A slide is the artefact
    that goes in front of other people, and "this is not the whole list you were
    given" is not something to leave in a caption somebody has to be handed
    separately -- the same argument QA-v2-retest §5 made about the safety caveat.
    """
    parts = []
    if filter_notes:
        parts.append("FILTERED (" + " · ".join(filter_notes) + ") — not the whole "
                     "submitted set; no-data genes kept and marked ~")
    parts += [f"the safety ring is {SAFETY_RING_CAVEAT}",
              ordering_note,
              "rings are scaled independently of one another",
              "see the caption"]
    return " · ".join(parts)


def _draw_stamps(fig, notes: Sequence[str], provenance: str, lay=None,
                 slide_text: str = "") -> list:
    out = []
    if lay is not None and not lay.draw_footer:
        # UX A4 / the UX review's "noise" list: at 3.7 pt on a slide none of the
        # six footer lines is read by anyone. One line does the honest work and
        # the rest go into the caption you paste underneath.
        #
        # QA-v2-retest \u00a75: the safety ring's caveat was one of the lines that
        # went into the caption, so the artefact that actually goes in front of
        # colleagues carried the ring and not the thing that qualifies it. A
        # caption you have to be handed separately is not where a caveat lives.
        # It is on the slide now, in the same 10 pt as the rest of this line.
        out.append(fig.text(0.5, 0.008, slide_text,
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


def caption(rings, norms, region, provenance, ordering_note="", filter_notes=(),
            gene_flags=None, dropped_names=()) -> str:
    """UX A4. Everything the slide render drops, as text to paste underneath.

    The full audit stamps stay on the audit render, which is the right artefact
    for the morning review. This is what makes the slide render honest anyway.
    """
    lines = [f"targetring \u2014 {region}. {provenance}", ""]
    lines.append("Rings, outermost first (scale is per-ring; a colour means nothing "
                 "between two rings):")
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = _range_label(spec, n)
        lines.append(f"  {i}. {spec.label} {rng} \u2014 {spec.source}")
    lines += [
        "",
        "Pipeline: furthest phase counts only drugs indicated for a disease with human "
        "genetic evidence linking it to this target. A drug that hits the target for an "
        "unrelated indication sets no phase.",
        f"Missing is never imputed. k = external rings with a real measurement for that "
        f"gene; it is a completeness gauge and orders nothing. Spoke ORDER: the circle "
        f"starts at 12 o'clock and runs clockwise \u2014 {ordering_note}. Region affects exactly "
        f"one ring (regional specificity).",
        "This figure is internally consistent and auditable. Nothing on it is ranked or "
        "scored: it is your gene set, the evidence rings, and a drawer per gene.",
    ]
    if filter_notes:
        lines += [
            "",
            "THRESHOLD FILTERS ARE ACTIVE \u2014 this is not your whole submitted set:",
            *(f"  \u00b7 {n}" for n in filter_notes),
            "  A gene with no data on a filtered ring is kept and marked ~, never dropped "
            "for being unmeasured. Every filtered gene is in ledger.csv with its reason, "
            "and the filter settings are in manifest.json and values.csv.",
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


def assert_text_within_canvas(fig, blocks, margin: float = 1.0) -> None:
    """Nothing may be drawn past the edge of the canvas.

    matplotlib does not clip figure-level text, and with `savefig.bbox` pinned
    to the declared canvas size (UX A4) anything that runs off the edge is
    simply cut. The colour-bar caption did exactly that: it began at x=1190.7 in
    a 1260 pt canvas and needed ~95 pt, so "RESCALED WITHIN EACH RING" -- the
    one string that tells a reader the rings are not comparable -- was clipped
    to "RESCALED WITHIN E".
    """
    if not blocks:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    w, h = fig.canvas.get_width_height()
    offenders = []
    for t in blocks:
        bb = t.get_window_extent(renderer=r)
        if bb.x0 < -margin or bb.y0 < -margin or bb.x1 > w + margin or bb.y1 > h + margin:
            offenders.append(
                f"{t.get_text()[:40]!r} spans ({bb.x0:.0f},{bb.y0:.0f})-"
                f"({bb.x1:.0f},{bb.y1:.0f}) on a {w:.0f}x{h:.0f} canvas")
    if offenders:
        raise AssertionError(
            "text runs off the edge of the canvas and is clipped: " + "; ".join(offenders))


def assert_no_text_over_the_rings(fig, ax, blocks) -> None:
    """UX A6, checked geometrically rather than by eye.

    Measured in v1's SVG: the monospace ring key started at x=36 and its longest
    line ran to x=429, while the circle's left edge at that height was x=262, so
    the key overprinted the coloured arcs. The footer block occupied
    y=983.3-1028.3 with the `CDKN2A (k=8) *` label at y=995.4 inside it.

    Two obstacles, because there are two ways to land on the data:

    * the coloured arcs. The axes box is the square that bounds the circle, so a
      block may sit in its corner without touching anything; the check is
      against the inscribed circle, not the box.
    * the spoke labels, which stick out past the circle by however long the gene
      name is. Rather than guess a margin, the label artists are measured -- so
      moving the colour bar left until its caption fits inside the canvas cannot
      silently put it on top of SERPINA1.
    """
    if not blocks:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    box = ax.get_window_extent()
    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    radius = min(box.width, box.height) / 2
    labels = [(t, t.get_window_extent(renderer=r)) for t in ax.texts if t.get_text()]

    offenders = []
    for t in blocks:
        bb = t.get_window_extent(renderer=r)
        dx = max(bb.x0 - cx, 0, cx - bb.x1)
        dy = max(bb.y0 - cy, 0, cy - bb.y1)
        if (dx * dx + dy * dy) ** 0.5 < radius:
            offenders.append(
                f"{t.get_text()[:40]!r} at ({bb.x0:.0f},{bb.y0:.0f})-({bb.x1:.0f},{bb.y1:.0f}) "
                f"overlaps the rings (centre {cx:.0f},{cy:.0f} radius {radius:.0f})")
            continue
        for lt, lb in labels:
            if (bb.x0 - LABEL_CLEARANCE_PX < lb.x1 and lb.x0 < bb.x1 + LABEL_CLEARANCE_PX
                    and bb.y0 - LABEL_CLEARANCE_PX < lb.y1
                    and lb.y0 < bb.y1 + LABEL_CLEARANCE_PX):
                gap_y = max(lb.y0 - bb.y1, bb.y0 - lb.y1)
                offenders.append(
                    f"{t.get_text()[:34]!r} comes within {LABEL_CLEARANCE_PX:g} px of the "
                    f"spoke label {lt.get_text()[:24]!r} (vertical gap {gap_y:.1f} px)")
                break
    if offenders:
        raise AssertionError(
            "text is drawn over the figure's own data or labels (UX A6): "
            + "; ".join(offenders))


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
           "assert_greyscale_disjoint", "relative_luminance", "value_cmap", "CMAP_STOPS",
           "assert_within_domain", "ACTIVITY_PALETTE", "RETIRED_CLAIM_WORDS",
           "slide_line", "assert_text_within_canvas",
           "assert_no_text_over_the_rings", "assert_centre_fits_the_hole", "fit_centre_to_the_hole", "caption", "Layout", "LAYOUTS", "AUDIT", "SLIDE"]
