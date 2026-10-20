"""The go/no-go evidence surface: what exists per target, and what is missing.

Puck asked for "evidence for go/no-go on in vitro experiments". This module is
the honest version of that, and the first thing to say is what it deliberately
does **not** produce: a verdict.

WHY THERE IS NO VERDICT
-----------------------
AGENTS.md rule 7 removed ranking, scores, weights, eligibility and the Pareto
front outright, because that machinery "kept reproducing the defect it was
prescribed to fix". A go/no-go *recommendation* is that machinery with a new
name: to say "go" you must combine twelve rings into one ordering, and the
moment you do, a gene with four hatched rings and one strong value outranks a
gene measured on all eleven. PLAN §8 already settled the wording -- "nothing on
this figure is a ranking, a score or a recommendation. It is a gene set, in a
stated order, with the evidence that exists for each gene drawn honestly beside
it -- including where none exists."

So this surface lays the evidence out per target, makes gaps and contradictions
impossible to miss, and lets the team decide. It emits no verdict, no score, no
total, and no per-gene number that could be sorted into one. The genes come back
in the order the run drew its spokes, which is a property of the run and is
stated on the figure; this module never reorders them.

THE THREE OUTCOMES, AND WHY THE THIRD ONE IS THE POINT
------------------------------------------------------
Every rule below reads two or three specific rings. For one gene a rule is:

  * ``fires``       -- the rings had values and the rule's condition held
  * ``clear``       -- the rings had values and it did not hold
  * ``unevaluable`` -- at least one ring it reads has no measurement

That third outcome is the reason this module exists rather than being a list
comprehension in `app.js`. A rule that silently returned "no contradiction"
when its inputs were hatched would make missing data read as reassurance, which
is AGENTS.md rule 1 ("missing is not low") in the one place it does the most
damage -- a reader deciding whether to spend bench time. `unevaluable` names the
rings that were missing, and the UI draws it distinctly from `clear`.

WHAT COUNTS AS A CONTRADICTION
------------------------------
Only a **disagreement between two named sources about the same fact**, where
both sources' own published definitions are what make it a disagreement. Not a
judgement that some combination is unpromising -- that would be eligibility
machinery, and it is exactly what rule 7 forbids. Each rule carries the
definition that makes it a contradiction in its own `why`, and the UI prints
that beside the finding, following the discipline of the heatmap's mitigation
panels: stated descriptive facts, each with its definition beside it.

Flags are the other kind: a single fact, or a factual history, that a reader
must not miss. They are not contradictions and are labelled separately, because
calling a stopped trial a "contradiction" would be this build crying wolf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .states import Cell

#: Rule outcomes. `UNEVALUABLE` is never collapsed into `CLEAR` anywhere.
FIRES = "fires"
CLEAR = "clear"
UNEVALUABLE = "unevaluable"

#: The two kinds of finding. Kept apart so a stopped trial is never drawn as a
#: source disagreement, and so the red-banner logic of the rest of the build --
#: don't cry wolf -- holds here too.
CONTRADICTION = "contradiction"
FLAG = "flag"

#: Ordinals these rules compare against, named rather than inlined so a rule
#: reads as its source's own vocabulary. From `adapters/pharos.TDL_ORDINAL` and
#: `figure.ACTIVITY_LABELS` / the `clinical_phase` RingSpec's `units`.
TDL_TDARK, TDL_TCLIN = 0.0, 3.0
PHASE_NONE, PHASE_APPROVED = 0.0, 5.0
ACTIVITY_STOPPED_FOR_CAUSE = -1.0
PROBE_PRESENT = 1.0
MOUSE_NO_ABNORMAL = 0.0


#: The domain each ring's value must sit in for a rule to mean anything, from
#: the `RingSpec`s in `figure.py`. A value outside it is a value on a DIFFERENT
#: SCALE, and a rule applied across scales is arithmetic about two things.
#:
#: Not hypothetical: 23 of the 261 runs under `runs/` were drawn before V9
#: reduced the rodent ring to three states and carry raw phenotype COUNTS, up
#: to 249. On those runs `rodent == 0` means "no phenotype records found",
#: while this build's `rodent == 0` means "phenotyped, and nothing abnormal" —
#: opposite readings of the same number, and `mouse_phenotyped_and_normal`
#: would have asserted the second over the first.
RING_DOMAINS: dict[str, tuple[float, float]] = {
    "clinical_phase": (0.0, 5.0),
    "tdl": (0.0, 3.0),
    "rodent": (0.0, 1.0),
    "chemical_probe": (0.0, 1.0),
    "safety": (-1.0, 1.0),
    "clinical_activity": (-1.0, 1.0),
}


def out_of_domain(ring: str, cell: Cell) -> bool:
    """Whether this cell's value is outside the scale the rules assume."""
    lo_hi = RING_DOMAINS.get(ring)
    if lo_hi is None or cell.state != "value" or cell.value is None:
        return False
    return not (lo_hi[0] <= cell.value <= lo_hi[1])


@dataclass(frozen=True)
class Rule:
    """One named reading over two or three rings.

    `test` is handed only cells that are all in state `value` -- the
    unevaluable case is decided before it runs, so no rule has to re-check and
    none can accidentally treat `None` as a number. It returns the headline when
    the condition holds and `""` when it does not.
    """

    key: str
    kind: str
    rings: tuple[str, ...]
    #: The definition that makes this a finding, printed beside it. Not a
    #: recommendation: it says why the two numbers are in tension, never what to
    #: do about it.
    why: str
    test: Callable[[dict[str, Cell]], str]


def _v(cells: dict[str, Cell], key: str) -> float:
    """The value of a ring this rule already knows is in state `value`."""
    v = cells[key].value
    assert v is not None, f"{key} passed the value gate with value None"
    return v


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
# Three contradictions, each one a disagreement between Open Targets and Pharos
# about a fact both of them publish a definition for, and three flags. Nothing
# here is a judgement that a target is weak; every one is a statement that two
# sources disagree, or that a specific thing happened.


def _approved_but_not_tclin(c: dict[str, Cell]) -> str:
    if _v(c, "clinical_phase") == PHASE_APPROVED and _v(c, "tdl") < TDL_TCLIN:
        return ("Open Targets attributes an APPROVED drug to this target, and "
                "Pharos does not class it Tclin")
    return ""


def _tclin_but_no_attributable_drug(c: dict[str, Cell]) -> str:
    if _v(c, "tdl") == TDL_TCLIN and _v(c, "clinical_phase") == PHASE_NONE:
        return ("Pharos classes this target Tclin — an approved drug acts "
                "through it — and Open Targets attributes no indication to it "
                "at all")
    return ""


def _probe_but_tdark(c: dict[str, Cell]) -> str:
    if (_v(c, "chemical_probe") == PROBE_PRESENT
            and _v(c, "tdl") == TDL_TDARK):
        return ("a high-quality chemical probe exists, and Pharos classes this "
                "target Tdark — nobody has looked")
    return ""


def _reached_a_phase_then_stopped_for_cause(c: dict[str, Cell]) -> str:
    if (_v(c, "clinical_phase") > PHASE_NONE
            and _v(c, "clinical_activity") == ACTIVITY_STOPPED_FOR_CAUSE):
        return ("a drug reached the clinic against this target and a trial was "
                "stopped for cause — Open Targets categorised the stop "
                "Negative or Safety_Sideeffects")
    return ""


def _safety_liabilities_recorded(c: dict[str, Cell]) -> str:
    """NEGATIVE is the liability arm. Measured, not assumed.

    `adapters/opentargets.SPARSE_KEY_ABSENT_VALUE`'s census over 24 targets:
    `hasSafetyEvent` is observed only as -1, `isCancerDriverGene` only as -1,
    `geneEssentiality` as -1 or 0. `palette.py`'s own legend for this family is
    "red = liability / constraint · blue = clean on these four keys", and red
    is the low arm.

    This read `> 0.0` until 2026-10-09 and was therefore exactly inverted: it
    fired on olfactory receptors with no safety record at all (OR51E2 +0.33,
    PCSK9 +0.23) and stayed silent on MYC (-1.00), KRAS (-0.97), BRAF (-0.73)
    and TP53 (-0.69). 679 firings across the runs on disk, every one of them
    backwards — the cardinal rule's defect in a finding that reads as evidence.
    """
    v = _v(c, "safety")
    if v < 0.0:
        return (f"Open Targets records safety liabilities or constraint here "
                f"({v:+.2f}, where -1.00 is the most constrained): a safety "
                f"event, essentiality, genetic constraint, or cancer-driver "
                f"status")
    return ""


def _mouse_phenotyped_and_normal(c: dict[str, Cell]) -> str:
    if _v(c, "rodent") == MOUSE_NO_ABNORMAL:
        return ("the mouse WAS phenotyped and no abnormal phenotype is "
                "reported — this is a measurement, not a gap")
    return ""


RULES: tuple[Rule, ...] = (
    Rule("approved_but_not_tclin", CONTRADICTION,
         ("clinical_phase", "tdl"),
         why=("Pharos defines Tclin as 'an approved drug acts through this "
              "target' and Open Targets' phase 5 means an approved drug is "
              "attributable to it. The two sources are answering the same "
              "question and returning different answers, so at most one of "
              "them is describing this target correctly and the figure cannot "
              "say which."),
         test=_approved_but_not_tclin),
    Rule("tclin_but_no_attributable_drug", CONTRADICTION,
         ("tdl", "clinical_phase"),
         why=("The mirror of the above, and it has a benign explanation that "
              "has to be stated rather than assumed: this build's phase ring "
              "is gated on Open Targets target-disease GENETIC evidence, so a "
              "genuinely drugged target with no genetic association scores 0 "
              "here. That makes this a disagreement about attribution rather "
              "than about the drug — which is worth seeing, and is not "
              "evidence against the target."),
         test=_tclin_but_no_attributable_drug),
    Rule("probe_but_tdark", CONTRADICTION,
         ("chemical_probe", "tdl"),
         why=("Pharos Tchem means known small-molecule ligands exist and "
              "Tdark means nothing is known beyond the sequence. A "
              "high-quality chemical probe IS a small-molecule ligand, so "
              "these two cells cannot both be current."),
         test=_probe_but_tdark),
    Rule("reached_a_phase_then_stopped_for_cause", FLAG,
         ("clinical_phase", "clinical_activity"),
         why=("Not a contradiction — a history, and the one most likely to "
              "change a go/no-go call. Measured: roughly 7 of 8 "
              "terminations say nothing about the target "
              "(Insufficient_Enrollment, Business_Administrative); those land "
              "on 'dormant' and never here. This cell is the remainder that "
              "Open Targets categorised Negative or Safety_Sideeffects."),
         test=_reached_a_phase_then_stopped_for_cause),
    Rule("safety_liabilities_recorded", FLAG,
         ("safety",),
         why=("The ring's own label is 'NOT evidence of safety'. NEGATIVE is "
              "the liability arm -- `hasSafetyEvent` and `isCancerDriverGene` "
              "are observed only as -1, and palette.py's legend for this "
              "family is 'red = liability / constraint, blue = clean on these "
              "four keys'. A negative value means Open Targets holds one of "
              "those records; it does not quantify risk. The converse matters "
              "more and is why this is a flag rather than a score: a value at "
              "or above zero means Open Targets holds no such record, which is "
              "a fact about Open Targets and NOT evidence that this target is "
              "safe."),
         test=_safety_liabilities_recorded),
    Rule("mouse_phenotyped_and_normal", FLAG,
         ("rodent",),
         why=("The rodent result is a lead, not a verdict. "
              "This is the level a reader is most likely to misread, because "
              "it looks like the hatched 'not phenotyped' state and means the "
              "opposite — somebody made the mouse and it was normal. Stated "
              "as a measurement so it is not mistaken for a gap."),
         test=_mouse_phenotyped_and_normal),
)


# ---------------------------------------------------------------------------
# Applying them
# ---------------------------------------------------------------------------


def evaluate_gene(cells: dict[str, Cell]) -> list[dict[str, Any]]:
    """Every rule's outcome for one gene, in `RULES` order.

    Returns one entry per rule, always — a rule that did not fire is reported
    as `clear` or `unevaluable` rather than omitted, because "this was checked
    and did not hold" and "this could not be checked" are different facts and a
    surface that drops both looks identical in each case.
    """
    out: list[dict[str, Any]] = []
    for rule in RULES:
        # Rings this run did not draw at all are not the same as rings it drew
        # without a value; both make the rule unevaluable and both are named.
        absent = [r for r in rule.rings if r not in cells]
        missing = [r for r in rule.rings
                   if r in cells and cells[r].state != "value"]
        # A value on another scale is not a value this rule can read. Reported
        # as unevaluable rather than guessed at — the same answer rule 1 gives
        # for a cell with no measurement, and for the same reason.
        off_scale = [r for r in rule.rings
                     if r in cells and out_of_domain(r, cells[r])]
        if absent or missing or off_scale:
            out.append({
                "rule": rule.key, "kind": rule.kind, "outcome": UNEVALUABLE,
                "rings": list(rule.rings), "why": rule.why,
                "headline": "",
                # Named, so the UI can say WHICH ring has to be filled in
                # before this reading means anything.
                "missing_rings": missing + absent + off_scale,
                "missing_states": {r: cells[r].state for r in missing},
                "not_drawn": absent,
                # Named separately: "this run drew it on another scale" is a
                # different fact from "nobody measured it", and a reader
                # chasing the second would never find the first.
                "off_scale": {r: cells[r].value for r in off_scale},
            })
            continue
        headline = rule.test(cells)
        out.append({
            "rule": rule.key, "kind": rule.kind,
            "outcome": FIRES if headline else CLEAR,
            "rings": list(rule.rings), "why": rule.why,
            "headline": headline,
            "missing_rings": [], "missing_states": {}, "not_drawn": [],
            "off_scale": {},
        })
    return out


def gaps_for_gene(cells: dict[str, Cell], ring_keys: list[str]
                  ) -> dict[str, list[str]]:
    """The rings with no measurement, grouped by WHICH of the three non-value
    states they are in.

    `ring_keys` is the caller's scope and `view.evidence_payload` passes the
    nine EXTERNAL rings — the same set `k` counts and the only rings any rule
    reads. The two DE rings are deliberately outside it: logFC and adjusted p
    are the input that selected this gene set, not evidence about the target,
    and a reading built on them would be this surface reasoning about effect
    size (rule 7). They also cannot be absent on a rendered gene, because
    `de_input` drops an unusable logFC or a missing padj to the drop ledger.

    Grouped rather than counted, and never summed into one "missingness"
    number: `failed` is a call that broke and is fixable by re-running, while
    `no_data` is the source genuinely saying nothing and `not_applicable` is the
    question not applying to this gene. Collapsing them would tell a reader to
    stop looking where the honest answer is "ask again" (AGENTS.md rule 1's
    four states, and the reason there are four).
    """
    out: dict[str, list[str]] = {"no_data": [], "not_applicable": [],
                                 "failed": []}
    for key in ring_keys:
        cell = cells.get(key)
        if cell is not None and cell.state in out:
            out[cell.state].append(key)
    return out
