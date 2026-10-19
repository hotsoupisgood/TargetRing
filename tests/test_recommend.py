"""The GO / NO-GO recommendation (`targetring/recommend.py`): Puck's explicit
rule, 2026-10-18 -- "we need the gene to be expressed, and also misregulated in
the study, and have a strong phenotype in a study."

One test per criterion state, the verdict logic including the precedence of
NO-GO over INSUFFICIENT DATA, the baked JSON's shape, the re-stamp from disk,
and a demo box serving it with no model call. No network anywhere here.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from targetring import config
from targetring import recommend as r
from targetring.app import app

T = r.Thresholds(min_tpm=1.0, min_abs_logfc=1.0, max_q=0.05, min_phenotypes=1)

GO_FACTS = r.Facts(region="Lung", region_tpm=26.6, logfc=2.2, padj=4e-14,
                   rodent_ring=1.0, rodent_state="value", n_abnormal_phenotypes=6)


def status(rec: dict, key: str) -> str:
    return next(c["status"] for c in rec["criteria"] if c["key"] == key)


def shown(rec: dict, key: str) -> str:
    return next(c["shown"] for c in rec["criteria"] if c["key"] == key)


class TestExpressed:
    def test_met_on_region_tpm_at_or_above_the_floor(self) -> None:
        rec = r.recommend(GO_FACTS, T)
        assert status(rec, "expressed") == r.MET
        assert "26.6 TPM in Lung" in shown(rec, "expressed")

    def test_exactly_the_floor_is_met(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": 1.0})
        assert status(r.recommend(f, T), "expressed") == r.MET

    def test_not_met_below_the_floor(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": 0.216})
        rec = r.recommend(f, T)
        assert status(rec, "expressed") == r.NOT_MET
        assert rec["verdict"] == r.NO_GO

    def test_fallback_to_tissue_distribution_when_gtex_is_absent(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": None, "tissue_distribution": 0.4})
        rec = r.recommend(f, T)
        assert status(rec, "expressed") == r.MET
        assert "tissueDistribution" in shown(rec, "expressed")
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": None, "tissue_distribution": -0.2})
        assert status(r.recommend(f, T), "expressed") == r.NOT_MET

    def test_no_data_when_neither_is_known(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": None, "tissue_distribution": None})
        rec = r.recommend(f, T)
        assert status(rec, "expressed") == r.NO_DATA
        assert rec["verdict"] == r.INSUFFICIENT

    def test_gtex_wins_over_the_fallback_when_both_exist(self) -> None:
        """A below-floor GTEx number is a measurement; a positive
        tissueDistribution must not rescue it."""
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": 0.1, "tissue_distribution": 0.9})
        assert status(r.recommend(f, T), "expressed") == r.NOT_MET


class TestDysregulated:
    def test_met(self) -> None:
        assert status(r.recommend(GO_FACTS, T), "dysregulated") == r.MET

    def test_negative_logfc_counts_by_magnitude(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "logfc": -1.5})
        assert status(r.recommend(f, T), "dysregulated") == r.MET

    def test_not_met_on_small_effect(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "logfc": 0.6})
        rec = r.recommend(f, T)
        assert status(rec, "dysregulated") == r.NOT_MET
        assert rec["verdict"] == r.NO_GO

    def test_not_met_on_weak_q_even_with_large_effect(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "padj": 0.2})
        assert status(r.recommend(f, T), "dysregulated") == r.NOT_MET

    def test_no_data_when_logfc_or_padj_is_missing(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "padj": None})
        rec = r.recommend(f, T)
        assert status(rec, "dysregulated") == r.NO_DATA
        assert "adjusted p" in shown(rec, "dysregulated")


class TestPhenotype:
    def test_met_on_the_ring(self) -> None:
        assert status(r.recommend(GO_FACTS, T), "phenotype") == r.MET

    def test_met_on_the_term_count_when_the_ring_is_hatched(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "rodent_ring": None, "n_abnormal_phenotypes": 2})
        rec = r.recommend(f, T)
        assert status(rec, "phenotype") == r.MET
        assert "ring hatched" in shown(rec, "phenotype")

    def test_not_met_when_phenotyped_and_normal(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "rodent_ring": 0.0, "n_abnormal_phenotypes": 0})
        rec = r.recommend(f, T)
        assert status(rec, "phenotype") == r.NOT_MET
        assert rec["verdict"] == r.NO_GO

    def test_no_data_when_not_phenotyped(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "rodent_ring": None, "n_abnormal_phenotypes": 0})
        rec = r.recommend(f, T)
        assert status(rec, "phenotype") == r.NO_DATA
        assert rec["verdict"] == r.INSUFFICIENT

    def test_the_informative_negative_label_is_not_an_abnormal_term(self) -> None:
        d = {"logfc": 2.0, "padj": 1e-5, "cells": {
            "regional_specificity": {"state": "value", "value": 3.0,
                                     "detail": "5 TPM in Lung / 1.5 TPM median across 54 GTEx tissues (above the 1 TPM floor)"},
            "rodent": {"state": "value", "value": 0.0, "detail": "phenotyped, no abnormal"}},
            "mouse_phenotypes": ["no abnormal phenotype detected"]}
        f = r.facts_from_drawer(d)
        assert f.n_abnormal_phenotypes == 0
        assert status(r.recommend(f, T), "phenotype") == r.NOT_MET


class TestVerdict:
    def test_go_needs_all_three(self) -> None:
        rec = r.recommend(GO_FACTS, T)
        assert rec["verdict"] == r.GO
        assert [c["status"] for c in rec["criteria"]] == [r.MET, r.MET, r.MET]

    def test_one_not_met_is_no_go_even_when_another_is_no_data(self) -> None:
        """NO-GO beats INSUFFICIENT DATA: a failed criterion is a verdict
        however little else is known."""
        f = r.Facts(**{**GO_FACTS.__dict__, "logfc": 0.1, "region_tpm": None,
                       "tissue_distribution": None})
        rec = r.recommend(f, T)
        assert status(rec, "expressed") == r.NO_DATA
        assert status(rec, "dysregulated") == r.NOT_MET
        assert rec["verdict"] == r.NO_GO

    def test_all_no_data_is_insufficient(self) -> None:
        rec = r.recommend(r.Facts(), T)
        assert rec["verdict"] == r.INSUFFICIENT
        assert {c["status"] for c in rec["criteria"]} == {r.NO_DATA}

    def test_the_criteria_are_exactly_three_in_rule_order(self) -> None:
        rec = r.recommend(GO_FACTS, T)
        assert [c["key"] for c in rec["criteria"]] == ["expressed", "dysregulated", "phenotype"]

    def test_druggability_and_safety_never_move_the_verdict(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "tractable_ring": -1.0, "safety_event": -1.0,
                       "n_severe_liabilities": 2})
        rec = r.recommend(f, T)
        assert rec["verdict"] == r.GO
        # 2026-10-18 18:42, Puck: "can remove druggability caveat." One short
        # line survives, and only for the severe-liability rows.
        assert rec["caveats"] == ["Safety: 2 severe liability records -- not an exclusion."]

    def test_low_druggability_gets_no_caveat_at_all(self) -> None:
        """The whole druggability caveat is gone: it was chemistry advice under
        a verdict that never read it (docs/RECOMMEND-SPEC.md, "Trim")."""
        f = r.Facts(**{**GO_FACTS.__dict__, "tractable_ring": -1.0})
        assert r.recommend(f, T)["caveats"] == []
        assert "druggab" not in json.dumps(r.recommend(f, T)).lower()

    def test_a_bare_safety_ring_flag_is_silent(self) -> None:
        """`hasSafetyEvent` fires for anything on record; one line saying so
        under a GO was noise the reader could not act on. Only a severe
        liability row speaks now."""
        f = r.Facts(**{**GO_FACTS.__dict__, "safety_event": -1.0})
        assert r.recommend(f, T)["caveats"] == []

    def test_one_severe_liability_is_singular(self) -> None:
        f = r.Facts(**{**GO_FACTS.__dict__, "n_severe_liabilities": 1})
        assert r.recommend(f, T)["caveats"] == [
            "Safety: 1 severe liability record -- not an exclusion."]

    def test_no_data_on_druggability_or_safety_gets_no_caveat(self) -> None:
        assert r.recommend(GO_FACTS, T)["caveats"] == []

    def test_modality_line_only_when_down_regulated(self) -> None:
        assert r.recommend(GO_FACTS, T)["modality"] is None
        f = r.Facts(**{**GO_FACTS.__dict__, "logfc": -2.0})
        assert "biologic" in r.recommend(f, T)["modality"]


class TestRuleVersionAndThresholds:
    def test_output_records_thresholds_rule_text_and_version(self) -> None:
        rec = r.recommend(GO_FACTS, T)
        assert rec["thresholds"] == {"min_tpm": 1.0, "min_abs_logfc": 1.0,
                                     "max_q": 0.05, "min_phenotypes": 1,
                                     "specific_ratio": 5.0}
        assert rec["rule_text"].startswith("Rule: expressed AND dysregulated AND phenotype; thresholds:")
        assert rec["rule_version"] == r.rule_version(T)
        assert rec["note"] == "not used to order or filter the figure"
        assert rec["mode"] == "rule"

    def test_a_threshold_change_changes_the_version(self) -> None:
        assert r.rule_version(T) != r.rule_version(r.Thresholds(min_tpm=2.0))

    def test_config_defaults_are_the_spec_defaults(self) -> None:
        t = r.current_thresholds()
        assert (t.min_tpm, t.min_abs_logfc, t.max_q, t.min_phenotypes) == (1.0, 1.0, 0.05, 1)

    def test_current_thresholds_follow_config(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "RECOMMEND_MIN_TPM", 5.0)
        f = r.Facts(**{**GO_FACTS.__dict__, "region_tpm": 3.0})
        assert status(r.recommend(f), "expressed") == r.NOT_MET
        assert r.recommend(f)["thresholds"]["min_tpm"] == 5.0


class TestFactsFromDrawer:
    """Reading the inputs off a baked drawer, the shape `drawer_bake` writes."""

    def test_region_tpm_is_parsed_off_both_shapes_of_the_specificity_detail(self) -> None:
        assert r.region_tpm_from_detail(
            "26.61 TPM in Lung / 1.73 TPM median across 54 GTEx tissues (above the 1 TPM floor)"
        ) == (26.61, "Lung")
        assert r.region_tpm_from_detail(
            "0.216 TPM in Liver is below the 1 TPM floor for 'expressed at all', so ..."
        ) == (0.216, "Liver")
        assert r.region_tpm_from_detail("GTEx returned no expression rows") == (None, None)
        assert r.region_tpm_from_detail(None) == (None, None)

    def test_the_expression_block_wins_over_the_detail_string(self) -> None:
        d = {"expression": {"region": "Colon - Transverse", "region_tpm": 7.5},
             "cells": {"regional_specificity": {"state": "value", "detail": "1 TPM in Lung / 2 TPM median"}}}
        f = r.facts_from_drawer(d)
        assert (f.region_tpm, f.region) == (7.5, "Colon - Transverse")

    def test_a_hatched_rodent_ring_is_none_not_zero(self) -> None:
        d = {"cells": {"rodent": {"state": "no_data", "value": None}}}
        assert r.facts_from_drawer(d).rodent_ring is None

    def test_safety_flag_is_read_off_the_ring_detail(self) -> None:
        d = {"cells": {"safety": {"state": "value", "value": -0.2,
                                  "detail": "mean of 4/4 keys: hasSafetyEvent=-1.00, geneEssentiality=+0.00"}}}
        assert r.facts_from_drawer(d).safety_event == -1.0
        d = {"cells": {"safety": {"state": "value", "value": 0.1,
                                  "detail": "mean of 4/4 keys: hasSafetyEvent=+0.00*, geneEssentiality=+0.00"}}}
        assert r.facts_from_drawer(d).safety_event == 0.0

    def test_the_flagship_runs_drawers_carry_a_recommendation(self) -> None:
        """The flagship of the four allowlisted Crohn's runs on disk
        (docs/DEMO-RUN-ALLOWLIST.md; re-run 2026-10-18 in the terminal ileum),
        baked by `scripts/bake_drawers.py`."""
        from targetring import table as table_mod
        run = "20261018T190526Z-94ef5f"
        genes = table_mod.baked_drawer_genes(run)
        if not genes:
            pytest.skip("no baked drawers on this machine")
        for g in genes:
            d = json.loads(table_mod.baked_drawer_path(run, g).read_text())
            rec = d.get("recommendation")
            assert rec, f"{g}: no recommendation baked"
            assert rec["verdict"] in (r.GO, r.NO_GO, r.INSUFFICIENT)
            assert rec["rule_version"] == r.rule_version(r.current_thresholds())
            assert rec == r.recommend_drawer(d), f"{g}: baked verdict is not what the rule says now"


class TestRestamp:
    def test_restamp_writes_the_block_keeps_the_rest_and_the_dir_mtime(
            self, monkeypatch, tmp_path) -> None:
        import os
        from targetring import drawer_bake, table as table_mod
        run = "20990101T000000Z-recom1"
        d = tmp_path / run / table_mod.DRAWER_DIR
        d.mkdir(parents=True)
        drawer = {"gene": "BRAF", "logfc": 2.0, "padj": 1e-6, "summary": {"text": "kept"},
                  "mouse_phenotypes": ["abnormal x"],
                  "cells": {"regional_specificity": {"state": "value", "value": 2.0,
                                                     "detail": "4 TPM in Lung / 2 TPM median across 54 GTEx tissues (above the 1 TPM floor)"},
                            "rodent": {"state": "value", "value": 1.0, "detail": ""}}}
        (d / "BRAF.json").write_text(json.dumps(drawer))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        old = (1_600_000_000_000_000_000, 1_600_000_000_000_000_000)
        os.utime(tmp_path / run, ns=old)
        s = drawer_bake.restamp_recommendations(run, log=lambda _s: None)
        assert s["restamped"] == ["BRAF"] and s["verdicts"] == {r.GO: 1}
        back = json.loads((d / "BRAF.json").read_text())
        assert back["summary"] == {"text": "kept"}
        assert back["recommendation"]["verdict"] == r.GO
        st = (tmp_path / run).stat()
        assert (st.st_atime_ns, st.st_mtime_ns) == old


class TestTheSummaryPromptCarriesTheVerdict:
    """`summary_llm` is told the verdict and asked to restate it first; it is an
    input to the model, never the model's decision."""

    def test_the_facts_carry_verdict_criteria_caveats_and_rule(self) -> None:
        from targetring import summary_llm
        rec = r.recommend(r.Facts(**{**GO_FACTS.__dict__, "logfc": -2.0,
                                     "tractable_ring": -1.0}), T)
        facts = summary_llm.build_facts({"gene": "X", "recommendation": rec}, {"region": "Lung"})
        got = facts["recommendation"]
        assert got["verdict"] == r.GO
        assert [c["status"] for c in got["criteria"]] == [r.MET, r.MET, r.MET]
        # Low druggability no longer produces a caveat (18:42 trim); the
        # modality line is untouched.
        assert got["caveats"] == [] and "biologic" in got["modality"]
        assert got["rule"].startswith("Rule: expressed AND") and got["rule_version"] == rec["rule_version"]

    def test_a_drawer_without_a_verdict_passes_none(self) -> None:
        from targetring import summary_llm
        assert summary_llm.build_facts({"gene": "X"}, {})["recommendation"] is None

    def test_the_prompt_asks_for_the_verdict_first_and_forbids_re_deriving_it(self) -> None:
        from targetring import summary_llm
        sp = summary_llm.system_prompt()
        assert '"GO", "NO-GO" or "INSUFFICIENT DATA"' in sp
        assert "do not compute, soften, dispute or re-derive it" in sp
        assert 'starting with the word "Hypothesis:"' in sp

    def test_a_verdict_change_changes_the_facts_key_so_the_summary_is_re_baked(self) -> None:
        from targetring import summary_llm
        base = {"gene": "X", "recommendation": r.recommend(GO_FACTS, T)}
        other = {"gene": "X", "recommendation": r.recommend(
            r.Facts(**{**GO_FACTS.__dict__, "logfc": 0.1}), T)}
        k1 = summary_llm.facts_key(summary_llm.build_facts(base, {}), "m")
        k2 = summary_llm.facts_key(summary_llm.build_facts(other, {}), "m")
        assert k1 != k2


class TestDemoServesItWithNoModelCall:
    @pytest.fixture
    def baked(self, monkeypatch, tmp_path):
        from targetring import table as table_mod
        run = "20990101T000000Z-recom2"
        d = tmp_path / run / table_mod.DRAWER_DIR
        d.mkdir(parents=True)
        (d / "BRAF.json").write_text(json.dumps(
            {"gene": "BRAF", "baked": {"at_utc": "2026-10-18T00:00:00Z"},
             "recommendation": r.recommend(GO_FACTS, T)}))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        monkeypatch.setattr(config, "DEMO_MODE", True)
        monkeypatch.setattr(config, "DEMO_RUNS", (run,))
        return run

    def test_served_static_while_every_client_would_raise(self, baked, monkeypatch) -> None:
        import anthropic
        from targetring import app as app_mod

        def boom(*a, **k):
            raise AssertionError("a model client was constructed on a demo box")
        monkeypatch.setattr(anthropic, "Anthropic", boom)
        async def boom2(*a, **k):
            raise AssertionError("the live drawer ran on a demo box")
        monkeypatch.setattr(app_mod, "fill_drawer_only_sources", boom2)
        r0 = TestClient(app).get(f"/api/run/{baked}/drawer/BRAF")
        assert r0.status_code == 200
        rec = r0.json()["recommendation"]
        assert rec["verdict"] == r.GO
        assert [c["status"] for c in rec["criteria"]] == [r.MET, r.MET, r.MET]
        assert rec["thresholds"]["min_tpm"] == 1.0 and rec["rule_version"]
