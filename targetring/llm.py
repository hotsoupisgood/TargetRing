"""The phenotype-page LLM call — the second of the two AGENTS.md rule 8
permits, and the first one this repo has ever actually made.

Three things this module is careful about, each because getting it wrong here
is a different failure mode from getting it wrong anywhere else in the repo:

1. **Rule 10 (nothing public, nothing Puck typed).** The only thing that
   reaches the model is `rows` — the phenotype table's own cells (MP terms,
   model ids, PMIDs, public paper titles, one abstract sentence each) — built
   by `phenotype.py` from `phenotype.summary_rows()`. No DE table, no run id,
   no region, nothing Puck typed ever passes through this module. The system
   prompt says so explicitly, and the manifest's `llm.phenotype` block records
   the same sentence, so the claim is auditable and not just asserted.
2. **No fallback, no silent retry (PLAN §3 stage 7b / §6).** A refusal or an
   API error is reported as what it is, never retried on another model and
   never swallowed into an empty summary. `messages.parse()`'s `output_format`
   guarantees a well-formed `PhenotypeSummary` on success; a `refusal` stop
   reason or a transport error is the caller's `Outcome.ok == False`.
3. **Structured output does the shape, I11 does the substance.** `output_format`
   guarantees the response parses into `PhenotypeSummary` — it cannot make a
   `quote` verbatim or a `row_id` real. That is `invariants.check_phenotype_claims_citable`
   (I11), called by `phenotype.py` on every call's output before anything is
   shown, never here — this module's job stops at "the API answered and here
   is what it said."
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from . import config
from .http import describe as describe_error

# --- the structured-output schema -------------------------------------------


class PhenotypeClaim(BaseModel):
    text: str
    row_ids: list[int]
    quote: str


class PhenotypeSummary(BaseModel):
    claims: list[PhenotypeClaim]


# --- the prompt, pinned so its hash is stable and auditable ------------------

SYSTEM_PROMPT = """You summarise a table of mouse phenotype records for one human gene.

You will be given a JSON array of rows. Each row is one (phenotype, model)
pair MGI curated, and has: row_id (an integer), mp_label (an MGI phenotype
term), model_id (an MGI biological model id), level (one of: asserted,
asserted-negative, none found), and papers (a list).

Each entry in papers has pmid, title (a paper title, or null) and sentence
(one sentence from that paper's abstract naming the gene or its mouse
ortholog, or the string "gene not named in abstract").

The papers on a row are that MODEL's literature. MGI curates which models show
a phenotype and which papers describe each model as two SEPARATE things, and
does not attribute a paper to a particular phenotype. So you must never write
that a paper reported, showed, found or described a phenotype. You may write
that a model is associated with a phenotype, and separately that a paper is
among the literature for that model.

Your ONLY source of information is this table. You have not been given, and
must not reference, any differential-expression data, any run, region, or
anything else — only these rows.

Write a short set of claims, grouped by phenotype class where the rows allow
it, one claim per sentence. Every claim MUST:
  - cite the row_ids (integers from the table) it is based on;
  - carry a "quote" field that is copied VERBATIM (exact substring, same
    case) from the mp_label of a row it cites, or from the title or sentence
    of one of that row's papers.

You must NOT:
  - attribute a phenotype to a paper, or a paper to a phenotype;
  - propose a mechanism for how the gene causes any phenotype;
  - use direction-of-effect language ("increases", "decreases", "causes",
    "leads to") that the rows themselves do not use;
  - write "suggests", "implies", "indicates", or any similar hedge that turns
    an observation into an inference;
  - make any recommendation of any kind;
  - claim anything not directly stated in a row's mp_label or in one of its
    papers' title or sentence.

If the rows support no citable claim at all, return an empty claims list.
Summarise only what the rows say. Do not add outside knowledge."""


def prompt_hash() -> str:
    """Stable across process restarts -- the hash the manifest and the cache
    key both use, over the exact text sent as `system`."""
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def user_content(rows: list[dict[str, Any]]) -> str:
    """Rule 10: the rows, and only the rows, as compact JSON."""
    return json.dumps(rows, sort_keys=True, default=str)


# --- the outcome, shared by every implementation ----------------------------


@dataclass
class LLMOutcome:
    """What a call produced, or why it did not.

    `ok` is the single thing `phenotype.py` branches on. Everything else is
    for the ledger commit and the manifest's `llm.phenotype` block.
    """

    ok: bool
    claims: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None
    error: str | None = None
    refused_by_provider: bool = False


class PhenotypeLLMClient(Protocol):
    """What `phenotype.py` needs from a client. `AnthropicPhenotypeClient` is
    the real one; `tests/test_phenotype.py` and `tests/test_llm.py` use a fake
    that implements the same shape with canned responses, some valid and some
    deliberately broken, so I11 has real negative cases to catch without ever
    spending money."""

    model: str

    def complete(self, rows: list[dict[str, Any]], *, max_tokens: int) -> LLMOutcome:
        ...


class AnthropicPhenotypeClient:
    """The real client. One call, `client.messages.parse()`, structured
    output, low effort (PLAN §3: "this is paraphrase, not reasoning"), no
    `fallbacks` (PLAN §6: no provider fallback, no silent retry on refusal).
    """

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # deferred: keeps `phenotype.py` importable, and this
        # module importable by every test, on a machine with no `anthropic`
        # package installed and no API key set -- only constructing THIS class
        # requires it.
        self.model = model or config.PHENOTYPE_LLM_MODEL
        self._client = anthropic.Anthropic()

    def complete(self, rows: list[dict[str, Any]], *, max_tokens: int) -> LLMOutcome:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                output_config={"effort": config.PHENOTYPE_LLM_EFFORT},
                messages=[{"role": "user", "content": user_content(rows)}],
                output_format=PhenotypeSummary,
            )
        except anthropic.APIError as e:
            return LLMOutcome(ok=False, model=self.model, error=str(e))
        except Exception as e:  # noqa: BLE001 -- e.g. no credentials resolvable
            # anthropic 1.9.0 raises a bare `TypeError` (not `APIError`) when no
            # auth method can be resolved at all -- found live, 2026-09-30,
            # running this stage's own verification with no key set. It must
            # not crash the route: 7a's table is independent of 7b's call and
            # has to render regardless of whether the LLM half can even start.
            return LLMOutcome(ok=False, model=self.model, error=describe_error(e))

        usage = response.usage
        if response.stop_reason == "refusal":
            return LLMOutcome(
                ok=False, model=self.model, stop_reason="refusal",
                refused_by_provider=True,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                error="summary refused by provider",
            )
        parsed = response.parsed_output
        if parsed is None:
            return LLMOutcome(
                ok=False, model=self.model, stop_reason=response.stop_reason,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                error=f"no parsed_output (stop_reason={response.stop_reason!r})",
            )
        return LLMOutcome(
            ok=True, model=self.model, stop_reason=response.stop_reason,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            claims=[c.model_dump() for c in parsed.claims],
        )


class FakeLLMClient:
    """A test double with the same shape as `AnthropicPhenotypeClient`, never
    making a network call. Queue canned `LLMOutcome`s with `queue_outcome`, or
    pass a `claims` list straight to `queue_claims` for the common case — some
    valid, some deliberately broken (a bad `row_id`, a `quote` that does not
    match), so I11 has real negative cases to catch without spending a cent.

    "This project's first paid LLM call" (stage 7's own framing) means every
    test in `tests/test_phenotype.py` and `tests/test_llm.py` uses this class,
    never `AnthropicPhenotypeClient` — the one live call is made exactly once,
    outside the test suite, for the final BRAF verification.
    """

    def __init__(self, model: str = "claude-opus-5") -> None:
        self.model = model
        self._queue: list[LLMOutcome] = []
        self.calls: list[list[dict[str, Any]]] = []

    def queue_outcome(self, outcome: LLMOutcome) -> None:
        self._queue.append(outcome)

    def queue_claims(self, claims: list[dict[str, Any]], *, input_tokens: int = 500,
                     output_tokens: int = 100) -> None:
        self.queue_outcome(LLMOutcome(
            ok=True, claims=claims, input_tokens=input_tokens,
            output_tokens=output_tokens, model=self.model, stop_reason="end_turn"))

    def queue_refusal(self) -> None:
        self.queue_outcome(LLMOutcome(
            ok=False, model=self.model, stop_reason="refusal",
            refused_by_provider=True, input_tokens=500, output_tokens=0,
            error="summary refused by provider"))

    def queue_error(self, message: str = "simulated transport error") -> None:
        self.queue_outcome(LLMOutcome(ok=False, model=self.model, error=message))

    def complete(self, rows: list[dict[str, Any]], *, max_tokens: int) -> LLMOutcome:
        self.calls.append(rows)
        if not self._queue:
            raise AssertionError(
                "FakeLLMClient.complete() called with nothing queued -- a test "
                "must call queue_claims/queue_refusal/queue_error first, so a "
                "call this test did not expect is a loud failure, not a "
                "silent default response")
        return self._queue.pop(0)


__all__ = [
    "AnthropicPhenotypeClient", "FakeLLMClient", "LLMOutcome", "PhenotypeClaim",
    "PhenotypeLLMClient", "PhenotypeSummary", "SYSTEM_PROMPT", "prompt_hash",
    "user_content",
]
