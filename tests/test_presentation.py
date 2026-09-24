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
from targetring.rank import DEFAULT_WEIGHTS, rank_genes
from targetring.states import failed, no_data, not_applicable, value

GENES = ["BRAF", "CDKN2A", "INS", "KCNQ1", "EGFR", "GAPDH"]


def _spec():
    return {
        "logfc": {g: value(float(i) - 2.5) for i, g in enumerate(GENES)},
        "druggability": {g: value(0.2 * i) for i, g in enumerate(GENES)},
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
    res = render(genes=GENES, cells_by_ring=spec, rankings=rank_genes(spec),
                 region="Liver", out_svg=out, input_count=8, rendered_count=6,
                 layout=layout, weights=DEFAULT_WEIGHTS,
                 provenance="run X · abc123", **kw)
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
        _, svg = _render(tmp_path, "audit")
        body = svg.read_text()
        assert "rings, outermost first" in body, "the audit render keeps the key"
        assert "Weighted score is a labelled CONVENIENCE" in body

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
        factor is 7.5in / canvas height. v1's 8 pt label became 3.9 pt."""
        _, svg = _render(tmp_path, "slide")
        w, h = _viewbox(svg)
        scale = 7.5 / (h / 100.0)          # 100 dpi canvas -> 7.5 in slide
        gene_label_pt = SLIDE.label_size * scale
        assert gene_label_pt >= 5.0, f"{gene_label_pt:.1f} pt on a full slide"
        assert gene_label_pt > 8.0 * (7.5 / 15.2), "must beat v1's 3.9 pt"

    def test_the_key_and_the_six_line_footer_leave_the_slide(self, tmp_path: Path) -> None:
        _, svg = _render(tmp_path, "slide")
        body = svg.read_text()
        assert "rings, outermost first" not in body
        assert "hasSmallMoleculeBinder" not in body
        assert "Weighted score is a labelled CONVENIENCE" not in body
        # One line still does the honest work.
        assert "ranking is provisional and not validated" in body
        assert "see the caption" in body

    def test_the_caption_carries_everything_the_slide_dropped(self, tmp_path: Path) -> None:
        res, _ = _render(tmp_path, "slide")
        cap = res.caption
        for must in ["Rings, outermost first", "hasSmallMoleculeBinder",
                     "Default weights", "Pareto non-dominated set",
                     "Missing is never imputed", "not a validated ranking",
                     "genetic evidence"]:
            assert must in cap, must

    def test_the_caption_names_the_liability_flag_when_there_is_one(
            self, tmp_path: Path) -> None:
        res, _ = _render(tmp_path, "slide", gene_flags={"KCNQ1": ["long QT syndrome"]})
        assert "⚠" in res.caption and "KCNQ1" in res.caption


class TestA5ColourBar:
    """A5: "There is no colour bar anywhere on the figure. Nothing tells a
    reader whether purple or yellow is the high end of a heatmap." """

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_both_renders_carry_a_colour_bar(self, tmp_path: Path, layout: str) -> None:
        _, svg = _render(tmp_path, layout)
        body = svg.read_text()
        assert ">low<" in body and ">high<" in body

    @pytest.mark.parametrize("layout", ["audit", "slide"])
    def test_it_says_the_rings_are_not_comparable(self, tmp_path: Path, layout: str) -> None:
        """The natural reading of a Circos plot is radial, and because every
        ring is min-max normalised within itself that reading is invalid."""
        _, svg = _render(tmp_path, layout)
        body = svg.read_text().replace("\n", " ")
        assert "RESCALED WITHIN EACH RING" in body
        assert "means nothing in common" in body

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
                "druggability": {g: value((i % 4) / 3) for i, g in enumerate(genes)}}
        for layout in ("audit", "slide"):
            render(genes=genes, cells_by_ring=spec, rankings=rank_genes(spec),
                   region="Liver", out_svg=tmp_path / f"{layout}{n}.svg",
                   input_count=n, rendered_count=n, ledger_counts={}, layout=layout)

    def test_the_check_would_catch_a_block_over_the_arcs(self, tmp_path: Path) -> None:
        """A check that has never been seen to fail is not a check."""
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(4, 4))
        ax = fig.add_axes((0.1, 0.1, 0.8, 0.8))
        bad = fig.text(0.5, 0.5, "straight through the middle", ha="center")
        with pytest.raises(AssertionError, match="over the figure's own data"):
            assert_no_text_over_the_rings(fig, ax, [bad])
        plt.close(fig)

    def test_a_block_in_the_corner_is_not_a_collision(self, tmp_path: Path) -> None:
        """The axes box bounds the circle, so a corner is outside the data."""
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(4, 4))
        ax = fig.add_axes((0.3, 0.3, 0.4, 0.4))
        ok = fig.text(0.02, 0.97, "key", ha="left", va="top")
        assert_no_text_over_the_rings(fig, ax, [ok])
        plt.close(fig)


class TestB1SectorCeiling:
    """B1: `Circos({...}, space=2)` raises "Too large sector space size is set!!"
    for n >= 181, which reached the user as a raw traceback after ~13 minutes of
    fetching."""

    def test_the_input_cap_is_below_the_geometric_ceiling(self) -> None:
        from targetring.config import CIRCOS_MAX_SECTORS, MAX_GENES
        assert MAX_GENES < CIRCOS_MAX_SECTORS
        assert MAX_GENES * 2 < 360

    def test_an_over_large_render_says_what_happened(self, tmp_path: Path) -> None:
        genes = [f"G{i}" for i in range(200)]
        spec = {"logfc": {g: value(1.0) for g in genes}}
        with pytest.raises(ValueError, match="exceeds the 360 degrees"):
            render(genes=genes, cells_by_ring=spec, rankings=rank_genes(spec),
                   region="Liver", out_svg=tmp_path / "big.svg",
                   input_count=200, rendered_count=200, ledger_counts={})

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
            if "RESCALED WITHIN EACH RING" not in text:
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
