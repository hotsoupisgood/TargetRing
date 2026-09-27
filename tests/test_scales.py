"""COUNCIL V6, V7, V9, V10, V12, V13, V17 and the greyscale finding.

All of these are the same class of defect: the figure's entire content is
colour, and v1's per-ring min-max normalisation made a colour mean something
different on every ring and in every run.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from targetring.adapters.opentargets import SOURCE_LABEL as ACTIVITY_SOURCE_LABEL
from targetring.adapters.pharos import TDL_MAX
from targetring.figure import (
    ACTIVITY_PALETTE,
    CMAP_LUM,
    GREYSCALE_MIN_DELTA,
    RING_SPECS,
    STATE_STYLE,
    _norm_for,
    _scaled,
    assert_fills_disjoint,
    assert_greyscale_disjoint,
    assert_ramp_luminance_is_monotone,
    assert_rings_are_distinguishable,
    colormap_hexes,
    relative_luminance,
    render,
    ring_cmap,
    ring_swatches,
    value_cmap,
)
from targetring.palette import FAMILIES, all_value_hexes
from targetring.order import EXTERNAL_RING_COUNT, EXTERNAL_RINGS, gene_stats
from targetring.states import no_data, value

SPEC = {s.key: s for s in RING_SPECS}

ORDER_NOTE = "spoke order: the order of your submitted DE table (unchanged)"


def _axes_spec(genes):
    """Five rings, strictly ordered, every value inside its declared domain so
    `assert_within_domain` is not tripped by synthetic data."""
    n = len(genes)
    spec = {
        "tractable": {g: value(i / (n - 1)) for i, g in enumerate(genes)},
        "safety": {g: value(2 * i / (n - 1) - 1) for i, g in enumerate(genes)},
        "regional_specificity": {g: value(1.0 + i) for i, g in enumerate(genes)},
        "clinical_phase": {g: value(float(i % 6)) for i, g in enumerate(genes)},
        "tdl": {g: value(float(i % (TDL_MAX + 1))) for i, g in enumerate(genes)},
    }
    return spec



def _fill(ring_key: str, cells: dict) -> dict[str, str]:
    """The fill each gene's cell would get on this ring.

    v2.6: the colormap is the *ring's*, not one shared ramp. This mirrors the
    render path in `figure.render` rather than approximating it, which is the
    only way these tests keep testing the figure people get.
    """
    from matplotlib.colors import to_hex
    spec = SPEC[ring_key]
    norm = _norm_for(spec, cells)
    cmap = ring_cmap(spec)
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

    @pytest.mark.parametrize("ring", ["tractable", "chemical_probe", "safety",
                                      "clinical_phase", "rodent", "completeness"])
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

    @pytest.mark.parametrize("ring", ["tractable", "chemical_probe", "safety", "clinical_phase"])
    def test_a_fixed_domain_does_not_move_with_the_submission(self, ring: str) -> None:
        small = {"A": value(0.0), "B": value(0.2)}
        big = {"A": value(0.0), "B": value(0.2), "C": value(1.0) if ring != "safety"
               else value(1.0), "D": value(-1.0) if ring == "safety" else value(5.0)
               if ring == "clinical_phase" else value(1.0)}
        assert _fill(ring, small)["B"] == _fill(ring, big)["B"]

    def test_the_k_ring_no_longer_spends_the_colormap_on_a_two_step_difference(self) -> None:
        """v1: "the same rule spends the entire colormap on a 7->9 difference,
        so TMEM229B's k cell is maximally dark"."""
        top = float(EXTERNAL_RING_COUNT)
        swatches = ring_swatches(SPEC["completeness"])
        cells = {"A": value(top - 2), "B": value(top - 1), "C": value(top)}
        fills = _fill("completeness", cells)
        assert fills["A"] != swatches[0], \
            "k-2 is not the bottom of the scale just because it is the lowest here"
        # A two-step difference covers 2/K of the ramp, not all of it.
        assert fills["C"] == swatches[-1], "K of K is the top, and genuinely is"
        norm = _norm_for(SPEC["completeness"], cells)
        assert norm(top - 2) == pytest.approx((top - 2) / top)


class TestFixedDomainsAreNeverSilentlyClipped:
    """A clipped cell is drawn in a colour that means a different number."""

    @pytest.mark.parametrize("ring,bad", [
        ("tractable", 1.4), ("chemical_probe", 1.4), ("safety", -1.2),
        ("clinical_phase", 6.0), ("completeness", 10.0), ("rodent", 2.0),
    ])
    def test_an_out_of_range_value_raises_rather_than_clipping(self, ring, bad) -> None:
        from targetring.figure import assert_within_domain
        with pytest.raises(AssertionError, match="outside this ring's declared domain"):
            assert_within_domain(SPEC[ring], {"X": value(bad)})

    def test_the_declared_domains_cover_what_the_adapters_can_produce(self) -> None:
        """tractability is a mean of 3 keys in {0,1}; safety a mean of keys in
        [-1,1]; clinical_phase the stage ordinal; rodent three states; k the
        count of external rings."""
        assert SPEC["tractable"].domain == (0.0, 1.0)
        assert SPEC["chemical_probe"].domain == (0.0, 1.0)
        assert SPEC["safety"].domain == (-1.0, 1.0)
        from targetring.adapters.opentargets import CLINICAL_STAGE_ORDINAL
        assert SPEC["clinical_phase"].domain == (0.0, float(max(CLINICAL_STAGE_ORDINAL.values())))
        assert SPEC["rodent"].domain == (0.0, 1.0)
        assert SPEC["completeness"].domain == (0.0, float(EXTERNAL_RING_COUNT))


class TestV13KCountsExternalRingsOnly:
    """V13: "k was counted before `completeness` was added, over nine
    rings of which `logfc` and `neg_log10_padj` come from the submitted table
    and are therefore never missing. Every gene in every run I did has k >= 2 for
    free… TMEM229B, a gene about which essentially nothing is known, shows k=7.""" 

    def test_the_users_own_columns_do_not_count(self) -> None:
        spec = {
            "logfc": {"A": value(1.0), "B": value(2.0)},
            "neg_log10_padj": {"A": value(3.0), "B": value(4.0)},
            "tractable": {"A": value(0.5), "B": no_data("x")},
        }
        out = gene_stats(spec)
        assert out["A"].k == 1, "logfc and padj are his measurement, not a source's"
        assert out["B"].k == 0, "a gene nothing is known about must be able to reach 0"

    def test_the_maximum_is_the_number_of_external_rings(self) -> None:
        # v2.2 item 4 added `tdl`, the eighth external ring; v2.3 item 1 split
        # druggability into `tractable` + `chemical_probe`, which makes nine.
        assert EXTERNAL_RING_COUNT == 9
        assert "tdl" in EXTERNAL_RINGS
        assert {"tractable", "chemical_probe"} <= set(EXTERNAL_RINGS)
        assert "logfc" not in EXTERNAL_RINGS and "neg_log10_padj" not in EXTERNAL_RINGS
        assert "completeness" not in EXTERNAL_RINGS
        assert SPEC["completeness"].domain == (0.0, float(EXTERNAL_RING_COUNT))

    def test_the_figure_states_the_denominator(self, tmp_path: Path) -> None:
        spec = {"logfc": {"A": value(1.0), "B": value(2.0)},
                "tractable": {"A": value(0.5), "B": value(0.9)}}
        render(genes=["A", "B"], cells_by_ring=spec, stats=gene_stats(spec),
               region="Liver", out_svg=tmp_path / "k.svg", input_count=2,
               rendered_count=2, ledger_counts={}, ordering_note=ORDER_NOTE)
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

    def test_its_colours_are_outside_every_value_ramp(self) -> None:
        """v2.5 had one ramp to be outside of. v2.6 has twelve families, and the
        claim is the same and stronger: a three-state categorical must not be a
        position on any of them."""
        ramps = {h for fam in FAMILIES.values() for h in fam.hexes}
        ramps |= {h for spec in RING_SPECS if spec.levels
                  for h in ring_swatches(spec)}
        for colour in ACTIVITY_PALETTE.values():
            assert colour not in ramps

    def test_its_colours_are_outside_the_reserved_state_fills(self) -> None:
        reserved = {st["fc"] for st in STATE_STYLE.values()}
        assert not (set(ACTIVITY_PALETTE.values()) & reserved)

    def test_the_three_states_are_named_in_the_legend(self, tmp_path: Path) -> None:
        spec = {"logfc": {g: value(1.0 * i) for i, g in enumerate("ABC")},
                "clinical_activity": {"A": value(-1.0), "B": value(0.0), "C": value(1.0)}}
        render(genes=list("ABC"), cells_by_ring=spec, stats=gene_stats(spec),
               region="Liver", out_svg=tmp_path / "a.svg", input_count=3,
               rendered_count=3, ledger_counts={}, ordering_note=ORDER_NOTE)
        body = (tmp_path / "a.svg").read_text()
        for label in ("stopped for cause", "dormant", "live trial"):
            assert label in body
        # Puck's first hard condition, unchanged: the ring must state which
        # source it counted, on the figure. v2.4 changed the answer back to Open
        # Targets' own clinicalReports rows, so that is what must appear.
        assert ACTIVITY_SOURCE_LABEL in body
        assert "stopped for cause" in body, (
            "Open Targets categorises stop reasons, so the -1 state says what it "
            "means again -- and the ~7-of-8 administrative stops are NOT it "
            "(COUNCIL D4, v2.4 item 2)")


class TestV17SignedRingHasAZeroAnchor:
    """V17: "GFAP at -3.3 renders #440154 (darkest) and TTR at +3.1 renders
    #fde725 (brightest), so a strongly down-regulated gene reads as 'worst' on
    the ring where every other ring uses dark for 'worst'." """

    def test_zero_sits_at_the_midpoint_of_the_ramp(self) -> None:
        from matplotlib.colors import to_hex
        cells = {"down": value(-3.3), "flat": value(0.0), "up": value(3.1)}
        assert _fill("logfc", cells)["flat"] == to_hex(ring_cmap(SPEC["logfc"])(0.5))

    def test_equal_and_opposite_changes_are_equally_far_from_the_middle(self) -> None:
        cells = {"a": value(-2.0), "b": value(2.0), "c": value(0.0)}
        norm = _norm_for(SPEC["logfc"], cells)
        assert norm(-2.0) == pytest.approx(1 - norm(2.0))


class TestV10TheHeadlineIsGoneFromTheArtefact:
    """V10 measured that on a 7-gene submission 71% of the figure got a headline
    diamond, including GAPDH — the canonical housekeeping negative control — and
    asked for the fraction to be printed and the diamond suppressed below a
    stated n.

    v2.5 answers it by deletion: there is no headline set on any list length, so
    the suppression rule, the floor and the printed fraction all go. What is
    checked here is that the artefact agrees — no diamond at any n, and the thing
    the spoke order *does* mean is stated instead.
    """

    def _render(self, tmp_path, n):
        genes = [f"G{i:02d}" for i in range(n)]
        spec = _axes_spec(genes)
        res = render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                     region="Liver", out_svg=tmp_path / f"n{n}.svg",
                     input_count=n, rendered_count=n, ledger_counts={},
                     ordering_note=ORDER_NOTE)
        return res, (tmp_path / f"n{n}.svg").read_text()

    @pytest.mark.parametrize("n", [7, 14])
    def test_no_diamond_at_any_list_length(self, tmp_path: Path, n: int) -> None:
        _, body = self._render(tmp_path, n)
        assert "\u25c6" not in body
        assert "eligible" not in body

    def test_the_spoke_order_is_stated_instead(self, tmp_path: Path) -> None:
        _, body = self._render(tmp_path, 7)
        assert ORDER_NOTE in body


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
        """v2.6: "any value colour" is now the union over twelve families, not
        256 steps of one ramp. That is the whole point -- a reserved fill has to
        be outside all of them."""
        ramp = [relative_luminance(h) for h in all_value_hexes()]
        assert len(ramp) > 1500, "this is the union over every ring, not one ramp"
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

    def test_one_luminance_envelope_is_what_makes_room(self) -> None:
        """v2.5 made room by truncating one ramp to [0.16, 0.92]. v2.6 has
        twelve, so the room is made by *construction*: every family is built
        inside `CMAP_LUM`, and the three reserved fills sit outside it."""
        lo, hi = CMAP_LUM
        assert lo >= GREYSCALE_MIN_DELTA, "black must clear the dark end"
        assert hi < 1.0 - 2 * GREYSCALE_MIN_DELTA, "white and near-white need room"
        ramp = [relative_luminance(h) for h in all_value_hexes()]
        assert min(ramp) >= lo - 2e-3 and max(ramp) <= hi + 2e-3, (
            "a family escaped the shared luminance envelope, which is how twelve "
            "colormaps would reopen the collision one truncated ramp closed")

    def test_every_ramp_is_monotone_in_luminance(self) -> None:
        """Within a ring, darker must always mean more -- on every family, and
        on each arm of a diverging one. v3.4 flipped this; before it, darker
        meant less."""
        assert_ramp_luminance_is_monotone()
        for name, fam in FAMILIES.items():
            if fam.kind != "sequential":
                continue
            lums = [relative_luminance(h) for h in fam.hexes]
            assert all(b <= a + 1e-6 for a, b in zip(lums, lums[1:])), name

    def test_the_low_end_is_pale_and_desaturated_and_the_high_end_is_not(self) -> None:
        """v3.4, Puck's ask: low reads washed out, high reads saturated and
        dark. Asserted on both channels, because lightness alone was what the
        ramp already had and it is not what he asked for."""
        from targetring.palette import oklch_from_hex
        for name, fam in FAMILIES.items():
            if fam.kind != "sequential":
                continue
            lums = [relative_luminance(h) for h in fam.hexes]
            assert lums[0] > lums[-1], f"{name}: the low end is not the pale one"
            c_lo = oklch_from_hex(fam.hexes[0])[1]
            c_hi = oklch_from_hex(fam.hexes[-1])[1]
            assert c_hi > c_lo, f"{name}: the high end is not the more saturated one"

    def test_the_pale_end_keeps_enough_chroma_to_read_as_a_measurement(self) -> None:
        """The guard on the flip. A low value drawn at zero chroma sits on the
        neutral axis beside the hatched grey `no_data` fill, and rule 1 says
        missing is not low. The floor is what stops the pale end drifting
        there."""
        from targetring.palette import LOW_END_CHROMA, oklch_from_hex
        for name, fam in FAMILIES.items():
            if fam.kind != "sequential":
                continue
            c_lo = oklch_from_hex(fam.hexes[0])[1]
            assert c_lo >= LOW_END_CHROMA - 1e-3, (
                f"{name}: the pale end fell to chroma {c_lo:.4f}, below the "
                f"{LOW_END_CHROMA} floor that keeps it off the neutral axis")

    def test_the_check_would_catch_a_family_that_left_the_envelope(self) -> None:
        """A check that has never been seen to fail is not a check."""
        import targetring.palette as P
        from targetring.palette import Family, sequential_hexes
        old = P.FAMILIES["padj"]
        try:
            P.FAMILIES["padj"] = Family(
                "escaped", "sequential",
                tuple(sequential_hexes("rose", lum=(0.075, 0.82))), "")
            with pytest.raises(AssertionError,
                               match="indistinguishable from a real measurement"):
                assert_greyscale_disjoint()
        finally:
            P.FAMILIES["padj"] = old


class TestTheKeyDoesNotMisstateItsOwnRings:
    """Caught on the v2.2 screenshot retake: the categorical activity ring has
    no Normalize, so the key printed "Pipeline: current activity
    (ClinicalTrials.gov v2) no values" beside a ring full of drawn cells. A
    figure must not make a false claim about its own data."""

    def test_a_categorical_ring_names_its_states_not_no_values(self) -> None:
        from targetring.figure import _range_label
        spec = next(r for r in RING_SPECS if r.key == "clinical_activity")
        label = _range_label(spec, None)
        assert "no values" not in label
        assert str(len(spec.categorical)) in label

    def test_a_continuous_ring_with_nothing_drawn_still_says_no_values(self) -> None:
        from targetring.figure import _range_label
        spec = next(r for r in RING_SPECS if r.key == "tdl")
        assert _range_label(spec, None) == "no values"

    def test_every_ring_states_its_units(self) -> None:
        """v2.6. A range is a pair of numbers; without the units it is a pair of
        numbers in whatever a reader assumes."""
        for spec in RING_SPECS:
            assert spec.units, spec.key
            assert spec.short_source, spec.key

    def test_a_transformed_domain_prints_its_transform(self) -> None:
        """V6's actual instruction: if a domain is changed, say so on the scale.
        These four rings do not colour the raw number."""
        for key in ("regional_specificity", "neg_log10_padj", "literature", "logfc"):
            assert SPEC[key].transform, key

    def test_the_rendered_key_says_it(self, tmp_path: Path) -> None:
        spec = {"logfc": {g: value(1.0 * i) for i, g in enumerate("ABC")},
                "clinical_activity": {"A": value(-1.0), "B": value(0.0), "C": value(1.0)}}
        render(genes=list("ABC"), cells_by_ring=spec, stats=gene_stats(spec),
               region="Liver", out_svg=tmp_path / "k2.svg", input_count=3,
               rendered_count=3, ledger_counts={}, ordering_note=ORDER_NOTE)
        body = (tmp_path / "k2.svg").read_text()
        # v2.6: the ring key this used to check is gone -- each ring has its own
        # scale now, and the activity ring's scale draws its three swatches with
        # their three levels under them. The claim is unchanged: a ring full of
        # drawn cells must never be described as having none.
        assert "three named states" in body
        assert "no values in this run" not in body


class TestV26EncodingFollowsTheDataType:
    """UX A5 and COUNCIL V6/V12/V17, taken together.

    v2.5 answered the *scale* half of these findings and left the *colour* half:
    twelve rings on one viridis ramp, so ring 3's yellow and ring 7's yellow
    were the same pixel and meant unrelated things. What is checked here is that
    each ring's colour now follows what its number is.
    """

    def test_the_signed_rings_diverge_and_nothing_else_does(self) -> None:
        """V17. A sequential ramp on a signed quantity puts 'strongly
        down-regulated' at the end of the scale where every other ring puts
        'worst'."""
        diverging = {k for k, s in SPEC.items()
                     if s.family and FAMILIES[s.family].kind == "diverging"}
        assert diverging == {"logfc", "safety"}

    def test_equal_and_opposite_are_equally_far_from_the_middle_in_colour(self) -> None:
        """Not just in the norm, which is what v2.5 fixed, but in the luminance
        a reader actually sees."""
        from targetring.palette import relative_luminance
        from matplotlib.colors import to_rgb
        for key in ("logfc", "safety"):
            ramp = FAMILIES[SPEC[key].family].hexes
            for d in (0, 10, 40, 100):
                lo = relative_luminance(to_rgb(ramp[d]))
                hi = relative_luminance(to_rgb(ramp[len(ramp) - 1 - d]))
                assert abs(lo - hi) < 0.02, (key, d, lo, hi)

    @pytest.mark.parametrize("ring,levels", [
        ("chemical_probe", 2), ("clinical_phase", 6), ("tdl", TDL_MAX + 1),
        ("rodent", 2),
    ])
    def test_a_few_level_ring_gets_that_many_colours(self, ring, levels) -> None:
        """A four-step ordinal drawn on a 256-step ramp offers a reader
        positions that do not exist."""
        assert SPEC[ring].levels == levels
        assert len(set(ring_swatches(SPEC[ring]))) == levels

    def test_a_stepped_ring_paints_only_its_own_swatches(self) -> None:
        cells = {f"G{i}": value(float(i)) for i in range(6)}
        assert set(_fill("clinical_phase", cells).values()) <= \
            set(ring_swatches(SPEC["clinical_phase"]))

    def test_the_categorical_ring_has_no_family_and_no_ramp(self) -> None:
        """COUNCIL V12: three states are not three positions on a scale."""
        assert SPEC["clinical_activity"].family == ""
        assert SPEC["clinical_activity"].levels is None
        assert SPEC["clinical_activity"].categorical

    def test_the_gauge_is_the_one_ring_drawn_muted(self) -> None:
        """v2.5 narrowed what k means — a completeness gauge that orders nothing
        and selects nothing. Drawing it at full chroma would make it look like a
        twelfth piece of evidence."""
        from targetring.palette import GAUGE_CHROMA, RAMP_CHROMA
        assert GAUGE_CHROMA < RAMP_CHROMA / 2
        assert SPEC["completeness"].family == "gauge"

    def test_no_colour_is_painted_by_two_rings_that_are_not_comparable(self) -> None:
        """The literal defect, as a check: with one ramp this was true of every
        pair of rings."""
        assert_rings_are_distinguishable()

    def test_the_check_would_catch_two_rings_sharing_a_ramp(self) -> None:
        """A check that has never been seen to fail is not a check. This is
        exactly what v2.5 looked like: two rings that answer different
        questions, painting the same colours."""
        import targetring.palette as P
        old = P.FAMILIES["specificity"]
        try:
            P.FAMILIES["specificity"] = P.FAMILIES["knowledge"]
            with pytest.raises(AssertionError, match="different colour families"):
                assert_rings_are_distinguishable(
                    [SPEC["regional_specificity"], SPEC["literature"]])
        finally:
            P.FAMILIES["specificity"] = old

    def test_the_rings_declared_comparable_are_exactly_the_two_pairs(self) -> None:
        """Sharing a family is a claim that two rings answer the same question.
        Only two pairs make it: the druggability ring v2.3 split in two, and the
        two rings that both measure how much anybody has studied the gene."""
        from collections import defaultdict
        by_family = defaultdict(list)
        for s in RING_SPECS:
            if s.family:
                by_family[s.family].append(s.key)
        shared = {tuple(sorted(v)) for v in by_family.values() if len(v) > 1}
        assert shared == {("chemical_probe", "tractable"), ("literature", "tdl")}


class TestV26TheScalesSayWhatWasDoneToTheDomain:
    """V6's actual instruction, which v2.5 half-answered: log-scale the ratio
    rings, fix the bounded ones — and *print the transform*, because a domain
    that was changed and not stated is a false claim about the data."""

    def test_a_heavy_tailed_ring_is_log_spaced_and_says_so(self) -> None:
        from targetring.figure import _scale_ticks
        spec = SPEC["neg_log10_padj"]
        assert spec.scale == "log1p"
        assert "log" in spec.transform
        # padj = 1 gives -log10 padj = 0, which log10 alone cannot colour.
        assert _scaled(spec, 0.0) == 0.0
        # An outlier at p=1e-300 no longer flattens the rest.
        cells = {"a": value(1.4), "b": value(2.0), "c": value(3.0), "d": value(300.0)}
        fills = _fill("neg_log10_padj", cells)
        assert len(set(fills.values())) == 4

    def test_the_ticks_are_in_the_units_the_source_counted_in(self) -> None:
        """The colour position is log10(1 + records); the tick is records."""
        from targetring.figure import _scale_ticks
        spec = SPEC["literature"]
        cells = {"a": value(1.81), "b": value(5.32)}
        ticks = _scale_ticks(spec, _norm_for(spec, cells))
        assert ticks[0][1] == "64", ticks
        assert ticks[-1][1].startswith("2e"), ticks

    def test_a_diverging_ring_ticks_its_zero(self) -> None:
        from targetring.figure import _scale_ticks
        spec = SPEC["logfc"]
        cells = {"a": value(-3.3), "b": value(3.1)}
        ticks = _scale_ticks(spec, _norm_for(spec, cells))
        assert [t[1] for t in ticks] == ["-3.3", "0", "3.3"]

    def test_a_stepped_ring_ticks_the_middle_of_each_swatch(self) -> None:
        """A tick on the join between two swatches labels neither."""
        from targetring.figure import _scale_ticks
        spec = SPEC["tdl"]
        ticks = _scale_ticks(spec, _norm_for(spec, {"a": value(0.0)}))
        assert [t[1] for t in ticks] == ["0", "1", "2", "3"]
        assert ticks[0][0] == pytest.approx(0.125)
