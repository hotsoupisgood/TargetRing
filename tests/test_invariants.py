"""Invariant tests.

A check that has never been seen to fail is not a check. Each of I1-I4 is
exercised against a deliberately broken artefact as well as a good one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from targetring.figure import (
    STATE_STYLE,
    VALUE_CMAP,
    assert_fills_disjoint,
    colormap_hexes,
    render,
)
from targetring.invariants import check_all
from targetring.rank import rank_genes
from targetring.states import failed, no_data, not_applicable, value

GENES = ["AAA", "BBB", "CCC", "DDD"]


def _spec():
    return {
        "logfc": {g: value(float(i) - 1.5) for i, g in enumerate(GENES)},
        "druggability": {
            "AAA": value(1.0), "BBB": value(0.2),
            "CCC": no_data("never measured"), "DDD": not_applicable("non-coding"),
        },
        "safety": {
            "AAA": value(-0.5), "BBB": value(0.7),
            "CCC": value(0.1), "DDD": failed("upstream call broke"),
        },
        "regional_specificity": {g: value(float(i) + 0.5) for i, g in enumerate(GENES)},
        "clinical_phase": {g: value(float(i % 3)) for i, g in enumerate(GENES)},
    }


@pytest.fixture
def rendered(tmp_path: Path):
    spec = _spec()
    rankings = rank_genes(spec)
    res = render(
        genes=GENES, cells_by_ring=spec, rankings=rankings, region="Lung",
        out_svg=tmp_path / "f.svg", input_count=6, rendered_count=4,
        ledger_counts={"unresolved": 2},
    )
    return res, tmp_path / "f.svg"


class TestPalette:
    def test_reserved_fills_are_outside_the_colormap(self) -> None:
        assert_fills_disjoint()

    def test_the_check_would_catch_a_bad_palette(self, monkeypatch) -> None:
        colliding = sorted(colormap_hexes())[len(colormap_hexes()) // 2]
        monkeypatch.setitem(STATE_STYLE, "no_data",
                            {**STATE_STYLE["no_data"], "fc": colliding})
        with pytest.raises(AssertionError, match=f"also produced by the {VALUE_CMAP}"):
            assert_fills_disjoint()


class TestAllPassOnAGoodRender:
    def test_i1_to_i4(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(
            svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
            input_count=6, rendered_count=4, ledger_count=2,
            regression={"ok": True, "detail": "stub"},
            literature_audit={"echo_checked": 4, "ncbi_checked": 4,
                              "echo_mismatches": [], "ncbi_errors": []},
        )
        assert rep.ok, rep.banner_lines()
        assert [r.outcome for r in rep.results] == ["PASS"] * 6

    def test_every_cell_reached_the_svg(self, rendered) -> None:
        """The pycirclize PatchCollection trap: un-hatched cells were being
        merged into one element and losing their id, which made I1 and I3
        silently partial."""
        res, svg = rendered
        ids = re.findall(r'id="(cell\|[^"]+)"', svg.read_text())
        assert len(ids) == len(res.csv_rows) == 5 * len(GENES)
        assert set(ids) == {r["cell_id"] for r in res.csv_rows}


class TestI1CellCount:
    def test_a_dropped_cell_is_caught(self, rendered) -> None:
        res, svg = rendered
        broken = [r for r in res.csv_rows if r["ring"] != "safety" or r["gene"] != "AAA"]
        rep = check_all(svg_path=svg, csv_rows=broken, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2)
        i1 = next(r for r in rep.results if r.id == "I1")
        assert i1.outcome == "FAIL" and "safety" in i1.detail


class TestI2Disjointness:
    def test_non_value_cells_share_no_colour_with_any_value_cell(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2)
        i2 = next(r for r in rep.results if r.id == "I2")
        assert i2.outcome == "PASS"
        # The proof must be about resolved colours, not url(#...) strings.
        assert "url(" not in i2.detail
        assert "hatch patterns resolved" in i2.detail

    def test_mislabelling_a_value_cell_as_no_data_is_caught(self, rendered) -> None:
        """If a value colour were ever used for a missing cell, I2 must fail."""
        res, svg = rendered
        rows = [dict(r) for r in res.csv_rows]
        for r in rows:
            if r["ring"] == "logfc" and r["gene"] == "AAA":
                r["state"] = "no_data"
        rep = check_all(svg_path=svg, csv_rows=rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2)
        i2 = next(r for r in rep.results if r.id == "I2")
        assert i2.outcome == "FAIL"


class TestI3LabelCorrespondence:
    def test_an_off_by_one_is_caught(self, rendered) -> None:
        """QA B2 in the rendered artefact: shift the CSV's genes by one and the
        check must fail even though every count still matches."""
        res, svg = rendered
        rotated = GENES[1:] + GENES[:1]
        rows = [dict(r) for r in res.csv_rows]
        for r in rows:
            r["gene"] = rotated[r["spoke_index"]]
        rep = check_all(svg_path=svg, csv_rows=rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2)
        i1 = next(r for r in rep.results if r.id == "I1")
        i3 = next(r for r in rep.results if r.id == "I3")
        assert i1.outcome == "PASS", "a count check alone passes — that is the point"
        assert i3.outcome == "FAIL"

    def test_a_reordered_gene_list_is_caught(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows,
                        gene_order=list(reversed(GENES)),
                        input_count=6, rendered_count=4, ledger_count=2)
        assert next(r for r in rep.results if r.id == "I3").outcome == "FAIL"


class TestI4Accounting:
    def test_unaccounted_gene_fails(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=9, rendered_count=4, ledger_count=2)
        i4 = next(r for r in rep.results if r.id == "I4")
        assert i4.outcome == "FAIL" and "+3 unaccounted" in i4.detail


class TestNotEvaluatedIsNotAPass:
    def test_absent_regression_is_not_evaluated(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        regression=None, literature_audit=None)
        assert next(r for r in rep.results if r.id == "I5").outcome == "NOT_EVALUATED"
        assert next(r for r in rep.results if r.id == "I6").outcome == "NOT_EVALUATED"
        assert not rep.ok, "a check that could not run is not a check that passed"
        assert len(rep.banner_lines()) == 2

    def test_unreachable_europepmc_is_not_evaluated_not_passed(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(
            svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
            input_count=6, rendered_count=4, ledger_count=2,
            literature_audit={"echo_checked": 0, "ncbi_checked": 4, "echo_mismatches": [],
                              "ncbi_errors": [], "unreachable": ["AAA: HTTP 503"]},
        )
        i5 = next(r for r in rep.results if r.id == "I5")
        assert i5.outcome == "NOT_EVALUATED" and "503" in i5.detail

    def test_echo_mismatch_is_a_failure(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(
            svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
            input_count=6, rendered_count=4, ledger_count=2,
            literature_audit={"echo_checked": 3, "ncbi_checked": 4,
                              "echo_mismatches": ["AAA: reinterpreted"], "ncbi_errors": []},
        )
        assert next(r for r in rep.results if r.id == "I5").outcome == "FAIL"


class TestBanner:
    def test_failures_reach_the_figure(self, tmp_path: Path) -> None:
        spec = _spec()
        render(genes=GENES, cells_by_ring=spec, rankings=rank_genes(spec), region="Lung",
               out_svg=tmp_path / "b.svg", input_count=6, rendered_count=4,
               ledger_counts={}, invariant_failures=["I4 accounting: FAIL — 3 unaccounted"])
        svg = (tmp_path / "b.svg").read_text()
        assert "INVARIANT FAILURE" in svg
        assert "DO NOT TRUST THIS FIGURE" in svg


class TestDegenerateGeometry:
    @pytest.mark.parametrize("n", [1, 2])
    def test_tiny_circles_render(self, tmp_path: Path, n: int) -> None:
        genes = GENES[:n]
        spec = {"logfc": {g: value(1.0) for g in genes},
                "druggability": {g: value(0.5) for g in genes}}
        res = render(genes=genes, cells_by_ring=spec, rankings=rank_genes(spec),
                     region="Lung", out_svg=tmp_path / f"n{n}.svg",
                     input_count=n, rendered_count=n, ledger_counts={})
        assert res.n_cells == 2 * n
        rep = check_all(svg_path=tmp_path / f"n{n}.svg", csv_rows=res.csv_rows,
                        gene_order=genes, input_count=n, rendered_count=n, ledger_count=0)
        for r in rep.results:
            if r.id in ("I1", "I2", "I3", "I4"):
                assert r.outcome == "PASS", (r.id, r.detail)
