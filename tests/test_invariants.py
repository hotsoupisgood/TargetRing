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
from targetring.order import gene_stats
from targetring.states import failed, no_data, not_applicable, value

GENES = ["AAA", "BBB", "CCC", "DDD"]


def _spec():
    return {
        "logfc": {g: value(float(i) - 1.5) for i, g in enumerate(GENES)},
        "tractable": {
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


ORDER_NOTE = "spoke order: the order of your submitted DE table (unchanged)"


@pytest.fixture
def rendered(tmp_path: Path):
    spec = _spec()
    res = render(
        genes=GENES, cells_by_ring=spec, stats=gene_stats(spec), region="Lung",
        out_svg=tmp_path / "f.svg", input_count=6, rendered_count=4,
        ledger_counts={"unresolved": 2}, ordering_note=ORDER_NOTE,
    )
    return res, tmp_path / "f.svg"


class TestPalette:
    def test_reserved_fills_are_outside_the_colormap(self) -> None:
        assert_fills_disjoint()

    def test_the_check_would_catch_a_bad_palette(self, monkeypatch) -> None:
        """v2.6: `colormap_hexes` is the union over twelve families, so this
        also proves the check widened with them -- a reserved fill has to be
        outside every ring's colours, not outside whichever ramp came first."""
        colliding = sorted(colormap_hexes())[len(colormap_hexes()) // 2]
        monkeypatch.setitem(STATE_STYLE, "no_data",
                            {**STATE_STYLE["no_data"], "fc": colliding})
        with pytest.raises(AssertionError, match="also produced by the value colormaps"):
            assert_fills_disjoint()


def _claims(res=None, **over):
    """The claims I7 checks, built the way run.py builds them."""
    return {
        "ordering": "submitted", "submitted_order": list(GENES),
        "ordering_note": res.ordering_note if res is not None else ORDER_NOTE,
        "filter_notes": list(res.filter_notes) if res is not None else [],
        "n_filtered": 0, "n_kept_unevaluated": 0, **over,
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
            figure_claims=_claims(res),
        )
        assert rep.ok, rep.banner_lines() + rep.condition_lines()
        # I7 (COUNCIL V5, re-pointed: the figure states what it did) is in the list.
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
        render(genes=GENES, cells_by_ring=spec, stats=gene_stats(spec), region="Lung",
               out_svg=tmp_path / "b.svg", input_count=6, rendered_count=4,
               ledger_counts={}, ordering_note=ORDER_NOTE,
               invariant_failures=["I4 accounting: FAIL — 3 unaccounted"])
        svg = (tmp_path / "b.svg").read_text()
        assert "INVARIANT FAILURE" in svg
        assert "DO NOT TRUST THIS FIGURE" in svg


class TestDegenerateGeometry:
    @pytest.mark.parametrize("n", [1, 2])
    def test_tiny_circles_render(self, tmp_path: Path, n: int) -> None:
        genes = GENES[:n]
        spec = {"logfc": {g: value(1.0) for g in genes},
                "tractable": {g: value(0.5) for g in genes}}
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Lung", out_svg=tmp_path / f"n{n}.svg",
                     input_count=n, rendered_count=n, ledger_counts={},
                     ordering_note=ORDER_NOTE)
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
            figure_claims=_claims(),
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
        kw = dict(genes=GENES, cells_by_ring=spec, stats=gene_stats(spec),
                  region="Lung", input_count=6, rendered_count=4, ledger_counts={},
                  ordering_note=ORDER_NOTE)

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
        # v4.3: it names the call that went live. The probe bypasses the
        # cache by construction; the per-gene counts do not, and the drawer
        # says so on each of them. I5 saying only "checked live" read as a
        # contradiction of the drawer (review of 2026-10-20, item 3).
        assert "1 Europe PMC integrity probe echo(es) checked live" in r.detail
        assert "read from this machine's cache and each says so" in r.detail
        assert "13 more replayed from cache and not counted" in r.detail

    def test_a_mismatch_is_still_a_failure_however_it_arrived(self) -> None:
        r = self._i5({"echo_live": 0, "echo_replayed": 5, "ncbi_checked": 5,
                      "echo_mismatches": ["BRAF: reinterpreted"], "ncbi_errors": []})
        assert r.outcome == "FAIL"


class TestI7TheFigureStatesWhatItDid:
    """COUNCIL V5, re-pointed by v2.5.

    I7 was "headline eligibility": every gene in the Pareto front had a real
    value on all the Pareto axes, and the diamonds drawn on the figure were
    exactly that front. The first half was a ranking check and went with ranking.
    The second half was a *figure-honesty* check — the artefact must not assert
    something the run does not support — and that survives its feature. I7 keeps
    its id and now proves three things about the drawn SVG: no headline marker
    outlived the ranking, the spoke order is stated, and every active filter is
    named on the figure.
    """

    def _run(self, rendered, claims):
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        figure_claims=claims)
        return next(r for r in rep.results if r.id == "I7")

    def test_passes_on_an_honest_unfiltered_figure(self, rendered) -> None:
        res, _ = rendered
        i7 = self._run(rendered, _claims(res))
        assert i7.outcome == "PASS"
        # v4.3: reworded. Nothing is ranked, so there was no ranking for a
        # marker to survive -- and the page's own sub-header says "Nothing here
        # is ranked or scored" (review of 2026-10-20, item 3).
        assert "no headline marker is drawn on the figure" in i7.detail
        assert "nothing is ranked or scored to put one there" in i7.detail
        assert "survives the ranking" not in i7.detail
        assert "no threshold filter was active" in i7.detail

    def test_fails_when_a_headline_marker_outlives_the_ranking(self, tmp_path: Path,
                                                               rendered) -> None:
        """Checked in the artefact, like I1-I3: a rendering bug must not be able
        to leave a ◆ on a figure that computes no front."""
        res, svg = rendered
        doctored = tmp_path / "doctored.svg"
        doctored.write_text(svg.read_text().replace(
            ">AAA (k=", ">\u25c6 AAA (k=", 1))
        rep = check_all(svg_path=doctored, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        figure_claims=_claims(res))
        i7 = next(r for r in rep.results if r.id == "I7")
        assert i7.outcome == "FAIL"
        assert "retired headline marker" in i7.detail

    def test_fails_when_the_figure_does_not_state_its_spoke_order(self, rendered) -> None:
        i7 = self._run(rendered, _claims(ordering_note="spoke order: by median rank"))
        assert i7.outcome == "FAIL"
        assert "does not say so" in i7.detail

    def test_fails_when_an_active_filter_is_not_named_on_the_figure(self,
                                                                    rendered) -> None:
        """The one that matters: a reader must not take a filtered figure for the
        whole submitted set."""
        i7 = self._run(rendered, _claims(filter_notes=["Literature volume >= 2"],
                                         n_filtered=3))
        assert i7.outcome == "FAIL"
        assert "Literature volume >= 2" in i7.detail

    def test_passes_when_the_filter_is_named(self, tmp_path: Path) -> None:
        spec = _spec()
        note = "Literature volume >= 2"
        res = render(genes=GENES, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Lung", out_svg=tmp_path / "f.svg", input_count=7,
                     rendered_count=4, ledger_counts={"filtered": 3},
                     ordering_note=ORDER_NOTE, filter_notes=[note])
        rep = check_all(svg_path=tmp_path / "f.svg", csv_rows=res.csv_rows,
                        gene_order=GENES, input_count=7, rendered_count=4,
                        ledger_count=3,
                        figure_claims=_claims(res, n_filtered=3, n_kept_unevaluated=1))
        i7 = next(r for r in rep.results if r.id == "I7")
        assert i7.outcome == "PASS", i7.detail
        assert "1 active filter(s) named on it" in i7.detail
        assert "3 gene(s) filtered into the ledger" in i7.detail

    def test_fails_when_the_spokes_are_not_in_the_order_the_figure_claims(
            self, tmp_path: Path) -> None:
        """The order is re-derived from values.csv, so a figure that *says*
        alphabetical and draws something else fails here instead of being
        believed. GENES is already alphabetical, so this draws it backwards
        under an alphabetical caption."""
        note = "spoke order: alphabetical by gene symbol"
        backwards = list(reversed(GENES))
        spec = _spec()
        res = render(genes=backwards, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Lung", out_svg=tmp_path / "w.svg", input_count=4,
                     rendered_count=4, ledger_counts={}, ordering_note=note)
        rep = check_all(svg_path=tmp_path / "w.svg", csv_rows=res.csv_rows,
                        gene_order=backwards, input_count=4, rendered_count=4,
                        ledger_count=0,
                        figure_claims=_claims(res, ordering="alphabetical",
                                              submitted_order=backwards))
        i7 = next(r for r in rep.results if r.id == "I7")
        assert i7.outcome == "FAIL"
        assert "spokes are in 'alphabetical' order and they are not" in i7.detail

    def test_the_order_check_reads_the_drawn_values_not_the_ordering_code(
            self, rendered) -> None:
        """A submitted order that disagrees with what was drawn is caught even
        though `order_for_figure` is not consulted for it."""
        i7 = self._run(rendered, _claims(submitted_order=list(reversed(GENES))))
        assert i7.outcome == "FAIL"
        assert "re-derived from values.csv" in i7.detail

    def test_a_single_ring_order_is_checked_against_that_rings_values(
            self, tmp_path: Path) -> None:
        from targetring.order import RING_PREFIX, order_for_figure
        spec = _spec()
        want = order_for_figure(GENES, spec, f"{RING_PREFIX}regional_specificity")
        note = ("spoke order: by the 'regional_specificity' ring, best first; "
                "genes with no value on it are last")
        res = render(genes=want, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Lung", out_svg=tmp_path / "r.svg", input_count=4,
                     rendered_count=4, ledger_counts={}, ordering_note=note)
        claims = _claims(res, ordering=f"{RING_PREFIX}regional_specificity")
        rep = check_all(svg_path=tmp_path / "r.svg", csv_rows=res.csv_rows,
                        gene_order=want, input_count=4, rendered_count=4,
                        ledger_count=0, figure_claims=claims)
        assert next(r for r in rep.results if r.id == "I7").outcome == "PASS"
        assert want != list(GENES), "otherwise this test proves nothing"

    def test_absent_claims_are_not_evaluated_not_passed(self, rendered) -> None:
        i7 = self._run(rendered, None)
        assert i7.outcome == "NOT_EVALUATED"

    def test_i7_is_in_the_manifest_pass_fail_list(self, rendered) -> None:
        res, svg = rendered
        rep = check_all(svg_path=svg, csv_rows=res.csv_rows, gene_order=GENES,
                        input_count=6, rendered_count=4, ledger_count=2,
                        regression={"ok": True, "detail": "x"},
                        literature_audit={"echo_live": 1, "ncbi_checked": 1,
                                          "echo_mismatches": [], "ncbi_errors": []},
                        figure_claims=_claims(res))
        ids = [r["id"] for r in rep.as_json()]
        assert ids == ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]
        assert rep.ok, rep.banner_lines() + rep.condition_lines()

    def test_no_diamond_is_drawn_on_any_figure_any_more(self, tmp_path: Path) -> None:
        """The readback that used to name the front's genes now proves the set
        is empty, on a figure large enough that v2.4 would have drawn one."""
        from targetring.invariants import headline_markers
        from targetring.states import value as v
        n = 12
        genes = [f"G{i:02d}" for i in range(n)]
        spec = {
            "tractable": {g: v(i / (n - 1)) for i, g in enumerate(genes)},
            "safety": {g: v(2 * i / (n - 1) - 1) for i, g in enumerate(genes)},
            "regional_specificity": {g: v(1.0 + i) for i, g in enumerate(genes)},
            "clinical_phase": {g: v(float(i % 6)) for i, g in enumerate(genes)},
            "tdl": {g: v(float(i % 4)) for i, g in enumerate(genes)},
        }
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec), region="Lung",
               out_svg=tmp_path / "d.svg", input_count=n, rendered_count=n,
               ledger_counts={}, ordering_note=ORDER_NOTE,
               gene_flags={genes[-1]: ["Torsades de Point"]})
        assert headline_markers(tmp_path / "d.svg") == []


class TestManifestCarriesTheRedBannerQuestion:
    """QA-v2-retest §4. V2-REPORT §3.1 said the red-banner/amber split was "in
    the manifest (`any_invariant_failed`, `run_conditions`)". `run_conditions`
    was; `any_invariant_failed` and `all_invariants_pass` were `@property` on
    `Manifest` and `asdict()` drops properties, so neither reached
    `manifest.json`. The values were recoverable from the `invariants` array —
    nothing on disk was wrong — but the specific promise, that a screenshot's
    manifest answers "did anything fail?" in one field, was unmet.
    """

    def _manifest(self, outcomes, tmp_path):
        import json

        from targetring import manifest as m
        man = m.Manifest(
            run_id="T", timestamp_utc="now", git_sha="deadbeef", region="Liver",
            input_row_count=1, rendered_count=1, ledger_count=0,
            invariants=[{"id": f"I{i}", "outcome": o, "detail": ""}
                        for i, o in enumerate(outcomes, start=1)],
        )
        return man, json.loads(man.write(tmp_path / "manifest.json").read_text())

    def test_both_keys_are_written(self, tmp_path: Path) -> None:
        man, on_disk = self._manifest(["PASS"] * 7, tmp_path)
        assert "any_invariant_failed" in on_disk, "the §3.1 claim, now true"
        assert "all_invariants_pass" in on_disk
        assert on_disk["any_invariant_failed"] is False
        assert on_disk["all_invariants_pass"] is True

    def test_a_failure_is_visible_in_the_one_field(self, tmp_path: Path) -> None:
        _, on_disk = self._manifest(["PASS", "FAIL"] + ["PASS"] * 5, tmp_path)
        assert on_disk["any_invariant_failed"] is True
        assert on_disk["all_invariants_pass"] is False

    def test_not_evaluated_is_not_a_failure(self, tmp_path: Path) -> None:
        """UX A1's whole point: a source being down and an invariant being
        violated are different things, and the red banner answers only the
        second. NOT_EVALUATED makes `all_invariants_pass` false and must leave
        `any_invariant_failed` false."""
        _, on_disk = self._manifest(["PASS"] * 6 + ["NOT_EVALUATED"], tmp_path)
        assert on_disk["any_invariant_failed"] is False
        assert on_disk["all_invariants_pass"] is False

    def test_the_file_cannot_drift_from_the_object(self, tmp_path: Path) -> None:
        man, on_disk = self._manifest(["FAIL"] + ["PASS"] * 6, tmp_path)
        assert on_disk["any_invariant_failed"] == man.any_invariant_failed
        assert on_disk["all_invariants_pass"] == man.all_invariants_pass
