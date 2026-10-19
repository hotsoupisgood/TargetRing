"""The per-gene evidence summary -- a baked, LLM-written paragraph shown when a
gene is highlighted. Puck, 2026-10-18: *"a text box on highlight that has an
LLM interpretation of the values, i.e. hypothesis is downregulation (because
upregulated in study), mouse model shows this phenotype, it's expressed in this
part of the body strongly ... basically a summary of evidence, kind of dry."*

This is a fifth paid call, and it is made OFFLINE, never by the app. The app
serves what `scripts/bake_summaries.py` wrote into the gene's baked drawer
(`runs/<id>/drawer/<GENE>.json`, key `summary`) through the file-read route the
drawer already uses. A demo box therefore shows summaries and holds no key; a
run with no baked summary shows nothing, never an error.

What the model is given, and what it is not:

* **Only the run's own numbers for that gene**, read off the baked drawer,
  which `drawer_bake.py` built cell-for-cell from `values.csv`: the study logFC
  and adjusted p with the contrast's sign sentence; every ring's state, value
  and recorded detail, with `no_data` / `failed` rings named as such; the Open
  Targets mouse phenotype terms; the GTEx region ratio; the pipeline counts;
  the ChEMBL direct-interaction count; the TDL; the PubTator count; the drop
  ledger status. Plus the ring definitions from `figure.RING_SPECS` (PLAN §2),
  which are constant text and therefore live in the system prompt, under the
  prompt hash.
* **No outside knowledge.** The system prompt says so. The output is a reading
  of the values on the figure, not a literature review, and it must be checkable
  against the drawer it sits in -- which is why the exact `facts` object the
  model saw is stored beside the text it wrote (`summary.inputs`).
* **Not a recommendation.** The first sentence names a hypothesis DIRECTION
  (inhibit something up-regulated in disease, restore something down-regulated)
  because that is the arithmetic of the sign, and nothing else in the text may
  rank, score or advise (AGENTS.md rule 7, PLAN §3). Rule 6's framing holds for
  the mouse sentence: a lead, not a verdict.

Instrument hygiene, as everywhere else: pinned model (`config.SUMMARY_LLM_MODEL`,
priced in `config.LLM_PRICE_USD_PER_MTOK` or the ledger refuses it), prompt text
hashed into `prompt_version`, low effort (paraphrase, not reasoning), every
output stored with its inputs, its model, its usage and its date. Reserve
worst-case on the `summary` spend account before the call, commit actual after
(`spend.SpendLedger`). No fallback model, no silent retry (PLAN §6).

Identical facts produce one call: the four demo runs are one contrast and share
most genes cell-for-cell, so a summary is keyed on sha256(facts + prompt hash +
model) and reused across runs where the key matches. Each run's drawer still
carries its own full copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from . import config
from . import recommend as recommend_mod
from . import spend as spend_mod
from . import table as table_mod
from .http import describe as describe_error
from .order import EXTERNAL_RINGS

# --- the prompt, pinned so its hash is stable and auditable ------------------

SYSTEM_PROMPT_HEAD = """You write a short, dry evidence summary for ONE human gene on a target-prioritisation figure.

You will be given a JSON object of facts about that gene, read from a single analysis run: the study's differential-expression result for the gene (log2 fold change and adjusted p, with a sentence saying which direction is "up"), the value and state of each of the figure's rings, a list of mouse phenotype terms, GTEx expression (the region's TPM, the panel median, whole-blood and spleen TPM when baked, and whether the region is a disease tissue for this contrast), an immune-origin hint computed from those TPMs with its rule, drug-pipeline counts and the furthest-stage agent, a ChEMBL mechanism count, a Pharos development level, a literature count, and the gene's drop-ledger status.

Your ONLY source of information is that JSON. Do not add anything you know or believe about this gene, this disease, these drugs or these phenotypes from anywhere else. Do not name a paper, a trial, a mechanism or a pathway that is not in the facts. If a fact is not there, it is not there.

Write 4 to 6 sentences of plain prose, no headings, no bullets, no markdown. Four sentences is the usual length; six is the ceiling, not a target. In this order, and nothing else:

0. First sentence, when the facts carry a "recommendation" object: state its verdict and agree with it. Start with the verdict word exactly as given ("GO", "NO-GO" or "INSUFFICIENT DATA"), then a colon, then one clause per criterion naming its status and the value shown, in the order given. Example of the form: "GO: expressed (26.6 TPM in lung), dysregulated in the study (logFC +2.2, adjusted p 4e-14), and a mouse phenotype on record (6 abnormal terms)." The verdict is the owner's own rule, computed before you were asked; do not compute, soften, dispute or re-derive it, and do not weigh druggability, safety or anything else into it. If the facts carry no "recommendation" object, write no verdict sentence. If "recommendation.modality" is present, append its sentence verbatim after the verdict sentence.
1. Then one sentence starting with the word "Hypothesis:". State the direction the figure's arithmetic implies and why, quoting the numbers. A positive log2FC in the disease-versus-control contrast means the gene is UP in disease, so the hypothesis is inhibition / down-regulation; a negative log2FC means DOWN in disease, so the hypothesis is restoration / up-regulation. Example of the form: "Hypothesis: inhibition, because the gene is up-regulated in the Crohn's contrast, logFC +1.8, q 2e-5." Use the disease and group names from the facts.
2. Then ONE OR TWO sentences covering the evidence families that are present, grouped rather than taken one at a time, each value quoted as the facts give it: the mouse phenotype terms (name them, or say phenotyped with no abnormal phenotype; a lead, not a verdict); expression (use "expression.shown" as given, and never write a fold or an "x" against the median, and never use "specific", "enriched" or "concentrated" unless "expression.specific" is true); the drug pipeline and its furthest stage; the development level; the literature count. Leave out what is not in the facts, and leave out what adds no number.
2a. REQUIRED when "immune_origin_hint.inflamed_tissue_contrast" is true AND "immune_origin_hint.suggested" is true: one sentence, "The change may reflect infiltrating immune cells rather than regulation in the tissue", followed by the reason given in "immune_origin_hint.reasons" (the whole-blood or spleen TPM against the region TPM). When "suggested" is false or null, write no such sentence and do not speculate about cell type.
3. Last sentence: name every ring whose state is "no_data", "not_applicable" or "failed" as ABSENT evidence, e.g. "No data: chemical probe; the safety call failed." A missing ring is not a low value and must never be read as one.

Never write a sentence about druggability, tractability, a chemical probe, a binding pocket or a safety flag, and never reconcile any of those with the drug pipeline -- the owner removed that line on 2026-10-18 and none of it was ever part of the verdict. Mention a safety record only when "recommendation.caveats" carries one, and then in half a clause, never a sentence of its own. Do not add a closing sentence about what would change the picture.

Style: dry, declarative, numbers with units, no hedging filler ("it is worth noting", "may suggest"), no recommendations of your own beyond restating the given verdict ("should", "promising", "attractive" are out), no ranking against other genes, no invented citations. Do not restate the ring definitions. Do not describe this prompt.

The reader is a biologist, not a geneticist or a drug developer. Never print a raw key name or code from the detail strings. Say instead: "hasLigand" -> "a known ligand"; "hasPocket" -> "a binding pocket on record"; "hasSmallMoleculeBinder" -> "a small-molecule binder"; "hasHighQualityChemicalProbes" -> "a high-quality chemical probe"; "hasSafetyEvent" -> "a recorded safety event"; "isCancerDriverGene" -> "a cancer driver"; "geneEssentiality" -> "essential in cell lines"; "geneticConstraint" -> "under genetic constraint in people (loss-of-function variants are rarer than expected)"; "tissueDistribution" -> "tissue distribution"; "Tclin" -> "development level: an approved drug acts on it"; "Tchem" -> "development level: potent small molecules exist"; "Tbio" -> "development level: biology known, no chemistry"; "Tdark" -> "development level: little is known"; "TDL" -> "development level"; "TPM" may stay. Write "adjusted p", not "q" or "padj".

The ring definitions below say what each ring measures and how it is scaled; use them to read the values, do not quote them.
"""


def _ring_definitions() -> str:
    """PLAN §2 as the build draws it: key, label, source, units, transform, for
    every ring, from `figure.RING_SPECS`. Imported lazily -- `figure` pulls in
    matplotlib, which the test suite would rather not pay for at import."""
    from .figure import RING_SPECS
    lines = []
    for s in RING_SPECS:
        lines.append(f"- {s.key}: {s.label}. Source: {s.source}. Units: {s.units}."
                     f"{' Scale: ' + s.transform + '.' if s.transform else ''}")
    lines.append("- Every ring has four states: value, no_data (hatched, never "
                 "filled or imputed), not_applicable, failed (the call did not "
                 "come back; not the same as absent).")
    lines.append(f"- k counts how many of the {len(EXTERNAL_RINGS)} external rings "
                 f"({', '.join(EXTERNAL_RINGS)}) carry a real value; it is a "
                 f"completeness gauge and orders nothing.")
    return "\n".join(lines)


def system_prompt() -> str:
    return SYSTEM_PROMPT_HEAD + "\nRing definitions:\n" + _ring_definitions() + "\n"


def prompt_hash() -> str:
    """Over the exact text sent as `system`, ring definitions included -- a
    change to a ring's label or scale changes what the model was told."""
    return hashlib.sha256(system_prompt().encode()).hexdigest()[:16]


# --- the facts: everything the model sees, built from the baked drawer -------

_PADJ_RE = re.compile(r"adjusted p ([0-9.eE+-]+)")

#: Ring keys, in PLAN §2 order, that a summary must account for. Read from the
#: drawer's `cells`; a ring the run did not draw at all is reported as such.
RING_KEYS = ("logfc", "neg_log10_padj", "tractable", "chemical_probe", "safety",
             "regional_specificity", "clinical_phase", "clinical_activity", "tdl",
             "rodent", "literature", "completeness")


def _ledger_status(manifest: dict[str, Any], gene: str, input_symbol: str) -> str:
    for e in manifest.get("ledger") or []:
        if e.get("resolved_symbol") == gene or e.get("input") == input_symbol:
            return f"{e.get('status')}: {e.get('reason', '')}".strip(": ")
    return "rendered; not in the drop ledger (submitted as drawn)"


def build_facts(drawer: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """The JSON object the model is given. Everything is read; nothing is
    computed beyond sign words and counts, and a missing ring is written down as
    missing rather than left out -- the prompt's rule 3 needs to see it."""
    cells = drawer.get("cells") or {}
    contrast = manifest.get("contrast") or {}
    logfc = drawer.get("logfc")
    padj = drawer.get("padj")
    if padj is None and "neg_log10_padj" in cells:
        m = _PADJ_RE.search(cells["neg_log10_padj"].get("detail") or "")
        padj = float(m.group(1)) if m else None
    direction = (None if logfc is None else
                 "up in the test group" if logfc > 0 else
                 "down in the test group" if logfc < 0 else "no change")

    rings = []
    for key in RING_KEYS:
        c = cells.get(key)
        if c is None:
            rings.append({"ring": key, "state": "not_drawn",
                          "value": None, "detail": "this run did not draw this ring"})
        else:
            rings.append({"ring": key, "state": c.get("state"),
                          "value": c.get("value"), "detail": c.get("detail") or ""})
    absent = [r["ring"] for r in rings if r["state"] != "value"]

    tr = drawer.get("trials") or {}
    ch = drawer.get("chembl") or {}
    tdl = drawer.get("tdl") or {}
    lit = drawer.get("literature") or {}
    drugs = drawer.get("drugs") or []
    stages = sorted({d.get("max_stage") for d in drugs if d.get("max_stage")})

    # 2026-10-18 walkthrough. The expression line, the disease-area aid and the
    # immune-origin hint all come from `recommend.py` over the same facts the
    # verdict read, so the summary and the verdict box cannot disagree about
    # the region or the TPMs. The furthest-stage agent is named with its
    # modality and indications so the model can reconcile "not druggable" with
    # "Phase 3, 12 agents" (prompt rule 2b) instead of leaving both standing.
    rec_facts = recommend_mod.facts_from_drawer(drawer, manifest)
    rec_block = drawer.get("recommendation") or {}
    expression = rec_block.get("expression") or recommend_mod.expression_summary(
        rec_facts, recommend_mod.current_thresholds())
    furthest = _furthest_agent(drugs)

    return {
        "gene": drawer.get("gene"),
        "input_symbol": drawer.get("input_symbol"),
        "ensembl_id": drawer.get("ensembl_id"),
        "study": {
            "source": (f"Expression Atlas {contrast.get('accession')} "
                       f"{contrast.get('contrast_id')}".strip()
                       if contrast else "submitted DE table"),
            "contrast_name": contrast.get("name") or "the submitted table",
            "sign_sentence": contrast.get("sign_sentence") or
                             "positive log2FC = up in the test group of the submitted table",
            "test_group": contrast.get("test_label"),
            "reference_group": contrast.get("ref_label"),
            "n_test": contrast.get("n_test"),
            "n_ref": contrast.get("n_ref"),
            "log2_fold_change": logfc,
            "adjusted_p": padj,
            "direction": direction,
        },
        "region": manifest.get("region"),
        "expression": expression,
        "immune_origin_hint": recommend_mod.immune_origin_hint(rec_facts),
        "rings": rings,
        "rings_without_a_value": absent,
        "k": drawer.get("k"),
        "k_max": drawer.get("k_max"),
        "mouse_phenotypes": list(drawer.get("mouse_phenotypes") or []),
        "pipeline": {
            "n_drugs": len(drugs),
            "stages_reached": stages,
            "furthest_agent": furthest,
            "summary_line": drawer.get("pipeline_summary"),
            "n_distinct_trials": tr.get("n_distinct_trials"),
            "n_live_trials": tr.get("n_distinct_live"),
            "n_stopped_trials": tr.get("n_distinct_stopped"),
            "n_stopped_for_cause": tr.get("n_pairs_for_cause"),
            "severe_safety_liabilities": [s.get("event") for s in
                                          (drawer.get("severe_liabilities") or [])],
            "n_safety_liability_rows": len(drawer.get("safety_liabilities") or []),
        },
        "chembl": {
            "state": ch.get("state"),
            "n_mechanisms": ch.get("n_mechanisms"),
            "n_direct_interaction": ch.get("n_direct"),
            "n_direct_with_disease_efficacy": ch.get("n_direct_efficacy"),
        },
        "tdl": {"state": tdl.get("state"),
                "label": ((tdl.get("raw") or {}).get("tdl")),
                "detail": tdl.get("detail")},
        "literature": {"pubtator_records": lit.get("pubtator_count"),
                       "europepmc_records": lit.get("europepmc_count")},
        "ledger_status": _ledger_status(manifest, drawer.get("gene") or "",
                                        drawer.get("input_symbol") or ""),
        # The GO / NO-GO verdict `drawer_bake` computed (`recommend.py`, Puck's
        # explicit rule). The model restates it in the first sentence; it is an
        # input, never something the model decides. Absent when the drawer was
        # baked before the rule existed, and then no verdict sentence is written.
        "recommendation": _recommendation_facts(drawer.get("recommendation")),
    }


def _recommendation_facts(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    area = rec.get("disease_area") or {}
    return {
        "verdict": rec.get("verdict"),
        "criteria": [{"criterion": c.get("label"), "status": c.get("status"),
                      "shown": c.get("shown")} for c in rec.get("criteria") or []],
        "caveats": list(rec.get("caveats") or []),
        "modality": rec.get("modality"),
        "disease_area": {"shown": area.get("shown"), "matched": area.get("matched"),
                         "note": area.get("note")} if area else None,
        "rule": rec.get("rule_text"),
        "rule_version": rec.get("rule_version"),
    }


# `adapters.opentargets.CLINICAL_STAGE_ORDINAL` orders these; repeated as a
# plain list here so this module imports no adapter. Highest first.
_STAGE_ORDER = ("APPROVAL", "PHASE_4", "PHASE_3", "PHASE_2_3", "PHASE_2",
                "PHASE_1_2", "PHASE_1", "PRECLINICAL", "UNKNOWN")
_STAGE_WORDS = {"APPROVAL": "approved", "PHASE_4": "Phase 4", "PHASE_3": "Phase 3",
                "PHASE_2_3": "Phase 2/3", "PHASE_2": "Phase 2", "PHASE_1_2": "Phase 1/2",
                "PHASE_1": "Phase 1", "PRECLINICAL": "preclinical", "UNKNOWN": "unknown stage"}


def _furthest_agent(drugs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The drug row at the furthest clinical stage, with its modality and
    indications as the drawer has them. Ties go to the first row in the
    drawer's own order; nothing is ranked beyond the stage word."""
    best = None
    for d in drugs:
        stage = d.get("max_stage")
        if stage not in _STAGE_ORDER:
            continue
        # `phase_ordinal` is the adapter's own ordering of the stage when the
        # row carries it; the list above is the fallback for rows that do not.
        rank = (-(d.get("phase_ordinal") or 0), _STAGE_ORDER.index(stage))
        if best is None or rank < best[0]:
            best = (rank, d)
    if best is None:
        return None
    best = best[1]
    return {"name": best.get("drug"), "stage": _STAGE_WORDS.get(best.get("max_stage"), best.get("max_stage")),
            "modality": best.get("modality"),
            "indications": list(best.get("indications") or [])[:6]}


def user_content(facts: dict[str, Any]) -> str:
    return json.dumps(facts, sort_keys=True, default=str)


def facts_key(facts: dict[str, Any], model: str) -> str:
    """One summary per distinct (facts, prompt, model). Across the four demo
    runs most genes share every cell, so this is also what keeps the bake to a
    handful of calls."""
    return hashlib.sha256(
        (user_content(facts) + prompt_hash() + model).encode()).hexdigest()[:16]


# --- the outcome and the client ----------------------------------------------


@dataclass
class SummaryOutcome:
    ok: bool
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None
    error: str | None = None
    refused_by_provider: bool = False


class SummaryLLMClient(Protocol):
    model: str

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, SummaryOutcome]:
        """`items` is [(key, facts)]; returns one outcome per key."""
        ...


def _text_of(message: Any) -> str:
    return "\n".join(b.text for b in message.content
                     if getattr(b, "type", "") == "text").strip()


def _outcome_from_message(message: Any, model: str) -> SummaryOutcome:
    usage = message.usage
    base = dict(model=model, stop_reason=message.stop_reason,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0)
    if message.stop_reason == "refusal":
        return SummaryOutcome(ok=False, refused_by_provider=True,
                              error="summary refused by provider", **base)
    text = _text_of(message)
    if not text:
        return SummaryOutcome(ok=False, error="empty text in response", **base)
    if message.stop_reason == "max_tokens":
        return SummaryOutcome(ok=False, text=text,
                              error="cut off at max_tokens; not stored", **base)
    return SummaryOutcome(ok=True, text=text, **base)


class AnthropicSummaryClient:
    """The real client. Plain text out, `effort: low`, no `fallbacks` (PLAN §6),
    no sampling parameters (the pinned model rejects `temperature`; the output
    is pinned instead by storing it). Up to `BATCH_THRESHOLD` distinct prompts
    go one `messages.create` at a time; more than that go through the Message
    Batches API in one batch, polled until it ends."""

    BATCH_THRESHOLD = 20
    POLL_SECONDS = 20

    def __init__(self, model: str | None = None,
                 log: Callable[[str], None] = print) -> None:
        import anthropic  # deferred, as in `llm.py`
        self.model = model or config.SUMMARY_LLM_MODEL
        self._client = anthropic.Anthropic()
        self._log = log

    def _params(self, facts: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        return dict(model=self.model, max_tokens=max_tokens, system=system_prompt(),
                    output_config={"effort": config.SUMMARY_LLM_EFFORT},
                    messages=[{"role": "user", "content": user_content(facts)}])

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, SummaryOutcome]:
        if len(items) > self.BATCH_THRESHOLD:
            return self._batch(items, max_tokens)
        out: dict[str, SummaryOutcome] = {}
        for key, facts in items:
            out[key] = self._one(facts, max_tokens)
        return out

    def _one(self, facts: dict[str, Any], max_tokens: int) -> SummaryOutcome:
        import anthropic
        try:
            msg = self._client.messages.create(**self._params(facts, max_tokens))
        except anthropic.APIError as e:
            return SummaryOutcome(ok=False, model=self.model, error=str(e))
        except Exception as e:  # noqa: BLE001 -- e.g. no credentials resolvable
            return SummaryOutcome(ok=False, model=self.model, error=describe_error(e))
        return _outcome_from_message(msg, self.model)

    def _batch(self, items: list[tuple[str, dict[str, Any]]],
               max_tokens: int) -> dict[str, SummaryOutcome]:
        import anthropic
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        try:
            batch = self._client.messages.batches.create(requests=[
                Request(custom_id=key,
                        params=MessageCreateParamsNonStreaming(**self._params(f, max_tokens)))
                for key, f in items])
            self._log(f"  batch {batch.id}: {len(items)} requests submitted")
            while batch.processing_status != "ended":
                time.sleep(self.POLL_SECONDS)
                batch = self._client.messages.batches.retrieve(batch.id)
                self._log(f"  batch {batch.id}: {batch.processing_status}, "
                          f"{batch.request_counts.processing} processing")
            out: dict[str, SummaryOutcome] = {}
            for r in self._client.messages.batches.results(batch.id):
                if r.result.type == "succeeded":
                    out[r.custom_id] = _outcome_from_message(r.result.message, self.model)
                else:
                    err = getattr(getattr(r.result, "error", None), "message", None)
                    out[r.custom_id] = SummaryOutcome(
                        ok=False, model=self.model,
                        error=f"batch result {r.result.type}: {err or ''}".strip())
        except anthropic.APIError as e:
            return {k: SummaryOutcome(ok=False, model=self.model, error=str(e))
                    for k, _ in items}
        except Exception as e:  # noqa: BLE001
            return {k: SummaryOutcome(ok=False, model=self.model, error=describe_error(e))
                    for k, _ in items}
        # Keyed by custom_id, never by position: results arrive in any order.
        for k, _ in items:
            out.setdefault(k, SummaryOutcome(ok=False, model=self.model,
                                             error="no result returned for this id"))
        return out


class FakeSummaryClient:
    """A test double. Returns `text` for every key, records what it was asked."""

    def __init__(self, text: str = "Hypothesis: inhibition, because ...",
                 model: str | None = None, fail: bool = False) -> None:
        self.model = model or config.SUMMARY_LLM_MODEL
        self.text = text
        self.fail = fail
        self.calls: list[list[tuple[str, dict[str, Any]]]] = []

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, SummaryOutcome]:
        self.calls.append(items)
        if self.fail:
            return {k: SummaryOutcome(ok=False, model=self.model,
                                      error="simulated transport error")
                    for k, _ in items}
        return {k: SummaryOutcome(ok=True, text=self.text, model=self.model,
                                  stop_reason="end_turn", input_tokens=2000,
                                  output_tokens=250) for k, _ in items}


# --- the bake ----------------------------------------------------------------

#: An input-token estimate for the worst-case reservation. ~4 chars per token,
#: plus the system prompt, rounded up; the commit replaces it with the real usage.
def _estimate_input_tokens(facts: dict[str, Any]) -> int:
    return (len(system_prompt()) + len(user_content(facts))) // 3 + 50


def _write_atomic(path: Path, body: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1, default=str))
    os.replace(tmp, path)


@dataclass
class BakeReport:
    runs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    calls: int = 0
    spent_usd: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)
    stopped: str | None = None


def bake_summaries(run_ids: list[str], *, client: SummaryLLMClient,
                   ledger: spend_mod.SpendLedger | None = None, force: bool = False,
                   stop_at_usd: float | None = None,
                   log: Callable[[str], None] = print) -> BakeReport:
    """Write a `summary` block into every baked drawer of every run named.

    Genes without a baked drawer are skipped and named: the summary reads the
    drawer, so `scripts/bake_drawers.py` comes first. Genes whose drawer already
    carries a summary at this prompt hash and model are kept unless `force`.
    Distinct facts are collected across all runs first, then sent once each.
    Every run directory's mtime is restored afterwards (see `drawer_bake`).
    """
    ledger = ledger or spend_mod.default_ledger()
    report = BakeReport()
    phash = prompt_hash()
    model = client.model
    # key -> facts; key -> [(run_id, gene, path, drawer)]
    wanted: dict[str, dict[str, Any]] = {}
    targets: dict[str, list[tuple[str, str, Path, dict[str, Any]]]] = {}
    dir_times: dict[Path, tuple[int, int]] = {}
    # Summaries already on disk at this prompt and model, by facts key, so a
    # gene baked in one run is copied -- not re-bought -- into another run whose
    # drawer carries the same facts. The copy is byte-identical, inputs included.
    existing: dict[str, dict[str, Any]] = {}

    for run_id in run_ids:
        rep = report.runs.setdefault(run_id, {"written": [], "kept": [], "skipped": []})
        try:
            t = table_mod.load(run_id)
        except table_mod.TableUnavailable as e:
            report.errors[run_id] = str(e)
            log(f"{run_id}: cannot load -- {e}")
            continue
        run_dir = table_mod.run_dir(run_id)
        st = run_dir.stat()
        dir_times[run_dir] = (st.st_atime_ns, st.st_mtime_ns)
        for gene in t.genes:
            path = table_mod.baked_drawer_path(run_id, gene)
            if path is None:
                rep["skipped"].append(f"{gene}: no baked drawer (run scripts/bake_drawers.py first)")
                continue
            drawer = json.loads(path.read_text())
            have = drawer.get("summary") or {}
            if (not force and have.get("prompt_version") == phash
                    and have.get("model") == model and have.get("text")):
                rep["kept"].append(gene)
                if have.get("facts_key"):
                    existing.setdefault(have["facts_key"], have)
                continue
            facts = build_facts(drawer, t.manifest)
            key = facts_key(facts, model)
            wanted.setdefault(key, facts)
            targets.setdefault(key, []).append((run_id, gene, path, drawer))

    try:
        for key in [k for k in wanted if k in existing and not force]:
            for run_id, gene, path, drawer in targets.pop(key):
                drawer["summary"] = dict(existing[key])
                _write_atomic(path, drawer)
                report.runs[run_id]["written"].append(gene)
                log(f"  {run_id} {gene}: summary copied from an identical baked gene")
            del wanted[key]
        if not wanted:
            return report
        items = list(wanted.items())
        log(f"{sum(len(v) for v in targets.values())} gene(s) across {len(run_ids)} "
            f"run(s) need a summary; {len(items)} distinct fact set(s) -> {len(items)} call(s)")

        reservations: dict[str, spend_mod.Reservation] = {}
        to_send: list[tuple[str, dict[str, Any]]] = []
        for key, facts in items:
            if stop_at_usd is not None and ledger.spent(spend_mod.SUMMARY) >= stop_at_usd:
                report.stopped = (f"stopped: ${ledger.spent(spend_mod.SUMMARY):.2f} "
                                  f"already on the summary account, stop_at ${stop_at_usd:.2f}")
                break
            try:
                reservations[key] = ledger.reserve(
                    account=spend_mod.SUMMARY, model=model,
                    input_tokens=_estimate_input_tokens(facts),
                    max_tokens=config.SUMMARY_LLM_MAX_TOKENS,
                    key=f"summary:{targets[key][0][1]}:{key}", prompt_hash=phash)
            except spend_mod.SpendError as e:
                report.stopped = str(e)
                break
            to_send.append((key, facts))
        if report.stopped:
            log(f"  {report.stopped}")
        if not to_send:
            return report

        outcomes = client.complete_many(to_send, max_tokens=config.SUMMARY_LLM_MAX_TOKENS)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for key, facts in to_send:
            out = outcomes.get(key) or SummaryOutcome(ok=False, model=model,
                                                      error="no outcome returned")
            status = (spend_mod.STATUS_OK if out.ok
                      else spend_mod.STATUS_REFUSED_VALIDATION
                      if (out.refused_by_provider or out.text)
                      else spend_mod.STATUS_API_ERROR)
            rec = ledger.commit(reservations[key], input_tokens=out.input_tokens,
                                output_tokens=out.output_tokens,
                                cache_read_tokens=out.cache_read_tokens,
                                status=status, note=out.error or "")
            report.calls += 1
            report.spent_usd += float(rec.get("usd") or 0.0)
            for run_id, gene, path, drawer in targets[key]:
                rep = report.runs[run_id]
                if not out.ok:
                    rep["skipped"].append(f"{gene}: {out.error}")
                    log(f"  {run_id} {gene}: NOT written -- {out.error}")
                    continue
                drawer["summary"] = {
                    "text": out.text,
                    "model": model,
                    "prompt_version": phash,
                    "effort": config.SUMMARY_LLM_EFFORT,
                    "generated_at": generated_at,
                    "inputs": facts,
                    "facts_key": key,
                    "usage": {"input_tokens": out.input_tokens,
                              "output_tokens": out.output_tokens,
                              "cache_read_tokens": out.cache_read_tokens,
                              "usd": rec.get("usd")},
                    "stop_reason": out.stop_reason,
                    "note": SUMMARY_NOTE,
                }
                _write_atomic(path, drawer)
                rep["written"].append(gene)
                log(f"  {run_id} {gene}: summary written")
    finally:
        for run_dir, times in dir_times.items():
            os.utime(run_dir, ns=times)
    return report


SUMMARY_NOTE = (
    "LLM-generated from the values in this drawer and nothing else, when the "
    "summary was baked, not when you opened it. It reads the figure; it is not "
    "a literature review. Its first sentence restates the GO / NO-GO verdict "
    "computed by the owner's rule (recommend.py), which it was given and did "
    "not decide. The exact facts it was given are stored beside it.")


__all__ = ["SYSTEM_PROMPT_HEAD", "system_prompt", "prompt_hash", "build_facts",
           "user_content", "facts_key", "SummaryOutcome", "SummaryLLMClient",
           "AnthropicSummaryClient", "FakeSummaryClient", "bake_summaries",
           "BakeReport", "RING_KEYS", "SUMMARY_NOTE"]
