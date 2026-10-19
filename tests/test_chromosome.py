"""v4.0: which chromosome each gene is on, on the rim and in the drawer.

Puck, 2026-10-19: "add which chromosome each gene is on next to the gene text
on the ring".

It is the first reading in this build that is a NAME rather than a number, so
the thing these tests are really protecting is that it did not arrive as a
plain string with four different reasons for being empty. `states.TextCell`
carries the same four states every ring carries, the adapter decides which one
applies, and the rim draws a suffix for exactly one of them while the figure
prints the rule it is following.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from targetring.adapters.opentargets import (
    HUMAN_CHROMOSOMES,
    OTRecord,
    chromosome_cell,
    has_genomic_location_key,
    TARGET_QUERY,
    _from_payload,
)
from targetring.figure import _CHR_NOTE, _CHR_SEP, _chromosome_suffix, render
from targetring.order import EXTERNAL_RINGS, gene_stats
from targetring.states import TextCell, text_no_data, text_value

from test_presentation import ORDER_NOTE, _full_ring_spec

SVG_TEXT = "{http://www.w3.org/2000/svg}text"


def payload(chromosome: str | None = "16", **kw) -> dict:
    """An Open Targets `targets()` row, as the live query returns one."""
    out: dict = {"id": "ENSG00000207734", "approvedSymbol": "NOD2",
                 "genomicLocation": {"chromosome": chromosome}}
    out.update(kw)
    return out


class TestTheAdapterReadsTheField:
    def test_the_production_query_asks_for_it(self) -> None:
        """QA B1: the query is pinned in the module, so what it asks for is
        checkable without a network. A ring drawn from a field the query does
        not request is empty for every gene and looks like absence."""
        assert "genomicLocation{chromosome}" in TARGET_QUERY

    def test_the_chromosome_comes_off_the_payload(self) -> None:
        rec = _from_payload("ENSG00000207734", payload("16"))
        assert rec.chromosome == "16"
        assert rec.chromosome_asked is True

    def test_a_non_numeric_chromosome_is_kept_verbatim(self) -> None:
        """X, Y and MT are chromosomes. Casting to int is the bug this asserts
        against -- the same class of bug as `parseInt("PHASE_1_2")`."""
        for name in ("X", "Y", "MT"):
            assert _from_payload("ENSG1", payload(name)).chromosome == name
            assert name in HUMAN_CHROMOSOMES

    def test_an_absent_location_is_none_not_an_empty_string(self) -> None:
        rec = _from_payload("ENSG1", {"id": "ENSG1", "genomicLocation": None})
        assert rec.chromosome is None
        assert rec.chromosome_asked is True

    def test_a_payload_with_no_location_key_was_never_asked(self) -> None:
        """The cache-staleness discriminator. ~250 runs under `runs/` cached a
        payload before this field was in the query, and the cache key is not
        versioned: serving one of those would turn "we did not ask" into
        `no_data`, which reads as Open Targets not knowing where the gene is."""
        rec = _from_payload("ENSG1", {"id": "ENSG1", "approvedSymbol": "NOD2"})
        assert rec.chromosome_asked is False
        assert not has_genomic_location_key({"id": "ENSG1"})
        assert has_genomic_location_key({"id": "ENSG1", "genomicLocation": None})


class TestTheFourStates:
    """AGENTS.md rule 1, applied to a label. Four reasons a suffix can be
    absent, and each of them says which it was."""

    def test_a_chromosome_is_a_value_with_its_sentence(self) -> None:
        cell = chromosome_cell(OTRecord("ENSG1", found=True, chromosome="16"))
        assert cell.state == "value"
        assert cell.text == "16"
        assert "genomicLocation" in cell.detail

    def test_a_gene_absent_from_open_targets_is_no_data(self) -> None:
        cell = chromosome_cell(OTRecord("ENSG00000279487", found=False))
        assert cell.state == "no_data"
        assert cell.text is None
        assert "ENSG00000279487" in cell.detail

    def test_a_target_with_no_location_is_no_data_not_blank(self) -> None:
        cell = chromosome_cell(OTRecord("ENSG1", found=True, chromosome=None))
        assert cell.state == "no_data"

    def test_an_unplaced_scaffold_is_not_applicable(self) -> None:
        """Ensembl's primary assembly carries patch and unplaced scaffolds. A
        gene on one is not on a numbered chromosome, and printing
        `GENE · KI270734.1` on the rim would call a scaffold a chromosome."""
        cell = chromosome_cell(OTRecord("ENSG1", found=True, chromosome="KI270734.1"))
        assert cell.state == "not_applicable"
        assert "KI270734.1" in cell.detail

    def test_a_broken_call_is_failed_not_missing(self) -> None:
        cell = chromosome_cell(OTRecord("ENSG1", found=False, error="ReadTimeout"))
        assert cell.state == "failed"
        assert "ReadTimeout" in cell.detail

    def test_a_stale_cache_entry_is_failed_not_no_data(self) -> None:
        """"Nobody asked" is not "nothing is there". It is the one state that
        cannot be reached from the live query at all, only from a cache written
        by an older build, and it must not be silently rounded to absence."""
        cell = chromosome_cell(
            OTRecord("ENSG1", found=True, chromosome=None, chromosome_asked=False))
        assert cell.state == "failed"
        assert "never asked" in cell.detail

    def test_every_state_is_reachable(self) -> None:
        states = {
            chromosome_cell(r).state for r in (
                OTRecord("E", found=True, chromosome="7"),
                OTRecord("E", found=True, chromosome=None),
                OTRecord("E", found=True, chromosome="KI270734.1"),
                OTRecord("E", found=False, error="boom"),
            )
        }
        assert states == {"value", "no_data", "not_applicable", "failed"}


class TestTheTextCellKeepsTheDiscipline:
    def test_a_value_must_carry_text(self) -> None:
        with pytest.raises(ValueError):
            TextCell("value", None)

    def test_a_non_value_must_not_carry_text(self) -> None:
        with pytest.raises(ValueError):
            TextCell("no_data", "16")

    def test_only_a_value_counts_as_available(self) -> None:
        assert text_value("16").available
        assert not text_no_data("nothing on record").available

    def test_the_drawer_shape_is_the_three_keys_a_ring_cell_writes(self) -> None:
        assert text_value("16", "why").as_dict() == {
            "state": "value", "text": "16", "detail": "why"}


class TestTheLabelOnTheRim:
    """What the spoke actually says."""

    def _render(self, tmp_path: Path, n: int, layout: str = "audit", **kw):
        genes = [f"GENE{i:03d}" for i in range(n)]
        spec = _full_ring_spec(genes)
        out = tmp_path / f"chr-{layout}-{n}.svg"
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=out, input_count=n, rendered_count=n,
                     ledger_counts={}, ordering_note=ORDER_NOTE, layout=layout,
                     provenance="run X · abc123", **kw)
        return res, out, genes

    def _rim_text(self, svg: Path) -> str:
        return re.sub(r"\s+", " ", " ".join(
            "".join(el.itertext()) for el in ET.parse(svg).getroot().iter(SVG_TEXT)))

    def test_the_suffix_is_the_number_after_the_symbol(self) -> None:
        assert _chromosome_suffix(text_value("16")) == f"{_CHR_SEP}16"

    @pytest.mark.parametrize("state_cell", [
        text_no_data("nothing on record"),
        TextCell("not_applicable", None, "an unplaced scaffold"),
        TextCell("failed", None, "the call broke"),
        None,
    ])
    def test_no_data_renders_no_suffix(self, state_cell) -> None:
        """Rule 1's one sanctioned blank, and it is sanctioned because the
        figure prints the rule and the drawer names the state."""
        assert _chromosome_suffix(state_cell) == ""

    @pytest.mark.parametrize("n", [6, 14, 50])
    def test_the_chromosome_is_drawn_next_to_the_gene(
            self, tmp_path: Path, n: int) -> None:
        """At 50 genes too: the label is already at 6.56 pt there and the rim
        has 121 px before the scale column, which is what killed the Ensembl id
        (`_ID_NOTE`). A render that overprinted would raise, not merely look
        wrong."""
        chroms = {f"GENE{i:03d}": text_value(str(i % 22 + 1)) for i in range(n)}
        _, svg, genes = self._render(tmp_path, n, gene_chromosomes=chroms)
        text = self._rim_text(svg)
        for g in genes:
            want = f"{g}{_CHR_SEP}{chroms[g].text}"
            assert want in text, f"the rim does not carry {want!r}"

    def test_the_suffix_sits_before_k_not_after_it(self, tmp_path: Path) -> None:
        chroms = {f"GENE{i:03d}": text_value("16") for i in range(6)}
        _, svg, _ = self._render(tmp_path, 6, gene_chromosomes=chroms)
        assert f"GENE000{_CHR_SEP}16 (k=" in self._rim_text(svg)

    def test_a_gene_with_no_chromosome_keeps_its_bare_symbol(
            self, tmp_path: Path) -> None:
        chroms = {"GENE000": text_value("16"),
                  "GENE001": text_no_data("Open Targets reports no genomicLocation")}
        _, svg, _ = self._render(tmp_path, 6, gene_chromosomes=chroms)
        text = self._rim_text(svg)
        assert f"GENE000{_CHR_SEP}16 (k=" in text
        assert "GENE001 (k=" in text
        assert f"GENE001{_CHR_SEP}" not in text

    def test_the_figure_states_the_rule_when_it_draws_a_suffix(
            self, tmp_path: Path) -> None:
        """COUNCIL V5. A reader who sees `GENE000 · 16` beside a bare `GENE001`
        must be told what the bare one means, on the figure, not in a doc."""
        chroms = {"GENE000": text_value("16")}
        _, svg, _ = self._render(tmp_path, 6, gene_chromosomes=chroms)
        body = re.sub(r"\s+", " ", self._rim_text(svg))
        assert re.sub(r"\s+", " ", _CHR_NOTE) in body

    def test_the_figure_is_silent_about_a_mark_it_never_drew(
            self, tmp_path: Path) -> None:
        """The other half, and the one that rots: a run where every gene came
        back without a chromosome must not print a rule about a suffix no spoke
        carries."""
        _, svg, _ = self._render(tmp_path, 6)
        assert "Chromosome per gene" not in self._rim_text(svg)

    def test_a_canvas_with_no_room_refuses_the_suffix_and_says_so(
            self, tmp_path: Path) -> None:
        """v4.0's retry, end to end on the canvas that earned it.

        The app canvas is 9.6 x 8.25 in and the comparability banner's bottom
        edge is 5 px above the longest spoke label; ` · 22` advances 19 px. So
        the render refuses -- as `ChromosomesDoNotFit`, not as a silent omission
        -- and the retry draws the same figure without the suffix and hands the
        page the clause that says where the chromosome went.
        """
        from targetring.figure import (
            ChromosomesDoNotFit, render_with_the_chromosomes_if_they_fit,
            web_layout_for_viewport,
        )
        genes = [f"GENE{i:03d}" for i in range(12)] + ["APOBEC3A"]
        spec = _full_ring_spec(genes)
        chroms = {g: text_value("22") for g in genes}
        out = tmp_path / "web.svg"

        def render_one(candidate):
            return render(
                genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                region="Liver", out_svg=out, input_count=len(genes),
                rendered_count=len(genes), ledger_counts={},
                ordering_note=ORDER_NOTE, layout=candidate,
                provenance="run X · abc123", gene_chromosomes=chroms)

        wanted = web_layout_for_viewport(name_the_rings=False)
        with pytest.raises(ChromosomesDoNotFit):
            render_one(wanted)
        res, lay = render_with_the_chromosomes_if_they_fit(render_one, wanted)
        assert lay.chromosomes_on_rim is False
        rim = self._rim_text(out)
        assert f"APOBEC3A{_CHR_SEP}22" not in rim
        assert "APOBEC3A (k=" in rim
        said = " ".join(c["text"] for c in res.web_caveats)
        assert "not on this render's rim" in said, res.web_caveats

    def test_a_canvas_with_room_keeps_the_suffix_and_retries_nothing(
            self, tmp_path: Path) -> None:
        from targetring.figure import render_with_the_chromosomes_if_they_fit, AUDIT
        genes = [f"GENE{i:03d}" for i in range(6)]
        spec = _full_ring_spec(genes)
        chroms = {g: text_value("22") for g in genes}
        out = tmp_path / "audit.svg"
        calls: list[str] = []

        def render_one(candidate):
            calls.append(candidate.name)
            return render(
                genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                region="Liver", out_svg=out, input_count=6, rendered_count=6,
                ledger_counts={}, ordering_note=ORDER_NOTE, layout=candidate,
                provenance="run X · abc123", gene_chromosomes=chroms)

        _, lay = render_with_the_chromosomes_if_they_fit(render_one, AUDIT)
        assert lay.chromosomes_on_rim is True
        assert len(calls) == 1, "the audit canvas has room; it must not re-render"

    def test_an_unrelated_collision_is_not_blamed_on_the_chromosome(self) -> None:
        """The half that keeps `AGENTS.md:17`. If the retry caught every
        `AssertionError` it would answer a genuine overprint by drawing the
        figure again without a suffix and shipping it clean."""
        from targetring.figure import _chromosome_blamed
        labels = [f"GENE000{_CHR_SEP}16 (k=9/9)", "GENE001 (k=8/9)"]
        assert _chromosome_blamed(
            AssertionError("'EACH RING HAS ITS OWN' comes within 4 px of the spoke "
                           "label 'GENE000 · 16 (k=9/9)'"), labels)
        assert not _chromosome_blamed(
            AssertionError("the footer block overlaps the scale column"), labels)
        assert not _chromosome_blamed(
            AssertionError("'derived' comes within 4 px of the spoke label "
                           "'GENE001 (k=8/9)'"), labels)

    def test_the_chromosome_is_not_a_ring_and_is_not_counted_in_k(self) -> None:
        """It drives no ring, has no colour scale and must never move k, which
        is a count of MEASUREMENTS. A chromosome is a label."""
        assert "chromosome" not in EXTERNAL_RINGS
        from targetring.figure import RING_SPECS
        assert "chromosome" not in {s.key for s in RING_SPECS}
