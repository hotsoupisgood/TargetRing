"""Which GTEx tissue to read the specificity ring against — inferred, and cached.

Puck, 2026-10-09: *"the target location on the body should also be handled by an
llm call by simply using general knowledge on where the disease is located. we
cache the results so less api calls are used in the future."* Rule 8's fourth
paid call, against the `region` account's own cap and counter.

WHAT THIS DECIDES, AND HOW MUCH IT MATTERS
------------------------------------------
Exactly one ring: `regional_specificity`, the region's median TPM divided by the
gene's median across the whole GTEx panel. Nothing else on the figure moves. So
a wrong region makes one ring of twelve misleading — bad, and bounded, and the
figure names the region it used in several places.

It is still a real claim. "Enriched in ileum" and "enriched in whole blood" are
different statements about a gene, and a reader who did not choose the tissue
will not notice it was chosen for them unless it is said. The answer therefore
carries its reasoning and its provenance everywhere the pick does.

THE MODEL CHOOSES FROM A CLOSED LIST
------------------------------------
The candidate regions are `run.available_regions()` — the GTEx panel this build
can actually read, fetched from GTEx itself. The model picks one of those
strings; anything else is refused and the caller falls back to the default
region. It cannot name a tissue GTEx does not have, and it cannot invent an
anatomy: the same closed-set guard `chooser_llm` applies to accessions and
`/api/analyse` applies to gene ids.

This is also the whole reason general knowledge is acceptable here. The model is
not being asked for a fact that must be true — it is being asked to map a
disease onto one of ~54 tissue names, a question where its training is a
reasonable source and where every possible answer is one the app already offers.

CACHED, AND THE CACHE SAYS SO
-----------------------------
Keyed on the disease id, the prompt hash and a hash of the region list. All
three belong in the key: a reworded prompt or a changed GTEx panel can change
the right answer, and a cache that ignored them would serve an answer to a
question nobody asked any more.

A cached answer is returned with `cached=True` and the date it was first made.
Presenting a remembered answer as a fresh one would be a small plausible wrong
figure of exactly the kind this repo spends its effort on — and it matters here
because the model, the prompt and the panel are all recorded in the manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel

from . import config
from .http import describe as describe_error

SYSTEM_PROMPT = """You are given a disease name and a list of human tissue names.

Choose the one tissue from the list where that disease principally acts — the \
tissue a researcher would look at to study it. Use ordinary medical knowledge.

Prefer the affected organ over blood, unless the disease is primarily \
haematological or systemic-inflammatory, in which case blood is the right answer.

Return the tissue name copied verbatim from the list, and one sentence of at \
most 30 words saying why. Keep it to one sentence: a longer answer is truncated \
and then nothing is returned at all.

If no tissue in the list is a reasonable site for this disease, return an empty \
tissue and say why."""


class RegionChoice(BaseModel):
    """One tissue, one sentence. Nothing rankable, no second choice."""

    tissue: str
    reason: str


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def cache_key(disease: str, regions: list[str]) -> str:
    """Disease + prompt + panel. All three can change the right answer."""
    panel = hashlib.sha256("\n".join(sorted(regions)).encode()).hexdigest()[:12]
    return f"{disease}:{prompt_hash()}:{panel}"


def user_content(disease: str, regions: list[str]) -> str:
    """Rule 10: a public disease label and a public tissue list. Nothing else."""
    return json.dumps({"disease": disease, "tissues": sorted(regions)},
                      sort_keys=True)


@dataclass
class RegionOutcome:
    ok: bool
    tissue: str = ""
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    error: str | None = None


@dataclass
class RegionResult:
    """What the caller gets. `tissue` empty means no region was chosen and the
    caller should keep its default — stated, never silently substituted."""

    tissue: str
    reason: str
    cached: bool = False
    chosen_utc: str = ""
    model: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"tissue": self.tissue, "reason": self.reason,
                "cached": self.cached, "chosen_utc": self.chosen_utc,
                "model": self.model, "error": self.error}


class RegionLLMClient(Protocol):
    model: str

    def complete(self, disease: str, regions: list[str], *,
                 max_tokens: int) -> RegionOutcome:
        ...


class AnthropicRegionClient:
    """The real client. One call, structured output, no provider fallback."""

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # deferred: this module must import with no key
        self.model = model or config.PHENOTYPE_LLM_MODEL
        self._client = anthropic.Anthropic()

    def complete(self, disease: str, regions: list[str], *,
                 max_tokens: int) -> RegionOutcome:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self.model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
                messages=[{"role": "user",
                           "content": user_content(disease, regions)}],
                output_format=RegionChoice,
            )
        except anthropic.APIError as e:
            return RegionOutcome(ok=False, model=self.model, error=str(e))
        except Exception as e:  # noqa: BLE001 -- unresolvable credentials, and
            # a truncated response, which `messages.parse` raises as a pydantic
            # ValidationError. `chooser_llm` hit that live on 2026-10-09 at a
            # too-small budget; the prompt here bounds the sentence for the
            # same reason.
            return RegionOutcome(ok=False, model=self.model,
                                 error=describe_error(e))

        usage = response.usage
        parsed = response.parsed_output
        if parsed is None:
            return RegionOutcome(
                ok=False, model=self.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                error=f"no parsed_output (stop_reason={response.stop_reason!r})")
        return RegionOutcome(
            ok=True, model=self.model, input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            tissue=parsed.tissue.strip(), reason=parsed.reason.strip())


@dataclass
class RegionChooser:
    """Infer a GTEx region for a disease, from a closed list, cached.

    Returns a `RegionResult` whose `tissue` is `""` on every failure path. The
    caller keeps its own default in that case and says so; this never
    substitutes one silently.
    """

    client: RegionLLMClient | None = None
    cache: Any = None
    ledger: Any = None
    max_tokens: int = 500

    def choose(self, disease: str, regions: list[str]) -> RegionResult:
        if not disease or not regions:
            return RegionResult("", "no disease or no region list", error="input")

        key = cache_key(disease, regions)
        if self.cache is not None:
            hit = self.cache.get("region_llm", key)
            if hit:
                # Returned as REMEMBERED, not as fresh. The model and prompt are
                # recorded in the manifest, so a cached answer presented as a new
                # one would misdate a claim the artefact is audited against.
                return RegionResult(
                    tissue=hit.get("tissue", ""), reason=hit.get("reason", ""),
                    cached=True, chosen_utc=hit.get("chosen_utc", ""),
                    model=hit.get("model", ""))

        if self.client is None:
            return RegionResult("", "no region chooser is configured",
                                error="no client")

        reservation = None
        if self.ledger is not None:
            from . import spend as spend_mod
            try:
                reservation = self.ledger.reserve(
                    account=spend_mod.REGION, model=self.client.model,
                    input_tokens=len(SYSTEM_PROMPT) // 3
                    + len(user_content(disease, regions)) // 3 + 200,
                    max_tokens=self.max_tokens, key=f"region:{disease}",
                    prompt_hash=prompt_hash())
            except Exception as e:  # noqa: BLE001 -- CapExceeded and friends
                return RegionResult("", f"the region chooser did not run — {e}",
                                    error="cap")

        out = self.client.complete(disease, regions, max_tokens=self.max_tokens)
        if self.ledger is not None and reservation is not None:
            self.ledger.commit(reservation, input_tokens=out.input_tokens,
                               output_tokens=out.output_tokens)

        if not out.ok:
            return RegionResult("", f"the region chooser did not run — "
                                    f"{out.error}", error="call")
        if not out.tissue:
            return RegionResult("", f"no tissue in the GTEx panel suits this "
                                    f"disease: {out.reason}", error="declined")
        if out.tissue not in set(regions):
            # The closed-set guard. A model naming a tissue GTEx does not have
            # would send `specificity_cell` looking for a key that is not there.
            return RegionResult(
                "", f"the region chooser returned {out.tissue!r}, which is not "
                    f"one of the {len(regions)} GTEx tissues it was given",
                error="outside list")

        result = RegionResult(tissue=out.tissue, reason=out.reason, cached=False,
                              chosen_utc=datetime.now(timezone.utc)
                              .isoformat(timespec="seconds"),
                              model=out.model)
        if self.cache is not None:
            self.cache.put("region_llm", key, {
                "tissue": result.tissue, "reason": result.reason,
                "chosen_utc": result.chosen_utc, "model": result.model,
                "disease": disease})
        return result


__all__ = ["RegionChooser", "AnthropicRegionClient", "RegionLLMClient",
           "RegionChoice", "RegionOutcome", "RegionResult", "SYSTEM_PROMPT",
           "prompt_hash", "cache_key", "user_content"]
