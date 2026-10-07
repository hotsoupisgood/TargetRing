"""Stage 7b — I11, the mechanical validator for the phenotype-page summary,
and the fake client every other phenotype test is built on.

What these tests defend: a claim that cites a row nobody sent, or quotes text
that is not actually in any row it cites, must refuse the WHOLE summary — not
just that one claim, and not with a softer outcome than the rows that cite
correctly. Zero claims is also a refusal, not an empty-but-valid summary.
"""

from __future__ import annotations

from targetring import invariants as invariants_mod
from targetring import llm as llm_mod

ROWS = [
    {"row_id": 1, "mp_label": "abnormal heart morphology", "model": "MGI:1",
     "pmid": "111", "title": "BRAF knockouts show cardiac defects",
     "sentence": "Braf mutant embryos show abnormal heart looping.",
     "level": "asserted"},
    {"row_id": 2, "mp_label": "no abnormal phenotype detected", "model": "MGI:2",
     "pmid": "222", "title": "A control study", "sentence": "gene not named in abstract",
     "level": "asserted-negative"},
]


#: The shape `phenotype.summary_rows` actually sends, since the (phenotype,
#: model, paper) cartesian product was replaced by one row per (phenotype,
#: model) carrying the MODEL's literature nested under it.
NESTED_ROWS = [
    {"row_id": 1, "mp_label": "abnormal heart morphology", "model_id": "MGI:1",
     "level": "asserted",
     "papers": [
         {"pmid": "111", "title": "BRAF knockouts show cardiac defects",
          "sentence": "Braf mutant embryos show abnormal heart looping."},
         {"pmid": "222", "title": "A follow-up study", "sentence": None},
     ]},
    {"row_id": 2, "mp_label": "no abnormal phenotype detected",
     "model_id": "MGI:2", "level": "asserted-negative", "papers": []},
]


class TestI11ReadsNestedPapers:
    """A quote may come from a row's own `mp_label` or from any of its PAPERS'
    title/sentence. Before the join was fixed every quotable string sat flat on
    the row, because each row carried exactly one paper -- which was itself the
    defect. I11 has to follow the text to where it now lives, or it starts
    refusing honest claims."""

    def test_a_quote_from_a_nested_paper_passes(self) -> None:
        claims = [{"text": "Cardiac defects are among the curated phenotypes.",
                   "row_ids": [1], "quote": "abnormal heart looping"}]
        assert invariants_mod.check_phenotype_claims_citable(
            claims, NESTED_ROWS).outcome == "PASS"

    def test_a_quote_from_the_second_paper_on_the_same_row_passes(self) -> None:
        claims = [{"text": "x", "row_ids": [1], "quote": "A follow-up study"}]
        assert invariants_mod.check_phenotype_claims_citable(
            claims, NESTED_ROWS).outcome == "PASS"

    def test_a_quote_from_the_rows_own_mp_label_still_passes(self) -> None:
        claims = [{"text": "x", "row_ids": [2],
                   "quote": "no abnormal phenotype detected"}]
        assert invariants_mod.check_phenotype_claims_citable(
            claims, NESTED_ROWS).outcome == "PASS"

    def test_a_quote_from_another_rows_paper_is_still_refused(self) -> None:
        """Nesting widens what one row can be quoted from. It must not widen
        what a claim can cite -- row 2 has no papers at all."""
        claims = [{"text": "x", "row_ids": [2], "quote": "abnormal heart looping"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, NESTED_ROWS)
        assert r.outcome == "FAIL"
        assert "papers" in r.detail

    def test_a_fabricated_quote_is_still_refused(self) -> None:
        claims = [{"text": "x", "row_ids": [1],
                   "quote": "Braf causes heart disease in humans"}]
        assert invariants_mod.check_phenotype_claims_citable(
            claims, NESTED_ROWS).outcome == "FAIL"

    def test_the_flat_row_shape_is_still_accepted(self) -> None:
        """`_citable_text` reads both shapes deliberately: quietly ignoring the
        flat fields would refuse honest claims from any caller still passing
        them."""
        claims = [{"text": "x", "row_ids": [1], "quote": "abnormal heart looping"}]
        assert invariants_mod.check_phenotype_claims_citable(
            claims, ROWS).outcome == "PASS"


class TestI11Citability:
    def test_claims_citing_real_rows_with_verbatim_quotes_pass(self) -> None:
        claims = [{"text": "Braf mutants show cardiac defects.", "row_ids": [1],
                   "quote": "abnormal heart looping"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "PASS"

    def test_zero_claims_is_refused(self) -> None:
        r = invariants_mod.check_phenotype_claims_citable([], ROWS)
        assert r.outcome == "FAIL"
        assert "zero claims" in r.detail

    def test_a_claim_citing_a_nonexistent_row_id_refuses_the_whole_summary(self) -> None:
        claims = [
            {"text": "fine", "row_ids": [1], "quote": "abnormal heart looping"},
            {"text": "fabricated", "row_ids": [99], "quote": "anything"},
        ]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "FAIL"
        assert "99" in r.detail

    def test_a_claim_whose_quote_is_not_in_its_cited_rows_refuses_it(self) -> None:
        claims = [{"text": "wrong quote", "row_ids": [1],
                   "quote": "this text appears nowhere in row 1"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "FAIL"
        assert "not a substring" in r.detail

    def test_one_bad_claim_refuses_claims_that_would_individually_pass(self) -> None:
        good = {"text": "fine", "row_ids": [1], "quote": "abnormal heart looping"}
        bad = {"text": "bad", "row_ids": [1], "quote": "nowhere in the row"}
        r = invariants_mod.check_phenotype_claims_citable([good, bad], ROWS)
        assert r.outcome == "FAIL", "one failing claim must refuse the entire summary"

    def test_quote_may_come_from_mp_label(self) -> None:
        claims = [{"text": "a negative finding", "row_ids": [2],
                   "quote": "no abnormal phenotype detected"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "PASS"

    def test_a_claim_citing_multiple_rows_passes_if_quote_matches_any_one(self) -> None:
        claims = [{"text": "spans two rows", "row_ids": [1, 2],
                   "quote": "abnormal heart looping"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "PASS"

    def test_a_claim_with_no_row_ids_is_refused(self) -> None:
        r = invariants_mod.check_phenotype_claims_citable(
            [{"text": "unsupported", "row_ids": [], "quote": "abnormal heart looping"}], ROWS)
        assert r.outcome == "FAIL"

    def test_quote_matching_is_case_sensitive(self) -> None:
        claims = [{"text": "wrong case", "row_ids": [1],
                   "quote": "ABNORMAL HEART LOOPING"}]
        r = invariants_mod.check_phenotype_claims_citable(claims, ROWS)
        assert r.outcome == "FAIL"


class TestPromptHash:
    def test_stable_across_calls(self) -> None:
        assert llm_mod.prompt_hash() == llm_mod.prompt_hash()

    def test_prompt_forbids_mechanism_and_recommendation_language(self) -> None:
        # Rule 6: the system prompt is the one place this build can enforce
        # "no mechanism, no direction-of-effect language, no recommendation"
        # before the model ever answers -- I11 cannot catch prose that cites
        # correctly and still oversteps, so the instruction has to carry this.
        p = llm_mod.SYSTEM_PROMPT.lower()
        assert "mechanism" in p
        assert "recommendation" in p
        assert "suggests" in p

    def test_prompt_states_its_only_source_is_the_table(self) -> None:
        assert "only source of information is this table" in llm_mod.SYSTEM_PROMPT.lower()


class TestFakeLLMClient:
    def test_queue_claims_then_complete_returns_them(self) -> None:
        c = llm_mod.FakeLLMClient()
        c.queue_claims([{"text": "x", "row_ids": [1], "quote": "y"}])
        out = c.complete(ROWS, max_tokens=1200)
        assert out.ok
        assert out.claims == [{"text": "x", "row_ids": [1], "quote": "y"}]
        assert c.calls == [ROWS]

    def test_queue_refusal(self) -> None:
        c = llm_mod.FakeLLMClient()
        c.queue_refusal()
        out = c.complete(ROWS, max_tokens=1200)
        assert not out.ok
        assert out.refused_by_provider

    def test_calling_with_nothing_queued_raises_rather_than_returning_a_default(self) -> None:
        c = llm_mod.FakeLLMClient()
        try:
            c.complete(ROWS, max_tokens=1200)
        except AssertionError:
            pass
        else:
            raise AssertionError("expected AssertionError for an un-queued call")
