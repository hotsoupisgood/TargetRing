"""Which model each mouse phenotype term came from.

Puck, 2026-10-18 19:15 PDT: *"can you state which model each term came from"*.
The drawer carried `mouse_phenotypes`, a flat list of labels, while the Open
Targets query had been fetching `biologicalModels{id allelicComposition
geneticBackground literature}` all along.

What is held here:

(a) `phenotype.terms_with_models` — one entry per TERM, models underneath,
    labels deduplicated, a term with no model keeps an empty list, the PMIDs
    stay the MODEL's literature;
(b) the baked drawer carries `phenotype_terms` AND the unchanged flat
    `mouse_phenotypes`, and nothing that decides anything reads the new key —
    so no verdict, prompt hash or facts key can move;
(c) the page renders the models under each term, the verdict box's phenotype
    tooltip and its `models: n`, and a drawer baked before the field falls back
    to the bare labels;
(d) the four demo runs' drawers on disk have it, and a demo box serves it as a
    static file read.

Spec: docs/RECOMMEND-SPEC.md, last section.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from targetring import config
from targetring import phenotype as phenotype_mod
from targetring import phenotype_llm
from targetring import recommend as r
from targetring.adapters.opentargets import OTRecord

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "targetring" / "static"
SPEC = ROOT / "docs" / "RECOMMEND-SPEC.md"

#: Two models curating the same label (the dedupe case), one model repeated
#: under one label, a term with no model at all, and a term whose model has no
#: literature.
RECORDS: list[dict[str, Any]] = [
    {"modelPhenotypeId": "MP:0005387", "modelPhenotypeLabel": "immune system phenotype",
     "targetInModel": "Cxcl1",
     "biologicalModels": [
         {"id": "MGI:3042220", "allelicComposition": "Cxcl1<tm1>/Cxcl1<tm1>",
          "geneticBackground": "C57BL/6", "literature": ["12345", "12345", "67890"]},
         {"id": "MGI:3042221", "allelicComposition": "Cxcl1<tm2>/Cxcl1<+>",
          "geneticBackground": "B6.129", "literature": []},
     ]},
    {"modelPhenotypeId": "MP:0005387", "modelPhenotypeLabel": "immune system phenotype",
     "targetInModel": "Cxcl1",
     "biologicalModels": [
         {"id": "MGI:3042220", "allelicComposition": "Cxcl1<tm1>/Cxcl1<tm1>",
          "geneticBackground": "C57BL/6", "literature": ["12345"]},
     ]},
    {"modelPhenotypeId": "MP:0002169", "modelPhenotypeLabel": "no abnormal phenotype detected",
     "targetInModel": "Cxcl1", "biologicalModels": []},
]


def rec(records: list[dict[str, Any]] | None = None) -> OTRecord:
    return OTRecord("ENSG00000163739", found=True, approved_symbol="CXCL1",
                    mouse_phenotypes=list(RECORDS if records is None else records))


# ---------------------------------------------------------------------------
# (a) the grouping
# ---------------------------------------------------------------------------


class TestTermsWithModels:
    def test_one_entry_per_term_with_its_models_underneath(self) -> None:
        terms = phenotype_mod.terms_with_models(rec())
        assert [t["label"] for t in terms] == ["immune system phenotype",
                                               "no abnormal phenotype detected"]
        assert terms[0]["mp_id"] == "MP:0005387"
        assert [m["id"] for m in terms[0]["models"]] == ["MGI:3042220", "MGI:3042221"]
        assert terms[0]["models"][0]["allelic_composition"] == "Cxcl1<tm1>/Cxcl1<tm1>"
        assert terms[0]["models"][0]["genetic_background"] == "C57BL/6"

    def test_a_label_from_several_records_appears_once(self) -> None:
        """Two source records carry the same label; the reader must not see the
        term twice and must see every model under the one it does see."""
        terms = phenotype_mod.terms_with_models(rec())
        assert sum(1 for t in terms if t["label"] == "immune system phenotype") == 1
        assert len(terms) == 2, "three records, two distinct terms"

    def test_a_model_repeated_under_one_label_is_listed_once(self) -> None:
        ids = [m["id"] for m in phenotype_mod.terms_with_models(rec())[0]["models"]]
        assert ids == sorted(set(ids), key=ids.index)

    def test_a_pmid_repeated_in_one_model_is_one_paper(self) -> None:
        models = phenotype_mod.terms_with_models(rec())[0]["models"]
        assert models[0]["pmids"] == ["12345", "67890"]

    def test_a_term_with_no_model_keeps_an_empty_list(self) -> None:
        """MGI naming no model is not the phenotype not existing (rule 1 one
        level down) -- the term is kept and says so with an empty list."""
        terms = phenotype_mod.terms_with_models(rec())
        assert terms[-1]["label"] == "no abnormal phenotype detected"
        assert terms[-1]["models"] == []

    def test_a_model_with_no_literature_keeps_an_empty_pmid_list(self) -> None:
        assert phenotype_mod.terms_with_models(rec())[0]["models"][1]["pmids"] == []

    def test_no_mouse_records_at_all_is_no_terms(self) -> None:
        assert phenotype_mod.terms_with_models(rec([])) == []

    def test_the_limit_counts_source_records_so_the_two_lists_agree(self) -> None:
        """The flat labels are `mouse_phenotypes[:N]`; the terms are read off
        the same N records, so neither list can name a term the other does not."""
        r_ = rec()
        labels = [p["modelPhenotypeLabel"] for p in r_.mouse_phenotypes[:1]]
        terms = phenotype_mod.terms_with_models(r_, 1)
        assert [t["label"] for t in terms] == labels
        assert [m["id"] for m in terms[0]["models"]] == ["MGI:3042220", "MGI:3042221"]

    def test_the_pmids_are_the_models_literature_not_the_terms(self) -> None:
        """The join `build_table_rows` refuses is still refused: nothing here
        attaches a paper to a phenotype."""
        terms = phenotype_mod.terms_with_models(rec())
        assert all("pmids" not in t for t in terms)
        assert all("pmids" in m for t in terms for m in t["models"])

    def test_the_limit_default_is_the_one_the_drawers_use(self) -> None:
        assert phenotype_mod.DRAWER_TERMS_LIMIT == 25


# ---------------------------------------------------------------------------
# (b) the baked schema, and that nothing deciding anything reads it
# ---------------------------------------------------------------------------


BAKED_TERMS = [
    {"label": "abnormal intestinal morphology", "mp_id": "MP:0002138",
     "models": [{"id": "MGI:1", "allelic_composition": "a<tm1>/a<tm1>",
                 "genetic_background": "C57BL/6", "pmids": ["111"]}]},
    {"label": "enlarged lymph nodes", "mp_id": "MP:0002166", "models": []},
]

DRAWER: dict[str, Any] = {
    "gene": "CXCL1",
    "mouse_phenotypes": ["abnormal intestinal morphology", "enlarged lymph nodes"],
    "phenotype_terms": BAKED_TERMS,
    "contrast_text": "'Crohn's disease' vs 'control' Crohn's disease",
    "cells": {"rodent": {"state": "value", "value": 1.0, "detail": "phenotyped"}},
    "expression": {"region": "Small_Intestine_Terminal_Ileum", "region_tpm": 19.06,
                   "panel_median_tpm": 6.99, "n_tissues": 54,
                   "region_is_disease_tissue": True},
    "logfc": 3.1, "padj": 2.2e-22,
}


class TestTheBakedSchema:
    def test_every_entry_is_a_label_an_id_and_a_list_of_models(self) -> None:
        for t in BAKED_TERMS:
            assert set(t) == {"label", "mp_id", "models"}
            for m in t["models"]:
                assert set(m) == {"id", "allelic_composition", "genetic_background",
                                  "pmids"}

    def test_the_flat_label_list_stays_beside_it(self) -> None:
        """Compatibility: `recommend` and `phenotype_llm` read the flat list."""
        assert [t["label"] for t in BAKED_TERMS] == DRAWER["mouse_phenotypes"]

    def test_the_rule_reads_the_labels_and_not_the_models(self) -> None:
        f = r.facts_from_drawer(DRAWER)
        assert f.mouse_phenotypes == tuple(DRAWER["mouse_phenotypes"])
        assert f.n_abnormal_phenotypes == 2
        without = r.facts_from_drawer({k: v for k, v in DRAWER.items()
                                       if k != "phenotype_terms"})
        assert r.recommend(f) == r.recommend(without), "the models move no verdict"

    def test_the_claude_flags_facts_and_key_are_unmoved(self) -> None:
        without = {k: v for k, v in DRAWER.items() if k != "phenotype_terms"}
        facts = phenotype_llm.build_facts(DRAWER)
        assert facts == phenotype_llm.build_facts(without)
        assert (phenotype_llm.facts_key(facts, "m")
                == phenotype_llm.facts_key(phenotype_llm.build_facts(without), "m"))

    def test_recommend_py_does_not_know_the_field_exists(self) -> None:
        for name in ("recommend.py", "phenotype_llm.py", "summary_llm.py"):
            body = (ROOT / "targetring" / name).read_text()
            # `phenotype_llm`'s own `mouse_phenotype_terms` facts key is the
            # flat LABEL list and is not this field; nothing reads the field.
            assert '"phenotype_terms"' not in body, name
            assert "phenotype_terms)" not in body, name

    def test_the_spec_records_it(self) -> None:
        body = SPEC.read_text()
        assert "Which model each phenotype term came from" in body
        assert "--phenotype-terms-only" in body


# ---------------------------------------------------------------------------
# (c) the page
# ---------------------------------------------------------------------------


class TestThePage:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return (STATIC / "app.js").read_text()

    def test_the_drawer_lists_each_terms_models(self, js: str) -> None:
        assert "function phenotypeTermListHtml(terms, labels)" in js
        assert "phenotypeTermListHtml(d.phenotype_terms, mp)" in js
        for bit in ("allelic_composition", "genetic_background", "pheno-models"):
            assert bit in js, bit

    def test_the_pmids_link_to_pubmed(self, js: str) -> None:
        assert 'const PUBMED = "https://pubmed.ncbi.nlm.nih.gov/";' in js
        assert "esc(PUBMED + p)" in js

    def test_a_drawer_with_no_models_renders_the_bare_labels_and_says_so(
            self, js: str) -> None:
        assert "if (!terms || !terms.length) {" in js
        assert "baked before the models were recorded" in js

    def test_a_term_with_no_model_is_not_silence(self, js: str) -> None:
        assert "names no biological model" in js

    def test_the_verdict_boxs_phenotype_tooltip_is_term_then_model_count(
            self, js: str) -> None:
        assert ("return terms.map((t) => `${t.label}: ${(t.models || []).length} "
                "model(s)`).join(\"\\n\");") in js
        assert 'c.key === "phenotype" ? phenotypeTermsTooltip(phenotypeTerms) : ""' in js

    def test_the_box_shows_models_n_beside_an_unchanged_flag_sentence(self, js: str) -> None:
        assert "function phenotypeFlagHtml(pf, terms)" in js
        assert "models: ${n}" in js
        # The sentence itself: still `pf.reason`, still nothing else.
        assert '<span class="flag-why">${esc(pf.reason || "")}</span>${models}' in js

    def test_the_small_type_is_styled(self) -> None:
        css = (STATIC / "app.css").read_text()
        for sel in ("li.pheno-model", "ul.pheno-models", ".flag-models"):
            assert sel in css, sel


# ---------------------------------------------------------------------------
# (d) the four demo runs, and a demo box serving it
# ---------------------------------------------------------------------------


def demo_drawers() -> list[Path]:
    from targetring import table as table_mod
    out: list[Path] = []
    for run_id in config.DEMO_RUNS:
        d = table_mod.run_dir(run_id) / table_mod.DRAWER_DIR
        out.extend(sorted(d.glob("*.json")))
    return out


@pytest.mark.skipif(not demo_drawers(), reason="no baked demo drawers on this machine")
class TestTheDemoRunsOnDisk:
    def test_every_baked_drawer_carries_the_terms(self) -> None:
        for p in demo_drawers():
            d = json.loads(p.read_text())
            assert "phenotype_terms" in d, p
            assert [t["label"] for t in d["phenotype_terms"]] == d["mouse_phenotypes"], p

    def test_the_entries_are_the_baked_shape(self) -> None:
        for p in demo_drawers():
            for t in json.loads(p.read_text())["phenotype_terms"]:
                assert set(t) == {"label", "mp_id", "models"}, p
                for m in t["models"]:
                    assert set(m) == {"id", "allelic_composition",
                                      "genetic_background", "pmids"}, p
                    assert isinstance(m["pmids"], list)

    def test_every_baked_pmid_is_digits_only(self) -> None:
        """A PMID is the whole PubMed link. `app.js` builds each href as the
        PubMed base plus this value with no validation, so one stray character
        in here is a dead link on the published site and nothing on the page
        says so.

        Not hypothetical. `wording.modernise` read each PMID as a count of
        four digits or more and gave it thousands separators, so `11911824`
        became `11,911,824` and every one of the 12 PubMed links in the tool
        resolved to an empty `[uid]` search. It shipped in 9f19811 and the live
        site served it until the link check of 2026-10-21. The guard is
        `wording._BARE_IDENTIFIER` plus `restamp_wording.ID_KEYS`; this is the
        assertion on the artefacts those two protect.
        """
        seen = 0
        for p in demo_drawers():
            for t in json.loads(p.read_text())["phenotype_terms"]:
                for m in t["models"]:
                    for pmid in m["pmids"]:
                        assert re.fullmatch(r"[0-9]+", pmid), (p.name, pmid)
                        seen += 1
        assert seen, "no baked PMIDs found, so this asserted nothing"

    def test_the_paid_blocks_survived_the_pass(self) -> None:
        """`--phenotype-terms-only` exists so adding a display field does not
        re-buy the summary and the flag."""
        for p in demo_drawers():
            d = json.loads(p.read_text())
            assert d.get("recommendation", {}).get("verdict")
            assert d.get("summary", {}).get("text")
            assert d.get("phenotype_llm", {}).get("flag") in (0, 1)


class TestADemoBoxServesItAsAFileRead:
    def test_the_baked_route_returns_the_terms_and_calls_nothing(
            self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from targetring import app as app_mod
        from targetring import table as table_mod
        run = "20990101T000000Z-models1"
        d = tmp_path / run / table_mod.DRAWER_DIR
        d.mkdir(parents=True)
        (d / "CXCL1.json").write_text(json.dumps(
            {"gene": "CXCL1", "mouse_phenotypes": [t["label"] for t in BAKED_TERMS],
             "phenotype_terms": BAKED_TERMS}))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        monkeypatch.setattr(config, "DEMO_MODE", True)
        monkeypatch.setattr(config, "DEMO_RUNS", (run,))

        async def boom(*a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("a demo box fetched something for the models")
        monkeypatch.setattr(app_mod, "fill_drawer_only_sources", boom)

        body = TestClient(app_mod.app).get(f"/api/run/{run}/drawer/CXCL1").json()
        assert body["phenotype_terms"] == BAKED_TERMS
        assert body["mouse_phenotypes"] == [t["label"] for t in BAKED_TERMS]
