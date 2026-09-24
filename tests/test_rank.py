"""Ranking tests. The load-bearing one is `TestNeverImpute`."""

from __future__ import annotations

import pytest

from targetring.rank import (
    DEFAULT_WEIGHTS,
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
        ranks = {
            "druggable": {"druggability": 1.0, "safety": 0.0},
            "safe": {"druggability": 0.0, "safety": 1.0},
        }
        assert pareto_front(ranks) == {"druggable", "safe"}

    def test_missing_axes_are_not_compared(self) -> None:
        """A gene must not win an axis by having no data on it."""
        ranks = {
            "full": {"druggability": 1.0, "safety": 1.0},
            "sparse": {"druggability": 0.5},
        }
        # `full` beats `sparse` on the only shared axis, so `sparse` is dominated.
        assert pareto_front(ranks) == {"full"}

    def test_no_shared_axis_means_no_domination(self) -> None:
        ranks = {"a": {"druggability": 0.1}, "b": {"safety": 0.9}}
        assert pareto_front(ranks) == {"a", "b"}

    def test_partial_pareto_membership_is_flagged_as_provisional(self) -> None:
        out = rank_genes({
            "druggability": cells(A=1.0, B=0.5),
            "safety": cells(A=1.0, B=0.5),
        })
        assert out["A"].pareto
        assert out["A"].pareto_k == 2
        assert "provisional" in out["A"].note


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
