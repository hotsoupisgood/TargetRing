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

    def test_a_short_label_is_written_not_truncated(self, tmp_path: Path) -> None:
        """QA-v2-retest §5: `_short` truncated at 34 characters and turned
        "Safety flags & constraint — NOT evidence of safety" into "…NOT e…", so
        the figure overstated its data.

        Two things are checkable here. Every short label is a written phrase,
        not a machine cut — nothing trails off. And the two hedges that change
        what a ring *claims* are on the figure whatever the wedge says: the
        safety caveat (COUNCIL V4) and the mouse ring being a lead rather than a
        verdict (AGENTS.md rule 6). `Mouse phenotype` does drop the word "lead"
        from its full label, and that is only acceptable because the footer
        carries the hedge in full on the same render.
        """
        from targetring.figure import RING_SPECS
        for spec in RING_SPECS:
            assert spec.short_label, f"{spec.key} has no short_label"
            assert not spec.short_label.rstrip().endswith(("…", "...", "-")), (
                f"{spec.key}: {spec.short_label!r} trails off")
        _, svg = self._render_n(tmp_path, 14, "audit")
        body = svg.read_text()
        # The scale column wraps the safety title onto a continuation line, so
        # the caveat is checked in the two pieces the figure actually draws.
        assert "NOT evidence" in body and "of safety" in body
        assert "a LEAD, not a verdict" in body

    def test_every_tooltip_answers_what_is_this_and_what_am_i_looking_at(self) -> None:
        """Full name, what one number is, what its ends mean, and who says so."""
        from targetring.figure import RING_SPECS
        for spec in RING_SPECS:
            if not spec.wedge_tooltip:
                assert spec.tooltip == "", f"{spec.key} builds a tooltip it does not use"
                continue
            tip = spec.tooltip
            assert spec.label in tip, f"{spec.key}: tooltip omits the full name"
            assert spec.units in tip, f"{spec.key}: tooltip omits the units"
            assert f"source: {spec.short_source}" in tip, f"{spec.key}: no source"
            assert len(tip) > len(spec.short_label) * 3, (
                f"{spec.key}: the tooltip adds too little to be worth a hover")

    def test_the_tooltip_is_in_the_file_and_not_in_the_app(self, tmp_path: Path) -> None:
        """`app.js` inlines `figure.svg`, so an SVG `<title>` is a native
        tooltip with no JavaScript — and the *downloaded* SVG still has it. A
        handler in `app.js` would be a tooltip that only existed while the app
        was running."""
        from targetring.figure import RING_SPECS
        _, svg = self._render_n(tmp_path, 14, "audit")
        body = svg.read_text()
        for spec in RING_SPECS:
            anchor = f'<g id="ringname|{spec.key}">'
            assert anchor in body, f"{spec.key} has no wedge label in the SVG"
            has_title = f"{anchor}\n   <title>" in body
            assert has_title == spec.wedge_tooltip, (
                f"{spec.key}: tooltip in the SVG={has_title}, declared="
                f"{spec.wedge_tooltip}")
        # Puck's rule, counted: most rings get their point across unaided.
        assert body.count("<title>") == sum(1 for s in RING_SPECS if s.wedge_tooltip)

    def test_the_specificity_tooltip_carries_this_runs_panel_not_a_constant(
            self, tmp_path: Path) -> None:
        """"Region TPM ÷ median" is the ring's meaning and the short label says
        so; how many tissues the median was taken across is the part no short
        label carries, and it is a fact about *this run*."""
        _, svg = self._render_n(tmp_path, 14, "audit")
        body = svg.read_text()
        i = body.index('<g id="ringname|regional_specificity">')
        tip = body[i:i + 900]
        assert "this run: Liver TPM ÷ median across" in tip

    def test_the_slide_carries_no_tooltips_because_it_carries_no_wedge(
            self, tmp_path: Path) -> None:
        """A projected slide has no hover, which is exactly why the printed
        label has to stand alone in the first place."""
        _, svg = self._render_n(tmp_path, 14, "slide")
        body = svg.read_text()
        assert "ringname|" not in body
        assert "<title>" not in body

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


class TestV28SourceLinksInTheLegend:
    """v2.8. Puck: *"can you keep links for sources in the legend."*

    The scale column already named each ring's source in words. These tests
    measure the three things that turns into, and each one is a different
    render's capability:

    * the **SVG** carries a real `<a xlink:href>` per ring, in the file, so the
      download still works in a browser weeks later -- the v2.7.1 argument for
      why a tooltip is not in `app.js`, applied again;
    * the **PNG and the slide** carry no link at all, so the *printed* text has
      to name the source and a domain a reader can type;
    * the **manifest** carries the same URLs, because a figure and its manifest
      disagreeing about where a number came from is a plausible wrong figure in
      an audit trail.
    """

    def _render_full(self, tmp_path: Path, layout: str):
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"src-{layout}.svg"
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=out, input_count=14, rendered_count=14,
                     ledger_counts={}, ordering_note=ORDER_NOTE, layout=layout,
                     provenance="run X · abc123")
        return res, out

    def test_every_external_ring_has_a_citation_and_the_others_say_why(self) -> None:
        """Nine external rings, three of Puck's own or derived. "No link" is a
        stated decision, not a gap a reader has to notice."""
        from targetring.figure import RING_SPECS
        from targetring import sources

        linked = {s.key for s in RING_SPECS if s.source_doc}
        assert linked == set(sources.RING_SOURCE_DOCS)
        for spec in RING_SPECS:
            if spec.key in linked:
                continue
            assert spec.key in sources.UNLINKED_RINGS, (
                f"{spec.key} has neither a citation nor a stated reason for not "
                f"having one")

    def test_a_citation_points_at_a_field_not_at_a_homepage(self) -> None:
        """"Open Targets" linking to opentargets.org is nearly useless. The
        structural half of that, asserted; the live half is
        `scripts/check_source_links.py`."""
        from targetring import sources
        sources.assert_registry_is_coherent()
        for key, doc in sources.RING_SOURCE_DOCS.items():
            path = doc.url.split("/", 3)[3].partition("#")[0]
            assert path.strip("/"), f"{key}: {doc.url} is a bare origin"
            assert doc.reads, f"{key}: does not say which field it cites"

    def test_the_registry_refuses_a_homepage_and_a_mismatched_domain(self) -> None:
        """The two edits that would quietly undo this work: a link back to a
        homepage, and a domain printed on the figure that is not where the link
        goes -- which strands every reader of the PNG."""
        from dataclasses import replace
        from targetring import sources

        doc = sources.RING_SOURCE_DOCS["tdl"]
        for bad, why in (
            (replace(doc, url="https://pharos.nih.gov/"), "homepage"),
            (replace(doc, url="https://pharos.nih.gov/about"), "mismatched domain"),
            (replace(doc, url="http://druggablegenome.net/IDGProteinFamilies"), "http"),
        ):
            saved = dict(sources.RING_SOURCE_DOCS)
            sources.RING_SOURCE_DOCS["tdl"] = bad
            try:
                with pytest.raises(AssertionError):
                    sources.assert_registry_is_coherent()
            finally:
                sources.RING_SOURCE_DOCS.clear()
                sources.RING_SOURCE_DOCS.update(saved)

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_the_link_is_in_the_file_and_not_added_by_the_app(
            self, tmp_path: Path, layout: str) -> None:
        """`set_url` is matplotlib's own SVG-backend feature, so the anchor is
        written into `figure.svg` at save time. `app.js` inlines that file, which
        is how it is clickable in the web app with no JavaScript at all."""
        from targetring.figure import RING_SPECS
        _, svg = self._render_full(tmp_path, layout)
        body = svg.read_text()
        for spec in RING_SPECS:
            anchor = f'<g id="ringsource|{spec.key}">'
            assert anchor in body, f"{spec.key}: no source line in the {layout} SVG"
            after = body[body.index(anchor):body.index(anchor) + 400]
            if spec.source_doc:
                assert f'<a xlink:href="{spec.source_doc.url}"' in after, (
                    f"{spec.key}: source line is not a link")
            else:
                assert "<a xlink:href=" not in after, (
                    f"{spec.key}: drawn as a link with nothing to cite")

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_the_printed_text_survives_export_to_a_png(
            self, tmp_path: Path, layout: str) -> None:
        """Nothing in a PNG or on a projected slide is clickable, so a bare
        underlined word would die on export. The source name AND a readable
        domain are drawn, on both renders."""
        from targetring.figure import RING_SPECS
        _, svg = self._render_full(tmp_path, layout)
        text = re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in
            ET.parse(svg).getroot().iter("{http://www.w3.org/2000/svg}text")))
        for spec in RING_SPECS:
            doc = spec.source_doc
            if not doc:
                continue
            assert doc.name in text, f"{spec.key}: source name not on the {layout} render"
            assert doc.domain in text, f"{spec.key}: no domain a reader could type"

    def test_the_legend_link_and_the_manifest_cannot_disagree(
            self, tmp_path: Path) -> None:
        """Both read one registry, and this is the check that they still do: the
        set of hrefs in the figure equals the set of `doc_url`s in the manifest.
        A second copy of a URL anywhere makes this fail."""
        from targetring import manifest as manifest_mod
        from targetring.figure import RING_SPECS
        from targetring.invariants import InvariantReport

        _, svg = self._render_full(tmp_path, "audit")
        in_figure = set(re.findall(r'<a xlink:href="([^"]+)"', svg.read_text()))
        man = manifest_mod.build(
            run_id="T", region="Liver", input_row_count=14, rendered_count=14,
            ledger_count=0, gene_order=[], invariants=InvariantReport(),
            contracts=[], ledger=[], llm={}, timings={},
            ring_keys=[s.key for s in RING_SPECS])
        rows = man.as_dict()["ring_sources"]
        assert [r["ring"] for r in rows] == [s.key for s in RING_SPECS], (
            "the manifest lists the rings in a different order from the legend")
        assert {r["doc_url"] for r in rows if r["doc_url"]} == in_figure
        for row in rows:
            if row["linked"] == "yes":
                assert row["endpoint"] and row["version"], row["ring"]
            else:
                assert row["reason"] and row["reason"] != "unstated", row["ring"]

    def test_the_manifest_names_the_endpoint_of_every_ring_it_cites(self) -> None:
        """v2.8 found Pharos absent from `SOURCE_URLS` — the source behind four
        discrete levels on the figure was the one the manifest never named, and
        AGENTS.md requires every source URL."""
        from targetring import manifest as manifest_mod, sources
        urls = set(manifest_mod.SOURCE_URLS.values())
        assert manifest_mod.SOURCE_URLS["pharos"]
        for key, doc in sources.RING_SOURCE_DOCS.items():
            assert any(doc.endpoint.startswith(u) or u.startswith(doc.endpoint)
                       for u in urls), f"{key}: {doc.endpoint} is in no SOURCE_URLS entry"

    def test_the_caption_writes_the_url_out_in_full(self, tmp_path: Path) -> None:
        """The caption is what a slide reader is handed, and a slide has no
        links, so the domain is not enough there — the whole URL is written."""
        from targetring.figure import RING_SPECS
        res, _ = self._render_full(tmp_path, "slide")
        for spec in RING_SPECS:
            if spec.source_doc:
                assert spec.source_doc.url in res.caption, spec.key
        assert "manifest.json" in res.caption

    def test_the_tooltip_carries_the_url_a_wedge_label_has_no_room_for(self) -> None:
        from targetring.figure import RING_SPECS
        for spec in RING_SPECS:
            if spec.wedge_tooltip and spec.source_doc:
                assert spec.source_doc.url in spec.tooltip, spec.key


class TestV29EnsemblIdsNextToTheGeneName:
    """v2.9. Puck: *"can you put the ensembl id's next to the gene name?"*, with
    the condition that if it does not fit on the rim the rule must be **explicit
    and stated on the figure** rather than silently truncated.

    It does not fit, at any gene count, and not for the reason either of us
    expected -- a spoke label runs radially, so the id reaches out into the
    scale column rather than crowding the neighbouring spoke. These tests pin
    the measurement, the stated rule, and the two places the id does live.
    """

    def _render_full(self, tmp_path: Path, n: int, layout: str, **kw):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"id-{layout}{n}.svg"
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=out, input_count=n, rendered_count=n,
                     ledger_counts={}, ordering_note=ORDER_NOTE, layout=layout,
                     provenance="run X · abc123", **kw)
        return res, out

    def test_the_values_csv_carries_the_id_for_every_rendered_gene(
            self, tmp_path: Path) -> None:
        """The half that matters most: the CSV is what makes a figure checkable
        against a source, and a symbol is ambiguous where an Ensembl id is not.
        The drop ledger has carried `ensembl_id` since v1, so until v2.9 a
        *dropped* gene's id was in the artefacts and a *rendered* gene's was
        not."""
        ids = {f"GENE{i:03d}": f"ENSG{i:011d}" for i in range(14)}
        res, _ = self._render_full(tmp_path, 14, "audit", gene_ids=ids)
        assert res.csv_rows, "no cells"
        for row in res.csv_rows:
            assert "ensembl_id" in row, "values.csv has no ensembl_id column"
            assert row["ensembl_id"] == ids[row["gene"]], row["cell_id"]
        # Next to the gene, not appended at the end of the row.
        cols = list(res.csv_rows[0].keys())
        assert cols.index("ensembl_id") == cols.index("gene") + 1, cols

    def test_a_gene_the_resolver_gave_no_id_is_blank_not_invented(
            self, tmp_path: Path) -> None:
        """AGENTS.md rule 1's shape, applied to a column: an absent id is empty,
        never a placeholder that reads like an identifier."""
        res, _ = self._render_full(tmp_path, 6, "audit", gene_ids={"GENE000": "ENSG1"})
        blanks = {r["ensembl_id"] for r in res.csv_rows if r["gene"] != "GENE000"}
        assert blanks == {""}, blanks

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    @pytest.mark.parametrize("n", [6, 14, 50])
    def test_no_spoke_label_carries_an_id_at_any_gene_count(
            self, tmp_path: Path, n: int, layout: str) -> None:
        """Puck asked me to measure rather than assume, and the measurement says
        never -- so "never" is what is checked, at the two counts he named and at
        the six-gene case that turned out to fail just as hard."""
        ids = {f"GENE{i:03d}": f"ENSG{i:011d}" for i in range(n)}
        _, svg = self._render_full(tmp_path, n, layout, gene_ids=ids)
        text = re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in
            ET.parse(svg).getroot().iter("{http://www.w3.org/2000/svg}text")))
        assert "ENSG00000000" not in text, (
            f"{layout} at n={n} drew an Ensembl id on the figure")

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_the_figure_states_where_the_ids_are(
            self, tmp_path: Path, layout: str) -> None:
        """Not silently absent. A reader who cannot see an id must be told it is
        one click away, on both renders -- the slide draws no footer, so it is
        the caption that has to carry it there."""
        from targetring.figure import _ID_NOTE
        res, svg = self._render_full(tmp_path, 14, layout)
        body = re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in
            ET.parse(svg).getroot().iter("{http://www.w3.org/2000/svg}text")))
        where = body if layout == "audit" else re.sub(r"\s+", " ", res.caption)
        for phrase in ("Ensembl id per gene", "values.csv", "NOT on the rim"):
            assert phrase in where, (layout, phrase)
        assert _ID_NOTE in re.sub(r"\s+", " ", res.caption)

    def test_putting_an_id_back_on_the_rim_is_caught_not_drawn(self) -> None:
        """The check that matters in six months. At six genes the rim *looks*
        roomy, so someone will try; the figure would then print a sentence its
        own labels contradict, which is COUNCIL V5."""
        from targetring.figure import _ID_NOTE, assert_the_id_rule_is_kept
        assert_the_id_rule_is_kept(["BRAF (k=9/9)"], [_ID_NOTE])
        with pytest.raises(AssertionError, match="NOT on the rim"):
            assert_the_id_rule_is_kept(
                ["BRAF · ENSG00000157764 (k=9/9)"], [_ID_NOTE])
        with pytest.raises(AssertionError, match="where its Ensembl ids are"):
            assert_the_id_rule_is_kept(["BRAF (k=9/9)"], ["some other note"])

    def test_the_measurement_the_rule_rests_on_still_holds(self) -> None:
        """The 121 px and the 155 px in `_ID_NOTE` are the whole argument, so
        they are re-measured rather than remembered. If the layout changes
        enough to make the id fit, this fails and the sentence gets rewritten --
        which is the right way round."""
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath
        from targetring.figure import AUDIT

        w, h = AUDIT.figsize[0] * 100, AUDIT.figsize[1] * 100
        ax_x, _, ax_w, ax_h = AUDIT.axes_box
        radius = min(ax_w * w, ax_h * h) / 2
        room = AUDIT.scale_box[0] * w - ((ax_x + ax_w / 2) * w + radius)
        advance = lambda s: TextPath(  # noqa: E731
            (0, 0), s, size=AUDIT.label_size,
            prop=FontProperties(family="DejaVu Sans")).get_extents().width

        plain = advance("GENE001 (k=9/9)")
        withid = advance("GENE001 · ENSG00000157764 (k=9/9)")
        assert 115 < room < 130, f"radial room is {room:.0f} px, not the measured 121"
        assert plain < room, f"the plain label ({plain:.0f} px) should fit in {room:.0f}"
        assert withid > room, (
            f"an id now fits ({withid:.0f} px in {room:.0f}) — re-decide the rule "
            f"and rewrite the footer sentence")


class TestV30TheExportAndTheAppMayDiffer:
    """v3.0. Puck: *"download figure and the online figure can be different.
    also we could have the download download legend as a separate image."*

    That releases a constraint every render has worked under: one artefact had
    to serve a screen that already showed most of the caveats in its own
    furniture AND a colleague reading a PNG with nobody to ask. The two now go
    in opposite directions — and every test here exists because divergence is
    only safe if three things are pinned:

      * the cells are identical (`assert_renders_agree`);
      * each file is the render its name promises
        (`assert_artefacts_are_their_own_layout`);
      * whatever else a render drops, it never drops the four claims that make
        a circle honest on its own (`assert_figure_states_the_minimum`).
    """

    def _render(self, tmp_path: Path, layout: str, n: int = 14, **kw):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"v30-{layout}.svg"
        kw.setdefault("ledger_counts", {"ambiguous": 1})
        kw.setdefault("dropped_names", ["HBB"])
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=out, input_count=n + 1,
                     rendered_count=n, ordering_note=ORDER_NOTE, layout=layout,
                     provenance="run T1 · abc123", **kw)
        return res, out

    @staticmethod
    def _text(svg: Path) -> str:
        return re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in
            ET.parse(svg).getroot().iter("{http://www.w3.org/2000/svg}text")))

    def test_the_two_renders_draw_the_same_cells(self, tmp_path: Path) -> None:
        """The property that makes the divergence safe to say yes to. If one
        cell were ever a different colour, the colleague with the PNG and the
        person at the screen would be reading different figures and neither
        would know."""
        from targetring.figure import assert_renders_agree
        _, audit = self._render(tmp_path, "audit")
        _, web = self._render(tmp_path, "web")
        assert_renders_agree(audit, web)

    def test_a_single_recoloured_cell_is_caught(self, tmp_path: Path) -> None:
        """The check earns its place only if it would fail. One hex digit."""
        from targetring.figure import assert_renders_agree
        _, audit = self._render(tmp_path, "audit")
        _, web = self._render(tmp_path, "web")
        body = web.read_text()
        first = re.search(r'<g id="cell\|[^"]+">.*?style="fill: (#[0-9a-f]{6})',
                          body, re.S)
        tampered = tmp_path / "tampered.svg"
        tampered.write_text(body.replace(first.group(1), "#123456", 1))
        with pytest.raises(AssertionError, match="different colour"):
            assert_renders_agree(audit, tampered)

    def test_the_app_render_is_leaner_and_the_export_is_fuller(
            self, tmp_path: Path) -> None:
        """Opposite directions, which is the point. The app drops only what the
        page itself already carries; the export gains the one fact a downloaded
        PNG could never state — that the invariants were checked and held."""
        summary = "INVARIANTS 7/7 PASS · every check held"
        _, audit = self._render(tmp_path, "audit", invariant_summary=summary)
        _, web = self._render(tmp_path, "web", invariant_summary=summary)
        a, w = self._text(audit), self._text(web)

        assert summary.split(" · ")[0] in a, "the export does not stamp its invariants"
        assert "Mouse-phenotype ring" in a, "the export dropped the caveat footer"
        assert "HBB" in a, "the export does not name the gene it left out"

        assert "Mouse-phenotype ring" not in w, "the app render still carries the footer"
        assert summary.split(" · ")[0] not in w, "the app has an Invariants tab"
        assert len(w) < len(a), (len(w), len(a))
        # And it points at where the PAGE keeps them, not at a caption file a
        # reader of a web page has never been handed.
        assert "Drop ledger tab" in w and "What this is not tab" in w
        assert "the caption" not in w
        # The canvas is 1.8 in shorter, which is what the app gets out of it:
        # the same circle (it is width-bound on both) filling more of the screen
        # at a given page width.
        from targetring.figure import AUDIT, WEB
        assert WEB.figsize[1] < AUDIT.figsize[1]
        assert _viewbox(web)[1] < _viewbox(audit)[1]

    @pytest.mark.parametrize("layout", ["audit", "web", "slide"])
    def test_no_render_may_drop_the_irreducible_minimum(
            self, tmp_path: Path, layout: str) -> None:
        """Puck's condition on the divergence: *"the figure alone must still not
        lie."* Someone will have the circle and no legend file, no page around
        it and nobody to ask."""
        from targetring.figure import COMPARABILITY_CLAIM
        _, svg = self._render(tmp_path, layout)
        t = self._text(svg)
        assert "targetring" in t, "what it is"
        assert COMPARABILITY_CLAIM in t, "that colours do not carry between rings"
        assert "15 genes in" in t and "14 rendered" in t, "submitted vs rendered"

    def test_a_filtered_render_says_so_on_every_layout(self, tmp_path: Path) -> None:
        note = "literature >= 2 removed 3 genes"
        for layout in ("audit", "web", "slide"):
            _, svg = self._render(tmp_path, layout, filter_notes=[note])
            assert "FILTERED" in self._text(svg), layout

    def test_the_minimum_check_would_catch_a_render_that_dropped_one(self) -> None:
        from targetring.figure import assert_figure_states_the_minimum as chk
        full = "targetring v2\nEACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS\n" \
               "15 genes in, 14 rendered"
        chk(full, 15, 14, [], "test")
        with pytest.raises(AssertionError, match="colours do not carry|own scale"):
            chk(full.replace("EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS", ""),
                15, 14, [], "test")
        with pytest.raises(AssertionError, match="how many genes were submitted"):
            chk(full, 99, 14, [], "test")
        with pytest.raises(AssertionError, match="threshold filters"):
            chk(full, 15, 14, ["literature >= 2"], "test")


class TestV30TheLegendAsItsOwnImage:
    """v3.0. "circle on one slide, legend on another, sized independently"."""

    def _legend(self, tmp_path: Path, **kw):
        from targetring.figure import render_legend
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / "legend.svg"
        kw.setdefault("run_id", "run T1")
        kw.setdefault("ordering_note", ORDER_NOTE)
        render_legend(cells_by_ring=spec, region="Liver", out_svg=out,
                      input_count=15, rendered_count=14, **kw)
        return out

    @staticmethod
    def _text(svg: Path) -> str:
        return re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in
            ET.parse(svg).getroot().iter("{http://www.w3.org/2000/svg}text")))

    def test_it_carries_every_ring_in_the_radial_order(self, tmp_path: Path) -> None:
        """Two columns of six, numbered 1..12 — not 1..6 twice. A ring number
        that does not match the radial order is a wrong figure, and the first
        draft of the second column restarted at 1."""
        from targetring.figure import RING_SPECS
        t = self._text(self._legend(tmp_path))
        for i, spec in enumerate(RING_SPECS, start=1):
            assert f"{i}. {spec.label.split(' — ')[0][:18]}" in t, (i, spec.key)
        assert t.count("1. logFC") == 1

    def test_it_is_16_by_9_so_it_is_a_slide(self, tmp_path: Path) -> None:
        """pycirclize sets `savefig.bbox='tight'`; without `SAVE_RC` the legend
        saved at 16.44 x 8.94 in — 1.84:1, letterboxed on the slide it exists
        for."""
        w, h = _viewbox(self._legend(tmp_path))
        assert abs(w / h - 16 / 9) < 0.01, f"{w / h:.3f}"

    def test_it_names_its_run_and_says_why_that_matters(self, tmp_path: Path) -> None:
        """Puck's condition: the figure and its legend must be impossible to
        mismatch. Nine of the twelve ranges move with the submitted set, so
        last week's legend would misread every cell on those nine and look
        entirely normal doing it."""
        t = self._text(self._legend(tmp_path, run_id="run V30-ABC"))
        assert "run V30-ABC" in t
        assert "true only of the run named above" in t
        assert "check the run id" in t

    def test_a_figure_and_its_legend_name_the_same_run(self, tmp_path: Path) -> None:
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        fig_svg = tmp_path / "f.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Liver", out_svg=fig_svg, input_count=15, rendered_count=14,
               ledger_counts={}, ordering_note=ORDER_NOTE, layout="audit",
               provenance="run V30-ABC · deadbeef")
        leg = self._legend(tmp_path, run_id="run V30-ABC",
                           provenance="run V30-ABC · deadbeef")
        for svg in (fig_svg, leg):
            assert "run V30-ABC" in self._text(svg), svg.name

    def test_it_carries_the_caveats_and_the_source_links(self, tmp_path: Path) -> None:
        """Everything a reader needs to decode a cell, and nothing that needs
        the circle to make sense."""
        from targetring import sources
        svg = self._legend(tmp_path)
        t, body = self._text(svg), svg.read_text()
        assert "Mouse-phenotype ring" in t, "the caveat block is not on the legend"
        assert "no data — never measured here" in t, "the cell states are not on it"
        hrefs = set(re.findall(r'<a xlink:href="([^"]+)"', body))
        assert hrefs == {d.url for d in sources.RING_SOURCE_DOCS.values()}

    def test_the_cell_state_box_does_not_print_over_the_scales(
            self, tmp_path: Path) -> None:
        """The legend canvas has no circle, so `assert_no_text_over_the_rings`
        does not apply to it and the first draft printed the box straight across
        the first scale column with nothing objecting. The obstacle here is the
        box, so the box is what is checked."""
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
        from targetring.figure import assert_legend_panels_do_not_overlap

        self._legend(tmp_path)  # the real one must pass, and does
        fig = plt.figure(figsize=(4, 3))
        box = fig.legend(handles=[Patch(facecolor="#fff", label="x" * 60)],
                         loc="upper left", bbox_to_anchor=(0.05, 0.95))
        clash = fig.text(0.10, 0.90, "a scale row")
        try:
            with pytest.raises(AssertionError, match="over the legend's own scales"):
                assert_legend_panels_do_not_overlap(fig, box, [clash])
        finally:
            plt.close(fig)


class TestV30TheArtefactSet:
    """Which file is which. The export and the app render may differ; which one
    a name refers to may not."""

    def test_every_artefact_is_the_layout_its_name_promises(
            self, tmp_path: Path) -> None:
        """Written for a real bug: the web render was asked for with `png=None`,
        which already meant "the default path, figure.png", so the LEAN render
        overwrote the export's PNG. `figure.svg` had the caveat footer and the
        invariant stamp and `figure.png` did not, and comparing the two SVGs
        passed because the SVGs were fine."""
        from targetring.figure import (AUDIT, SLIDE, WEB,
                                       assert_artefacts_are_their_own_layout,
                                       render_legend)
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        common = dict(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                      region="Liver", input_count=14, rendered_count=14,
                      ledger_counts={}, ordering_note=ORDER_NOTE,
                      provenance="run T1 · abc")
        render(out_svg=tmp_path / "figure.svg",
               out_png=tmp_path / "figure.png", layout="audit", **common)
        render(out_svg=tmp_path / "figure-web.svg", out_png=None,
               layout="web", **common)
        render(out_svg=tmp_path / "figure-slide.svg",
               out_png=tmp_path / "figure-slide.png", layout="slide", **common)
        render_legend(cells_by_ring=spec, region="Liver",
                      out_svg=tmp_path / "legend.svg",
                      out_png=tmp_path / "legend.png", run_id="run T1",
                      ordering_note=ORDER_NOTE, input_count=14, rendered_count=14)
        assert_artefacts_are_their_own_layout(tmp_path)

        # And it fails when a file is the wrong render, which is the bug.
        render(out_svg=tmp_path / "spare.svg", out_png=tmp_path / "figure.png",
               layout="web", **common)
        with pytest.raises(AssertionError, match="figure.png"):
            assert_artefacts_are_their_own_layout(tmp_path)
        assert AUDIT.figsize != WEB.figsize != SLIDE.figsize

    def test_the_lean_render_is_not_in_the_download_set(self) -> None:
        """A file that drops the caveat footer must never be the one somebody
        sends to a colleague. It is served, because the page fetches it; it is
        not offered."""
        html = (Path(__file__).resolve().parent.parent / "targetring" / "static"
                / "index.html").read_text()
        js = (Path(__file__).resolve().parent.parent / "targetring" / "static"
              / "app.js").read_text()
        assert "figure-web" not in html
        assert 'dl["web' not in js and '"dl-web"' not in js
        # The page inlines it, though.
        assert "b.web_url" in js

    def test_the_download_row_is_grouped_not_a_wall_of_buttons(self) -> None:
        """Puck: "make the download buttons make sense with the new set rather
        than accumulating one per artefact ... do not leave it a row of nine."
        Ten artefacts are offered behind three controls."""
        html = (Path(__file__).resolve().parent.parent / "targetring" / "static"
                / "index.html").read_text()
        top_level = re.findall(r'<summary class="btn">([^<]+)</summary>', html)
        assert len(top_level) == 3, top_level
        offered = re.findall(r'<a id="dl-[a-z-]+"', html)
        assert len(offered) >= 9, offered
