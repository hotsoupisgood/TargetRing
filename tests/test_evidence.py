"""The go/no-go evidence surface — the one that must not become a score.

Puck asked for "evidence for go/no-go on in vitro experiments". `evidence.py`'s
docstring says why that is a surface and not a verdict; these are the tests that
keep it one. Four ways this feature becomes `AGENTS.md:17`'s plausible wrong
output, and a class for each:

* `TestMissingIsNotClear` — the central property. A rule whose rings have no
  measurement must report `unevaluable`, never `clear`. A surface that answered
  "no contradiction found" over three hatched cells would turn missing data into
  reassurance, in the one place it does the most damage: a reader deciding
  whether to spend bench time on a target. AGENTS.md rule 1 in a new place.
* `TestNoVerdictAndNoScore` — AGENTS.md rule 7. No verdict key, no per-gene
  number that could be sorted into a ranking, and no total of findings. The
  Pareto front was removed for reproducing the defect it was prescribed to fix;
  a "go/no-go score" is that machinery with a new name.
* `TestEveryRuleIsReachable` — each rule can both fire and clear. `37b8d20`'s S2
  finding was an influence flag whose relative threshold meant it could never
  report zero; a rule that cannot change state is decoration that reads as
  evidence. Two rules do not fire on any of the ~200 runs under `runs/`, which
  is why they are proved reachable here on constructed cells.
* `TestOnePivotNotTwo` — the cells are `table_payload`'s, not a second read of
  `values.csv`. `view.py`'s own docstring gives the reason: two builders of one
  grid is how two surfaces come to disagree about a number.

Everything runs against artefacts under `runs/` and synthetic cells. No source
is contacted and no analysis is triggered (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import evidence as ev
from targetring import view as view_mod
from targetring.app import app
from targetring.states import Cell, failed, no_data, not_applicable, value

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"


def a_real_run() -> str:
    """The newest timestamped run whose artefacts load. Not pinned, for the
    reason `test_table.py` gives: the claim is that this works on any run."""
    for d in sorted((p for p in RUNS.iterdir() if p.name.startswith("20")),
                    reverse=True):
        if (d / "values.csv").exists() and (d / "manifest.json").exists():
            return d.name
    pytest.skip("no run artefacts under runs/")


@pytest.fixture(scope="module")
def run_id() -> str:
    return a_real_run()


@pytest.fixture(scope="module")
def payload(run_id) -> dict:
    return view_mod.evidence_payload(run_id)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def outcome(rule_key: str, cells: dict[str, Cell]) -> dict:
    """One rule's entry for a synthetic gene row."""
    hits = [e for e in ev.evaluate_gene(cells) if e["rule"] == rule_key]
    assert len(hits) == 1, f"{rule_key} appeared {len(hits)} times"
    return hits[0]


#: Cell rows that make each rule fire, and the minimal edit that clears it.
#: Written out per rule rather than generated, because the point of each one is
#: that these specific two source values are the contradiction.
FIRING_ROWS: dict[str, dict[str, Cell]] = {
    "approved_but_not_tclin": {
        "clinical_phase": value(5.0), "tdl": value(1.0)},
    "tclin_but_no_attributable_drug": {
        "tdl": value(3.0), "clinical_phase": value(0.0)},
    "probe_but_tdark": {
        "chemical_probe": value(1.0), "tdl": value(0.0)},
    "reached_a_phase_then_stopped_for_cause": {
        "clinical_phase": value(3.0), "clinical_activity": value(-1.0)},
    "safety_flags_present": {"safety": value(0.4)},
    "mouse_phenotyped_and_normal": {"rodent": value(0.0)},
}

CLEARING_ROWS: dict[str, dict[str, Cell]] = {
    "approved_but_not_tclin": {
        "clinical_phase": value(5.0), "tdl": value(3.0)},
    "tclin_but_no_attributable_drug": {
        "tdl": value(3.0), "clinical_phase": value(4.0)},
    "probe_but_tdark": {
        "chemical_probe": value(1.0), "tdl": value(2.0)},
    "reached_a_phase_then_stopped_for_cause": {
        "clinical_phase": value(3.0), "clinical_activity": value(1.0)},
    "safety_flags_present": {"safety": value(-0.3)},
    "mouse_phenotyped_and_normal": {"rodent": value(1.0)},
}

ALL_RULE_KEYS = [r.key for r in ev.RULES]


# ===========================================================================


class TestEveryRuleIsReachable:
    """A rule that cannot change state is decoration that reads as evidence."""

    @pytest.mark.parametrize("key", ALL_RULE_KEYS)
    def test_it_fires_on_the_row_it_is_for(self, key) -> None:
        got = outcome(key, FIRING_ROWS[key])
        assert got["outcome"] == ev.FIRES, got
        assert got["headline"], "a firing rule must say what it found"

    @pytest.mark.parametrize("key", ALL_RULE_KEYS)
    def test_it_clears_on_the_minimal_edit(self, key) -> None:
        got = outcome(key, CLEARING_ROWS[key])
        assert got["outcome"] == ev.CLEAR, got
        assert got["headline"] == "", "a cleared rule states nothing"

    def test_every_rule_has_a_row_in_both_tables(self) -> None:
        """So adding a rule without proving it reachable fails here."""
        assert set(FIRING_ROWS) == set(ALL_RULE_KEYS)
        assert set(CLEARING_ROWS) == set(ALL_RULE_KEYS)

    @pytest.mark.parametrize("key", ALL_RULE_KEYS)
    def test_every_rule_states_its_definition(self, key) -> None:
        """The `why` is what makes a finding readable as a fact rather than as a
        recommendation, and it is printed beside it."""
        rule = next(r for r in ev.RULES if r.key == key)
        assert len(rule.why) > 80, f"{key} has no stated definition"
        assert rule.kind in (ev.CONTRADICTION, ev.FLAG)
        assert rule.rings, f"{key} reads no rings"


class TestMissingIsNotClear:
    """AGENTS.md rule 1, applied to the findings themselves."""

    @pytest.mark.parametrize("key", ALL_RULE_KEYS)
    @pytest.mark.parametrize("absent", [no_data, not_applicable, failed])
    def test_any_missing_input_makes_the_rule_unevaluable(self, key, absent
                                                          ) -> None:
        rule = next(r for r in ev.RULES if r.key == key)
        for ring in rule.rings:
            row = dict(FIRING_ROWS[key])
            row[ring] = absent("synthetic")
            got = outcome(key, row)
            assert got["outcome"] == ev.UNEVALUABLE, (key, ring, got)
            assert got["outcome"] != ev.CLEAR
            # and it names which ring has to be filled in
            assert ring in got["missing_rings"], got
            assert got["missing_states"][ring] == absent("x").state

    @pytest.mark.parametrize("key", ALL_RULE_KEYS)
    def test_a_ring_this_run_did_not_draw_is_unevaluable_and_named(self, key
                                                                   ) -> None:
        """Not drawn at all is a different fact from drawn-without-a-value, and
        both are reported rather than one standing in for the other."""
        rule = next(r for r in ev.RULES if r.key == key)
        for ring in rule.rings:
            row = {k: v for k, v in FIRING_ROWS[key].items() if k != ring}
            got = outcome(key, row)
            assert got["outcome"] == ev.UNEVALUABLE, (key, ring, got)
            assert ring in got["not_drawn"], got
            assert ring in got["missing_rings"], got

    def test_an_empty_row_evaluates_nothing_and_claims_nothing(self) -> None:
        entries = ev.evaluate_gene({})
        assert len(entries) == len(ev.RULES)
        assert {e["outcome"] for e in entries} == {ev.UNEVALUABLE}
        assert not any(e["headline"] for e in entries)

    def test_every_rule_is_reported_for_every_gene(self, payload) -> None:
        """A rule that did not fire is present as `clear` or `unevaluable`,
        never dropped — "checked and did not hold" and "could not be checked"
        are different facts and omitting both looks identical."""
        for gene, entries in payload["findings"].items():
            assert [e["rule"] for e in entries] == ALL_RULE_KEYS, gene

    def test_the_surface_says_that_no_finding_is_not_no_problem(self, payload
                                                                ) -> None:
        note = payload["evidence_empty_note"]
        assert "unevaluable" in note
        assert "not the same as" in note


class TestNoVerdictAndNoScore:
    """AGENTS.md rule 7. The thing this surface must never become."""

    #: Words that would mean the machinery rule 7 removed had come back.
    BANNED = ("verdict", "score", "rank", "ranking", "weight", "weighted",
              "eligible", "eligibility", "pareto", "recommend", "priority",
              "go_no_go", "overall", "composite", "confidence")

    def test_no_payload_key_names_a_score(self, payload) -> None:
        def walk(obj, path="") -> list[str]:
            bad: list[str] = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    low = str(k).lower()
                    if any(b in low for b in self.BANNED):
                        bad.append(f"{path}.{k}")
                    bad += walk(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:5]):
                    bad += walk(v, f"{path}[{i}]")
            return bad

        assert walk(payload) == []

    def test_no_per_gene_finding_count_exists(self, payload) -> None:
        """The number that, sorted, would be a ranking. Deliberately absent —
        the same reasoning `table_payload` gives for carrying no per-gene count
        of dim filters matched."""
        for gene, entries in payload["findings"].items():
            for e in entries:
                for k, v in e.items():
                    assert not isinstance(v, (int, float)) or isinstance(v, bool), \
                        f"{gene}.{e['rule']}.{k} is a number: {v!r}"

    def test_the_run_level_counts_are_genes_not_a_total_of_findings(
            self, payload) -> None:
        """Three independent counts of genes. A sum over findings would invite
        reading two contradictions as twice one."""
        fc = payload["finding_counts"]
        assert set(fc) == {"genes", "with_contradiction", "with_flag",
                           "with_unevaluable"}
        n = fc["genes"]
        assert n == len(payload["order"])
        for k in ("with_contradiction", "with_flag", "with_unevaluable"):
            assert 0 <= fc[k] <= n, (k, fc[k], n)

    def test_the_surface_states_that_it_does_not_rank_or_recommend(
            self, payload) -> None:
        note = payload["evidence_note"]
        assert "does not rank" in note
        assert "recommend" in note
        assert "yours" in note, "it has to hand the call back explicitly"

    def test_the_order_is_the_runs_own_and_is_not_resorted(self, payload,
                                                           run_id) -> None:
        """Re-ordering by how much evidence a gene has would be the ranking."""
        base = view_mod.table_payload(run_id)
        assert payload["order"] == base["order"]
        assert list(payload["findings"]) == base["order"]

    def test_no_rule_recommends_an_action(self) -> None:
        """A `why` explains why two numbers are in tension. It must not tell the
        reader what to do, which is the line between evidence and a verdict."""
        for rule in ev.RULES:
            low = rule.why.lower()
            for word in ("should", "recommend", "prefer", "deprioritise",
                         "drop this", "avoid this target"):
                assert word not in low, (rule.key, word)


class TestGapsKeepTheFourStatesApart:
    """AGENTS.md rule 1: there are four states because the three absent ones
    mean different things, and `failed` is the one a re-run can fix."""

    def test_the_three_absent_states_are_grouped_not_summed(self) -> None:
        row = {"tdl": value(1.0), "safety": no_data("absent"),
               "rodent": failed("HTTP 500"),
               "chemical_probe": not_applicable("non-coding")}
        gaps = ev.gaps_for_gene(row, list(row))
        assert gaps == {"no_data": ["safety"], "failed": ["rodent"],
                        "not_applicable": ["chemical_probe"]}

    def test_a_ring_with_a_value_is_not_a_gap(self) -> None:
        gaps = ev.gaps_for_gene({"tdl": value(3.0)}, ["tdl"])
        assert gaps == {"no_data": [], "not_applicable": [], "failed": []}

    def test_completeness_is_not_counted_as_a_gap(self, payload) -> None:
        """It is a derived gauge over the other rings, so counting it would
        count the gaps twice."""
        for gene, gaps in payload["gaps"].items():
            for state, keys in gaps.items():
                assert "completeness" not in keys, (gene, state)

    def test_the_gap_scan_is_exactly_the_rings_k_counts(self, payload) -> None:
        """One scope per card. The scan used to be every ring except
        `completeness`, which left the two DE rings in it while `k` on the same
        card counts the nine external ones and no rule reads them — two numbers
        disagreeing about what is in scope."""
        from targetring.order import EXTERNAL_RINGS
        drawn = {r["key"] for r in payload["rings"]}
        scanned = set(EXTERNAL_RINGS) & drawn
        assert len(scanned) == payload["k_max"], (scanned, payload["k_max"])
        for gene, gaps in payload["gaps"].items():
            for state, keys in gaps.items():
                assert set(keys) <= scanned, (gene, state, keys)

    def test_the_de_rings_are_never_scanned_as_gaps(self, payload) -> None:
        """logFC and adjusted p are the INPUT that selected this gene set, not
        evidence about the target. A reading built on them would be this surface
        reasoning about effect size, which is where scoring starts (rule 7)."""
        for gene, gaps in payload["gaps"].items():
            for state, keys in gaps.items():
                assert "logfc" not in keys, (gene, state)
                assert "neg_log10_padj" not in keys, (gene, state)

    def test_no_rule_reads_a_de_ring_either(self, payload) -> None:
        """The other half of the same scope: if a rule read logFC, excluding it
        from the gap scan would hide a genuine unevaluable reading."""
        for rule in payload["rules"]:
            assert "logfc" not in rule["rings"], rule["key"]
            assert "neg_log10_padj" not in rule["rings"], rule["key"]

    def test_a_de_ring_cannot_be_absent_on_a_rendered_gene_anyway(self, payload
                                                                   ) -> None:
        """Why excluding them loses nothing: `de_input` sends a row with an
        unusable logFC or a missing padj to the drop ledger, so a gene that
        reached the figure carries both."""
        by_gene = {g["gene"]: g for g in payload["genes"]}
        for gene, g in by_gene.items():
            for ring in ("logfc", "neg_log10_padj"):
                if ring in g["cells"]:
                    assert g["cells"][ring]["state"] == "value", (gene, ring)

    def test_every_rendered_gene_has_a_gap_entry(self, payload) -> None:
        assert set(payload["gaps"]) == set(payload["order"])


class TestOnePivotNotTwo:
    """The cells are `table_payload`'s. Not a second read of `values.csv`."""

    def test_the_cells_are_identical_to_the_table_tabs(self, payload, run_id
                                                       ) -> None:
        base = view_mod.table_payload(run_id)
        assert payload["genes"] == base["genes"]
        assert payload["rings"] == base["rings"]
        assert payload["counts"] == base["counts"]
        assert payload["ledger"] == base["ledger"]

    def test_the_gaps_agree_with_the_cells_they_came_from(self, payload) -> None:
        """The one check that the two halves of this payload describe the same
        run: a ring listed as a gap must be in that state in the cell grid."""
        by_gene = {g["gene"]: g for g in payload["genes"]}
        for gene, gaps in payload["gaps"].items():
            for state, keys in gaps.items():
                for key in keys:
                    assert by_gene[gene]["cells"][key]["state"] == state, \
                        (gene, key, state)

    def test_a_firing_rule_only_ever_read_cells_with_values(self, payload
                                                             ) -> None:
        by_gene = {g["gene"]: g for g in payload["genes"]}
        for gene, entries in payload["findings"].items():
            for e in entries:
                if e["outcome"] in (ev.FIRES, ev.CLEAR):
                    for ring in e["rings"]:
                        assert by_gene[gene]["cells"][ring]["state"] == "value", \
                            (gene, e["rule"], ring)

    def test_the_ring_labels_are_the_table_columns_not_new_names(self, payload
                                                                  ) -> None:
        """v3.7 item 4's defect was ten of twelve rings being called different
        things on two surfaces. This surface builds no name of its own."""
        for r in payload["rings"]:
            assert payload["ring_labels"][r["key"]] == r["column_label"]

    def test_the_rule_catalogue_is_carried_for_an_all_unevaluable_gene(
            self, payload) -> None:
        """Without it a reader cannot tell "six rules, all unevaluable" from
        "nothing was checked"."""
        assert [r["key"] for r in payload["rules"]] == ALL_RULE_KEYS
        for r in payload["rules"]:
            assert r["why"] and r["rings"]


class TestTheRoute:

    def test_it_serves_a_run_on_disk(self, client, run_id) -> None:
        r = client.get(f"/api/run/{run_id}/evidence")
        assert r.status_code == 200
        d = r.json()
        assert d["run_id"] == run_id
        assert d["findings"] and d["gaps"] and d["rules"]

    def test_an_unknown_run_is_a_404_and_not_an_empty_surface(self, client
                                                              ) -> None:
        r = client.get("/api/run/20200101T000000Z-nope/evidence")
        assert r.status_code == 404
        assert r.json()["error"] == "table_unavailable"

    def test_it_does_not_collide_with_the_artefact_catch_all(self, client,
                                                             run_id) -> None:
        """`evidence` is registered above `/{filename}`; both still work."""
        assert client.get(f"/api/run/{run_id}/evidence").status_code == 200
        assert client.get(f"/api/run/{run_id}/manifest.json").status_code == 200
        assert client.get(f"/api/run/{run_id}/values").status_code == 200
