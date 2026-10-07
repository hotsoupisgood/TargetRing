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
import re
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Any, Callable, Sequence

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
from .palette import (  # noqa: E402
    ACTIVITY_HEXES, DIVERGING_MID_LUM, FAMILIES, RAMP_LUM, all_value_hexes,
    cmap_from, discrete_hexes,
)
from .palette import relative_luminance as _luminance  # noqa: E402
from . import sources as SOURCES  # noqa: E402
from .states import Cell  # noqa: E402

# v2.6. Every ring used to be drawn on one ramp -- viridis, truncated to
# [0.16, 0.92] so its luminance sat in [0.056, 0.663] and the three reserved
# state fills had bands of their own. That truncation was the right fix for the
# greyscale finding and the wrong shape for everything else, because one ramp
# for twelve measurements is what UX A5 is about: "a reader would not realise
# the rings aren't comparable". Colour now comes from `palette.py`, which builds
# a family per ring inside the *same* luminance envelope, so the greyscale
# guarantee generalises from one colormap to twelve instead of being relaxed.
VALUE_CMAP = "targetring per-ring families"

# The luminance band every ramp occupies, and the clearance the reserved fills
# need from it. RAMP_LUM is the generalisation of v2.5's CMAP_STOPS.
CMAP_LUM = RAMP_LUM
GREYSCALE_MIN_DELTA = 0.05

# The separation two touching rings are *aimed* at, as OKLab Delta E x100 under
# the worst of normal / protanopic / deuteranopic vision (Machado 2009 at
# severity 1.0) -- the dataviz method's categorical target.
#
# A target, and measured rather than asserted: see
# `assert_rings_are_distinguishable` for why twelve concentric full-range ramps
# cannot meet it pairwise and what carries ring identity instead.
# `scripts/hue_search.py` maximises against it and
# `scripts/check_ring_colours.py` prints what was achieved.
ADJACENT_RING_MIN_DELTA_E = 8.0

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

# v2.7, Puck's decision. The ring names move into the circle: an angular wedge at
# 12 o'clock is left empty and each ring is named inside it, at that ring's own
# radius. It is the standard Circos idiom and it beats a numbered list beside the
# figure for one reason -- a name sitting on the band it names cannot be
# mis-paired, and the numbered key could be (UX called it noise).
#
# The width is a constant and it is deliberately a constant. The wedge costs
# angular space, so every spoke narrows; the alternative -- shrinking the wedge
# as the gene count grows -- makes the innermost ring's label a function of how
# many genes somebody submitted, which is the kind of silent rescaling v2.6 spent
# its whole budget removing. So the gap never moves, the cost is stated on the
# figure (`wedge_note`), and `assert_wedge_labels_fit` proves the labels fit it
# at every gene count rather than trusting this number.
#
# 26 deg is not a guess either: the binding case is the *innermost* ring, whose
# radius is smallest and whose half-width budget is therefore tightest. At 12
# rings that band's mid-radius is ~48 of 100 plotted units, and
# 48 * tan(26/2 - 1.5) = 9.8 units ~ 52 px of half-width on the audit canvas,
# against the ~44 px the longest short label needs at 7.4 pt. Measured in
# `TestWedge`, not asserted here.
WEDGE_DEG = 26.0
# The label must clear the first and last sector edge, not merely land inside the
# wedge: a glyph that touches the arc it labels is UX A6 from the inside.
WEDGE_TEXT_MARGIN_DEG = 1.5

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

    v2.6 adds the other half of the same argument. A scale says *how much*; the
    colour family says *what kind of number this is*, and v2.5 got that wrong on
    four rings at once by giving all twelve one sequential ramp:

      family      which `palette.FAMILIES` entry the ring draws from. Rings that
                  are genuinely comparable share one (tractability/probe,
                  tdl/literature); rings that are not must not.
      levels      None for a continuous ring; an integer for a ring with that
                  many discrete steps and no meaning in between. A four-step
                  ordinal drawn on a 256-step ramp invites a reader to read a
                  value off a position that does not exist.
      units       what one number on this ring *is*, printed on its scale.
      transform   the sentence on the scale when the colour position is not the
                  raw number. If a domain is transformed and the figure does not
                  say so, the scale is a false claim about the data.
      short_source  four to twelve characters naming the source, so the slide
                  render can carry it (v2.3 Still open 4) without the full
                  provenance string that made the v1 key overprint the arcs.

    v2.7 adds one more, for the wedge:

      short_label  the name drawn inside the 12 o'clock gap, at this ring's own
                  radius. It is a *separate field* rather than a truncation of
                  `label`, because QA-v2-retest §5 is the record of what
                  truncating a ring label does: "Safety flags & constraint —
                  NOT evidence of safety" clipped to "…NOT e…" and the figure
                  then overstated its data. A short label has to be written to
                  stand alone at its length; the full label, caveat included,
                  stays on the scale in the side column.

      wedge_tooltip  whether that short label needs a hover title carrying the
                  ring's full name, units, domain and source.

    v3.7 adds one more, for the table view's column head:

      table_label  the name at the top of this ring's COLUMN. A third written
                  name rather than a reuse of either of the other two, and the
                  reason is what v3.7 was asked to fix: the table was headed
                  with `short_label` and the scale column with `label`, so ten
                  of twelve rings were called different things on two surfaces
                  describing the same band -- "Region TPM ÷ median" in the grid
                  against "Regional specificity" in the legend -- and the ring
                  number was the only bridge between them.

                  The wedge label cannot be that name: it is written to stand
                  alone at ~44 px inside the 12 o'clock gap, which is a
                  different budget and produced "TDL — knowledge" for a ring the
                  legend calls "Target development level". So `table_label` is
                  written against one rule instead, and the rule is asserted
                  (`TestTheTableAndTheLegendAgreeOnEveryRingName`): it is the
                  full `label`'s own words, in the full label's order, starting
                  on the same word. Empty means "use the full label", which five
                  of the twelve do. Nothing is truncated to arrive at it --
                  QA-v2-retest §5 is the record of what truncating a ring label
                  does -- and the full label is on the same surface, in the
                  citation strip under the grid and in the column's `title`.

                  Puck's rule, the right way round: *"if you can get the point
                  across no tool tip."* The default is False and a True here is
                  an admission that the short label is incomplete. What a
                  tooltip may never be is the place the meaning lives -- the
                  exported SVG in Illustrator, the PNG and a projected slide
                  have no hover at all, so the printed label has to be honest
                  on its own and the tooltip only ever adds detail. A label
                  that would be *misleading* without its tooltip is a bug in
                  the label, not a reason to set this flag.
    """

    key: str
    label: str
    source: str
    family: str
    units: str
    scale: str = "linear"
    domain: tuple[float, float] | None = None
    categorical: dict[float, str] | None = None
    category_labels: dict[float, str] | None = None
    levels: int | None = None
    transform: str = ""
    short_source: str = ""
    short_label: str = ""
    wedge_tooltip: bool = False
    table_label: str = ""

    @property
    def wedge_label(self) -> str:
        """What the wedge draws. Falls back to the full label, and then the fit
        assertion is what refuses the render -- never a silent truncation."""
        return self.short_label or self.label

    @property
    def column_label(self) -> str:
        """The name at the top of this ring's column in the table view.

        Falls back to the full `label`, which is what the scale column prints, so
        an unwritten `table_label` makes the two surfaces agree *exactly* rather
        than making the column head a truncation of a name.
        """
        return self.table_label or self.label

    @property
    def source_doc(self):
        """This ring's citation, or None where there is nothing to cite.

        v2.8. Read from `sources.RING_SOURCE_DOCS` rather than stored here, so
        the legend, the caption and the manifest cannot hold three versions of
        the same URL.
        """
        return SOURCES.RING_SOURCE_DOCS.get(self.key)

    @property
    def source_line(self) -> str:
        """What the scale column prints under the ticks.

        `Open Targets · platform-docs.opentargets.org` on a ring with a
        citation, and the bare short source on one without. The domain is on
        the figure because the PNG and the projected slide have no links at all
        -- the same argument that decided where the v2.7.1 tooltips live, one
        step further: a link that only works in the SVG has to leave a
        findable address behind in the renders that cannot follow it.
        """
        doc = self.source_doc
        return doc.legend_text if doc else self.short_source

    @property
    def tooltip(self) -> str:
        """"What is this and what am I looking at", in one hover.

        Full name, then what one number on the ring *is*, then what its ends
        mean, then who says so -- which is the order a reader asks them in. A
        bounded ring prints its fixed domain; an unbounded one says the range
        moves with the submitted set, because the honest answer there is that a
        colour means something different in the next run and the scale in the
        column is the only place the numbers live.
        """
        if not self.wedge_tooltip:
            return ""
        bits = [self.label, self.units]
        # `transform` already says "fixed 0-1" or "range moves with the
        # submitted set" on the rings that have one, so it stands in for the
        # synthetic sentence rather than sitting beside it: a tooltip that
        # states one fact twice in two wordings reads as two facts.
        if self.transform:
            bits.append(self.transform)
        elif self.domain:
            bits.append(f"fixed domain {self.domain[0]:g} to {self.domain[1]:g}")
        elif not self.categorical:
            bits.append("range moves with the submitted set — see its scale")
        bits.append(f"source: {self.short_source}")
        # v2.8. The hover already answers "who says so"; the URL is the only
        # part a reader can act on, and a wedge label has no room for it.
        if self.source_doc:
            bits.append(self.source_doc.url)
        return " · ".join(b for b in bits if b)


# PLAN.md §2. Order is outermost-first.
# COUNCIL V12. Three discrete states rendered on a continuous colormap, whose
# colour depended on which *other* genes were submitted: in the liver run only
# {0, 1} occurred, so "dormant" rendered #440154 -- the same darkest purple that
# meant "0 drugs" on the phase ring one band away. Submit one gene with a
# stopped-for-cause history and the same dormant gene's cell turned mid-teal
# with no change to its data. A fixed palette, outside the value colormap.
# v2.6 re-steps the three colours rather than keeping v2.4's hand-picked
# #8c2d04 / #fdbe6f / #08519c: they were chosen against one viridis ramp, and
# with twelve families in play they have to be placed against all of them and
# against the reserved fills. They are built in `palette.ACTIVITY_HEXES` at
# three different luminances as well as three hues, so the ring survives
# greyscale and both common colour-vision deficiencies.
ACTIVITY_PALETTE: dict[float, str] = dict(ACTIVITY_HEXES)
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

# v3.0. Named, because it is now the load-bearing sentence in two places at once.
# Puck let the app render and the exported one diverge, and the rule he attached
# to that is "the figure alone must still not lie" -- so this claim has to be on
# every render of the circle whatever else is dropped, including the one whose
# legend has been detached into its own file. `assert_figure_states_the_minimum`
# is what enforces it; this constant is what both the drawing and the check read,
# so the check cannot pass against a sentence the figure no longer draws.
COMPARABILITY_CLAIM = "EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS"

# v3.2 / UX v3 round 2 A5. What this tool calls itself, in one place.
#
# It was a literal in `_draw_centre` and another in `render_legend`, and the page
# had a third opinion: `index.html` said `targetring v1` in its `<title>` and its
# `<h1>` from v2.1 through v3.1, so a screenshot of the app carried "v1" in the
# header and "v2" in the middle of the circle 1,400 px below it. Nothing asserted
# they agreed, which is how it survived eleven releases.
#
# `test_the_page_and_the_figure_cannot_disagree_about_the_version` reads this
# constant and the page's own markup, so the next disagreement is a red test
# rather than a screenshot somebody notices.
TOOL_NAME = "targetring"
TOOL_VERSION = "v2"
FIGURE_TITLE = f"{TOOL_NAME} {TOOL_VERSION}"

# v3.2 / UX v3 round 2 A4 and A6. The point size of the comparability banner on
# the app render, which since A6 is the ONLY copy of that sentence a reader
# meets. It is a named constant because it is now load-bearing twice over: rule 2
# of this review requires it above the 12.0 CSS px floor at the width the app
# gives the figure (scale 1.5046, so 7.98 pt is the floor and this is the margin
# above it), and A4's vertical budget is decided partly by how many lines it
# takes. `scripts/ux_v3_web_fit.py` reads it, and
# `test_the_comparability_sentence_clears_the_floor_on_the_app_render` holds it
# to the floor rather than to a number somebody remembered to update.
WEB_BANNER_PT = 11.0

RING_SPECS: tuple[RingSpec, ...] = (
    # V17. logFC is signed, zero is meaningful, and up and down are opposite
    # rather than more and less of one thing -- so a sequential ramp on it is
    # not a presentation choice, it is the wrong encoding. v2.5 anchored the
    # *norm* on zero, which fixed the arithmetic and left the colour saying that
    # -3.3 was the bottom of a scale that ran up to +3.1. Diverging, blue for
    # down and red for up, with a pale near-neutral at zero.
    #
    # What the figure still cannot say, and does not: whether up-regulation is
    # the therapeutic direction. M10's question is not answerable from this
    # data, so neither arm is drawn as the good one and the scale says so.
    RingSpec("logfc", "logFC (yours)", "submitted DE table",
             family="logfc", units="log2 fold change", scale="diverging",
             transform="diverging, anchored at 0 and symmetric; "
                       "neither direction is drawn as the good one",
             short_source="yours",
             short_label="logFC (yours)"),
    # Heavy-tailed even after the -log10: a gene at p=1e-300 and fourteen genes
    # between p=0.05 and p=1e-5 is an ordinary DE table, and linear on that
    # range is V6's collapse with different numbers. log10(1+x) rather than a
    # second log10, because padj=1 gives -log10 padj = 0 and log10(0) is not a
    # colour. The ticks on the scale are printed in raw -log10 padj.
    RingSpec("neg_log10_padj", "-log10 padj (yours)", "submitted DE table",
             family="padj", units="-log10 adjusted p", scale="log1p",
             transform="log-spaced: colour is log10(1 + x), ticks are x",
             short_source="yours",
             short_label="−log10 padj (yours)"),
    # v2.3 item 1 / COUNCIL M7/V8. One ring became two, and the labels say which
    # claim each one makes: the dense three-key ring is not the sparse probe key,
    # and a hatched probe cell must not take tractability down with it.
    #
    # They share a colour family on purpose. They were one ring until v2.3, they
    # answer the same question, and "rings that are genuinely comparable may
    # share a family" is the other half of making twelve families legible.
    RingSpec("tractable", "Tractability (family suggests druggable)",
             "OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder — "
             "3 dense keys, present for every target with a prioritisation record",
             family="tractability", units="mean of 3 keys, each 0 or 1",
             domain=(0.0, 1.0), transform="fixed 0–1, same colour in every run",
             short_source="Open Targets",
             short_label="Tractability (suggested)",
             wedge_tooltip=True,
             table_label="Tractability"),
    # Binary with absence: an explicit 0.0 is a measurement ("looked, no probe")
    # and a missing key is no_data. Two values and nothing between them, so two
    # swatches -- a 256-step ramp here would offer a reader positions that do
    # not exist.
    RingSpec("chemical_probe", "Chemical probe available (prior tool-compound effort)",
             "OT prioritisation: hasHighQualityChemicalProbes — SPARSE. Explicit 0.0 = "
             "looked, no probe; key absent = no_data (hatched)",
             family="tractability", units="probe: no / yes",
             domain=(0.0, 1.0), levels=2,
             transform="binary; absent key is hatched, never 0",
             short_source="Open Targets",
             short_label="Chemical probe (yes/no)",
             table_label="Chemical probe available"),
    # Signed with a stated bad direction, which makes it diverging and NOT the
    # same kind of diverging as logFC: here one arm genuinely is the bad one, so
    # it gets the warm arm and says so. The caveat stays on the label because
    # the four keys buy comparability, not evidence of safety.
    RingSpec("safety", "Safety flags & constraint — NOT evidence of safety",
             "OT prioritisation: hasSafetyEvent, geneEssentiality, geneticConstraint, isCancerDriverGene",
             family="safety", units="mean of 4 keys, each in [-1, 1]",
             domain=(-1.0, 1.0), scale="diverging",
             transform="fixed −1…+1, diverging at 0; amber arm is the bad one",
             short_source="Open Targets",
             short_label="Safety flags ≠ safe",
             # The caveat travels into the column head rather than being dropped
             # to shorten it. `SAFETY_RING_CAVEAT` is checked onto both renders
             # for the same reason: "Safety flags & constraint" on its own reads
             # as a safety score, which is the one thing this ring is not.
             table_label="Safety flags — NOT evidence of safety"),
    # V6: a ratio over five orders of magnitude, so log10. Deliberately NOT a
    # fixed domain: the ratio is unbounded above (TTR reaches 5,204x in liver)
    # and a fixed domain would clip TTR, APOB and SERPINA1 to the same top
    # colour -- reintroducing at the top end exactly the collapse V6 is about.
    # The log transform is what fixes the collapse; the range is printed on the
    # ring's own scale, with the ticks in raw x-fold.
    RingSpec("regional_specificity", "Regional specificity",
             "GTEx v8 median TPM ratio, log10, floor applied",
             family="specificity", units="× median across the GTEx panel",
             scale="log",
             transform="log10; ticks are the raw ratio. Range moves with the "
                       "submitted set — unbounded above",
             short_source="GTEx v8",
             short_label="Region TPM ÷ median",
             wedge_tooltip=True),
    # A stage ordinal with six levels and nothing between them.
    RingSpec("clinical_phase", "Pipeline: furthest phase (attributable indications only)",
             "OT drugAndClinicalCandidates, gated on OT target-disease genetic evidence",
             family="pipeline", units="0 none · 1 Ph1 · 2 Ph1/2 · 3 Ph2 · 4 Ph3 · 5 approved",
             domain=(0.0, 5.0), levels=6,
             transform="6 discrete stages, fixed; no value between two stages",
             short_source="Open Targets",
             short_label="Furthest trial phase",
             wedge_tooltip=True,
             table_label="Pipeline: furthest phase"),
    # Puck's hard condition from v2.2 item 2 stands: the ring must state which
    # source it counted, on the figure. v2.4 changes the answer -- the per-drug
    # ClinicalTrials.gov hop is retired and these rows come from Open Targets'
    # own `clinicalReports`, in the same query as every other OT ring -- so the
    # label says that, and the cell detail names the `source` values actually
    # seen on the gene. v2.6 puts it on the ring's own scale as well, which the
    # slide render carries (v2.3 Still open 4).
    RingSpec("clinical_activity", f"Pipeline: current activity ({ACTIVITY_SOURCE_LABEL})",
             "OT drugAndClinicalCandidates.rows[].clinicalReports, selected by carrying a "
             "trialOverallStatus — NOT by a source literal (the AACT->ClinicalTrials.gov "
             "relabelling of 2026-09-24 is what a literal filter missed)",
             family="", units="three named states, no order implied by colour",
             categorical=ACTIVITY_PALETTE, category_labels=ACTIVITY_LABELS,
             transform="categorical — not a ramp, and not comparable to one",
             short_source="Open Targets",
             short_label="Trial activity now",
             wedge_tooltip=True,
             table_label="Pipeline: current activity"),
    # v2.2 item 4 / COUNCIL S1. An explicit ordinal, never inferred -- four
    # named levels, so four swatches.
    RingSpec("tdl", "Target development level (Pharos TDL — knowledge, not a pocket)",
             "Pharos GraphQL target(q:{sym}).tdl; Tdark 0 / Tbio 1 / Tchem 2 / Tclin 3",
             family="knowledge", units="Tdark · Tbio · Tchem · Tclin",
             domain=(0.0, float(TDL_MAX)), levels=TDL_MAX + 1,
             transform="4 discrete levels, fixed",
             short_source="Pharos",
             short_label="TDL — knowledge",
             wedge_tooltip=True,
             table_label="Target development level"),
    # V9: reduced to three states, so there is no count left to scale. Two of
    # the three are values (0 phenotyped-and-normal, 1 phenotyped-and-abnormal)
    # and the third is `no_data`, hatched -- so the ring draws two swatches.
    # COUNCIL V15: the source is OT `mousePhenotypes` -- mouse only. PLAN §2
    # retired the rodent source citing a QA finding about IMPC, but COUNCIL S6
    # had recommended Alliance of Genome Resources (MGI *and* RGD), which is
    # why the ring was called "rodent" in the first place. A finding about one
    # source was used to retire a different one. Renamed to what it is;
    # revisiting Alliance is still open.
    RingSpec("rodent", "Mouse phenotype lead (3 states)",
             "OT mousePhenotypes, 'no abnormal phenotype' excluded; mouse only, not rat",
             family="rodent", units="0 no abnormal · 1 abnormal · hatched not phenotyped",
             domain=(0.0, 1.0), levels=2,
             transform="binary; 'not phenotyped' is hatched, never 0",
             short_source="Open Targets",
             short_label="Mouse phenotype",
             table_label="Mouse phenotype lead"),
    # The adapter already stores log10(1 + n), so the ring's *value* is the
    # transform (V6's own prescription, applied upstream in v1). The scale prints
    # the ticks as raw PubTator3 records, which is the number a reader has.
    RingSpec("literature", "Literature volume",
             "PubTator3, gene-id resolved, log10(1+n)",
             family="knowledge", units="PubTator3 records", scale="prelog1p",
             transform="the adapter stores log10(1 + records); ticks are "
                       "records. Range moves with the submitted set",
             short_source="PubTator3",
             short_label="Literature count"),
    # V13: k over external rings only, on a fixed 0..K domain. It is a gauge and
    # not evidence, which is why it is the one ring drawn at a third of the
    # chroma the others use -- a deliberately muted instrument rather than a
    # twelfth measurement. v2.5 narrowed what k means; this is what that
    # narrowing looks like in colour.
    RingSpec("completeness", "k — external rings with a measurement",
             "derived; maximum is the number of external rings",
             family="gauge", units=f"rings with a real value, of {EXTERNAL_RING_COUNT}",
             domain=(0.0, float(EXTERNAL_RING_COUNT)),
             levels=EXTERNAL_RING_COUNT + 1,
             transform="a completeness gauge: it orders nothing and selects "
                       "nothing, and is drawn muted to say so",
             short_source="derived",
             short_label=f"k of {EXTERNAL_RING_COUNT} rings",
             table_label="k — external rings"),
)

#: The two rings whose provenance is the run's own DE input rather than an
#: external source, and which therefore cannot be described by a constant.
DE_INPUT_RINGS = ("logfc", "neg_log10_padj")


def ring_specs_for(de: "SOURCES.DESource | None" = None) -> tuple[RingSpec, ...]:
    """`RING_SPECS` with rings 1 and 2 described by where this run's numbers
    actually came from.

    `RING_SPECS` itself is the paste-path spelling, and was the ONLY spelling:
    `RingSpec("logfc", "logFC (yours)", "submitted DE table", ...,
    short_source="yours")`. On an Atlas run every word of that is false, and
    because it is a module constant the same false label reached the figure's
    ring name, its scale column, `caption.txt`, the manifest's `ring_sources`,
    the Table tab, the offline export and the web scales panel — six surfaces
    from one root cause. `git log -S'you submitted'` shows the text entered in
    `6391a79` and was never revisited; it predates the contrast entry point.

    Everything downstream already reads a list of `RingSpec` rather than the
    constant — `render()` filters it, `caption()` walks it, `view.py` and
    `export.py` index it — so resolving the right list once, per run, is enough
    to correct all of them at the same time and in the same words.
    """
    source = de or SOURCES.DESource.pasted()
    out: list[RingSpec] = []
    for spec in RING_SPECS:
        if spec.key not in DE_INPUT_RINGS:
            out.append(spec)
            continue
        # The label's bracketed qualifier is rebuilt rather than string-patched:
        # "logFC (yours)" -> "logFC (Atlas)" by regex would silently do nothing
        # the day the label is reworded.
        base = spec.label.split(" (")[0]
        short_base = spec.short_label.split(" (")[0] if spec.short_label else ""
        out.append(dc_replace(
            spec,
            label=f"{base} ({source.label_qualifier})",
            source=source.source_phrase,
            short_source=source.short_source,
            short_label=(f"{short_base} ({source.label_qualifier})"
                         if short_base else spec.short_label)))
    return tuple(out)


def ring_cmap(spec: RingSpec):
    """The colormap this ring draws from — its family, at its number of levels.

    A ring with `levels` gets exactly that many colours. Anything else gets the
    family's 256-step ramp. A categorical ring has no family and no ramp at all
    (COUNCIL V12) -- its swatches are the whole encoding, and the colormap
    exists only so the scale column can draw them in one code path.
    """
    if spec.categorical:
        return cmap_from(ring_swatches(spec), f"{spec.key}_categorical")
    fam = FAMILIES[spec.family]
    if spec.levels is None:
        return fam.cmap()
    return cmap_from(ring_swatches(spec), f"{fam.name}_{spec.levels}")


# Where a stepped ring's swatches sit on its family's ramp. Inset from both
# ends on purpose: a two-level ring sampled at 0.0 and 1.0 gets a near-black and
# a near-white, and at those luminances sRGB holds so little chroma that the
# ring loses its hue -- the one thing that says which ring it is. Inset, a
# binary ring is still unmistakably two-tone and still recognisably its own
# colour. The high end stops short of 1.0 for the same reason.
SWATCH_SPAN = (0.14, 0.94)


def ring_swatches(spec: RingSpec) -> list[str]:
    """The discrete colours a stepped ring can paint, darkest first."""
    if spec.categorical:
        return [spec.categorical[v] for v in sorted(spec.categorical)]
    fam = FAMILIES[spec.family]
    hexes = list(fam.hexes)
    n = spec.levels or len(hexes)
    if n == 1:
        return [hexes[len(hexes) // 2]]
    lo, hi = SWATCH_SPAN
    # Sampled from the family's own ramp, so a stepped ring and a continuous
    # ring in the same family are the same colours at the same magnitudes.
    return [hexes[round((lo + (hi - lo) * i / (n - 1)) * (len(hexes) - 1))]
            for i in range(n)]


def ring_colours(spec: RingSpec, n: int = 17) -> list[str]:
    """The colours this ring can actually paint, sampled across its whole range.

    Not an "identity colour": comparing one representative per ring answers the
    wrong question. What a reader confuses is two *cells* -- one on each of two
    touching rings -- and cells are confusable when they are the same
    brightness, which is what `adjacent_ring_delta_e` pairs them on.
    """
    if spec.categorical or spec.levels:
        return ring_swatches(spec)
    hexes = FAMILIES[spec.family].hexes
    return [hexes[round(i * (len(hexes) - 1) / (n - 1))] for i in range(n)]


def ring_identity_colours(spec: RingSpec) -> list[str]:
    """The colours that stand for this ring when rings are compared.

    A sequential ring has one: the middle of its ramp. A *diverging* ring has
    two -- one per arm -- and must not be represented by its middle, which is
    near-neutral by construction and would compare as grey against everything.
    A stepped or categorical ring is represented by the colours it paints.
    """
    if spec.categorical or spec.levels:
        return ring_swatches(spec)
    fam = FAMILIES[spec.family]
    if fam.kind == "diverging":
        return [fam.hexes[round(t * (len(fam.hexes) - 1))] for t in (0.18, 0.82)]
    return [fam.hexes[len(fam.hexes) // 2]]


def ring_identity_colour(spec: RingSpec) -> str:
    """One colour for a legend or a report line."""
    cols = ring_identity_colours(spec)
    return cols[len(cols) // 2]


# The part of the luminance band where a ring's hue is actually available.
# Every ramp converges at its ends: at luminance 0.074 sRGB holds almost no
# chroma at any hue, so the darkest cell of *any* ring is a near-black, and the
# lightest is a near-white. That convergence is a property of the gamut, not of
# this palette, and no hue assignment removes it -- which is why the check below
# is stated over the middle of the band and the ends are reported as a known
# limit rather than asserted away.
IDENTITY_LUM_RANGE = (0.12, 0.58)


def adjacent_ring_delta_e(a: RingSpec, b: RingSpec,
                          lum_range: tuple[float, float] = IDENTITY_LUM_RANGE,
                          lum_window: float = 0.04) -> tuple[float, str, str]:
    """The worst confusable pair of cells across the boundary of two rings.

    Two cells are confusable when they are the same brightness: at different
    luminances a reader separates them whatever the hue, and under colour-vision
    deficiency luminance is most of what is left. So colours are paired within
    `lum_window` of each other and scored under the worst of normal, protanopic
    and deuteranopic vision.

    `lum_range` is the middle of the band -- see `IDENTITY_LUM_RANGE`. Passing
    the full band measures the ends too, which `scripts/check_ring_colours.py`
    does and prints, because a reader does meet those cells even if no palette
    can separate them.
    """
    from scripts.hue_search import VISION, dE  # measurement code, not a render dep

    def rgb(h: str):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

    worst, pair = 999.0, ("", "")
    for ca in ring_colours(a):
        la = _luminance(rgb(ca))
        if not (lum_range[0] <= la <= lum_range[1]):
            continue
        for cb in ring_colours(b):
            lb = _luminance(rgb(cb))
            if not (lum_range[0] <= lb <= lum_range[1]) or abs(la - lb) > lum_window:
                continue
            d = min(dE(rgb(ca), rgb(cb), k) for k in VISION)
            if d < worst:
                worst, pair = d, (ca, cb)
    return worst, pair[0], pair[1]


def value_cmap(spec: RingSpec | None = None):
    """The ramp for one ring.

    v2.5 had a single ramp and this took no argument. It keeps working without
    one -- returning `logfc`'s, the outermost ring -- only so a caller asking
    "what does a value colour look like" gets one; every drawing path passes the
    ring it is drawing.
    """
    return ring_cmap(spec if spec is not None else RING_SPECS[0])


def colormap_hexes(n: int = 256) -> set[str]:
    """Every colour any ring can paint a value cell, across all twelve families.

    v2.5 returned 256 hexes from one ramp. Widening it is what keeps invariant 2
    and `assert_fills_disjoint` honest now that there are twelve: a reserved
    fill has to be outside *all* of them, not outside the one that happens to be
    drawn first.
    """
    return all_value_hexes()


def relative_luminance(colour) -> float:
    """WCAG relative luminance — what a colour becomes in greyscale."""
    return _luminance(to_rgb(colour))


def _all_value_luminances() -> list[float]:
    return [_luminance(to_rgb(h)) for h in all_value_hexes()]


def assert_greyscale_disjoint(min_delta: float = GREYSCALE_MIN_DELTA) -> None:
    """PLAN §6.2, extended to the render people actually project and photocopy.

    `assert_fills_disjoint` proves the reserved fills are not *colours* any ring
    produces. That proof does not survive desaturation, and the UX review
    measured the consequence: no_data and a real mid-scale value were identical
    in greyscale at a contrast ratio of 1.001. This asserts the same guarantee
    in luminance, against every ramp and between the states.

    v2.6 is where this check earned its keep. Twelve independently chosen
    colormaps would have twelve luminance spans, and `no_data` at 0.807 only
    needs *one* of them to reach up that far to become a plausible value again.
    The fix is not a bigger tolerance: `palette.RAMP_LUM` is a single luminance
    envelope every family is built inside, so the number of families does not
    change what this has to prove.
    """
    ramp = _all_value_luminances()
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
    a palette change, at the moment someone edits STATE_STYLE or adds a family.
    """
    values = colormap_hexes()
    for state, style in STATE_STYLE.items():
        fill = to_hex(style["fc"])
        if fill in values:
            raise AssertionError(
                f"the {state!r} fill {fill} is also produced by the value colormaps "
                f"({VALUE_CMAP}); missing would be indistinguishable from a value"
            )


def assert_ramp_luminance_is_monotone() -> None:
    """Within a ring, darker must always mean more (v3.4; it was less before).

    Sequential ramps are monotone end to end, and after v3.4 they run *down* in
    luminance: pale is low, dark is high. The direction is asserted, not merely
    the monotonicity, because a single ring running the other way is worse than
    all twelve running either way -- a reader who has learned the figure reads
    that one ring backwards and is never told.

    A diverging ramp is monotone along each arm and symmetric about the middle,
    which is the property that makes "equally far from zero" look equally far
    from zero -- and the property that a sequential ramp on signed data (V17)
    does not have. Diverging rings were already pale-at-zero and dark-at-the-
    extremes, so v3.4 did not move them.
    """
    for name, fam in FAMILIES.items():
        lums = [_luminance(to_rgb(h)) for h in fam.hexes]
        if fam.kind == "diverging":
            half = len(lums) // 2 + 1
            arms = [lums[:half], list(reversed(lums[half - 1:]))]
            for i, arm in enumerate(arms):
                if not all(b >= a - 1e-6 for a, b in zip(arm, arm[1:])):
                    raise AssertionError(
                        f"the {name!r} family's arm {i} is not monotone in luminance; "
                        f"on a diverging ring that makes two different distances from "
                        f"zero look like the same distance")
            if abs(arms[0][-1] - arms[1][-1]) > 0.02:
                raise AssertionError(
                    f"the {name!r} family's two arms do not meet at the same luminance "
                    f"({arms[0][-1]:.4f} vs {arms[1][-1]:.4f}), so equal and opposite "
                    f"values are drawn at different distances from the middle")
        elif not all(b <= a + 1e-6 for a, b in zip(lums, lums[1:])):
            raise AssertionError(
                f"the {name!r} family does not fall monotonically in luminance, so a "
                f"darker cell on that ring does not reliably mean a larger number")


def assert_rings_are_distinguishable(rings: Sequence[RingSpec] | None = None) -> None:
    """UX A5, the part that is an invariant rather than a measurement.

    "A reader cannot tell that ring 3's yellow and ring 7's yellow mean
    unrelated things." With one ramp that was true of every pair of rings, and
    literally true: the same hex, two rings, two meanings. This asserts it is
    now true of none -- **no colour is painted by two rings that are not
    declared comparable.** `tractable`/`chemical_probe` and `tdl`/`literature`
    are declared comparable, share a family on purpose, and are exempt.

    Separation *beyond* identity is measured, not asserted, and
    `scripts/check_ring_colours.py` prints it against the
    `ADJACENT_RING_MIN_DELTA_E` target. Making it an assertion would be
    dishonest, because it cannot be met and the reason is geometric rather than
    fixable:

    * Under protanopia or deuteranopia at a fixed luminance the hue circle
      collapses onto one axis, where the widest separation any two hues reach is
      about Delta E 23. Twelve rings cannot all be pairwise far apart on one
      axis.
    * Every ramp converges at its ends. sRGB holds almost no chroma near black
      or near white, so the darkest cell of any two rings is two near-blacks
      whatever their hues, and a diverging ring's middle is near-neutral by
      construction.

    What carries ring identity instead, and does so for every reader: the fixed
    radial position, the ring number, and the labelled swatch on each ring's own
    scale in the column beside the figure. That is the secondary encoding the
    dataviz method requires when a pair sits in its 6-8 floor band, and it is
    why the scale column is not optional decoration.
    """
    rings = list(rings if rings is not None else RING_SPECS)
    painted: dict[str, list[str]] = {}
    for spec in rings:
        for hexc in (ring_swatches(spec) if (spec.levels or spec.categorical)
                     else FAMILIES[spec.family].hexes):
            painted.setdefault(hexc, []).append(spec.key)
    for hexc, keys in painted.items():
        fams = {next(r.family for r in rings if r.key == k) for k in keys}
        if len(keys) > 1 and len(fams) > 1:
            raise AssertionError(
                f"{hexc} is painted by {sorted(set(keys))}, which are in different "
                f"colour families. The same colour on two rings that are not declared "
                f"comparable is UX A5 exactly.")


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
    if spec.scale == "log1p":
        # V6 on a quantity that is already -log10(p): a table with one gene at
        # p=1e-300 puts every other gene in one colour. log10(1+x) rather than a
        # second log10 because padj=1 gives x=0, and log10(0) is not a colour.
        return math.log10(1.0 + max(v, 0.0))
    # "prelog1p" is a ring whose *adapter* already stored log10(1+n) -- the
    # literature ring has since v1, which is V6's own prescription applied
    # upstream. Nothing more is done to the colour; the inverse exists so the
    # scale can tick in the units the source counted in.
    return v


def _unscaled(spec: RingSpec, v: float) -> float:
    """The raw number a scaled colour position stands for — what a tick says."""
    if spec.scale == "log":
        return 10.0 ** v
    if spec.scale in ("log1p", "prelog1p"):
        return (10.0 ** v) - 1.0
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
    # Whether the run-id / git-SHA stamp is set at 6 pt or at 8 pt. It is the
    # ONLY thing this flag has decided since v2.6 retired the monospace ring
    # key it was named after, and the name outliving the feature cost a whole
    # slide render during v3.1: `_draw_legend` read it as "does this layout draw
    # the scale column", which is a question it had never answered. `slide` has
    # it False and draws a column; `web` has it False and does not. Renamed to
    # what it does. The question it was mistaken for is `scales_in_svg`, below.
    stamp_size_small: bool
    draw_footer: bool
    footer_size: float
    centre_scale: float
    # The amber run-conditions notice belongs on the audit render and in the
    # caption. On a slide it would sit across the bottom spoke labels, and the
    # slide is the artefact you stand next to and explain. The RED banner is
    # drawn on both: you must never present a figure whose invariants failed.
    draw_conditions: bool
    # v2.6 / UX A5. Where the per-ring scale column lives, as
    # (x, bottom, width, height) in figure fractions. v2.5 had one colour bar
    # (`cbar_x`, `cbar_gap`) because there was one ramp; there are twelve scales
    # now and they are the primary way a reader learns a ring's range, so they
    # get a column rather than a corner. The numbered monospace key they replace
    # is gone from both renders.
    scale_box: tuple[float, float, float, float]
    scale_title_size: float
    scale_tick_size: float
    scale_wrap: int
    # The audit render prints each ring's transform and source under its scale;
    # the slide prints the source only, because at 16:9 the column is shorter
    # and a transform sentence per ring would take the type below legibility --
    # UX A4's failure mode, reproduced in a new place.
    scale_detail: bool
    # Whether the centre carries the per-category drop counts and the list of
    # dropped gene names. The audit render does; the slide render gives the
    # count and points at the caption, because fitting nine names plus seven
    # category counts into the hole shrinks all of it past legibility -- which
    # is UX A4 reappearing in the middle of the figure.
    centre_detail: bool
    # v2.7. The angular gap at 12 o'clock, in degrees, and the type size of the
    # ring names drawn in it.
    #
    # Per layout, not one constant, because the budget for a horizontal label at
    # radius r is `2 * r * tan(wedge/2)` in *pixels* and the two renders draw the
    # same circle at very different sizes: the audit's plotted square is 1057 px
    # and the slide's is 552. Measured at 12 rings, where the innermost band's
    # mid-radius is 0.48 of the plotted radius: 26 deg buys the audit ~52 px of
    # half-width and the slide ~27 px, against the ~44 px `k — rings with data`
    # needs at 7.4 pt. One constant therefore either wastes the audit's space or
    # drops the slide's inner names to ~5 pt, which is UX A4 reappearing at the
    # centre of the figure. The two renders already differ in canvas, type and
    # stamps; invariant 7 checks the spoke *order*, which is identical, and
    # nothing claims the two put a gene at the same angle.
    wedge_deg: float = WEDGE_DEG
    wedge_label_size: float = 7.4
    # v3.1 / UX v3 A2. Whether the cell-state box and the per-ring scale column
    # are drawn INTO the SVG, or left to the surface the render is read on.
    #
    # True everywhere it has always been true -- `audit` and `slide` are files
    # that leave the app and must carry everything needed to decode a cell
    # (UX-v1-mvp.md A5 is binding on both). False on `web` alone, where the page
    # itself draws the same twelve scales as HTML at 13 px rather than as 5.8 px
    # of vector text, which is what §v3.0 made legal.
    #
    # Default True, so a layout added later carries its own decode furniture
    # unless it says otherwise -- the safe direction for a flag whose wrong
    # value produces a figure a reader cannot decode.
    scales_in_svg: bool = True
    # v3.2 / UX v3 round 2 A4. Whether the twelve ring names are drawn INSIDE the
    # 12 o'clock gap, or only in the surface's own furniture. `wedge_deg` decides
    # whether the gap exists; this decides whether it holds text.
    #
    # They are two questions and round 2 is where that became visible. A4 wanted
    # the app figure inside one 1440 x 900 viewport, which caps the canvas at
    # 8.31 in and the circle at about 640 px. The names need much more than that:
    # the gap is a triangle, so a name's room is `r · tan(13°)`, and
    # `Tractability (suggested)` sits on ring 3 at a small radius —
    # `assert_wedge_labels_fit` refused every circle under roughly 790 px, which
    # is the 10.6 in canvas A4 exists to shorten (`logs/v32-web-fit-sweep2.txt`,
    # 14 more candidates after the footer stopped being the binding wall).
    #
    # So on the app render the gap stays and the names go. The gap is doing a
    # second job there that is worth its 26°: it keeps 12 o'clock clear of spoke
    # labels, which is what lets the comparability banner be drawn large in the
    # corner at all — with `wedge_deg=0` a spoke label moves to the top and the
    # banner lands 20.7 px inside it.
    #
    # Nothing is lost and one thing is gained. The names were round 1's LARGEST
    # deliberate exception to the 12.0 px floor — twelve strings at 9.07 px,
    # justified by "all twelve are numbered and named at 13 px in the panel",
    # which is the same reasoning A6 used to delete the duplicated comparability
    # band. Keeping an unreadable copy of a legible thing is what this review is
    # against. `wedge_note` states which arrangement this render has, derived
    # from the layout rather than written down twice (COUNCIL V5).
    #
    # Default True: a layout added later names its own rings unless it can say
    # where else they are.
    wedge_labels_in_svg: bool = True
    # v3.5. The as-seen floor a ring name drawn inside the gap must clear on this
    # layout, in CSS pixels, or None where this layout's type is measured against
    # a different floor.
    #
    # It is set on `web` alone and it is the whole difference between v3.5 and
    # round 1. Round 1 DID draw the twelve names in the app's gap -- at 9.07 CSS
    # px, because `fit_wedge_labels` shrank 7.4 pt until the geometry accepted it
    # -- and round 2 deleted them for exactly that: "an unreadable copy of a
    # legible thing is what this review is against". So a layout that is read in
    # the browser declares the floor here, `assert_wedge_names_clear_the_floor`
    # measures the DRAWN artists against it after the fit loop has had its way,
    # and a render that could only fit the names by going under it refuses them.
    # `audit` and `slide` are files, measured against the 10.0 pt presentation
    # floor on the surface they are presented at (§v3.3), so they leave it None.
    wedge_label_floor_px: float | None = None
    # v3.3 / UX v3 round 3 A7. The point size of the comparability claim on this
    # layout's scale column, or None to keep it tied to the fitted row titles.
    #
    # Tied is how it was, and on the slide that is how it became 4.88 pt at the
    # size the artefact exists for. The header was `scale_title_size * 0.95` and
    # `scale_title_size` is fitted DOWN until twelve rows fit the column, so the
    # one sentence this review protects on every surface was sized by how much
    # unrelated text happened to be beside it. On a 16 in canvas at a full 13.33
    # in slide the scale is 0.8333, so 12.0 pt here is 10.0 pt as seen: the
    # floor, met deliberately, by a number that does not move when a ring label
    # grows a caveat.
    claim_size: float | None = None


# v2.6 widens the audit canvas from 17.5 to 19.5 in and narrows the axes box to
# match, so the circle is the same size it was and the 2.0 in it gained is the
# scale column. Growing the canvas rather than shrinking the figure is the right
# trade here: the audit render is read on a screen at full size, and UX A4's
# argument against a big canvas was about fitting one onto a slide, which is
# what the separate slide layout is for.
AUDIT = Layout("audit", (19.5, 14.0), 8.0, (0.225, 0.205, 0.475, 0.755),
               True, True, 7.5, 1.0, True, (0.762, 0.035, 0.232, 0.930),
               7.4, 6.6, 42, True, True,
               wedge_deg=26.0, wedge_label_size=7.4)
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
# v2.7: the slide gets NO wedge, and the reason is measured rather than argued.
# The obstacle is radial, not angular, so no wedge angle fixes it. The slide's
# plotted square is 552 px, so twelve rings span (97-46)/105 * 276 = 134 px of
# radius -- 11.2 px per band -- against one line of 8.4 pt type at 13.0 px tall.
# The ring names would overlap each *other* before they came near a sector edge,
# and the only cures are type below legibility (UX A4, which is the whole reason
# the slide layout exists) or a smaller circle on the render that already has the
# smaller circle. A 40 deg wedge would take 15% of every spoke's width and buy
# nothing.
#
# So the slide keeps v2.6's arrangement: a full 360 deg of sectors, and the ring
# names on their own scales in the column, where the type is 7.6 pt and has room.
# The figure says which one it is doing -- `wedge_note`'s zero-wedge branch
# prints the measurement, not a preference.
#
# v2.8: the slide's scale column is TALLER, because a source line per ring costs
# twelve lines and the fit loop pays for them in type size -- which on the
# projected render is UX A4 again. Unpaid, the column went 6.98 px to 5.79 px;
# at 0.912 tall it is 6.58 px, one 0.94 step down rather than three.
# `logs/v28-slide-sweep3.txt` is the sweep.
#
# The column does NOT move left, and that was measured rather than assumed. The
# circle's right edge is at x=0.590 and 0.11 of width sits unused, so widening
# looked free -- but at six genes `CDKN2A (k=5/9)` reaches further right than any
# label in a 14- or 50-gene run, and `assert_no_text_over_the_rings` refused
# x=0.655 with the source line 14.3 px inside it (`logs/v28-slide-sweep.txt`,
# `-sweep2`). The gap belongs to the longest spoke label, not to the column.
#
# v3.0 raises the bottom margin 0.075 -> 0.105, and it costs NOTHING, which is
# part of why it went so long unnoticed: the circle is width-bound here too --
# 0.345 x 16 = 5.52 in against 0.83 x 9 = 7.47 in of height -- so the radius is
# 2.76 in before and after.
#
# What it fixes PREDATES v3.0 and no test had it. With a filter active
# `slide_line` grows a second line upward, and at 3, 7 and 14 genes that line
# landed 1.6 px under the bottom spoke label, so
# `assert_no_text_over_the_rings` refused the render. The design worked -- the
# failure was a crash, not a misleading figure -- but a filtered 14-gene slide
# could not be drawn at all. Found by the new
# `test_a_filtered_render_says_so_on_every_layout`, which is the first test to
# render every layout WITH filters; the filtered matrix run happened to use
# seven genes whose shorter labels cleared it.
# v3.3 / UX v3 round 3. Two numbers on this line changed and both are the same
# measurement: what a string on this artefact is at the size the artefact exists
# for. A full 13.33 in slide scales a 16 in canvas by 0.8333, so the 10.0 pt
# floor is 12.0 pt in the file.
#
#   A7  the comparability claim   5.86 pt -> 4.88 pt as seen.  `claim_size=12.0`
#       makes it 10.00, and it is a constant rather than `scale_title_size *
#       0.95` because the old value was decided by how much text was beside it.
#   B11 the gene labels           11.50 pt -> 9.58 pt as seen, missing the floor
#       by 0.42 pt on the artefact UX A4 created to solve exactly this. 12.1 pt
#       is 10.08, and the extra 0.1 is not a rounding fudge: 12.0 lands on 10.00
#       and `ux_v3_measure.py` reports `>=` the floor, so a figure one hundredth
#       of a point either way of compliant is not a figure that has met a floor.
#
# Half a slide is not claimed on this artefact and round 1's A3 is why: 10.0 pt
# as seen at 0.4167 needs 24.0 pt in the file, and a twelve-ring circle with a
# twelve-scale column does not hold 24 pt type on 16x9. The slide render exists
# so a FULL slide works; A3 said the same thing about the legend and said it
# rather than shrinking something to close it.
# Two more numbers moved and neither costs the circle anything, which is the
# same arithmetic §v3.0 found when it raised this bottom margin the first time:
# the radius here is WIDTH-bound -- 0.345 x 16 = 5.52 in against 0.79 x 9 =
# 7.11 in of height -- so it is 2.76 in before and after.
#
#   bottom margin 0.105 -> 0.145, axes height 0.83 -> 0.79. At 12.1 pt the gene
#   labels reach further down than they did at 11.5, and with a filter active
#   `slide_line` grows a line upward: `assert_no_text_over_the_rings` refused
#   the render at 3, 7 and 14 genes, and then refused 0.125 at 3 and 7. This is
#   §v3.0's own finding arriving one type size later, and the fix it used is the
#   one that still works -- bought twice over from height the circle never had.
#
#   scale column height 0.912 -> 0.885. The claim is 12 pt now and it is drawn
#   above the column, so at the old height it reached the run-id stamp and
#   `assert_text_does_not_overprint_text` refused every slide render. Measured
#   with a deliberately long run id, because round 1's item 5 is that a constant
#   which clears today's stamp is a constant a longer one walks through.
SLIDE = Layout("slide", (16.0, 9.0), 12.1, (0.245, 0.145, 0.345, 0.79),
               False, False, 0.0, 1.15, False, (0.700, 0.026, 0.290, 0.885),
               8.4, 7.6, 52, False, False,
               wedge_deg=0.0, wedge_label_size=8.4, claim_size=12.0)
# v3.0, Puck: *"download figure and the online figure can be different."*
#
# Until now one artefact had to serve both, so it carried every caveat the
# export needs while being read on a screen that already showed most of them in
# its own furniture. Released from that, the two go in OPPOSITE directions and
# that is the point:
#
#   `audit` -- the EXPORT. Gains, rather than loses: the seven-line caveat
#              footer it always had, plus the invariant summary, because the
#              person reading the downloaded PNG has no Invariants tab to click
#              and no one standing next to them.
#   `web`   -- the APP. Drops exactly what the page itself already says, and
#              nothing else. The footer block is the `What this is not` tab; the
#              dropped-gene names in the hole are the `Drop ledger` tab; the
#              amber run-conditions box is the `#conditions` banner above the
#              figure. All three are one click or zero clicks away, on screen,
#              always. What it keeps is the irreducible minimum -- the title and
#              region, the comparability claim, the in/rendered counts and any
#              active filter -- because `assert_figure_states_the_minimum`
#              refuses to draw a circle without them, on every layout.
#
# The 1.9 in of canvas the footer gave back goes to the circle, which is the
# whole point on a screen: the axes box grows from 0.755 to 0.885 of a shorter
# canvas. Nothing about the CELLS differs between the two, and that is asserted
# rather than trusted -- `assert_renders_agree` compares every `cell|` gid and
# fill in the two SVGs and fails the run if one pixel of data disagrees.
# The axes box looks like it should be as tall as the canvas allows and it is
# not, because the circle is WIDTH-bound on both: 0.475 x 19.5 = 9.26 in against
# 0.815 x 12.2 = 9.94 in of height, so the radius is 4.63 in here and 4.63 in on
# the audit canvas -- identical. Nothing is gained by pushing the box taller and
# `assert_no_text_over_the_rings` charged for trying: at bottom=0.045 the
# one-line footer landed 1.6 px under `GENE007 (k=9/9)`. What the app actually
# gains is the 1.8 in of empty canvas below the circle, so the same circle fills
# more of the screen at a given page width.
# v3.1 / UX v3 A2. The canvas shrank from 19.5 x 12.2 in to 12.0 x 10.5, and
# that is the whole fix rather than a tidy-up.
#
# A canvas is only ever seen at the width the page gives it. At 19.5 in in a
# 1388 px panel -- a 1440 px laptop, which is what Puck uses -- the scale factor
# is 0.9886, so a point in the file is a CSS pixel on screen and **148 of the
# render's 149 strings were under 12 px**. The comparability sentence, which is
# one of the two things that may never be hard to find, was 178 x 7 px of amber
# in a corner: not behind a click, and not seen either.
#
# Two things paid for it. The scale column and the cell-state box leave the SVG
# (`draw_key=False`) and the page draws them as HTML at 13 px, which is what
# §v3.0 made legal -- "the export and the app render can be different" -- and
# what nothing before v3.0 could have done. And the canvas then has no reason
# to be 19.5 in wide: 23.2% of it was the column and 22.5% a margin holding one
# oversized box, leaving the circle 47.5%.
#
# At 9.6 in, with the 330 px decode panel taking its share of the row, the
# figure gets 1040 px and the scale is **1.505**: the 8 pt gene labels land at
# 12.0 CSS px and the circle goes from 659 px across to 790.
#
# Three strings are still under the floor and each is deliberate, because each
# is duplicated on the page at or above it: the twelve wedge ring names at 9.07
# px (the panel numbers and names all twelve at 13 px, and the SVG keeps its
# v2.7.1 tooltips), the centre's `spoke order` note at 10.23 (the counts line
# above the figure says it at 14), and `the 1 not shown are named in the Drop
# ledger tab` at 11.28 (the tab itself is 14 px and two inches away). The wedge
# names are geometry-bound rather than a choice -- widening the gap to 32
# degrees was measured and `assert_wedge_labels_fit` refused it, because at
# twelve rings the binding budget is the band's HEIGHT, which is v2.7's finding
# about the slide arriving on a third layout.
#
# Nothing is dropped that the page does not carry: UX-v1-mvp.md A5's binding
# requirement that a scale exists per ring is met by the HTML panel, the export
# keeps its column untouched, and `assert_figure_states_the_minimum` still
# refuses to draw this circle without the comparability claim -- which
# `_draw_legend` now draws large in the corner instead of as a 6 px column
# header.
# v3.2 / UX v3 round 2 A4. The canvas keeps its width and gives back its
# height: 9.6 x 10.6 in becomes 9.6 x 8.2.
#
# Round 1 bought type size by trading a 19.5 x 12.2 in canvas for a nearly
# square one, and at a fixed panel width an aspect change IS a height change.
# The figure came out 1,148 px tall in a 900 px viewport -- so there was no
# scroll position at which the circle was whole, on a page that put its top at
# y = 1,279. Round 1 measured what a reader can read and not what a reader can
# see at once.
#
# It costs nothing it did not have to cost, and the arithmetic is why:
# `scale = panel_px / (width_in * 72)` involves no height at all, so every
# string keeps its 1.5046 and its 12.04 CSS px. What height buys is the circle,
# because the axes box stops being width-bound: 0.760 x 9.6 = 7.30 in of width
# against 0.715 x 8.25 = 5.90 in of height, so the radius is height-bound now at
# 2.95 in where it was width-bound at 3.65.
#
#   before round 1   19.5 x 12.2 in   869 px tall   circle 659 px   type 7.91 px
#   after round 1     9.6 x 10.6 in  1148 px tall   circle 790 px   type 12.04 px
#   now               9.6 x  8.25 in  894 px tall   circle 639 px   type 12.04 px
#
# Nearly the circle v3.0 had, with type 1.5x the size it was, whole inside one
# laptop viewport with 6 px to spare. A 900 px viewport caps the canvas at
# 8.31 in and this is 8.25, deliberately not 8.30: `figure 899 px` also passed
# every assertion and one pixel is not a margin.
#
# **The margins were not guessed and they were not free either.** A4 said to let
# the geometry assertions arbitrate them; they arbitrated 97 candidates and
# rejected all 97 (`logs/v32-web-sweep.txt`, `logs/v32-web-fit-sweep.txt`), and
# two blocks had to leave this canvas before any triple passed. Both are in
# `docs/reviews/UX-v3-rounds.md` §2.2, both follow §v3.0's own rule for this
# layout -- *drop exactly what the page already says* -- and neither relaxes an
# assertion: the four-to-five-line caveat block (`caveats_for_the_page`) and the
# twelve ring names in the wedge (`Layout.wedge_labels_in_svg`). What is left
# above the circle is the comparability banner and what is left below it is the
# FILTERED clause and the spoke labels' overhang, and
# `assert_no_text_over_the_rings`, `assert_text_within_canvas` and
# `assert_text_does_not_overprint_text` are what settled 0.140 and 0.145 --
# 0.725 of axes height was refused at 50 genes with the banner 4.0 px off
# `GENE049 (k=9/9)`, which is the clearance exactly and therefore not a margin.
# v3.5, Puck: *"the names should appear automatically when the viewport allows a
# circle big enough to hold them honestly, and stay off when it does not."*
#
# So this Layout stops being the app render and becomes the app render **in one
# window** -- a 1440 x 900 laptop, which is the window round 2 A4 measured and
# the one every number in the comment above is still true of.
# `web_layout_for_viewport` derives the canvas for any other window from it, and
# `render_naming_the_rings_if_they_fit` decides the names by rendering them and
# keeping the result only if the assertions accept it.
#
# Two fields on this line changed and neither is a nudge.
#
#   `wedge_label_size` 7.4 -> 8.0. 7.4 was inherited from the audit render, whose
#   canvas is twice as wide; here the scale is fixed at 1.5046 CSS px per point
#   (`WEB_PX_PER_IN`, below), so 7.4 pt is 11.13 CSS px -- under the floor before
#   the fit loop has shrunk anything, which is round 1's 9.07 px exception with
#   two thirds of the gap between it and legibility already spent. 8.0 pt is
#   12.04 px: the same arithmetic, and the same number, as `label_size`.
#
#   `wedge_label_floor_px` 12.0. The names are allowed back only at a size a
#   reader can read, measured on the drawn artists rather than on this constant.
#
# `wedge_labels_in_svg` stays False HERE, and that is not a leftover: at this
# canvas the names do not fit at 8.0 pt and the gap stays empty, exactly as round
# 2 left it. What is new is that 9.6 x 8.25 in is no longer the only canvas.
WEB = Layout("web", (9.6, 8.25), 8.0, (0.120, 0.145, 0.760, 0.715),
             False, False, 0.0, 1.0, False, (0.762, 0.035, 0.232, 0.930),
             7.4, 6.6, 42, True, False,
             wedge_deg=26.0, wedge_label_size=8.0, scales_in_svg=False,
             wedge_labels_in_svg=False, wedge_label_floor_px=12.0)

# v3.0, Puck: *"we could have the download download legend as a separate
# image"* -- "circle on one slide, legend on another, sized independently".
#
# Not a Layout for `render`: there is no circle on it, so `render_legend` draws
# it. The Layout exists so the scale column code is the SAME code, called twice
# with two `scale_box`es for two columns of six. A legend whose ramps or ticks
# came from a second implementation could disagree with its figure, which is the
# one new failure mode a detachable legend introduces and the one worth spending
# structure on.
# v3.1 / UX v3 A3. The scale box is one column's geometry; `render_legend`
# offsets it three times. It was `(0.360, 0.045, 0.300, 0.845)` -- two columns
# of six starting a third of the way across, which left the whole lower-left
# quadrant of a 16:9 canvas empty and still set the ring names at 9 pt. Three
# shorter columns start further left, stop above the caveat band, and spend the
# reclaimed space on type: titles 9 -> 12.5 pt, ticks 8 -> 11 pt.
#
# The wrap is narrower because the column is (46 -> 34 characters): a 12.5 pt
# title in a 0.225-wide column is the same number of glyphs as a 9 pt one in a
# 0.300-wide column, give or take, and `_key_lines` wraps rather than eliding,
# so a longer caveat costs a line rather than its tail.
LEGEND_COLUMNS = 4
LEGEND_COLUMN_PITCH = 0.2455
LEGEND = Layout("legend", (16.0, 9.0), 8.0, (0.0, 0.0, 0.01, 0.01),
                True, True, 7.2, 1.0, False, (0.025, 0.245, 0.212, 0.530),
                12.5, 11.0, 34, True, False,
                wedge_deg=0.0, wedge_label_size=8.0)

LAYOUTS = {"audit": AUDIT, "slide": SLIDE, "web": WEB}

# v3.5. The app render, at any window size.
#
# The panel the app gave the figure on Puck's laptop, and the window that panel
# was measured in: 1440 px wide, minus 52 px of `main` padding and the 330 + 18
# px decode column, is 1040 px, and round 2's viewport was 900 px tall
# (`docs/reviews/UX-v3-rounds.md` §2, `scripts/ux_v3_measure.py`). Everything
# `WEB` declares is true at this pair and only at this pair, so they are named
# here rather than left as two magic numbers in three files.
WEB_REFERENCE_PANEL_PX = 1040.0
WEB_REFERENCE_VIEWPORT_PX = 900.0
# CSS pixels per canvas inch on the app render, and the one number that makes a
# responsive canvas safe. `scale = panel_px / (width_in * 72)` is what turns a
# point in the file into a pixel on the screen, so holding `panel_px / width_in`
# CONSTANT holds every string's as-seen size constant: the canvas grows with the
# window and 8.0 pt is 12.04 CSS px at 1040 px and at 1500. A canvas that grew
# without this would buy circle at the price of the floor round 1 paid to reach.
WEB_PX_PER_IN = WEB_REFERENCE_PANEL_PX / WEB.figsize[0]      # 108.33 px / in
# The CSS pixels a point of this layout is drawn at, everywhere, by construction.
WEB_CSS_SCALE = WEB_PX_PER_IN / 72.0                          # 1.5046
# Held back from the height budget, because one pixel is not a margin -- round 2
# rejected an 8.30 in canvas that also passed every assertion for the same
# reason and took 8.25.
WEB_VIEWPORT_SLACK_PX = 6.0


def web_layout_for_viewport(panel_px: float = WEB_REFERENCE_PANEL_PX,
                            viewport_px: float = WEB_REFERENCE_VIEWPORT_PX,
                            *, name_the_rings: bool = False) -> Layout:
    """v3.5. The `web` canvas for the window this render is going to be read in.

    Round 2 A4 pinned this layout to one canvas because one canvas was all a
    fixed figure could have, and picked the one that fits a 1440 x 900 laptop
    whole. That is the right canvas for that laptop and it is 640 px of circle on
    a 27 in monitor, where the same page has a thousand more pixels in both
    directions and spends them on white margin. It is also what emptied the 26°
    gap: the names need a bigger circle than 900 px of height can carry, so
    pinning the canvas to the smallest supported window pinned the *arrangement*
    to it too.

    Two inputs, both measured by the browser and handed over rather than derived
    from a copy of the stylesheet in Python (`app.js`, `/api/analyse`):

      `panel_px`     the width `#figure` actually got, in CSS px.
      `viewport_px`  `window.innerHeight`, in CSS px.

    and each decides one side of the canvas:

      width   `panel_px / WEB_PX_PER_IN`, which is the constant-scale rule above:
              the canvas is exactly as many inches as the panel will draw at
              1.5046 px per point, so every string keeps its 12.04 CSS px.
      height  what is left of the window, in the same currency. This is A4's own
              rule -- "the whole figure fits with no scrolling" -- applied to
              whatever window it is instead of to the one window.

    with two limits and both of them matter:

      **Never smaller than `WEB`.** A window narrower or shorter than the laptop
      A4 measured gets the laptop's canvas, scaled down by the browser as it
      always was. Shrinking the canvas instead would put the spoke labels --
      which are points, and do not shrink with it -- straight through
      `assert_no_text_over_the_rings`, so the app would answer a narrow window
      with no figure at all.

      **Never taller than the width can use.** The circle is
      `min(0.760 * w, 0.715 * h)` inches, so past `h = 0.760/0.715 * w` more
      height is empty canvas that makes the figure harder to fit in the window
      and the circle no larger. Derived from `axes_box` rather than written down,
      because those two fractions were arbitrated by the geometry assertions and
      a constant here would not follow them if they moved.

    Rounded DOWN to a hundredth of an inch (1.08 px): the budget is a ceiling,
    and 8.2523 in is not a truer canvas than 8.25 in.
    """
    def down(x: float) -> float:
        return math.floor(x * 100.0) / 100.0

    w = max(WEB.figsize[0], down(panel_px / WEB_PX_PER_IN))
    tall_enough_to_be_width_bound = w * WEB.axes_box[2] / WEB.axes_box[3]
    h = min(max(WEB.figsize[1],
                down((viewport_px - WEB_VIEWPORT_SLACK_PX) / WEB_PX_PER_IN)),
            down(tall_enough_to_be_width_bound))
    return dc_replace(WEB, figsize=(w, h), wedge_labels_in_svg=name_the_rings)


def web_css_scale(lay: Layout, panel_px: float = WEB_REFERENCE_PANEL_PX) -> float:
    """CSS pixels per canvas point, as the browser will actually draw this canvas
    in this panel. Equal to `WEB_CSS_SCALE` for every canvas
    `web_layout_for_viewport` derived from the same panel, and smaller than it in
    the one case that function clamps: a window narrower than the laptop A4
    measured, which keeps the laptop's canvas and is scaled down to fit -- exactly
    as it was before v3.5, and the reason this is a function and not the constant.
    """
    return panel_px / (lay.figsize[0] * 72.0)


def web_circle_px(lay: Layout, panel_px: float = WEB_REFERENCE_PANEL_PX) -> float:
    """The circle's diameter in CSS px, in a panel this wide.

    One expression, one place, and deliberately in the SAME currency as every
    circle size already in the record: the shorter side of the axes box, which is
    the 639 px of round 2, the 790 px of round 1 and the 659 px of v3.0
    (`docs/reviews/UX-v3-rounds.md` §2.2). The drawn rings stop at 97 of the
    polar axes' 105, so the coloured band's own outer edge is 0.924 of this.
    """
    square_in = min(lay.axes_box[2] * lay.figsize[0], lay.axes_box[3] * lay.figsize[1])
    return square_in * 72.0 * web_css_scale(lay, panel_px)


def render_naming_the_rings_if_they_fit(
    render_one: "Callable[[Layout], FigureResult]",
    *,
    panel_px: float = WEB_REFERENCE_PANEL_PX,
    viewport_px: float = WEB_REFERENCE_VIEWPORT_PX,
) -> tuple["FigureResult", Layout]:
    """v3.5. Draw the app render with the ring names in the gap if they fit it,
    and without them if they do not. Returns the result and the layout that
    produced it.

    **The names are not decided by a threshold in this function.** They are
    decided by drawing them and keeping the drawing only if
    `assert_wedge_labels_fit` and `assert_wedge_names_clear_the_floor` both
    accept it -- the first that every name is inside the empty wedge and clear of
    its neighbours by `LABEL_CLEARANCE_PX`, the second that `fit_wedge_labels`
    did not buy that by shrinking the type under this surface's floor. Neither is
    relaxed, widened or special-cased here; the only thing that is new is what
    happens when one of them refuses, and what happens is round 2's arrangement,
    which is the honest one: the gap stays empty and `wedge_note` says so.

    A predicted threshold would have been cheaper by one render and wrong the
    first time `RING_SPECS` grew a thirteenth ring or a ring grew a longer
    `short_label`, because the binding budget is a band's height against a line
    of type and both move. The cost is one extra `render` -- about a second, on a
    run whose network work is 20-60 s -- and only on the windows where the names
    do not fit.
    """
    wanted = web_layout_for_viewport(panel_px, viewport_px, name_the_rings=True)
    try:
        return render_one(wanted), wanted
    except RingNamesDoNotFit:
        # The failed render never reached `savefig`, so nothing was written; it
        # did leave its matplotlib figure open, and this is a server that draws
        # one figure at a time and runs for weeks.
        plt.close("all")
    without = dc_replace(wanted, wedge_labels_in_svg=False)
    return render_one(without), without


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
    # v3.2 / UX v3 round 2 A4. The caveat clauses the app render handed to the
    # page instead of drawing them under its own circle. Returned rather than
    # recomputed by the page for the reason `scale_rows` exists: the wedge clause
    # contains this run's own spoke angles, and a second implementation of it
    # could describe a geometry the figure does not have (COUNCIL V5). Empty on
    # every layout that draws its own footer.
    web_caveats: list[str] = field(default_factory=list)


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
    # v3.5. A name in `LAYOUTS`, or a Layout itself. The app render's canvas is a
    # function of the window it will be read in (`web_layout_for_viewport`), so
    # there is a layout per window and the registry cannot hold it. Everything
    # downstream reads `lay.name`, which `dataclasses.replace` preserves, so a
    # resized `web` is still the `web` render to every branch and every sentence.
    layout: "str | Layout" = "audit",
    gene_ids: dict[str, str] | None = None,
    invariant_summary: str = "",
    # Stage 5. The contrast this run's genes came from, as whole sentences
    # ready to draw -- built by the caller (`run.py`) from the `ContrastRef`,
    # never reworded here, so the figure cannot say the sign a different way
    # than the manifest does. Empty on a paste-path run: `()` draws nothing.
    contrast_notes: Sequence[str] = (),
    # Where THIS run's logFC and adjusted p actually came from. `None` is the
    # paste path, which is what `RING_SPECS` has always hard-coded. An Atlas
    # run passes its own, and rings 1 and 2 stop claiming to be a file the
    # reader submitted. One value corrects the ring label, the scale column
    # and the caption together, because all three read the ring list this
    # resolves -- see `ring_specs_for` and `sources.DESource`.
    de_source: "SOURCES.DESource | None" = None,
) -> FigureResult:
    assert_fills_disjoint()
    assert_greyscale_disjoint()
    assert_ramp_luminance_is_monotone()
    # v2.8. Before a link is drawn, not after a reader follows one.
    # v3.9: and with the keys of the rings being drawn, so a ring with neither a
    # citation nor a stated reason stops the render instead of reaching the
    # manifest as `"unstated"` — the default that kept `druggability` invisible
    # across 991 cells and 69 runs.
    SOURCES.assert_registry_is_coherent([s.key for s in RING_SPECS])
    lay = layout if isinstance(layout, Layout) else LAYOUTS[layout]
    filter_notes = list(filter_notes)
    genes = list(genes)
    if not genes:
        raise ValueError("no genes to render")

    rings, norms, cmaps = _rings_norms_cmaps(cells_by_ring, de_source)

    # UX B1: pycirclize raises "Too large sector space size is set!!" at
    # n >= 181 with space=2, which reached the user as a raw traceback after
    # ~13 minutes of fetching. `MAX_GENES` stops it before any network work;
    # this stops a direct render() call with a message that says what happened.
    space = 2 if len(genes) > 2 else 8
    # v2.7: the budget is no longer the whole circle. The wedge is reserved
    # first, so the guard has to subtract it -- otherwise the failure mode it
    # exists to prevent returns `lay.wedge_deg` degrees earlier than it used to.
    wedge = lay.wedge_deg
    if (len(genes) - 1) * space >= 360 - wedge:
        budget = (f"the {360 - wedge:g} degrees left after the {wedge:g} degree ring-name "
                  f"gap at 12 o'clock" if wedge > 0 else "the 360 degrees of a circle")
        raise ValueError(
            f"{len(genes)} genes x {space} degrees of spacing exceeds {budget}, so the "
            f"figure cannot be drawn. The input cap is {MAX_GENES} genes "
            f"(config.MAX_GENES); above about 60 spokes the figure is unreadable anyway."
        )
    if wedge > 0:
        # The sectors no longer span 360 deg: `start`/`end` leave `wedge` degrees
        # empty, centred on 12 o'clock, and the ring names go in it.
        # `endspace=False` is what makes the empty span exactly `wedge` -- with
        # the default the last sector gets a trailing gap too, the visible wedge
        # is `wedge + space`, and the angle `wedge_note` prints is then wrong by
        # two degrees.
        circos = Circos({g: 1 for g in genes}, start=wedge / 2, end=360 - wedge / 2,
                        space=space, endspace=False)
    else:
        # A layout that declares no wedge is drawn exactly as v2.6 drew it, down
        # to `endspace`, so a v2.6 slide and a v2.7 slide differ only in where
        # the rings are named and not in where a gene sits.
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
    # Each ring's own mid-radius, kept so the wedge can put the name at exactly
    # the radius of the band it names. That correspondence is the whole argument
    # for the wedge over a numbered key, so it is read off the same numbers the
    # cells are drawn from rather than recomputed from a formula that could
    # drift.
    ring_mid_r: dict[str, float] = {}

    for ring_i, spec in enumerate(rings):
        hi = r_outer - ring_i * band
        lo = hi - band * 0.86
        ring_mid_r[spec.key] = (lo + hi) / 2
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
                fill = to_hex(cmaps[spec.key](norm(_scaled(spec, cell.value))))
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
                # v2.9. Puck: "can you put the ensembl id's next to the gene
                # name?" -- and `values.csv` is where that matters most, because
                # the CSV is what makes a figure checkable against a source. A
                # symbol is ambiguous (HBB matches two Open Targets targets on
                # the council list and is ledgered for exactly that reason); the
                # Ensembl id is what the run actually queried on. The ledger
                # section of this file has carried `ensembl_id` since v1 -- so
                # until now a *dropped* gene's id was in the artefact and a
                # *rendered* gene's was not, which is the wrong way round.
                "ensembl_id": (gene_ids or {}).get(gene, ""),
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
    spoke_labels: list[str] = []
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
        # v2.9: the Ensembl id is deliberately absent from here. See `_ID_NOTE`
        # for the measurement, and `assert_the_id_rule_is_kept` for the check
        # that stops it drifting back in without the footer changing with it.
        label = f"{liab}{gene} (k={k}/{EXTERNAL_RING_COUNT}){unf}"
        spoke_labels.append(label)
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

    # v2.7, Puck's layout. Each ring is named inside the wedge, at its own
    # radius. Drawn on the polar axes directly rather than through
    # `circos.text`, which defers its `ax.text` call into `plotfig` and hands
    # back nothing: the artists have to be in hand here, because the type size
    # is fitted to the wedge by measurement and then the fit is asserted.
    #
    # deg=0 is 12 o'clock, which is the middle of the gap by construction
    # (sectors run from wedge/2 to 360-wedge/2).
    # v3.2 / UX v3 round 2 A4. The gap is reserved on every layout that asks for
    # one; the NAMES inside it are drawn only where there is nowhere else to put
    # them. See `Layout.wedge_labels_in_svg` for why the app render is the one
    # place there is somewhere else.
    wedge_labels = []
    if wedge > 0 and lay.wedge_labels_in_svg:
        for spec in rings:
            t = ax.text(0.0, ring_mid_r[spec.key], spec.wedge_label,
                        size=lay.wedge_label_size, ha="center", va="center",
                        color="#111111", zorder=CELL_ZORDER + 1)
            # The gid is the handle `_add_wedge_tooltips` writes the hover title
            # against after the file is saved, and the same handle the tests
            # read the names back out of. Same idiom as the cells' `cell|...`.
            t.set_gid(f"ringname|{spec.key}")
            wedge_labels.append(t)
    if wedge_labels:
        # The band, in pixels, is the vertical room a name has before it crowds
        # the name on the ring above it. Read off the axes' own transform rather
        # than computed from `band` and a remembered r-limit: pycirclize sets the
        # polar axes to (0, 105), not (0, 100), and that 5% is the difference
        # between a check that holds and one that is slightly wrong everywhere.
        y0 = ax.transData.transform((0.0, 0.0))[1]
        band_px = abs(ax.transData.transform((0.0, band))[1] - y0)
        fit_wedge_labels(fig, ax, wedge_labels, wedge, band_px)
    # The assertion waits until the centre block has been fitted to the hole, at
    # the foot of this function: the innermost ring name sits about two plotted
    # units above the hole's edge, so the centre block is one of its obstacles
    # and it is not its final size yet.

    # COUNCIL V5, re-pointed by v2.5: the figure must not make a false claim
    # about its own method. Every sentence it is about to draw is checked against
    # what this render is actually doing, before any of it reaches the canvas.
    wnote, clock = wedge_note(lay, len(genes), space), clock_phrase(lay)
    notes = _stamp_notes(ordering_note, filter_notes, region, cells_by_ring,
                         gene_flags, subtitle, wnote, clock)
    # Stage 5: appended rather than folded into `_stamp_notes` itself, which
    # `render_legend` also calls with no contrast in scope. Present on both the
    # audit and the slide footer (plan: "slide footer"; audit gets it too
    # because the two share this one list, which is a strictly more honest
    # figure, not a different one).
    notes = list(notes) + list(contrast_notes)
    # v3.0. The export gets MORE, not less, and this is the clearest instance:
    # "all seven invariants PASS" is the single fact that makes a downloaded PNG
    # auditable, and until now it was only in manifest.json and in a tab. The
    # person the export exists for has neither. A failure was always shouted in
    # red; a pass was silent, which left the two indistinguishable on paper.
    if invariant_summary:
        notes.insert(0, invariant_summary)
    cap = caption(rings, norms, region, provenance, ordering_note, filter_notes,
                  gene_flags, dropped_names, wnote, clock, invariant_summary)
    assert_footer_is_true(notes + cap.splitlines(), ordering_note, filter_notes,
                          wnote)
    assert_the_id_rule_is_kept(spoke_labels, notes)

    centre_blocks = _draw_centre(ax, region, len(genes), input_count,
                                 rendered_count, ledger_counts,
                                 dropped_names, lay)
    centre_blocks += _draw_ordering_and_filters(ax, ordering_note, filter_notes,
                                                stats, lay)
    centre_blocks += _draw_contrast_note(ax, contrast_notes, lay)
    text_blocks = []
    # v3.1 / UX v3 A1: the stamps are drawn FIRST now, and the scale column's
    # bottom is then set above whatever they turned out to need. The footer is
    # user-driven -- how many drop categories, how many dropped names, whether
    # filters are active -- so its height is not knowable when a constant is
    # chosen, and `AUDIT.scale_box`'s 0.035 bottom was chosen against a 14-gene
    # run. At three genes the footer block reaches y=112 px and the column's
    # last line, `derived`, sits at y=103: the column printed into the footer
    # and every assertion passed. Measured rather than re-guessed.
    wshort = wedge_note(lay, len(genes), space, short=True)
    footer_line = slide_line(ordering_note, filter_notes, wshort,
                             where=("see the What this is not tab"
                                    if lay.name == "web" else "see the caption"))
    web_caveats: list[str] = []
    if not lay.draw_footer and not lay.scales_in_svg:
        footer_line, web_caveats = caveats_for_the_page(
            ordering_note, filter_notes, wshort)
    stamps = _draw_stamps(fig, notes, provenance, lay, footer_line)
    text_blocks += _draw_legend(fig, rings, norms, cmaps,
                                _column_above_the_footer(fig, lay, stamps),
                                banner_width=_free_width_beside(fig, stamps, 0.018))
    text_blocks += stamps
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
    assert_no_text_over_the_rings(fig, ax, text_blocks, wedge_labels)
    # The innermost ring's inner edge, as a fraction of the plotted radius.
    hole = (r_outer - (len(rings) - 1) * band - band * 0.86) / (r_outer + 3)
    fit_centre_to_the_hole(fig, ax, centre_blocks, hole)
    assert_centre_fits_the_hole(fig, ax, centre_blocks, hole)
    # v2.7, last because the centre block is one of the obstacles and has only
    # just reached its final size. Two claims: every ring name is inside the
    # empty wedge, and none of them has landed on the counts in the hole or on
    # the first or last gene's spoke label.
    assert_wedge_labels_fit(fig, ax, wedge_labels, wedge,
                            obstacles=[t for t in ax.texts
                                       if t.get_text() and t not in wedge_labels])
    # v3.5, and it has to be after the fit loop rather than beside the font size:
    # "these names fit" and "these names are readable" are two claims and round 1
    # shipped the first while failing the second.
    assert_wedge_names_clear_the_floor(fig, wedge_labels, lay)
    # v3.1 / UX v3 A1. Last of the geometry checks, because every block has now
    # reached its final size and position: the centre has been fitted and
    # re-stacked, the wedge labels have been shrunk to fit, the stamps are
    # wrapped. Five collisions on the slide render survived four releases
    # because nothing compared a drawn string with another drawn string.
    assert_text_does_not_overprint_text(fig, text_blocks + centre_blocks,
                                        where=f"{lay.name} render")
    # v3.0. The four claims a circle may never be without, checked against the
    # text this render actually drew. The centre block is measured last because
    # `fit_centre_to_the_hole` may have rewritten nothing but has certainly
    # resized it, and the counts line has to be legible, not merely present.
    assert_figure_states_the_minimum(
        "\n".join(t.get_text() for t in text_blocks + centre_blocks),
        input_count, rendered_count, filter_notes, lay.name)

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    # `bbox_inches=None` is not enough: matplotlib resolves a None kwarg from
    # the rcParam, which pycirclize has already set to "tight". The canvas is now
    # exactly what the Layout declares.
    with plt.rc_context(SAVE_RC):
        fig.savefig(out_svg, format="svg")
    _add_wedge_tooltips(out_svg, rings, {
        "regional_specificity":
            f"this run: {region} TPM ÷ median across "
            f"{gtex_panel_size(cells_by_ring) or 'the GTEx panel'}",
    })
    if out_png is not None:
        # Convenience only. SVG plus the CSV is the export that matters for v1
        # (PLAN §5); this exists so a run can be eyeballed without a browser.
        with plt.rc_context(SAVE_RC):
            fig.savefig(out_png, format="png", dpi=110)
    plt.close(fig)
    return FigureResult(out_svg, csv_rows, cell_ids, len(cell_ids),
                        caption=cap,
                        ordering_note=ordering_note,
                        filter_notes=list(filter_notes),
                        web_caveats=web_caveats)


def _rings_norms_cmaps(cells_by_ring, de_source=None):
    """The three things every render of this data has to agree about.

    v3.0 factors this out of `render` because `render_legend` draws the same
    scales onto a different canvas, and a legend whose ramps or ticks were
    computed by a second code path would be a legend that could disagree with
    its figure -- which is the one failure mode a detachable legend introduces.
    They are derived from `cells_by_ring` and `de_source` and nothing else, so
    two renders of one run cannot differ here.

    `de_source` reaches this function rather than only `render` for the same
    reason the rest of it is shared: the figure, the detachable legend and the
    web scales panel all resolve their ring list here, and a ring labelled
    "logFC (yours)" on one of them and "logFC (Atlas)" on another would be
    exactly the disagreement this factoring exists to prevent.
    """
    rings = [r for r in ring_specs_for(de_source) if r.key in cells_by_ring]
    # v2.6: one colormap per ring, chosen by the ring's data type. The check that
    # no two rings outside a declared-comparable family can paint the same
    # colour runs on the rings this render is actually drawing, because a render
    # of a subset is the case where two families could end up adjacent that are
    # not adjacent in the full twelve.
    cmaps = {r.key: ring_cmap(r) for r in rings}
    assert_rings_are_distinguishable(rings)
    for r in rings:
        assert_within_domain(r, cells_by_ring[r.key])
    norms = {r.key: _norm_for(r, cells_by_ring[r.key]) for r in rings}
    return rings, norms, cmaps


def _add_wedge_tooltips(out_svg: Path, rings: Sequence[RingSpec],
                        run_detail: dict[str, str] | None = None) -> None:
    """The hover text, written into the artefact rather than into the app.

    Puck asked for "a tool tip on hover or something" where a short name cannot
    stand alone. The `or something` is decided by where it has to keep working:
    `app.js` inlines `figure.svg` into the page, so an SVG `<title>` is a native
    browser tooltip with no JavaScript -- and, because it lives in the file, the
    downloaded SVG still has it when it is opened in a browser weeks later. A
    handler in `app.js` would have been a tooltip that only existed while the
    app was running, which is the opposite of what this build exports.

    What it is NOT is where the meaning lives. Illustrator, PowerPoint, the PNG
    and a projected slide have no hover, so every ring's printed label has to be
    honest without this. That is `wedge_tooltip`'s whole discipline: it is set
    on the rings whose short label is *incomplete*, never on one that would be
    wrong without it.

    matplotlib has no API for this, so it is a post-pass on the saved file.
    Anchored on the `ringname|<key>` gid rather than on the drawn string,
    because `fit_wedge_labels` may have wrapped that string onto two lines.
    """
    tips = {s.key: s.tooltip for s in rings if s.tooltip}
    # A ring whose meaning depends on the run gets that number here rather than
    # a generic phrase. `regional_specificity` is the case Puck named: "region
    # TPM / median across 54 tissues" is what it means, and 54 is this run's
    # panel, not a constant.
    for key, extra in (run_detail or {}).items():
        if tips.get(key) and extra:
            tips[key] = f"{tips[key]} · {extra}"
    if not tips:
        return
    text = out_svg.read_text()
    for key, tip in tips.items():
        anchor = f'<g id="ringname|{key}">'
        if anchor not in text:            # no wedge on this layout
            continue
        safe = (tip.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        text = text.replace(anchor, f"{anchor}\n   <title>{safe}</title>", 1)
    out_svg.write_text(text)


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
    # v3.1 / UX v3 A1. Every block here is created at the same nominal y and
    # `stack_centre_blocks` then places all of them, in this list's order, by
    # their own measured heights.
    #
    # Until v3.1 each line carried its own constant -- 0.575, 0.542, 0.512, then
    # a hand-decremented `y` -- while only the font SIZES were multiplied by
    # `lay.centre_scale`. On `audit` and `web` that factor is 1.0 and the
    # constants had been tuned for it, so it looked right for four releases. On
    # `slide` it is 1.15: the type grew 15%, the spacing did not, and the block
    # printed through itself on the one artefact that gets projected.
    #
    # The constants are gone rather than corrected. Leaving them would leave the
    # file with two layout models, one of them dead, and the next person to
    # adjust the centre would adjust the dead one.
    Y = 0.5
    out = [
        ax.text(0.5, Y, FIGURE_TITLE, ha="center", va="center", size=15 * k,
                weight="bold", transform=ax.transAxes),
        ax.text(0.5, Y, region, ha="center", va="center", size=11 * k,
                transform=ax.transAxes, color="#333333"),
        ax.text(0.5, Y, _wrap(parts, 2), ha="center", va="center", size=9.5 * k,
                transform=ax.transAxes, color="#111111"),
    ]
    if lay is None or lay.centre_detail:
        out.append(ax.text(0.5, Y, detail, ha="center", va="top", size=6.8 * k,
                           transform=ax.transAxes, color="#555555"))

    # UX A3. v1 printed counts by category only, so the slide could not answer
    # "where's TP53?" and neither could the CSV beside it. On the broken fixture
    # more than half the input vanished and the artefact was silent about which
    # half. The names now travel with the figure.
    if dropped_names and lay is not None and not lay.centre_detail:
        # v3.0: where "named" points depends on which render this is, and being
        # wrong about it is COUNCIL V5. The slide is handed out with a
        # `caption.txt`; the web render is inside a page with a Drop ledger tab
        # and no caption anywhere. The audit render names them outright.
        where = "in the Drop ledger tab" if lay.name == "web" else "in the caption"
        out.append(ax.text(0.5, Y, f"the {len(dropped_names)} not shown are named "
                                   f"{where}", ha="center", va="top",
                           size=7.5 * k, transform=ax.transAxes, color="#8a4b00"))
    elif dropped_names:
        shown = list(dropped_names)[:9]
        more = len(dropped_names) - len(shown)
        text = _wrap(shown, 3) + (f"\n\u2026 and {more} more" if more else "")
        out.append(ax.text(0.5, Y, "not shown: " + text, ha="center", va="top",
                           size=6.5 * k, transform=ax.transAxes, color="#8a4b00",
                           family="monospace"))
    # v2.5 item 1: "ranking is provisional and not validated" is gone from here,
    # because nothing is ranked. The caveat existed only to qualify the headline
    # and the score, and a caveat about a thing the figure no longer does is not
    # honesty, it is noise. What replaces it is the ordering rule, which is a
    # statement about what this circle *is*.
    #
    # The centre block lives in the hole at the middle of the rings by design, so
    # it is checked against the hole rather than against the circle.
    return out


def _fmt(x: float) -> str:
    """A tick label a reader can act on: no scientific notation for ordinary
    numbers, no fifteen digits for a ratio."""
    ax = abs(x)
    if ax >= 1e5 or (0 < ax < 1e-3):
        return f"{x:.0e}".replace("e+0", "e").replace("e-0", "e-")
    if ax >= 100:
        return f"{x:,.0f}"
    if ax >= 10:
        return f"{x:.0f}"
    if ax >= 1:
        return f"{x:.2g}"
    return f"{x:.3g}"


def _scale_ticks(spec: RingSpec, norm) -> list[tuple[float, str]]:
    """(position in 0..1, label) for one ring's scale, in the ring's own units.

    The position is where the colour sits; the label is the *raw* number, which
    is not the same thing on a transformed ring and is the reason V6 asks for
    the transform to be printed. `regional_specificity` is drawn on log10 and
    ticked in x-fold; `neg_log10_padj` is drawn on log10(1+x) and ticked in
    -log10 padj.
    """
    if spec.categorical:
        # COUNCIL V12 again, on the scale this time: a categorical ring has no
        # Normalize, and v2.5's key printed "no values" for it beside a ring
        # full of drawn cells. Its levels are the labels.
        vals = sorted(spec.categorical)
        return [((i + 0.5) / len(vals), _fmt(v)) for i, v in enumerate(vals)]
    if norm is None:
        return []
    lo, hi = float(norm.vmin), float(norm.vmax)
    if spec.levels and spec.domain:
        # Centred on each swatch, because that is where the colour is. A tick at
        # the join between two swatches labels neither.
        n = spec.levels
        return [((i + 0.5) / n, _fmt(spec.domain[0] + (spec.domain[1] - spec.domain[0])
                                     * i / (n - 1))) for i in range(n)]
    stops = [0.0, 1.0]
    if spec.scale == "diverging" and lo < 0 < hi:
        # Zero is the whole point of a diverging ring; it gets a tick.
        stops = [0.0, (0.0 - lo) / (hi - lo), 1.0]
    return [(t, _fmt(_unscaled(spec, lo + (hi - lo) * t))) for t in stops]


def _draw_ring_scales(fig, rings, norms, cmaps, lay, header: bool = True,
                      start: int = 1, header_size: float | None = None) -> list:
    """UX A5, and the reason it was "the one that gets a question you can't
    answer": there was no colour bar anywhere, and the only statement of a
    ring's range was 6.6 pt monospace in the left margin -- 3.3 pt on a slide.

    One scale per ring, in the ring's own domain and units, in a column beside
    the circle. The figure centre is at its geometric limit
    (`assert_centre_fits_the_hole` refused to render during v2.5), so the hole
    was never a candidate.

    Each row says four things, and they are four different claims:
      * which ring it is -- the number and the name, matching the radial order;
      * what the colours are -- the ring's own ramp or its own swatches, so a
        reader decodes a cell against the ring it came from and nothing else;
      * what the numbers are -- ticks in the units the ring measures in;
      * what was done to the domain, and by whom -- the transform sentence and
        the source. The slide render carries the source too, which is §v2.3
        Still open 4.
    """
    import textwrap

    x0, y0, w, h = lay.scale_box
    n = len(rings)
    ts, tk = lay.scale_title_size, lay.scale_tick_size
    # Figure-fraction height of one line of type, from points.
    line = lambda pt: pt * 1.35 / (fig.get_figheight() * 72.0)  # noqa: E731
    bar_h = min(0.011, h / n * 0.20)

    def _title(i, spec):
        return "\n".join(l.rstrip() for l in
                          _key_lines(f"{i}. ", spec.label, "", limit=lay.scale_wrap))

    def _note(spec):
        # v2.8: the source moved off this line and onto its own, because it is
        # now a link and an `<a>` has to wrap the source and nothing else -- a
        # reader must not find the units clickable and land on a doc page for
        # them. The units stay here, where they always were.
        lines = textwrap.wrap(spec.units, lay.scale_wrap + 6) or [""]
        if lay.scale_detail and spec.transform:
            lines += textwrap.wrap(spec.transform, lay.scale_wrap + 6)
        return "\n".join(lines)

    def _source(spec):
        return "\n".join(textwrap.wrap(spec.source_line, lay.scale_wrap + 6) or [""])

    # v3.0: `start` so the legend file's second column continues 7..12 rather
    # than restarting at 1. A ring number that does not match the radial order
    # is a wrong figure, not a cosmetic slip.
    titles = {sp.key: _title(i, sp) for i, sp in enumerate(rings, start=start)}
    notes = {sp.key: _note(sp) for sp in rings}
    srcs = {sp.key: _source(sp) for sp in rings}

    # A ring's label is as long as its caveat and its units are as long as its
    # source, so how tall a row needs to be is not knowable when the font sizes
    # are chosen. Rows are laid out end to end and the type is fitted to the
    # column, the same way the centre block is fitted to the hole -- and for the
    # same reason: v2.5's slide render is where UX A4 was found, and a column
    # that silently overprints itself is that finding in a new place.
    # v3.1 / UX v3 A1. This budget used to be a second, independently written
    # formula for the height of a row, and it did not agree with the loop below
    # that actually lays one out: it counted `note_lines + 2` and `src_lines + 1`
    # against a drawing that advances by `note_lines + src_lines + 1.15`, and it
    # left out the 0.2 and 0.45 line-gaps entirely. On the audit canvas at twelve
    # rings the two differ by 0.016 of the figure height -- enough that the loop
    # concluded "0.8816 fits in 0.8822" and then drew a column 0.8975 tall,
    # which ran off the bottom and printed `derived` inside the footer.
    #
    # One function now, used by the budget AND by the drawing, so the two cannot
    # drift apart again. It returns the exact `top`-to-next-`top` advance.
    def _row_advance(sp, ts_: float, tk_: float) -> float:
        return (line(ts_) * (titles[sp.key].count("\n") + 1 + 0.2)
                + bar_h + line(tk_) * 0.1
                + line(tk_) * 1.05
                + line(tk_) * (notes[sp.key].count("\n") + 1)
                + line(tk_) * (srcs[sp.key].count("\n") + 1)
                + line(ts_) * 0.45)

    scale = 1.0
    for _ in range(14):
        need = sum(_row_advance(sp, ts * scale, tk * scale) for sp in rings)
        if need <= h:
            break
        scale *= 0.94
    ts, tk = ts * scale, tk * scale
    blocks = []

    # The one sentence that replaces v2.5's "RESCALED WITHIN EACH RING". It is a
    # stronger claim now and it needs to be: the rings no longer merely have
    # different ranges, they have different colours and, on five of them, a
    # different kind of scale.
    # Split on purpose rather than wrapped: the first line is the claim and it
    # has to survive being read out of the SVG in one piece, which is how
    # tests/test_presentation.py checks it is on the figure and not clipped.
    # v3.0: `header=False` on the legend file's second column. The sentence is
    # the most important one in the column and it is drawn once per column
    # everywhere else; two identical amber blocks side by side on one canvas
    # read as two different warnings.
    if header:
        # v3.3 / UX v3 round 3 A7. Where a layout sizes this claim for its own
        # surface rather than letting the fitted row titles decide, it stops
        # being the column's header and becomes the figure's banner -- which is
        # what it always was, and what round 1's A2 turned it into on the app.
        #
        # It is anchored to the RIGHT edge rather than to the column's left one.
        # At 12 pt the 47-character claim is 535 px wide and the column is 464,
        # so left-anchored it ran 55 px off a 1600 px canvas and
        # `assert_text_within_canvas` refused every slide render. Wrapping was
        # the other option and it is the wrong one: the claim has to survive
        # being read back out of the SVG in one piece, which is how three tests
        # check it is on the figure at all.
        claim_pt = header_size or lay.claim_size or ts * 0.95
        if lay.claim_size is not None and header_size is None:
            blocks.append(fig.text(
                0.995, y0 + h + line(ts) * 0.5,
                COMPARABILITY_CLAIM + "\n"
                "a colour means nothing outside its own ring",
                fontsize=claim_pt, va="bottom", ha="right",
                color="#8a4b00", weight="bold"))
        else:
            blocks.append(fig.text(
                x0, y0 + h + line(ts) * 0.5,
                COMPARABILITY_CLAIM + "\n"
                "a colour means nothing outside its own ring",
                fontsize=claim_pt, va="bottom", ha="left",
                color="#8a4b00", weight="bold"))

    top = y0 + h
    for i, spec in enumerate(rings, start=start):
        # QA-v2-retest §5's mechanism, reused rather than reinvented: a ring
        # label whose tail is a caveat ("\u2014 NOT evidence of safety") wraps
        # onto a continuation line. Truncation is never the answer for a caveat,
        # and the scale column is the second place that would have been tempted.
        title = titles[spec.key]
        row_top = top
        blocks.append(fig.text(x0, top, title, fontsize=ts, va="top", ha="left",
                               color="#111111"))
        y = top - line(ts) * (title.count("\n") + 1) - line(ts) * 0.2

        # The bar. A stepped ring gets its swatches with a gap between them, so
        # "four levels" is visible as four things rather than read as a ramp
        # somebody chose to render coarsely.
        cax = fig.add_axes((x0, y - bar_h, w * 0.62, bar_h))
        cax.set_xticks([])
        cax.set_yticks([])
        if spec.levels or spec.categorical:
            sw = ring_swatches(spec)
            cax.set_xlim(0, len(sw))
            cax.set_ylim(0, 1)
            for j, colour in enumerate(sw):
                cax.add_patch(plt.Rectangle((j + 0.06, 0), 0.88, 1,
                                            facecolor=colour, edgecolor="none",
                                            clip_on=False))
            cax.set_facecolor("none")
            for side in cax.spines.values():
                side.set_visible(False)
        else:
            # Drawn as 256 unclipped rectangles rather than an `imshow`: an
            # image inside an axes carries a clipPath into the SVG -- and
            # `TestExportRemainsEditable` asserts there are none, so that an
            # Illustrator user cannot find part of the figure invisible and
            # unselectable. Rectangles also keep the export editable.
            cax.set_xlim(0, 1)
            cax.set_ylim(0, 1)
            ramp = FAMILIES[spec.family].hexes
            for j in range(len(ramp)):
                cax.add_patch(plt.Rectangle((j / len(ramp), 0), 1.02 / len(ramp), 1,
                                            facecolor=ramp[j], edgecolor="none",
                                            clip_on=False))
            for side in cax.spines.values():
                side.set_linewidth(0.5)
                side.set_color("#666666")

        ticks = _scale_ticks(spec, norms.get(spec.key))
        y -= bar_h + line(tk) * 0.1
        if ticks:
            # Ends are anchored to the ends of the bar; anything between is
            # centred on its own position, which is where the colour is.
            for t, label in ticks:
                ha = "left" if t <= 0.01 else "right" if t >= 0.99 else "center"
                blocks.append(fig.text(x0 + w * 0.62 * t, y, label, fontsize=tk,
                                       va="top", ha=ha, color="#333333"))
        else:
            blocks.append(fig.text(x0, y, "no values in this run", fontsize=tk,
                                   va="top", ha="left", color="#333333"))
        y -= line(tk) * 1.05

        note = notes[spec.key]
        blocks.append(fig.text(x0, y, note, fontsize=tk, va="top", ha="left",
                               color="#666666"))
        y -= line(tk) * (note.count("\n") + 1)

        # v2.8. The source, as a link where there is one.
        #
        # `set_url` is matplotlib's own SVG-backend feature and it writes
        # `<a xlink:href="..." target="_blank">` around this text in the saved
        # file -- so, exactly like the v2.7.1 tooltips, the link is *in the
        # artefact*. `app.js` inlines `figure.svg`, so it is clickable in the web
        # app with no JavaScript, and the downloaded SVG still opens the doc page
        # in a browser weeks later. The PNG backend ignores the url and the slide
        # PNG has no links at all, which is why `source_line` prints the domain:
        # the visible text is the fallback, not decoration on top of the link.
        #
        # The gid is `ringsource|<key>` -- the same handle idiom as the cells and
        # the wedge names, so a test can find this ring's link without matching
        # on the drawn string.
        doc = spec.source_doc
        t = fig.text(x0, y, srcs[spec.key], fontsize=tk, va="top", ha="left",
                     color="#0b5cad" if doc else "#666666")
        t.set_gid(f"ringsource|{spec.key}")
        if doc:
            t.set_url(doc.url)
        blocks.append(t)
        y -= line(tk) * (srcs[spec.key].count("\n") + 1)

        # The budget and the layout, compared on every row rather than trusted
        # to stay in step. This is the one line that would have caught the
        # original drift the day it was written.
        expected = row_top - _row_advance(spec, ts, tk)
        top = y - line(ts) * 0.45
        assert abs(top - expected) < 1e-9, (
            f"the scale column's height budget and its layout disagree on "
            f"{spec.key}: budget says the next row starts at {expected:.5f}, "
            f"the drawing put it at {top:.5f}. They must be the same arithmetic.")
    return blocks


def scale_rows(cells_by_ring, de_source=None) -> list[dict]:
    """The scale column as data, for the page to draw as HTML.

    v3.1 / UX v3 A2. The twelve scales are not leaving the app -- UX-v1-mvp.md
    A5 is binding and a reader must be able to decode a cell -- they are leaving
    the *SVG*, because a 19.5 in canvas in a 1388 px panel drew them at 5.8 to
    6.5 CSS px and the page around them is 13 px. Same numbers, same colours,
    same ticks, same source links; drawn by the browser at a size a browser can
    draw legibly.

    It is deliberately the **same functions** the renders use -- `ring_swatches`,
    `FAMILIES[...].hexes`, `_scale_ticks`, `spec.source_doc` -- rather than a
    second description of a ring. A panel whose ramp or ticks came from a second
    implementation could disagree with the figure beside it, which is the whole
    argument §v3.0 made for `render_legend` sharing `_draw_ring_scales`.
    """
    rings, norms, _ = _rings_norms_cmaps(cells_by_ring, de_source)
    out = []
    for i, spec in enumerate(rings, start=1):
        doc = spec.source_doc
        stepped = bool(spec.levels or spec.categorical)
        out.append({
            "n": i,
            "key": spec.key,
            "label": spec.label,
            "short_label": spec.wedge_label,
            # v3.7. The name the table's column head uses, carried here so the
            # one place in the decode panel that needs a ring's name short --
            # ring 8's categorical group heading -- names it the way the grid
            # does instead of the way the wedge does.
            "column_label": spec.column_label,
            "units": spec.units,
            "transform": spec.transform or "",
            "stepped": stepped,
            "colours": ring_swatches(spec) if stepped
            else FAMILIES[spec.family].hexes[::8],
            # COUNCIL V12. A categorical ring's colours are off every ramp, so
            # they need naming or they read as positions on one -- which is what
            # the first version of the HTML panel did, printing ring 8's three
            # states as `-1 / 0 / 1` from the tick labels while the figure's own
            # legend named them. Same order as `colours`, from the same sort, so
            # the panel cannot pair a colour with the wrong state.
            "categories": [(spec.category_labels or {}).get(v, str(v))
                           for v in sorted(spec.categorical)]
            if spec.categorical else [],
            "ticks": [{"at": round(t, 4), "label": lab}
                      for t, lab in _scale_ticks(spec, norms.get(spec.key))],
            "source_text": spec.source_line,
            "source_url": doc.url if doc else "",
            "tooltip": spec.tooltip,
        })
    return out


def _draw_ordering_and_filters(ax, ordering_note: str, filter_notes: Sequence[str],
                               stats, lay) -> list:
    """v2.5. Two claims about the figure that a reader cannot recover by looking.

    The spoke order carries meaning -- it always did -- and an unstated ordering
    rule is the same defect as an unlabelled colour scale. And a filtered plot
    that does not say it is filtered is a plausible wrong figure: it looks like
    the whole submitted set. Both go in the hole, where the counts already are.

    v3.1 / UX v3 A1: these two continue the centre block, so they are created at
    its nominal y and placed by `stack_centre_blocks` with everything above them.
    The `y` this used to take and decrement was the second half of the layout
    model that collided with itself on the slide.
    """
    import textwrap

    k = (lay.centre_scale if lay else 1.0)
    y = 0.5
    out = []
    # The hole is ~30 characters wide at this size, and both of these are
    # user-driven: a ring label is as long as its caveat and a filter list is as
    # long as the user's. Wrapped here rather than measured afterwards, because
    # `assert_centre_fits_the_hole` refuses to render instead of clipping, and a
    # filter statement that stops the figure being drawn helps nobody.
    if ordering_note:
        # v3.3 / UX v3 round 3 B13. 6.8 pt is 10.23 CSS px on the app render and
        # it was the LAST string under this review's 12.0 px floor there.
        #
        # Round 2's B10 proposed deleting it: it was one of a pair, said again
        # at 15.05 px in the footer 40 px below, and removing the small copy
        # removes the exception rather than arguing for it. Round 2's own fix
        # then removed the other half of the pair when the caveat line left the
        # canvas (A4), so deleting this one now would leave the app render
        # stating no spoke order at all on its own canvas -- which PLAN §3.1
        # requires ("decided, stated on the figure, and checked in the
        # artefact") and which §v3.0's "the figure alone must still not lie" is
        # exactly about. So the remaining fix is the other one: make it legible.
        #
        # 8.0 pt is 12.04 px at the app's 1.5046, the same arithmetic that set
        # the gene labels. The hole is not being asked to find the room out of
        # nowhere -- `fit_centre_to_the_hole` still shrinks the whole block to
        # the innermost ring and `assert_centre_fits_the_hole` still refuses the
        # render rather than clipping, so this is a request, not an override.
        out.append(ax.text(0.5, y, textwrap.fill(ordering_note, 34),
                           ha="center", va="top",
                           size=(8.0 if lay and not lay.scales_in_svg else 6.8) * k,
                           transform=ax.transAxes, color="#444444"))
    if filter_notes:
        n_marked = sum(1 for s in (stats or {}).values() if s.unfiltered_rings)
        lines = [f"FILTERED \u2014 {len(filter_notes)} active, this is not",
                 "your whole submitted set:"]
        for note in filter_notes:
            lines += textwrap.wrap(note, 32, subsequent_indent="   ")
        if n_marked:
            lines += textwrap.wrap(f"~ {n_marked} gene(s) kept: no data on a "
                                   f"filtered ring", 32)
        out.append(ax.text(0.5, y, "\n".join(lines), ha="center", va="top",
                           size=6.5 * k, transform=ax.transAxes, color="#8a4b00",
                           weight="bold"))
    return out


def _draw_contrast_note(ax, contrast_notes: Sequence[str], lay) -> list:
    """Stage 5: which Atlas contrast this run's genes came from, in the hole
    beside the counts -- so a screenshot of the circle alone still carries the
    contrast's prose name and its sign sentence (I8). Empty and draws nothing
    on a paste-path run, which is every run before stage 5 and every test that
    does not pass `contrast_notes`.
    """
    import textwrap

    if not contrast_notes:
        return []
    k = (lay.centre_scale if lay else 1.0)
    lines: list[str] = []
    for note in contrast_notes:
        lines += textwrap.wrap(note, 34)
    return [ax.text(0.5, 0.5, "\n".join(lines), ha="center", va="top",
                    size=6.8 * k, transform=ax.transAxes, color="#1a1a5e")]


def _draw_legend(fig, rings, norms, cmaps, lay, banner_width: float = 1.0) -> list:
    """The four cell states, the three categorical states, and the scale column.

    v2.5 also drew a numbered monospace ring key in the left margin and a single
    colour bar. Both are gone: the key's job -- "which ring is this and what is
    its range" -- is what each ring's own scale now does, beside its own
    colours, and the single bar was only ever honest because every ring shared
    one ramp. Keeping either would leave two places on the figure claiming to
    tell a reader how to decode a cell.
    """
    handles = [
        Patch(facecolor=s["fc"], hatch=s["hatch"], edgecolor=s["ec"], label=s["label"])
        for s in STATE_STYLE.values()
    ]
    # One swatch per ring would duplicate the column, and any single colour
    # would say the thing v2.6 exists to stop saying -- that one colour means
    # "a value". So the entry is an empty dashed box that points at the column
    # instead. Dashed and unfilled rather than white-filled, because a white
    # patch beside `not_applicable`'s white-with-dots is two states told apart
    # by a hatch alone.
    # v3.1 / UX v3 A2. On the app render there is no box and no column in the
    # SVG at all -- the page draws both as HTML at 13 px instead of as 6 px
    # vector text -- so what the circle must still carry on its own is the one
    # claim `assert_figure_states_the_minimum` will not let any layout drop. It
    # is drawn large, in the top-left corner, which is outside the inscribed
    # circle and therefore not an A6 collision.
    if not lay.scales_in_svg:
        # v3.1: wrapped to the width actually free beside the run-id stamp, not
        # to the canvas. See `_free_width_beside` -- the two corners met when the
        # canvas went from 19.5 in to 9.6, and the new overprint check is what
        # said so rather than a screenshot three rounds later.
        pt = WEB_BANNER_PT
        # v3.2 / round 2 A4. Two lines, not three, and the line it lost was
        # bought with 13 px of top margin rather than with type size.
        #
        # This banner is one of the two blocks the circle was trapped between:
        # at three lines it reached to within 0.6 px of `GENE000 (k=9/9)`, the
        # top spoke label, on every canvas height where the circle was still
        # large enough for `fit_wedge_labels` to place twelve ring names in the
        # 26° gap (`logs/v32-web-sweep.txt` — 83 candidates, none passing). The
        # second sentence said the same thing in 89 characters that it says in
        # 78, and the shorter one wraps to one line at the width left beside the
        # run-id stamp.
        text = _wrap_to_canvas(
            fig,
            COMPARABILITY_CLAIM + "\n"
            "decode every cell against that ring's own scale, on the right",
            pt, margin=(1.0 - banner_width) / 2.0)
        return [fig.text(0.018, 0.988, text, fontsize=pt, va="top", ha="left",
                         color="#8a4b00", weight="bold")]
    handles.append(Patch(facecolor="none", edgecolor="#111111", linestyle="--",
                         label="value — decode it against THIS ring's own scale, right"))
    # COUNCIL V12: the categorical ring is off every ramp, so its three colours
    # need naming or they read as positions on one.
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
    return _draw_ring_scales(fig, rings, norms, cmaps, lay)


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
                          filter_notes: Sequence[str], wedge: str = "") -> None:
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
      4. **The wedge rule is printed** (v2.7). The gap at 12 o'clock narrows
         every spoke, and Puck's condition on it was that the rule be stated
         rather than applied quietly. A figure that reserves the gap and does
         not say what it cost is the V5 defect by omission: the reader sees a
         geometry and is given no way to know it was chosen.
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
    if wedge and wedge not in body:
        raise AssertionError(
            "this figure reserves an angular gap for the ring names and does not state "
            "the rule. The gap narrows every spoke; say so, and say what it cost, rather "
            "than scaling the geometry silently."
        )


def assert_the_id_rule_is_kept(spoke_labels: Sequence[str],
                               notes: Sequence[str]) -> None:
    """v2.9. The figure says where the Ensembl ids are; this checks it is true.

    Two halves, and the second is the one that matters in six months:

    1. the rule is printed. A figure that quietly has no gene ids is a figure a
       reader assumes has none available, and they are one click away;
    2. **no spoke label carries an id anyway.** `_ID_NOTE` is a claim about the
       rim, so the rim is checked against it. If someone later puts the id back
       on a spoke -- reasonably, because at six genes it *looks* like it would
       fit -- the figure would be printing a sentence its own labels contradict,
       which is COUNCIL V5 exactly. It fails here instead, and then
       `assert_no_text_over_the_rings` would have failed on the geometry anyway.
    """
    if not any(_ID_NOTE == n for n in notes):
        raise AssertionError(
            "the figure does not say where its Ensembl ids are. They exist for "
            "every rendered gene; a reader who cannot see one must be told it "
            "is in the drawer and in values.csv, not left to assume it is absent."
        )
    offenders = [l for l in spoke_labels if "ENSG" in l]
    if offenders:
        raise AssertionError(
            "the footer says the Ensembl id is NOT on the rim and these spoke "
            f"labels carry one: {offenders[:3]}. The id needs 155 px of radial "
            "room and there are 121 px before the scale column; either the "
            "sentence is wrong or the labels are."
        )


def clock_phrase(lay) -> str:
    """How the figure describes where its first spoke is.

    v2.7 made the old wording -- "starts at 12 o'clock" -- false on the audit
    render and left it true on the slide, because only one of the two reserves a
    gap there. COUNCIL V5 is a figure describing a geometry it does not have, so
    the sentence is derived from the layout instead of written down twice.
    """
    if lay.wedge_deg > 0:
        # v3.2 / round 2 A4: the gap is reserved on the app render too, and it is
        # still where the circle starts — but it holds no names there, so calling
        # it the ring-name gap would be V5 again, one word further in.
        return ("runs clockwise from the ring-name gap at 12 o'clock"
                if lay.wedge_labels_in_svg else
                "runs clockwise from the reserved gap at 12 o'clock")
    return "starts at 12 o'clock and runs clockwise"


def wedge_note(lay, n_genes: int, space: float, short: bool = False) -> str:
    """What the 12 o'clock gap costs this figure, in the figure's own numbers.

    Puck's condition on the wedge was explicit: if the gap has to shrink at large
    gene counts, say the rule on the figure rather than scaling silently. The
    rule is that it does *not* shrink -- `Layout.wedge_deg` is a constant, the
    spokes absorb the whole cost, and a colour or an angle therefore means the
    same thing whether the submission held 8 genes or 50. A gap that narrowed as
    genes were added would make the innermost ring's label size a function of
    somebody's gene count, which is COUNCIL V6's defect wearing a different hat.

    So the sentence states the constant, the spoke width it produces, and the
    spoke width a full circle would have produced. All three, because "26°" on
    its own does not tell a reader what it cost and the point of printing it is
    that they can see the trade rather than take it on trust.
    """
    if not n_genes:
        return ""
    if lay.wedge_deg <= 0:
        # The slide. Not a preference and not an omission: the constraint is
        # radial and no wedge angle fixes it, so the figure states the
        # measurement that decided it rather than leaving a reader to wonder why
        # the two renders differ.
        if short:
            return ("ring names are on their own scales in the column, right, not inside "
                    "the circle — at this size a ring band is 11 px and the names would "
                    "overlap each other")
        return (
            "Ring names are in the column beside the circle, not inside it. On this "
            "render the twelve rings span 134 px of radius — 11 px a ring — against 13 px "
            "for one line of legible type, so names at their own radii would overlap each "
            "other. The audit render, whose circle is twice as wide, puts them in a gap at "
            "12 o'clock instead."
        )
    got = (360.0 - lay.wedge_deg - (n_genes - 1) * space) / n_genes
    full = (360.0 - n_genes * space) / n_genes
    if not lay.wedge_labels_in_svg:
        # v3.2 / round 2 A4. The app render: the gap is reserved and costs the
        # spokes exactly what it costs on the audit render, and it holds no
        # names. Both halves are said, because a reader who can see a gap and is
        # told nothing about it has been left to guess — Puck's own condition on
        # the wedge — and because a reader who is told the names are "inside the
        # circle" on a render where they are not is reading COUNCIL V5.
        where = ("each ring is numbered and named in the panel beside this "
                 "figure, with its own scale")
        if short:
            return (f"a FIXED {lay.wedge_deg:g}° gap is reserved at 12 o'clock — so spokes "
                    f"are {got:.2f}°, not the {full:.2f}° of a full circle; {where}")
        return (
            f"A {lay.wedge_deg:g}° gap is reserved at 12 o'clock. It is FIXED — it does not "
            f"shrink as genes are added — so the spokes carry the cost: {n_genes} spokes at "
            f"{got:.2f}° against the {full:.2f}° a full circle would give "
            f"({(got / full - 1) * 100:+.1f}%).\nThe ring names are not drawn inside it on "
            f"this render: {where}."
        )
    if short:
        # UX A4's rule for the slide: one line, and it has to carry the claim
        # rather than point at a caption somebody has to be handed. The three
        # numbers survive; the reasoning goes to the caption.
        # v3.1: where the scales are depends on the render, and being wrong
        # about it is COUNCIL V5 -- a figure describing a layout it no longer
        # has. The app render has no column; the page draws the scales as HTML
        # beside the figure, which is why they are legible there at all.
        return (f"ring names are inside the circle, in a FIXED {lay.wedge_deg:g}° gap at 12 "
                f"o'clock — so spokes are {got:.2f}°, not the {full:.2f}° of a full circle; "
                + ("each ring's scale is in the panel beside this figure"
                   if not lay.scales_in_svg else
                   "each ring's scale is in the column, right"))
    # Two lines, not one. The footer block is centred and the other five notes
    # already run most of the canvas width; measured at 14 genes this sentence
    # reached x=1903 of 1950 as a single line, i.e. 46 px from the clip
    # `assert_text_within_canvas` exists to catch.
    return (
        f"Ring names are inside the circle, in a {lay.wedge_deg:g}° gap at 12 o'clock, each at "
        f"its own ring's radius. The gap is FIXED — it does not shrink as genes are added — "
        f"so the spokes carry the cost:\n"
        f"{n_genes} spokes at {got:.2f}° against the {full:.2f}° a full circle would give "
        f"({(got / full - 1) * 100:+.1f}%). Each ring's SCALE, range and source stay in the "
        f"column beside the circle: at the inner radii the gap is too narrow for a colour bar."
    )


def gtex_panel_size(cells_by_ring) -> str:
    """How many GTEx tissues this run's median was taken across, as a phrase.

    Read off the cells rather than hard-coded: the panel is whatever the adapter
    actually returned for these genes, and "54" in a sentence the adapter did not
    produce is a number the figure cannot stand behind.
    """
    for c in cells_by_ring.get("regional_specificity", {}).values():
        if c.state == "value" and isinstance(c.raw, dict) and c.raw.get("n_tissues"):
            return f"{c.raw['n_tissues']} GTEx tissues"
    return ""


# v2.9. Puck: "can you put the ensembl id's next to the gene name?" -- and, if
# it does not fit on the rim, "I would rather the rule were explicit and stated
# on the figure than silently truncated".
#
# It does not fit, at ANY gene count, and the reason is not the one either of us
# expected. A spoke label is drawn `orientation="vertical"`, so it runs radially
# outward: an Ensembl id does not crowd the neighbouring spoke, it reaches
# further out. Measured on the audit canvas (`logs/v29-rim-measure.txt`), there
# are **121 px** between the circle's edge (x=1365) and the scale column
# (x=1486). `GENE001 (k=9/9)` advances 69 px at 8 pt and fits with room to
# spare; `GENE001 · ENSG00000157764 (k=9/9)` advances **155 px** and is 34 px
# into the column. At fifty genes the label shrinks to 6.56 pt and it is still
# 127 px -- over. So the angular width Puck asked me to check (22.00 deg at
# fourteen genes, 4.72 at fifty) never becomes the binding constraint; the
# column does, and it binds at six genes as hard as at fifty.
#
# The two ways to make it fit are both worse than not fitting: delete the scale
# column, which is the thing v2.6 exists to provide, or take the spoke labels
# below legibility, which is UX A4. A conditional rule -- ids below some gene
# count -- was the other option Puck offered and there is no count where it is
# true, so offering it would be a threshold with nothing on the near side.
#
# Therefore: never on the rim, always in the drawer and in values.csv, and the
# figure says so rather than leaving a reader to conclude the id is not there.
_ID_NOTE = (
    "Ensembl id per gene: in the click-drawer and in values.csv, deliberately NOT on the "
    "rim at any gene count — a spoke label runs radially and the id needs 155 px where "
    "there are 121 between the circle and the scale column. Symbols are ambiguous; the id "
    "is what was queried."
)


def _stamp_notes(ordering_note: str, filter_notes: Sequence[str], region: str,
                 cells_by_ring, gene_flags=None, subtitle: str = "",
                 wedge: str = "", clock: str = "") -> list[str]:
    """The footer's sentences, built once so `assert_footer_is_true` checks
    exactly the text that gets drawn rather than a copy of it."""
    n_tissues = gtex_panel_size(cells_by_ring)
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
        # v2.7. This used to read "starts at 12 o'clock". 12 o'clock is now the
        # middle of the ring-name gap and the first spoke begins half a wedge
        # clockwise of it. COUNCIL V5 is about exactly this -- a figure
        # describing a geometry it no longer has -- so the words move with the
        # layout.
        f"The circle {clock or clock_phrase(AUDIT)} — {ordering_note}. "
        f"Region affects exactly one ring (regional specificity).",
        "Pipeline: furthest phase counts only drugs indicated for a disease with mechanistic "
        "evidence linking it to THIS target. A drug that hits the target for an unrelated "
        "indication sets no phase; those genes are drawn 'not applicable', not 'low'.",
    ]
    notes.append(_ID_NOTE)
    if wedge:
        notes.append(wedge)
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


def slide_line(ordering_note: str, filter_notes: Sequence[str],
               wedge_short: str = "", where: str = "see the caption") -> str:
    """UX A4: the one line the slide render carries instead of the footer block.

    The filters are on it, first, when there are any. A slide is the artefact
    that goes in front of other people, and "this is not the whole list you were
    given" is not something to leave in a caption somebody has to be handed
    separately -- the same argument QA-v2-retest §5 made about the safety caveat.
    """
    base = " · ".join([f"the safety ring is {SAFETY_RING_CAVEAT}",
                           ordering_note,
                           "rings are scaled independently of one another",
                           # v3.0: where the rest of it is depends on which
                           # render this is. A slide is handed out with a
                           # caption.txt; the web render is inside a page whose
                           # tabs carry the same text and which has no caption
                           # anywhere. Pointing a reader at a file that does not
                           # exist for them is COUNCIL V5 in one clause.
                           where])
    # v2.7. Its own line rather than a fifth clause: at 10 pt on a 1600 px canvas
    # the four clauses already run to ~900 px and `assert_text_within_canvas` is
    # what caught the last sentence that was appended to a line that was full
    # (UX A4). The wedge rule is a claim about the geometry the reader is looking
    # at, so it stays on the slide rather than going to the caption alone.
    if wedge_short:
        base = base + "\n" + wedge_short
    if not filter_notes:
        return base
    # Its own line, above the rest. At 10 pt across a 1600 px canvas the two do
    # not fit side by side -- `assert_text_within_canvas` said so rather than
    # shipping a sentence clipped at "not the whole" -- and the filters are the
    # half a reader must not miss, so they go first and get the width.
    return ("FILTERED — not your whole submitted set: " + " · ".join(filter_notes)
            + "\n" + base)


def caveats_for_the_page(ordering_note: str, filter_notes: Sequence[str],
                        wedge_short: str = "") -> tuple[str, list[str]]:
    """v3.2 / UX v3 round 2 A4. Split the app render's caveat line in two: what
    the circle keeps, and what the page draws in HTML beside it.

    Returns `(what the render still draws, what the page must draw)`.

    A4 asked for a `web` canvas short enough that the circle is whole in a
    1440 x 900 viewport, and said to let the geometry assertions arbitrate the
    margins. They arbitrated and returned nothing: 83 (height, bottom,
    axes-height) triples in `logs/v32-web-sweep.txt` and 14 more in
    `logs/v32-web-fit-sweep.txt`, and every one failed. The walls are 25 px
    apart — under a bottom margin of about 0.21 this line lands inside the
    bottom spoke labels, and over it the circle is too small for
    `assert_wedge_labels_fit` to place twelve ring names in the 26° gap. Neither
    is a margin that can be nudged; the line itself is four to five wrapped
    lines of 10 pt, 0.83 in of a 8.2 in canvas, and that is what had to give.

    So this is A2's move applied to the footer, and §v3.0's own rule for this
    layout carried out rather than half-carried-out: *"`web` drops exactly what
    the page itself already says"*. What the render keeps is the one clause a
    reader must not miss and the page cannot state as loudly — **FILTERED** —
    because it is the difference between this circle being the submitted set and
    a subset of it, and the only other copy of it is the centre's 6.5 pt one
    (9.8 CSS px, under this review's floor). The rest goes to the page at 13 px,
    in document order directly under the circle, not behind a click.

    **Nothing is relaxed by this and that is checkable rather than asserted in a
    comment.** `assert_figure_states_the_minimum` still passes on the web render
    on its own strings: the title and the comparability claim from the banner,
    both counts from the hole, and `FILTERED` from this line and from the centre.
    `assert_footer_is_true` reads `notes + caption`, which are unchanged — the
    audit render and `caption.txt` carry every clause they always did, and the
    slide render is not touched at all.

    v3.3 / UX v3 round 3 B14. The clauses come back CLASSIFIED, as
    `{"kind": "warn"|"how"|"where", "text": ...}`. A safety caveat and a note about
    where the ring names are are not the same kind of statement, and round 2
    shipped five of them at one weight -- `UX-v1-mvp.md` B3's "two sentences
    with different jobs reading as one grey run-on", one surface along.

    v3.5 item 2. `where` is the third kind and it is one clause: the one that
    explains the gap at 12 o'clock. It is classified rather than matched on,
    because the page has to put it somewhere else and the alternative -- `app.js`
    recognising the sentence by its words -- is a second implementation of which
    sentence this is, in a file that cannot see when the sentence changes.

    It needs a different place because of what it explains. The others are
    caveats about the DATA and can be read after the figure; this one answers
    "why is there a hole in my circle", which a reader asks while looking at the
    hole. Under the circle it was 953 px down the document on a 900 px laptop --
    below the fold, on the one page element whose whole job is to be read at the
    same time as the thing it is about. The page draws it at the top of the
    decode panel beside the figure (`#wedge-note`), and exactly once: this
    function is the only place it comes from, and round 2's A6 is what happens
    when a sentence is moved by copying it.

    And one clause is gone rather than reclassified. `slide_line` carries
    *"rings are scaled independently of one another"*, which is the
    comparability claim in other words; on the slide that is the only statement
    of it below the column header, and on the app it sat 13 px under a 16.55 px
    banner making the same claim. That is **A6 restored by A6's own fix**, found
    by round 3 reading round 2's output. The slide keeps it; the page does not.
    """
    keep = ("FILTERED — not your whole submitted set: " + " · ".join(filter_notes)
            if filter_notes else "")
    page = [("warn", f"The safety ring is {SAFETY_RING_CAVEAT}."),
            ("how", ordering_note)]
    if wedge_short:
        page.append(("where", wedge_short[0].upper() + wedge_short[1:]))
    page.append(("how", "Every caveat this run carries is in the "
                        "What this is not tab."))
    return keep, [{"kind": k, "text": t} for k, t in page if t]


def _column_above_the_footer(fig, lay, stamps) -> "Layout":
    """Raise the scale column's floor to clear the footer this run produced.

    The column is laid out top-down from `scale_box`'s top and stops where its
    twelve rows stop; `scale_box`'s *bottom* is what the fit loop treats as the
    budget. Both were constants, tuned against a 14-gene run, and the footer is
    not a constant: it grows with the drop categories, the dropped gene names
    and the active filters. At three genes it reaches 165 px further right and
    9 px higher than the column's last line, so `derived` was printed inside
    `Regional specificity = Liver TPM ÷ median across 54 GTEx tissues`.

    Only the vertical budget moves, and only when the footer actually reaches
    under the column -- a footer narrower than `scale_box[0]` costs the column
    nothing, and paying for it anyway would shrink the type on every render for
    a collision that does not happen on most of them. Shrinking type is UX A4
    and is the last resort here, not the first.
    """
    import dataclasses

    x0, y0, w, h = lay.scale_box
    if not stamps:
        return lay
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    W, H = fig.get_size_inches() * fig.dpi
    top = 0.0
    for t in stamps:
        if not (t.get_text() or "").strip():
            continue
        bb = t.get_window_extent(renderer=r)
        if bb.x1 / W <= x0:           # ends before the column starts
            continue
        if bb.y1 / H >= y0 + h:       # the run-id stamp, above the column's top
            continue                  # -- it cannot be under the column's floor
        top = max(top, bb.y1 / H)
    if top <= 0.0:
        return lay
    floor = top + 4.0 / H             # four pixels of air, not a judgement call
    # A floor that ate the column would be a worse figure than the collision:
    # the rows would be fitted into nothing and the type would go to its own
    # floor. `assert_text_does_not_overprint_text` is what then refuses the
    # render, which is the right way round -- fail, do not silently shrink.
    if floor <= y0 or floor >= y0 + h * 0.5:
        return lay
    return dataclasses.replace(lay, scale_box=(x0, floor, w, h - (floor - y0)))


def _wrap_to_canvas(fig, text: str, pt: float, margin: float = 0.04) -> str:
    """Re-wrap a stamp line to the width of the canvas it is being drawn on.

    Each existing line is wrapped independently, so the deliberate breaks
    `slide_line` already makes -- the filter warning on its own line above
    everything, the wedge rule on its own line below -- survive.
    """
    import textwrap

    if not text:
        return text
    width_pt = fig.get_size_inches()[0] * 72.0 * (1.0 - 2 * margin)
    # DejaVu Sans averages ~0.55 em per character over prose of this kind.
    cols = max(40, int(width_pt / (pt * 0.55)))
    return "\n".join(
        "\n".join(textwrap.wrap(line, cols)) if len(line) > cols else line
        for line in text.splitlines())


def _free_width_beside(fig, artists, x0: float, top: float = 0.90) -> float:
    """How much of the canvas width is free, left of anything in the top band.

    v3.1 / UX v3 A2. The app render's comparability banner is drawn top-left and
    the run-id stamp is drawn top-right, and on a 19.5 in canvas that was two
    corners with a continent between them. A2 shrank the canvas to 9.6 in and
    the two met: `assert_text_does_not_overprint_text` refused the render with
    the banner 62 px into `run V31-COUNCIL-COLD · 62d40a38`.

    Measured rather than nudged, for the reason A1 is about: a constant that
    clears today's run id is a constant that a longer one walks through. Returns
    a width in figure fractions, starting at `x0`.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    W, H = fig.get_size_inches() * fig.dpi
    right = 1.0
    for t in artists:
        if not (t.get_text() or "").strip():
            continue
        bb = t.get_window_extent(renderer=r)
        if bb.y0 / H < top:               # not in the top band
            continue
        if bb.x0 / W <= x0:               # starts left of us; not a right edge
            continue
        right = min(right, bb.x0 / W)
    # Six points of air, in fractions, so a wrap that lands exactly on the
    # stamp's left edge is still a wrap that clears it.
    return max(0.25, right - x0 - 6.0 / W)


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
        #
        # v3.1 / UX v3 A1: this line stayed exactly where it was, and the scale
        # column moved instead (`_column_above_the_footer`). The obvious fix --
        # centre it on the circle rather than on the canvas, so it cannot reach
        # the column -- was tried and `assert_no_text_over_the_rings` refused it:
        # shifted left, it came within 4 px of `GENE006 (k=9/9)`, the bottom-left
        # spoke label. The gap under the circle belongs to the spoke labels, so
        # the caveat keeps the width and the column gives up the height.
        # v3.1: wrapped to the canvas it is actually on. `slide_line` builds
        # clauses joined by `·` and breaks itself at two or three fixed places,
        # which fitted a 16 in slide and nothing else -- on the 12 in app canvas
        # the first line ran 43 pt off the left edge and 43 off the right, and
        # `assert_text_within_canvas` refused the render. Re-wrapping rather
        # than shrinking, because shrinking here is UX A4.
        # v3.2 / round 2 A4: on the app render this is the FILTERED clause alone
        # and nothing else, so on an unfiltered run there is no line to draw at
        # all. Not drawn rather than drawn empty: an empty artist still has a
        # box, and `_free_width_beside` and `_column_above_the_footer` both
        # measure boxes.
        if slide_text:
            out.append(fig.text(0.5, 0.008, _wrap_to_canvas(fig, slide_text, 10.0),
                                fontsize=10, ha="center", va="bottom",
                                color="#8a4b00"))
    else:
        out.append(fig.text(0.5, 0.008, "\n".join(notes),
                            fontsize=(lay.footer_size if lay else 7.5),
                            ha="center", va="bottom", color="#222222"))
    if provenance:
        out.append(fig.text(0.995, 0.995, provenance,
                            fontsize=6 if (lay is None or lay.stamp_size_small) else 8,
                            ha="right", va="top", color="#555555", family="monospace"))
    return out


# v3.1. The bottom band of the legend: the cell-state box on the left, the
# caveats in two columns to the right of it. The box's width is not negotiable
# -- ring 8's activity labels are sentences ("stopped for cause — OT category
# Negative / Safety_Sideeffects"), so at a readable 10.5 pt the box is 0.42 of
# the canvas. That is what decides where the caveats start, and it was measured
# off the drawn artist rather than guessed: `assert_legend_panels_do_not_overlap`
# refused two earlier arrangements before this one.
CAVEAT_COLUMNS = ((0.025, 0.295), (0.350, 0.295), (0.675, 0.305))
CAVEAT_TOP = 0.215
CAVEAT_SIZE = 10.0


def _draw_caveat_columns(fig, notes: Sequence[str],
                         size: float = CAVEAT_SIZE) -> list:
    """The caveat block, along the bottom of the legend, in columns.

    v3.0 drew these as 22 unbroken wrapped lines at 6.4 pt -- 5.3 pt on a full
    slide, 2.7 pt on half of one -- with no space between subjects, stacked
    above 18.7% of blank canvas. The block is not the problem; the type size and
    the single tall column were.

    Balanced by **line** rather than by note: the notes are 1 to 4 lines each
    once wrapped, so dealing them out alternately would leave one column twice
    the height of another and the band would be sized for the taller. Each note
    stays whole and gets a blank line after it -- a caveat split across a column
    break is a caveat half the room reads, and a caveat with no air around it is
    the wall this replaces.

    If the balanced columns would run past the top of the band, the type
    shrinks, and it shrinks to a floor of 0.80. At the floor this returns the
    block anyway and the canvas check refuses the render: quietly going to 6 pt
    to make something fit is the defect this whole pass is about.
    """
    import textwrap

    n_cols = len(CAVEAT_COLUMNS)
    W = fig.get_size_inches()[0] * 72.0
    scale, out = 1.0, []
    for _ in range(8):
        for t in out:
            t.remove()
        pt = size * scale
        # DejaVu Sans averages ~0.55 em per character over prose of this kind.
        widths = [max(24, int(w * W / (pt * 0.55))) for _, w in CAVEAT_COLUMNS]
        wrapped = [textwrap.wrap(n, min(widths)) for n in notes]
        # Greedy: each note goes to whichever column is shortest so far. Filling
        # columns in order until each passed its share put the whole of a
        # four-line note on the wrong side of the threshold and left one column
        # 1.6x the height of the next -- and the band is sized for the tallest,
        # so the imbalance was paid for in type size by every column.
        buckets = [[] for _ in CAVEAT_COLUMNS]
        used = [0] * n_cols
        for w in wrapped:
            cur = min(range(n_cols), key=lambda i: used[i])
            buckets[cur].append(w)
            used[cur] += len(w) + 1
        out = []
        for (x, _w), bucket in zip(CAVEAT_COLUMNS, buckets):
            text = "\n\n".join("\n".join(w) for w in bucket)
            if not text:
                continue
            out.append(fig.text(x, CAVEAT_TOP, text, fontsize=pt,
                                va="top", ha="left", color="#333333"))
        # Measured, not predicted. A line-height estimate of `pt * 1.35` put the
        # first attempt 2 px past the bottom of the canvas and
        # `assert_text_within_canvas` caught it -- the same class of mistake as
        # the scale column's budget, so it gets the same answer: ask the
        # renderer instead of modelling it.
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        lowest = min(t.get_window_extent(renderer=r).y0 for t in out)
        if lowest >= 6.0 or scale <= 0.80:
            break
        scale *= 0.94
    return out


def render_legend(
    *,
    cells_by_ring: dict[str, dict[str, Cell]],
    region: str,
    out_svg: Path,
    out_png: Path | None = None,
    run_id: str = "",
    provenance: str = "",
    ordering_note: str = "",
    filter_notes: Sequence[str] = (),
    input_count: int = 0,
    rendered_count: int = 0,
    gene_flags: dict[str, list[str]] | None = None,
    de_source: "SOURCES.DESource | None" = None,
) -> Path:
    """The legend as its own image, for the slide that is not the circle.

    Puck: *"we could have the download download legend as a separate image"*,
    for "circle on one slide, legend on another, sized independently".

    What is on it is everything a reader needs to decode a cell and nothing that
    needs the circle to make sense: the twelve scales in their own colours and
    units with their source links, the four cell states, the three categorical
    states, and the caveat block. 16:9, two columns of six, so it is a slide.

    **The pairing is the risk this artefact introduces**, and it is the thing to
    get right: a legend is only true of the run that produced it, and the colour
    ranges on nine of the twelve rings move with the submitted set, so last
    week's legend against this week's figure would misread every cell on those
    nine and look perfectly normal doing it. So the run id is drawn **large and
    first**, in the same string the figure stamps, with a line that says in
    words what has to match. `test_a_figure_and_its_legend_name_the_same_run`
    checks the two files carry the same id; a reader checks the two slides.

    Returns the SVG path.
    """
    import dataclasses

    rings, norms, cmaps = _rings_norms_cmaps(cells_by_ring, de_source)
    lay = LEGEND
    fig = plt.figure(figsize=lay.figsize, dpi=100)
    fig.patch.set_facecolor("#ffffff")
    blocks = []

    # --- the pairing block, top left, before anything it is a legend TO -----
    #
    # v3.1 / UX v3 A3: every size here went up, and the space was already on the
    # canvas. v3.0's legend measured 9.3% ink, its bounding box stopped 115 px
    # short of the bottom, and the block below x=35% / y=70% -- 616 x 297 px,
    # 18.7% of the artefact -- was 0.00% ink, directly underneath a 6.4 pt
    # caveat wall. It was small *and* empty. Three bands now: identity and cell
    # states top-left, three scale columns filling the width, caveats along the
    # bottom in three columns of their own.
    blocks.append(fig.text(0.025, 0.994, f"{FIGURE_TITLE} — legend", fontsize=21,
                           weight="bold", va="top", ha="left", color="#111111"))
    blocks.append(fig.text(0.025, 0.947, f"{region} · {input_count} genes in, "
                                         f"{rendered_count} rendered",
                           fontsize=13, va="top", ha="left", color="#333333"))
    # The run id keeps its place as the third thing on the artefact and gets
    # bigger, not smaller: the pairing is the one risk a detachable legend
    # introduces, and nine of the twelve ranges move with the submitted set.
    blocks.append(fig.text(0.300, 0.994, run_id or provenance, fontsize=16,
                           weight="bold", va="top", ha="left", color="#8a4b00"))
    blocks.append(fig.text(
        0.300, 0.955,
        "This legend is true only of the run named above.\n"
        "Nine of the twelve ranges move with the submitted\n"
        "gene set — check the run id on the figure matches\n"
        "this one before reading a colour off it.",
        fontsize=11.5, va="top", ha="left", color="#8a4b00"))
    if provenance and provenance != run_id:
        # v3.1: bottom-right, not top-right. The cell-state box moved into the
        # top band and this stamp was the first thing it printed through.
        blocks.append(fig.text(0.995, 0.010, provenance, fontsize=8.0,
                               va="bottom", ha="right", color="#666666",
                               family="monospace"))

    # --- cell states, reusing the figure's own handles ----------------------
    handles = [
        Patch(facecolor=s["fc"], hatch=s["hatch"], edgecolor=s["ec"], label=s["label"])
        for s in STATE_STYLE.values()
    ]
    handles.append(Patch(facecolor="none", edgecolor="#111111", linestyle="--",
                         label="value — decode it against THAT ring's own scale"))
    for spec in rings:
        if not spec.categorical:
            continue
        for v, colour in sorted(spec.categorical.items()):
            # The ring's SHORT label here, not its full one. On the figure the
            # box sits in a corner with the whole canvas to spread into; on this
            # canvas "Pipeline: current activity (Open Targets clinicalReports)"
            # three times over made the box 0.42 of the width and it printed
            # straight across the first scale column. The ring is numbered and
            # named in full two inches to the right, so the prefix here only has
            # to say which ring, not describe it.
            handles.append(Patch(
                facecolor=colour, edgecolor="#ffffff",
                label=f"{spec.wedge_label}: "
                      f"{(spec.category_labels or {}).get(v, str(v))}"))
    # v3.1: the box is in the bottom band, beside the caveats, not in the left
    # margin. It is 0.42 of the canvas wide at a readable type size -- the
    # activity labels are sentences, not words -- and a 0.42-wide box in a
    # 0.29-wide margin is what `assert_legend_panels_do_not_overlap` refused
    # when this layout was first tried. Width it has; height it does not need.
    legend_box = fig.legend(handles=handles, loc="upper left",
                            bbox_to_anchor=(0.620, 0.995), fontsize=9.5,
                            frameon=True,
                            title="the four cell states, and ring 8's three",
                            title_fontsize=10.5)

    # --- the caveats, which is what the slide render drops ------------------
    #
    # v3.1 / UX v3 A3. These were 22 unbroken wrapped lines at 6.4 pt in the
    # left column -- 2.7 pt at half a slide, 5.3 pt at a full one -- with no
    # spacing between subjects, directly above the 18.7% of blank canvas that is
    # now paying for them. They run along the bottom in three columns at 11 pt,
    # one note per paragraph, balanced by line count rather than by note count
    # so a three-line note and a one-line note do not sit in the same column by
    # accident. Nothing is dropped: the legend is where the caveats live now
    # that the slide render carries one line and points here.
    notes = _stamp_notes(ordering_note, filter_notes, region, cells_by_ring,
                         gene_flags, "", "", clock_phrase(AUDIT))
    assert_footer_is_true(notes, ordering_note, filter_notes)
    blocks += _draw_caveat_columns(fig, notes)

    # --- the scales: one code path, four columns ----------------------------
    #
    # Four of three rather than two of six. Two columns left the type at 9 pt
    # -- 3.75 pt at half a slide -- while a fifth of the canvas went unused;
    # four shorter columns spend the whole width and the reclaimed block on the
    # type instead. `start` continues the numbering across them: a ring number
    # that does not match the radial order is a wrong figure, which is the bug
    # v3.0 found in the two-column version and the reason `start` exists.
    per = -(-len(rings) // LEGEND_COLUMNS)
    x0, y0, w, h = lay.scale_box
    for col in range(LEGEND_COLUMNS):
        chunk = rings[col * per:(col + 1) * per]
        if not chunk:
            continue
        box = dataclasses.replace(lay,
                                  scale_box=(x0 + col * LEGEND_COLUMN_PITCH,
                                             y0, w, h))
        blocks += _draw_ring_scales(fig, chunk, norms, cmaps, box,
                                    header=(col == 0), start=col * per + 1,
                                    # v3.1. On the legend this sentence is the
                                    # single most important string on the
                                    # artefact and it was set SMALLER than the
                                    # ring names it qualifies -- 8.55 pt against
                                    # 9. It leads now.
                                    header_size=15.0)

    assert_text_within_canvas(fig, blocks)
    assert_legend_panels_do_not_overlap(fig, legend_box, blocks)
    # v3.1. The box-versus-text check above is v3.0's, and it is not the same
    # question: two scale rows growing into each other, or the caveat block
    # growing down into a column, is text over text and it had nothing looking
    # at it. Measured at 0 pairs on the v3.0 legend, so this starts life as a
    # check that holds rather than one that has to be made to hold -- which is
    # exactly when to add it.
    assert_text_does_not_overprint_text(fig, blocks, where="legend render")
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    # `SAVE_RC`, for the reason at the top of this module: pycirclize sets
    # `savefig.bbox="tight"` with half an inch of padding, and a "16:9" legend
    # saved without it comes out 16.44 x 8.94 in -- 1.84:1, letterboxed on the
    # slide it exists to go on. `assert_artefacts_are_their_own_layout` is what
    # found it, ten minutes after being written for a different bug.
    with plt.rc_context(SAVE_RC):
        fig.savefig(out_svg, format="svg")
        if out_png:
            fig.savefig(out_png, format="png", dpi=110)
    plt.close(fig)
    return out_svg


def assert_legend_panels_do_not_overlap(fig, legend_box, blocks) -> None:
    """v3.0. The legend file's own UX A6.

    The figure has `assert_no_text_over_the_rings` because the thing text must
    not land on there is the circle. On the legend canvas there is no circle and
    that assertion does not apply -- so the first draft printed the cell-state
    box straight across the first scale column and nothing objected. The
    obstacle here is the box, so the box is what is checked.

    `fig.legend` returns the artist, which is why it is captured rather than
    called for effect: its bbox is only knowable after a draw.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    box = legend_box.get_window_extent(renderer=r)
    offenders = []
    for t in blocks:
        bb = t.get_window_extent(renderer=r)
        if bb.x1 > box.x0 and bb.x0 < box.x1 and bb.y1 > box.y0 and bb.y0 < box.y1:
            offenders.append(f"{t.get_text().splitlines()[0][:40]!r}")
    if offenders:
        raise AssertionError(
            "the cell-state box is drawn over the legend's own scales: "
            + "; ".join(offenders[:4])
            + ". A legend that overprints itself is worse than no legend file, "
              "because the reader cannot tell which half is missing.")


def assert_figure_states_the_minimum(drawn: str, input_count: int,
                                     rendered_count: int,
                                     filter_notes: Sequence[str],
                                     layout: str) -> None:
    """v3.0. What every render of the circle must say, however lean it gets.

    Puck's condition on letting the app render and the export diverge: *"the
    figure alone must still not lie. If someone has the circle and not the
    legend -- which will happen -- it still has to say what it is, that each
    ring has its own scale so colours mean nothing across rings, how many genes
    were submitted versus rendered, and whether filters were active."*

    That is the whole of this check, and it runs on the text the render actually
    drew rather than on the intent of the code that drew it. It is the guard
    that makes the divergence safe: `web` may drop the footer block and the
    dropped-gene list, and a future layout may drop more, but none of them can
    drop these four and still render.
    """
    missing = []
    if TOOL_NAME not in drawn:
        missing.append("what it is (the title)")
    if COMPARABILITY_CLAIM not in drawn:
        missing.append("that each ring has its own scale and colours do not "
                       "carry between rings")
    if f"{input_count} genes in" not in drawn:
        missing.append(f"how many genes were submitted ({input_count})")
    if f"{rendered_count} rendered" not in drawn:
        missing.append(f"how many were rendered ({rendered_count})")
    if filter_notes and "FILTERED" not in drawn:
        missing.append("that threshold filters removed genes from this plot")
    if missing:
        raise AssertionError(
            f"the {layout} render does not state: " + "; ".join(missing)
            + ". A reader may have this circle and nothing else -- no legend "
              "file, no page around it, nobody to ask -- and it must still be "
              "true on its own."
        )


def assert_artefacts_are_their_own_layout(runs_dir: Path, web: Layout = WEB) -> None:
    """v3.0. Every artefact is the render it is named after.

    Written because it was not. The web render was asked for with `png=None`,
    which in `do_render` already meant "the default path, figure.png" rather
    than "no PNG", so the lean render wrote itself over the EXPORT's PNG:
    `figure.svg` carried the caveat footer and the invariant stamp and
    `figure.png` -- the file most people actually send -- did not.
    `assert_renders_agree` compared the two SVGs and passed, because the SVGs
    were fine. Nothing in the run noticed.

    A canvas size is the cheapest thing that distinguishes these renders and it
    is decided entirely by the Layout, so it is what gets checked. Every layout
    here has a different one, deliberately: 19.5x14 audit, 9.6x8.25 web,
    16x9 slide and legend.

    v3.5: `web` is the one canvas that is not a constant any more -- it is a
    function of the window the render was asked for (`web_layout_for_viewport`) --
    so the caller passes the layout the run actually used. The check is the same
    check: `figure-web.svg` must be the app canvas this run chose and not, as it
    once was, the export written to the app's name.
    """
    from PIL import Image

    expect = {
        "figure.svg": AUDIT, "figure.png": AUDIT,
        "figure-web.svg": web,
        "figure-slide.svg": SLIDE, "figure-slide.png": SLIDE,
        "legend.svg": LEGEND, "legend.png": LEGEND,
    }
    wrong = []
    for name, lay in expect.items():
        path = runs_dir / name
        if not path.exists():
            wrong.append(f"{name} was not written")
            continue
        if name.endswith(".svg"):
            root = __import__("xml.etree.ElementTree", fromlist=["x"]).parse(path).getroot()
            _, _, w, h = (float(v) for v in root.get("viewBox").split())
            got, want = (w / 72.0, h / 72.0), lay.figsize
        else:
            with Image.open(path) as im:
                got, want = (im.width, im.height), (lay.figsize[0] * 110,
                                                    lay.figsize[1] * 110)
        if abs(got[0] - want[0]) > max(2.0, want[0] * 0.01) or \
           abs(got[1] - want[1]) > max(2.0, want[1] * 0.01):
            wrong.append(f"{name} is {got[0]:.0f}x{got[1]:.0f}, but the "
                         f"{lay.name} layout is {want[0]:.0f}x{want[1]:.0f}")
    if wrong:
        raise AssertionError(
            "an artefact is not the render its name promises: " + "; ".join(wrong)
            + ". The export and the app render may differ; which file is which "
              "may not.")


def assert_renders_agree(a_svg: Path, b_svg: Path) -> None:
    """v3.0. Two renders of one run may differ in furniture, never in data.

    This is what makes "the download and the online figure can be different"
    safe to say yes to. The app inlines `figure-web.svg` and the colleague opens
    `figure.png`; if a cell were ever a different colour between them, the two
    people would be reading different figures and neither would know. So every
    `cell|<ring>|<index>|<gene>` group in both files is compared by id and by
    fill, and any difference fails the run.

    Furniture is expected to differ and is not compared: the footer, the hole's
    drop list, the canvas size.
    """
    import xml.etree.ElementTree as ET

    def cells(p: Path) -> dict[str, str]:
        # Parsed rather than matched with a regex: a cell's `<path d=…>` runs to
        # several thousand characters of arc, so any bounded pattern between the
        # gid and the fill silently matches nothing and the check passes on an
        # empty comparison. It did, the first time.
        out = {}
        for el in ET.parse(p).getroot().iter():
            gid = el.get("id") or ""
            if not gid.startswith("cell|"):
                continue
            style = ""
            for child in el.iter():
                style = child.get("style") or style
                if "fill" in style:
                    break
            m = re.search(r"fill:\s*(#[0-9a-fA-F]{6})", style)
            out[gid] = m.group(1).lower() if m else "?"
        return out

    a, b = cells(a_svg), cells(b_svg)
    if not a:
        raise AssertionError(f"{a_svg.name} has no cell groups to compare")
    if a.keys() != b.keys():
        only_a = sorted(a.keys() - b.keys())[:3]
        only_b = sorted(b.keys() - a.keys())[:3]
        raise AssertionError(
            f"{a_svg.name} and {b_svg.name} draw different cells — "
            f"only in {a_svg.name}: {only_a}; only in {b_svg.name}: {only_b}")
    differ = [k for k in a if a[k] != b[k]]
    if differ:
        k = differ[0]
        raise AssertionError(
            f"{len(differ)} cell(s) are a different colour in {a_svg.name} and "
            f"{b_svg.name} — e.g. {k}: {a[k]} vs {b[k]}. The two renders may "
            f"differ in furniture, never in data.")


def caption(rings, norms, region, provenance, ordering_note="", filter_notes=(),
            gene_flags=None, dropped_names=(), wedge="", clock="",
            invariant_summary="") -> str:
    """UX A4. Everything the slide render drops, as text to paste underneath.

    The full audit stamps stay on the audit render, which is the right artefact
    for the morning review. This is what makes the slide render honest anyway.
    """
    lines = [f"targetring \u2014 {region}. {provenance}", ""]
    if invariant_summary:
        lines += [invariant_summary, ""]
    lines.append("Rings, outermost first (scale is per-ring; a colour means nothing "
                 "between two rings):")
    for i, spec in enumerate(rings, start=1):
        n = norms.get(spec.key)
        rng = _range_label(spec, n)
        # v2.6: the units and the transform travel with the range, because a
        # range without them is a pair of numbers a reader cannot act on -- and
        # on a transformed ring it is a pair of numbers in units nobody
        # measured in.
        lines.append(f"  {i}. {spec.label} {rng} {spec.units} \u2014 {spec.source}")
        if spec.transform:
            lines.append(f"       scale: {spec.transform}")
        # v2.8. The caption is the artefact a slide reader is handed, and a
        # slide has no links, so this is where the URL has to be written out in
        # full rather than shortened to a domain. A ring with no citation says
        # so here too: nine links and three silences is a question.
        doc = spec.source_doc
        if doc:
            lines.append(f"       source: {doc.name} \u2014 {doc.url}")
        else:
            lines.append(f"       source: "
                         f"{SOURCES.UNLINKED_RINGS.get(spec.key, spec.short_source)}")
    lines += [
        "",
        "The ring sources above are also in manifest.json (`ring_sources`), with the "
        "endpoint and the pinned schema or dataset version for each, and the run's UTC "
        "timestamp is the access date. In the SVG each source line is a real link; in a "
        "PNG or on a slide nothing is clickable, which is why the figure prints the "
        "domain beside the name.",
    ]
    lines += [
        "",
        "Pipeline: furthest phase counts only drugs indicated for a disease with human "
        "genetic evidence linking it to this target. A drug that hits the target for an "
        "unrelated indication sets no phase.",
        f"Missing is never imputed. k = external rings with a real measurement for that "
        f"gene; it is a completeness gauge and orders nothing. Spoke ORDER: the circle "
        f"{clock or clock_phrase(AUDIT)} \u2014 {ordering_note}. Region affects exactly one "
        f"ring (regional specificity).",
        "This figure is internally consistent and auditable. Nothing on it is ranked or "
        "scored: it is your gene set, the evidence rings, and a drawer per gene.",
        # v2.9. The slide render draws no footer at all, so without this line the
        # slide's reader would be the one person not told where the ids are.
        _ID_NOTE,
    ]
    if wedge:
        lines += ["", wedge]
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


def _wedge_geometry(fig, ax, wedge_deg: float):
    """Centre, plotted radius, and the half-angle a label may actually occupy.

    The usable half-angle is the wedge's own half-angle less
    `WEDGE_TEXT_MARGIN_DEG`, so a label stops short of the sector edge rather
    than touching the first and last arc.
    """
    fig.canvas.draw()
    box = ax.get_window_extent()
    return (
        fig.canvas.get_renderer(),
        (box.x0 + box.x1) / 2,
        (box.y0 + box.y1) / 2,
        min(box.width, box.height) / 2,
        math.tan(math.radians(max(wedge_deg / 2 - WEDGE_TEXT_MARGIN_DEG, 0.0))),
    )


def _wedge_overhang(t, r, cx, cy, tan_half) -> float:
    """How far this label sticks out of the wedge, in px. <= 0 means it fits.

    The wedge opens upward from the centre, so the two *bottom* corners bind:
    they have the least vertical distance and therefore the least room. A label
    drawn below the centre is not in the wedge at all and is reported as such,
    by its full half-width.
    """
    bb = t.get_window_extent(renderer=r)
    worst = -1e9
    for x, y in ((bb.x0, bb.y0), (bb.x1, bb.y0), (bb.x0, bb.y1), (bb.x1, bb.y1)):
        dy = y - cy
        allowed = dy * tan_half if dy > 0 else -1.0
        worst = max(worst, abs(x - cx) - allowed)
    return worst


WEDGE_MIN_SCALE = 0.80


def _wedge_wrapped(text: str, lines: int) -> str:
    """`text` broken at word boundaries onto at most `lines` lines.

    Balanced rather than greedy: a ring name wrapped to "Pipeline:" over
    "furthest phase" is a label with a hanging word, and the point of wrapping
    here is to halve the *widest* line.
    """
    import textwrap

    words = text.split()
    if lines <= 1 or len(words) < lines:
        return text
    width = max(len(text) // lines + 1, max(len(w) for w in words))
    out = textwrap.wrap(text, width)
    while len(out) > lines:
        width += 1
        out = textwrap.wrap(text, width)
    return "\n".join(out)


def fit_wedge_labels(fig, ax, labels, wedge_deg: float, band_px: float) -> float:
    """Shrink the ring names until every one fits inside the 12 o'clock gap.

    The budget is `2 * r * tan(wedge/2 - margin)` and `r` is the ring's own
    radius, so the innermost ring binds and how tight it binds depends on the
    layout (the audit's circle is 926 px across, the slide's 552) and on how
    many rings this render is drawing. None of that is knowable where the font
    size is chosen, so it is measured -- the same argument as
    `fit_centre_to_the_hole`, applied to the other end of the radius.

    Two knobs, and they are tried in this order for a reason.

    **Wrap first.** The two constraints pull opposite ways: the *width* budget
    is tightest on the innermost ring, and the *height* budget -- the band,
    `(r_outer - r_inner) / n` -- is widest when there are fewest rings. A
    six-ring render is exactly the case where a name lands at a small radius
    with a 37 px band around it, so a second line is free there and costs
    nothing anywhere else. This is not hypothetical: `clinical_phase` is ring 7
    of 12 in a full run and the innermost ring of the invariants fixture.

    **Then shrink**, down to a floor that is tighter than the hole's (0.80
    against 0.62) on purpose. The centre block is a caveat you lean in to read;
    a ring name is the label you read at a glance, and at 0.62 of 7.4 pt it is
    UX A4 again. If both knobs run out, `assert_wedge_labels_fit` raises and the
    render refuses -- which is correct, because the alternatives are a name
    printed over the arcs or a name truncated past its caveat (QA-v2-retest §5).
    """
    if not labels:
        return 1.0
    sizes = [t.get_fontsize() for t in labels]
    texts = [t.get_text() for t in labels]
    scale = 1.0
    for _ in range(18):
        r, cx, cy, _radius, tan_half = _wedge_geometry(fig, ax, wedge_deg)
        over = [i for i, t in enumerate(labels)
                if _wedge_overhang(t, r, cx, cy, tan_half) > 0]
        if not over:
            break
        wrapped_one = False
        for i in over:
            t = labels[i]
            cand = _wedge_wrapped(texts[i], t.get_text().count("\n") + 2)
            if cand == t.get_text():
                continue
            was = t.get_text()
            t.set_text(cand)
            fig.canvas.draw()
            if (t.get_window_extent(renderer=fig.canvas.get_renderer()).height
                    + LABEL_CLEARANCE_PX > band_px):
                t.set_text(was)          # the extra line does not fit the band
            else:
                wrapped_one = True
        if wrapped_one:
            continue
        if scale <= WEDGE_MIN_SCALE:
            break
        scale = max(scale * 0.96, WEDGE_MIN_SCALE)
        for t, size in zip(labels, sizes):
            t.set_fontsize(size * scale)
    return scale


class RingNamesDoNotFit(AssertionError):
    """v3.5. This geometry cannot name its rings from inside the 12 o'clock gap.

    An `AssertionError` still, and with the same message it always had: nothing
    about what refuses a render has changed, and every caller that catches
    `AssertionError` -- `/api/analyse`'s invariant_abort, `ux_v3_web_fit.py` --
    behaves exactly as before. What the class buys is that
    `render_naming_the_rings_if_they_fit` can offer a canvas the names and take
    the refusal as an answer, without catching an `AssertionError` raised by one
    of the fourteen other things this render asserts. A bare `except
    AssertionError` there would have swallowed a genuine collision and drawn a
    figure with no names and no complaint, which is this repo's one rule.
    """


class RingNamesUnderTheFloor(RingNamesDoNotFit):
    """The names fit the gap only by being too small to read on this surface.

    A subclass, because to the caller asking "can this canvas name its rings"
    these are one answer: round 1's names were *inside* the gap and 9.07 CSS px,
    and round 2 deleted them for the second half of that sentence.
    """


def assert_wedge_names_clear_the_floor(fig, labels, lay: Layout) -> None:
    """v3.5. On a surface with a declared floor, a ring name is either legible or
    it is not there.

    `fit_wedge_labels` shrinks to `WEDGE_MIN_SCALE` (0.80) and wraps, and both
    knobs make a name *fit* while making it smaller. That is the right behaviour
    on a 19.5 in export read at full size and it is how round 1 ended up drawing
    twelve strings at 9.07 CSS px in the app, which round 2 called "an
    unreadable copy of a legible thing" and deleted.

    So: measured on the artists that were actually drawn, after the fit loop, in
    the CSS pixels a reader sees -- `WEB_CSS_SCALE` is constant by construction
    (see `web_layout_for_viewport`), so a point here is 1.5046 px in the browser
    on every canvas this layout is ever given. A layout that declares no floor
    (`audit`, `slide`, `legend`) is not measured here at all: they are files, and
    §v3.3 measures them against the 10.0 pt presentation floor instead.
    """
    if not labels or not lay.wedge_label_floor_px:
        return
    floor = lay.wedge_label_floor_px
    # `WEB_CSS_SCALE` and not `web_css_scale(lay, panel)`: this layout's canvas is
    # sized so that the two are the same number, and the one window where they are
    # not -- narrower than the laptop, where the canvas is clamped and scaled down
    # -- is a window whose circle `assert_wedge_labels_fit` has already refused,
    # because clamping the width caps the circle at the 7.30 in that round 2
    # measured as too small. Nothing reaches here on a canvas the constant would
    # flatter.
    small = sorted(
        (t.get_fontsize() * WEB_CSS_SCALE, t.get_text().replace("\n", " "))
        for t in labels if t.get_fontsize() * WEB_CSS_SCALE < floor
    )
    if small:
        raise RingNamesUnderTheFloor(
            f"{len(small)} of {len(labels)} ring names fit the {lay.wedge_deg:g}° gap only "
            f"under the {floor:g} CSS px floor of the {lay.name} surface: "
            + "; ".join(f"{name!r} at {px:.2f} px" for px, name in small[:4])
            + f". `fit_wedge_labels` shrinks to {WEDGE_MIN_SCALE:g} of the declared "
              f"{lay.wedge_label_size:g} pt to make a name fit, and a name that fits by "
              f"being unreadable is not a name the reader has. Draw a bigger circle -- on "
              f"this layout that means a larger window -- or leave the gap empty and let "
              f"`wedge_note` say where the names are."
        )


def assert_wedge_labels_fit(fig, ax, labels, wedge_deg: float, obstacles=()) -> None:
    """Every ring name is inside the gap, clear of the first and last sector.

    This is the inward-facing half of UX A6. `assert_no_text_over_the_rings`
    stops text outside the circle from landing on the arcs; the wedge puts text
    *inside* the circle deliberately, and the thing that makes that legitimate
    rather than a violation is that the angular span it occupies is empty. So
    the span is checked, at each label's own radius, against the same geometry
    the sectors were built from.

    `obstacles` is the rest of the text on the polar axes -- the spoke labels and
    the centre block. The innermost ring name sits about two plotted units above
    the hole's edge, so "inside the wedge" is necessary and not sufficient: the
    counts in the hole grow with the drop ledger, and a seven-category ledger is
    an ordinary input (`assert_centre_fits_the_hole`'s own broken fixture).
    """
    if not labels:
        return
    r, cx, cy, _radius, tan_half = _wedge_geometry(fig, ax, wedge_deg)
    offenders = [
        f"{t.get_text()[:44]!r} at {t.get_fontsize():.1f} pt overhangs the "
        f"{wedge_deg:g}° gap by {_wedge_overhang(t, r, cx, cy, tan_half):.1f} px"
        for t in labels if _wedge_overhang(t, r, cx, cy, tan_half) > 0
    ]
    # Ring names must also clear each *other*. This is the radial constraint, and
    # it is the one that decided the slide render: bands are (r_outer-r_inner)/n
    # apart and a line of type has a height that does not shrink with them, so a
    # circle small enough or a ring count high enough makes the wedge impossible
    # at any angle. Checked rather than assumed, because `RING_SPECS` growing by
    # one is exactly how it would first go wrong.
    boxes = [(t, t.get_window_extent(renderer=r)) for t in obstacles]
    ordered = sorted(labels, key=lambda t: t.get_window_extent(renderer=r).y0)
    for lower, upper in zip(ordered, ordered[1:]):
        lb, ub = (x.get_window_extent(renderer=r) for x in (lower, upper))
        if ub.y0 - lb.y1 < LABEL_CLEARANCE_PX:
            offenders.append(
                f"the ring names {lower.get_text()[:24]!r} and {upper.get_text()[:24]!r} are "
                f"{ub.y0 - lb.y1:.1f} px apart, under the {LABEL_CLEARANCE_PX:g} px required — "
                f"the bands are too close together at this circle size to name from inside")
    for t in labels:
        bb = t.get_window_extent(renderer=r)
        for ot, ob in boxes:
            if (bb.x0 - LABEL_CLEARANCE_PX < ob.x1 and ob.x0 < bb.x1 + LABEL_CLEARANCE_PX
                    and bb.y0 - LABEL_CLEARANCE_PX < ob.y1
                    and ob.y0 < bb.y1 + LABEL_CLEARANCE_PX):
                offenders.append(
                    f"the ring name {t.get_text()[:30]!r} comes within "
                    f"{LABEL_CLEARANCE_PX:g} px of {ot.get_text()[:30]!r}")
                break
    if offenders:
        # v3.5: `RingNamesDoNotFit` is an `AssertionError` with this same message.
        # The condition, the clearance and the wording are untouched; the class is
        # how `render_naming_the_rings_if_they_fit` can tell this refusal from the
        # fourteen other assertions on this render without matching on prose.
        raise RingNamesDoNotFit(
            "a ring name does not fit the 12 o'clock gap and would be drawn over the "
            "first or last spoke: " + "; ".join(offenders)
            + ". `fit_wedge_labels` has already tried wrapping it onto more lines and "
              "shrinking the type to its floor. Shorten the ring's `short_label` or widen "
              "`Layout.wedge_deg` — and if you widen it, the spoke cost changes and "
              "`wedge_note` must change with it."
        )


CENTRE_MIN_SCALE = 0.62

# v3.1. Where the *middle* of the centre block sits, in axes fractions. The
# block used to be positioned line by line from 0.575 downward; it is stacked
# now and placed by its centre, and 0.507 is where the old block's centre came
# out -- so the figures move by nothing on `audit` and `web`, where the old
# constants were right, and stop colliding on `slide`, where they were not.
CENTRE_BLOCK_MID = 0.507


def stack_centre_blocks(fig, ax, blocks, centre_y: float = CENTRE_BLOCK_MID,
                        leading: float = 0.34) -> None:
    """UX v3 A1. Lay the centre block out by measured height, not at constants.

    Until v3.1 the block's lines sat at fixed axes fractions -- 0.575, 0.542,
    0.512 and a hand-decremented `y` after that -- while only the *font sizes*
    were multiplied by `lay.centre_scale`. On `audit` and `web` that factor is
    1.0 and the constants were tuned for it, so it looked correct for four
    releases. On `slide` it is **1.15**: the type grew 15%, the spacing did not,
    and the block printed through itself. `Lung` was struck across
    `15 genes in, 14 rendered` and the amber `the 1 not shown are named in the
    caption` across `1 not shown`, on the one artefact that gets projected.

    Neither existing check could see it. `fit_centre_to_the_hole` shrinks until
    every line's *radial reach* clears the hole, and two lines sharing a y have
    the same reach they had apart; `assert_centre_fits_the_hole` measures that
    same radius. Both were asking "does this escape the hole", and the defect
    was entirely inside it.

    So the positions are derived: every line is anchored by its top, advanced by
    its own measured height plus `leading` of its own line height, and the
    finished stack is centred on `centre_y`. A different `centre_scale`, a
    longer region name, a seventh drop category or a filter block now moves the
    lines below it rather than landing on them, because the layout is a
    consequence of the type rather than a guess at it.
    """
    if not blocks:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    inv = ax.transAxes.inverted()

    def frac_h(px: float) -> float:
        """A height in display pixels, as a fraction of the axes."""
        y0 = inv.transform((0.0, 0.0))[1]
        y1 = inv.transform((0.0, px))[1]
        return abs(y1 - y0)

    heights, gaps = [], []
    for t in blocks:
        t.set_va("top")
        bb = t.get_window_extent(renderer=r)
        heights.append(frac_h(bb.height))
        # The gap after a block scales with that block's own type, so a 6.5 pt
        # filter note is not given the same air as a 15 pt title.
        gaps.append(frac_h(t.get_fontsize() * fig.dpi / 72.0) * leading)

    total = sum(heights) + sum(gaps[:-1])
    y = centre_y + total / 2.0
    for t, h, g in zip(blocks, heights, gaps):
        t.set_position((0.5, y))
        y -= h + g


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

    v3.1: the block is re-stacked at the top of every iteration, because a font
    size and a line position are not independent any more. Shrinking the type
    without restacking would leave the gaps of the size before it, which is the
    defect this pass exists to remove, arriving from the other direction.
    """
    if not blocks:
        return 1.0
    original = [t.get_fontsize() for t in blocks]
    scale = 1.0
    for _ in range(14):
        stack_centre_blocks(fig, ax, blocks)
        r, cx, cy, hole = _hole_geometry(fig, ax, hole_fraction)
        if max(_reach(t, r, cx, cy) for t in blocks) <= hole:
            return scale
        scale *= 0.93
        if scale < CENTRE_MIN_SCALE:
            scale /= 0.93
            break
        for t, size in zip(blocks, original):
            t.set_fontsize(size * scale)
    stack_centre_blocks(fig, ax, blocks)
    return scale


def assert_text_does_not_overprint_text(fig, blocks, slop: float = 1.0,
                                        where: str = "") -> None:
    """v3.1. No drawn string may sit on top of another drawn string.

    The build had three checks about text landing where it should not -- over
    the rings, outside the canvas, over the legend box -- and none of them about
    text over *text*. So the slide render printed its region name through its own
    counts line, and the bottom of its scale column through its own caveat line,
    for four releases, while every assertion passed. The collisions were real,
    visible and measured off the artefact (`logs/ux-v3-r1-overlap.txt`); nothing
    was looking.

    `blocks` are figure- and axes-level `Text` artists, all axis-aligned. The
    rotated spoke labels are deliberately **not** in it: a label set at 37
    degrees has an axis-aligned box far larger than its ink, so including them
    would report collisions that are not there, and the thing that checks them
    properly is `assert_no_text_over_the_rings`, which does the real geometry.

    `slop` is a point in each axis. A tick label resting on the edge of its own
    colour bar is a design; touching by a fraction of a point is not what a
    reader sees.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    boxes = []
    for t in blocks:
        if not (t.get_text() or "").strip() or not t.get_visible():
            continue
        if abs(t.get_rotation() or 0) > 1:
            continue
        boxes.append((t, t.get_window_extent(renderer=r)))
    offenders = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (ta, a), (tb, b) = boxes[i], boxes[j]
            dx = min(a.x1, b.x1) - max(a.x0, b.x0)
            dy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if dx > slop and dy > slop:
                offenders.append(
                    f"{ta.get_text().splitlines()[0][:38]!r} over "
                    f"{tb.get_text().splitlines()[0][:38]!r} "
                    f"by {dx:.0f}x{dy:.0f} px")
    if offenders:
        raise AssertionError(
            (f"{where}: " if where else "")
            + "text is printed on top of other text: "
            + "; ".join(offenders[:5])
            + ". A figure that overprints itself is not a formatting problem -- "
              "the reader cannot tell which of the two strings is the one that "
              "is true.")


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


def assert_no_text_over_the_rings(fig, ax, blocks, wedge_labels=()) -> None:
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

    v2.7: the ring names now sit *inside* the circle, in the empty wedge at 12
    o'clock. They would fail the radius test, and they should not -- the wedge
    is not a ring, it is the absence of one. The assertion is told about them
    rather than left to assume full sectors: they are checked by
    `assert_wedge_labels_fit` against the angular span that is actually empty,
    and they are dropped from the obstacle list here, because they are not
    spoke labels and leaving them in made every ring name an obstacle to the
    ring name above it.
    """
    if not blocks:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    box = ax.get_window_extent()
    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    radius = min(box.width, box.height) / 2
    inside = {id(t) for t in wedge_labels}
    labels = [(t, t.get_window_extent(renderer=r)) for t in ax.texts
              if t.get_text() and id(t) not in inside]

    offenders = []
    for t in blocks:
        if id(t) in inside:
            continue
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


__all__ = ["render", "RING_SPECS", "RingSpec", "STATE_STYLE", "VALUE_CMAP",
           "FigureResult", "assert_fills_disjoint", "assert_footer_is_true",
           "colormap_hexes", "assert_greyscale_disjoint", "relative_luminance",
           "value_cmap", "ring_cmap", "ring_swatches", "ring_identity_colour",
           "ring_identity_colours", "ring_colours", "adjacent_ring_delta_e",
           "CMAP_LUM", "GREYSCALE_MIN_DELTA", "ADJACENT_RING_MIN_DELTA_E",
           "assert_ramp_luminance_is_monotone", "assert_rings_are_distinguishable",
           "assert_within_domain", "ACTIVITY_PALETTE", "RETIRED_CLAIM_WORDS",
           "slide_line", "assert_text_within_canvas", "WEDGE_DEG", "clock_phrase",
           "WEDGE_TEXT_MARGIN_DEG", "WEDGE_MIN_SCALE", "wedge_note",
           "fit_wedge_labels", "assert_wedge_labels_fit",
           "assert_wedge_names_clear_the_floor", "RingNamesDoNotFit",
           "RingNamesUnderTheFloor", "web_layout_for_viewport", "web_circle_px",
           "render_naming_the_rings_if_they_fit", "WEB_PX_PER_IN", "WEB_CSS_SCALE",
           "WEB_REFERENCE_PANEL_PX", "WEB_REFERENCE_VIEWPORT_PX",
           "WEB_VIEWPORT_SLACK_PX",
           "assert_no_text_over_the_rings", "assert_centre_fits_the_hole",
           "fit_centre_to_the_hole", "caption", "Layout", "LAYOUTS", "AUDIT", "SLIDE",
           "WEB", "LEGEND", "render_legend", "COMPARABILITY_CLAIM",
           "assert_figure_states_the_minimum", "assert_renders_agree",
           "assert_artefacts_are_their_own_layout", "assert_legend_panels_do_not_overlap",
           "assert_the_id_rule_is_kept", "assert_text_does_not_overprint_text",
           "FIGURE_TITLE", "TOOL_NAME", "TOOL_VERSION",
           "stack_centre_blocks", "CENTRE_BLOCK_MID", "scale_rows", "STATE_STYLE"]
