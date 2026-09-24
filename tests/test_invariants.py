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


def _pareto_audit(svg, rankings):
    """The audit I7 checks, built the way run.py builds it."""
    from targetring.invariants import diamond_genes
    from targetring.rank import PARETO_AXES, pareto_axis_completeness
    c = pareto_axis_completeness(rankings)
    return {
        "axes": list(PARETO_AXES),
        "front": sorted(g for g, r in rankings.items() if r.pareto),
        "front_pareto_k": {g: r.pareto_k for g, r in rankings.items()},
        "n_eligible": c["n_eligible"], "n_ineligible": c["n_ineligible"],
        "diamond_genes": diamond_genes(svg),
    }


class TestAllPassOnAGoodRender:
    def test_i1_to_i7(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(
            svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
            input_count=6, rendered_count=4, ledger_count=2,
            regression={"ok": True, "detail": "stub"},
            literature_audit={"echo_live": 4, "echo_replayed": 0, "ncbi_checked": 4,
                              "echo_mismatches": [], "ncbi_errors": []},
            pareto_audit=_pareto_audit(svg, rank_genes(_spec())),
        )
        assert rep.ok, rep.banner_lines() + rep.condition_lines()
        # I7 (COUNCIL V2/V3, headline eligibility) joins the PASS/FAIL list.
        assert [r.outcome for r in rep.results] == ["PASS"] * 7

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
        # Superseded by UX A1: it is still not a pass, but it reaches the reader
        # through the amber conditions notice, not the red DO-NOT-TRUST banner.
        # I5, I6 and I7 all lack an input here.
        assert rep.banner_lines() == []
        assert rep.data_is_sound
        assert len(rep.condition_lines()) == 3

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


class TestA1OutageIsNotInvariantFailure:
    """UX A1 / COUNCIL V3 / V11.

    A Europe PMC outage — a source that sets no ring value at all, as the app's
    own amber note said directly underneath — stamped a full-width
    "INVARIANT FAILURE — DO NOT TRUST THIS FIGURE" across the middle of the
    exported SVG. Cry wolf twice and the banner stops working the day it is
    real. A source being down and an invariant being violated are different
    facts and must not render identically.
    """

    @staticmethod
    def _rep(**kw):
        defaults = dict(
            input_count=6, rendered_count=4, ledger_count=2,
            regression={"ok": True, "detail": "stub"},
            literature_audit={"echo_live": 4, "echo_replayed": 0, "ncbi_checked": 4,
                              "echo_mismatches": [], "ncbi_errors": []},
            pareto_audit={"axes": ["druggability", "safety", "regional_specificity",
                                   "clinical_phase"],
                          "front": [], "front_pareto_k": {}, "n_eligible": 0,
                          "n_ineligible": 0, "diamond_genes": []},
        )
        defaults.update(kw)
        return check_all(**defaults)

    def test_an_outage_raises_conditions_and_not_the_red_banner(self, rendered) -> None:
        res, svg = rendered
        rep = self._rep(
            svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
            literature_audit={"echo_live": 0, "echo_replayed": 0, "ncbi_checked": 4,
                              "echo_mismatches": [], "ncbi_errors": [],
                              "unreachable": ["AAA: HTTP 503 text/html"]},
        )
        assert rep.banner_lines() == [], "an outage must not say DO NOT TRUST"
        assert rep.data_is_sound, "no invariant was checked and violated"
        assert not rep.ok, "and it is still not a pass"
        conds = rep.condition_lines()
        assert len(conds) == 1 and "NOT CHECKED" in conds[0]
        assert "sets no ring value" in conds[0], "the notice must say what it affects"

    def test_a_real_invariant_failure_still_raises_the_red_banner(self, rendered) -> None:
        res, svg = rendered
        rep = self._rep(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=9)
        assert not rep.data_is_sound
        assert any("I4" in b and "FAIL" in b for b in rep.banner_lines())

    def test_the_two_reach_the_figure_differently(self, tmp_path: Path) -> None:
        """Measured in the SVG, not on screen."""
        spec = _spec()
        kw = dict(genes=GENES, cells_by_ring=spec, rankings=rank_genes(spec),
                  region="Lung", input_count=6, rendered_count=4, ledger_counts={})

        render(out_svg=tmp_path / "outage.svg", run_conditions=[
            "I5 literature query integrity: NOT CHECKED — 0 live echoes "
            "· Europe PMC sets no ring value"], **kw)
        outage = (tmp_path / "outage.svg").read_text()
        assert "DO NOT TRUST THIS FIGURE" not in outage
        assert "INVARIANT FAILURE" not in outage
        assert "RUN CONDITIONS" in outage and "not an invariant failure" in outage

        render(out_svg=tmp_path / "broken.svg",
               invariant_failures=["I4 input == rendered + ledgered: FAIL"], **kw)
        broken = (tmp_path / "broken.svg").read_text()
        assert "DO NOT TRUST THIS FIGURE" in broken
        assert "RUN CONDITIONS" not in broken

    def test_the_banner_text_is_not_truncated_mid_url(self) -> None:
        """UX A1: v1 cut the failure string at 200 characters and ended mid-URL
        at `?query=%22S`, so the mangled text was in the source before it was
        ever drawn."""
        from targetring.invariants import InvariantReport, Result
        url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
               "?query=%22SERPINA1%22&format=json&pageSize=1")
        rep = InvariantReport([Result("I5", "literature", "FAIL",
                                      f"HTTP 503 from {url} while checking the echo")])
        line = rep.banner_lines()[0]
        assert "http" not in line
        assert "%22S" not in line
        assert "[url in the manifest]" in line
        assert not line.endswith("…"), "short enough to need no truncation at all"

    def test_a_long_detail_is_cut_on_a_word_boundary(self) -> None:
        from targetring.invariants import InvariantReport, Result
        rep = InvariantReport([Result("I1", "cells", "FAIL", "wordy " * 80)])
        line = rep.banner_lines()[0]
        assert line.endswith("…") and not line.endswith("wor …")


class TestV11ReplayedEchoIsNotACheckedEcho:
    """COUNCIL V11. v1 reported `I5 PASS — 14 Europe PMC echo(es) byte-identical`
    off a warm cache while EBI was returning 503 to every live request."""

    def _i5(self, audit):
        from targetring.invariants import _i5_literature
        return _i5_literature(audit)

    def test_replays_alone_do_not_pass(self) -> None:
        r = self._i5({"echo_live": 0, "echo_replayed": 14, "ncbi_checked": 14,
                      "echo_mismatches": [], "ncbi_errors": [],
                      "unreachable": ["SERPINA1: HTTP 503"]})
        assert r.outcome == "NOT_EVALUATED"
        assert "replayed" in r.detail and "14" in r.detail

    def test_one_live_echo_is_enough_to_check(self) -> None:
        r = self._i5({"echo_live": 1, "echo_replayed": 13, "ncbi_checked": 14,
                      "echo_mismatches": [], "ncbi_errors": []})
        assert r.outcome == "PASS"
        assert "1 Europe PMC echo(es) checked live" in r.detail
        assert "13 more replayed from cache and not counted" in r.detail

    def test_a_mismatch_is_still_a_failure_however_it_arrived(self) -> None:
        r = self._i5({"echo_live": 0, "echo_replayed": 5, "ncbi_checked": 5,
                      "echo_mismatches": ["BRAF: reinterpreted"], "ncbi_errors": []})
        assert r.outcome == "FAIL"


class TestI7HeadlineEligibility:
    """COUNCIL V2/V3, the new invariant. It would have caught the original
    defect: a gene whose only Pareto axis was druggability=1.0 reached the front
    and ejected two genes that had all four."""

    def _audit(self, **kw):
        base = {
            "axes": ["druggability", "safety", "regional_specificity", "clinical_phase"],
            "front": ["AAA"], "front_pareto_k": {"AAA": 4, "BBB": 2},
            "n_eligible": 1, "n_ineligible": 1, "diamond_genes": ["AAA"],
        }
        base.update(kw)
        return base

    def _run(self, rendered, audit):
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        pareto_audit=audit)
        return next(r for r in rep.results if r.id == "I7")

    def test_passes_when_every_front_member_has_every_axis(self, rendered) -> None:
        i7 = self._run(rendered, self._audit())
        assert i7.outcome == "PASS"
        assert "1 gene(s) ineligible" in i7.detail

    def test_fails_when_a_one_axis_gene_reaches_the_front(self, rendered) -> None:
        i7 = self._run(rendered, self._audit(
            front=["AAA", "ONEAXIS"],
            front_pareto_k={"AAA": 4, "ONEAXIS": 1},
            diamond_genes=["AAA", "ONEAXIS"]))
        assert i7.outcome == "FAIL"
        assert "ONEAXIS" in i7.detail and "every axis" in i7.detail

    def test_fails_when_the_figure_and_the_ranking_disagree(self, rendered) -> None:
        """Checked in the artefact, like I1-I3: a rendering bug must not be able
        to put a headline marker on a gene the ranking says is ineligible."""
        i7 = self._run(rendered, self._audit(diamond_genes=["AAA", "BBB"]))
        assert i7.outcome == "FAIL"
        assert "diamond" in i7.detail

    def test_absent_audit_is_not_evaluated_not_passed(self, rendered) -> None:
        i7 = self._run(rendered, None)
        assert i7.outcome == "NOT_EVALUATED"

    def test_i7_is_in_the_manifest_pass_fail_list(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        regression={"ok": True, "detail": "x"},
                        literature_audit={"echo_live": 1, "ncbi_checked": 1,
                                          "echo_mismatches": [], "ncbi_errors": []},
                        pareto_audit=self._audit())
        ids = [r["id"] for r in rep.as_json()]
        assert ids == ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]
        assert rep.ok, rep.banner_lines() + rep.condition_lines()

    def test_the_diamond_is_readable_back_out_of_the_svg(self, tmp_path: Path) -> None:
        from targetring.invariants import diamond_genes
        spec = _spec()
        rankings = rank_genes(spec)
        rankings["AAA"].pareto = True
        render(genes=GENES, cells_by_ring=spec, rankings=rankings, region="Lung",
               out_svg=tmp_path / "d.svg", input_count=4, rendered_count=4,
               ledger_counts={}, gene_flags={"AAA": ["Torsades de Point"]})
        want = sorted(g for g, r in rankings.items() if r.pareto)
        assert sorted(diamond_genes(tmp_path / "d.svg")) == want
        assert "AAA" in want, "the ⚠ liability marker must not hide the diamond"
