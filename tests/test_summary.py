"""The baked per-gene evidence summary (`summary_llm.py`).

Three claims. The prompt builder puts EVERY fact the drawer carries in front of
the model and names every ring without a value as absent -- a summary that was
silently not told about a ring would read that ring as nothing rather than as
missing. Demo mode serves a baked summary through the drawer file route and
never constructs a model client. And a run with no baked summary renders --
table payload and drawer route -- with no error and no `summary` key.

No network and no model: `FakeSummaryClient` throughout, and the real client's
constructor is patched to explode where a test asserts it is never reached.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from targetring import config
from targetring import spend as spend_mod
from targetring import summary_llm
from targetring import table as table_mod
from targetring import view
from targetring.app import app

from test_table import a_real_run

# A drawer in the shape `drawer_bake.py` writes, with one ring hatched and one
# failed, so the "absent evidence" rule has something to catch.
DRAWER = {
    "gene": "BRAF", "input_symbol": "BRAF", "ensembl_id": "ENSG00000157764",
    "logfc": -1.8, "padj": 2e-5, "k": 7, "k_max": 9,
    "pipeline_summary": "3 agents · 1 with an active trial",
    "severe_liabilities": [{"event": "cardiac arrhythmia", "matched": "arrhythm"}],
    "safety_liabilities": [{"event": "cardiac arrhythmia"}],
    "drugs": [{"drug": "VEMURAFENIB", "max_stage": "APPROVAL"},
              {"drug": "X-1", "max_stage": "PHASE_2"}, {"drug": "X-2", "max_stage": None}],
    "trials": {"n_distinct_trials": 40, "n_distinct_live": 3, "n_distinct_stopped": 9,
               "n_pairs_for_cause": 2},
    "tdl": {"state": "value", "value": 3.0, "detail": "Tclin (3/3)", "raw": {"tdl": "Tclin"}},
    "literature": {"pubtator_count": 12345, "europepmc_count": 9876},
    "chembl": {"state": "mechanisms", "n_mechanisms": 14, "n_direct": 14, "n_direct_efficacy": 12},
    "mouse_phenotypes": ["embryonic lethality", "abnormal heart morphology"],
    "cells": {
        "logfc": {"state": "value", "value": -1.8, "detail": "logFC -1.8 (submitted)"},
        "neg_log10_padj": {"state": "value", "value": 4.7, "detail": "adjusted p 2e-05 (submitted)"},
        "tractable": {"state": "value", "value": 1.0, "detail": "mean of 3/3 keys"},
        "chemical_probe": {"state": "no_data", "value": None, "detail": "key absent"},
        "safety": {"state": "failed", "value": None, "detail": "Open Targets 502"},
        "regional_specificity": {"state": "value", "value": 0.4,
                                 "detail": "3.1 TPM in Lung / 7.7 TPM median across 54 GTEx tissues"},
        "clinical_phase": {"state": "value", "value": 5.0, "detail": "approved for melanoma"},
        "clinical_activity": {"state": "value", "value": 1.0, "detail": "live trials"},
        "tdl": {"state": "value", "value": 3.0, "detail": "Tclin (3/3)"},
        "rodent": {"state": "value", "value": 1.0, "detail": "phenotyped, abnormal"},
        "literature": {"state": "value", "value": 4.09, "detail": "12345 PubTator3 records"},
        "completeness": {"state": "value", "value": 7.0, "detail": "7 of 9"},
    },
}

MANIFEST = {
    "region": "Lung",
    "contrast": {"accession": "E-GEOD-101794", "contrast_id": "g2_g1",
                 "name": "'Crohn's disease' vs 'control'",
                 "sign_sentence": "positive log2FC = up in Crohn's disease (n=61) vs control (n=13)",
                 "test_label": "Crohn's disease", "ref_label": "control",
                 "n_test": 61, "n_ref": 13},
    "ledger": [{"input": "Braf", "status": "renamed", "resolved_symbol": "BRAF",
                "reason": "case-normalised"}],
}


class TestThePromptBuilderIncludesEveryFact:
    def test_every_available_fact_reaches_the_model(self) -> None:
        facts = summary_llm.build_facts(DRAWER, MANIFEST)
        sent = summary_llm.user_content(facts)
        # The study: numbers, direction, and the sign sentence that defines "up".
        assert facts["study"]["log2_fold_change"] == -1.8
        assert facts["study"]["adjusted_p"] == 2e-5
        assert facts["study"]["direction"] == "down in the test group"
        assert "positive log2FC = up in Crohn's disease" in sent
        # Every one of the twelve rings, with its state, value and detail.
        assert [r["ring"] for r in facts["rings"]] == list(summary_llm.RING_KEYS)
        for key, c in DRAWER["cells"].items():
            r = next(r for r in facts["rings"] if r["ring"] == key)
            assert (r["state"], r["value"], r["detail"]) == (c["state"], c["value"], c["detail"])
        # Mouse terms, GTEx detail, region.
        assert facts["mouse_phenotypes"] == ["embryonic lethality", "abnormal heart morphology"]
        assert "3.1 TPM in Lung" in sent and facts["region"] == "Lung"
        # Pipeline: counts, stages, live/stopped/for-cause, liabilities.
        p = facts["pipeline"]
        assert p["n_drugs"] == 3 and p["stages_reached"] == ["APPROVAL", "PHASE_2"]
        assert (p["n_distinct_trials"], p["n_live_trials"], p["n_stopped_trials"],
                p["n_stopped_for_cause"]) == (40, 3, 9, 2)
        assert p["severe_safety_liabilities"] == ["cardiac arrhythmia"]
        # ChEMBL direct-interaction flag, TDL, literature, k, ledger.
        assert facts["chembl"]["n_direct_interaction"] == 14
        assert facts["tdl"]["label"] == "Tclin"
        assert facts["literature"]["pubtator_records"] == 12345
        assert (facts["k"], facts["k_max"]) == (7, 9)
        assert facts["ledger_status"].startswith("renamed")

    def test_rings_without_a_value_are_named_as_absent(self) -> None:
        facts = summary_llm.build_facts(DRAWER, MANIFEST)
        assert facts["rings_without_a_value"] == ["chemical_probe", "safety"]
        states = {r["ring"]: r["state"] for r in facts["rings"]}
        assert states["chemical_probe"] == "no_data" and states["safety"] == "failed"

    def test_a_ring_the_run_did_not_draw_is_reported_not_dropped(self) -> None:
        d = json.loads(json.dumps(DRAWER))
        del d["cells"]["tdl"]
        facts = summary_llm.build_facts(d, MANIFEST)
        r = next(r for r in facts["rings"] if r["ring"] == "tdl")
        assert r["state"] == "not_drawn"
        assert "tdl" in facts["rings_without_a_value"]

    def test_the_prompt_says_no_outside_knowledge_and_carries_the_ring_definitions(self) -> None:
        sp = summary_llm.system_prompt()
        assert "ONLY source of information" in sp
        assert "Do not add anything you know" in sp
        assert 'starting with the word "Hypothesis:"' in sp
        for key in summary_llm.RING_KEYS:
            assert f"- {key}:" in sp, key
        # The hash covers the ring definitions: change one, change the version.
        assert len(summary_llm.prompt_hash()) == 16

    def test_identical_facts_share_a_key_and_different_ones_do_not(self) -> None:
        f1 = summary_llm.build_facts(DRAWER, MANIFEST)
        f2 = summary_llm.build_facts(json.loads(json.dumps(DRAWER)), MANIFEST)
        assert summary_llm.facts_key(f1, "m") == summary_llm.facts_key(f2, "m")
        d = json.loads(json.dumps(DRAWER))
        d["logfc"] = 2.0
        assert summary_llm.facts_key(summary_llm.build_facts(d, MANIFEST), "m") != \
            summary_llm.facts_key(f1, "m")


@pytest.fixture
def fake_run(monkeypatch, tmp_path):
    """A real run's artefacts copied under a temporary RUNS_DIR, with a baked
    drawer for its first gene, so the bake can be exercised without touching
    `runs/` or spending anything."""
    import shutil
    real = a_real_run()
    src = config.RUNS_DIR / real
    dst = tmp_path / real
    dst.mkdir()
    for name in ("values.csv", "manifest.json"):
        shutil.copy(src / name, dst / name)
    monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    t = table_mod.load(real)
    gene = t.genes[0]
    d = json.loads(json.dumps(DRAWER))
    d.update({"gene": gene, "input_symbol": gene, "ensembl_id": t.ensembl.get(gene)})
    (dst / table_mod.DRAWER_DIR).mkdir()
    (dst / table_mod.DRAWER_DIR / f"{gene}.json").write_text(json.dumps(d))
    monkeypatch.setattr(spend_mod, "default_ledger",
                        lambda: spend_mod.SpendLedger(tmp_path / "ledger.jsonl"))
    return real, gene, dst


class TestTheBake:
    def test_writes_text_model_prompt_version_date_and_inputs(self, fake_run) -> None:
        run, gene, dst = fake_run
        client = summary_llm.FakeSummaryClient(text="Hypothesis: restoration, because ...")
        ledger = spend_mod.SpendLedger(dst.parent / "ledger.jsonl")
        before = dst.stat().st_mtime_ns
        rep = summary_llm.bake_summaries([run], client=client, ledger=ledger, log=lambda s: None)
        assert rep.runs[run]["written"] == [gene]
        assert len(rep.runs[run]["skipped"]) == len(table_mod.load(run).genes) - 1
        s = json.loads((dst / table_mod.DRAWER_DIR / f"{gene}.json").read_text())["summary"]
        assert s["text"].startswith("Hypothesis:")
        assert s["model"] == config.SUMMARY_LLM_MODEL
        assert s["prompt_version"] == summary_llm.prompt_hash()
        assert s["generated_at"].endswith("Z")
        assert s["inputs"] == summary_llm.build_facts(
            json.loads((dst / table_mod.DRAWER_DIR / f"{gene}.json").read_text()),
            table_mod.load(run).manifest)
        assert s["usage"]["usd"] > 0
        # The spend is on its own account, reserved then committed.
        assert ledger.spent(spend_mod.SUMMARY) == pytest.approx(s["usage"]["usd"])
        assert ledger.spent(spend_mod.PHENOTYPE) == 0
        # The run directory is not made "newer" by the bake (test_table's rule).
        assert dst.stat().st_mtime_ns == before

    def test_a_second_bake_keeps_and_does_not_call(self, fake_run) -> None:
        run, gene, dst = fake_run
        ledger = spend_mod.SpendLedger(dst.parent / "ledger.jsonl")
        c1 = summary_llm.FakeSummaryClient()
        summary_llm.bake_summaries([run], client=c1, ledger=ledger, log=lambda s: None)
        c2 = summary_llm.FakeSummaryClient()
        rep = summary_llm.bake_summaries([run], client=c2, ledger=ledger, log=lambda s: None)
        assert rep.runs[run]["kept"] == [gene] and c2.calls == []

    def test_a_failed_call_writes_nothing_and_is_on_the_ledger(self, fake_run) -> None:
        run, gene, dst = fake_run
        ledger = spend_mod.SpendLedger(dst.parent / "ledger.jsonl")
        rep = summary_llm.bake_summaries([run], client=summary_llm.FakeSummaryClient(fail=True),
                                         ledger=ledger, log=lambda s: None)
        assert rep.runs[run]["written"] == []
        assert any(s.startswith(gene) for s in rep.runs[run]["skipped"])
        d = json.loads((dst / table_mod.DRAWER_DIR / f"{gene}.json").read_text())
        assert "summary" not in d
        assert ledger.calls(spend_mod.SUMMARY) == 1

    def test_the_cap_stops_the_bake_before_the_call(self, fake_run, monkeypatch) -> None:
        run, gene, dst = fake_run
        monkeypatch.setattr(config, "SUMMARY_LLM_SPEND_CAP_USD", 0.001)
        client = summary_llm.FakeSummaryClient()
        rep = summary_llm.bake_summaries([run], client=client,
                                         ledger=spend_mod.SpendLedger(dst.parent / "l.jsonl"),
                                         log=lambda s: None)
        assert rep.stopped and "No call was made" in rep.stopped and client.calls == []


class TestDemoServesABakedSummaryAndNeverCallsTheModel:
    @pytest.fixture
    def demo(self, fake_run, monkeypatch):
        run, gene, dst = fake_run
        ledger = spend_mod.SpendLedger(dst.parent / "ledger.jsonl")
        summary_llm.bake_summaries([run], client=summary_llm.FakeSummaryClient(
            text="Hypothesis: restoration, because the gene is down-regulated."),
            ledger=ledger, log=lambda s: None)
        monkeypatch.setattr(config, "DEMO_MODE", True)
        monkeypatch.setattr(config, "DEMO_RUNS", (run,))

        def boom(*a, **k):
            raise AssertionError("a model client was constructed on a demo box")
        monkeypatch.setattr(summary_llm, "AnthropicSummaryClient", boom)
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", boom)
        return run, gene

    def test_the_drawer_route_carries_the_summary(self, demo) -> None:
        run, gene = demo
        r = TestClient(app).get(f"/api/run/{run}/drawer/{gene}")
        assert r.status_code == 200, r.text[:200]
        s = r.json()["summary"]
        assert s["text"].startswith("Hypothesis:")
        assert s["model"] and s["prompt_version"] and s["generated_at"] and s["inputs"]

    def test_the_table_payload_names_the_gene_as_baked(self, demo) -> None:
        run, gene = demo
        body = TestClient(app).get(f"/api/run/{run}/values").json()
        assert gene in body["baked_drawers"]


class TestARunWithoutSummariesRendersWithoutError:
    def test_table_and_drawer_route(self, fake_run, monkeypatch) -> None:
        run, gene, dst = fake_run
        monkeypatch.setattr(config, "DEMO_MODE", False)
        client = TestClient(app)
        assert client.get(f"/api/run/{run}/values").status_code == 200
        payload = view.table_payload(run)
        assert gene in payload["baked_drawers"]
        r = client.get(f"/api/run/{run}/drawer/{gene}")
        assert r.status_code == 200 and "summary" not in r.json()
        # A gene with no drawer at all is the drawer route's own 404, unchanged.
        other = [g for g in table_mod.load(run).genes if g != gene]
        if other:
            assert client.get(f"/api/run/{run}/drawer/{other[0]}").status_code == 404

    def test_the_script_refuses_under_demo(self, monkeypatch) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bake_summaries", config.REPO_ROOT / "scripts" / "bake_summaries.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(config, "DEMO_MODE", True)
        assert mod.main(["x"], False, None) == 1
