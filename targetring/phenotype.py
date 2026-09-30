"""Stage 7: the phenotype page. §9 — per-gene, lives in the drawer, NOT a step
in the disease->contrast->heatmap flow.

**7a, the table: structured sources only, no LLM, no cost.** Rows come from
Open Targets `mousePhenotypes` x `biologicalModels` x `literature` — one row
per (phenotype, model, PMID) — plus, for each distinct PMID up to
`config.PHENOTYPE_MAX_ARTICLES`, one Europe PMC article fetch. Every row
carries a rule-6 level (`asserted` / `asserted-negative`) with its verbatim
label, and a sentence: the first abstract sentence naming the gene or its
mouse symbol, or "gene not named in abstract" when none does. A gene with no
mousePhenotypes rows at all is `none found` and the page stops — no LLM call.

**7b, the summary: the one paid call.** `llm.PhenotypeLLMClient.complete()` on
`summary_rows()` — the first `config.PHENOTYPE_SUMMARY_MAX_ROWS` table rows
(see `config.py`'s note on why that cap exists and is not in the plan as
written), never the DE table, never anything Puck typed (rule 10). Every claim
is checked against `invariants.check_phenotype_claims_citable` (I11) before it
is ever returned; a failing claim refuses the WHOLE summary and the table
renders with a banner. Spend is reserved before the call and committed after,
on the `phenotype` account only (`spend.PHENOTYPE`) — the `triage` account is
never touched by anything in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import config, invariants, llm as llm_mod, spend as spend_mod
from .adapters.literature import ArticleRecord, EuropePMCAdapter
from .adapters.opentargets import NO_ABNORMAL, OTRecord

# Rule 6, verbatim, for every row at the `asserted` level. Not the mp_label —
# the label MGI's finding gets in this build, independent of which of the 77+
# possible MP terms it is.
RULE6_ASSERTED_LABEL = "abstract asserts target manipulation altered the phenotype (unverified)"

LEVEL_ASSERTED = "asserted"
LEVEL_ASSERTED_NEGATIVE = "asserted-negative"
LEVEL_NONE_FOUND = "none found"

_LEVEL_LABEL = {
    LEVEL_ASSERTED: RULE6_ASSERTED_LABEL,
    LEVEL_ASSERTED_NEGATIVE: NO_ABNORMAL,
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _is_negative_label(mp_label: str) -> bool:
    return NO_ABNORMAL in str(mp_label or "").lower()


def first_sentence_naming(abstract_text: str | None, *symbols: str) -> str:
    """The first abstract sentence containing any of `symbols` (word-boundary,
    case-insensitive), or the stated fallback.

    PLAN §3 stage 7a: "the first abstract sentence containing the gene's
    symbol or its mouse ortholog symbol, or 'gene not named in abstract'."
    """
    if not abstract_text:
        return "gene not named in abstract"
    names = [s for s in symbols if s]
    if not names:
        return "gene not named in abstract"
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(s) for s in names) + r")\b", re.IGNORECASE)
    for sentence in _SENTENCE_SPLIT.split(abstract_text):
        if pattern.search(sentence):
            return sentence.strip()
    return "gene not named in abstract"


@dataclass
class PhenotypeTable:
    """7a's output. `rows` is never truncated — the on-screen table shows
    every (phenotype, model, PMID) row this gene has. Only `llm.summary_rows`
    (below) caps what leaves this process."""

    gene_symbol: str
    ensembl_id: str
    mouse_symbol: str | None
    rows: list[dict[str, Any]] = field(default_factory=list)
    n_distinct_pmids: int = 0
    n_articles_fetched: int = 0
    max_articles_cap: int = config.PHENOTYPE_MAX_ARTICLES

    @property
    def state(self) -> str:
        return LEVEL_NONE_FOUND if not self.rows else "has_rows"

    @property
    def level_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r["level"]] = out.get(r["level"], 0) + 1
        return out


def build_table_rows(rec: OTRecord) -> list[dict[str, Any]]:
    """The raw (phenotype, model, PMID) expansion, before any article fetch.

    Missing models or missing PMIDs on a row are kept, not dropped (AGENTS.md
    rule 1's spirit applied one level down from "gene"): a phenotype MGI
    curates with no biologicalModels record, or a model with no literature PMID
    on it, is a real and different fact from that phenotype not existing at
    all, and collapsing the two would read as fewer papers than MGI actually
    cites.
    """
    rows: list[dict[str, Any]] = []
    for mp in rec.mouse_phenotypes:
        mp_id = mp.get("modelPhenotypeId")
        mp_label = mp.get("modelPhenotypeLabel") or ""
        models = mp.get("biologicalModels") or []
        if not models:
            rows.append({"mp_id": mp_id, "mp_label": mp_label, "model_id": None,
                        "allelic_composition": None, "genetic_background": None,
                        "pmid": None})
            continue
        for m in models:
            lits = m.get("literature") or []
            base = {"mp_id": mp_id, "mp_label": mp_label, "model_id": m.get("id"),
                    "allelic_composition": m.get("allelicComposition"),
                    "genetic_background": m.get("geneticBackground")}
            if not lits:
                rows.append({**base, "pmid": None})
                continue
            for pmid in lits:
                rows.append({**base, "pmid": str(pmid)})
    return rows


async def build_table(rec: OTRecord, *, gene_symbol: str, ensembl_id: str,
                      http: Any, cache: Any) -> PhenotypeTable:
    """7a end to end: the (phenotype, model, PMID) rows, article fetch on the
    distinct PMIDs up to the cap, sentence extraction, rule-6 levels.
    """
    base_rows = build_table_rows(rec)
    mouse_symbol = next(
        (mp.get("targetInModel") for mp in rec.mouse_phenotypes if mp.get("targetInModel")),
        None)

    distinct_pmids = list(dict.fromkeys(r["pmid"] for r in base_rows if r["pmid"]))
    to_fetch = distinct_pmids[:config.PHENOTYPE_MAX_ARTICLES]
    adapter = EuropePMCAdapter(http, cache)
    articles: dict[str, ArticleRecord] = {}
    if to_fetch:
        import asyncio
        results = await asyncio.gather(*(adapter.fetch_article(p) for p in to_fetch))
        articles = {a.pmid: a for a in results}

    rows: list[dict[str, Any]] = []
    for i, r in enumerate(base_rows, start=1):
        level = LEVEL_ASSERTED_NEGATIVE if _is_negative_label(r["mp_label"]) else LEVEL_ASSERTED
        art = articles.get(r["pmid"]) if r["pmid"] else None
        if r["pmid"] is None:
            abstract_status = "no PMID on record"
            title = year = journal = sentence = None
        elif art is None:
            abstract_status = f"not fetched (beyond the {config.PHENOTYPE_MAX_ARTICLES}-article cap)"
            title = year = journal = sentence = None
        elif art.error:
            abstract_status = f"abstract unavailable ({art.error[:120]})"
            title, year, journal, sentence = art.title, art.year, art.journal, None
        elif not art.abstract_available:
            abstract_status = "abstract unavailable"
            title, year, journal = art.title, art.year, art.journal
            sentence = first_sentence_naming(None)
        else:
            abstract_status = "fetched"
            title, year, journal = art.title, art.year, art.journal
            sentence = first_sentence_naming(art.abstract_text, gene_symbol, mouse_symbol)
        rows.append({
            "row_id": i, "mp_id": r["mp_id"], "mp_label": r["mp_label"],
            "model_id": r["model_id"], "allelic_composition": r["allelic_composition"],
            "genetic_background": r["genetic_background"], "pmid": r["pmid"],
            "level": level, "level_label": _LEVEL_LABEL[level],
            "title": title, "year": year, "journal": journal,
            "abstract_status": abstract_status, "sentence": sentence,
        })

    return PhenotypeTable(
        gene_symbol=gene_symbol, ensembl_id=ensembl_id, mouse_symbol=mouse_symbol,
        rows=rows, n_distinct_pmids=len(distinct_pmids),
        n_articles_fetched=len(to_fetch),
    )


# --- 7b: the summary ---------------------------------------------------------


def summary_rows(table: PhenotypeTable) -> list[dict[str, Any]]:
    """The rows sent to the model: the first `PHENOTYPE_SUMMARY_MAX_ROWS` of
    the table, in table order, with only the fields the prompt names.

    Row ids are NOT renumbered — they stay the table's own `row_id`. That is
    what makes I11 exact: the `rows` this function returns are the same
    objects `invariants.check_phenotype_claims_citable` validates against, so
    a claim citing a row past the cut is a claim citing a row that was never
    sent, and correctly fails "row_id does not exist" rather than being judged
    against the full table it never saw.
    """
    fields = ("row_id", "mp_label", "model_id", "pmid", "title", "sentence", "level")
    cut = table.rows[:config.PHENOTYPE_SUMMARY_MAX_ROWS]
    return [{k: r.get(k) for k in fields} for r in cut]


def _rows_hash(rows: list[dict[str, Any]], model: str) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str) + llm_mod.prompt_hash() + model
    return hashlib.sha256(payload.encode()).hexdigest()


def _estimate_input_tokens(rows: list[dict[str, Any]]) -> int:
    """A deliberately high estimate for `spend.reserve`'s pre-call worst-case
    gate -- 3 characters/token is conservative for English+JSON, which is the
    direction this estimate must err in (spend.py reserves worst case, never
    best case). The ACTUAL charge always comes from `commit()`'s real usage,
    never from this number.
    """
    text = llm_mod.SYSTEM_PROMPT + llm_mod.user_content(rows)
    return max(1, len(text) // 3)


@dataclass
class SummaryOutcome:
    """What `summarize` returns: exactly one of a rendered summary, a refusal
    with a reason, or "not attempted" (gene had no rows)."""

    attempted: bool
    ok: bool = False
    claims: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    cached: bool = False
    cached_at: str | None = None
    model: str = ""
    prompt_hash: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    account: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["note"] = "summarises the rows below only"
        # AGENTS.md rule 10 / PLAN §1 item 10: stated explicitly on every call,
        # not just true in the code. The model never sees the DE table, the run
        # id, the region, or anything Puck typed.
        d["sends_only"] = ("MP terms, model ids, PMIDs, public paper titles "
                           "and one abstract sentence per row — the rows shown "
                           "below, nothing else")
        return d


def summarize(table: PhenotypeTable, *, client: llm_mod.PhenotypeLLMClient,
              ledger: spend_mod.SpendLedger, cache: Any) -> SummaryOutcome:
    """7b end to end. Never called when `table.state == 'none found'` — the
    caller (`app.py`) gates on that first, per PLAN §3 stage 7a: "a gene with
    zero rows... the page says so and stops, no LLM call."
    """
    rows = summary_rows(table)
    model = client.model
    ph = llm_mod.prompt_hash()
    key = f"{table.gene_symbol}:{table.ensembl_id}"
    cache_key = _rows_hash(rows, model)

    cached = cache.get("llm.phenotype_summary", cache_key)
    if cached is not None:
        return SummaryOutcome(
            attempted=True, ok=True, claims=cached["claims"], cached=True,
            cached_at=cached["cached_at"], model=model, prompt_hash=ph,
            input_tokens=cached["input_tokens"], output_tokens=cached["output_tokens"],
            cost_usd=cached["cost_usd"], account=ledger.state(spend_mod.PHENOTYPE).as_dict(),
        )

    projected_input = _estimate_input_tokens(rows)
    try:
        reservation = ledger.reserve(
            account=spend_mod.PHENOTYPE, model=model, input_tokens=projected_input,
            max_tokens=config.PHENOTYPE_LLM_MAX_TOKENS, key=key, prompt_hash=ph,
        )
    except spend_mod.CapExceeded as e:
        return SummaryOutcome(
            attempted=True, ok=False,
            reason=(f"phenotype LLM budget exhausted: "
                    f"${ledger.state(spend_mod.PHENOTYPE).spent_usd:.2f} of "
                    f"${ledger.state(spend_mod.PHENOTYPE).cap_usd:.2f}; "
                    f"summary not generated"),
            model=model, prompt_hash=ph, account=ledger.state(spend_mod.PHENOTYPE).as_dict(),
        )
    except spend_mod.UnpricedModel as e:
        return SummaryOutcome(attempted=True, ok=False, reason=str(e), model=model,
                              prompt_hash=ph, account=ledger.state(spend_mod.PHENOTYPE).as_dict())

    outcome = client.complete(rows, max_tokens=config.PHENOTYPE_LLM_MAX_TOKENS)

    if not outcome.ok:
        status = (spend_mod.STATUS_REFUSED_VALIDATION if outcome.refused_by_provider
                 else spend_mod.STATUS_API_ERROR)
        ledger.commit(reservation, input_tokens=outcome.input_tokens,
                      output_tokens=outcome.output_tokens,
                      cache_read_tokens=outcome.cache_read_tokens, status=status,
                      note=outcome.error or "")
        reason = ("summary refused by provider" if outcome.refused_by_provider
                 else f"phenotype summary call failed: {outcome.error}")
        return SummaryOutcome(attempted=True, ok=False, reason=reason, model=model,
                              prompt_hash=ph, input_tokens=outcome.input_tokens,
                              output_tokens=outcome.output_tokens,
                              account=ledger.state(spend_mod.PHENOTYPE).as_dict())

    i11 = invariants.check_phenotype_claims_citable(outcome.claims, rows)
    if i11.failed:
        ledger.commit(reservation, input_tokens=outcome.input_tokens,
                      output_tokens=outcome.output_tokens,
                      cache_read_tokens=outcome.cache_read_tokens,
                      status=spend_mod.STATUS_REFUSED_VALIDATION, note=i11.detail)
        return SummaryOutcome(
            attempted=True, ok=False,
            reason=f"summary refused: {i11.detail}", model=model, prompt_hash=ph,
            input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
            account=ledger.state(spend_mod.PHENOTYPE).as_dict())

    rec = ledger.commit(reservation, input_tokens=outcome.input_tokens,
                        output_tokens=outcome.output_tokens,
                        cache_read_tokens=outcome.cache_read_tokens,
                        status=spend_mod.STATUS_OK)
    cache.put("llm.phenotype_summary", cache_key, {
        "claims": outcome.claims, "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens, "cost_usd": rec["usd"],
        "cached_at": datetime.now(timezone.utc).date().isoformat(),
    })
    return SummaryOutcome(
        attempted=True, ok=True, claims=outcome.claims, model=model, prompt_hash=ph,
        input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
        cost_usd=rec["usd"], account=ledger.state(spend_mod.PHENOTYPE).as_dict())


__all__ = [
    "LEVEL_ASSERTED", "LEVEL_ASSERTED_NEGATIVE", "LEVEL_NONE_FOUND",
    "PhenotypeTable", "RULE6_ASSERTED_LABEL", "SummaryOutcome",
    "build_table", "build_table_rows", "first_sentence_naming", "summarize",
    "summary_rows",
]
