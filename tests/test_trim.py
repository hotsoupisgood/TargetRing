"""The 2026-10-18 18:42 PDT trim, and the Claude-coded phenotype flag.

Puck, on WhatsApp: *"can remove druggability caveat. and make it much less
wordy. or remove the word expression altogether as the information is in the
graph. i would like an explanation on the phenotype though, somewhere, like a
0/1 for phenotype that is claude coded."*

Four things are being held here:

(a) the druggability caveat is gone from the rule's output, from the box and
    from the summary prompt, and a bare safety-ring flag is silent too;
(b) the box is the trimmed one: bare values, a three-word region tag, a
    disease-area count with its terms in a tooltip, one rule line -- and no
    expression sentence and no two-TPM text at all;
(c) `phenotype_llm` is a schema-checked 0/1 with a sentence, spent on its own
    account, never a criterion, never called at serve time, and never called
    under TARGETRING_DEMO=1;
(d) the summary prompt is the short one, verdict first and direction second.

The spec these are read against is docs/RECOMMEND-SPEC.md, last section.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from targetring import config
from targetring import phenotype_llm
from targetring import recommend as r
from targetring import spend as spend_mod
from targetring import summary_llm

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "targetring" / "static"
SPEC = ROOT / "docs" / "RECOMMEND-SPEC.md"
ILEUM = "Small_Intestine_Terminal_Ileum"

T = r.Thresholds()

BASE: dict[str, Any] = dict(
    region=ILEUM, region_tpm=19.06, panel_median_tpm=6.99, n_tissues=54,
    region_is_disease_tissue=True, logfc=3.1, padj=2.2e-22,
    rodent_ring=1.0, rodent_state="value", n_abnormal_phenotypes=6,
    mouse_phenotypes=("abnormal intestinal morphology", "enlarged lymph nodes",
                      "abnormal eye morphology"),
    disease_text="'Crohn's disease' vs 'control' Crohn's disease",
)


def crit(rec: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in rec["criteria"] if c["key"] == key)


# ---------------------------------------------------------------------------
# (a) the caveat
# ---------------------------------------------------------------------------


class TestTheDruggabilityCaveatIsGone:
    def test_a_low_tractability_ring_produces_no_line(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "tractable_ring": -1.0}), T)
        assert rec["caveats"] == []
        assert "chemist" not in json.dumps(rec).lower()

    def test_the_word_druggability_is_nowhere_in_the_output(self) -> None:
        for extra in ({}, {"tractable_ring": -1.0}, {"tractable_ring": 0.0},
                      {"safety_event": -1.0}, {"n_severe_liabilities": 3}):
            body = json.dumps(r.recommend(r.Facts(**{**BASE, **extra}), T)).lower()
            assert "druggab" not in body, extra

    def test_only_a_severe_liability_speaks_and_in_one_short_line(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "n_severe_liabilities": 2,
                                     "safety_event": -1.0, "tractable_ring": -1.0}), T)
        assert rec["caveats"] == ["Safety: 2 severe liability records -- not an exclusion."]
        assert len(rec["caveats"][0]) < 70
        assert rec["verdict"] == r.GO, "a caveat still moves no verdict"

    def test_the_page_no_longer_renders_an_expression_or_caveat_sentence(self) -> None:
        js = (STATIC / "app.js").read_text()
        assert "verdict-expression" not in js
        assert "rec.expression" not in js

    def test_the_summary_prompt_forbids_the_whole_family(self) -> None:
        sp = summary_llm.system_prompt()
        assert ("Never write a sentence about druggability, tractability, a "
                "chemical probe, a binding pocket or a safety flag") in sp
        assert "not a <disease> lead" not in sp

    def test_the_spec_records_the_removal(self) -> None:
        body = SPEC.read_text()
        assert "Remove the druggability caveat line" in body
        assert "Trim and the Claude-coded phenotype flag" in body


# ---------------------------------------------------------------------------
# (b) the trimmed box
# ---------------------------------------------------------------------------


class TestTheTrimmedBox:
    def test_each_criterion_carries_a_bare_value_with_no_sentence(self) -> None:
        rec = r.recommend(r.Facts(**BASE), T)
        assert crit(rec, "expressed")["bare"] == "19.06 TPM"
        # The p is printed against the cutoff rather than as an exponent (Puck,
        # 2026-10-19 18:03: "the small ps should just show less than the cut
        # off: <0.05"). `tests/test_pvalue.py` owns the format; this line is
        # here because the bare string is what the box prints.
        assert crit(rec, "dysregulated")["bare"] == "logFC +3.1, p <0.05"
        assert crit(rec, "phenotype")["bare"] == "6 abnormal terms"
        for c in rec["criteria"]:
            # No sentence: short, no prose clause, nothing about the tissue.
            assert len(c["bare"]) <= 24, c
            assert "the run's chosen tissue" not in c["bare"]
            assert not c["bare"].endswith("."), c

    def test_the_long_sentence_is_still_computed_and_still_in_the_json(self) -> None:
        """Less wordy in the BOX is not less in the record: the summary prompt
        and the drawer's long form read `shown`, and the tooltip prints it."""
        rec = r.recommend(r.Facts(**BASE), T)
        assert "the run's chosen tissue" in crit(rec, "expressed")["shown"]
        assert "median across 54 tissues" in rec["expression"]["shown"]

    def test_bare_values_for_the_no_data_and_not_met_shapes(self) -> None:
        none_known = r.recommend(r.Facts(), T)
        assert crit(none_known, "expressed")["bare"] == "no TPM"
        assert crit(none_known, "phenotype")["bare"] == "not phenotyped"
        assert crit(none_known, "dysregulated")["bare"] == "no logFC or adjusted p"
        td = r.recommend(r.Facts(**{**BASE, "region_tpm": None,
                                    "tissue_distribution": 0.42}), T)
        assert crit(td, "expressed")["bare"] == "tissue distribution +0.42"
        flat = r.recommend(r.Facts(**{**BASE, "rodent_ring": 0.0,
                                      "n_abnormal_phenotypes": 0,
                                      "mouse_phenotypes": ()}), T)
        assert crit(flat, "phenotype")["bare"] == "0 abnormal terms"

    def test_one_abnormal_term_is_singular(self) -> None:
        rec = r.recommend(r.Facts(**{**BASE, "n_abnormal_phenotypes": 1}), T)
        assert crit(rec, "phenotype")["bare"] == "1 abnormal term"

    def test_the_region_tag_uses_the_one_display_form(self) -> None:
        """v4.3, review of 2026-10-20 item 5. This tag used to shorten the
        region to one word -- "region: ileum, inferred" -- which made three
        names for one tissue on one page, against the underscored GTEx id on
        the opening view and on the figure. Worse, it RENAMED the organ: GTEx
        has `Small_Intestine_Terminal_Ileum` and `Terminal_Ileum` as separate
        tissues and `REGION_SHORT` mapped both to "ileum".

        One form now (`wording.region_label`), and the trim it was part of is
        kept where it belongs: the long expression sentence is still in the
        tooltip and not in the box.
        """
        tag = r.recommend(r.Facts(**BASE), T)["region_tag"]
        assert tag["shown"] == "region: Small Intestine Terminal Ileum, inferred"
        assert "the run's chosen tissue" in tag["tooltip"]
        # The box still carries a TAG and not the sentence.
        assert "\n" not in tag["shown"] and len(tag["shown"]) < 60
        assert "median across" not in tag["shown"]

    def test_the_tag_says_how_the_region_was_come_by(self) -> None:
        for flag, word in ((True, "inferred"), (False, "form default"), (None, "chosen")):
            tag = r.recommend(r.Facts(**{**BASE, "region": "Lung",
                                         "region_is_disease_tissue": flag}), T)["region_tag"]
            assert tag["shown"] == f"region: Lung, {word}"

    def test_an_unmapped_gtex_name_is_printed_in_full_not_cut(self) -> None:
        """A rule that shortened any underscored name could rename the organ;
        an unknown name is lower-cased and left whole instead."""
        assert r.region_short("Brain_Anterior_Cingulate_Cortex_BA24") == (
            "brain anterior cingulate cortex ba24")
        assert r.region_short(None) == "none"

    def test_the_disease_area_aid_is_a_count_with_the_terms_in_a_tooltip(self) -> None:
        area = r.recommend(r.Facts(**BASE), T)["disease_area"]
        assert area["short"] == "disease-area terms: 2"
        assert "abnormal intestinal morphology [digestive/alimentary system]" in area["tooltip"]
        assert "enlarged lymph nodes [immune system]" in area["tooltip"]
        assert "display aid, not a criterion" in area["tooltip"].lower()
        assert "abnormal eye morphology" not in area["tooltip"]

    def test_the_count_is_zero_not_a_sentence_when_nothing_matches(self) -> None:
        area = r.recommend(r.Facts(**{**BASE, "n_abnormal_phenotypes": 1,
                                      "mouse_phenotypes": ("abnormal eye morphology",)}),
                           T)["disease_area"]
        assert area["short"] == "disease-area terms: 0"
        assert "none of the 1 abnormal term(s)" in area["tooltip"]

    def test_an_unmapped_contrast_says_no_mapping_in_three_words(self) -> None:
        area = r.recommend(r.Facts(**{**BASE, "disease_text": "psoriasis vs control"}),
                           T)["disease_area"]
        assert area["short"] == "disease-area terms: no mapping"

    def test_the_rule_is_one_line_with_the_thresholds_in_the_tooltip(self) -> None:
        rec = r.recommend(r.Facts(**BASE), T)
        assert rec["rule_short"] == ("rule: expressed AND dysregulated AND phenotype "
                                     "— not used to order or filter the figure")
        assert "\n" not in rec["rule_short"] and len(rec["rule_short"]) < 100
        # v4.3: the bound is printed with the glyph a reader reads. The RULE
        # VERSION is hashed over the ASCII form (`recommend._canonical`), so a
        # typography change cannot say the rule changed.
        assert "GTEx region TPM ≥ 1" in rec["rule_tooltip"]
        assert ">=" not in rec["rule_tooltip"]
        assert r.rule_version(T) in rec["rule_tooltip"]

    def test_the_modality_line_survives_the_trim(self) -> None:
        down = r.recommend(r.Facts(**{**BASE, "logfc": -3.1}), T)
        assert down["modality"] == ("down-regulated in the study: a biologic may "
                                    "be preferred")
        assert r.recommend(r.Facts(**BASE), T)["modality"] is None

    def test_the_trim_changes_no_verdict_and_no_rule_version(self) -> None:
        for extra in ({}, {"tractable_ring": -1.0}, {"n_severe_liabilities": 2},
                      {"logfc": -3.1}, {"region_is_disease_tissue": False}):
            rec = r.recommend(r.Facts(**{**BASE, **extra}), T)
            assert rec["verdict"] == r.GO, extra
            assert rec["rule_version"] == r.rule_version(T), extra

    def test_the_page_prints_bare_the_tag_and_the_one_line_rule(self) -> None:
        js = (STATIC / "app.js").read_text()
        assert "c.bare" in js
        assert "rec.region_tag" in js
        assert "rec.rule_short" in js and "rec.rule_tooltip" in js

    def test_no_new_stylesheet_rule_goes_under_the_twelve_pixel_floor(self) -> None:
        """The presentation review's B12 floor still applies to the trimmed
        box's own rules (tests/test_presentation.py holds it for the file)."""
        import re
        css = re.sub(r"/\*.*?\*/", "", (STATIC / "app.css").read_text(), flags=re.S)
        block = "\n".join(line for line in css.splitlines()
                          if ".crit-flag" in line or ".crit-aid" in line
                          or ".verdict-region" in line or ".verdict-rule" in line)
        sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)px", block)]
        assert sizes and all(s >= 12.0 for s in sizes), sizes


# ---------------------------------------------------------------------------
# (c) the Claude-coded phenotype flag
# ---------------------------------------------------------------------------

FLAG_DRAWER: dict[str, Any] = {
    "gene": "CXCL1",
    "contrast_text": "'Crohn's disease' vs 'control' Crohn's disease",
    "mouse_phenotypes": ["abnormal intestinal morphology", "enlarged lymph nodes"],
    "cells": {"rodent": {"state": "value", "value": 1.0,
                         "detail": "abnormal phenotype on record"}},
    # Deliberately present, and deliberately NOT given to the model:
    "logfc": 3.1, "padj": 2.2e-22,
    "recommendation": {"verdict": "GO"},
    "expression": {"region": ILEUM, "region_tpm": 19.06},
}


class TestThePhenotypeFlagPrompt:
    def test_the_facts_are_only_terms_ring_and_area(self) -> None:
        f = phenotype_llm.build_facts(FLAG_DRAWER)
        assert set(f) == {"gene", "disease_area", "contrast_text",
                          "mouse_phenotype_terms", "n_terms", "knockout_ring",
                          "question"}
        assert f["disease_area"] == "inflammatory bowel disease"
        assert f["mouse_phenotype_terms"] == ["abnormal intestinal morphology",
                                              "enlarged lymph nodes"]
        assert f["n_terms"] == 2
        assert f["knockout_ring"] == {"state": "value", "value": 1.0,
                                      "detail": "abnormal phenotype on record"}

    def test_the_verdict_and_the_study_numbers_are_withheld(self) -> None:
        """A model given the verdict agrees with the verdict; this is asked to
        read the terms."""
        body = phenotype_llm.user_content(phenotype_llm.build_facts(FLAG_DRAWER))
        for absent in ("GO", "logfc", "3.1", "2.2e-22", "19.06", "recommendation"):
            assert absent not in body, absent

    def test_a_hatched_ring_is_none_not_zero(self) -> None:
        d = {**FLAG_DRAWER, "cells": {"rodent": {"state": "no_data", "value": None}}}
        assert phenotype_llm.build_facts(d)["knockout_ring"]["value"] is None

    def test_an_unmapped_contrast_gives_a_null_area(self) -> None:
        d = {**FLAG_DRAWER, "contrast_text": "psoriasis vs control"}
        assert phenotype_llm.build_facts(d)["disease_area"] is None

    def test_the_prompt_carries_the_owners_question_verbatim(self) -> None:
        sp = phenotype_llm.system_prompt()
        assert phenotype_llm.QUESTION in sp
        assert "Is there a model-organism phenotype that a biologist would read as " \
               "evidence this gene matters for this disease area?" in sp

    def test_the_prompt_grounds_the_answer_and_requires_a_named_term(self) -> None:
        sp = phenotype_llm.system_prompt()
        assert "Your ONLY source of information is that JSON" in sp
        assert "Do not name a term that is not in the list" in sp
        assert "Never write a sentence that names no term unless the list is empty" in sp
        assert "a lead, not a verdict" in sp
        assert "one sentence" in sp

    def test_the_prompt_is_in_the_spec_and_the_question_matches_it(self) -> None:
        body = SPEC.read_text()
        assert "phenotype_llm" in body
        assert "Answer 1 or 0 and one plain sentence naming the term(s)" in body

    def test_the_output_schema_pins_a_zero_or_one_and_one_sentence(self) -> None:
        s = phenotype_llm.OUTPUT_SCHEMA
        assert s["properties"]["flag"] == {"type": "integer", "enum": [0, 1]}
        assert s["properties"]["reason"]["type"] == "string"
        assert s["required"] == ["flag", "reason"]
        assert s["additionalProperties"] is False

    def test_the_prompt_hash_moves_with_the_prompt_and_with_the_schema(self) -> None:
        before = phenotype_llm.prompt_hash()
        assert len(before) == 16
        saved = phenotype_llm.SYSTEM_PROMPT
        try:
            phenotype_llm.SYSTEM_PROMPT = saved + "\nand one more rule.\n"
            assert phenotype_llm.prompt_hash() != before
        finally:
            phenotype_llm.SYSTEM_PROMPT = saved
        assert phenotype_llm.prompt_hash() == before


class TestThePhenotypeFlagOutcome:
    """The response is schema-constrained and STILL validated: a flag is one
    character and a wrong one looks exactly like a right one on the page."""

    class _Usage:
        input_tokens = 600
        output_tokens = 40
        cache_read_input_tokens = 0

    class _Block:
        type = "text"

        def __init__(self, text: str) -> None:
            self.text = text

    class _Msg:
        def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
            self.content = [TestThePhenotypeFlagOutcome._Block(text)]
            self.stop_reason = stop_reason
            self.usage = TestThePhenotypeFlagOutcome._Usage()

    def _out(self, text: str, stop_reason: str = "end_turn"):
        return phenotype_llm._outcome_from_message(
            self._Msg(text, stop_reason), "claude-opus-5")

    def test_a_good_answer_is_parsed(self) -> None:
        out = self._out('{"flag": 1, "reason": "Abnormal intestinal morphology is on record."}')
        assert (out.ok, out.flag) == (True, 1)
        assert out.reason == "Abnormal intestinal morphology is on record."

    def test_a_flag_that_is_not_zero_or_one_is_refused(self) -> None:
        for body in ('{"flag": 2, "reason": "x"}', '{"flag": "1", "reason": "x"}',
                     '{"flag": true, "reason": "x"}', '{"reason": "x"}'):
            out = self._out(body)
            assert not out.ok and "not 0 or 1" in (out.error or ""), body

    def test_a_missing_sentence_is_refused(self) -> None:
        out = self._out('{"flag": 0, "reason": "  "}')
        assert not out.ok and "no reason sentence" in out.error

    def test_prose_instead_of_json_is_refused_not_guessed_at(self) -> None:
        out = self._out("The flag is 1 because of the intestinal term.")
        assert not out.ok and "not JSON" in out.error

    def test_an_overlong_sentence_is_refused(self) -> None:
        out = self._out(json.dumps({"flag": 1, "reason": "x" * 400}))
        assert not out.ok and "over the" in out.error

    def test_a_refusal_and_a_cutoff_are_both_failures(self) -> None:
        assert self._out('{"flag": 1, "reason": "x"}', "refusal").refused_by_provider
        assert not self._out('{"flag": 1, "reason": "x"}', "max_tokens").ok


class TestThePhenotypeFlagBake:
    """The bake, against a patched client. No network, no key, no spend."""

    #: Two runs of one contrast, as the four demo runs are: the first has both
    #: genes, the second repeats CXCL1 term-for-term. That is what the dedupe
    #: is for.
    GENES = {"20990101T000000Z-flag01": ["CXCL1", "MUC1"],
             "20990101T000000Z-flag02": ["CXCL1"]}

    @pytest.fixture()
    def run(self, monkeypatch, tmp_path):
        from targetring import table as table_mod
        dirs: dict[str, Path] = {}
        for run_id, genes in self.GENES.items():
            d = tmp_path / run_id / table_mod.DRAWER_DIR
            d.mkdir(parents=True)
            for gene in genes:
                (d / f"{gene}.json").write_text(
                    json.dumps({**FLAG_DRAWER, "gene": gene}))
            dirs[run_id] = d
            os.utime(tmp_path / run_id, ns=(1_600_000_000_000_000_000,
                                            1_600_000_000_000_000_000))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)

        class _T:
            def __init__(self, run_id: str) -> None:
                self.genes = TestThePhenotypeFlagBake.GENES[run_id]
                self.manifest: dict[str, Any] = {"region": ILEUM}

        monkeypatch.setattr(table_mod, "load", _T)
        monkeypatch.setattr(config, "DEMO_MODE", False)
        first = next(iter(self.GENES))
        return first, dirs[first], tmp_path / first

    @pytest.fixture()
    def ledger(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SPEND_LEDGER_PATH",
                            tmp_path / "spend-ledger.jsonl")
        return spend_mod.SpendLedger(tmp_path / "spend-ledger.jsonl")

    def test_it_writes_the_flag_block_and_keeps_everything_else(self, run, ledger) -> None:
        run_id, d, _ = run
        client = phenotype_llm.FakePhenotypeFlagClient(
            flag=1, reason="Abnormal intestinal morphology is on record.")
        rep = phenotype_llm.bake_phenotype_flags(
            [run_id], client=client, ledger=ledger, log=lambda _s: None)
        assert sorted(rep.runs[run_id]["written"]) == ["CXCL1", "MUC1"]
        back = json.loads((d / "CXCL1.json").read_text())
        pf = back["phenotype_llm"]
        assert (pf["flag"], pf["model"]) == (1, config.PHENOTYPE_FLAG_LLM_MODEL)
        assert pf["prompt_version"] == phenotype_llm.prompt_hash()
        assert pf["inputs"]["mouse_phenotype_terms"] == FLAG_DRAWER["mouse_phenotypes"]
        assert pf["is_criterion"] is False
        assert back["logfc"] == 3.1 and back["recommendation"] == {"verdict": "GO"}

    def test_identical_facts_are_one_call_across_runs(self, run, ledger) -> None:
        """Three drawers over two runs, two distinct genes: two calls, not
        three. The second run's CXCL1 is the same facts as the first's."""
        client = phenotype_llm.FakePhenotypeFlagClient()
        rep = phenotype_llm.bake_phenotype_flags(
            list(self.GENES), client=client, ledger=ledger, log=lambda _s: None)
        assert len(client.calls) == 1, "one batch, submitted once"
        assert len(client.calls[0]) == 2, [k for k, _ in client.calls[0]]
        assert rep.calls == 2
        assert rep.flags == {1: 3}, "three drawers written from two calls"
        for run_id, genes in self.GENES.items():
            assert sorted(rep.runs[run_id]["written"]) == sorted(genes)

    def test_a_second_bake_keeps_what_is_there_and_spends_nothing(self, run, ledger) -> None:
        run_id, _, _ = run
        c1 = phenotype_llm.FakePhenotypeFlagClient()
        phenotype_llm.bake_phenotype_flags([run_id], client=c1, ledger=ledger,
                                           log=lambda _s: None)
        spent = ledger.spent(spend_mod.PHENOTYPE_LLM)
        c2 = phenotype_llm.FakePhenotypeFlagClient()
        rep = phenotype_llm.bake_phenotype_flags([run_id], client=c2, ledger=ledger,
                                                log=lambda _s: None)
        assert c2.calls == [] and rep.calls == 0
        assert sorted(rep.runs[run_id]["kept"]) == ["CXCL1", "MUC1"]
        assert ledger.spent(spend_mod.PHENOTYPE_LLM) == spent

    def test_a_flag_already_baked_elsewhere_is_copied_not_re_bought(
            self, run, ledger) -> None:
        """A gene baked in one run and seen again in another, in a later bake
        of both, is copied off disk rather than asked for twice."""
        first, second = list(self.GENES)
        c1 = phenotype_llm.FakePhenotypeFlagClient()
        phenotype_llm.bake_phenotype_flags([first], client=c1, ledger=ledger,
                                           log=lambda _s: None)
        spent = ledger.spent(spend_mod.PHENOTYPE_LLM)
        c2 = phenotype_llm.FakePhenotypeFlagClient()
        rep = phenotype_llm.bake_phenotype_flags([first, second], client=c2,
                                                 ledger=ledger, log=lambda _s: None)
        assert c2.calls == [] and rep.calls == 0
        assert rep.runs[second]["written"] == ["CXCL1"]
        assert sorted(rep.runs[first]["kept"]) == ["CXCL1", "MUC1"]
        assert ledger.spent(spend_mod.PHENOTYPE_LLM) == spent

    def test_a_failed_call_writes_nothing_for_that_gene(self, run, ledger) -> None:
        run_id, d, _ = run
        client = phenotype_llm.FakePhenotypeFlagClient(fail=True)
        rep = phenotype_llm.bake_phenotype_flags([run_id], client=client, ledger=ledger,
                                                 log=lambda _s: None)
        assert rep.runs[run_id]["written"] == []
        assert len(rep.runs[run_id]["skipped"]) == 2
        assert "phenotype_llm" not in json.loads((d / "CXCL1.json").read_text())

    def test_it_spends_the_phenotype_llm_account_and_no_other(self, run, ledger) -> None:
        run_id, _, _ = run
        phenotype_llm.bake_phenotype_flags(
            [run_id], client=phenotype_llm.FakePhenotypeFlagClient(),
            ledger=ledger, log=lambda _s: None)
        assert ledger.spent(spend_mod.PHENOTYPE_LLM) > 0
        for other in (spend_mod.TRIAGE, spend_mod.PHENOTYPE, spend_mod.CHOOSER,
                      spend_mod.REGION, spend_mod.SUMMARY):
            assert ledger.spent(other) == 0.0, other

    def test_the_cap_stops_it_and_records_the_refusal(self, run, ledger, monkeypatch) -> None:
        run_id, d, _ = run
        monkeypatch.setattr(config, "PHENOTYPE_FLAG_SPEND_CAP_USD", 0.0)
        rep = phenotype_llm.bake_phenotype_flags(
            [run_id], client=phenotype_llm.FakePhenotypeFlagClient(),
            ledger=ledger, log=lambda _s: None)
        assert rep.stopped and "phenotype_llm" in rep.stopped
        assert rep.calls == 0
        assert "phenotype_llm" not in json.loads((d / "CXCL1.json").read_text())
        assert any(l["status"] == spend_mod.STATUS_REFUSED_CAP
                   for l in ledger.records_for(spend_mod.PHENOTYPE_LLM))

    def test_the_run_directory_mtime_is_put_back(self, run, ledger) -> None:
        run_id, _, run_dir = run
        phenotype_llm.bake_phenotype_flags(
            [run_id], client=phenotype_llm.FakePhenotypeFlagClient(),
            ledger=ledger, log=lambda _s: None)
        st = run_dir.stat()
        assert (st.st_atime_ns, st.st_mtime_ns) == (1_600_000_000_000_000_000,
                                                    1_600_000_000_000_000_000)

    def test_demo_mode_refuses_before_any_call(self, run, ledger, monkeypatch) -> None:
        """Baked only: the flag is a file on a demo box, never a call from it
        (AGENTS.md rule 10)."""
        run_id, _, _ = run
        monkeypatch.setattr(config, "DEMO_MODE", True)
        client = phenotype_llm.FakePhenotypeFlagClient()
        with pytest.raises(RuntimeError, match="TARGETRING_DEMO"):
            phenotype_llm.bake_phenotype_flags([run_id], client=client, ledger=ledger,
                                               log=lambda _s: None)
        assert client.calls == []
        assert ledger.spent(spend_mod.PHENOTYPE_LLM) == 0.0


class TestTheFlagIsNotTheCriterion:
    def test_no_app_route_or_module_calls_the_flag_at_serve_time(self) -> None:
        """Only the bake script imports `phenotype_llm`; the app serves the
        baked file through the drawer route it already had."""
        import re
        imports = re.compile(r"^\s*(?:from\s+\.?\s*import\s+phenotype_llm"
                             r"|import\s+phenotype_llm"
                             r"|from\s+\.phenotype_llm\s+import"
                             r"|from\s+targetring\s+import\s+phenotype_llm)",
                             re.M)
        pkg = ROOT / "targetring"
        importers = sorted(p.name for p in pkg.glob("*.py")
                           if p.name != "phenotype_llm.py"
                           and imports.search(p.read_text()))
        assert importers == [], importers
        scripts = sorted(p.name for p in (ROOT / "scripts").glob("*.py")
                         if imports.search(p.read_text()))
        assert scripts == ["bake_phenotype_flags.py"]

    def test_recommend_knows_nothing_about_the_flag(self) -> None:
        body = (ROOT / "targetring" / "recommend.py").read_text()
        assert "phenotype_llm" not in body
        rec = r.recommend(r.Facts(**BASE), T)
        assert "phenotype_llm" not in json.dumps(rec)

    def test_the_page_prints_it_beside_the_criterion_and_labels_it(self) -> None:
        js = (STATIC / "app.js").read_text()
        assert "phenotype (Claude)" in js
        assert "d.phenotype_llm" in js
        # It is passed in separately from `rec`, so a drawer with a flag and no
        # verdict shows no box and a verdict with no flag shows no flag row.
        # `terms` (2026-10-18: which model each phenotype term came from) is
        # display data printed beside the sentence and never an input to it --
        # tests/test_phenotype_models.py.
        assert "function phenotypeFlagHtml(pf, terms)" in js
        assert "pf.flag !== 0 && pf.flag !== 1" in js

    def test_the_baked_block_says_on_its_face_that_it_is_not_the_criterion(self) -> None:
        assert "never the criterion" in phenotype_llm.NOTE
        assert "never changes the GO / NO-GO verdict" in phenotype_llm.NOTE


# ---------------------------------------------------------------------------
# (d) the shorter summary prompt
# ---------------------------------------------------------------------------


class TestTheShorterSummaryPrompt:
    def test_it_asks_for_four_to_six_sentences(self) -> None:
        sp = summary_llm.system_prompt()
        assert "Write 4 to 6 sentences" in sp
        assert "5 to 9 sentences" not in sp

    def test_verdict_first_then_direction(self) -> None:
        sp = summary_llm.system_prompt()
        verdict = sp.index('Start with the verdict word exactly as given')
        hypothesis = sp.index('starting with the word "Hypothesis:"')
        families = sp.index("the evidence families that are present")
        absent = sp.index("as ABSENT evidence")
        assert verdict < hypothesis < families < absent

    def test_the_closing_what_would_change_it_sentence_is_gone(self) -> None:
        sp = summary_llm.system_prompt()
        assert "what single fact, if it were different" not in sp
        assert "Do not add a closing sentence about what would change the picture." in sp

    def test_the_rules_that_still_hold_still_hold(self) -> None:
        sp = summary_llm.system_prompt()
        assert "do not compute, soften, dispute or re-derive it" in sp
        assert "The change may reflect infiltrating immune cells rather than regulation in the tissue" in sp
        assert "never write a fold" in sp
        assert "A missing ring is not a low value" in sp

    def test_the_prompt_hash_moved_so_the_bake_re_buys(self) -> None:
        """A prompt change re-buys every summary; the hash has to move or the
        bake would keep the old text at the new prompt."""
        assert summary_llm.prompt_hash() != "0000000000000000"
        assert "Write 4 to 6 sentences" in summary_llm.SYSTEM_PROMPT_HEAD
