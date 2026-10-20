"""The 2026-10-18 geneticist walkthrough fixes (Puck: "fix walkthrough").

Seven findings on the four Crohn's demo runs, each with a test here:

(a) the runs were judged "expressed in Lung" because the analyse form's silent
    default went through -- a contrast run now names its region or infers it
    from the disease (cached chooser answer), or is refused with "choose a
    region"; the paste path keeps its default;
(b) "38.5x Lung" read as tissue-specific -- the expression line is two
    absolute TPMs and says "concentrated" only above a documented ratio AND in
    a disease tissue;
(c) the phenotype criterion was met by bone density and gallstones -- a
    disease-area match is printed beside it, as a display aid, from a
    documented keyword mapping;
(d) "up in Crohn's so inhibition" ignored infiltrating cells -- the summary
    prompt requires the infiltration sentence when the GTEx blood/spleen TPM
    says so;
(e) "not druggable" sat beside "Phase 3, 12 agents" -- the prompt requires one
    reconciling sentence naming the agent; jargon keys are translated;
(f) the Evidence tab said "the call is yours" under a GO pill -- one voice;
(g) the demo index's first figure (a Liver fixture) sat above a Crohn's run's
    tabs -- a disk run shows its own figure.png or nothing.

No network, no model: fakes throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import app as app_mod
from targetring import config
from targetring import recommend as r
from targetring import region_llm
from targetring import summary_llm
from targetring import view as view_mod

STATIC = Path(__file__).resolve().parent.parent / "targetring" / "static"
REGIONS = ["Lung", "Small_Intestine_Terminal_Ileum", "Whole_Blood", "Spleen"]
ILEUM = "Small_Intestine_Terminal_Ileum"
CROHNS_MANIFEST = {
    "region": ILEUM,
    "contrast": {"name": "'Crohn's disease' vs 'non inflammatory bowel disease control'",
                 "test_label": "A1a Paris classification; Crohn's disease"},
    "run_conditions": [{"condition": f"region {ILEUM} was inferred from the disease, not chosen",
                        "affects": "the regional-specificity ring only"}],
}
LUNG_DEFAULT_MANIFEST = {"region": "Lung", "contrast": CROHNS_MANIFEST["contrast"],
                         "run_conditions": []}


class FakeCache:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get(self, source, key):
        return self.store.get((source, key))

    def put(self, source, key, body):
        self.store[(source, key)] = body


def _cache_with_crohns_answer() -> FakeCache:
    key = region_llm.cache_key("Crohn disease", REGIONS)
    return FakeCache({("region_llm", key): {
        "tissue": ILEUM, "reason": "Crohn disease most characteristically affects the terminal ileum.",
        "chosen_utc": "2026-10-09T03:38:20+00:00", "model": "claude-opus-5"}})


# ---------------------------------------------------------------------------
# (a) region: refuse the silent default on the contrast path
# ---------------------------------------------------------------------------


class TestContrastRunRegion:
    @pytest.fixture(autouse=True)
    def _no_gtex_no_model(self, monkeypatch):
        async def regions(_cache):
            return list(REGIONS)
        monkeypatch.setattr(app_mod, "available_regions", regions)

        def boom(*a, **k):
            raise AssertionError("a model client was constructed")
        monkeypatch.setattr(app_mod.region_llm, "AnthropicRegionClient", boom)

    async def test_an_explicit_region_is_the_readers_and_claims_nothing(self) -> None:
        got = await app_mod._region_for_contrast_run("Whole_Blood", "Crohn disease", FakeCache())
        assert got == ("Whole_Blood", False, "")

    async def test_no_region_and_no_disease_is_a_422_choose_a_region(self) -> None:
        got = await app_mod._region_for_contrast_run("", "", FakeCache())
        assert got.status_code == 422
        body = json.loads(got.body)
        assert body["error"] == "choose_a_region" and "choose a region" in body["message"]

    async def test_no_region_uses_the_cached_chooser_answer_and_marks_it_inferred(self) -> None:
        got = await app_mod._region_for_contrast_run("", "Crohn disease", _cache_with_crohns_answer())
        region, inferred, reason = got
        assert region == ILEUM and inferred is True
        assert "terminal ileum" in reason and "region chooser" in reason
        assert "remembered from 2026-10-09" in reason, "a cached answer says it is cached"

    async def test_no_region_and_no_answer_is_a_422_not_the_default(self, monkeypatch) -> None:
        # No cache hit and no client (the constructor is patched to fail, as on
        # a machine with no key): nothing is substituted, Lung least of all.
        got = await app_mod._region_for_contrast_run("", "Crohn disease", FakeCache())
        assert got.status_code == 422
        body = json.loads(got.body)
        assert body["error"] == "choose_a_region"
        assert "Lung" not in body["message"]

    def test_the_route_refuses_before_any_fetch(self, monkeypatch) -> None:
        """A contrast run posted with no region and no disease is refused by the
        route itself, before Atlas is asked for anything."""
        class NoHttp:
            def __init__(self, *a, **k):
                raise AssertionError("Http was constructed: a fetch was attempted")
        monkeypatch.setattr(app_mod, "Http", NoHttp)
        c = TestClient(app_mod.app)
        res = c.post("/api/analyse", data={
            "source": "atlas", "accession": "E-GEOD-101794", "contrast_id": "g2_g1",
            "n": 60, "direction": "both", "ensembl_ids": json.dumps(["ENSG00000185499"])})
        assert res.status_code == 422, res.text
        assert res.json()["error"] == "choose_a_region"

    def test_the_form_default_is_empty_not_lung(self) -> None:
        import inspect
        sig = inspect.signature(app_mod.analyse)
        assert sig.parameters["region"].default.default == ""
        assert "disease" in sig.parameters

    def test_the_page_posts_an_untouched_region_as_empty_with_the_disease(self) -> None:
        js = (STATIC / "app.js").read_text()
        assert 'fd.append("region", (REGION_TOUCHED || REGION_WAS_INFERRED) ? $("#region").value : "")' in js
        assert 'fd.append("disease", (PICKER.disease && (PICKER.disease.label || PICKER.disease.current_id)) || "")' in js
        assert 'sel.addEventListener("change", () => { REGION_TOUCHED = true; })' in js


# ---------------------------------------------------------------------------
# (b) the expression line: two TPMs, the region named and judged
# ---------------------------------------------------------------------------

T = r.Thresholds(min_tpm=1.0, min_abs_logfc=1.0, max_q=0.05, min_phenotypes=1, specific_ratio=5.0)
BASE = dict(region="Lung", region_tpm=26.61, panel_median_tpm=1.73, n_tissues=54,
            logfc=2.2, padj=4e-14, rodent_ring=1.0, rodent_state="value",
            n_abnormal_phenotypes=6)


def crit(rec, key):
    return next(c for c in rec["criteria"] if c["key"] == key)


class TestExpressionPhrasing:
    def test_the_criterion_names_the_region_as_the_runs_chosen_tissue(self) -> None:
        rec = r.recommend(r.Facts(**BASE), T)
        assert crit(rec, "expressed")["shown"] == "26.61 TPM in Lung, the run's chosen tissue"

    def test_the_silent_default_is_called_not_a_disease_tissue(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "region_is_disease_tissue": False}), T)
        assert "not a disease tissue for this contrast" in crit(rec, "expressed")["shown"]
        assert "not a disease tissue for this contrast" in rec["expression"]["shown"]
        assert rec["verdict"] == r.GO, "the wording changes, the verdict does not"

    def test_an_inferred_region_is_called_a_disease_tissue(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "region": ILEUM, "region_tpm": 19.06,
                                     "region_is_disease_tissue": True}), T)
        assert "a disease tissue for this contrast" in crit(rec, "expressed")["shown"]

    def test_two_absolute_tpms_and_no_fold(self) -> None:
        e = r.recommend(r.Facts(**BASE), T)["expression"]
        assert e["shown"] == ("26.61 TPM in Lung, the run's chosen tissue; median across "
                              "54 tissues 1.73 TPM")
        for word in ("x the", "fold", "specific", "enriched", "concentrated"):
            assert word not in e["shown"].lower()
        assert e["specific"] is False
        assert e["ratio"] == pytest.approx(26.61 / 1.73)

    def test_concentrated_needs_the_ratio_and_a_disease_tissue(self) -> None:
        hi = {**BASE, "region": ILEUM, "region_tpm": 50.0, "panel_median_tpm": 2.0}
        # Above 5x but the region is the silent default: no specificity word.
        e = r.recommend(r.Facts(**{**hi, "region_is_disease_tissue": False}), T)["expression"]
        assert "concentrated" not in e["shown"] and e["specific"] is False
        # Above 5x and chosen by hand (no claim either way): still no word.
        e = r.recommend(r.Facts(**{**hi, "region_is_disease_tissue": None}), T)["expression"]
        assert "concentrated" not in e["shown"] and e["specific"] is False
        # Above 5x in a disease tissue: the word, with the ratio and the line.
        e = r.recommend(r.Facts(**{**hi, "region_is_disease_tissue": True}), T)["expression"]
        assert e["specific"] is True
        assert "concentrated in Small Intestine Terminal Ileum (25.0x the panel median, above the documented 5x line)" in e["shown"]
        # In a disease tissue but under the line: no word.
        e = r.recommend(r.Facts(**{**hi, "region_tpm": 8.0, "region_is_disease_tissue": True}), T)["expression"]
        assert e["specific"] is False and "concentrated" not in e["shown"]
        # Above the line, in a disease tissue, but spleen is above the region
        # (CXCL1 in the ileum: 19 TPM against 213 in spleen): no word.
        e = r.recommend(r.Facts(**{**hi, "region_is_disease_tissue": True,
                                   "spleen_tpm": 213.0}), T)["expression"]
        assert e["specific"] is False and "concentrated" not in e["shown"]
        assert e["blood_or_spleen_at_or_above_region"] is True

    def test_the_threshold_is_documented_in_config_and_the_spec(self) -> None:
        assert config.RECOMMEND_SPECIFIC_RATIO == 5.0
        spec = (Path(__file__).resolve().parent.parent / "docs" / "RECOMMEND-SPEC.md").read_text()
        assert "RECOMMEND_SPECIFIC_RATIO" in spec and "not a disease tissue for this contrast" in spec

    def test_the_display_changes_do_not_change_the_rule_version(self) -> None:
        assert r.rule_version(T) == r.rule_version(r.Thresholds(**{**T.__dict__, "specific_ratio": 99.0}))

    def test_disease_tissue_is_read_off_the_manifest(self) -> None:
        assert r.region_is_disease_tissue_from_manifest(CROHNS_MANIFEST) is True
        assert r.region_is_disease_tissue_from_manifest(LUNG_DEFAULT_MANIFEST) is False
        assert r.region_is_disease_tissue_from_manifest({"region": "Whole_Blood", "run_conditions": []}) is None
        assert r.region_is_disease_tissue_from_manifest(None) is None

    def test_facts_from_drawer_reads_the_median_off_the_cell_detail(self) -> None:
        d = {"cells": {"regional_specificity": {
            "state": "value", "value": 21.6,
            "detail": "151.2 TPM in Lung / 6.992 TPM median across 54 GTEx tissues (above the 1 TPM floor)"}}}
        f = r.facts_from_drawer(d, LUNG_DEFAULT_MANIFEST)
        assert (f.region_tpm, f.panel_median_tpm, f.n_tissues) == (151.2, 6.992, 54)
        assert f.region_is_disease_tissue is False
        assert "Crohn" in f.disease_text


# ---------------------------------------------------------------------------
# (c) the disease-area display aid
# ---------------------------------------------------------------------------

CXCL1_TERMS = ("decreased bone mineral density", "abnormal eye morphology",
               "decreased bone mineral content", "increased monocyte cell number",
               "enlarged lymph nodes", "abnormal lymph node morphology")
MUC1_TERMS = ("no abnormal phenotype detected", "abnormal gallbladder physiology",
              "abnormal intestinal lipid absorption", "decreased digestive secretion",
              "gallstones", "decreased tumor growth/size")


class TestDiseaseAreaMatch:
    def test_the_contrast_text_selects_the_area(self) -> None:
        assert r.disease_area_for("'Crohn's disease' vs 'control'") == "inflammatory bowel disease"
        assert r.disease_area_for("ulcerative colitis biopsy") == "inflammatory bowel disease"
        assert r.disease_area_for("COVID-19 vs healthy") is None
        assert r.disease_area_for(None) is None

    def test_yes_lists_the_matching_terms_and_their_bucket(self) -> None:
        f = r.Facts(**{**BASE, "mouse_phenotypes": CXCL1_TERMS, "disease_text": "Crohn's disease"})
        a = r.disease_area_match(f)
        assert a["matched"] is True
        assert [m["term"] for m in a["matching_terms"]] == [
            "increased monocyte cell number", "enlarged lymph nodes", "abnormal lymph node morphology"]
        assert all(m["area"] == "immune system" for m in a["matching_terms"])
        assert a["shown"].startswith("disease-area match: yes (inflammatory bowel disease)")
        assert "bone mineral" not in a["shown"]

    def test_digestive_terms_match_too(self) -> None:
        f = r.Facts(**{**BASE, "mouse_phenotypes": MUC1_TERMS, "disease_text": "Crohn's disease",
                       "n_abnormal_phenotypes": 5})
        a = r.disease_area_match(f)
        assert a["matched"] is True
        areas = {m["term"]: m["area"] for m in a["matching_terms"]}
        assert areas["abnormal intestinal lipid absorption"] == "digestive/alimentary system"
        assert areas["gallstones"] == "digestive/alimentary system"
        assert "no abnormal phenotype detected" not in areas
        assert "decreased tumor growth/size" not in areas

    def test_no_says_met_on_n_terms_none_in_the_area(self) -> None:
        f = r.Facts(**{**BASE, "mouse_phenotypes": ("decreased bone mineral density", "abnormal eye morphology"),
                       "n_abnormal_phenotypes": 2, "disease_text": "Crohn's disease"})
        a = r.disease_area_match(f)
        assert a["matched"] is False
        assert a["shown"].startswith("disease-area match: no — met on 2 term(s), none in the disease area")

    def test_an_unmapped_contrast_claims_nothing(self) -> None:
        f = r.Facts(**{**BASE, "mouse_phenotypes": CXCL1_TERMS, "disease_text": "COVID-19"})
        a = r.disease_area_match(f)
        assert a["matched"] is None and "no mapping" in a["shown"]

    def test_it_sits_beside_the_phenotype_criterion_and_is_labelled_an_aid(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "mouse_phenotypes": CXCL1_TERMS,
                                     "disease_text": "Crohn's disease"}), T)
        ph = crit(rec, "phenotype")
        assert ph["status"] == r.MET, "the owner's rule: any abnormal term meets it"
        assert ph["disease_area"]["matched"] is True
        assert "display aid, not a criterion" in ph["disease_area"]["note"]
        assert rec["disease_area"] == ph["disease_area"]
        assert rec["disease_area"]["mapping_version"] == r.mapping_version()

    def test_the_match_never_moves_the_verdict(self) -> None:
        yes = r.recommend(r.Facts(**{**BASE, "mouse_phenotypes": CXCL1_TERMS,
                                     "disease_text": "Crohn's disease"}), T)
        no = r.recommend(r.Facts(**{**BASE, "mouse_phenotypes": ("abnormal eye morphology",),
                                    "n_abnormal_phenotypes": 1, "disease_text": "Crohn's disease"}), T)
        assert yes["verdict"] == no["verdict"] == r.GO

    def test_the_mapping_is_documented_as_a_display_aid(self) -> None:
        spec = (Path(__file__).resolve().parent.parent / "docs" / "RECOMMEND-SPEC.md").read_text()
        assert "display aid, not a criterion" in spec
        assert "digestive/alimentary system" in spec and "immune system" in spec and "inflammation" in spec
        for area, s in r.DISEASE_AREAS.items():
            assert area in spec
            assert set(s["areas"]) == {"digestive/alimentary system", "immune system", "inflammation"}

    def test_the_page_prints_it_beside_the_criterion(self) -> None:
        """Still beside the criterion after the 2026-10-18 18:42 trim, but as a
        count with the terms in a tooltip (tests/test_trim.py). The long
        `shown` sentence is still computed and still in the JSON."""
        js = (STATIC / "app.js").read_text()
        assert "c.disease_area && c.disease_area.short" in js
        assert "c.disease_area.tooltip" in js
        rec = r.recommend(r.Facts(**{**BASE, "mouse_phenotypes": CXCL1_TERMS,
                                     "disease_text": "Crohn's disease"}), T)
        assert "display aid, not a criterion" in rec["disease_area"]["tooltip"].lower()
        assert rec["disease_area"]["shown"].startswith("disease-area match: yes")


# ---------------------------------------------------------------------------
# (d) + (e) the summary prompt and its facts
# ---------------------------------------------------------------------------

MUC1_DRAWER = {
    "gene": "MUC1", "logfc": 3.1, "padj": 2.2e-22,
    "mouse_phenotypes": list(MUC1_TERMS),
    "drugs": [{"drug": "TECEMOTIDE", "modality": "Protein", "max_stage": "PHASE_3", "phase_ordinal": 4,
               "indications": ["non-small cell lung carcinoma", "breast cancer"]},
              {"drug": "SONTUZUMAB", "modality": "Antibody", "max_stage": "PHASE_2", "phase_ordinal": 3,
               "indications": ["breast cancer"]}],
    "expression": {"region": ILEUM, "region_tpm": 9.3, "panel_median_tpm": 6.99, "n_tissues": 54,
                   "region_is_disease_tissue": True, "whole_blood_tpm": 0.6, "spleen_tpm": 3.0},
    "contrast_text": "'Crohn's disease' vs 'control' Crohn's disease",
    "cells": {"tractable": {"state": "value", "value": 0.0, "detail": "mean of 3/3 keys"},
              "rodent": {"state": "value", "value": 1.0, "detail": ""}},
}


class TestSummaryPromptAndFacts:
    def test_the_prompt_requires_the_infiltration_sentence(self) -> None:
        sp = summary_llm.system_prompt()
        assert "The change may reflect infiltrating immune cells rather than regulation in the tissue" in sp
        assert 'REQUIRED when "immune_origin_hint.inflamed_tissue_contrast" is true' in sp

    def test_the_prompt_no_longer_reconciles_druggability_with_the_pipeline(self) -> None:
        """Reversed by Puck on 2026-10-18 18:42 PDT ("can remove druggability
        caveat"): the caveat it reconciled is gone from the box and from the
        rule's output, so the sentence that explained it away has no subject.
        What is forbidden now is any sentence about druggability at all."""
        sp = summary_llm.system_prompt()
        assert "not a <disease> lead" not in sp
        assert 'names low druggability' not in sp
        assert ("Never write a sentence about druggability, tractability, a "
                "chemical probe, a binding pocket or a safety flag") in sp

    def test_the_prompt_forbids_the_fold_and_the_jargon(self) -> None:
        sp = summary_llm.system_prompt()
        assert "never write a fold" in sp
        assert "The reader is a biologist" in sp
        for key in ("hasPocket", "hasLigand", "hasSmallMoleculeBinder", "Tbio", "Tchem",
                    "geneticConstraint", "hasSafetyEvent", "isCancerDriverGene"):
            assert f'"{key}" ->' in sp, key
        assert 'Write "adjusted p", not "q" or "padj"' in sp

    def test_immune_origin_hint_fires_on_spleen_or_blood_at_or_above_the_region(self) -> None:
        # CXCL1 in ileum: spleen 213 TPM >= ileum 19 TPM.
        f = r.Facts(**{**BASE, "region": ILEUM, "region_tpm": 19.06, "whole_blood_tpm": 10.56,
                       "spleen_tpm": 213.6, "disease_text": "Crohn's disease"})
        h = r.immune_origin_hint(f, T)
        assert h["suggested"] is True and h["inflamed_tissue_contrast"] is True
        assert h["reasons"] == ["spleen 213.6 TPM ≥ Small Intestine Terminal Ileum 19.06 TPM"]
        # MUC1 in ileum: blood 0.6, spleen 3.0, both under ileum 9.3.
        f = r.Facts(**{**BASE, "region": ILEUM, "region_tpm": 9.3, "whole_blood_tpm": 0.6,
                       "spleen_tpm": 3.0, "disease_text": "Crohn's disease"})
        assert r.immune_origin_hint(f, T)["suggested"] is False
        # Nothing baked: nothing claimed.
        f = r.Facts(**{**BASE, "disease_text": "Crohn's disease"})
        assert r.immune_origin_hint(f, T)["suggested"] is None

    def test_the_facts_carry_expression_hint_area_and_furthest_agent(self) -> None:
        d = dict(MUC1_DRAWER)
        d["recommendation"] = r.recommend_drawer(d, T)
        facts = summary_llm.build_facts(d, {"region": ILEUM})
        assert facts["expression"]["shown"].startswith("9.3 TPM in Small Intestine Terminal Ileum, the run's chosen tissue (a disease tissue for this contrast); median across 54 tissues 6.99 TPM")
        assert facts["immune_origin_hint"]["suggested"] is False
        assert facts["pipeline"]["furthest_agent"] == {
            "name": "TECEMOTIDE", "stage": "Phase 3", "modality": "Protein",
            "indications": ["non-small cell lung carcinoma", "breast cancer"]}
        assert facts["recommendation"]["disease_area"]["matched"] is True
        # MUC1's tractable ring is 0.0 and it has no severe liability row, so
        # after the 18:42 trim it carries no caveat at all (it used to carry
        # the low-druggability one).
        assert facts["recommendation"]["caveats"] == []

    def test_a_drawer_with_no_drugs_has_no_furthest_agent(self) -> None:
        facts = summary_llm.build_facts({"gene": "X", "drugs": []}, {})
        assert facts["pipeline"]["furthest_agent"] is None

    def test_the_prompt_change_changes_the_prompt_hash(self) -> None:
        # A prompt change re-buys every summary (memory: that is the cost of a
        # rewording); the hash must move so the bake knows to.
        assert summary_llm.prompt_hash() != "0000000000000000"
        assert "infiltrating immune cells" in summary_llm.SYSTEM_PROMPT_HEAD


# ---------------------------------------------------------------------------
# (f) one voice
# ---------------------------------------------------------------------------


class TestOneVoice:
    def test_the_evidence_header_names_the_rule_and_does_not_hand_the_call_back(self) -> None:
        note = view_mod.EVIDENCE_NOTE
        assert ("This surface does not rank or score genes. The GO / NO-GO line is the "
                "owner's explicit three-part rule, shown for each gene; it never orders or "
                "filters the figure.") in note
        assert "yours" not in note


# ---------------------------------------------------------------------------
# (g) no fixture figure above a disk run's tabs
# ---------------------------------------------------------------------------


class TestDiskRunFigure:
    def test_a_deep_linked_run_hides_the_demo_index_and_shows_its_own_figure_or_nothing(self) -> None:
        js = (STATIC / "app.js").read_text()
        css = (STATIC / "app.css").read_text()
        deep = js[js.index('new URLSearchParams(location.search).get("run")'):]
        # The block ends where the deep-link path hands the view over to the
        # tab router. That used to be the literal `showTab("table")`; 6a2e352
        # ("Table is the default tab") made the fallback `showTab(DEFAULT_TAB)`
        # with `DEFAULT_TAB = "table"` — the same handover, named.
        deep = deep[:deep.index("showTab(DEFAULT_TAB)")]
        assert 'const DEFAULT_TAB = "table";' in js
        assert 'document.body.classList.add("viewing-run")' in deep
        assert "body.viewing-run #demo-panel { display: none !important; }" in css
        # v4.0. The deep-link path now draws THIS run's own `figure-web.svg`,
        # inlined, so the circle is clickable (Puck, 2026-10-19: "i want the
        # target ring to be interactive too"). The claim this test was written
        # for is unchanged and is what is asserted below: the figure shown is
        # the deep-linked run's own, addressed by the id asked for, or there is
        # none — never another run's circle left over in the page's state.
        assert "showToolFigure(wanted)" in deep
        fn = js[js.index("async function showToolFigure"):]
        fn = fn[:fn.index("\n}\n") + 3]
        assert "${encodeURIComponent(runId)}/figure-web.svg" in fn
        assert "demo-runs" not in fn
        assert "if (!res.ok) throw" in fn, (
            "no exported web SVG means the grid, not a placeholder circle")
        # The `<img>` path it supersedes is still there and still honest, for a
        # surface with no inlineable SVG.
        old = js[js.index("async function showDiskRunFigure"):]
        old = old[:old.index("\n}\n") + 3]
        assert 'fetch(url, { method: "HEAD" })' in old
        assert "if (!ok) return;" in old, (
            "no figure.png means nothing, not a placeholder")
        assert "figure.png" in old and "demo-runs" not in old

    def test_the_artefact_route_answers_head_for_an_existing_figure(self, monkeypatch, tmp_path) -> None:
        from targetring import table as table_mod
        run = "20990101T000000Z-walkg1"
        (tmp_path / run).mkdir()
        (tmp_path / run / "figure.png").write_bytes(b"\x89PNG")
        monkeypatch.setattr(app_mod, "RUNS_DIR", tmp_path)
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        c = TestClient(app_mod.app)
        assert c.head(f"/api/run/{run}/figure.png").status_code == 200
        assert c.head(f"/api/run/{run}/legend.png").status_code == 404
