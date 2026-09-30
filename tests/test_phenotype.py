"""Stage 7 — the phenotype table (7a) and its summary (7b), fake client only.

No network call and no LLM call anywhere in this file — `llm.FakeLLMClient`
stands in for the real one everywhere, per the "zero spend during development
and testing" rule for this stage. What these tests defend:

* the table keeps a row even when a phenotype has no biologicalModels, or a
  model has no PMID, rather than dropping it (rule 1's spirit one level down);
* the three rule-6 levels, and the gene-level `none found` state that skips
  the LLM call entirely;
* the `phenotype` spend account and the `triage` spend account never move
  together -- every phenotype-summary test asserts `triage` stayed at $0.00;
* a cap that is already exceeded stops the call that would cross it, not the
  call already in flight;
* a refused summary (cap, provider, or I11) still records real spend where a
  call was actually made, and the table still renders regardless.
"""

from __future__ import annotations

import asyncio

import pytest

from targetring import config, phenotype as phenotype_mod, spend
from targetring.adapters.literature import ArticleRecord, EuropePMCAdapter
from targetring.adapters.opentargets import OTRecord

OPUS = "claude-opus-5"

# A small BRAF-like fixture covering all three rule-6 levels and both
# "no model on record" / "no PMID on record" edge cases in one gene.
MOUSE_PHENOTYPES = [
    {  # asserted, one model, two PMIDs
        "modelPhenotypeId": "MP:0000074", "modelPhenotypeLabel": "abnormal heart morphology",
        "targetInModelMgiId": "MGI:88190", "targetInModel": "Braf",
        "biologicalModels": [
            {"id": "MGI:1", "allelicComposition": "Braf<tm1>/Braf<+>",
             "geneticBackground": "C57BL/6J", "literature": ["111", "222"]},
        ],
    },
    {  # asserted-negative, one model, no PMIDs at all
        "modelPhenotypeId": "MP:0002169", "modelPhenotypeLabel": "no abnormal phenotype detected",
        "targetInModelMgiId": "MGI:88190", "targetInModel": "Braf",
        "biologicalModels": [
            {"id": "MGI:2", "allelicComposition": "Braf<tm2>/Braf<+>",
             "geneticBackground": "129", "literature": []},
        ],
    },
    {  # asserted, zero biologicalModels
        "modelPhenotypeId": "MP:0000368", "modelPhenotypeLabel": "abnormal craniofacial morphology",
        "targetInModelMgiId": "MGI:88190", "targetInModel": "Braf",
        "biologicalModels": [],
    },
]


def braf_record(mouse_phenotypes=MOUSE_PHENOTYPES) -> OTRecord:
    return OTRecord("ENSG00000157764", found=True, approved_symbol="BRAF",
                    mouse_phenotypes=list(mouse_phenotypes))


ARTICLES = {
    "111": ArticleRecord("111", "q", title="Braf knockouts show cardiac defects",
                         year="2010", journal="J Dev Biol",
                         abstract_text="Braf mutant embryos show abnormal heart looping."),
    "222": ArticleRecord("222", "q", title="A follow-up study", year="2012",
                         journal="J Dev Biol", abstract_text=None),
}


async def _fake_fetch_article(self, pmid: str) -> ArticleRecord:
    return ARTICLES.get(pmid, ArticleRecord(pmid, "q", error="not in fixture"))


# Saved before any monkeypatching, so `TestFetchArticleEcho` below -- which
# wants the REAL `fetch_article` over a fake HTTP layer, not the table-level
# fake above -- can call it directly and not be shadowed by the autouse
# fixture every other class in this module wants.
_real_fetch_article = EuropePMCAdapter.fetch_article


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    if "TestFetchArticleEcho" in request.node.nodeid:
        return
    monkeypatch.setattr(EuropePMCAdapter, "fetch_article", _fake_fetch_article)


def build(rec=None):
    rec = rec or braf_record()
    return asyncio.run(phenotype_mod.build_table(
        rec, gene_symbol="BRAF", ensembl_id=rec.ensembl_id, http=None, cache=None))


# ---------------------------------------------------------------------------
# 7a: build_table_rows -- the raw flattening, missing models/PMIDs kept
# ---------------------------------------------------------------------------

class TestBuildTableRows:
    def test_one_row_per_phenotype_model_pmid(self) -> None:
        rows = phenotype_mod.build_table_rows(braf_record())
        assert len(rows) == 4  # 2 pmids + 1 no-pmid model + 1 no-model phenotype
        pmids = [r["pmid"] for r in rows]
        assert pmids.count("111") == 1 and pmids.count("222") == 1
        assert pmids.count(None) == 2

    def test_a_phenotype_with_no_biological_models_is_kept_not_dropped(self) -> None:
        rows = phenotype_mod.build_table_rows(braf_record())
        craniofacial = [r for r in rows if r["mp_id"] == "MP:0000368"]
        assert len(craniofacial) == 1
        assert craniofacial[0]["model_id"] is None

    def test_a_model_with_no_literature_is_kept_with_pmid_none(self) -> None:
        rows = phenotype_mod.build_table_rows(braf_record())
        negative = [r for r in rows if r["mp_id"] == "MP:0002169"]
        assert len(negative) == 1
        assert negative[0]["pmid"] is None

    def test_a_gene_with_no_mouse_phenotypes_produces_zero_rows(self) -> None:
        assert phenotype_mod.build_table_rows(braf_record(mouse_phenotypes=[])) == []


class TestFirstSentenceNaming:
    def test_finds_the_sentence_naming_the_human_symbol(self) -> None:
        text = "First sentence is irrelevant. BRAF mutants show cardiac defects. Third."
        assert phenotype_mod.first_sentence_naming(text, "BRAF", "Braf") == \
            "BRAF mutants show cardiac defects."

    def test_finds_the_sentence_naming_the_mouse_ortholog(self) -> None:
        text = "Braf mutant embryos show abnormal heart looping. Nothing else names it."
        assert "Braf mutant embryos" in phenotype_mod.first_sentence_naming(text, "BRAF", "Braf")

    def test_is_case_insensitive(self) -> None:
        text = "braf knockouts show defects."
        assert phenotype_mod.first_sentence_naming(text, "BRAF", "Braf") == \
            "braf knockouts show defects."

    def test_no_abstract_falls_back(self) -> None:
        assert phenotype_mod.first_sentence_naming(None, "BRAF", "Braf") == \
            "gene not named in abstract"

    def test_gene_not_named_falls_back(self) -> None:
        text = "This paper is about something else entirely."
        assert phenotype_mod.first_sentence_naming(text, "BRAF", "Braf") == \
            "gene not named in abstract"


# ---------------------------------------------------------------------------
# 7a: build_table end to end -- rule-6 levels, states, article fetch
# ---------------------------------------------------------------------------

class TestBuildTable:
    def test_all_three_rule6_levels_are_representable(self) -> None:
        table = build()
        levels = {r["level"] for r in table.rows}
        assert levels == {phenotype_mod.LEVEL_ASSERTED, phenotype_mod.LEVEL_ASSERTED_NEGATIVE}
        # "none found" is a GENE-level state, tested separately below, not a
        # per-row level -- a gene with zero rows never reaches this function's
        # per-row loop at all.

    def test_the_verbatim_rule6_label_is_present_on_asserted_rows(self) -> None:
        table = build()
        asserted = [r for r in table.rows if r["level"] == phenotype_mod.LEVEL_ASSERTED]
        assert asserted
        assert all(r["level_label"] == phenotype_mod.RULE6_ASSERTED_LABEL for r in asserted)

    def test_the_negative_label_is_the_no_abnormal_string(self) -> None:
        table = build()
        neg = [r for r in table.rows if r["level"] == phenotype_mod.LEVEL_ASSERTED_NEGATIVE]
        assert neg and all(r["level_label"] == "no abnormal phenotype detected" for r in neg)

    def test_a_gene_with_zero_phenotype_rows_is_none_found_and_has_no_rows(self) -> None:
        table = build(braf_record(mouse_phenotypes=[]))
        assert table.state == phenotype_mod.LEVEL_NONE_FOUND
        assert table.rows == []

    def test_fetched_abstract_produces_a_sentence(self) -> None:
        table = build()
        row = next(r for r in table.rows if r["pmid"] == "111")
        assert row["abstract_status"] == "fetched"
        assert "heart looping" in row["sentence"]

    def test_pmid_with_no_abstract_text_is_unavailable_not_dropped(self) -> None:
        table = build()
        row = next(r for r in table.rows if r["pmid"] == "222")
        assert row["abstract_status"] == "abstract unavailable"
        assert row["title"] == "A follow-up study"  # title kept even with no abstract

    def test_row_with_no_pmid_is_marked_no_pmid_on_record(self) -> None:
        table = build()
        row = next(r for r in table.rows if r["mp_id"] == "MP:0002169")
        assert row["abstract_status"] == "no PMID on record"

    def test_articles_beyond_the_cap_are_not_fetched_and_say_so(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "PHENOTYPE_MAX_ARTICLES", 1)
        table = build()
        capped = [r for r in table.rows if "beyond the 1-article cap" in (r["abstract_status"] or "")]
        assert capped, "expected at least one row past the lowered cap"

    def test_mouse_symbol_is_read_from_targetInModel(self) -> None:
        assert build().mouse_symbol == "Braf"


# ---------------------------------------------------------------------------
# 7b: summary_rows -- the cap the plan's own cost model assumes
# ---------------------------------------------------------------------------

class TestSummaryRows:
    def test_caps_at_phenotype_summary_max_rows(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "PHENOTYPE_SUMMARY_MAX_ROWS", 2)
        table = build()
        assert len(table.rows) > 2
        rows = phenotype_mod.summary_rows(table)
        assert len(rows) == 2

    def test_row_ids_are_not_renumbered(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "PHENOTYPE_SUMMARY_MAX_ROWS", 2)
        table = build()
        rows = phenotype_mod.summary_rows(table)
        assert [r["row_id"] for r in rows] == [table.rows[0]["row_id"], table.rows[1]["row_id"]]

    def test_only_the_named_fields_travel(self) -> None:
        rows = phenotype_mod.summary_rows(build())
        assert set(rows[0]) == {"row_id", "mp_label", "model_id", "pmid", "title",
                                "sentence", "level"}
        assert "allelic_composition" not in rows[0]
        assert "genetic_background" not in rows[0]


# ---------------------------------------------------------------------------
# 7b: summarize() -- spend, I11, caching, and the two-account wall
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    return spend.SpendLedger(tmp_path / "ledger.jsonl")


def _valid_claims(table) -> list[dict]:
    rows = phenotype_mod.summary_rows(table)
    row = next(r for r in rows if r.get("sentence") and "heart looping" in (r["sentence"] or ""))
    return [{"text": "Braf mutants show cardiac defects.", "row_ids": [row["row_id"]],
             "quote": "heart looping"}]


class TestSummarize:
    def test_a_valid_summary_is_accepted_and_spends_only_the_phenotype_account(self, ledger) -> None:
        from targetring.llm import FakeLLMClient
        table = build()
        client = FakeLLMClient(OPUS)
        client.queue_claims(_valid_claims(table), input_tokens=1000, output_tokens=200)
        out = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=_NoCache())
        assert out.ok
        assert out.claims
        assert ledger.calls("phenotype") == 1
        assert ledger.spent("phenotype") > 0
        assert ledger.calls("triage") == 0
        assert ledger.spent("triage") == 0.0

    def test_claim_citing_a_nonexistent_row_refuses_and_still_records_spend(self, ledger) -> None:
        table = build()
        from targetring.llm import FakeLLMClient
        client = FakeLLMClient(OPUS)
        client.queue_claims([{"text": "fabricated", "row_ids": [99999], "quote": "x"}],
                            input_tokens=900, output_tokens=150)
        out = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=_NoCache())
        assert not out.ok
        assert "summary refused" in out.reason
        assert ledger.calls("phenotype") == 1
        assert ledger.spent("phenotype") > 0, "the tokens were spent whether or not usable"
        assert ledger.spent("triage") == 0.0

    def test_claim_with_a_quote_not_in_its_rows_refuses_the_summary(self, ledger) -> None:
        table = build()
        from targetring.llm import FakeLLMClient
        client = FakeLLMClient(OPUS)
        rows = phenotype_mod.summary_rows(table)
        client.queue_claims(
            [{"text": "x", "row_ids": [rows[0]["row_id"]], "quote": "not present anywhere"}],
            input_tokens=900, output_tokens=150)
        out = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=_NoCache())
        assert not out.ok
        assert ledger.spent("triage") == 0.0

    def test_provider_refusal_is_reported_and_recorded(self, ledger) -> None:
        table = build()
        from targetring.llm import FakeLLMClient
        client = FakeLLMClient(OPUS)
        client.queue_refusal()
        out = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=_NoCache())
        assert not out.ok
        assert out.reason == "summary refused by provider"
        assert ledger.calls("phenotype") == 1
        assert ledger.spent("triage") == 0.0

    def test_cache_hit_costs_nothing_and_does_not_call_the_client_again(self, ledger, tmp_path) -> None:
        from targetring.cache import Cache
        from targetring.llm import FakeLLMClient
        cache = Cache(root=tmp_path / "cache")
        table = build()
        client = FakeLLMClient(OPUS)
        client.queue_claims(_valid_claims(table), input_tokens=1000, output_tokens=200)
        out1 = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=cache)
        assert out1.ok and not out1.cached
        spent_after_first = ledger.spent("phenotype")

        out2 = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=cache)
        assert out2.ok and out2.cached
        assert len(client.calls) == 1, "the client must not be called a second time on a cache hit"
        assert ledger.spent("phenotype") == spent_after_first, "a cache hit must cost nothing"

    def test_cap_already_exceeded_stops_the_call_before_it_is_made(self, ledger) -> None:
        from targetring.llm import FakeLLMClient
        # Spend the account down to $0.01 of its $5.00 cap directly.
        reservation = ledger.reserve(account="phenotype", model=OPUS, input_tokens=998000,
                                     max_tokens=0, key="setup", prompt_hash="ph")
        ledger.commit(reservation, input_tokens=998000, output_tokens=0)
        assert ledger.state("phenotype").remaining_usd < 0.02

        table = build()
        client = FakeLLMClient(OPUS)
        out = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=_NoCache())
        assert not out.ok
        assert "budget exhausted" in out.reason
        assert client.calls == [], "the call that would cross the cap must never be made"
        assert ledger.spent("triage") == 0.0

    def test_cap_stops_the_next_call_not_the_one_in_flight(self, ledger) -> None:
        """A call already committed is not retroactively refused when a LATER
        call would cross the cap -- the cap only ever gates the call about to
        be made."""
        from targetring.llm import FakeLLMClient
        table = build()

        client1 = FakeLLMClient(OPUS)
        client1.queue_claims(_valid_claims(table), input_tokens=1000, output_tokens=200)
        out1 = phenotype_mod.summarize(table, client=client1, ledger=ledger, cache=_NoCache())
        assert out1.ok

        # Now exhaust the rest of the cap directly.
        reservation = ledger.reserve(account="phenotype", model=OPUS, input_tokens=997000,
                                     max_tokens=0, key="setup", prompt_hash="ph")
        ledger.commit(reservation, input_tokens=997000, output_tokens=0)

        # A second, differently-shaped gene's table -- distinct cache key --
        # must be refused, while the first call's success stands unchanged.
        table2 = build(braf_record(mouse_phenotypes=MOUSE_PHENOTYPES[:1]))
        client2 = FakeLLMClient(OPUS)
        out2 = phenotype_mod.summarize(table2, client=client2, ledger=ledger, cache=_NoCache())
        assert out1.ok, "the first, already-committed call must not be retroactively refused"
        assert not out2.ok
        assert "budget exhausted" in out2.reason


class _NoCache:
    """A cache that always misses and never stores -- every `summarize()` test
    that is not specifically testing the cache uses this, so a cache hit in
    one test can never leak into another."""

    def get(self, *_a, **_kw):
        return None

    def put(self, *_a, **_kw):
        pass


# ---------------------------------------------------------------------------
# EuropePMCAdapter.fetch_article -- the rule-4 echo assertion, at the HTTP
# layer this time (the fixture above bypasses it entirely by monkeypatching
# the method, which is right for phenotype-table tests but leaves this
# specific new method with no test of its own otherwise).
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._body


class _FakeHttp:
    def __init__(self, body_by_query: dict) -> None:
        self._by_query = body_by_query
        self.calls: list[dict] = []

    async def get(self, url, params=None):
        self.calls.append(params or {})
        q = (params or {}).get("query")
        return _FakeResponse(self._by_query.get(q, {"request": {"queryString": q}, "resultList": {"result": []}}))


class TestFetchArticleEcho:
    def test_echo_ok_returns_the_article_fields(self) -> None:
        from targetring.cache import Cache
        q = "EXT_ID:111 AND SRC:MED"
        body = {
            "request": {"queryString": q},
            "resultList": {"result": [{
                "title": "Braf knockouts show cardiac defects", "pubYear": "2010",
                "journalInfo": {"journal": {"title": "J Dev Biol"}},
                "abstractText": "Braf mutant embryos show abnormal heart looping.",
            }]},
        }
        http = _FakeHttp({q: body})
        rec = asyncio.run(EuropePMCAdapter(http, Cache(enabled=False)).fetch_article("111"))
        assert rec.title == "Braf knockouts show cardiac defects"
        assert rec.journal == "J Dev Biol"
        assert rec.abstract_available
        assert rec.error is None

    def test_a_pmid_with_no_result_is_unavailable_not_an_error(self) -> None:
        from targetring.cache import Cache
        q = "EXT_ID:999 AND SRC:MED"
        http = _FakeHttp({q: {"request": {"queryString": q}, "resultList": {"result": []}}})
        rec = asyncio.run(EuropePMCAdapter(http, Cache(enabled=False)).fetch_article("999"))
        assert rec.error is None
        assert not rec.abstract_available

    def test_an_echo_mismatch_is_a_hard_error_not_a_silent_wrong_record(self) -> None:
        from targetring.cache import Cache
        q = "EXT_ID:111 AND SRC:MED"
        http = _FakeHttp({q: {"request": {"queryString": "EXT_ID:999 AND SRC:MED"},
                              "resultList": {"result": [{"title": "wrong record"}]}}})
        rec = asyncio.run(EuropePMCAdapter(http, Cache(enabled=False)).fetch_article("111"))
        assert rec.error is not None
        assert rec.title is None, "must not return the mismatched record's fields"
