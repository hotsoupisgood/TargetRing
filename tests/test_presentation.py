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
        # And where it points, it points at the PAGE -- never at a caption file a
        # reader of a web page has never been handed.
        #
        # v3.2 / round 2 A4: the `What this is not` pointer used to be a clause on
        # this render's own caveat line, and that line is what had to leave the
        # canvas for the circle to fit a 900 px window. It is on the page now, in
        # `#figure-caveats`, directly under the circle and at 13 px rather than
        # 15.05 -- checked in `TestUXv3Round2ThePageAReaderWorksIn`. What is
        # unchanged, and is the half of this assertion that was ever load-bearing,
        # is that the app render does not send its reader to a file.
        assert "Drop ledger tab" in w, (
            "the app render's own pointer to where the dropped genes are")
        assert "the caption" not in w
        from targetring.figure import caveats_for_the_page
        assert any("What this is not tab" in c["text"] for c in
                   caveats_for_the_page(ORDER_NOTE, [], "")[1]), (
            "the clauses this render handed to the page no longer point at the "
            "tab that holds the rest")
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


STATIC = Path(__file__).resolve().parent.parent / "targetring" / "static"


def _html() -> str:
    return (STATIC / "index.html").read_text()


def _js() -> str:
    return (STATIC / "app.js").read_text()


def _inside_a_disclosure(html: str, element_id: str) -> bool:
    """Is `#element_id` anywhere inside a `<details>`?

    Parsed rather than grepped: "is this behind a click" is a question about
    ancestry, and a regex over the source answers a different one. The page is
    well-formed enough for the stdlib parser, and it stays that way because
    this test would fail loudly if it stopped being.
    """
    from html.parser import HTMLParser

    class Finder(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.depth = 0
            self.stack: list[str] = []
            self.hit: bool | None = None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if d.get("id") == element_id and self.hit is None:
                self.hit = "details" in self.stack
            if tag not in ("br", "img", "input", "meta", "link", "hr"):
                self.stack.append(tag)

        def handle_startendtag(self, tag, attrs):
            d = dict(attrs)
            if d.get("id") == element_id and self.hit is None:
                self.hit = "details" in self.stack

        def handle_endtag(self, tag):
            if tag in self.stack:
                while self.stack.pop() != tag:
                    pass

    f = Finder()
    f.feed(html)
    assert f.hit is not None, f"#{element_id} is not on the page at all"
    return f.hit


class TestUXv3TheTwoThingsThatSurviveWhateverAnimatesAway:
    """The rule `docs/reviews/UX-v3-rounds.md` states once and holds through all
    three rounds, and the sentence that matters in it is the last one:
    *"Both are checked by an assertion, not by care."*

    Puck asked for animations that hide non-essential information, and §v3.0
    made simplifying the app screen free — nothing hidden in the browser reaches
    any exported artefact. Two things are exempt:

      1. the four cell states stay reachable, with no dead end;
      2. "each ring has its own scale; colours do not compare across rings" is
         never behind a click.

    A round-1 fix that collapsed the scale panel and left those two inside it
    would pass every other test in this file. These are what stop round 2 or
    round 3 doing that by accident while tidying the page — which is exactly the
    kind of change a later round makes.
    """

    def test_the_four_cell_states_are_not_behind_a_disclosure(self) -> None:
        """Rule 1. `#cell-states` has no toggle of its own and is not inside
        anybody else's, so there is no state of the page in which the states are
        hidden. That is stronger than "the control that reopens it is visible",
        which is the weaker option the rule also allows."""
        html = _html()
        assert not _inside_a_disclosure(html, "cell-states"), (
            "the four cell states are inside a <details>: the rule allows a "
            "collapse only if the control that reopens it is visible and says "
            "what it opens, and the simpler way to hold the rule is not to "
            "collapse them at all")

    def test_all_four_cell_states_are_actually_drawn_into_it(self) -> None:
        """Reachable is not the same as present. The states come from
        `STATE_STYLE`, so a state added to the figure appears in the panel
        without anybody remembering to add it — and the count is asserted here
        so that stays true."""
        from targetring.figure import STATE_STYLE
        js = _js()
        # Three non-value states live in `STATE_STYLE`; `value` is the fourth
        # and is the dashed empty box on every render, because any filled swatch
        # would say the thing v2.6 exists to stop saying -- that one colour
        # means "a value". The panel draws it the same way.
        assert sorted(STATE_STYLE) == ["failed", "no_data", "not_applicable"]
        assert "s.states.map(" in js, (
            "the panel must render every state the figure defines, not a list "
            "written out a second time in JavaScript")
        assert "border-style:dashed" in js, "the fourth state, `value`"
        assert '$("#cell-states").innerHTML' in js
        assert "the four cell states" in js

    def test_the_comparability_sentence_clears_the_floor_on_the_app_render(
            self, tmp_path: Path) -> None:
        """Rule 2, and v3.2 / round 2 A6 is where this test changed shape.

        Round 1 held rule 2 by asking whether an amber HTML band existed and
        whether it preceded the figure row. Round 2's A6 found that the band and
        the render's own banner were BOTH there, eight pixels apart, printing the
        same 47 characters — `UX-v1-mvp.md`'s noise finding restored — and that
        only one of the two fixes had been needed: round 1's objection to the
        in-SVG copy was that it was *"178 x 7 CSS px in the top-right corner"*,
        an objection to its SIZE, which the canvas change answered by itself.

        So the band went and this assertion got STRONGER rather than weaker. It
        no longer asks whether an element is in the markup — a question a grep
        answers and a reader cannot — it measures the sentence as it is actually
        drawn, at the 1040 px the app gives the figure, against this review's own
        12.0 CSS px floor. That is what rule 2 was always about: "present at
        6 px in a corner" was never compliance.
        """
        from targetring.figure import COMPARABILITY_CLAIM, WEB, WEB_BANNER_PT
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / "web.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=14, rendered_count=14,
               ordering_note=ORDER_NOTE, layout="web", ledger_counts={},
               provenance="run T1 · abc123")
        text = " ".join("".join(el.itertext()) for el in ET.parse(out).getroot()
                        .iter("{http://www.w3.org/2000/svg}text"))
        assert COMPARABILITY_CLAIM in re.sub(r"\s+", " ", text), (
            "the app render does not carry the sentence at all, and it is the "
            "only copy of it on the screen since A6 removed the HTML band")
        # 1040 px is the panel the app gives the figure once the 330 px decode
        # panel and the 18 px gap are taken; `ux_v3_measure.py` carries the same
        # constant and round 1's re-review is where it was corrected from 1388.
        as_seen = WEB_BANNER_PT * (1040.0 / (WEB.figsize[0] * 72.0))
        assert as_seen >= 12.0, (
            f"the comparability sentence is drawn at {as_seen:.2f} CSS px on a "
            f"1440x900 laptop, under this review's 12.0 px floor. Rule 2 says "
            f"it is never behind a click, and round 1 established that 'present "
            f"at 6 px in a corner' is the same outcome by another route")

    def test_the_sentence_is_not_printed_twice_on_the_same_screen(self) -> None:
        """A6 itself, held by a test so round 3 cannot restore the band while
        tidying. The render draws it; the page must not draw it again."""
        html, css, js = _html(), (STATIC / "app.css").read_text(), _js()
        assert 'id="comparability"' not in html, (
            "the amber HTML band is back: it and the render's own banner are the "
            "same 47 characters eight pixels apart (round 2 A6)")
        assert "s.comparability" not in js, (
            "the page is drawing the comparability sentence again as well as the "
            "render")
        # Comments stripped first: this file explains at length why the band went,
        # and a rule and a paragraph about a rule are different things.
        rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        assert ".comparability" not in rules

    def test_the_one_thing_that_may_collapse_says_what_it_opens(self) -> None:
        """No dead end. `#scales` is the reference a reader consults for a
        particular cell, so it is the thing that earns an animation — and its
        summary names the contents and the count in the closed state."""
        html = _html()
        assert _inside_a_disclosure(html, "scale-rows"), (
            "the scale panel is the one part that is allowed to animate away")
        summary = re.search(r'<details id="scales"[^>]*>\s*<summary>(.*?)</summary>',
                            html, re.S)
        assert summary, "the collapsible panel has no summary at all"
        words = re.sub(r"<[^>]+>", " ", summary.group(1))
        assert "scale" in words.lower(), words
        assert re.search(r"\d", words), (
            "the closed state must say how many scales are behind it", words)

    def test_the_panel_animates_and_respects_reduced_motion(self) -> None:
        """Puck asked for the animation; an animation nobody can turn off is a
        different defect from the one it fixes."""
        css = (STATIC / "app.css").read_text()
        assert "@keyframes reveal" in css
        assert "prefers-reduced-motion" in css
        block = css[css.index("prefers-reduced-motion"):]
        assert "animation: none" in block


class TestUXv3A2TheScalesLeaveTheSvgAndStayInTheApp:
    """A2's fix in one sentence: the twelve scales stopped being 5.8 px of
    vector text and became 13 px of HTML. What must not follow from that is the
    export losing them — `UX-v1-mvp.md` A5 is binding on both exported renders.
    """

    def _spec(self):
        return _full_ring_spec([f"GENE{i:03d}" for i in range(14)])

    @pytest.mark.parametrize("layout,in_svg", [("audit", True), ("slide", True),
                                               ("web", False)])
    def test_only_the_app_render_drops_the_column(self, layout: str,
                                                  in_svg: bool) -> None:
        from targetring.figure import LAYOUTS
        assert LAYOUTS[layout].scales_in_svg is in_svg

    def test_the_flag_that_decides_it_is_its_own_flag(self) -> None:
        """`slide` has `stamp_size_small=False` and draws a full scale column;
        `web` has `stamp_size_small=False` and draws none. The two questions
        were one field for one afternoon during this fix, and the slide render
        lost its column and printed a banner across its own spoke labels. The
        names are now what they decide, and this is the test that says so."""
        from targetring.figure import SLIDE, WEB
        assert SLIDE.stamp_size_small is False and SLIDE.scales_in_svg is True
        assert WEB.stamp_size_small is False and WEB.scales_in_svg is False

    def test_the_panel_is_built_from_the_renders_own_ramps_and_ticks(self) -> None:
        """A panel whose ramp or ticks came from a second implementation could
        disagree with the figure beside it — which is the failure mode §v3.0
        spent structure on for the detachable legend, arriving on the attached
        one."""
        from targetring.figure import scale_rows
        rows = scale_rows(self._spec())
        assert [r["n"] for r in rows] == list(range(1, len(rows) + 1)), (
            "a ring number that does not match the radial order is a wrong "
            "figure — the bug v3.0 found in the two-column legend")
        for r in rows:
            assert r["colours"], r["key"]
            assert r["units"], r["key"]
            assert all(0.0 <= t["at"] <= 1.0 for t in r["ticks"]), r["key"]
            assert r["source_text"], r["key"]

    def test_the_categorical_ring_is_named_in_the_panel_not_numbered(self) -> None:
        """COUNCIL V12, arriving on the HTML panel. Ring 8's three colours are
        off every ramp, so they need naming — and the panel's first version read
        its numeric tick labels instead and printed `-1 / 0 / 1` beside three
        colours that are not positions on anything."""
        from targetring.figure import scale_rows
        act = next(r for r in scale_rows(self._spec())
                   if r["key"] == "clinical_activity")
        assert len(act["categories"]) == len(act["colours"]) == 3, act
        for name in act["categories"]:
            assert name and not re.fullmatch(r"-?[\d.]+", name), (
                "a state is printed as its numeric position", act["categories"])
        js = _js()
        assert "act.categories[i]" in js
        assert "act.ticks[i]" not in js, (
            "the panel is back to reading the ring's numeric ticks")

    def test_every_ring_reports_whether_it_is_categorical(self) -> None:
        """And the non-categorical eleven carry an empty list rather than a
        made-up name, which is AGENTS.md rule 1 in a JSON field."""
        from targetring.figure import scale_rows
        rows = scale_rows(self._spec())
        cats = [r for r in rows if r["categories"]]
        assert [r["key"] for r in cats] == ["clinical_activity"], [
            r["key"] for r in cats]

    def test_the_export_still_carries_a_scale_for_every_ring(self,
                                                             tmp_path: Path) -> None:
        """A5, unchanged, on the two files that leave the app."""
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        for layout in ("audit", "slide"):
            out = tmp_path / f"{layout}.svg"
            render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                   region="Liver", out_svg=out, input_count=14,
                   rendered_count=14, ordering_note=ORDER_NOTE, layout=layout,
                   provenance="run T1 · abc", ledger_counts={})
            text = re.sub(r"\s+", " ", " ".join(
                "".join(el.itertext()) for el in ET.parse(out).getroot()
                .iter("{http://www.w3.org/2000/svg}text")))
            from targetring.figure import RING_SPECS
            drawn = [s for s in RING_SPECS if s.key in spec]
            assert len(drawn) == 12, len(drawn)
            for s in drawn:
                assert s.wedge_label in text or s.label[:18] in text, (
                    layout, s.key, "has no scale in the exported column")


class TestUXv3A1TextMayNotPrintOverText:
    """A1. Five pairs of strings intersected on `figure-slide.svg`, measured off
    the file, and they had done since v2.6 — *"'Liver' sits on 'targetring v2'…
    Say the word and it is ten minutes."* Nobody said the word, so it shipped
    through v2.7, v2.8, v2.9 and v3.0.

    The reason no check caught it is the interesting part. The build had three
    assertions about text landing where it should not — over the rings, outside
    the canvas, over the legend box — and none about text over *text*.
    `fit_centre_to_the_hole` shrinks until every line's radial reach clears the
    hole, and two lines sharing a y have the same reach they had apart; so did
    `assert_centre_fits_the_hole`. Both were asking whether the block escaped
    the hole, and the defect was entirely inside it.
    """

    def _render(self, tmp_path: Path, layout: str, n: int = 14, **kw):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"a1-{layout}-{n}.svg"
        kw.setdefault("ledger_counts", {"ambiguous": 1})
        kw.setdefault("dropped_names", ["HBB"])
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=n + 1, rendered_count=n,
               ordering_note=ORDER_NOTE, layout=layout,
               provenance="run T1 · abc123", **kw)
        return out

    @pytest.mark.parametrize("layout", ["audit", "web", "slide"])
    @pytest.mark.parametrize("n", [3, 14])
    def test_no_render_prints_a_string_over_another_string(
            self, tmp_path: Path, layout: str, n: int) -> None:
        """Rendering is the assertion: `assert_text_does_not_overprint_text`
        runs inside `render` and raises rather than returning a figure. Three
        genes is in the matrix because the footer is user-driven — the drop
        categories, the dropped names and the filters all change its height —
        and a constant tuned against a 14-gene run was what put the scale
        column's last line inside it."""
        self._render(tmp_path, layout, n=n)

    @pytest.mark.parametrize("layout", ["audit", "web", "slide"])
    def test_it_holds_with_filters_active_too(self, tmp_path: Path,
                                              layout: str) -> None:
        self._render(tmp_path, layout,
                     filter_notes=["literature >= 2 removed 3 genes"])

    def test_the_check_would_actually_fail(self) -> None:
        """The negative. A check that has never refused anything is a comment.
        Two strings at the same point, which is precisely what `Lung` and
        `15 genes in, 14 rendered` were on the slide."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from targetring.figure import assert_text_does_not_overprint_text

        fig = plt.figure(figsize=(6, 4))
        a = fig.text(0.5, 0.5, "Lung", fontsize=14, ha="center")
        b = fig.text(0.5, 0.5, "15 genes in, 14 rendered", fontsize=12,
                     ha="center")
        try:
            with pytest.raises(AssertionError, match="on top of other text"):
                assert_text_does_not_overprint_text(fig, [a, b], where="test")
            # and it passes once they are apart
            b.set_position((0.5, 0.2))
            assert_text_does_not_overprint_text(fig, [a, b], where="test")
        finally:
            plt.close(fig)

    def test_the_centre_block_is_stacked_by_measurement_not_by_constants(
            self, tmp_path: Path) -> None:
        """The fix, rather than its symptom. Nudging three constants would have
        cleared the 1.15 slide and broken on the next `centre_scale`; the block
        is laid out from its own measured line heights, so a longer region name
        or a seventh drop category moves the lines below it instead of landing
        on them."""
        import inspect
        import re as _re
        from targetring.figure import (_draw_centre, _draw_ordering_and_filters,
                                       stack_centre_blocks)
        for fn in (_draw_centre, _draw_ordering_and_filters):
            code = "\n".join(l for l in inspect.getsource(fn).splitlines()
                             if not l.lstrip().startswith("#"))
            ys = set(_re.findall(r"ax\.text\(0\.5,\s*([A-Za-z0-9_.]+)", code))
            assert ys and ys <= {"Y", "y"}, (
                fn.__name__, ys, "a line is still placed at its own constant; "
                "the stack owns vertical position now")
        assert "stack_centre_blocks" in inspect.getsource(
            __import__("targetring.figure", fromlist=["x"]).fit_centre_to_the_hole)
        assert stack_centre_blocks.__doc__

    def test_the_slide_is_the_layout_that_used_to_fail(self, tmp_path: Path) -> None:
        """A1 named the artefact: `centre_scale` is 1.0 on `audit` and `web`
        and 1.15 on `slide`, so the type grew 15% and the spacing did not. The
        regression test is that the slide renders at the scale factor that broke
        it."""
        from targetring.figure import SLIDE
        assert SLIDE.centre_scale == 1.15
        self._render(tmp_path, "slide", n=14)


class TestUXv3Round2ThePageAReaderWorksIn:
    """UX v3 round 2. Round 1 measured the artefacts; round 2 measured the page —
    where things are, how far apart, and how much of it is visible at once —
    and found three category-A defects, two of which round 1 had caused.

    Each of these is the assertion that would have caught its finding before a
    screenshot did, which is the only reason three rounds are worth running.
    """

    def _web(self, tmp_path: Path, n: int = 14, **kw):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"web-{n}.svg"
        kw.setdefault("ledger_counts", {})
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=n, rendered_count=n,
               ordering_note=ORDER_NOTE, layout="web",
               provenance="run T1 · abc123", **kw)
        return out

    # --- A4: the figure fits one laptop viewport -------------------------------

    def test_the_app_figure_fits_a_1440x900_viewport(self) -> None:
        """A4. Round 1 bought type size by trading a 19.5 x 12.2 in canvas for a
        nearly square 9.6 x 10.6, and at a fixed panel width an aspect change IS
        a height change: the figure came out 1,148 px tall in a 900 px window, so
        there was no scroll position at which the circle was whole.

        The floor and the ceiling are both asserted. A canvas that fits but is
        tiny would pass a height check and fail the review, so the circle has a
        minimum too.
        """
        from targetring.figure import WEB
        scale = 1040.0 / (WEB.figsize[0] * 72.0)
        height_px = WEB.figsize[1] * 72.0 * scale
        assert height_px <= 900.0, (
            f"the app render is {height_px:.0f} px tall on a 1440x900 laptop; "
            f"round 2 A4 is that there is then no scroll position at which the "
            f"circle is whole")
        circle_px = min(WEB.axes_box[2] * WEB.figsize[0],
                        WEB.axes_box[3] * WEB.figsize[1]) * 72.0 * scale
        assert circle_px >= 600.0, (
            f"the circle is {circle_px:.0f} px across. A4's argument was that "
            f"height was nearly free -- v3.0 drew it at 659 px -- so a canvas "
            f"that fits the window by shrinking the figure has missed the point")

    def test_type_size_did_not_pay_for_the_height(self) -> None:
        """The arithmetic A4 rests on: `scale = panel_px / (width_in * 72)` has
        no height term in it, so shortening the canvas costs no legibility at
        all. Held as a test because it is the reason this fix was allowed to be
        a canvas change rather than a compromise."""
        from targetring.figure import WEB
        scale = 1040.0 / (WEB.figsize[0] * 72.0)
        assert WEB.figsize[0] == 9.6, "the width is what sets the type size"
        assert WEB.label_size * scale >= 12.0, (
            f"the gene labels are {WEB.label_size * scale:.2f} CSS px, under the "
            f"12.0 floor -- round 1's whole A2 fix was buying this")

    def test_the_render_still_states_the_minimum_with_the_caveats_on_the_page(
            self, tmp_path: Path) -> None:
        """A4's fix moved four caveat clauses off this canvas, and the guard that
        makes such a move safe is `assert_figure_states_the_minimum`. It runs
        inside `render`, so this test is that the web render draws at all -- but
        FILTERED is the clause that had to stay, so it is checked by name on the
        drawn text rather than trusted to the move."""
        out = self._web(tmp_path, 14,
                        filter_notes=["logfc >= 1.0 (kept genes with no value)"])
        text = re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in ET.parse(out).getroot()
            .iter("{http://www.w3.org/2000/svg}text")))
        assert "FILTERED" in text, (
            "the app render no longer says it is filtered. That clause is the "
            "difference between this circle being the submitted set and being a "
            "subset of it, and it is the one A4 kept on the canvas")
        assert "logfc >= 1.0" in text

    def test_the_clauses_that_moved_are_handed_over_rather_than_dropped(
            self) -> None:
        """`caveats_for_the_page` returns both halves, and the page half is
        non-empty and carries the safety caveat and the spoke order. A fix that
        deleted them instead of moving them would be this review producing the
        defect it exists to find."""
        from targetring.figure import SAFETY_RING_CAVEAT, caveats_for_the_page
        keep, page = caveats_for_the_page(ORDER_NOTE, [], "a FIXED 26° gap")
        assert keep == "", "nothing to say about filters on an unfiltered run"
        assert {c["kind"] for c in page} <= {"warn", "how"}, page
        body = " ".join(c["text"] for c in page)
        assert SAFETY_RING_CAVEAT in body
        assert ORDER_NOTE in body
        assert "26°" in body
        keep, page = caveats_for_the_page(ORDER_NOTE, ["logfc >= 1.0"], "")
        assert keep.startswith("FILTERED") and "logfc >= 1.0" in keep

    def test_the_page_draws_the_handed_over_clauses_and_not_behind_a_click(
            self) -> None:
        html, js = _html(), _js()
        assert 'id="figure-caveats"' in html
        assert not _inside_a_disclosure(html, "figure-caveats"), (
            "a caveat behind a click is a caveat nobody read")
        assert html.index('id="figure"') < html.index('id="figure-caveats"'), (
            "the caveats belong under the circle they are about")
        assert "s.figure_caveats" in js, (
            "the page must draw what the render handed it, not a second copy of "
            "the same sentences written out in JavaScript")

    def test_the_wedge_gap_survives_even_where_its_names_do_not(self) -> None:
        """A4's other half. The 26° gap is what keeps 12 o'clock clear of spoke
        labels, which is what lets the comparability banner be drawn large in the
        corner at all -- at `wedge_deg=0` a spoke label moves to the top and the
        banner lands 20.7 px inside it. So the gap stays on the app render and
        only the twelve names leave, to the panel that draws them at 13 px."""
        from targetring.figure import AUDIT, SLIDE, WEB
        assert WEB.wedge_deg == 26.0
        assert WEB.wedge_labels_in_svg is False
        assert AUDIT.wedge_labels_in_svg is True
        # The slide has no gap at all, so the flag is moot there and must not be
        # the thing that decides it.
        assert SLIDE.wedge_deg == 0.0

    def test_the_render_does_not_claim_a_ring_name_gap_it_did_not_fill(
            self, tmp_path: Path) -> None:
        """COUNCIL V5, one word in. The gap is reserved on the app render and
        holds no names, so a figure that calls it "the ring-name gap" or says the
        names are "inside the circle" is describing a geometry it does not have.
        `wedge_note` and `clock_phrase` derive the wording from the layout."""
        from targetring.figure import AUDIT, WEB, clock_phrase, wedge_note
        assert "reserved gap" in clock_phrase(WEB)
        assert "ring-name gap" in clock_phrase(AUDIT)
        web = wedge_note(WEB, 14, 2.0, short=True)
        assert "reserved at 12 o'clock" in web
        assert "inside the circle" not in web
        assert "panel beside this figure" in web
        # and it still states what the gap cost, which was Puck's condition on
        # the wedge existing at all.
        assert "°" in web and "full circle" in web
        out = self._web(tmp_path, 14)
        names = [el.get("{http://www.w3.org/2000/svg}id") or el.get("id")
                 for el in ET.parse(out).getroot().iter()]
        assert not [n for n in names if n and n.startswith("ringname|")], (
            "the twelve wedge ring names are back in the app SVG at 9.07 px, "
            "which is round 1's largest under-floor exception restored")

    def test_the_export_keeps_its_ring_names_inside_the_circle(
            self, tmp_path: Path) -> None:
        """The other side of the same flag: the audit render is a file that
        leaves the app and has no panel beside it."""
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / "audit.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=14, rendered_count=14,
               ordering_note=ORDER_NOTE, layout="audit", ledger_counts={},
               provenance="run T1 · abc123")
        ids = [el.get("id") for el in ET.parse(out).getroot().iter()]
        assert len([i for i in ids if i and i.startswith("ringname|")]) == 12, (
            "the export lost the ring names; UX-v1-mvp.md A5 is binding on it")

    # --- A5: the page and the figure cannot disagree about the version ---------

    def test_the_page_and_the_figure_cannot_disagree_about_the_version(
            self) -> None:
        """A5. `index.html` said `targetring v1` in its `<title>` and its `<h1>`
        while every artefact drew `targetring v2`, so one screenshot carried both
        — the header at the top and the middle of the circle 1,400 px below it.
        COUNCIL V5's defect in the chrome: an artefact making a false claim about
        itself. It survived v2.1 through v3.1 because nothing asserted the two
        agreed, so this is that assertion rather than two corrected words."""
        from targetring.figure import FIGURE_TITLE, TOOL_NAME, TOOL_VERSION
        html = _html()
        assert FIGURE_TITLE == f"{TOOL_NAME} {TOOL_VERSION}"
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        assert title and title.group(1).strip() == FIGURE_TITLE, (
            f"<title> says {title and title.group(1).strip()!r}; the figure "
            f"draws {FIGURE_TITLE!r}")
        h1 = re.search(r"<h1>(.*?)</h1>", html, re.S)
        assert h1, "the page has no <h1>"
        words = " ".join(re.sub(r"<[^>]+>", " ", h1.group(1)).split())
        assert words == FIGURE_TITLE, (
            f"the <h1> reads {words!r} and the circle inside it reads "
            f"{FIGURE_TITLE!r}")

    def test_every_artefact_reads_the_version_from_the_one_constant(self) -> None:
        """The literal was in `_draw_centre` and again in `render_legend` and a
        third time in the page. Two of the three agreed, which is how the third
        survived eleven releases."""
        import inspect
        from targetring import figure as F
        for fn in (F._draw_centre, F.render_legend):
            src = inspect.getsource(fn)
            assert "targetring v" not in src, (
                f"{fn.__name__} still hard-codes a version string")
            assert "FIGURE_TITLE" in src

    # --- B7: the controls and the result on one screen -------------------------

    def test_the_form_collapses_once_there_is_a_figure(self) -> None:
        """B7. The form is 1,053 px of a 2,487 px document, so the figure began
        at y = 1,279 and changing one threshold meant scrolling past four
        sections of help text and back. It collapses after the first render —
        after, not before, because until you have a figure the help is the page.
        """
        html, js = _html(), _js()
        assert 'id="controls"' in html
        assert _inside_a_disclosure(html, "filterable"), (
            "the filter help is not inside the collapse, so B7's 1,053 px are "
            "still between the reader and their figure")
        assert '<details id="controls" open>' in html, (
            "it must start open: a first-time reader meets the help, not a "
            "closed box")
        assert re.search(r'\$\("#controls"\)[\s\S]{0,80}open = false', js), (
            "nothing closes it after a run, so B7 is unfixed at the moment it "
            "matters")

    def test_the_collapsed_form_is_not_a_dead_end(self) -> None:
        """The same contract `#scales` has, because this review's rule about dead
        ends is about the page and not about one panel: the control that reopens
        it is visible, says what it opens, and says how much is behind it."""
        html = _html()
        m = re.search(r'<details id="controls"[^>]*>\s*<summary>(.*?)</summary>',
                      html, re.S)
        assert m, "the collapse has no summary at all"
        words = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
        assert re.search(r"\d", words), (words, "no count in the closed state")
        for word in ("table", "region", "order", "filter"):
            assert word in words.lower(), (word, words)

    def test_the_button_that_draws_the_figure_stays_outside_the_collapse(
            self) -> None:
        """Button placement, which Puck named. The help text is what collapses;
        the control that acts on it is not part of it."""
        html = _html()
        assert not _inside_a_disclosure(html, "go")
        assert not _inside_a_disclosure(html, "status")

    def test_neither_survivor_was_swallowed_by_the_new_collapse(self) -> None:
        """The failure mode a third `<details>` on this page introduces, named
        and checked rather than noticed later."""
        html = _html()
        assert not _inside_a_disclosure(html, "cell-states")
        assert not _inside_a_disclosure(html, "figure")
        assert not _inside_a_disclosure(html, "counts")


class TestUXv3Round3TheProjectedArtefactAndTheDrawer:
    """UX v3 round 3, the last round. Rounds 1 and 2 took the app; this takes the
    two surfaces neither of them measured — the artefact that gets projected and
    the drawer — and the strings that were deferred twice on purpose.
    """

    SLIDE_SCALE = 10.0 / 12.0   # a 16 in canvas on a full 13.33 in slide

    # --- A7: rule 2 on the artefact that gets projected -----------------------

    def test_the_slide_states_the_claim_at_the_size_a_slide_is_read_at(
            self, tmp_path: Path) -> None:
        """A7. The rule that holds for all three rounds says the comparability
        claim is never behind a click, and round 1 established that *"present at
        6 px in a corner"* is the same outcome by another route. It was 5.86 pt
        on `figure-slide.svg` — **4.88 pt at a full slide, 2.44 at half** —
        because it was the scale column's header, sized at
        `scale_title_size * 0.95` and then fitted DOWN with the rows. The one
        sentence this review protects everywhere was sized by how much unrelated
        text happened to be beside it.
        """
        from targetring.figure import SLIDE
        assert SLIDE.claim_size is not None, (
            "the slide's claim is back on the fitted row titles")
        as_seen = SLIDE.claim_size * self.SLIDE_SCALE
        assert as_seen >= 10.0, (
            f"the comparability claim is {as_seen:.2f} pt at a full slide, under "
            f"the review's 10.0 pt floor for a projected artefact")

    def test_the_slide_gene_labels_clear_the_floor_at_a_full_slide(self) -> None:
        """B11, round 2's. 11.5 pt was 9.58 pt as seen — missing by 0.42 pt on
        the artefact `UX-v1-mvp.md` A4 created to solve exactly this."""
        from targetring.figure import SLIDE
        as_seen = SLIDE.label_size * self.SLIDE_SCALE
        assert as_seen >= 10.0, f"{as_seen:.2f} pt at a full slide"
        assert as_seen > 10.0, (
            "12.0 pt lands on 10.00 exactly and a figure one hundredth of a "
            "point either way of compliant has not met a floor")

    def test_the_claim_is_still_one_run_in_the_slide_svg(self,
                                                         tmp_path: Path) -> None:
        """The reason it is right-anchored rather than wrapped. At 12 pt the
        47-character claim is 535 px wide and the column is 464, so left-aligned
        it ran off a 1600 px canvas; wrapping it would have been the other fix
        and it is the wrong one, because three tests read this sentence back out
        of the SVG in one piece and a reader has to as well."""
        from targetring.figure import COMPARABILITY_CLAIM
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / "slide.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=14, rendered_count=14,
               ordering_note=ORDER_NOTE, layout="slide", ledger_counts={},
               provenance="run V33-A-DELIBERATELY-LONG-RUN-ID · abc123def456")
        runs = [re.sub(r"\s+", " ", "".join(el.itertext())).strip()
                for el in ET.parse(out).getroot()
                .iter("{http://www.w3.org/2000/svg}text")]
        assert any(r.startswith(COMPARABILITY_CLAIM) for r in runs), (
            "the claim is no longer a single unbroken run in the slide SVG")

    def test_the_slide_still_draws_with_a_filter_and_at_three_genes(
            self, tmp_path: Path) -> None:
        """Where A7 and B11 actually cost something. Bigger labels reach further
        down, and with a filter active `slide_line` grows a line upward:
        `assert_no_text_over_the_rings` refused 0.105 and then 0.125 of bottom
        margin at 3 and 7 genes before 0.145 held. §v3.0's own finding, arriving
        one type size later."""
        for n in (3, 7, 14):
            genes = [f"GENE{i:03d}" for i in range(n)]
            spec = _full_ring_spec(genes)
            render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                   region="Lung", out_svg=tmp_path / f"s{n}.svg",
                   input_count=n + 1, rendered_count=n, layout="slide",
                   ordering_note=ORDER_NOTE, ledger_counts={"ambiguous": 1},
                   dropped_names=["HBB"], provenance="run T1 · abc123",
                   filter_notes=["logfc >= 1.0 (kept genes with no value)"])

    def test_the_circle_did_not_pay_for_any_of_it(self) -> None:
        """The slide's radius is width-bound — 0.345 × 16 = 5.52 in against
        0.79 × 9 = 7.11 in of height — so every point of type this round bought
        came out of height the circle was never using. Asserted because the next
        person to adjust these numbers should find out here rather than in a
        screenshot."""
        from targetring.figure import SLIDE
        w = SLIDE.axes_box[2] * SLIDE.figsize[0]
        h = SLIDE.axes_box[3] * SLIDE.figsize[1]
        assert w < h, (w, h, "the slide circle has become height-bound, so the "
                             "bottom margin is now charged to the radius")
        assert abs(w / 2 - 2.76) < 0.01, w / 2

    # --- A8: the drawer -------------------------------------------------------

    def test_the_drawer_sections_collapse_and_say_what_they_hold(self) -> None:
        """A8. 5,621 px, 6.25 viewports, nine headings, four tables and zero
        collapsibles. Round 1 called it B1 and said it should not survive three
        rounds; round 2 called it B8 and handed it here."""
        js = _js()
        assert "function sectionise" in js
        assert re.search(r'\$\("#drawer-body"\)\.innerHTML\s*=\s*sectionise\(', js), (
            "the drawer is rendered without being sectioned")
        assert '<details class="sect"' in js
        assert "DRAWER_OPEN" in js and "sectionCount" in js

    def test_the_three_sections_the_drawer_exists_for_start_open(self) -> None:
        """PLAN §1: *"clicking a gene opens a drawer with its drug pipeline and
        its literature"*. Plus the reader's own measurement, which is what they
        arrived holding."""
        js = _js()
        m = re.search(r"const DRAWER_OPEN = \[(.*?)\];", js, re.S)
        assert m, "the open set is not a named list any more"
        names = re.findall(r'"([^"]+)"', m.group(1))
        assert names == ["your measurement", "drug pipeline", "literature"], names

    def test_the_untrusted_sections_keep_their_caveat_outside_the_collapse(
            self) -> None:
        """The failure mode this fix could have introduced, and the reason it
        did not. A `mouse phenotype lead` collapsed to a bare heading puts
        COUNCIL M4's *"a lead, not a verdict"* one click further from the number
        it qualifies than the number is, and AGENTS.md rule 6 is not a thing to
        hide. Same for PLAN §8's ChEMBL warning."""
        js = _js()
        m = re.search(r"const DRAWER_CAVEATS = \[(.*?)\];", js, re.S)
        assert m, "nothing carries a caveat onto the summary any more"
        block = m.group(1)
        for section in ("mouse phenotype lead", "ChEMBL mechanisms",
                        "safety liabilities"):
            assert f'"{section}"' in block, section
        assert "a lead, not a verdict" in block
        assert "untrusted" in block
        # and the caveat is interpolated into the summary, not into the body
        assert 'class="caveat"' in js
        assert re.search(r"\$\{caveat\}[\s\S]{0,160}</summary>", js), (
            "the caveat is inside the collapse, which is where it must not be")

    def test_every_closed_section_says_how_much_is_behind_it(self) -> None:
        """No dead end — the same contract `#scales` and `#controls` hold."""
        js = _js()
        assert re.search(r'<summary>[\s\S]{0,300}class="count"', js)
        assert "sectionCount" in js
        for word in ("row", "item", "field"):
            assert f'"{word}"' in js, word

    def test_the_close_button_meets_the_target(self) -> None:
        """Round 1's B5, measured at 28 × 39 px. A desktop-only app, so it is
        genuinely minor — and it is the one control on a 5,621 px surface."""
        css = (STATIC / "app.css").read_text()
        block = css[css.index("#drawer-close {"):]
        block = block[:block.index("}")]
        assert "width: 44px" in block and "height: 44px" in block, block

    # --- B12, B13, B14: the strings ------------------------------------------

    def test_no_stylesheet_rule_sets_type_under_the_floor(self) -> None:
        """B12. Sixty-one strings at 11.5 px — round 2's B9 counted 25 in the
        two diagnostic tabs and the drawer holds 36 more that B9 could not see
        because nobody had opened it with an instrument. The floor's own
        justification cites 11.5 px table headers as this build's smallest
        existing thing, which is how they survived two rounds; half a pixel is
        not a trade-off."""
        css = re.sub(r"/\*.*?\*/", "", (STATIC / "app.css").read_text(), flags=re.S)
        small = [(m.group(0), float(m.group(1)))
                 for m in re.finditer(r"font-size:\s*([0-9.]+)px", css)
                 if float(m.group(1)) < 12.0]
        assert not small, (
            f"{len(small)} stylesheet rule(s) set type under the 12.0 px floor "
            f"this review measures against: {small}")

    def test_the_app_render_has_no_string_left_under_the_floor(
            self, tmp_path: Path) -> None:
        """B13, and the end of round 1's list of three deliberate exceptions.

        The wedge names went with round 2's A4 and `the N not shown are named in
        the Drop ledger tab` came up with the canvas; the spoke-order note in
        the hole was the last one, at 10.23 px. Round 2's B10 proposed deleting
        it — but round 2's own fix removed the other copy when the caveat line
        left the canvas, so deleting it would leave the app render stating no
        spoke order at all, which PLAN §3.1 requires it to state.
        """
        from targetring.figure import WEB
        genes = [f"GENE{i:03d}" for i in range(14)]
        spec = _full_ring_spec(genes)
        out = tmp_path / "web.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
               region="Lung", out_svg=out, input_count=14, rendered_count=14,
               ordering_note=ORDER_NOTE, layout="web", ledger_counts={},
               provenance="run T1 · abc123")
        scale = 1040.0 / (WEB.figsize[0] * 72.0)
        under = []
        for el in ET.parse(out).getroot().iter("{http://www.w3.org/2000/svg}text"):
            m = re.search(r"font-size:\s*([0-9.]+)px", el.get("style") or "")
            if not m:
                continue
            as_seen = float(m.group(1)) * scale
            if as_seen < 12.0:
                under.append((round(as_seen, 2),
                              "".join(el.itertext())[:40]))
        assert not under, (
            f"{len(under)} string(s) on the app render are under the 12.0 CSS px "
            f"floor at 1440x900: {under}")

    def test_the_page_does_not_restate_the_banners_claim_a_third_time(
            self) -> None:
        """B14's other half, and it is A6 restored by A6's own fix. Round 2 moved
        `slide_line`'s clauses to the page and one of them —
        *"rings are scaled independently of one another"* — is the comparability
        claim in other words, so the page carried it 13 px under a 16.55 px
        banner making the same claim. The slide keeps the clause; the page does
        not."""
        from targetring.figure import caveats_for_the_page, slide_line
        page = " ".join(c["text"] for c in
                        caveats_for_the_page(ORDER_NOTE, [], "")[1]).lower()
        assert "scaled independently" not in page, page
        # ...and the slide, which has no banner, still says it.
        assert "scaled independently" in slide_line(ORDER_NOTE, [], "").lower()

    def test_the_caveats_are_two_kinds_of_statement_not_five_grey_lines(
            self) -> None:
        """B14. A safety caveat and a note about where the ring names are are
        not the same kind of thing, and round 2 shipped them at one weight."""
        from targetring.figure import caveats_for_the_page
        page = caveats_for_the_page(ORDER_NOTE, [], "a FIXED 26° gap")[1]
        kinds = [c["kind"] for c in page]
        assert kinds[0] == "warn", "the safety caveat leads"
        assert set(kinds) == {"warn", "how"}, kinds
        css = (STATIC / "app.css").read_text()
        assert ".figure-caveats span.warn" in css
        assert ".figure-caveats span.how" in css
        assert "esc(c.kind)" in _js()

    def test_the_reader_lands_on_a_whole_figure(self) -> None:
        """B15, round 2's hand-off. The figure is 894 px and whole; the page put
        the reader 155 px of tab row and download row above it, so the first
        thing anyone does with this page still ended in a clipped circle."""
        js = _js()
        assert re.search(r"getBoundingClientRect\(\)\.height <= window\.innerHeight",
                         js), "nothing measures whether the figure fits"
        assert re.search(r'fig\.scrollIntoView', js)


# The windows these tests measure at. The laptop is round 2's own viewport, and
# `BIG` is the smallest of the three that earns the ring names in the sweep
# (`logs/v35-web-window-sweep.txt`): a 1440p monitor with the browser full screen.
LAPTOP_WINDOW = (1040.0, 900.0)          # (panel px, viewport px) — measured live
BIG_WINDOW = (2160.0, 1440.0)
# The window where they fit the gap and are not readable in it: the fit loop takes
# two 0.96 steps off 8.0 pt and the twelve names land at 11.09 CSS px.
NEARLY_WINDOW = (1280.0, 1250.0)


class TestV35TheCircleGrowsWithTheWindow:
    """v3.5, Puck: *"the names should appear automatically when the viewport
    allows a circle big enough to hold them honestly, and stay off when it does
    not."*

    Round 2 A4 pinned the app canvas to one 9.6 x 8.25 in so that the figure
    would be whole in a 1440 x 900 laptop. It is the right canvas for that laptop,
    and pinning it also pinned the ARRANGEMENT: the twelve ring names need a
    bigger circle than 900 px of height can carry, so the 26° gap has been drawn
    and empty on every screen since, including the ones with a thousand pixels to
    spare.

    So the canvas is a function of the window (`web_layout_for_viewport`) and the
    names are decided by drawing them and keeping the drawing only if
    `assert_wedge_labels_fit` and `assert_wedge_names_clear_the_floor` accept it.
    These tests are the two halves of that sentence — on above the threshold, off
    below it — plus A4, which still holds at the window it was written for.
    """

    def _render_web(self, tmp_path: Path, window, *, name_the_rings: bool,
                    n: int = 14, tag: str = ""):
        from targetring.figure import web_layout_for_viewport
        lay = web_layout_for_viewport(*window, name_the_rings=name_the_rings)
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"web-{int(window[0])}x{int(window[1])}-{tag}.svg"
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Lung", out_svg=out, input_count=n, rendered_count=n,
                     ledger_counts={}, ordering_note=ORDER_NOTE, layout=lay,
                     provenance="run T35 · abc123")
        return lay, out, res

    def _ring_names(self, svg: Path) -> dict[str, float]:
        """Every ring name drawn inside the gap, by gid, with its size in CSS px.

        Read out of the artefact rather than off the artists, because the size a
        reader gets is the size in the file times the scale the browser draws it
        at, and that product is the whole claim being made here.
        """
        from targetring.figure import WEB_CSS_SCALE
        body = svg.read_text()
        out = {}
        for m in re.finditer(r'<g id="ringname\|([a-z0-9_]+)">(.*?)</g>', body, re.S):
            sizes = [float(s) for s in re.findall(r"font-size:\s*([0-9.]+)px", m.group(2))]
            out[m.group(1)] = max(sizes) * WEB_CSS_SCALE if sizes else 0.0
        return out

    # --- A4 still holds, at the window it was written for ----------------------

    def test_the_laptop_gets_exactly_the_canvas_round_2_measured(self) -> None:
        """A4 is not weakened by making the canvas a function: the laptop is one
        point in the function's domain and it maps to 9.6 x 8.25 in. Held as an
        identity rather than as an inequality, because a responsive canvas that
        happened to land near round 2's is not the same thing as one that lands
        on it."""
        from targetring.figure import WEB, web_layout_for_viewport
        lay = web_layout_for_viewport(*LAPTOP_WINDOW)
        assert lay.figsize == WEB.figsize == (9.6, 8.25)
        assert lay.axes_box == WEB.axes_box
        assert lay.wedge_labels_in_svg is False, (
            "the gap is empty on the laptop and that is the measured answer, not "
            "a default: see the fit test below")

    def test_the_figure_fits_every_window_it_is_drawn_for(self) -> None:
        """A4's rule, applied to whatever window it is instead of to one window:
        the whole figure fits with no scrolling. Checked at the laptop — 894 px in
        900, round 2's own six pixels of slack — and up a range of larger ones,
        because the height budget is the thing this feature spends."""
        from targetring.figure import web_css_scale, web_layout_for_viewport
        laptop = web_layout_for_viewport(*LAPTOP_WINDOW)
        px = laptop.figsize[1] * 72.0 * web_css_scale(laptop, LAPTOP_WINDOW[0])
        assert 888.0 <= px <= 900.0, f"the laptop figure is {px:.0f} px tall"
        for panel, window_h in [(880, 800), (1040, 900), (1112, 982), (1280, 1250),
                                (1520, 1080), (1520, 1413), (2160, 1440),
                                (2608, 1692)]:
            lay = web_layout_for_viewport(panel, window_h)
            tall = lay.figsize[1] * 72.0 * web_css_scale(lay, panel)
            assert tall <= window_h, (
                f"a {panel} x {window_h} window draws a {tall:.0f} px figure, so "
                f"there is no scroll position at which the circle is whole")

    def test_a_bigger_window_buys_circle_and_never_type(self) -> None:
        """The arithmetic the whole feature rests on. `scale = panel_px /
        (width_in * 72)`, so a canvas whose width tracks the panel holds every
        string at 12.04 CSS px while the circle grows — which is round 2's own
        observation about height, used in the other direction."""
        from targetring.figure import (WEB, web_circle_px, web_css_scale,
                                       web_layout_for_viewport)
        seen = []
        for panel, window_h in [(1040, 900), (1112, 982), (1280, 1250),
                                (1520, 1413), (2160, 1440)]:
            lay = web_layout_for_viewport(panel, window_h)
            scale = web_css_scale(lay, panel)
            assert abs(lay.label_size * scale - 12.04) < 0.02, (
                f"{panel}x{window_h}: the gene labels are {lay.label_size * scale:.2f} "
                f"CSS px, so the canvas bought circle with legibility")
            seen.append(round(web_circle_px(lay, panel), 1))
        assert seen == sorted(seen), seen
        assert seen[0] == round(web_circle_px(WEB), 1) == 639.0
        assert seen[-1] > 1000.0, ("a 1440p monitor gets no more circle than a "
                                   "laptop, which is the defect this fixes")

    def test_a_window_smaller_than_the_laptop_keeps_the_laptop_canvas(self) -> None:
        """The floor, and it is not tidiness. The spoke labels are points and do
        not shrink with the canvas, so a canvas cut to a narrow window walks them
        into `assert_no_text_over_the_rings` and the app answers a small window
        with no figure at all. Small windows scale the laptop canvas down, exactly
        as they did before v3.5."""
        from targetring.figure import WEB, web_layout_for_viewport
        for panel, window_h in [(600, 500), (880, 800), (1039, 899)]:
            assert web_layout_for_viewport(panel, window_h).figsize == WEB.figsize

    def test_height_past_the_point_the_width_can_use_is_not_taken(self) -> None:
        """The ceiling. The circle is `min(0.760 w, 0.715 h)`, so past
        `h = 0.760/0.715 w` more height is empty canvas: it makes the figure
        harder to fit in the window and the circle no larger."""
        from targetring.figure import WEB, web_circle_px, web_layout_for_viewport
        tall = web_layout_for_viewport(1520, 4000)
        enough = web_layout_for_viewport(1520, 1500)
        assert tall.figsize[1] <= WEB.axes_box[2] / WEB.axes_box[3] * tall.figsize[0] + 0.01
        assert web_circle_px(tall, 1520) == pytest.approx(
            web_circle_px(web_layout_for_viewport(1520, 3000), 1520))
        assert enough.figsize[1] < tall.figsize[1]

    # --- the names: on above the threshold, off below it ----------------------

    def test_the_names_come_back_when_the_circle_is_big_enough(
            self, tmp_path: Path) -> None:
        """The deliverable. Twelve names, inside the gap, each at or above the
        12.0 CSS px floor this review measures the app against — which is what
        round 1's twelve names at 9.07 px were not."""
        from targetring.figure import RING_SPECS, web_circle_px
        lay, svg, _ = self._render_web(tmp_path, BIG_WINDOW, name_the_rings=True,
                                       tag="on")
        names = self._ring_names(svg)
        assert set(names) == {s.key for s in RING_SPECS}, sorted(names)
        assert min(names.values()) >= 12.0, (
            f"the smallest ring name is {min(names.values()):.2f} CSS px: "
            f"{sorted(names.items(), key=lambda kv: kv[1])[:3]}")
        assert web_circle_px(lay, BIG_WINDOW[0]) > 1000.0

    def test_the_names_stay_off_on_the_laptop_and_the_refusal_is_the_reason(
            self, tmp_path: Path) -> None:
        """The other half, and the half that must not be a preference. Asking the
        laptop canvas for the names raises from `assert_wedge_labels_fit` itself,
        so what keeps the gap empty there is the geometry and not a flag somebody
        set."""
        from targetring.figure import RingNamesDoNotFit
        with pytest.raises(RingNamesDoNotFit) as e:
            self._render_web(tmp_path, LAPTOP_WINDOW, name_the_rings=True,
                             tag="refused")
        assert "does not fit the 12 o'clock gap" in str(e.value)
        import matplotlib.pyplot as plt
        plt.close("all")
        _, svg, _ = self._render_web(tmp_path, LAPTOP_WINDOW, name_the_rings=False,
                                     tag="off")
        assert self._ring_names(svg) == {}

    def test_names_that_only_fit_by_shrinking_under_the_floor_are_refused(
            self, tmp_path: Path) -> None:
        """The trap this feature could most easily have fallen into, and the one
        round 1 did fall into: `fit_wedge_labels` makes a name *fit* by shrinking
        it, so a circle just under the threshold can pass the geometry check with
        twelve unreadable strings in the gap. At this window it does — the loop
        takes two 0.96 steps off 8.0 pt and the names land at 11.09 CSS px, inside
        the gap and under the floor — and the render refuses rather than drawing
        them. The threshold is therefore sharp on purpose: the names are drawn at
        8.0 pt or not at all, because the first step down from it is already under
        the floor."""
        from targetring.figure import RingNamesUnderTheFloor
        with pytest.raises(RingNamesUnderTheFloor) as e:
            self._render_web(tmp_path, NEARLY_WINDOW, name_the_rings=True,
                             tag="nearly")
        assert "under the 12 CSS px floor" in str(e.value)
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_the_floor_check_is_measured_on_the_drawn_artists(self) -> None:
        """A unit test of the check itself, because a check that cannot fail is
        not a check. 8.0 pt clears the floor at this layout's scale and 7.0 pt
        does not, and the only difference between them is the number in the file.
        """
        import matplotlib.pyplot as plt
        from targetring.figure import (WEB, RingNamesUnderTheFloor,
                                       assert_wedge_names_clear_the_floor)
        fig = plt.figure(figsize=(4, 4))
        ok = fig.text(0.5, 0.5, "Tractability (suggested)", size=8.0)
        assert_wedge_names_clear_the_floor(fig, [ok], WEB)
        small = fig.text(0.5, 0.4, "Tractability (suggested)", size=7.0)
        with pytest.raises(RingNamesUnderTheFloor):
            assert_wedge_names_clear_the_floor(fig, [small], WEB)
        # ...and a layout that declares no floor is not measured here at all: the
        # export is measured against the 10.0 pt presentation floor instead.
        assert AUDIT.wedge_label_floor_px is None
        assert_wedge_names_clear_the_floor(fig, [small], AUDIT)
        plt.close(fig)

    def test_the_arrangement_is_read_off_the_layout_not_written_down_twice(
            self, tmp_path: Path) -> None:
        """COUNCIL V5, which is the rule this feature most endangers: there are
        two arrangements now and one run can draw either, so every sentence about
        where the ring names are has to come from the render that drew them.

        On this layout that sentence is handed to the page rather than drawn under
        the circle (round 2's A4 move), so it is read back off the render's own
        `FigureResult` -- which is what `app.js` draws and therefore the actual
        claim a reader meets.
        """
        from targetring.figure import clock_phrase, web_layout_for_viewport
        on_lay = web_layout_for_viewport(*BIG_WINDOW, name_the_rings=True)
        off_lay = web_layout_for_viewport(*LAPTOP_WINDOW)
        assert "ring-name gap" in clock_phrase(on_lay)
        assert "reserved gap" in clock_phrase(off_lay)

        _, _, on = self._render_web(tmp_path, BIG_WINDOW, name_the_rings=True,
                                    tag="says-on")
        _, _, off = self._render_web(tmp_path, LAPTOP_WINDOW, name_the_rings=False,
                                     tag="says-off")
        said_on = " ".join(c["text"] for c in on.web_caveats)
        said_off = " ".join(c["text"] for c in off.web_caveats)
        assert "inside the circle" in said_on, said_on
        assert "inside the circle" not in said_off, said_off
        assert "reserved at 12 o'clock" in said_off, said_off
        # Both say what the gap cost the spokes -- Puck's condition on it existing
        # at all -- and both say the scales are in the panel, because they are on
        # this layout whichever arrangement the names are in.
        for said in (said_on, said_off):
            assert "26°" in said and "full circle" in said, said
            assert "panel beside this figure" in said, said

    def test_the_two_arrangements_differ_in_furniture_and_never_in_a_cell(
            self, tmp_path: Path) -> None:
        """What makes drawing the same run two ways safe, and it is the assertion
        v3.0 already wrote for "the export and the app render may differ"."""
        from targetring.figure import assert_renders_agree
        _, on, _ = self._render_web(tmp_path, BIG_WINDOW, name_the_rings=True, tag="a")
        _, off, _ = self._render_web(tmp_path, LAPTOP_WINDOW, name_the_rings=False,
                                     tag="b")
        assert_renders_agree(on, off)

    def test_the_probe_keeps_the_arrangement_the_assertions_accepted(
            self, tmp_path: Path) -> None:
        """`render_naming_the_rings_if_they_fit` is the only thing that decides
        this, and it decides it by rendering: names first, round 2's arrangement
        if the render refuses them. Tested through a fake `render_one` so the
        control flow is visible without six renders of a real figure."""
        from targetring.figure import (RingNamesDoNotFit,
                                       render_naming_the_rings_if_they_fit)
        asked: list[bool] = []

        def refuse_the_names(lay):
            asked.append(lay.wedge_labels_in_svg)
            if lay.wedge_labels_in_svg:
                raise RingNamesDoNotFit("no room")
            return "drawn"

        got, lay = render_naming_the_rings_if_they_fit(
            refuse_the_names, panel_px=LAPTOP_WINDOW[0],
            viewport_px=LAPTOP_WINDOW[1])
        assert (got, lay.wedge_labels_in_svg) == ("drawn", False)
        assert asked == [True, False], "the names have to be tried, not predicted"

        got, lay = render_naming_the_rings_if_they_fit(
            lambda lay: "drawn", panel_px=BIG_WINDOW[0], viewport_px=BIG_WINDOW[1])
        assert (got, lay.wedge_labels_in_svg) == ("drawn", True)

    def test_an_unrelated_assertion_is_not_swallowed_by_the_probe(self) -> None:
        """The reason `RingNamesDoNotFit` is a class and not a message match. A
        bare `except AssertionError` here would answer a genuine collision — text
        over the rings, a centre block out of its hole — by drawing the same
        figure with no names and no complaint, which is this repo's one rule."""
        from targetring.figure import render_naming_the_rings_if_they_fit
        with pytest.raises(AssertionError, match="a spoke label is over the rings"):
            render_naming_the_rings_if_they_fit(
                lambda lay: (_ for _ in ()).throw(
                    AssertionError("a spoke label is over the rings")))

    # --- the page measures the window; nothing here guesses at CSS ------------

    def test_the_page_measures_the_panel_and_hands_it_over(self) -> None:
        """`#figure-col` is a flex child with `min-width: 0` beside a 330 px
        panel inside a padded `main`, so its width is not the sum of four
        constants. The page measures it and the server is told; a Python guess at
        it would be a second implementation of `app.css` that could disagree with
        the real one."""
        import inspect
        from targetring import app as A
        from targetring import figure as F
        js = _js()
        assert "getBoundingClientRect().width" in js and "figurePanelPx" in js
        assert 'fd.append("panel_px"' in js and 'fd.append("viewport_px"' in js
        assert "panel_px" in inspect.signature(A.analyse).parameters
        src = inspect.getsource(F.web_layout_for_viewport)
        src = src[src.rindex('"""') + 3:]          # the code, not the reasoning
        for css_constant in ("330", "1500"):
            assert css_constant not in src, (
                f"{css_constant} is a stylesheet number and this function must "
                f"not contain one: the browser measures, this sizes")

    def test_a_resize_redraws_the_app_figure_and_only_that(self) -> None:
        """Without this the feature is "the names appear if the window was
        already big when you pressed the button". The endpoint redraws the app
        render for the same run — no source fetched, no export rewritten — and the
        page asks for it when the resize settles."""
        import inspect
        from targetring import app as A
        js = _js()
        assert "addEventListener(\"resize\"" in js
        assert "redrawForThisWindow" in js and "RESIZE_SETTLE_MS" in js
        routes = {r.path: r for r in A.app.routes if hasattr(r, "methods")}
        route = routes.get("/api/run/{run_id}/web-figure")
        assert route is not None and "POST" in route.methods
        src = inspect.getsource(A.rewidth_web_figure)
        assert "rerender_for_window" in src
        # The one artefact a run can rewrite after serving it must not be cached.
        assert "figure-web.svg" in inspect.getsource(A.artefact)
        assert "_NO_STORE" in inspect.getsource(A.artefact)

    def test_the_figure_row_is_no_longer_capped_at_1500_px(self) -> None:
        """The cap that made a 27 in monitor pointless: `main` was 1500 px, so
        the panel topped out at 1100 px and the circle at 772 — under the 1,006 px
        the names need, on every window there is. The measure stays on the prose,
        which is what it was for."""
        css = (STATIC / "app.css").read_text()
        assert not re.search(r"main\s*\{[^}]*max-width:\s*1500px", css), (
            "the figure row is still inside a 1500 px cap")
        assert "--prose: 1500px" in css
        assert re.search(r"#input-panel[^{]*\{[^}]*max-width:\s*var\(--prose\)", css)
        assert not re.search(r"\.figure-row[^{]*\{[^}]*max-width", css)

    def test_the_export_layouts_are_untouched_by_all_of_this(self) -> None:
        """The blast radius. `audit` and `slide` are files, sized for a screen at
        full size and for a projector, and neither has a window."""
        from targetring.figure import AUDIT, LEGEND, SLIDE
        assert AUDIT.figsize == (19.5, 14.0) and SLIDE.figsize == (16.0, 9.0)
        assert AUDIT.wedge_label_size == 7.4 and AUDIT.wedge_labels_in_svg is True
        for lay in (AUDIT, SLIDE, LEGEND):
            assert lay.wedge_label_floor_px is None
