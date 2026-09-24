"""COUNCIL V6, V7, V9, V10, V12, V13, V17 and the greyscale finding.

All of these are the same class of defect: the figure's entire content is
colour, and v1's per-ring min-max normalisation made a colour mean something
different on every ring and in every run.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from targetring.figure import (
    ACTIVITY_PALETTE,
    CMAP_STOPS,
    GREYSCALE_MIN_DELTA,
    PARETO_MIN_ELIGIBLE,
    RING_SPECS,
    STATE_STYLE,
    _norm_for,
    _scaled,
    assert_fills_disjoint,
    assert_greyscale_disjoint,
    colormap_hexes,
    relative_luminance,
    render,
    value_cmap,
)
from targetring.rank import EXTERNAL_RING_COUNT, EXTERNAL_RINGS, rank_genes
from targetring.states import no_data, value

SPEC = {s.key: s for s in RING_SPECS}

def _axes_spec(genes):
    """Four Pareto axes, strictly ordered, every value inside its declared
    domain so `assert_within_domain` is not tripped by synthetic data."""
    n = len(genes)
    return {
        "druggability": {g: value(i / (n - 1)) for i, g in enumerate(genes)},
        "safety": {g: value(2 * i / (n - 1) - 1) for i, g in enumerate(genes)},
        "regional_specificity": {g: value(1.0 + i) for i, g in enumerate(genes)},
        "clinical_phase": {g: value(float(i % 6)) for i, g in enumerate(genes)},
    }



def _fill(ring_key: str, cells: dict) -> dict[str, str]:
    """The fill each gene's cell would get on this ring."""
    from matplotlib.colors import to_hex
    spec = SPEC[ring_key]
    norm = _norm_for(spec, cells)
    cmap = value_cmap()
    out = {}
    for g, c in cells.items():
        if c.state != "value":
            continue
        if spec.categorical:
            out[g] = spec.categorical[round(c.value, 6)]
        else:
            out[g] = to_hex(cmap(norm(_scaled(spec, c.value))))
    return out


class TestV6ScalesMeanSomething:
    """V6: "9 of 14 cells are the same darkest purple, so HMGCR (0.93, i.e.
    exactly average for liver) is indistinguishable from KCNH2 (0.023)" — over a
    41-fold range of ratios. And the k ring spent the entire colormap on a 7-to-9
    difference."""

    LIVER = {  # the council's measured regional_specificity values
        "TTR": 5203.97, "APOB": 2463.47, "SERPINA1": 1827.17, "ASGR1": 295.86,
        "PCSK9": 196.11, "HMGCR": 0.928, "EPHA2": 0.721, "TP53": 0.468,
        "ACTB": 0.344, "BRAF": 0.317, "GFAP": 0.270, "TMEM229B": 0.152,
        "DDR1": 0.052, "KCNH2": 0.023,
    }

    def test_the_forty_one_fold_range_is_no_longer_one_pixel(self) -> None:
        cells = {g: value(v) for g, v in self.LIVER.items()}
        fills = _fill("regional_specificity", cells)
        collapsed = [g for g in ("HMGCR", "EPHA2", "TP53", "ACTB", "BRAF",
                                 "GFAP", "TMEM229B", "DDR1", "KCNH2")]
        distinct = {fills[g] for g in collapsed}
        assert len(distinct) >= 7, f"v1 had 1 colour for these nine; now {len(distinct)}"
        assert fills["HMGCR"] != fills["KCNH2"]

    def test_a_ratio_ring_is_log_scaled(self) -> None:
        assert SPEC["regional_specificity"].scale == "log"
        # An order of magnitude is a fixed distance, wherever it sits.
        a = _scaled(SPEC["regional_specificity"], 1.0)
        b = _scaled(SPEC["regional_specificity"], 10.0)
        c = _scaled(SPEC["regional_specificity"], 100.0)
        assert b - a == pytest.approx(c - b) == pytest.approx(1.0)

    @pytest.mark.parametrize("ring", ["druggability", "safety", "clinical_phase",
                                      "rodent", "completeness"])
    def test_a_bounded_ring_has_a_fixed_domain(self, ring: str) -> None:
        """So a colour means the same thing between two runs."""
        assert SPEC[ring].domain is not None

    def test_the_unbounded_ratio_ring_deliberately_has_none(self) -> None:
        """A fixed domain would clip TTR (5,204x), APOB (2,463x) and SERPINA1
        (1,827x) to one colour — V6's collapse, moved to the top end. The log
        transform is what fixes the collapse; the range goes in the key."""
        assert SPEC["regional_specificity"].domain is None
        assert SPEC["regional_specificity"].scale == "log"
        cells = {g: value(v) for g, v in self.LIVER.items()}
        fills = _fill("regional_specificity", cells)
        assert len({fills[g] for g in ("TTR", "APOB", "SERPINA1")}) == 3

    @pytest.mark.parametrize("ring", ["druggability", "safety", "clinical_phase"])
    def test_a_fixed_domain_does_not_move_with_the_submission(self, ring: str) -> None:
        small = {"A": value(0.0), "B": value(0.2)}
        big = {"A": value(0.0), "B": value(0.2), "C": value(1.0) if ring != "safety"
               else value(1.0), "D": value(-1.0) if ring == "safety" else value(5.0)
               if ring == "clinical_phase" else value(1.0)}
        assert _fill(ring, small)["B"] == _fill(ring, big)["B"]

    def test_the_k_ring_no_longer_spends_the_colormap_on_a_two_step_difference(self) -> None:
        """v1: "the same rule spends the entire colormap on a 7->9 difference,
        so TMEM229B's k cell is maximally dark"."""
        from matplotlib.colors import to_hex
        cmap = value_cmap()
        cells = {"A": value(5.0), "B": value(6.0), "C": value(7.0)}
        fills = _fill("completeness", cells)
        assert fills["A"] != to_hex(cmap(0.0)), \
            "5 of 7 is not the bottom of the scale just because it is the lowest here"
        # A two-step difference on a 0..7 domain covers 2/7 of the ramp, not all.
        assert fills["C"] == to_hex(cmap(1.0)), "7 of 7 is the top, and genuinely is"
        norm = _norm_for(SPEC["completeness"], cells)
        assert norm(5.0) == pytest.approx(5 / 7)


class TestFixedDomainsAreNeverSilentlyClipped:
    """A clipped cell is drawn in a colour that means a different number."""

    @pytest.mark.parametrize("ring,bad", [
        ("druggability", 1.4), ("safety", -1.2), ("clinical_phase", 6.0),
        ("completeness", 9.0), ("rodent", 2.0),
    ])
    def test_an_out_of_range_value_raises_rather_than_clipping(self, ring, bad) -> None:
        from targetring.figure import assert_within_domain
        with pytest.raises(AssertionError, match="outside this ring's declared domain"):
            assert_within_domain(SPEC[ring], {"X": value(bad)})

    def test_the_declared_domains_cover_what_the_adapters_can_produce(self) -> None:
        """druggability is a mean of keys in {0,1}; safety a mean of keys in
        [-1,1]; clinical_phase the stage ordinal; rodent three states; k the
        count of external rings."""
        assert SPEC["druggability"].domain == (0.0, 1.0)
        assert SPEC["safety"].domain == (-1.0, 1.0)
        from targetring.adapters.opentargets import CLINICAL_STAGE_ORDINAL
        assert SPEC["clinical_phase"].domain == (0.0, float(max(CLINICAL_STAGE_ORDINAL.values())))
        assert SPEC["rodent"].domain == (0.0, 1.0)
        assert SPEC["completeness"].domain == (0.0, float(EXTERNAL_RING_COUNT))


class TestV13KCountsExternalRingsOnly:
    """V13: "`rank_genes` is called before `completeness` is added, over nine
    rings of which `logfc` and `neg_log10_padj` come from the submitted table
    and are therefore never missing. Every gene in every run I did has k >= 2 for
    free… TMEM229B, a gene about which essentially nothing is known, shows k=7.""" 

    def test_the_users_own_columns_do_not_count(self) -> None:
        spec = {
            "logfc": {"A": value(1.0), "B": value(2.0)},
            "neg_log10_padj": {"A": value(3.0), "B": value(4.0)},
            "druggability": {"A": value(0.5), "B": no_data("x")},
        }
        out = rank_genes(spec)
        assert out["A"].k == 1, "logfc and padj are his measurement, not a source's"
        assert out["B"].k == 0, "a gene nothing is known about must be able to reach 0"

    def test_the_maximum_is_the_number_of_external_rings(self) -> None:
        assert EXTERNAL_RING_COUNT == 7
        assert "logfc" not in EXTERNAL_RINGS and "neg_log10_padj" not in EXTERNAL_RINGS
        assert "completeness" not in EXTERNAL_RINGS
        assert SPEC["completeness"].domain == (0.0, float(EXTERNAL_RING_COUNT))

    def test_the_figure_states_the_denominator(self, tmp_path: Path) -> None:
        spec = {"logfc": {"A": value(1.0), "B": value(2.0)},
                "druggability": {"A": value(0.5), "B": value(0.9)}}
        render(genes=["A", "B"], cells_by_ring=spec, rankings=rank_genes(spec),
               region="Liver", out_svg=tmp_path / "k.svg", input_count=2,
               rendered_count=2, ledger_counts={})
        body = (tmp_path / "k.svg").read_text()
        assert f"(k=1/{EXTERNAL_RING_COUNT})" in body
        assert "EXTERNAL rings" in body


class TestV12CategoricalActivityRing:
    """V12: "Encoded -1 / 0 / +1. In the liver run only {0, 1} occur, so the norm
    is [0, 1] and 'dormant' renders #440154 — the same darkest purple that means
    '0 drugs' on the phase ring one band away." """

    def test_the_colour_does_not_depend_on_the_other_genes(self) -> None:
        two = {"A": value(0.0), "B": value(1.0)}
        three = {"A": value(0.0), "B": value(1.0), "C": value(-1.0)}
        assert _fill("clinical_activity", two)["A"] == \
            _fill("clinical_activity", three)["A"]

    def test_its_colours_are_outside_the_value_colormap(self) -> None:
        ramp = colormap_hexes()
        for colour in ACTIVITY_PALETTE.values():
            assert colour not in ramp

    def test_its_colours_are_outside_the_reserved_state_fills(self) -> None:
        reserved = {st["fc"] for st in STATE_STYLE.values()}
        assert not (set(ACTIVITY_PALETTE.values()) & reserved)

    def test_the_three_states_are_named_in_the_legend(self, tmp_path: Path) -> None:
        spec = {"logfc": {g: value(1.0 * i) for i, g in enumerate("ABC")},
                "clinical_activity": {"A": value(-1.0), "B": value(0.0), "C": value(1.0)}}
        render(genes=list("ABC"), cells_by_ring=spec, rankings=rank_genes(spec),
               region="Liver", out_svg=tmp_path / "a.svg", input_count=3,
               rendered_count=3, ledger_counts={})
        body = (tmp_path / "a.svg").read_text()
        for label in ("stopped for cause", "dormant", "active"):
            assert label in body


class TestV17SignedRingHasAZeroAnchor:
    """V17: "GFAP at -3.3 renders #440154 (darkest) and TTR at +3.1 renders
    #fde725 (brightest), so a strongly down-regulated gene reads as 'worst' on
    the ring where every other ring uses dark for 'worst'." """

    def test_zero_sits_at_the_midpoint_of_the_ramp(self) -> None:
        from matplotlib.colors import to_hex
        cells = {"down": value(-3.3), "flat": value(0.0), "up": value(3.1)}
        assert _fill("logfc", cells)["flat"] == to_hex(value_cmap()(0.5))

    def test_equal_and_opposite_changes_are_equally_far_from_the_middle(self) -> None:
        cells = {"a": value(-2.0), "b": value(2.0), "c": value(0.0)}
        norm = _norm_for(SPEC["logfc"], cells)
        assert norm(-2.0) == pytest.approx(1 - norm(2.0))


class TestV10HeadlineSizeIsStated:
    """V10: "on a 7-gene submission 71% of the figure gets a headline diamond,
    including GAPDH — the canonical housekeeping negative control." """

    def _render(self, tmp_path, n):
        genes = [f"G{i:02d}" for i in range(n)]
        spec = _axes_spec(genes)
        res = render(genes=genes, cells_by_ring=spec, rankings=rank_genes(spec),
                     region="Liver", out_svg=tmp_path / f"n{n}.svg",
                     input_count=n, rendered_count=n, ledger_counts={})
        return res, (tmp_path / f"n{n}.svg").read_text()

    def test_the_diamond_is_suppressed_on_a_short_list(self, tmp_path: Path) -> None:
        res, body = self._render(tmp_path, PARETO_MIN_ELIGIBLE - 3)
        assert res.diamonds_suppressed
        assert "◆ not drawn" in body
        assert "almost nothing is dominated" in body

    def test_the_fraction_is_printed_when_it_is_drawn(self, tmp_path: Path) -> None:
        n = PARETO_MIN_ELIGIBLE + 4
        res, body = self._render(tmp_path, n)
        assert not res.diamonds_suppressed
        assert f"of {n} eligible" in body

    def test_it_says_membership_is_a_property_of_the_list(self, tmp_path: Path) -> None:
        _, body = self._render(tmp_path, PARETO_MIN_ELIGIBLE + 4)
        assert "property of" in body and "not of the target" in body


class TestGreyscaleDisjointness:
    """The UX review's accessibility finding.

    "The `no_data` fill #b3b3b3 has relative luminance 0.4508. Viridis step
    192/255 — #5ec962, a real value at ~0.75 of the scale — has 0.4503. The
    greyscale contrast ratio between them is 1.001: identical. 23 of 256 steps
    (9% of the ramp) sit within 0.05 luminance of the no-data grey."
    """

    def test_the_guarantee_now_survives_desaturation(self) -> None:
        assert_fills_disjoint()
        assert_greyscale_disjoint()

    def test_no_value_colour_is_within_the_threshold_of_any_reserved_fill(self) -> None:
        ramp = [relative_luminance(value_cmap()(i / 255)) for i in range(256)]
        for name, st in STATE_STYLE.items():
            l = relative_luminance(st["fc"])
            closest = min(abs(l - x) for x in ramp)
            assert closest >= GREYSCALE_MIN_DELTA, (name, closest)

    def test_the_three_reserved_fills_separate_from_each_other(self) -> None:
        lums = {n: relative_luminance(st["fc"]) for n, st in STATE_STYLE.items()}
        for a, b in itertools.combinations(lums, 2):
            assert abs(lums[a] - lums[b]) >= GREYSCALE_MIN_DELTA, (a, b)

    def test_the_v1_palette_would_now_fail(self) -> None:
        """A check that has never been seen to fail is not a check."""
        import targetring.figure as F
        old = dict(F.STATE_STYLE["no_data"])
        try:
            F.STATE_STYLE["no_data"] = {**old, "fc": "#b3b3b3"}
            # It still passes the colour check — that is the whole point.
            assert_fills_disjoint()
            with pytest.raises(AssertionError, match="indistinguishable from a real"):
                assert_greyscale_disjoint()
        finally:
            F.STATE_STYLE["no_data"] = old

    def test_the_truncation_is_what_makes_room(self) -> None:
        lo, hi = CMAP_STOPS
        assert lo > 0.0 and hi < 1.0
        ramp = [relative_luminance(value_cmap()(i / 255)) for i in range(256)]
        assert min(ramp) > GREYSCALE_MIN_DELTA, "black must clear the dark end"
        assert max(ramp) < 1.0 - 2 * GREYSCALE_MIN_DELTA, "white and near-white need room"

    def test_the_ramp_is_still_monotone_in_luminance(self) -> None:
        """Truncating must not have broken the thing that makes viridis readable."""
        ramp = [relative_luminance(value_cmap()(i / 255)) for i in range(256)]
        assert all(b >= a - 1e-6 for a, b in zip(ramp, ramp[1:]))
