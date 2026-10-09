"""The model-backed contrast chooser — rule 8's third paid call.

Permitted by Puck on 2026-10-09 ("there is no cap. i just want to ok
increases"), recorded in `AGENTS.md` rule 8 and
`docs/DECISION-AUTOPICK-2026-10-09.md`. One call per disease, against the
`chooser` account's own cap and counter.

WHAT THE MODEL IS FOR, AND WHAT IT IS NOT FOR
---------------------------------------------
`autopick.RuleChooser` already picks, deterministically and for free, by the
most samples on the smaller side. It is a defensible rule and it is also a
thin one: it knows nothing about whether the tissue is the one the question is
about, whether the control is a healthy volunteer or a symptomatic non-case,
or whether an experiment's design suits a case/control read at all. Those are
the judgements a reader makes in the seconds they spend scanning the table,
and they are what this call is for.

It chooses among contrasts **the index already offered it**. It does not
search, does not name a dataset from memory, and cannot return an accession
nobody handed it — `autopick.Pick` raises if it tries, the same guard
`/api/analyse` applies to gene ids. A model that could name its own accession
would be a model inventing a dataset, and this build's cardinal rule prices
that above a crash.

IT CHOOSES; IT DOES NOT SCORE
-----------------------------
The output is one contrast key and one sentence of reasoning. There is
deliberately no per-candidate score, no ranking of the others and no numeric
confidence: a scored list of contrasts is the machinery rule 7 removed,
rebuilt one level upstream, and it would also be the thing a reader starts
sorting by. The runners-up are already visible in the audit record as
`considered`, unranked.

NO FALLBACK INSIDE THE CALL, AND AN HONEST ONE OUTSIDE IT
----------------------------------------------------------
`llm.py`'s rule holds: no provider fallback, no silent retry, a refusal
reported as a refusal. But a failed chooser must not take the page with it, so
`choose()` returns `(None, reason)` on any failure and `autopick.pick` falls
back to the deterministic chooser.

That fallback is honest only because `Pick.chooser` records who actually
chose. The surface prints that name and the stated rule, so a reader is never
told a model chose when the rule did. Getting this backwards — falling back
silently under the model's name — would be a plausible wrong figure about the
figure's own provenance.

Rule 10: what reaches the model is public Expression Atlas metadata —
accessions, contrast ids, group labels, assay counts, tissue. Nothing Puck
typed, no run id, no region, no gene.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from . import config
from .autopick import Candidate
from .http import describe as describe_error

SYSTEM_PROMPT = """You choose one differential-expression contrast from a list.

The list is from EBI Expression Atlas. Every row compares a test group against \
a reference group in one experiment, and every row in the list has already been \
checked to be a comparison of affected individuals against unaffected ones.

Choose the row that best supports asking "which genes differ in this disease" \
for in vitro target work. Weigh: whether the tissue is one the disease acts in, \
whether the reference group is genuinely unaffected rather than symptomatic or \
adjacent-to-lesion tissue, and whether the group sizes support a differential \
estimate at all.

Return the `key` of exactly one row from the list, copied verbatim, and one \
sentence saying what decided it. Name the tissue or the control in that \
sentence where it mattered.

Do not rank the rows. Do not score them. Do not return a row that is not in \
the list. If no row is a reasonable case/control comparison for this disease, \
return an empty key and say why."""


class ContrastChoice(BaseModel):
    """The structured output. One key, one sentence, nothing rankable."""

    key: str
    reason: str


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def user_content(cands: list[Candidate]) -> str:
    """Rule 10: public Atlas metadata, and only that."""
    return json.dumps([
        {"key": c.key, "test": c.test_label, "n_test": c.n_test,
         "control": c.ref_label, "n_control": c.n_ref,
         "tissue": list(c.tissue)}
        for c in cands], sort_keys=True, default=str)


@dataclass
class ChoiceOutcome:
    ok: bool
    key: str = ""
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None
    error: str | None = None


class ContrastLLMClient(Protocol):
    model: str

    def complete(self, cands: list[Candidate], *, max_tokens: int) -> ChoiceOutcome:
        ...


class AnthropicContrastClient:
    """The real client. One call, structured output, no provider fallback."""

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # deferred, like `llm.py`: this module must import on
        # a machine with no `anthropic` package and no key.
        self.model = model or config.PHENOTYPE_LLM_MODEL
        self._client = anthropic.Anthropic()

    def complete(self, cands: list[Candidate], *, max_tokens: int) -> ChoiceOutcome:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self.model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content(cands)}],
                output_format=ContrastChoice,
            )
        except anthropic.APIError as e:
            return ChoiceOutcome(ok=False, model=self.model, error=str(e))
        except Exception as e:  # noqa: BLE001 -- no resolvable credentials
            # `llm.py` found this live on 2026-09-30: anthropic raises a bare
            # TypeError when no auth method resolves. It must not crash the
            # picker, which works perfectly well without a chooser.
            return ChoiceOutcome(ok=False, model=self.model,
                                 error=describe_error(e))

        usage = response.usage
        if response.stop_reason == "refusal":
            return ChoiceOutcome(
                ok=False, model=self.model, stop_reason="refusal",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                error="choice refused by provider")
        parsed = response.parsed_output
        if parsed is None:
            return ChoiceOutcome(
                ok=False, model=self.model, stop_reason=response.stop_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                error=f"no parsed_output (stop_reason={response.stop_reason!r})")
        return ChoiceOutcome(
            ok=True, model=self.model, stop_reason=response.stop_reason,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            key=parsed.key.strip(), reason=parsed.reason.strip())


@dataclass
class ModelChooser:
    """An `autopick.Chooser` backed by a model, with the spend gate in front.

    Returns `(None, reason)` on every failure path — cap, transport, refusal,
    or a key that is not in the candidate list — so `autopick.pick` can fall
    back to the deterministic chooser and record THAT as the chooser.
    """

    client: ContrastLLMClient
    ledger: Any = None
    max_tokens: int = 400
    name: str = "model"
    stated_rule: str = (
        "a model chose among the contrasts this app can draw that compare "
        "affected against unaffected, weighing tissue, what the control group "
        "actually is, and whether the group sizes support a differential "
        "estimate; it ranked nothing and chose only from the list it was given")

    def choose(self, cands: list[Candidate]) -> tuple[Candidate | None, str]:
        if not cands:
            return None, ("no contrast for this disease is both drawable and a "
                          "comparison of affected against unaffected")

        reservation = None
        if self.ledger is not None:
            from . import spend as spend_mod
            try:
                reservation = self.ledger.reserve(
                    account=spend_mod.CHOOSER, model=self.client.model,
                    input_tokens=_estimate_tokens(cands),
                    max_tokens=self.max_tokens,
                    key=f"chooser:{len(cands)}", prompt_hash=prompt_hash())
            except Exception as e:  # noqa: BLE001 -- CapExceeded and friends
                # Rule 8 as amended: a cap is an approval gate. Say the number
                # rather than failing mute, so the next step is obvious.
                return None, f"the chooser did not run — {e}"

        out = self.client.complete(cands, max_tokens=self.max_tokens)

        if self.ledger is not None and reservation is not None:
            self.ledger.commit(reservation, input_tokens=out.input_tokens,
                               output_tokens=out.output_tokens)
        if not out.ok:
            return None, f"the chooser did not run — {out.error}"

        by_key = {c.key: c for c in cands}
        if out.key not in by_key:
            # Not an exception: a model naming something outside its list is a
            # foreseeable output, and the deterministic chooser is right there.
            return None, (f"the chooser returned {out.key!r}, which is not one "
                          f"of the {len(cands)} contrasts it was given")
        return by_key[out.key], out.reason


def _estimate_tokens(cands: list[Candidate]) -> int:
    """Worst case for the reservation. Deliberately generous: an under-estimate
    reserves too little and lets a call cross the cap it was checked against."""
    return len(SYSTEM_PROMPT) // 3 + len(user_content(cands)) // 3 + 200


__all__ = ["ModelChooser", "AnthropicContrastClient", "ContrastLLMClient",
           "ContrastChoice", "ChoiceOutcome", "SYSTEM_PROMPT", "prompt_hash",
           "user_content"]
