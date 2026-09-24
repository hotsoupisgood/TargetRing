"""UX A4, A5, A6 — the boundary where the figure leaves the app.

The UX review's summary: "The engineering underneath this is unusually honest…
The problems are almost all at the boundary where the figure leaves the app: it
is built for reading on a 27-inch monitor and it is going into slides."

These tests measure the exported SVG, the way the review did, rather than
checking that a drawing call was made.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from targetring.figure import (
    AUDIT,
    SLIDE,
    assert_no_text_over_the_rings,
    caption,
    render,
)
from targetring.order import gene_stats
from targetring.states import failed, no_data, not_applicable, value

GENES = ["BRAF", "CDKN2A", "INS", "KCNQ1", "EGFR", "GAPDH"]
ORDER_NOTE = "spoke order: the order of your submitted DE table (unchanged)"


def _spec():
    return {
        "logfc": {g: value(float(i) - 2.5) for i, g in enumerate(GENES)},
        "tractable": {g: value(0.2 * i) for i, g in enumerate(GENES)},
        "safety": {
            **{g: value(-0.1 * i) for i, g in enumerate(GENES)},
            "GAPDH": no_data("never measured"),
        },
        "regional_specificity": {g: value(1.0 + i) for i, g in enumerate(GENES)},
        "clinical_phase": {
            **{g: value(float(i % 5)) for i, g in enumerate(GENES)},
            "KCNQ1": not_applicable("drugged, no attributable indication"),
            "INS": failed("attribution query failed"),
        },
        "literature": {g: value(2.0 + i * 0.4) for i, g in enumerate(GENES)},
    }


def _render(tmp_path: Path, layout: str, **kw):
    spec = _spec()
    out = tmp_path / f"{layout}.svg"
    kw.setdefault("ledger_counts", {"unresolved": 2})
    kw.setdefault("ordering_note", ORDER_NOTE)
    res = render(genes=GENES, cells_by_ring=spec, stats=gene_stats(spec),
                 region="Liver", out_svg=out, input_count=8, rendered_count=6,
                 layout=layout, provenance="run X · abc123", **kw)
    return res, out


def _viewbox(svg: Path) -> tuple[float, float]:
    root = ET.parse(svg).getroot()
    _, _, w, h = (float(v) for v in root.get("viewBox").split())
    return w, h


def _font_sizes(svg: Path) -> list[float]:
    text = svg.read_text()
    return [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)px", text)]


class TestA4SlideRender:
    """A4: every text element was 3-5 pt at full-slide size, and at 1.20:1 on a
    1.78:1 slide the figure filled the middle ~60% of the width."""

    def test_the_audit_render_is_unchanged_in_kind(self, tmp_path: Path) -> None:
        """v2.6 retires the numbered monospace key: each ring has its own scale
        now, beside its own colours, and two places claiming to tell a reader
        how to decode a cell is one too many. What the key was *for* -- the ring
        number, its name and its range -- is asserted here on the thing that
        replaced it."""
        _, svg = _render(tmp_path, "audit")
        body = svg.read_text()
        assert "rings, outermost first" not in body, "the numbered key is retired"
        assert "1. logFC (yours)" in body, "the scale column numbers the rings"
        assert "EACH RING HAS ITS OWN SCALE" in body
        assert "Mouse-phenotype ring" in body, "and the full footer block stays"

    def test_the_slide_render_is_16_by_9(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "slide")
        w, h = _viewbox(svg)
        assert abs(w / h - 16 / 9) < 0.02, f"aspect {w / h:.3f}"

    def test_the_audit_render_is_not(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "audit")
        w, h = _viewbox(svg)
        assert w / h < 1.4, "the audit canvas is for a monitor, not a slide"

    def test_gene_labels_survive_the_fit_to_a_slide(self, tmp_path: Path) -> None:
        """A 16:9 canvas fits a 16:9 slide with no letterboxing, so the scale
        factor is 7.5 in / canvas height in inches.

        matplotlib writes the SVG viewBox in POINTS, 72 to the inch — an earlier
        version of this test divided by 100 and reported a scale of 1.16 where
        the real one is 0.83. It passed, on a wrong number.

        v1: an 18.3 x 15.2 in canvas fitted to a 7.5 in slide is a 0.49 scale,
        so its 8 pt gene label became 3.9 pt.
        """
        _, svg = _render(tmp_path, "slide")
        w, h = _viewbox(svg)
        canvas_in = h / 72.0
        assert abs(canvas_in - SLIDE.figsize[1]) < 0.05, f"{canvas_in:.2f} in"
        scale = 7.5 / canvas_in
        gene_label_pt = SLIDE.label_size * scale
        assert gene_label_pt >= 8.0, f"{gene_label_pt:.1f} pt on a full slide"
        assert gene_label_pt > 2 * 8.0 * (7.5 / 15.2), "must beat v1's 3.9 pt"

    def test_the_key_and_the_six_line_footer_leave_the_slide(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "slide")
        body = svg.read_text()
        assert "rings, outermost first" not in body
        assert "hasSmallMoleculeBinder" not in body
        assert "Mouse-phenotype ring" not in body
        # v2.6 / §v2.3 Still open 4: the slide now carries the ring scales and
        # a short source per ring. The long provenance strings stay in the
        # caption -- they are what made v1's key overprint the arcs.
        assert "1. logFC (yours)" in body
        assert "GTEx v8" in body
        # One line still does the honest work, and v2.5 put the spoke order on
        # it: a slide whose circle has an order nobody can see is the same
        # defect the ring key's caveat had.
        assert "NOT evidence of safety" in body
        assert ORDER_NOTE in body
        assert "see the caption" in body

    def test_the_caption_carries_everything_the_slide_dropped(self, tmp_path: Path) -> None:
        res, _ = _render(tmp_path, "slide")
        cap = res.caption
        for must in ["Rings, outermost first", "hasSmallMoleculeBinder",
                     "Missing is never imputed", "Nothing on it is ranked or scored",
                     ORDER_NOTE, "genetic evidence"]:
            assert must in cap, must

    def test_the_caption_names_the_liability_flag_when_there_is_one(
            self, tmp_path: Path) -> None:
        res, _ = _render(tmp_path, "slide", gene_flags={"KCNQ1": ["long QT syndrome"]})
        assert "⚠" in res.caption and "KCNQ1" in res.caption


class TestA5ColourBar:
    """A5: "There is no colour bar anywhere on the figure. Nothing tells a
    reader whether purple or yellow is the high end of a heatmap." """

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_both_renders_carry_a_scale_for_every_ring(
            self, tmp_path: Path, layout: str) -> None:
        """v2.5 answered A5 with one bar labelled low/high, which was honest
        only while every ring shared one ramp. v2.6 gives each ring its own
        scale in its own units, and that is what the reader decodes against."""
        res, svg = _render(tmp_path, layout)
        body = svg.read_text()
        for i, key in enumerate(("logFC (yours)", "Tractability", "Safety flags",
                                 "Regional specificity", "Pipeline: furthest phase",
                                 "Literature volume"), start=1):
            assert f"{i}. {key}" in body, (layout, key)
        # In the ring's own units, not a position on a shared ramp.
        assert "PubTator3 records" in body
        assert "GTEx v8" in body

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_it_says_the_rings_are_not_comparable(self, tmp_path: Path, layout: str) -> None:
        """The natural reading of a Circos plot is radial, and that reading is
        invalid here. v2.5 said "RESCALED WITHIN EACH RING"; v2.6's claim is
        stronger because the rings no longer share colours either."""
        _, svg = _render(tmp_path, layout)
        body = svg.read_text().replace("\n", " ")
        assert "EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS" in body
        assert "a colour means nothing outside its own ring" in body

    def test_the_value_swatch_no_longer_hides_the_point_in_a_parenthetical(
            self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "audit")
        assert "per-ring scale, viridis" not in svg.read_text()


class TestA6NoTextOverTheRings:
    """A6, measured in the SVG: the key's longest line ran to x=429 while the
    circle's left edge at that height was x=262, and the footer block occupied
    y=983-1028 with `CDKN2A (k=8) *` at y=995 inside it."""

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_a_clean_render_has_no_collisions(self, tmp_path: Path, layout: str) -> None:
        # render() runs the assertion itself; reaching here is the check.
        _render(tmp_path, layout)

    @pytest.mark.parametrize("n", [2, 6, 40])
    def test_no_collisions_across_the_range_of_list_sizes(
            self, tmp_path: Path, n: int) -> None:
        genes = [f"G{i:02d}" for i in range(n)]
        spec = {"logfc": {g: value(float(i)) for i, g in enumerate(genes)},
                # inside the declared [0, 1] domain, so assert_within_domain
                # does not fire on synthetic data
                "tractable": {g: value((i % 4) / 3) for i, g in enumerate(genes)}}
        for layout in ("audit", "slide"):
            render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                   region="Liver", out_svg=tmp_path / f"{layout}{n}.svg",
                   input_count=n, rendered_count=n, ledger_counts={}, layout=layout,
                   ordering_note=ORDER_NOTE)

    def test_the_check_would_catch_a_block_over_the_arcs(self, tmp_path: Path) -> None:
        """A check that has never been seen to fail is not a check."""
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(4, 4))
        ax = fig.add_axes((0.1, 0.1, 0.8, 0.8))
        bad = fig.text(0.5, 0.5, "straight through the middle", ha="center")
        with pytest.raises(AssertionError, match="over the figure's own data"):
            assert_no_text_over_the_rings(fig, ax, [bad])
        plt.close(fig)

    def test_a_near_miss_is_a_collision_too(self, tmp_path: Path) -> None:
        """v2.3 item 1. The slide's footer cleared the bottom spoke label by
        0.3 px on a 900 px canvas — strictly no overlap, so the check passed,
        and one spoke reorder later the same geometry failed. A required
        clearance makes "we got away with it" fail while it is still cheap."""
        import matplotlib.pyplot as plt

        from targetring.figure import LABEL_CLEARANCE_PX
        fig = plt.figure(figsize=(4, 4), dpi=100)
        ax = fig.add_axes((0.3, 0.3, 0.4, 0.4))
        # A spoke label at the very bottom-left corner, outside the circle...
        ax.text(0.02, 0.02, "SPOKE", transform=fig.transFigure, va="bottom")
        fig.canvas.draw()
        lb = ax.texts[0].get_window_extent(renderer=fig.canvas.get_renderer())
        # ...and a footer block ending a fraction of a pixel below it.
        y = (lb.y0 - LABEL_CLEARANCE_PX / 2) / fig.bbox.height
        near = fig.text(0.02, y, "footer", va="top")
        with pytest.raises(AssertionError, match="comes within"):
            assert_no_text_over_the_rings(fig, ax, [near])
        plt.close(fig)

    def test_a_block_in_the_corner_is_not_a_collision(self, tmp_path: Path) -> None:
        """The axes box bounds the circle, so a corner is outside the data."""
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(4, 4))
        ax = fig.add_axes((0.3, 0.3, 0.4, 0.4))
        ok = fig.text(0.02, 0.97, "key", ha="left", va="top")
        assert_no_text_over_the_rings(fig, ax, [ok])
        plt.close(fig)


def _full_ring_spec(genes):
    """Every one of the twelve rings, in range, so the wedge is measured at the
    band spacing a real run actually produces rather than at a six-ring fixture's
    much roomier one."""
    from targetring.figure import RING_SPECS
    out = {}
    for s in RING_SPECS:
        if s.categorical:
            levels = sorted(s.categorical)
            out[s.key] = {g: value(levels[i % len(levels)]) for i, g in enumerate(genes)}
        elif s.domain:
            lo, hi = s.domain
            out[s.key] = {g: value(lo + (hi - lo) * (i % 5) / 4.0)
                          for i, g in enumerate(genes)}
        else:
            out[s.key] = {g: value(1.0 + i * 0.3) for i, g in enumerate(genes)}
    return out


class TestWedge:
    """v2.7, Puck's layout: an angular gap at 12 o'clock with each ring named
    inside it, at that ring's own radius."""

    def _render_n(self, tmp_path: Path, n: int, layout: str):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"{layout}{n}.svg"
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=out, input_count=n, rendered_count=n,
                     ledger_counts={}, ordering_note=ORDER_NOTE, layout=layout,
                     provenance="run X · abc123")
        return res, out

    @pytest.mark.parametrize("n", [3, 14, 50])
    def test_every_ring_is_named_inside_the_circle_on_the_audit_render(
            self, tmp_path: Path, n: int) -> None:
        """The whole point of the wedge over a numbered key: the name is on the
        figure, at the radius of the band it names."""
        from targetring.figure import RING_SPECS
        _, svg = self._render_n(tmp_path, n, "audit")
        body = svg.read_text()
        for spec in RING_SPECS:
            # `fit_wedge_labels` may have wrapped a name onto a second line, so
            # the words are checked rather than the joined string.
            for word in spec.wedge_label.split():
                assert word in body, f"{spec.key}: {word!r} missing from the audit SVG"

    @pytest.mark.parametrize("n", [3, 14, 50])
    def test_the_gap_does_not_move_with_the_gene_count(
            self, tmp_path: Path, n: int) -> None:
        """Puck's condition. A gap that narrowed as genes were added would make
        the innermost ring's label size a function of somebody's submission."""
        from targetring.figure import AUDIT
        _, svg = self._render_n(tmp_path, n, "audit")
        assert f"in a {AUDIT.wedge_deg:g}° gap at 12 o'clock" in svg.read_text()

    def test_the_figure_states_what_the_gap_cost_the_spokes(
            self, tmp_path: Path) -> None:
        """At 50 genes the spokes are the thin case, so the number is printed
        rather than absorbed: 4.72° against a full circle's 5.20°."""
        _, svg = self._render_n(tmp_path, 50, "audit")
        body = svg.read_text()
        assert "50 spokes at 4.72°" in body
        assert "5.20° a full circle would give" in body
        assert "(-9.2%)" in body or "(−9.2%)" in body

    def test_the_gap_is_reserved_and_the_first_spoke_starts_after_it(self) -> None:
        """Measured on the sectors, not on the sentence: the empty span is
        exactly `wedge_deg`, which is what `endspace=False` buys."""
        from pycirclize import Circos

        from targetring.figure import AUDIT
        w = AUDIT.wedge_deg
        c = Circos({f"G{i}": 1 for i in range(50)}, start=w / 2, end=360 - w / 2,
                   space=2, endspace=False)
        first, last = c.sectors[0], c.sectors[-1]
        assert first.deg_lim[0] == pytest.approx(w / 2)
        assert 360 - last.deg_lim[1] == pytest.approx(w / 2)
        assert first.deg_size == pytest.approx(4.72, abs=0.01)

    def test_the_slide_names_its_rings_in_the_column_and_says_why(
            self, tmp_path: Path) -> None:
        """The constraint on the slide is radial, so no wedge angle fixes it:
        twelve bands over 134 px of radius against 13 px of legible type. The
        render says so rather than silently differing from the audit."""
        from targetring.figure import SLIDE
        assert SLIDE.wedge_deg == 0
        _, svg = self._render_n(tmp_path, 14, "slide")
        body = svg.read_text()
        assert "gap at 12 o'clock" not in body
        assert "a ring band is 11 px" in body

    def test_text_in_the_wedge_is_not_text_over_the_rings(self, tmp_path: Path) -> None:
        """The assertion has to understand the gap rather than assume full
        sectors: a name at r=76 fails the radius test and passes the wedge
        test, and both are correct."""
        import math

        import matplotlib.pyplot as plt
        from pycirclize import Circos

        from targetring.figure import (
            AUDIT,
            assert_wedge_labels_fit,
        )
        w = AUDIT.wedge_deg
        c = Circos({f"G{i}": 1 for i in range(6)}, start=w / 2, end=360 - w / 2,
                   space=8, endspace=False)
        for g in (f"G{i}" for i in range(6)):
            c.get_sector(g).add_track((72.0, 80.0)).rect(0, 1, fc="#dddddd")
        fig = c.plotfig(figsize=AUDIT.figsize, dpi=100)
        ax = fig.axes[0]
        fig.set_layout_engine("none")
        ax.set_position(AUDIT.axes_box)
        good = ax.text(0.0, 76.0, "Tractability", size=AUDIT.wedge_label_size,
                       ha="center", va="center")
        # Inside the circle, so the old check calls it a collision...
        with pytest.raises(AssertionError, match="overlaps the rings"):
            assert_no_text_over_the_rings(fig, ax, [good])
        # ...and told about the wedge, it is exempt and passes its own check.
        assert_no_text_over_the_rings(fig, ax, [good], [good])
        assert_wedge_labels_fit(fig, ax, [good], w)

        # A name the same distance out but swung clear of the gap is over a
        # sector, and the wedge check is what catches it.
        bad = ax.text(math.radians(90.0), 76.0, "Tractability",
                      size=AUDIT.wedge_label_size, ha="center", va="center")
        with pytest.raises(AssertionError, match="overhangs the 26° gap"):
            assert_wedge_labels_fit(fig, ax, [bad], w)
        plt.close(fig)

    def test_a_name_too_long_for_its_radius_refuses_rather_than_overprints(
            self, tmp_path: Path) -> None:
        """QA-v2-retest §5 is the record of what truncating a ring label does.
        The wedge never truncates: it wraps, then shrinks to a floor, then
        refuses."""
        import matplotlib.pyplot as plt
        from pycirclize import Circos

        from targetring.figure import AUDIT, assert_wedge_labels_fit
        w = AUDIT.wedge_deg
        c = Circos({f"G{i}": 1 for i in range(6)}, start=w / 2, end=360 - w / 2,
                   space=8, endspace=False)
        fig = c.plotfig(figsize=AUDIT.figsize, dpi=100)
        ax = fig.axes[0]
        fig.set_layout_engine("none")
        ax.set_position(AUDIT.axes_box)
        t = ax.text(0.0, 50.0, "an unbroken ring name far too long for the inner radius",
                    size=AUDIT.wedge_label_size, ha="center", va="center")
        with pytest.raises(AssertionError, match="does not fit the 12 o'clock gap"):
            assert_wedge_labels_fit(fig, ax, [t], w)
        plt.close(fig)

    def test_two_ring_names_that_would_touch_are_a_failure(self) -> None:
        """The radial constraint, checked rather than assumed — it is what
        decided the slide render, and `RING_SPECS` growing by one is how it
        would first go wrong on the audit render too."""
        import matplotlib.pyplot as plt
        from pycirclize import Circos

        from targetring.figure import AUDIT, assert_wedge_labels_fit
        w = AUDIT.wedge_deg
        c = Circos({f"G{i}": 1 for i in range(6)}, start=w / 2, end=360 - w / 2,
                   space=8, endspace=False)
        fig = c.plotfig(figsize=AUDIT.figsize, dpi=100)
        ax = fig.axes[0]
        fig.set_layout_engine("none")
        ax.set_position(AUDIT.axes_box)
        a = ax.text(0.0, 80.0, "TDL", size=AUDIT.wedge_label_size,
                    ha="center", va="center")
        b = ax.text(0.0, 80.4, "k", size=AUDIT.wedge_label_size,
                    ha="center", va="center")
        with pytest.raises(AssertionError, match="px apart, under the"):
            assert_wedge_labels_fit(fig, ax, [a, b], w)
        plt.close(fig)

    def test_the_footer_no_longer_claims_the_circle_starts_at_12_oclock(
            self, tmp_path: Path) -> None:
        """COUNCIL V5. 12 o'clock is the middle of the gap now and the first
        spoke begins half a wedge clockwise of it."""
        _, svg = self._render_n(tmp_path, 14, "audit")
        body = svg.read_text()
        assert "starts at 12 o'clock" not in body
        assert "runs clockwise from the ring-name gap at 12 o'clock" in body

    def test_each_renders_caption_describes_that_render(self, tmp_path: Path) -> None:
        """`caption.txt` is the slide's artefact — text to paste underneath it —
        and `run.py` writes the slide render's. Until v2.7 the two captions were
        word for word identical; now each states its own geometry, and pasting
        the audit's gap sentence under a slide that has no gap would be COUNCIL
        V5 in the one file that is read beside the figure."""
        audit, _ = self._render_n(tmp_path, 14, "audit")
        slide, _ = self._render_n(tmp_path, 14, "slide")
        assert "26° gap at 12 o'clock" in audit.caption
        assert "runs clockwise from the ring-name gap" in audit.caption
        # The slide reserves no gap, so its first spoke really is at 12 o'clock
        # and its caption says the old sentence, which is true there and only
        # there. It may still *refer* to the audit's gap; what it may not do is
        # claim one of its own.
        assert "starts at 12 o'clock and runs clockwise" in slide.caption
        assert "runs clockwise from the ring-name gap" not in slide.caption
        assert "in the column beside the circle, not inside it" in slide.caption

    def test_the_scales_stay_in_the_column_and_the_figure_says_why(
            self, tmp_path: Path) -> None:
        """Name in the wedge, scale in the column. Measured: the innermost band
        has ~42 px of half-width in the gap, against the 280 px the colour bar
        alone occupies — so the two cannot share one place, and the figure says
        which holds which."""
        _, svg = self._render_n(tmp_path, 14, "audit")
        body = svg.read_text()
        assert "the gap is too narrow for a colour bar" in body
        assert "EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS" in body


class TestB1SectorCeiling:
    """B1: `Circos({...}, space=2)` raises "Too large sector space size is set!!"
    for n >= 181, which reached the user as a raw traceback after ~13 minutes of
    fetching."""

    def test_the_input_cap_is_below_the_geometric_ceiling(self) -> None:
        from targetring.config import CIRCOS_MAX_SECTORS, MAX_GENES
        assert MAX_GENES < CIRCOS_MAX_SECTORS
        assert MAX_GENES * 2 < 360

    def test_an_over_large_render_says_what_happened(self, tmp_path: Path) -> None:
        """v2.7: the budget the message quotes is 360 minus the ring-name gap,
        not 360. The gap is reserved before any sector is placed, so a guard
        that still tested against a full circle would let 26 degrees' worth of
        genes through into the traceback this exists to prevent."""
        genes = [f"G{i}" for i in range(200)]
        spec = {"logfc": {g: value(1.0) for g in genes}}
        with pytest.raises(ValueError, match=r"exceeds the 334 degrees left after the 26"):
            render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                   region="Liver", out_svg=tmp_path / "big.svg",
                   input_count=200, rendered_count=200, ledger_counts={},
                   ordering_note=ORDER_NOTE)

    def test_the_slide_guard_quotes_the_full_circle_it_actually_uses(
            self, tmp_path: Path) -> None:
        """The slide declares no wedge, so its budget really is 360 degrees and
        the message must not invent a gap the render does not reserve."""
        genes = [f"G{i}" for i in range(200)]
        spec = {"logfc": {g: value(1.0) for g in genes}}
        with pytest.raises(ValueError, match=r"exceeds the 360 degrees of a circle"):
            render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                   region="Liver", out_svg=tmp_path / "big.svg", layout="slide",
                   input_count=200, rendered_count=200, ledger_counts={},
                   ordering_note=ORDER_NOTE)

    def test_the_cap_applies_before_any_network_work(self) -> None:
        """`parse_de_table` runs first in run_analysis, before resolve and
        before any adapter."""
        from targetring.config import MAX_GENES
        from targetring.de_input import parse_de_table
        rows = "\n".join(f"G{i},1.0,0.01" for i in range(MAX_GENES + 25))
        parsed = parse_de_table("gene,logFC,padj\n" + rows)
        assert len(parsed.rows) == MAX_GENES
        capped = [e for e in parsed.ledger if e.status == "dropped_over_cap"]
        assert len(capped) == 25
        assert all(e.input_symbol for e in capped), "every excluded gene is named"


class TestExportRemainsEditable:
    """The UX review's editability findings, and the one that was incidental.

    "the real-text behaviour is incidental: matplotlib's own default is
    `svg.fonttype: 'path'`, and it is *pycirclize* that flips it to 'none' on
    import. Nothing in this repo asserts it. A dependency bump could silently
    turn all your text into outlines."
    """

    def test_text_is_real_text_not_outlines(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "audit")
        body = svg.read_text()
        assert "<text" in body
        assert body.count("<text") > 20
        # With svg.fonttype='path' matplotlib emits the glyphs as <path
        # id="DejaVuSans-..."> in <defs> and references them with <use>. The
        # colour bar's tick mark is also a <use>, so the check is specifically
        # for glyph references, not for <use> at all.
        assert "DejaVuSans-" not in body, "the text was rendered as outlines"
        assert not re.search(r'<use[^>]*xlink:href="#[A-Za-z]+-[0-9a-f]+"', body)

    def test_the_fonttype_is_pinned_by_this_repo_not_by_a_dependency(self) -> None:
        import matplotlib
        from targetring.figure import SVG_FONTTYPE
        assert SVG_FONTTYPE == "none"
        assert matplotlib.rcParams["svg.fonttype"] == "none"

    def test_the_canvas_is_exactly_what_the_layout_declares(self, tmp_path: Path) -> None:
        """A tight bbox with 0.5in of padding grew v1's 17.5x14 canvas to
        18.3x15.2 — and would have made the 16:9 render 1.83:1."""
        for layout, lay in (("audit", AUDIT), ("slide", SLIDE)):
            _, svg = _render(tmp_path, layout)
            w, h = _viewbox(svg)
            # matplotlib writes the SVG viewBox in points, 72 to the inch.
            assert abs(w / 72.0 - lay.figsize[0]) < 0.05, (layout, w)
            assert abs(h / 72.0 - lay.figsize[1]) < 0.05, (layout, h)

    def test_every_cell_is_its_own_named_group(self, tmp_path: Path) -> None:
        res, svg = _render(tmp_path, "audit")
        ids = re.findall(r'id="(cell\|[^"]+)"', svg.read_text())
        assert len(ids) == len(res.csv_rows)

    def test_no_clippath_hides_anything(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "audit")
        assert "clipPath" not in svg.read_text()


class TestA3DroppedGenesAreNamed:
    """UX A3: "the slide cannot answer 'where's TP53?' — and neither can the CSV
    you'd send alongside it. More than half the input vanished and the artefact
    you present is silent about which half." """

    DROPPED = ["TP53", "EGFR", "BRCA1", "PTEN", "9-Sep", "1-Mar",
               "NOTAREALGENE123", "ALB", "KCNQ1"]

    def test_the_audit_figure_names_them(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "audit", dropped_names=self.DROPPED)
        body = svg.read_text()
        for name in self.DROPPED:
            assert f">{name}" in body or name in body, name

    def test_the_slide_points_at_the_caption_rather_than_shrinking_to_fit(
            self, tmp_path: Path) -> None:
        """Nine names plus seven category counts in the hole shrinks all of it
        past legibility, which is A4 reappearing in the middle of the figure."""
        res, svg = _render(tmp_path, "slide", dropped_names=self.DROPPED)
        assert "named in the caption" in svg.read_text()
        assert "TP53" in res.caption and "NOTAREALGENE123" in res.caption

    def test_the_caption_names_every_one(self, tmp_path: Path) -> None:
        res, _ = _render(tmp_path, "audit", dropped_names=self.DROPPED)
        for name in self.DROPPED:
            assert name in res.caption, name
        assert "ledger.csv" in res.caption

    def test_the_centre_never_spills_over_the_rings(self, tmp_path: Path) -> None:
        """The broken fixture produces seven drop categories and nine names, so
        the block's width is user-driven and cannot be assumed. render() fits it
        and asserts the fit; reaching here is the check."""
        many = [f"GENE{i}" for i in range(40)]
        for layout in ("audit", "slide"):
            _render(tmp_path, layout, dropped_names=many,
                    ledger_counts={f"reason_{i}": i + 1 for i in range(7)})


class TestNothingIsClippedAtTheCanvasEdge:
    """matplotlib does not clip figure-level text, and with `savefig.bbox`
    pinned to the declared canvas size anything past the edge is simply cut.

    Found by looking at a rendered figure: the colour-bar caption began at
    x=1190.7 in a 1260 pt canvas and needed ~95 pt, so "RESCALED WITHIN EACH
    RING" — the one string that tells a reader the rings are not comparable —
    was clipped to "RESCALED WITHIN E".
    """

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_a_clean_render_clips_nothing(self, tmp_path: Path, layout: str) -> None:
        # render() runs the assertion; reaching here is the check.
        _render(tmp_path, layout)

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_the_comparability_warning_is_whole_in_the_svg(
            self, tmp_path: Path, layout: str) -> None:
        """Measured in the export, not in the drawing call."""
        import xml.etree.ElementTree as ET
        _, svg = _render(tmp_path, layout)
        root = ET.parse(svg).getroot()
        vb_w = float(root.get("viewBox").split()[2])
        ns = "{http://www.w3.org/2000/svg}"
        found = False
        for el in root.iter(f"{ns}text"):
            text = "".join(el.itertext())
            if "EACH RING HAS ITS OWN SCALE" not in text:
                continue
            found = True
            tr = el.get("transform") or ""
            m = re.match(r"translate\(([\d.-]+)", tr)
            assert m, tr
            x = float(m.group(1))
            # ~0.62 em per character is a conservative width for this face.
            size = float(re.search(r"font-size:\s*([0-9.]+)px", svg.read_text()).group(1))
            assert x + len(text) * 0.5 * size < vb_w, (
                f"{text!r} starts at {x:.0f} on a {vb_w:.0f}-wide canvas and would be cut")
        assert found, "the comparability warning must be on the figure at all"

    def test_the_check_would_catch_a_block_off_the_edge(self) -> None:
        import matplotlib.pyplot as plt
        from targetring.figure import assert_text_within_canvas
        fig = plt.figure(figsize=(4, 4))
        bad = fig.text(0.97, 0.5, "a long caption that runs off the right edge",
                       ha="left")
        with pytest.raises(AssertionError, match="runs off the edge"):
            assert_text_within_canvas(fig, [bad])
        plt.close(fig)


class TestTheSafetyCaveatReachesBothRenders:
    """QA-v2-retest §5. V2 §2.4's central move was renaming the safety ring to
    "Safety flags & constraint — NOT evidence of safety". The rename reached
    `values.csv` and `caption.txt` and reached neither picture: the audit
    figure's ring key truncated it at 34 characters to
    "Safety flags & constraint — NOT e…", and the slide render carries no ring
    key at all, so the artefact that goes in front of colleagues had the ring
    and not the thing that qualifies it.

    The four safety keys buy comparability across genes; they do not separate a
    clean target from an unstudied one. A figure that shows the ring and drops
    the caveat overstates its data, which is the one thing AGENTS.md forbids —
    "Say this on the figure, not only in the report".
    """

    def _svg_text(self, svg: Path) -> str:
        """Every string the SVG actually renders, in order, with runs of
        whitespace collapsed.

        Read out of the `<text>` elements rather than off the file, so nothing
        passes on a string that is in the source and not drawn. Matplotlib emits
        one element per line, so whitespace is collapsed across them: a caveat
        that *wraps* is intact, a caveat that is *elided* is not, and this has
        to be able to tell those two apart.
        """
        root = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        return re.sub(
            r"\s+", " ",
            " ".join("".join(el.itertext()) for el in root.iter(f"{ns}text")))

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_the_caveat_is_in_the_render(self, tmp_path: Path, layout: str) -> None:
        from targetring.figure import SAFETY_RING_CAVEAT
        _, svg = _render(tmp_path, layout)
        assert SAFETY_RING_CAVEAT in self._svg_text(svg), (
            f"the {layout} render draws the safety ring without the caveat that "
            f"qualifies it")

    def test_the_audit_key_no_longer_truncates_it(self, tmp_path: Path) -> None:
        """The specific v2 artefact: "…& constraint — NOT e…" in the ring key."""
        _, svg = _render(tmp_path, "audit")
        text = self._svg_text(svg)
        assert "NOT e…" not in text, "truncated mid-caveat, as in v2"
        assert "Safety flags & constraint" in text

    def test_the_caption_still_carries_it_too(self, tmp_path: Path) -> None:
        """The slide's caption is not where the caveat lives, but it must not
        have lost it in the move either."""
        from targetring.figure import SAFETY_RING_CAVEAT
        res, _ = _render(tmp_path, "slide")
        assert SAFETY_RING_CAVEAT in res.caption

    def test_a_long_ring_label_wraps_rather_than_eliding(self, tmp_path: Path) -> None:
        """The mechanism, in isolation: truncation is never the answer for a
        label whose tail is a caveat."""
        from targetring.figure import _key_lines
        lines = _key_lines("4. ", "Safety flags & constraint — NOT evidence of safety",
                           "[-1, 1]")
        assert len(lines) > 1, "wrapped, not shortened"
        assert "NOT evidence of safety" in " ".join(l.strip() for l in lines)
        assert "…" not in "".join(lines)
