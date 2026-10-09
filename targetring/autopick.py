"""Choosing a contrast autonomously — the machinery, and what it must carry.

Puck, 2026-10-09: *"Model picks a public dataset, ideally from peer reviewed
research or a open database. but there is some decision being made
autonomously."* That is a deliberate reversal of a refusal this build made on
the record, and `docs/DECISION-AUTOPICK-2026-10-09.md` is where it is recorded.
Read that before changing anything here.

WHAT IS AND IS NOT REVERSED
---------------------------
`atlas_index.picker_payload` says: *"There is no `selected` or `recommended`
field and there will not be one ... a default pick in a payload is a
recommendation whatever the UI calls it (rule 7, COUNCIL V10 by analogy)."*
That refusal is about **the listing**: 138 rows arriving with one of them
already blessed, which a reader cannot help but read as the app's opinion.

It still holds, and `picker_payload` is untouched. What this module adds is a
**separate, explicitly invoked action**: you ask for a pick, and the answer
arrives with the rule that produced it, the candidates it chose among, and
every row it excluded with a reason. Nobody is handed a recommendation they did
not ask for, and nobody gets a choice they cannot audit.

WHY THE AUDIT TRAIL IS THE FEATURE
----------------------------------
A wrong contrast produces a complete, internally consistent, entirely wrong
figure — `AGENTS.md:17`'s defect with every invariant passing, because nothing
downstream can tell that the gene set came from the wrong experiment. Picking
by hand, a reader at least saw the 17 rows they were choosing between. Picking
automatically removes that, so the pick has to carry what the reader would have
seen: how many candidates there were, why the others lost, and what the control
side of the winner actually is.

That is why `Pick` carries `considered` and `excluded` rather than just a
winner, and why `stated_rule` is a sentence rather than a function name.

THE CHOOSER IS PLUGGABLE, AND THE FIRST ONE IS FREE
---------------------------------------------------
`Chooser` is a protocol over an already-filtered candidate list. `RuleChooser`
is deterministic, costs nothing, and is both the default and the fallback.
`chooser_llm.ModelChooser` is the same interface backed by a model — rule 8's
third paid call, permitted by Puck on 2026-10-09 and metered against the
`chooser` account's own cap.

When a chooser declines — over its cap, a transport error, a key outside the
list — `pick()` falls back to `RuleChooser` and records **the rule chooser** as
`chooser`, with the original named in `fell_back_from`. The attribution is what
makes the fallback honest: the surface prints who actually chose.

Whatever chooses, the result is verified the same way: the chosen id must be one
of the candidates it was given (`Pick.__post_init__`). That is the same guard
`/api/analyse` already applies to gene ids with `selection_outside_top_n`, and
it is what stops a chooser — rule-based or model-based — returning an accession
nobody offered it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .atlas_index import COMPARISON_LABELS

#: Why a row is not eligible to be chosen automatically. Each is a statement
#: about the row, not a judgement of the experiment behind it.
EXCLUSION_REASONS: dict[str, str] = {
    "not_drawable": ("this app does not read this contrast's format — "
                     "microarray, a settled decision of 2026-10-02 — or its "
                     "contrast id does not establish the sign"),
    "not_case_control": ("both sides carry the same disease annotation, or "
                         "neither side is annotated as diseased, so this is "
                         "not a comparison of affected against unaffected"),
}


@dataclass(frozen=True)
class Candidate:
    """One contrast the chooser may pick, reduced to what a choice turns on."""

    accession: str
    contrast_id: str
    n_ref: int
    n_test: int
    min_n: int
    ref_label: str
    test_label: str
    tissue: tuple[str, ...]
    comparison: str
    flag_keys: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.accession} {self.contrast_id}"

    def as_dict(self) -> dict[str, Any]:
        return {"accession": self.accession, "contrast_id": self.contrast_id,
                "key": self.key, "n_ref": self.n_ref, "n_test": self.n_test,
                "min_n": self.min_n, "ref_label": self.ref_label,
                "test_label": self.test_label, "tissue": list(self.tissue),
                "comparison": self.comparison,
                "comparison_label": COMPARISON_LABELS.get(self.comparison, ""),
                "flag_keys": list(self.flag_keys)}


@dataclass(frozen=True)
class Pick:
    """A chosen contrast, and everything needed to disagree with the choice.

    `chosen` is `None` when nothing was eligible. That is a refusal and is
    reported as one — an empty candidate list must never resolve to "the least
    bad row", which is how an automatic picker starts drawing figures from
    contrasts a human would have rejected on sight.
    """

    chooser: str
    stated_rule: str
    chosen: Candidate | None
    reason: str
    considered: tuple[Candidate, ...]
    excluded: tuple[tuple[Candidate, str], ...] = ()
    #: Set when a chooser declined and `RuleChooser` stood in for it. Carried,
    #: not swallowed: `chooser` above names who ACTUALLY chose, and this says
    #: who was asked first and why they did not. A reader must never be told a
    #: model chose when the rule did.
    fell_back_from: str = ""
    #: Set when the control side is annotated but is not a healthy one. Carried
    #: because it changes what every log2FC on the figure means, and because it
    #: is the single thing a reader is most likely to assume rather than check.
    control_caveat: str = ""

    def __post_init__(self) -> None:
        if self.chosen is None:
            return
        keys = {c.key for c in self.considered}
        if self.chosen.key not in keys:
            # The same guard `/api/analyse` applies to gene ids. A chooser that
            # can return something outside the set it was handed is a chooser
            # that can invent an accession.
            raise ValueError(
                f"{self.chooser} chose {self.chosen.key}, which is not one of "
                f"the {len(keys)} candidate(s) it was given")

    def as_dict(self) -> dict[str, Any]:
        return {
            "chooser": self.chooser,
            "stated_rule": self.stated_rule,
            "chosen": self.chosen.as_dict() if self.chosen else None,
            "reason": self.reason,
            "control_caveat": self.control_caveat,
            "fell_back_from": self.fell_back_from,
            "n_considered": len(self.considered),
            "considered": [c.as_dict() for c in self.considered],
            "excluded": [{**c.as_dict(), "excluded_because": why,
                          "excluded_reason": EXCLUSION_REASONS.get(why, why)}
                         for c, why in self.excluded],
        }


def candidates_from_rows(rows: list[dict[str, Any]]
                         ) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Split `picker_payload`'s rows into what may be chosen and what may not.

    Two filters, both of them already-settled facts rather than new judgements:
    the contrast must be drawable (RNA-seq with an established sign — the
    2026-10-02 decision), and it must compare affected against unaffected
    (`atlas_index.comparison_kind`, from the SDRF disease ids).

    Everything excluded is returned with its reason rather than dropped. An
    automatic picker that silently considered 17 of 138 rows would be making the
    most consequential part of the choice invisible.
    """
    ok: list[Candidate] = []
    out: list[tuple[Candidate, str]] = []
    for r in rows:
        c = Candidate(
            accession=r["accession"], contrast_id=r["contrast_id"],
            n_ref=int(r["n_ref"]), n_test=int(r["n_test"]),
            min_n=int(r["min_n"]), ref_label=r.get("ref_label") or "",
            test_label=r.get("test_label") or "",
            tissue=tuple(r.get("tissue") or ()),
            comparison=r.get("comparison") or "undetermined",
            flag_keys=tuple(r.get("flag_keys") or ()))
        if not r.get("drawable"):
            out.append((c, "not_drawable"))
        elif c.comparison != "case_vs_unaffected":
            out.append((c, "not_case_control"))
        else:
            ok.append(c)
    return ok, out


#: Control labels that are annotated as unaffected but are not healthy people.
#: Stated rather than scored: each changes what a log2FC means and none of them
#: makes a contrast unusable.
_CONTROL_HINTS: tuple[tuple[str, str], ...] = (
    ("non inflammatory bowel disease", "the control side is people investigated "
     "for this disease who turned out not to have it, not healthy volunteers — "
     "shared symptomatic signal is subtracted out"),
    ("adjacent", "the control side is tissue taken near the lesion, from the "
     "same affected individuals"),
    ("not applicable", "the control side's condition is annotated "
     "'not applicable', so what it controls for is unstated"),
)


def control_caveat(c: Candidate) -> str:
    """What the control side actually is, when it is not simply healthy.

    "Control" in Expression Atlas is frequently not a healthy volunteer, and
    which kind it is changes the meaning of every number on the figure. A
    reader choosing by hand reads the label; a reader handed an automatic pick
    does not, unless it is said.
    """
    low = c.ref_label.lower()
    for needle, sentence in _CONTROL_HINTS:
        if needle in low:
            return sentence
    return ""


class Chooser(Protocol):
    """Picks one candidate, or none, and says why in a sentence."""

    name: str
    stated_rule: str

    def choose(self, cands: list[Candidate]) -> tuple[Candidate | None, str]:
        ...


@dataclass
class RuleChooser:
    """The deterministic chooser: most samples on the smaller side.

    One stated rule, applied to an already-filtered set, with a deterministic
    tie-break so the same index always produces the same pick. It is not a
    quality judgement about the experiments and does not combine anything:
    `min(n_ref, n_test)` is one number from one source, and it is the number
    that decides whether a contrast can support a dispersion estimate at all
    (`contrast.SMALL_N`, and the small-n caveat the figure already carries).

    No weights, no composite, nothing summed across sources. A chooser that
    scored contrasts on several axes would be the machinery rule 7 removed,
    rebuilt one level upstream.
    """

    name: str = "largest-smaller-side"
    stated_rule: str = (
        "among contrasts this app can draw that compare affected against "
        "unaffected, the one with the most samples on its smaller side; ties "
        "broken by accession then contrast id")

    def choose(self, cands: list[Candidate]) -> tuple[Candidate | None, str]:
        if not cands:
            return None, ("no contrast for this disease is both drawable and a "
                          "comparison of affected against unaffected")
        # Ties broken by the LOWEST accession then contrast id, which is what
        # "ties broken by accession then contrast id" means in the stated rule.
        # `max` over a tuple took the highest, i.e. reverse-alphabetical, so the
        # rule said one thing and did another.
        best = min(cands, key=lambda c: (-c.min_n, c.accession, c.contrast_id))
        others = [c for c in cands if c.key != best.key]
        # The next best is the best of the REST, which on a tie is the same
        # number — "the most of 3; the next best has 12" is what happens when
        # this reads the whole list instead.
        runner = max((c.min_n for c in others), default=None)
        if runner is None:
            tail = "; it was the only candidate"
        elif runner == best.min_n:
            tied = len([c for c in cands if c.min_n == best.min_n])
            tail = (f"; {tied} candidates tie at {best.min_n} and this is the "
                    f"first by accession")
        else:
            tail = f"; the next best has {runner} on its smaller side"
        return best, (f"{best.min_n} samples on its smaller side "
                      f"(n={best.n_ref} vs {best.n_test}), the most of "
                      f"{len(cands)} candidate(s){tail}")


def pick(rows: list[dict[str, Any]], chooser: Chooser | None = None) -> Pick:
    """Choose one contrast from a `picker_payload`'s rows, auditably.

    With a `chooser` that fails — a model over its cap, a transport error, a
    key outside the candidate list — this falls back to `RuleChooser` and
    records **the rule chooser** as `chooser`, with the fallback stated in
    `fell_back_from`.

    That attribution is the whole reason a fallback is permissible here. The
    surface prints `chooser` and `stated_rule`, so a reader is never told a
    model chose when the rule did. Falling back silently under the model's name
    would be a plausible wrong claim about the figure's own provenance, which
    is the defect this build spends most of its effort on.
    """
    ch = chooser or RuleChooser()
    cands, excluded = candidates_from_rows(rows)
    chosen, reason = ch.choose(cands)

    fell_back = ""
    if chosen is None and cands and not isinstance(ch, RuleChooser):
        # Candidates existed and this chooser declined them. A genuinely empty
        # candidate set is a refusal and is NOT retried — there is nothing to
        # fall back to and inventing one is the failure this guards against.
        fell_back = f"{ch.name}: {reason}"
        ch = RuleChooser()
        chosen, reason = ch.choose(cands)

    return Pick(chooser=ch.name, stated_rule=ch.stated_rule, chosen=chosen,
                reason=reason, considered=tuple(cands),
                excluded=tuple(excluded), fell_back_from=fell_back,
                control_caveat=control_caveat(chosen) if chosen else "")


__all__ = ["Candidate", "Pick", "Chooser", "RuleChooser", "pick",
           "candidates_from_rows", "control_caveat", "EXCLUSION_REASONS"]
