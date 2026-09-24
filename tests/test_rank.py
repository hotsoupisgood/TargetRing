"""Ranking tests. The load-bearing one is `TestNeverImpute`."""

from __future__ import annotations

import pytest

from targetring.rank import (
    DEFAULT_WEIGHTS,
    pareto_eligible,
    pareto_front,
    rank_genes,
    rank_within_set,
    weighted,
)
from targetring.states import Cell, no_data, not_applicable, value


def cells(**kw) -> dict[str, Cell]:
    return {g: (value(v) if v is not None else no_data("absent")) for g, v in kw.items()}


class TestRankWithinSet:
    def test_ranks_are_relative_to_the_submitted_set(self) -> None:
        """PLAN §3: within the submitted gene set, not a global background."""
        small = rank_within_set(cells(A=1.0, B=2.0, C=3.0), "druggability")
        assert small == {"A": 0.0, "B": 0.5, "C": 1.0}
        # Same absolute value, different set, different rank. That is the point.
        big = rank_within_set(cells(A=1.0, B=100.0), "druggability")
        assert big["A"] == 0.0

    def test_ties_average_rather_than_invent_an_order(self) -> None:
        r = rank_within_set(cells(A=5.0, B=5.0, C=1.0), "druggability")
        assert r["A"] == r["B"] == 0.75
        assert r["C"] == 0.0

    def test_single_gene_does_not_divide_by_zero(self) -> None:
        assert rank_within_set(cells(A=1.0), "druggability") == {"A": 1.0}

    def test_no_values_returns_empty(self) -> None:
        assert rank_within_set(cells(A=None, B=None), "druggability") == {}


class TestNeverImpute:
    """AGENTS.md rule 1 and PLAN §3's last line."""

    def test_missing_genes_are_absent_from_the_ranking_not_ranked_last(self) -> None:
        r = rank_within_set(cells(A=1.0, B=None, C=3.0), "druggability")
        assert "B" not in r
        assert r == {"A": 0.0, "C": 1.0}

    def test_all_four_states_except_value_are_unranked(self) -> None:
        cs = {"A": value(1.0), "B": no_data("x"), "C": not_applicable("y"),
              "D": Cell("failed", None, "z")}
        assert set(rank_within_set(cs, "druggability")) == {"A"}

    def test_k_counts_only_real_measurements(self) -> None:
        out = rank_genes({
            "druggability": cells(A=1.0, B=2.0),
            "safety": {"A": value(1.0), "B": no_data("x")},
            "rodent": {"A": not_applicable("x"), "B": value(3.0)},
        })
        assert out["A"].k == 2
        assert out["B"].k == 2

    def test_a_gene_with_nothing_is_reported_not_dropped(self) -> None:
        out = rank_genes({"druggability": {"A": value(1.0), "Z": no_data("x")}})
        assert "Z" in out
        assert out["Z"].median_rank is None
        assert out["Z"].weighted_score is None
        assert "no ranked ring" in out["Z"].note


class TestPareto:
    def test_dominated_gene_is_excluded(self) -> None:
        ranks = {
            "best": {"druggability": 1.0, "safety": 1.0,
                     "regional_specificity": 1.0, "clinical_phase": 1.0},
            "worst": {"druggability": 0.0, "safety": 0.0,
                      "regional_specificity": 0.0, "clinical_phase": 0.0},
        }
        assert pareto_front(ranks) == {"best"}

    def test_a_tradeoff_keeps_both(self) -> None:
        """Both are complete on all four axes and neither beats the other
        everywhere, so both survive. Eligibility (V2) does not collapse the
        front onto a single winner."""
        ranks = {
            "druggable": {"druggability": 1.0, "safety": 0.0,
                          "regional_specificity": 1.0, "clinical_phase": 0.0},
            "safe": {"druggability": 0.0, "safety": 1.0,
                     "regional_specificity": 0.0, "clinical_phase": 1.0},
        }
        assert pareto_front(ranks) == {"druggable", "safe"}

    def test_a_gene_missing_an_axis_is_not_in_the_front(self) -> None:
        """COUNCIL V2. Eligibility is all four axes, not the shared subset."""
        ranks = {
            "full": {"druggability": 1.0, "safety": 1.0,
                     "regional_specificity": 1.0, "clinical_phase": 1.0},
            "sparse": {"druggability": 0.5},
        }
        assert pareto_front(ranks) == {"full"}
        assert not pareto_eligible(ranks["sparse"])

    def test_no_axis_at_all_is_not_a_free_pass(self) -> None:
        """v1 returned both of these as non-dominated because they shared no axis."""
        ranks = {"a": {"druggability": 0.1}, "b": {"safety": 0.9}}
        assert pareto_front(ranks) == set()

    def test_ineligible_genes_are_flagged_with_what_is_missing(self) -> None:
        out = rank_genes({
            "druggability": cells(A=1.0, B=0.5),
            "safety": cells(A=1.0, B=0.5),
        })
        assert not out["A"].pareto
        assert out["A"].pareto_k == 2
        assert not out["A"].pareto_eligible
        assert "not eligible" in out["A"].note
        assert "regional_specificity" in out["A"].note and "clinical_phase" in out["A"].note


class TestV2OneAxisCannotDominateFour:
    """COUNCIL V2, reproduced from the council's own probe.

    A gene whose only Pareto axis is druggability=1.0 (tied top) and whose other
    three axes are `no_data` entered the v1 front and ejected two genes that had
    all four axes. The note v1 printed — "fewer axes make domination harder" —
    stated the opposite of what the code did.
    """

    def _spec(self, with_oneaxis: bool):
        spec = {
            "druggability": {
                "ASGR1": value(0.8), "PCSK9": value(1.0),
                "SERPINA1": value(0.6), "TTR": value(0.4),
            },
            "safety": {
                "ASGR1": value(0.9), "PCSK9": value(0.5),
                "SERPINA1": value(0.7), "TTR": value(1.0),
            },
            "regional_specificity": {
                "ASGR1": value(0.6), "PCSK9": value(0.4),
                "SERPINA1": value(0.8), "TTR": value(1.0),
            },
            "clinical_phase": {
                "ASGR1": value(1.0), "PCSK9": value(1.0),
                "SERPINA1": value(0.3), "TTR": value(0.7),
            },
        }
        if with_oneaxis:
            spec["druggability"]["ONEAXIS"] = value(1.0)
            for ring in ("safety", "regional_specificity", "clinical_phase"):
                spec[ring]["ONEAXIS"] = no_data("adapter returned nothing")
        return spec

    def test_baseline_front(self) -> None:
        out = rank_genes(self._spec(with_oneaxis=False))
        assert {g for g, r in out.items() if r.pareto} == {
            "ASGR1", "PCSK9", "SERPINA1", "TTR"}

    def test_one_axis_gene_neither_enters_nor_evicts(self) -> None:
        out = rank_genes(self._spec(with_oneaxis=True))
        front = {g for g, r in out.items() if r.pareto}
        assert "ONEAXIS" not in front, "one measurement must not reach the headline"
        assert front == {"ASGR1", "PCSK9", "SERPINA1", "TTR"}, \
            "and it must not delete genes that have all four axes"
        assert out["ONEAXIS"].pareto_k == 1
        assert not out["ONEAXIS"].pareto_eligible

    def test_the_printed_note_no_longer_contradicts_the_code(self) -> None:
        out = rank_genes(self._spec(with_oneaxis=True))
        note = out["ONEAXIS"].note
        assert "harder" not in note
        assert "not eligible" in note


class TestV3TransientFailureCannotRewriteTheHeadline:
    """COUNCIL V3. One `no_data` cell on a gene that is not in the front must not
    delete other genes from it. v1: forcing APOB.safety to no_data removed ASGR1
    and SERPINA1, because losing its own value let APOB dominate on the three
    axes that remained."""

    def _spec(self):
        return {
            "druggability": {g: value(v) for g, v in
                             [("ASGR1", .8), ("PCSK9", 1.), ("SERPINA1", .6),
                              ("TTR", .4), ("APOB", .9)]},
            "safety": {g: value(v) for g, v in
                       [("ASGR1", .9), ("PCSK9", .5), ("SERPINA1", .7),
                        ("TTR", 1.), ("APOB", .1)]},
            "regional_specificity": {g: value(v) for g, v in
                                     [("ASGR1", .6), ("PCSK9", .4), ("SERPINA1", .8),
                                      ("TTR", 1.), ("APOB", .5)]},
            "clinical_phase": {g: value(v) for g, v in
                               [("ASGR1", 1.), ("PCSK9", 1.), ("SERPINA1", .3),
                                ("TTR", .7), ("APOB", .95)]},
        }

    @staticmethod
    def _front(spec):
        return {g for g, r in rank_genes(spec).items() if r.pareto}

    def test_a_single_503_cannot_remove_a_gene_from_the_front(self) -> None:
        base = self._front(self._spec())
        for gene in ("APOB", "TTR", "PCSK9"):
            for ring in ("safety", "regional_specificity", "druggability", "clinical_phase"):
                spec = self._spec()
                spec[ring][gene] = no_data("transient 503")
                got = self._front(spec)
                lost = (base - {gene}) - got
                assert not lost, (
                    f"{gene}.{ring} -> no_data removed {sorted(lost)} from the front; "
                    f"a transient upstream failure rewrote the headline")

    def test_the_only_permitted_change_is_the_failed_gene_leaving(self) -> None:
        base = self._front(self._spec())
        spec = self._spec()
        spec["safety"]["APOB"] = no_data("transient 503")
        assert self._front(spec) == base - {"APOB"}


class TestWeightedScore:
    def test_renormalised_over_available_rings(self) -> None:
        w = {"druggability": 1.0, "safety": 3.0}
        assert weighted({"druggability": 1.0, "safety": 0.0}, w) == pytest.approx(0.25)
        # Missing safety: the score uses druggability alone, not a zero.
        assert weighted({"druggability": 1.0}, w) == pytest.approx(1.0)

    def test_no_rings_gives_none_not_zero(self) -> None:
        assert weighted({}, DEFAULT_WEIGHTS) is None

    def test_headline_is_pareto_not_the_score(self) -> None:
        """A gene can top the weighted score and still be outside the front, and
        the app must report the front as the headline."""
        out = rank_genes({
            "druggability": cells(A=1.0, B=0.9, C=0.8),
            "safety": cells(A=1.0, B=0.9, C=0.8),
            "regional_specificity": cells(A=1.0, B=0.9, C=0.8),
            "clinical_phase": cells(A=1.0, B=0.9, C=0.8),
        })
        assert out["A"].pareto and not out["B"].pareto and not out["C"].pareto


class TestStabilityFlag:
    def test_deterministic(self) -> None:
        spec = {
            "druggability": cells(**{f"G{i}": float(i) for i in range(14)}),
            "safety": cells(**{f"G{i}": float(13 - i) for i in range(14)}),
            "regional_specificity": cells(**{f"G{i}": float(i % 5) for i in range(14)}),
            "clinical_phase": cells(**{f"G{i}": float(i % 3) for i in range(14)}),
        }
        a = rank_genes(spec)
        b = rank_genes(spec)
        assert {g: r.unstable for g, r in a.items()} == {g: r.unstable for g, r in b.items()}

    def test_a_tradeoff_heavy_set_flags_something(self) -> None:
        spec = {
            "druggability": cells(**{f"G{i}": float(i) for i in range(14)}),
            "safety": cells(**{f"G{i}": float(13 - i) for i in range(14)}),
            "regional_specificity": cells(**{f"G{i}": float((i * 7) % 14) for i in range(14)}),
            "clinical_phase": cells(**{f"G{i}": float((i * 3) % 14) for i in range(14)}),
        }
        out = rank_genes(spec)
        assert any(r.unstable for r in out.values())


class TestV5TheFooterMustNotContradictTheCode:
    """COUNCIL V5. The rendered footer said 'Rodent ring: … Never folded into any
    score' one line below 'Default weights … rodent=0.5', and `rodent` was in
    RANKED_RINGS too, so it was in the weighted score *and* the median available
    rank. Removing `rodent` from the weights moved TP53 #7 -> #9 and BRAF #4 -> #6.
    """

    def test_rodent_and_clinical_phase_are_in_no_score(self) -> None:
        from targetring.rank import EXCLUDED_FROM_SCORE
        from targetring.run import RANKED_RINGS
        assert set(EXCLUDED_FROM_SCORE) == {"rodent", "clinical_phase"}
        for ring in EXCLUDED_FROM_SCORE:
            assert ring not in DEFAULT_WEIGHTS
        assert "rodent" not in RANKED_RINGS
        # clinical_phase stays ranked: it is a Pareto axis, just not a score term.
        assert "clinical_phase" in RANKED_RINGS

    def test_the_rodent_ring_reaches_no_rank(self) -> None:
        from targetring.run import RANKED_RINGS
        spec = {
            "druggability": cells(A=1.0, B=0.0),
            "rodent": cells(A=0.0, B=249.0),
        }
        out = rank_genes(spec, DEFAULT_WEIGHTS, RANKED_RINGS)
        assert "rodent" not in out["A"].ranks and "rodent" not in out["B"].ranks
        # B's 249 mouse-phenotype terms buy it nothing.
        assert out["A"].median_rank > out["B"].median_rank

    def test_putting_rodent_back_in_the_weights_fails_the_render(self) -> None:
        """The claim is checked against the structure it describes, so the two
        cannot drift apart again."""
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="never folded into any score"):
            assert_footer_is_true({**DEFAULT_WEIGHTS, "rodent": 0.5}, ("druggability",))
        with pytest.raises(AssertionError, match="never folded into any score"):
            assert_footer_is_true({**DEFAULT_WEIGHTS, "clinical_phase": 1.0}, ())

    def test_putting_rodent_back_in_ranked_rings_fails_the_render(self) -> None:
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="median available rank"):
            assert_footer_is_true(DEFAULT_WEIGHTS, ("druggability", "rodent"))

    def test_a_clean_configuration_passes(self) -> None:
        from targetring.figure import assert_footer_is_true
        from targetring.run import RANKED_RINGS
        assert_footer_is_true(DEFAULT_WEIGHTS, RANKED_RINGS)


class TestDrawerOnlySourceCostIsReported:
    """A source that drives no ring must not dominate a run silently.

    v1 reported one `adapters_s` number, so "Europe PMC is 52 s of a 50-gene
    run" had to be measured by hand afterwards — and nothing noticed when ChEMBL
    started costing 177 s of a 192 s 15-gene run while EBI was 500-ing. Failures
    are deliberately never cached, so every run re-pays it.
    """

    def test_the_sources_that_drive_no_ring_are_named(self) -> None:
        from targetring.run import DRAWER_ONLY_SOURCES, RANKED_RINGS
        from targetring.figure import RING_SPECS
        ring_keys = {s.key for s in RING_SPECS}
        for source in DRAWER_ONLY_SOURCES:
            assert source not in ring_keys
            assert source not in RANKED_RINGS

    def test_chembl_drives_no_ring_and_no_rank(self) -> None:
        """PLAN §8, and the council verified it in v1. It must stay true."""
        from targetring.figure import RING_SPECS
        from targetring.run import RANKED_RINGS
        sources = " ".join(s.source for s in RING_SPECS).lower()
        assert "chembl" not in sources
        assert not any("chembl" in r for r in RANKED_RINGS)
