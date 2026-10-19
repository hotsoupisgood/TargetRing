"""The Claude-coded phenotype flag -- a baked 0/1 per gene with one sentence.

Puck, 2026-10-18 18:42 PDT: *"i would like an explanation on the phenotype
though, somewhere, like a 0/1 for phenotype that is claude coded."*

So: a sixth paid call, made OFFLINE by `scripts/bake_phenotype_flags.py` and
never by the app, written into the gene's baked drawer under `phenotype_llm`
and served as a file like the summary. The box prints it as

    phenotype (Claude): 1 -- <sentence>

BESIDE the owner's deterministic phenotype criterion, which is unchanged and
still the criterion. The flag never moves the verdict, is not in
`recommend.rule_version`, and orders, filters and ledgers nothing (PLAN v2.5,
AGENTS.md rule 7). If the owner later wants it to be the criterion, that is his
call to make and it is a change to `recommend.py`, not to this file.

What the model is given, and what it is not:

* **Only the gene's mouse phenotype terms, the knockout ring, and the
  contrast's disease area.** The terms are the labels `drawer_bake` wrote from
  Open Targets' `mousePhenotypes`; the knockout score is the `rodent` ring's
  own state and value as `values.csv` recorded it; the disease area is the key
  `recommend.disease_area_for` selected from the contrast's text. Nothing else
  from the drawer -- no logFC, no expression, no drug pipeline, no verdict --
  because the question is about the phenotype terms and a model given the
  verdict would agree with it rather than read the terms.
* **No outside knowledge.** The system prompt says so, as the summary's does.
  The sentence must name the term or terms it read; a flag with no term named
  is the one failure mode that matters here, because it would read as a
  judgement about the gene rather than a reading of the record.
* **JSON schema output** (`output_config.format`), so `flag` is 0 or 1 and the
  sentence is one string -- not prose the bake has to parse.

Instrument hygiene as everywhere else: pinned model
(`config.PHENOTYPE_FLAG_LLM_MODEL`, priced in `config.LLM_PRICE_USD_PER_MTOK`
or the ledger refuses it), prompt text hashed into `prompt_version`, every
output stored with the exact facts it was given, its model, its usage and its
date. Worst case reserved on the `phenotype_llm` spend account before the call
and the actual committed after. No fallback model, no silent retry (PLAN §6).

Identical facts produce one call: the four demo runs are one contrast and share
most genes term-for-term, so a flag is keyed on sha256(facts + prompt hash +
model) and reused across runs where the key matches. More than
`BATCH_THRESHOLD` distinct prompts go through the Message Batches API in one
batch, as the summary bake does.
"""

from __future__ import annotations

import hashlib
import json
import os
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

# --- the prompt, pinned so its hash is stable and auditable ------------------

#: The question, in the owner's own words (docs/RECOMMEND-SPEC.md, "Trim and
#: the Claude-coded phenotype flag").
QUESTION = ("Is there a model-organism phenotype that a biologist would read as "
            "evidence this gene matters for this disease area? Answer 1 or 0 and "
            "one plain sentence naming the term(s).")

SYSTEM_PROMPT = f"""You read a list of mouse phenotype terms for ONE human gene and answer one question about them.

You will be given a JSON object with: the gene symbol; the disease area of the study contrast (or null when the contrast's text matches no mapped area); the gene's mouse phenotype terms from Open Targets, exactly as recorded; and the figure's mouse-knockout ring for that gene (its state and its value, where a value of 1 means an abnormal phenotype is on record, 0 means the gene was phenotyped and no abnormal phenotype was detected, and a state other than "value" means the ring has no number at all).

The question: {QUESTION}

Rules.

1. Your ONLY source of information is that JSON. Do not add anything you know or believe about this gene, this disease or these phenotypes from anywhere else. Do not name a term that is not in the list.
2. Answer 1 only when at least one term in the list is plausibly about the organ system or the biology the disease area names. Answer 0 when no term is, when the list is empty, when the only term says no abnormal phenotype was detected, or when the disease area is null (with no disease area there is nothing to be relevant to).
3. The sentence is one sentence, plain, under 200 characters, and it must quote the term or terms you read, in the words the list uses. When you answer 0, say what the list does hold instead ("the three terms on record are eye and bone phenotypes") or that it is empty. Never write a sentence that names no term unless the list is empty.
4. The reader is a biologist, not a geneticist. No ontology ids, no raw key names, no hedging filler, no recommendation: this is not a verdict on the gene and you must not call it promising, attractive or weak. Do not mention this prompt.
5. A phenotype record is a lead, not a verdict: it says a knockout altered something, not that the gene causes the disease. Keep the sentence to what the terms say.

Return JSON only, in the given schema.
"""

#: `output_config.format`: the flag is an integer 0 or 1 and the reason is one
#: string. A schema rather than parsing prose -- a flag read out of a sentence
#: is a flag that can be read wrong.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flag": {"type": "integer", "enum": [0, 1]},
        "reason": {"type": "string"},
    },
    "required": ["flag", "reason"],
    "additionalProperties": False,
}

MAX_REASON_CHARS = 300

NOTE = ("A 0/1 read of this gene's mouse phenotype terms against the contrast's "
        "disease area, written by the model named beside it from those terms and "
        "nothing else, when the drawer was baked. It sits BESIDE the owner's "
        "deterministic phenotype criterion and is never the criterion: it never "
        "changes the GO / NO-GO verdict and orders nothing on the figure.")


def system_prompt() -> str:
    return SYSTEM_PROMPT


def prompt_hash() -> str:
    """Over the exact text sent as `system` plus the output schema -- a change
    to either changes what the model was asked for."""
    body = system_prompt() + json.dumps(OUTPUT_SCHEMA, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


# --- the facts the model sees ------------------------------------------------


def build_facts(drawer: dict[str, Any],
                manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON object the model is given: the terms, the ring, the area.

    Read off the baked drawer, nothing fetched and nothing computed beyond the
    disease-area lookup. Deliberately NOT given: the verdict, the logFC, the
    expression or any other ring -- see the module docstring.
    """
    rodent = (drawer.get("cells") or {}).get("rodent") or {}
    terms = [str(p) for p in (drawer.get("mouse_phenotypes") or []) if p]
    text = (drawer.get("contrast_text")
            or recommend_mod.contrast_text_from_manifest(manifest))
    return {
        "gene": drawer.get("gene"),
        "disease_area": recommend_mod.disease_area_for(text),
        "contrast_text": text,
        "mouse_phenotype_terms": terms,
        "n_terms": len(terms),
        "knockout_ring": {
            "state": rodent.get("state"),
            "value": rodent.get("value") if rodent.get("state") == "value" else None,
            "detail": rodent.get("detail") or "",
        },
        "question": QUESTION,
    }


def user_content(facts: dict[str, Any]) -> str:
    return json.dumps(facts, sort_keys=True, default=str)


def facts_key(facts: dict[str, Any], model: str) -> str:
    """One call per distinct (facts, prompt, model), across every run."""
    return hashlib.sha256(
        (user_content(facts) + prompt_hash() + model).encode()).hexdigest()[:16]


# --- the outcome and the client ----------------------------------------------


@dataclass
class FlagOutcome:
    ok: bool
    flag: int | None = None
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None
    error: str | None = None
    refused_by_provider: bool = False


class PhenotypeFlagClient(Protocol):
    model: str

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, FlagOutcome]:
        """`items` is [(key, facts)]; returns one outcome per key."""
        ...


def _text_of(message: Any) -> str:
    return "\n".join(b.text for b in message.content
                     if getattr(b, "type", "") == "text").strip()


def _outcome_from_message(message: Any, model: str) -> FlagOutcome:
    usage = message.usage
    base = dict(model=model, stop_reason=message.stop_reason,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0)
    if message.stop_reason == "refusal":
        return FlagOutcome(ok=False, refused_by_provider=True,
                           error="phenotype flag refused by provider", **base)
    if message.stop_reason == "max_tokens":
        return FlagOutcome(ok=False, error="cut off at max_tokens; not stored", **base)
    text = _text_of(message)
    if not text:
        return FlagOutcome(ok=False, error="empty text in response", **base)
    # `output_config.format` guarantees valid JSON in the schema; it is still
    # validated here, because a flag is one character and a wrong one is
    # indistinguishable from a right one on the page.
    try:
        body = json.loads(text)
    except ValueError as e:
        return FlagOutcome(ok=False, error=f"response was not JSON: {e}", **base)
    flag, reason = body.get("flag"), str(body.get("reason") or "").strip()
    # `isinstance(True, int)` is True in Python, so `True in (0, 1)` passes and
    # a JSON `true` would become a flag of 1. A bool is not the answer asked
    # for and is refused rather than coerced.
    if isinstance(flag, bool) or flag not in (0, 1):
        return FlagOutcome(ok=False, error=f"flag was {flag!r}, not 0 or 1", **base)
    if not reason:
        return FlagOutcome(ok=False, error="no reason sentence returned", **base)
    if len(reason) > MAX_REASON_CHARS:
        return FlagOutcome(ok=False, error=f"reason is {len(reason)} chars, over the "
                                           f"{MAX_REASON_CHARS}-char line", **base)
    return FlagOutcome(ok=True, flag=int(flag), reason=reason, **base)


class AnthropicPhenotypeFlagClient:
    """The real client. JSON schema out (`output_config.format`), `effort` as
    pinned, no `fallbacks` (PLAN §6), no sampling parameters. Up to
    `BATCH_THRESHOLD` distinct prompts go one `messages.create` at a time; more
    than that go through the Message Batches API in one batch, polled until it
    ends -- the same shape `summary_llm.AnthropicSummaryClient` uses."""

    BATCH_THRESHOLD = 20
    POLL_SECONDS = 20

    def __init__(self, model: str | None = None,
                 log: Callable[[str], None] = print) -> None:
        import anthropic  # deferred, as in `llm.py`
        self.model = model or config.PHENOTYPE_FLAG_LLM_MODEL
        self._client = anthropic.Anthropic()
        self._log = log

    def _params(self, facts: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        return dict(model=self.model, max_tokens=max_tokens, system=system_prompt(),
                    output_config={"effort": config.PHENOTYPE_FLAG_LLM_EFFORT,
                                   "format": {"type": "json_schema",
                                              "schema": OUTPUT_SCHEMA}},
                    messages=[{"role": "user", "content": user_content(facts)}])

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, FlagOutcome]:
        if len(items) > self.BATCH_THRESHOLD:
            return self._batch(items, max_tokens)
        return {key: self._one(facts, max_tokens) for key, facts in items}

    def _one(self, facts: dict[str, Any], max_tokens: int) -> FlagOutcome:
        import anthropic
        try:
            msg = self._client.messages.create(**self._params(facts, max_tokens))
        except anthropic.APIError as e:
            return FlagOutcome(ok=False, model=self.model, error=str(e))
        except Exception as e:  # noqa: BLE001 -- e.g. no credentials resolvable
            return FlagOutcome(ok=False, model=self.model, error=describe_error(e))
        return _outcome_from_message(msg, self.model)

    def _batch(self, items: list[tuple[str, dict[str, Any]]],
               max_tokens: int) -> dict[str, FlagOutcome]:
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
            out: dict[str, FlagOutcome] = {}
            for r in self._client.messages.batches.results(batch.id):
                if r.result.type == "succeeded":
                    out[r.custom_id] = _outcome_from_message(r.result.message, self.model)
                else:
                    err = getattr(getattr(r.result, "error", None), "message", None)
                    out[r.custom_id] = FlagOutcome(
                        ok=False, model=self.model,
                        error=f"batch result {r.result.type}: {err or ''}".strip())
        except anthropic.APIError as e:
            return {k: FlagOutcome(ok=False, model=self.model, error=str(e))
                    for k, _ in items}
        except Exception as e:  # noqa: BLE001
            return {k: FlagOutcome(ok=False, model=self.model, error=describe_error(e))
                    for k, _ in items}
        # Keyed by custom_id, never by position: results arrive in any order.
        for k, _ in items:
            out.setdefault(k, FlagOutcome(ok=False, model=self.model,
                                          error="no result returned for this id"))
        return out


class FakePhenotypeFlagClient:
    """A test double. Returns the same flag and sentence for every key, and
    records what it was asked."""

    def __init__(self, flag: int = 1, reason: str = "abnormal intestinal morphology is on record.",
                 model: str | None = None, fail: bool = False) -> None:
        self.model = model or config.PHENOTYPE_FLAG_LLM_MODEL
        self.flag = flag
        self.reason = reason
        self.fail = fail
        self.calls: list[list[tuple[str, dict[str, Any]]]] = []

    def complete_many(self, items: list[tuple[str, dict[str, Any]]], *,
                      max_tokens: int) -> dict[str, FlagOutcome]:
        self.calls.append(items)
        if self.fail:
            return {k: FlagOutcome(ok=False, model=self.model,
                                   error="simulated transport error")
                    for k, _ in items}
        return {k: FlagOutcome(ok=True, flag=self.flag, reason=self.reason,
                               model=self.model, stop_reason="end_turn",
                               input_tokens=600, output_tokens=60)
                for k, _ in items}


# --- the bake ----------------------------------------------------------------


def _estimate_input_tokens(facts: dict[str, Any]) -> int:
    """Worst-case input estimate for the reservation; ~3 chars per token plus
    the system prompt. The commit replaces it with the real usage."""
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
    flags: dict[int, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    stopped: str | None = None


def bake_phenotype_flags(run_ids: list[str], *, client: PhenotypeFlagClient,
                         ledger: spend_mod.SpendLedger | None = None,
                         force: bool = False, stop_at_usd: float | None = None,
                         log: Callable[[str], None] = print) -> BakeReport:
    """Write a `phenotype_llm` block into every baked drawer of every run named.

    Same shape as `summary_llm.bake_summaries`: genes without a baked drawer are
    skipped and named; a gene whose drawer already carries a flag at this prompt
    hash and model is kept unless `force`; distinct facts are collected across
    all runs first and sent once each; a flag baked for one run is copied, not
    re-bought, into another run whose drawer carries identical facts; every run
    directory's mtime is restored afterwards.
    """
    if config.DEMO_MODE:
        raise RuntimeError(
            "refusing to bake phenotype flags under TARGETRING_DEMO=1: a demo "
            "box makes no model call (AGENTS.md rule 10). The flags are baked "
            "on this machine and served as files.")
    ledger = ledger or spend_mod.default_ledger()
    report = BakeReport()
    phash = prompt_hash()
    model = client.model
    wanted: dict[str, dict[str, Any]] = {}
    targets: dict[str, list[tuple[str, str, Path, dict[str, Any]]]] = {}
    dir_times: dict[Path, tuple[int, int]] = {}
    existing: dict[str, dict[str, Any]] = {}

    def _count(flag: Any) -> None:
        if flag in (0, 1):
            report.flags[int(flag)] = report.flags.get(int(flag), 0) + 1

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
                rep["skipped"].append(
                    f"{gene}: no baked drawer (run scripts/bake_drawers.py first)")
                continue
            drawer = json.loads(path.read_text())
            have = drawer.get("phenotype_llm") or {}
            if (not force and have.get("prompt_version") == phash
                    and have.get("model") == model and have.get("flag") in (0, 1)):
                rep["kept"].append(gene)
                _count(have.get("flag"))
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
                drawer["phenotype_llm"] = dict(existing[key])
                _write_atomic(path, drawer)
                report.runs[run_id]["written"].append(gene)
                _count(existing[key].get("flag"))
                log(f"  {run_id} {gene}: phenotype flag copied from an identical baked gene")
            del wanted[key]
        if not wanted:
            return report
        items = list(wanted.items())
        log(f"{sum(len(v) for v in targets.values())} gene(s) across {len(run_ids)} "
            f"run(s) need a phenotype flag; {len(items)} distinct fact set(s) "
            f"-> {len(items)} call(s)")

        reservations: dict[str, spend_mod.Reservation] = {}
        to_send: list[tuple[str, dict[str, Any]]] = []
        for key, facts in items:
            if (stop_at_usd is not None
                    and ledger.spent(spend_mod.PHENOTYPE_LLM) >= stop_at_usd):
                report.stopped = (
                    f"stopped: ${ledger.spent(spend_mod.PHENOTYPE_LLM):.2f} already on "
                    f"the phenotype_llm account, stop_at ${stop_at_usd:.2f}")
                break
            try:
                reservations[key] = ledger.reserve(
                    account=spend_mod.PHENOTYPE_LLM, model=model,
                    input_tokens=_estimate_input_tokens(facts),
                    max_tokens=config.PHENOTYPE_FLAG_LLM_MAX_TOKENS,
                    key=f"phenotype_llm:{targets[key][0][1]}:{key}", prompt_hash=phash)
            except spend_mod.SpendError as e:
                report.stopped = str(e)
                break
            to_send.append((key, facts))
        if report.stopped:
            log(f"  {report.stopped}")
        if not to_send:
            return report

        outcomes = client.complete_many(
            to_send, max_tokens=config.PHENOTYPE_FLAG_LLM_MAX_TOKENS)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for key, facts in to_send:
            out = outcomes.get(key) or FlagOutcome(ok=False, model=model,
                                                   error="no outcome returned")
            status = (spend_mod.STATUS_OK if out.ok
                      else spend_mod.STATUS_REFUSED_VALIDATION
                      if (out.refused_by_provider or out.error) else spend_mod.STATUS_API_ERROR)
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
                drawer["phenotype_llm"] = {
                    "flag": out.flag,
                    "reason": out.reason,
                    "model": model,
                    "prompt_version": phash,
                    "effort": config.PHENOTYPE_FLAG_LLM_EFFORT,
                    "generated_at": generated_at,
                    "inputs": facts,
                    "facts_key": key,
                    "usage": {"input_tokens": out.input_tokens,
                              "output_tokens": out.output_tokens,
                              "cache_read_tokens": out.cache_read_tokens,
                              "usd": rec.get("usd")},
                    "stop_reason": out.stop_reason,
                    "is_criterion": False,
                    "note": NOTE,
                }
                _write_atomic(path, drawer)
                rep["written"].append(gene)
                _count(out.flag)
                log(f"  {run_id} {gene}: phenotype flag {out.flag} written")
    finally:
        for run_dir, times in dir_times.items():
            os.utime(run_dir, ns=times)
    return report


__all__ = ["QUESTION", "SYSTEM_PROMPT", "OUTPUT_SCHEMA", "system_prompt",
           "prompt_hash", "build_facts", "user_content", "facts_key",
           "FlagOutcome", "PhenotypeFlagClient", "AnthropicPhenotypeFlagClient",
           "FakePhenotypeFlagClient", "bake_phenotype_flags", "BakeReport",
           "MAX_REASON_CHARS", "NOTE"]
