"""v2.5 item 2 — manual threshold filters, per data source, that remove genes.

Two rules were promised before this was built and both are binding. They are the
first two classes here, in that order, because everything else in the module is
plumbing around them:

1. A threshold never silently drops a gene with no data on that ring. Otherwise
   filtering becomes exclusion-by-missingness, which systematically deletes the
   under-studied genes — `docs/reviews/COUNCIL-v0-plan.md` M3's bias, arriving
   through a UI control instead of through an imputation.
2. A filtered gene goes in the drop ledger, distinguishable from a parse failure
   and from an unresolvable symbol, so `input == rendered + ledgered` holds and
   the counts on the figure add up.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from targetring import filters as F
from targetring.states import Cell, no_data, not_applicable, value


def spec(**rings) -> dict[str, dict[str, Cell]]:
    return rings


class TestRule1NoDataIsNeverDroppedSilently:
    """The bias M3 identified, arriving through a slider."""

    CELLS = spec(literature={"HAS": value(3.0), "LOW": value(0.5),
                             "NONE": no_data("PubTator has no record")})

    def test_a_gene_with_no_data_on_the_filtered_ring_is_kept(self) -> None:
        out = F.apply(["HAS", "LOW", "NONE"], self.CELLS,
                      [F.Filter("literature", "ge", 1.0)])
        assert out.kept == ["HAS", "NONE"]
        assert [d.gene for d in out.drops] == ["LOW"], "only the measured miss is dropped"

    def test_the_kept_gene_is_marked_not_quietly_mixed_in(self) -> None:
        out = F.apply(["HAS", "NONE"], self.CELLS, [F.Filter("literature", "ge", 1.0)])
        assert out.kept_unevaluated == {"NONE": ["literature"]}
        assert "HAS" not in out.kept_unevaluated, "a gene that passed is not 'unevaluated'"

    @pytest.mark.parametrize("state_cell", [
        no_data("never measured"),
        not_applicable("non-coding"),
        Cell("failed", None, "upstream 503"),
    ])
    def test_every_non_value_state_is_kept_not_only_no_data(self, state_cell) -> None:
        """AGENTS.md rule 1 names four states. Three of them are "there is no
        number here", and a threshold can be evaluated against none of them."""
        cells = spec(literature={"X": state_cell})
        out = F.apply(["X"], cells, [F.Filter("literature", "ge", 99.0)])
        assert out.kept == ["X"] and out.drops == []

    def test_a_ring_the_figure_does_not_have_does_not_delete_the_whole_set(self) -> None:
        out = F.apply(["HAS", "LOW"], self.CELLS, [F.Filter("rodent", "ge", 1.0)])
        assert out.kept == ["HAS", "LOW"]
        assert out.kept_unevaluated == {"HAS": ["rodent"], "LOW": ["rodent"]}

    def test_the_explicit_toggle_drops_them_and_says_which_toggle_did_it(self) -> None:
        out = F.apply(["HAS", "NONE"], self.CELLS,
                      [F.Filter("literature", "ge", 1.0, drop_no_data=True)])
        assert out.kept == ["HAS"]
        assert out.drops[0].gene == "NONE"
        assert out.drops[0].status == F.FILTERED_NO_DATA
        assert "drop genes with no data" in out.drops[0].reason
        assert "Without that toggle it would have been kept" in out.drops[0].reason

    def test_the_toggle_is_off_unless_it_is_asked_for(self) -> None:
        assert F.Filter("literature", "ge", 1.0).drop_no_data is False
        assert F.parse_filters("literature >= 1")[0].drop_no_data is False
        assert F.parse_filters([{"ring": "literature", "op": "ge",
                                 "threshold": 1}])[0].drop_no_data is False

    def test_the_toggle_is_per_filter_not_global(self) -> None:
        """One threshold may be a hard requirement and another a preference."""
        cells = spec(a={"G": no_data("x")}, b={"G": no_data("y")})
        out = F.apply(["G"], cells, [F.Filter("a", "ge", 1.0, drop_no_data=True),
                                     F.Filter("b", "ge", 1.0)])
        assert out.kept == [] and out.drops[0].status == F.FILTERED_NO_DATA
        out2 = F.apply(["G"], cells, [F.Filter("a", "ge", 1.0),
                                      F.Filter("b", "ge", 1.0)])
        assert out2.kept == ["G"]


class TestRule2TheLedgerStillAddsUp:
    def test_the_two_filter_statuses_are_distinct_from_each_other(self) -> None:
        """A gene removed for missing a threshold and a gene removed for having
        no measurement are different facts, and only the second biases a set."""
        cells = spec(lit={"LOW": value(0.1), "NONE": no_data("x")})
        out = F.apply(["LOW", "NONE"], cells,
                      [F.Filter("lit", "ge", 1.0, drop_no_data=True)])
        assert {d.gene: d.status for d in out.drops} == {
            "LOW": F.FILTERED, "NONE": F.FILTERED_NO_DATA}

    def test_they_are_distinct_from_parse_and_resolution_failures(self) -> None:
        from targetring.de_input import RENDERED_STATUSES
        assert F.FILTER_STATUSES.isdisjoint(RENDERED_STATUSES), (
            "a filtered gene is NOT on the figure, so it must not be a rendered status")
        assert F.FILTER_STATUSES.isdisjoint(
            {"excel_corrupt", "ambiguous", "unresolved", "dropped_dup",
             "dropped_no_padj", "dropped_bad_logfc", "dropped_over_cap"})

    def test_both_statuses_are_declared_in_the_ledgers_own_vocabulary(self) -> None:
        import typing

        from targetring.de_input import DropStatus
        assert F.FILTER_STATUSES <= set(typing.get_args(DropStatus))

    def test_one_ledger_row_per_gene_however_many_filters_it_failed(self) -> None:
        """The ledger is one row per input row. A gene failing three filters
        that produced three rows would break `input == rendered + ledgered` by
        two."""
        cells = spec(a={"G": value(0.0)}, b={"G": value(0.0)}, c={"G": value(0.0)})
        out = F.apply(["G"], cells, [F.Filter(r, "ge", 1.0) for r in "abc"])
        assert len(out.drops) == 1
        for ring in "abc":
            assert ring in out.drops[0].reason, "and it names every filter it failed"

    def test_a_gene_failing_both_kinds_is_ledgered_as_a_real_miss(self) -> None:
        """It missed a threshold it could actually be measured against, so
        `filtered` is the honest status and the reason carries both."""
        cells = spec(a={"G": value(0.0)}, b={"G": no_data("x")})
        out = F.apply(["G"], cells, [F.Filter("a", "ge", 1.0),
                                     F.Filter("b", "ge", 1.0, drop_no_data=True)])
        assert out.drops[0].status == F.FILTERED
        assert "a = 0" in out.drops[0].reason and "no data" in out.drops[0].reason

    def test_kept_plus_dropped_is_every_gene_submitted(self) -> None:
        cells = spec(lit={g: (value(float(i)) if i % 3 else no_data("x"))
                          for i, g in enumerate(f"G{i}" for i in range(12))})
        genes = list(cells["lit"])
        out = F.apply(genes, cells, [F.Filter("lit", "ge", 5.0)])
        assert len(out.kept) + len(out.drops) == len(genes)
        assert set(out.kept) | {d.gene for d in out.drops} == set(genes)


class TestThresholdsAreOnTheRingsOwnNumbers:
    def test_the_raw_value_is_what_is_compared_not_a_rank_or_a_colour(self) -> None:
        """`regional_specificity` is a TPM ratio, so `>= 5` means five-fold —
        the number in values.csv, in the units the ring's label states."""
        cells = spec(regional_specificity={"TTR": value(5204.0), "X": value(4.9),
                                           "Y": value(5.0)})
        out = F.apply(["TTR", "X", "Y"], cells,
                      [F.Filter("regional_specificity", "ge", 5.0)])
        assert out.kept == ["TTR", "Y"], "the bound is inclusive"

    def test_le_is_offered_because_more_is_not_always_better(self) -> None:
        cells = spec(safety={"RISKY": value(-0.9), "CLEAN": value(0.8)})
        out = F.apply(["RISKY", "CLEAN"], cells, [F.Filter("safety", "le", 0.0)])
        assert out.kept == ["RISKY"]

    def test_filters_combine_as_a_conjunction(self) -> None:
        cells = spec(a={"BOTH": value(2.0), "ONE": value(2.0)},
                     b={"BOTH": value(2.0), "ONE": value(0.0)})
        out = F.apply(["BOTH", "ONE"], cells,
                      [F.Filter("a", "ge", 1.0), F.Filter("b", "ge", 1.0)])
        assert out.kept == ["BOTH"]

    def test_the_reason_names_the_value_and_the_threshold_it_missed(self) -> None:
        cells = spec(lit={"G": value(0.25)})
        out = F.apply(["G"], cells, [F.Filter("lit", "ge", 1.5)])
        assert "lit = 0.25" in out.drops[0].reason
        assert "lit >= 1.5" in out.drops[0].reason


class TestParsingRejectsRatherThanGuesses:
    RINGS = ("literature", "safety")

    def test_no_filters_is_not_an_error(self) -> None:
        assert F.parse_filters(None) == [] and F.parse_filters("") == []

    def test_one_per_line(self) -> None:
        got = F.parse_filters("literature >= 2\nsafety <= 0.5", self.RINGS)
        assert [(f.ring, f.op, f.threshold) for f in got] == [
            ("literature", "ge", 2.0), ("safety", "le", 0.5)]

    def test_json_from_the_form(self) -> None:
        got = F.parse_filters(json.dumps([
            {"ring": "literature", "op": ">=", "threshold": "2", "drop_no_data": True}]),
            self.RINGS)
        assert got[0].ring == "literature" and got[0].op == "ge"
        assert got[0].threshold == 2.0 and got[0].drop_no_data is True

    def test_the_drop_no_data_suffix_is_spelled_out_in_full(self) -> None:
        got = F.parse_filters("literature >= 2 drop_no_data", self.RINGS)
        assert got[0].drop_no_data is True

    def test_a_ring_this_figure_does_not_have_is_rejected_outright(self) -> None:
        """Otherwise it is printed on the figure as active while removing
        nothing — a false claim about the figure's own method."""
        with pytest.raises(F.FilterRejected, match="no 'druggability' ring"):
            F.parse_filters("druggability >= 1", self.RINGS)

    def test_an_empty_box_is_not_a_filter(self) -> None:
        assert F.parse_filters([{"ring": "literature", "threshold": ""}],
                               self.RINGS) == []

    def test_a_threshold_that_is_not_a_number_is_rejected(self) -> None:
        with pytest.raises(F.FilterRejected, match="not a number"):
            F.parse_filters([{"ring": "literature", "threshold": "high"}], self.RINGS)

    def test_an_unreadable_line_names_the_line(self) -> None:
        with pytest.raises(F.FilterRejected, match="literature is big"):
            F.parse_filters("literature is big", self.RINGS)

    def test_two_filters_on_the_same_ring_and_comparison_are_rejected(self) -> None:
        with pytest.raises(F.FilterRejected, match="keep one"):
            F.parse_filters("literature >= 2\nliterature >= 3", self.RINGS)

    def test_a_range_is_allowed_because_the_comparisons_differ(self) -> None:
        got = F.parse_filters("literature >= 2\nliterature <= 9", self.RINGS)
        assert len(got) == 2

    def test_nan_and_infinity_are_not_thresholds(self) -> None:
        for bad in ("nan", "inf", "-inf"):
            with pytest.raises(F.FilterRejected, match="not a threshold"):
                F.Filter("literature", "ge", float(bad))


class TestTheFigureAndTheCellsStayConsistent:
    def test_restrict_removes_a_filtered_gene_from_every_ring(self) -> None:
        """A gene surviving in one ring's dict and vanishing from another's is
        the cell-count invariant (I1) failing one render later."""
        cells = spec(a={"KEEP": value(1.0), "GO": value(0.0)},
                     b={"KEEP": value(1.0), "GO": value(0.0)},
                     c={"KEEP": value(1.0)})
        out = F.restrict(cells, ["KEEP"])
        assert all(set(r) == {"KEEP"} for r in out.values())
        assert set(out) == {"a", "b", "c"}, "no ring is dropped, only genes"

    def test_the_labels_the_figure_prints_carry_the_units(self) -> None:
        fs = [F.Filter("regional_specificity", "ge", 5.0)]
        assert F.filter_lines(fs, {"regional_specificity": "Regional specificity "
                                                           "(log10 ratio)"}) == [
            "Regional specificity (log10 ratio) >= 5"]
        assert F.filter_lines(fs) == ["regional_specificity >= 5"]

    def test_the_label_says_when_no_data_genes_are_being_dropped(self) -> None:
        assert "+ drop no-data" in F.Filter("lit", "ge", 1.0, drop_no_data=True).label()
        assert "+ drop no-data" not in F.Filter("lit", "ge", 1.0).label()

    def test_the_manifest_dict_is_enough_to_rebuild_the_filter(self) -> None:
        f = F.Filter("literature", "le", 2.5, drop_no_data=True)
        d = f.as_dict()
        assert F.parse_filters([d])[0] == f
        assert d["expression"] == "literature <= 2.5"


class TestTheWiringIntoARun:
    """Structural checks on the run path, so the two rules cannot be bypassed by
    a caller rather than by a change to `filters.py`."""

    def test_the_filterable_rings_all_exist_on_the_figure(self) -> None:
        from targetring.figure import RING_SPECS
        from targetring.run import FILTERABLE_RINGS
        keys = {s.key for s in RING_SPECS}
        assert set(FILTERABLE_RINGS) <= keys

    def test_k_is_not_filterable(self) -> None:
        """Filtering on the completeness ring would filter on how much anybody
        has studied a gene — M3's bias with a slider on it."""
        from targetring.run import FILTERABLE_RINGS
        assert "completeness" not in FILTERABLE_RINGS

    def test_the_accounting_assertion_runs_again_after_filtering(self) -> None:
        from pathlib import Path
        src = Path("targetring/run.py").read_text()
        before, after = src.split("filters_mod.apply(", 1)
        assert "assert_accounted(" in before, "rule 3 before anything is drawn"
        assert "assert_accounted(" in after, (
            "a gene removed by a slider is as missing as one that failed to resolve")

    def test_the_manifest_carries_the_settings(self) -> None:
        from targetring.manifest import Manifest
        m = Manifest(run_id="r", timestamp_utc="t", git_sha="s", region="Liver",
                     input_row_count=3, rendered_count=2, ledger_count=1,
                     ordering="submitted", ordering_note="spoke order: submitted",
                     filters=[F.Filter("literature", "ge", 2.0).as_dict()],
                     filter_counts={"filtered": 1, "kept_unevaluated": 0})
        d = m.as_dict()
        assert d["filters"][0]["expression"] == "literature >= 2"
        assert d["ordering"] == "submitted" and d["filter_counts"]["filtered"] == 1
        assert "weights" not in d and "pareto_completeness" not in d

    def test_the_csv_export_carries_the_settings(self, tmp_path) -> None:
        from targetring.de_input import ParsedInput
        from targetring.run import _write_csv
        rows = [{"cell_id": "cell|lit|0|KEEP", "spoke_index": 0, "gene": "KEEP",
                 "ring": "lit", "state": "value", "value": 3.0}]
        path = tmp_path / "values.csv"
        _write_csv(path, rows, [], ParsedInput(input_row_count=2),
                   "spoke order: alphabetical by gene symbol",
                   [F.Filter("literature", "ge", 2.0, drop_no_data=True)],
                   {"NONE": ["literature"]})
        body = path.read_text()
        assert "spoke order: alphabetical by gene symbol" in body
        assert "literature >= 2" in body
        assert "not the whole submitted set" in body
        assert "KEPT, NOT FILTERED" in body and "NONE,literature" in body
        # Still a readable CSV: the value rows parse on their own.
        head = list(csv.DictReader(io.StringIO(body.split("\n#")[0])))
        assert head[0]["gene"] == "KEEP"
