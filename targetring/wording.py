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


#: Tissue names GTEx writes with underscores and this build prints with spaces.
#: One display form, decided by the 2026-10-20 review: the page had three --
#: `Small_Intestine_Terminal_Ileum` on the opening view and the figure,
#: `ileum` in the drawer's verdict tag (`recommend.REGION_SHORT`), and the
#: spaced form in neither.
#:
#: The spaced one wins because it is the same NAME: a reader who wants to find
#: this tissue in GTEx is looking for these words, and shortening "Small
#: Intestine Terminal Ileum" to "ileum" renames the organ -- GTEx also has
#: `Terminal_Ileum`, and the two are different tissue samples. The underscored
#: id is still what the manifest records and what the GTEx query used; it is an
#: IDENTIFIER, and it stays on the hovers and in the artefact beside this.
def region_label(region: str | None) -> str:
    """The region as a reader reads it. One form, everywhere.

    >>> region_label("Small_Intestine_Terminal_Ileum")
    'Small Intestine Terminal Ileum'
    >>> region_label("Whole_Blood")
    'Whole Blood'
    >>> region_label("Lung")
    'Lung'
    >>> region_label(None)
    'none'
    >>> region_label("")
    'none'

    GTEx's own capitalisation is kept rather than re-cased: `Brain_Cortex_BA24`
    carries a Brodmann area and title-casing it to `Ba24` would be wrong about
    a name this build did not coin.

    >>> region_label("Brain_Anterior_Cingulate_Cortex_BA24")
    'Brain Anterior Cingulate Cortex BA24'
    """
    return (region or "").replace("_", " ") or "none"


#: The bounds, as a reader reads them. The page's own markup has always used
#: "≥" while the server's strings used ">=", so one sentence on the Table tab
#: carried both (review of 2026-10-20, item 5). The source strings were changed;
#: these are for text already on disk.
#:
#: The third rule used to rewrite " -- " to an em dash. The dashboard language
#: standard of 2026-10-21 forbids an em dash in reader-visible text (rule 5),
#: so a spaced "--" now closes to a comma instead: the one separator that reads
#: correctly wherever a dash was carrying a clause, and that never leaves a
#: sentence fragment behind. The leading space is consumed and the trailing one
#: kept, so "a -- b" becomes "a, b". Idempotent, because the output holds no
#: spaced "--" for a second pass to find.
#:
#: Spaces are required around "--" so a hyphenated word and a command-line flag
#: survive, and ">=" is only rewritten with a space or a digit after it so a
#: filter EXPRESSION -- the syntax the analyse form parses, echoed back at the
#: reader -- is handled by the caller skipping that field rather than by this
#: pattern guessing.
_OPS = ((re.compile(r">="), "≥"),
        (re.compile(r"<="), "≤"),
        (re.compile(r" --(?= )"), ","))

#: "target" meaning "gene". This build draws a gene set and calls it that
#: everywhere except in Open Targets' own name and its own field names
#: ("target-disease associations", "target-prioritisation"), which are proper
#: nouns and are left alone -- hence the phrase list rather than a word swap.
_GENE_NOT_TARGET = (
    ("this target", "this gene"),
    ("the target for an", "the gene for an"),
    ("no target record", "no gene record"),
    ("at a target we do hold", "at a gene we do hold"),
    ("human targets", "human genes"),
    ("matched no Open Targets target", "matched no Open Targets record"),
    # The ChEMBL phrase before the bare one, or it becomes "ChEMBL a gene
    # record exists": these are applied in order and the first match wins the
    # substring.
    ("ChEMBL target record exists", "a ChEMBL record exists"),
    ("target record exists", "a gene record exists"),
)

#: The three sentences the dashboard language standard of 2026-10-21 changed by
#: more than punctuation. Rule 2 is "precise, not impressionistic", and its word
#: list bans "reads as" and "sits": Puck's own objection was *"reads is
#: unspecific. do a full review of this kind of language"*.
#:
#: These are here for the same reason the bounds are. The source strings were
#: edited in the same commit, so a new run writes the right words and this is a
#: no-op on it (`tests/test_wording.py` asserts that); the four finished runs
#: have the old words baked into 46 drawer files and 4 manifests, and
#: `scripts/restamp_wording.py` is what reaches those. Each pair is anchored on
#: enough of its own sentence that it cannot fire on anything else, and the
#: replacement keeps every number and every fact.
_IMPRESSIONISTIC = (
    # `recommend.expression_summary`'s note.
    ("panel median reads as tissue-specific for",
     "panel median is high for"),
    # `phenotype_llm.NOTE`.
    ("It sits BESIDE the owner's", "It is printed BESIDE the owner's"),
    # `spend.ACCOUNT_PURPOSE[PHENOTYPE]`.
    ("only the table it sits above", "only the table printed below it"),
    # `contract.OPENTARGETS_RING_KEYS`, baked into each manifest invariant row.
    ("every key a ring reads is present", "every key a ring queries is present"),
)

#: Units, and file paths off the surface. Two instructions from 2026-10-21,
#: reaching the same 46 baked drawers the pairs above do.
#:
#: Puck, 10:05: *"please be descriptive. do not allow disembodied numbers
#: without units"*, with "logFC +2.6" -> "log2 fold change +2.6" as the worked
#: example. So wherever a NUMBER is printed and `logFC` is the only thing
#: naming what it measures, the quantity is spelled out. Where `logFC` is a
#: LABEL and not a unit it stays, which is why none of these pairs is a bare
#: "logFC": the ring's name on the baked figures ("logFC (yours)", "logFC
#: (Atlas)") and the token the filter syntax parses (`logfc >= 1.0`) are
#: untouched, and a pair that fired on them would rename a column head to
#: disagree with its own figure.
#:
#: Rule 10 of the standard, for the rest: a reader of the page cannot follow a
#: repo path, so the three notes that carried one say the same fact in words.
#: The mouse ring's "> 0" gains the scale it is read on, which is rule 16's
#: "a score names its scale".
#:
#: Every number and every bound is carried through unchanged. The rule version
#: does not move with any of this: `recommend.RULE_WORDS` folds the wording back
#: before the hash, and `tests/test_wording.py` pins `bdc70c8fc230` both ways.
_UNITS_AND_PATHS = (
    # `run.py`'s logfc cell detail: "logFC 2.2 (submitted)".
    (re.compile(r"\blogFC (?=[-+0-9])"), "log2 fold change "),
    # `run.py`'s k note and the completeness cell's detail.
    ("your own logFC and padj are not counted",
     "your own log2 fold change and adjusted p are not counted"),
    ("your own logFC and padj are not counted. It",
     "your own log2 fold change and adjusted p are not counted. It"),
    # `recommend._dysregulated`'s no-data bare value and sentence.
    ("no logFC or adjusted p", "no log2 fold change or adjusted p"),
    # `Thresholds.sentence`, `criteria_spec` and the criterion threshold.
    ("|logFC|", "|log2 fold change|"),
    ("mouse phenotype ring > 0 or",
     "mouse phenotype ring above 0 on its 0-to-1 scale, or"),
    ("mouse-KO ring > 0, or",
     "mouse-KO ring above 0 on its 0-to-1 scale, or"),
    # `recommend.disease_area_match`'s note.
    ("display aid, not a criterion (docs/RECOMMEND-SPEC.md)",
     "display aid, not a criterion"),
    # `summary_llm.SUMMARY_NOTE`.
    ("computed by the owner's rule (recommend.py), which it was given",
     "computed by the owner's three-criteria rule, which it was given"),
    # `drawer_bake.BAKED_NOTE`.
    ("state, value and reason is the run's own values.csv.",
     "state, value and reason is the run's own recorded table of values."),
    # `spend.ACCOUNT_PURPOSE[SUMMARY]`.
    ("baked offline by scripts/bake_summaries.py from the gene's own drawer "
     "values", "baked offline before publication from the gene's own drawer "
     "values"),
)

#: k in the ONE printed form. The completeness cell's reason said "8 of 9
#: external rings have a real measurement" in the drawer, beside a grid cell
#: reading "8/9" (review of 2026-10-20, item 5).
_K_SPELLED_OUT = re.compile(
    r"\b(\d+) of (\d+) external rings have a real measurement")

#: A COUNT of four digits or more, ungrouped. Not an IDENTIFIER: the digits must
#: not be glued to a letter or a separator, so `ENSG00000081041`,
#: `E-GEOD-101794`, `b39c02a638ef`, `#808080` and `2026-10-18` are left alone.
#: The `#` in that class is not hypothetical: the first draft turned the I2
#: invariant's "#808080" into "#808,080" in a baked manifest. Python's own
#: `f"{n:,}"` is the grouping, which is what the rest of the server uses and
#: what `app.js`'s `fmtCount` mirrors.
_UNGROUPED_COUNT = re.compile(r"(?<![\w.,/:#-])(\d{4,})(?![\d,/:-]|\w)")

#: Four digits that are a YEAR rather than a count. "2026" must not become
#: "2,026", and no count this build prints is a bare number in this window:
#: the gene totals are five digits and every other count is under 1,000.
_YEAR = range(1900, 2101)

#: An IDENTIFIER standing alone, with no sentence around it. `modernise` rewords
#: a sentence; a string that is nothing but digits is not one, it is a key.
#:
#: `_UNGROUPED_COUNT` cannot tell a key from a count, because every exemption it
#: has looks at the NEIGHBOURS that a bare identifier does not have: a PMID is
#: not glued to a letter, a separator or a `#`. So the exemption has to come
#: from the whole string, which is what this is.
#:
#: Not hypothetical, and not cheap. `scripts/restamp_wording.py` grouped PMID
#: 11911824 to "11,911,824" inside `phenotype_terms[].models[].pmids[]`,
#: `app.js` builds each PubMed href as the base plus that value, and the result
#: was that every one of the 12 PubMed links in the tool resolved to an empty
#: `[uid]` search. It shipped: the live site served it from 9f19811 until the
#: link check of 2026-10-21 read the rendered hrefs rather than the registry.
#:
#: The comma is in the class so the guard holds on text that is ALREADY grouped,
#: which is what a second pass over a baked artefact sees.
_BARE_IDENTIFIER = re.compile(r"\A\s*\d[\d,]*\s*\Z")

#: A GTEx tissue id used as a NAME inside a sentence. Anchored on the phrase so
#: it cannot reach the manifest's own `region` value, which is the identifier
#: the GTEx query used.
_REGION_IN_SENTENCE = re.compile(
    r"\b(TPM in |concentrated in )([A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)")


#: Rule 5 of the dashboard language standard, adopted 2026-10-21: no em dash in
#: reader-visible text, and no en dash standing in for one. The source strings
#: were edited to this form in the same pass; these four runs were baked on
#: 2026-10-18, so `scripts/restamp_wording.py` applies it to their artefacts,
#: which is where the baked drawer summaries live.
#:
#: An em dash in this build's prose is almost always a label joined to its own
#: gloss ("not phenotyped — no mouse phenotype records"), and a colon is the
#: separator that reads correctly there. A SECOND dash in the same string is a
#: bracketed aside rather than a second gloss, so it closes with a comma: one
#: colon per sentence, or the sentence stops reading as one.
#:
#: A dash that OPENS or CLOSES a string is carrying nothing and is dropped with
#: its space, because ": replayed from cache" is worse than what it replaced.
#:
#: An en dash between digits is a range and becomes " to ", the form the
#: standard names. A HYPHEN is never touched at all: a hyphenated word, a
#: command-line flag and `-log10` are not dashes.
_EN_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=\d)")
_DASH_CLAUSE = re.compile(r"\s*[—–]\s*")


def plain_dashes(text: str) -> str:
    """Em and en dashes out, reading unchanged. Idempotent: the output holds
    neither character, so a second pass finds nothing.

    >>> plain_dashes("not phenotyped — no mouse phenotype records")
    'not phenotyped: no mouse phenotype records'
    >>> plain_dashes("0 agents — a record exists — genuinely undrugged")
    '0 agents: a record exists, genuinely undrugged'
    >>> plain_dashes("— replayed from cache")
    'replayed from cache'
    >>> plain_dashes("mean of 3 keys, each 0 or 1 · fixed 0–1")
    'mean of 3 keys, each 0 or 1 · fixed 0 to 1'
    >>> plain_dashes("-log10 adjusted p, non-zero, --force")
    '-log10 adjusted p, non-zero, --force'
    """
    out = _EN_RANGE.sub(" to ", text or "")
    seen = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal seen
        if m.start() == 0 or m.end() == len(out):
            return ""
        seen += 1
        return ": " if seen == 1 else ", "

    return _DASH_CLAUSE.sub(_sub, out)


def _group(m: "re.Match[str]") -> str:
    n = int(m.group(1))
    return m.group(1) if n in _YEAR else f"{n:,}"


def modernise(text: str) -> str:
    """One sentence in this build's current wording: codes out, one region
    form, one name for a gene, the typographic bounds.

    The source strings were edited to this form in the same commit, so this is
    a no-op on anything the running code writes -- `tests/test_wording.py`
    asserts that. It exists for the four finished runs, whose text was baked on
    2026-10-18 (`scripts/restamp_wording.py`).

    >>> modernise("2 candidates hit this target at score >= 0.1")
    '2 candidates hit this gene at score ≥ 0.1'
    >>> modernise("4.0 TPM in Small_Intestine_Terminal_Ileum / 6.0 TPM median")
    '4.0 TPM in Small Intestine Terminal Ileum / 6.0 TPM median'
    >>> modernise("7 of 9 external rings have a real measurement (yours not counted)")
    'k = 7/9 external rings with a real measurement (yours not counted)'
    >>> modernise("Pharos covers 20,412 human targets")
    'Pharos covers 20,412 human genes'
    >>> modernise("65217 = 25232 with a statistic + 39985 without")
    '65,217 = 25,232 with a statistic + 39,985 without'
    >>> modernise("ENSG00000081041 on 2026-10-18, rule b39c02a638ef")
    'ENSG00000081041 on 2026-10-18, rule b39c02a638ef'
    >>> modernise("painted only in ['#4d4d4d', '#808080'] over 2200-step ramp")
    "painted only in ['#4d4d4d', '#808080'] over 2200-step ramp"
    >>> modernise("nothing to change here")
    'nothing to change here'

    A string that is nothing but digits is an identifier, not a sentence, and
    nothing here applies to it. A PMID is the case that matters: grouped, it
    becomes a dead PubMed link.

    >>> modernise("11911824")
    '11911824'
    >>> modernise("11,911,824")
    '11,911,824'
    >>> modernise("20643340")
    '20643340'

    A count inside a sentence still groups, which is the whole point of the
    distinction: the neighbours are what make it a count.

    >>> modernise("Pharos covers 20412 human targets")
    'Pharos covers 20,412 human genes'
    """
    if _BARE_IDENTIFIER.match(text or ""):
        return text
    out = strip_internal_codes(text)
    for a, b in _GENE_NOT_TARGET:
        out = out.replace(a, b)
    for a, b in _IMPRESSIONISTIC:
        out = out.replace(a, b)
    # Before `_OPS`: these pairs are anchored on the ASCII ">" the baked text
    # carries, and `_OPS` is what turns ">=" into its glyph.
    for a, b in _UNITS_AND_PATHS:
        out = a.sub(b, out) if hasattr(a, "sub") else out.replace(a, b)
    out = _K_SPELLED_OUT.sub(
        lambda m: f"k = {m.group(1)}/{m.group(2)} external rings with a real "
                  f"measurement", out)
    out = _REGION_IN_SENTENCE.sub(
        lambda m: m.group(1) + m.group(2).replace("_", " "), out)
    for pat, rep in _OPS:
        out = pat.sub(rep, out)
    out = plain_dashes(out)
    out = _UNGROUPED_COUNT.sub(_group, out)
    return out


def has_internal_code(text: str) -> bool:
    """True where `text` still names a build code. The test's predicate, and
    the restamp's own check: a surface that fails this is printing a reference
    a reader cannot follow."""
    return bool(re.search(rf"\b{CODE}", text or ""))


__all__ = ["CODE", "region_label", "modernise", "plain_dashes",
           "strip_internal_codes", "has_internal_code"]
