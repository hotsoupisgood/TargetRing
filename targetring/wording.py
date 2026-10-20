"""Internal build codes out of reader-visible text, facts kept.

Review of 2026-10-20, item 3. Nine surfaces printed this build's own review
codes at a reader: every gene's completeness hover said "(COUNCIL V13: your own
logFC and padj are not counted)", the drawer's literature block said "COUNCIL
M6 measured it swinging 44% on phrasing alone", the flow page said "no ranking
of any kind (AGENTS.md rule 7)". `COUNCIL V13` is a row in a review document
that is not on the page and is not linked from it, so for the one reader it
reaches it is a dead end — and the sentence around it was already carrying the
fact.

So: **the fact stays, the code goes.** "(COUNCIL V13: your own logFC and padj
are not counted)" becomes "(your own logFC and padj are not counted)" — same
sentence, one dead reference shorter. A code that was the *whole* parenthetical
is deleted outright, because there was no fact in it to keep.

Two callers, one rule:

* the **source strings** were edited to their stripped form in the same commit,
  so a new run writes clean text and this function is a no-op on it —
  `tests/test_wording.py` asserts exactly that, which is what stops the codes
  coming back one adapter at a time;
* the **finished run** on the page was baked on 2026-10-18 and its text is on
  disk, so `scripts/restamp_wording.py` applies this to its artefacts. Offline:
  no network, no model call, no spend. It changes wording and never a number,
  and it is idempotent.

The build record for every decision these codes point at is unchanged and still
in `docs/reviews/`; the references now live in code comments, next to the
behaviour they explain, where their reader is a reader of the code.
"""

from __future__ import annotations

import re

#: One code reference: "COUNCIL V13", "COUNCIL M7/V8", "COUNCIL v0 M3",
#: "COUNCIL D1 gap (ii)", "AGENTS.md rule 7", "AGENTS.md:17", bare "AGENTS.md".
#: The trailing `[^)(:;.]*` is what carries a code's own qualifier ("gap (ii)"
#: is the one with a nested paren and it is handled by the caller's pattern, not
#: here).
CODE = r"(?:COUNCIL(?:\s+v\d+)?\s+[A-Z]?\d+(?:/[A-Z]?\d+)*|AGENTS\.md(?::\d+|\s+rule\s+\d+)?)"

#: A parenthetical that is NOTHING BUT codes, with the version/plan prefixes
#: that travel with them: "(COUNCIL V4)", "(v2.3 item 1, COUNCIL M7/V8)",
#: "(COUNCIL V1/D2)", "(COUNCIL S1)", "(COUNCIL D1 gap (ii))". Deleted whole,
#: along with the space in front of it.
_ONLY_CODES = re.compile(
    r"\s*\((?:(?:PLAN\s+§[\d.]+|v[\d.]+\s+item\s+\d+|QA\s+\S+)[,;]\s*)*"
    rf"{CODE}(?:[,/]\s*{CODE})*"
    r"(?:\s+gap\s+\(\w+\))?\)")

#: A code that INTRODUCES a fact: "(COUNCIL V13: your own logFC and padj are
#: not counted)" -> "(your own logFC and padj are not counted)". The colon is
#: the hinge, and everything after it is the sentence's own content.
_CODE_THEN_FACT = re.compile(
    rf"\((?:(?:PLAN\s+§[\d.]+|v[\d.]+\s+item\s+\d+)[,;]\s*)*{CODE}:\s*")

#: A code hung off the end of a clause on a dash: "…is deliberately NOT on the
#: ring — COUNCIL V9)" -> "…is deliberately NOT on the ring)".
_DASHED_CODE = re.compile(rf"\s*[—–-]{{1,2}}\s*{CODE}(?=[\s).,;]|$)")

#: A code inside a parenthetical that also carries a FACT: "(see the figure,
#: COUNCIL V9)" -> "(see the figure)". Run AFTER `_ONLY_CODES`, which has
#: already taken the parentheticals that were only references.
_TRAILING_IN_PARENS = re.compile(rf"(\([^()]*?),\s*{CODE}(?=\))")

#: A code that OPENS a sentence: "AGENTS.md rule 6: the rodent result is a lead"
#: -> "The rodent result is a lead". Capitalised back up, because the code was
#: carrying the sentence's first letter.
_LEADING_CODE = re.compile(rf"(^|(?<=[.;]\s)){CODE}(?:\s+\([^()]*\))?:\s*")

#: Marks where a removal happened, so the tidy-up runs ONLY at that seam.
#: A blanket "collapse a space before a period" pass would reach text with no
#: code in it at all: the first draft of this turned "run_conditions: 'region
#: ... was inferred'" into "'region... was inferred'" in a manifest string that
#: was not being changed for any other reason.
_SEAM = "\x00"

#: What closes a seam, in order. A seam against punctuation, a bracket or a
#: closing quote closes to nothing; any other seam closes to the one space that
#: held the sentence together. The quote matters because this runs over a line
#: of `values.csv` and over a JSON string: a code at the end of a field would
#: otherwise leave a space inside the quotes.
_CLOSE = (
    (re.compile(rf"^[ \t]*{_SEAM}[ \t]*"), ""),
    (re.compile(rf"[ \t]*{_SEAM}[ \t]*$"), ""),
    (re.compile(rf"[ \t]*{_SEAM}[ \t]*(?=[.,;:)\]\"'])"), ""),
    (re.compile(rf"(?<=[(\[]){_SEAM}[ \t]*"), ""),
    (re.compile(rf"[ \t]*{_SEAM}[ \t]*"), " "),
)


def strip_internal_codes(text: str) -> str:
    """`text` with this build's review codes removed and its facts kept.

    Idempotent, and a no-op on text that has none — which is every string the
    current source writes.

    >>> strip_internal_codes("3 of 9 external rings have a real measurement "
    ...                      "(COUNCIL V13: your own logFC and padj are not counted)")
    '3 of 9 external rings have a real measurement (your own logFC and padj are not counted)'
    >>> strip_internal_codes("Refusing a shorter mean (COUNCIL V4).")
    'Refusing a shorter mean.'
    >>> strip_internal_codes("the count is in the drawer and is deliberately "
    ...                      "NOT on the ring — COUNCIL V9)")
    'the count is in the drawer and is deliberately NOT on the ring)'
    >>> strip_internal_codes("not biophysical druggability (COUNCIL S1): Tclin "
    ...                      "means somebody already drugged it.")
    'not biophysical druggability: Tclin means somebody already drugged it.'
    >>> strip_internal_codes("it sets no ring value (PLAN §2, COUNCIL M6); the "
    ...                      "literature ring is PubTator3")
    'it sets no ring value; the literature ring is PubTator3'
    >>> strip_internal_codes("the ring is three states (see the figure, COUNCIL V9).")
    'the ring is three states (see the figure).'
    >>> strip_internal_codes("a 4-key mean (v2.3 item 1, COUNCIL M7/V8).")
    'a 4-key mean.'
    >>> strip_internal_codes("AGENTS.md rule 6: the rodent result is a lead.")
    'The rodent result is a lead.'
    >>> strip_internal_codes("no number here")
    'no number here'
    >>> strip_internal_codes("run_conditions: 'region ... was inferred'")
    "run_conditions: 'region ... was inferred'"
    """
    # The guard, not an optimisation: a string with no code in it is returned
    # BY IDENTITY, so this function cannot reach text it has no business in.
    if not text or not has_internal_code(text):
        return text
    out = _LEADING_CODE.sub(lambda m: m.group(1) + _SEAM, text)
    out = _CODE_THEN_FACT.sub("(" + _SEAM, out)
    # `_ONLY_CODES` before `_TRAILING_IN_PARENS`: a parenthetical that is
    # nothing but references goes whole, prefixes and all, so "(PLAN §2,
    # COUNCIL M6)" does not survive as "(PLAN §2)" -- a spec section is one
    # more reference that is not on the page. `_TRAILING_IN_PARENS` is then
    # only for a parenthetical carrying a FACT as well.
    out = _ONLY_CODES.sub(_SEAM, out)
    out = _TRAILING_IN_PARENS.sub(r"\1" + _SEAM, out)
    out = _DASHED_CODE.sub(_SEAM, out)
    for pat, rep in _CLOSE:
        out = pat.sub(rep, out)
    assert _SEAM not in out, out
    # `_LEADING_CODE` took the sentence's first letter with it.
    if out and out[0].islower() and not text[0].islower():
        out = out[0].upper() + out[1:]
    return out


def has_internal_code(text: str) -> bool:
    """True where `text` still names a build code. The test's predicate, and
    the restamp's own check: a surface that fails this is printing a reference
    a reader cannot follow."""
    return bool(re.search(rf"\b{CODE}", text or ""))


__all__ = ["CODE", "strip_internal_codes", "has_internal_code"]
