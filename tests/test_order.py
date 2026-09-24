"""Spoke order and k — what is left of ranking after v2.5 deleted it.

Most of `tests/test_rank.py` went with the feature it tested: the Pareto front,
the weighted score, eligibility, the reweighting-stability flag and the
rank-within-the-submitted-set all stopped existing, and a test asserting how they
behaved asserts nothing now.

Three groups of assertions were **not** about ranking and are re-pointed here
rather than deleted:

* `TestNeverImpute` — AGENTS.md rule 1. It used to prove that a missing cell was
  absent from the ranking instead of ranked last; it proves now that a missing
  cell is absent from k and cannot be placed by a single-ring ordering.
* `TestV5TheFooterMustNotContradictTheCode` — COUNCIL V5's figure-honesty check.
  The claim it guards changed; the guard did not.
* `TestDrawerOnlySourceCostIsReported` — a source that drives no ring must not
  dominate a run silently.
"""

from __future__ import annotations

import pytest

from targetring.order import (
    ALPHABETICAL,
    EXTERNAL_RINGS,
    RING_PREFIX,
    SUBMITTED,
    gene_stats,
    normalise_ordering,
    order_for_figure,
    ordering_label,
)
from targetring.states import Cell, no_data, not_applicable, value


def cells(**kw) -> dict[str, Cell]:
    return {g: (value(v) if v is not None else no_data("absent")) for g, v in kw.items()}


class TestNeverImpute:
    """AGENTS.md rule 1. A cell with no value is never a low value."""

    def test_k_counts_only_real_measurements(self) -> None:
        out = gene_stats({
            "tractable": cells(A=1.0, B=2.0),
            "safety": {"A": value(1.0), "B": no_data("x")},
            "rodent": {"A": not_applicable("x"), "B": value(3.0)},
        })
        assert out["A"].k == 2
        assert out["B"].k == 2

    def test_all_states_except_value_are_uncounted(self) -> None:
        out = gene_stats({
            "tractable": {"A": value(1.0), "B": no_data("x"), "C": not_applicable("y"),
                          "D": Cell("failed", None, "z")},
        })
        assert out["A"].k == 1
        assert [out[g].k for g in "BCD"] == [0, 0, 0]

    def test_a_gene_with_nothing_is_reported_not_dropped(self) -> None:
        out = gene_stats({"tractable": {"A": value(1.0), "Z": no_data("x")}})
        assert "Z" in out
        assert out["Z"].k == 0
        assert "no external ring" in out["Z"].note

    def test_k_counts_external_rings_only(self) -> None:
        """COUNCIL V13: `logfc` and `neg_log10_padj` come from the user's own
        table and are therefore never missing, so counting them gave every gene
        k >= 2 for free."""
        assert "logfc" not in EXTERNAL_RINGS
        assert "neg_log10_padj" not in EXTERNAL_RINGS
        assert "completeness" not in EXTERNAL_RINGS
        out = gene_stats({"logfc": cells(A=1.0), "neg_log10_padj": cells(A=2.0)})
        assert out["A"].k == 0

    def test_a_gene_with_no_value_on_the_ordering_ring_is_last_not_lowest(self) -> None:
        spec = {"literature": {"A": value(1.0), "B": no_data("x"), "C": value(3.0)}}
        assert order_for_figure(["A", "B", "C"], spec, f"{RING_PREFIX}literature") == [
            "C", "A", "B"]


class TestTheDefaultIsHisOwnTable:
    """v2.5. The submitted order is the default because it is his measurement,
    not a composite this build invented."""

    def test_submitted_order_is_returned_unchanged(self) -> None:
        genes = ["ZZZ", "AAA", "MMM"]
        assert order_for_figure(genes, {}, SUBMITTED) == genes

    def test_it_is_what_an_unset_ordering_means(self) -> None:
        assert normalise_ordering(None, {}) == SUBMITTED
        assert normalise_ordering("", {}) == SUBMITTED

    def test_alphabetical_is_offered(self) -> None:
        assert order_for_figure(["ZZZ", "AAA", "MMM"], {}, ALPHABETICAL) == [
            "AAA", "MMM", "ZZZ"]

    def test_a_single_ring_ordering_uses_that_rings_own_number(self) -> None:
        spec = {"literature": cells(A=1.0, B=3.0, C=2.0)}
        assert order_for_figure(list("ABC"), spec, f"{RING_PREFIX}literature") == [
            "B", "C", "A"]

    def test_ties_are_broken_by_the_submitted_position_so_two_runs_agree(self) -> None:
        spec = {"literature": cells(A=1.0, B=1.0, C=1.0)}
        order = f"{RING_PREFIX}literature"
        assert order_for_figure(list("CBA"), spec, order) == list("CBA")
        assert order_for_figure(list("ABC"), spec, order) == list("ABC")

    def test_an_ordering_the_figure_cannot_honour_is_rejected_not_silently_swapped(
            self) -> None:
        """Falling back to one ordering while printing another on the figure is
        the V5 defect in a different costume."""
        with pytest.raises(ValueError, match="no such ring"):
            normalise_ordering(f"{RING_PREFIX}nonexistent", {"literature": {}})
        with pytest.raises(ValueError, match="unknown spoke ordering"):
            normalise_ordering("by_merit", {})

    def test_every_ordering_has_a_sentence_for_the_figure(self) -> None:
        assert "submitted DE table" in ordering_label(SUBMITTED)
        assert "alphabetical" in ordering_label(ALPHABETICAL)
        label = ordering_label(f"{RING_PREFIX}literature", "Literature volume")
        assert "Literature volume" in label and "no value on it are last" in label


class TestV5TheFooterMustNotContradictTheCode:
    """COUNCIL V5, re-pointed by v2.5.

    v1's footer read "Rodent ring: … Never folded into any score" one line below
    "Default weights … rodent=0.5". The weights are gone, so the assertion now
    guards the claims the figure actually makes: that it is not ranking anything,
    that it states its spoke order, and that it names every active filter.
    """

    ORDER = "spoke order: the order of your submitted DE table (unchanged)"

    def test_a_clean_configuration_passes(self) -> None:
        from targetring.figure import assert_footer_is_true
        assert_footer_is_true([f"Spoke ORDER — {self.ORDER}."], self.ORDER, [])

    @pytest.mark.parametrize("claim", [
        "HEADLINE = Pareto non-dominated set over four axes",
        "Weighted score is a labelled CONVENIENCE",
        "Default weights (provisional): tractable=1",
        "* = this gene's top-10 place does not survive reweighting",
        "10 of 14 eligible genes carry a diamond",
    ])
    def test_a_sentence_promising_a_ranking_fails_the_render(self, claim: str) -> None:
        """The figure must not describe a method it no longer has. A leftover
        sentence is as wrong as leftover code."""
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="v2.5 removed ranking"):
            assert_footer_is_true([claim, self.ORDER], self.ORDER, [])

    def test_an_unstated_spoke_order_fails_the_render(self) -> None:
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="does not state it"):
            assert_footer_is_true(["some other line"], "", [])

    def test_printing_one_order_while_using_another_fails_the_render(self) -> None:
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="does not print it"):
            assert_footer_is_true(["spoke order: alphabetical by gene symbol"],
                                  self.ORDER, [])

    def test_an_unnamed_active_filter_fails_the_render(self) -> None:
        """A filtered figure that does not name its filters reads as the whole
        submitted set — a plausible wrong figure."""
        from targetring.figure import assert_footer_is_true
        with pytest.raises(AssertionError, match="does not say so"):
            assert_footer_is_true([self.ORDER], self.ORDER,
                                  ["Literature volume >= 2"])
        assert_footer_is_true([self.ORDER, "FILTERED: Literature volume >= 2"],
                              self.ORDER, ["Literature volume >= 2"])

    def test_no_ring_label_promises_a_ranking_either(self) -> None:
        """The stale-claim check covers the stamps and the caption, and the ring
        labels reach both."""
        from targetring.figure import RETIRED_CLAIM_WORDS, RING_SPECS
        text = " ".join(f"{s.label} {s.source}" for s in RING_SPECS).lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in text, f"a ring label still says {word!r}"


class TestDrawerOnlySourceCostIsReported:
    """A source that drives no ring must not dominate a run silently.

    v1 reported one `adapters_s` number, so "Europe PMC is 52 s of a 50-gene
    run" had to be measured by hand afterwards — and nothing noticed when ChEMBL
    started costing 177 s of a 192 s 15-gene run while EBI was 500-ing. Failures
    are deliberately never cached, so every run re-pays it.
    """

    def test_the_sources_that_drive_no_ring_are_named(self) -> None:
        from targetring.figure import RING_SPECS
        from targetring.run import DRAWER_ONLY_SOURCES, FILTERABLE_RINGS
        ring_keys = {s.key for s in RING_SPECS}
        for source in DRAWER_ONLY_SOURCES:
            assert source not in ring_keys
            assert source not in FILTERABLE_RINGS

    def test_chembl_drives_no_ring(self) -> None:
        """PLAN §8, and the council verified it in v1. It must stay true."""
        from targetring.figure import RING_SPECS
        sources = " ".join(s.source for s in RING_SPECS).lower()
        assert "chembl" not in sources
