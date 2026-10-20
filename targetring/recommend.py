"""GO / NO-GO: Puck's explicit rule, evaluated per gene from its baked facts.

Puck, 2026-10-18 10:26 PDT: "make a recommendation, have go/no go: we need the
gene to be expressed, and also misregulated in the study, and have a strong
phenotype in a study."

Three criteria, each `met` / `not_met` / `no_data`, each printed with the value
it was judged on and the threshold it was judged against:

    expressed      GTEx median TPM in the run's region >= RECOMMEND_MIN_TPM;
                   when the run has no GTEx number for the gene, Open Targets'
                   `tissueDistribution` prioritisation key > 0 stands in
    dysregulated   |logFC| >= RECOMMEND_MIN_ABS_LOGFC  AND  adjusted p <= RECOMMEND_MAX_Q
                   (both from the submitted table -- Puck's own measurement)
    phenotype      the mouse-phenotype ring > 0 (phenotyped, abnormal term on
                   record), or >= RECOMMEND_MIN_PHENOTYPES abnormal
                   mousePhenotypes terms in the drawer's list

Verdict: GO when all three are met; NO-GO when any is not met; INSUFFICIENT
DATA when nothing is unmet but something could not be judged. `not_met` beats
`no_data` on purpose: a gene that fails a criterion is a NO-GO however little
else is known about it.

What this is not. It is not a score, not an ordering and not a filter -- PLAN
v2.5 removed all of those and this does not bring one back. It never touches
the spoke order, the threshold filters or the drop ledger; it is a line in the
gene's drawer, computed from numbers the drawer already shows, with the rule
and the thresholds printed beside it so a reader can disagree with it on the
spot. It is kept apart from the evidence rings (which carry no verdict, rule 6
and rule 7) and from the rodent lead ("a lead, not a verdict") -- the rule
takes the mouse ring as one input *because Puck asked for a phenotype criterion*,
and says so.

2026-10-18 walkthrough (a geneticist read the four Crohn's demo runs). Three
display changes, none of them a change to the rule or its verdicts:

* The expressed line names the region AND says whether it is a disease tissue
  for this contrast. The four demo runs had been judged against Lung because
  the form's silent default was never changed; "expressed in Lung" for a
  Crohn's biopsy contrast is a true number about the wrong organ.
* The specificity sentence is two absolute TPMs ("151 TPM in <region>; median
  across 54 tissues 7 TPM"), not "21.6x the GTEx median". A fold against a
  panel median reads as tissue-specific for anything blood-borne in a
  blood-rich tissue. "Concentrated in <region>" appears only above
  `RECOMMEND_SPECIFIC_RATIO` AND in a disease tissue.
* Beside the phenotype criterion, "disease-area match: yes/no": whether any of
  the abnormal mouse terms falls in the contrast's disease area, by a small
  keyword mapping (`DISEASE_AREAS`, documented in docs/RECOMMEND-SPEC.md). A
  display aid. The owner's rule -- any abnormal term meets the criterion --
  is unchanged, and the match never moves the verdict.

2026-10-18 18:42 PDT trim (Puck: "can remove druggability caveat. and make it
much less wordy. or remove the word expression altogether as the information is
in the graph"). Again no change to the rule or a verdict -- only to what is
printed. The druggability caveat is gone and a bare safety-ring flag is silent
(`caveats` now fires only on a severe liability row); each criterion carries a
`bare` string, the value and its unit and nothing else, which is what the box
prints; the expression sentence leaves the box for a three-word `region_tag`
("region: ileum, inferred"); the disease-area aid leaves it for a count with
the terms in a tooltip; and the rule collapses to `rule_short` with
`rule_tooltip`. `shown`, `expression.shown` and `rule_text` are all still
computed and still in the JSON -- the summary prompt and the drawer's long form
read them, and a trimmed box must not mean a trimmed record.

Pure: no I/O, no network, no model. `recommend(facts)` takes the small
`Facts` record that `facts_from_drawer` reads off a baked drawer dict, so the
bake script, a re-stamp of thresholds and the tests all go through one door.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from . import config
from .pvalue import format_p

RULE_TEXT = "expressed AND dysregulated AND phenotype"
RULE_NOTE = "not used to order or filter the figure"
GO, NO_GO, INSUFFICIENT = "GO", "NO-GO", "INSUFFICIENT DATA"
MET, NOT_MET, NO_DATA = "met", "not_met", "no_data"

# The mouse label that is an informative negative, not an abnormal phenotype
# (`adapters.opentargets.NO_ABNORMAL`; repeated here so this module imports no
# adapter).
_NO_ABNORMAL = "no abnormal phenotype detected"

# The GTEx tissue the analyse form falls back to when nobody chose one
# (`run.DEFAULT_REGION`; repeated here so this module imports no adapter and
# no matplotlib). A run whose region is this AND was not inferred for the
# disease was judged against the silent default.
SILENT_DEFAULT_REGION = "Lung"

# `adapters.gtex.specificity_cell` prints the region TPM in both of its
# shapes: "26.61 TPM in Lung / 1.73 TPM median ..." (value) and "0.216 TPM in
# Liver is below the 1 TPM floor ..." (no_data). The absolute number is what
# the expression criterion needs; the cell's own value is a ratio.
_TPM_RE = re.compile(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?) TPM in (.+?)(?: / | is below )")
_MEDIAN_RE = re.compile(r"/ ([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?) TPM median across ([0-9]+) GTEx tissues")

# ---------------------------------------------------------------------------
# The disease-area display aid (walkthrough item c).
#
# Keyed by a disease area; `disease_patterns` match the CONTRAST's text (its
# Atlas name and test-group label, lower-cased), `areas` map a Mammalian
# Phenotype top-level class to the substrings of a term label that place a
# term under it. Labels, not ontology ids: Open Targets' mousePhenotypes rows
# carry the term label and id but not the top-level class, and the drawer
# bakes the labels. So this is a keyword mapping, honest about being one, and
# it is printed as "display aid, not a criterion". Extend it per disease; a
# contrast that matches no entry gets "no disease-area mapping" and no
# yes/no.
# ---------------------------------------------------------------------------
DISEASE_AREAS: dict[str, dict[str, Any]] = {
    "inflammatory bowel disease": {
        "disease_patterns": ("crohn", "colitis", "inflammatory bowel", "ibd"),
        "areas": {
            "digestive/alimentary system": (
                "intestin", "colon", "colitis", "ileum", "ileal", "bowel", "gut",
                "digest", "gastr", "enteric", "enterocyte", "crypt", "paneth",
                "goblet", "stomach", "duoden", "jejun", "rectum", "rectal",
                "cecum", "caecum", "peyer", "villus", "villi", "mucosa",
                "absorption", "feces", "fecal", "diarrhea", "gallbladder",
                "gallstone", "bile", "biliary", "liver", "hepat", "pancrea", "esophag",
                "oesophag", "saliva", "tooth", "teeth"),
            "immune system": (
                "immun", "lymph", "leukocyte", "monocyte", "neutrophil",
                "macrophage", "granulocyte", "t cell", "b cell", "t-cell",
                "b-cell", "thymus", "thymocyte", "spleen", "splenic", "cytokine",
                "interleukin", "antibody", "immunoglobulin", "iga", "igg", "igm",
                "ige", "nk cell", "dendritic", "eosinophil", "basophil",
                "mast cell", "autoimmun", "complement", "interferon"),
            "inflammation": ("inflamm", "arthritis", "dermatitis", "vasculitis",
                             "hepatitis", "nephritis", "sepsis", "septic"),
        },
    },
}


def disease_area_for(disease_text: str | None) -> str | None:
    """The `DISEASE_AREAS` key whose patterns match the contrast's text, or
    None. Text, not an ontology id: the manifest carries the contrast's Atlas
    name and group labels, and that is what a reader of the figure sees."""
    low = (disease_text or "").lower()
    if not low:
        return None
    for area, spec in DISEASE_AREAS.items():
        if any(p in low for p in spec["disease_patterns"]):
            return area
    return None


def mapping_version() -> str:
    """Hash of the mapping, so a baked match says which mapping produced it."""
    body = repr(sorted((k, sorted(v["disease_patterns"]),
                        sorted((a, sorted(p)) for a, p in v["areas"].items()))
                       for k, v in DISEASE_AREAS.items()))
    return hashlib.sha256(body.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Thresholds:
    min_tpm: float = config.RECOMMEND_MIN_TPM
    min_abs_logfc: float = config.RECOMMEND_MIN_ABS_LOGFC
    max_q: float = config.RECOMMEND_MAX_Q
    min_phenotypes: int = config.RECOMMEND_MIN_PHENOTYPES
    specific_ratio: float = config.RECOMMEND_SPECIFIC_RATIO

    def as_dict(self) -> dict[str, float | int]:
        return {"min_tpm": self.min_tpm, "min_abs_logfc": self.min_abs_logfc,
                "max_q": self.max_q, "min_phenotypes": self.min_phenotypes,
                "specific_ratio": self.specific_ratio}

    def sentence(self) -> str:
        return (f"GTEx region TPM >= {self.min_tpm:g}; |logFC| >= {self.min_abs_logfc:g} "
                f"and adjusted p <= {self.max_q:g}; mouse phenotype ring > 0 or "
                f">= {self.min_phenotypes:d} abnormal term(s)")


def current_thresholds() -> Thresholds:
    """Read at call time, so a monkeypatched `config` is honoured."""
    return Thresholds(config.RECOMMEND_MIN_TPM, config.RECOMMEND_MIN_ABS_LOGFC,
                      config.RECOMMEND_MAX_Q, config.RECOMMEND_MIN_PHENOTYPES,
                      config.RECOMMEND_SPECIFIC_RATIO)


def rule_version(t: Thresholds) -> str:
    """Short hash of the rule text and the thresholds, so a baked verdict says
    which rule produced it. Changing a number changes the version. The
    specificity line and the disease-area aid are display, not rule, and are
    deliberately NOT in this hash."""
    body = f"{RULE_TEXT}|{t.sentence()}|not_met beats no_data"
    return hashlib.sha256(body.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Facts:
    """Everything the rule reads. `None` means the fact is not known, which
    is never the same as zero (AGENTS.md rule 1)."""
    region: str | None = None
    region_tpm: float | None = None          # GTEx median TPM in `region`
    tissue_distribution: float | None = None # Open Targets prioritisation key, [-1, 1]
    logfc: float | None = None
    padj: float | None = None
    rodent_ring: float | None = None         # 0.0 / 1.0, or None when hatched
    rodent_state: str | None = None          # the ring's state, for the message
    n_abnormal_phenotypes: int | None = None # abnormal terms in the drawer list
    # Caveat inputs only -- never criteria (Puck, 10:28 PDT: "no toxicity data
    # or low druggability does not exclude. this could be fixed by consulting a
    # chemist").
    tractable_ring: float | None = None      # mean of the three dense OT keys, [-1, 1]
    safety_event: float | None = None        # OT hasSafetyEvent key as the ring printed it
    n_severe_liabilities: int = 0            # drawer `severe_liabilities` rows
    # Display inputs (2026-10-18 walkthrough) -- never criteria.
    panel_median_tpm: float | None = None    # GTEx median across the panel, as the cell printed it
    n_tissues: int | None = None             # size of that panel
    # True: the region was inferred for the disease (chooser) or otherwise
    # recorded as a disease tissue. False: the run's region is the silent form
    # default and was not inferred. None: chosen by hand; nothing is claimed.
    region_is_disease_tissue: bool | None = None
    mouse_phenotypes: tuple[str, ...] = ()   # the drawer's term labels
    disease_text: str | None = None          # the contrast's name + test label
    whole_blood_tpm: float | None = None     # GTEx Whole_Blood median TPM, if baked
    spleen_tpm: float | None = None          # GTEx Spleen median TPM, if baked


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def region_tpm_from_detail(detail: str | None) -> tuple[float | None, str | None]:
    """(TPM, region) parsed off a `regional_specificity` cell's detail, or
    (None, None) when the detail has no absolute TPM in it."""
    m = _TPM_RE.search(detail or "")
    if not m:
        return None, None
    return _num(m.group(1)), m.group(2).strip()


def panel_median_from_detail(detail: str | None) -> tuple[float | None, int | None]:
    """(panel median TPM, n tissues) off the same detail, or (None, None)."""
    m = _MEDIAN_RE.search(detail or "")
    if not m:
        return None, None
    return _num(m.group(1)), int(m.group(2))


def region_is_disease_tissue_from_manifest(manifest: dict[str, Any] | None) -> bool | None:
    """Read off the run's manifest: True when `run.py` stamped the
    "region X was inferred from the disease" condition (the chooser's answer),
    False when the region is the silent form default and nothing says it was
    chosen for this disease, None otherwise (chosen by hand -- no claim)."""
    if not manifest:
        return None
    region = manifest.get("region")
    for c in manifest.get("run_conditions") or []:
        cond = str((c or {}).get("condition") or "")
        if cond.startswith("region ") and "inferred from the disease" in cond:
            return True
    if region == SILENT_DEFAULT_REGION:
        return False
    return None


def contrast_text_from_manifest(manifest: dict[str, Any] | None) -> str | None:
    c = (manifest or {}).get("contrast") or {}
    parts = [str(c.get("name") or ""), str(c.get("test_label") or "")]
    text = " ".join(p for p in parts if p).strip()
    return text or None


def facts_from_drawer(d: dict[str, Any], manifest: dict[str, Any] | None = None) -> Facts:
    """Read the rule's inputs off a baked drawer dict (`drawer_bake`). Nothing
    is fetched; a drawer that lacks a fact yields `None` for it. The manifest,
    when given, supplies the contrast text and whether the region was inferred
    for the disease; a drawer baked after 2026-10-18 carries both itself."""
    cells = d.get("cells") or {}
    spec = cells.get("regional_specificity") or {}
    expr = d.get("expression") or {}
    tpm, region = _num(expr.get("region_tpm")), expr.get("region")
    if tpm is None:
        tpm, region = region_tpm_from_detail(spec.get("detail"))
    median, n_tissues = _num(expr.get("panel_median_tpm")), expr.get("n_tissues")
    if median is None:
        median, n_tissues = panel_median_from_detail(spec.get("detail"))
    rodent = cells.get("rodent") or {}
    rodent_val = _num(rodent.get("value")) if rodent.get("state") == "value" else None
    mp = d.get("mouse_phenotypes")
    n_abn: int | None = None
    terms: tuple[str, ...] = ()
    if isinstance(mp, list):
        terms = tuple(str(p) for p in mp if p)
        n_abn = sum(1 for p in mp if _NO_ABNORMAL not in str(p or "").lower())
    tract = cells.get("tractable") or {}
    safety = cells.get("safety") or {}
    disease_tissue = expr.get("region_is_disease_tissue")
    if disease_tissue is None:
        disease_tissue = region_is_disease_tissue_from_manifest(manifest)
    return Facts(
        region=region,
        region_tpm=tpm,
        tissue_distribution=_num(expr.get("tissue_distribution")),
        logfc=_num(d.get("logfc")),
        padj=_num(d.get("padj")),
        rodent_ring=rodent_val,
        rodent_state=rodent.get("state"),
        n_abnormal_phenotypes=n_abn,
        tractable_ring=_num(tract.get("value")) if tract.get("state") == "value" else None,
        safety_event=_safety_event_from_detail(safety.get("detail"))
        if safety.get("state") == "value" else None,
        n_severe_liabilities=len(d.get("severe_liabilities") or []),
        panel_median_tpm=median,
        n_tissues=int(n_tissues) if n_tissues is not None else None,
        region_is_disease_tissue=disease_tissue,
        mouse_phenotypes=terms,
        disease_text=d.get("contrast_text") or contrast_text_from_manifest(manifest),
        whole_blood_tpm=_num(expr.get("whole_blood_tpm")),
        spleen_tpm=_num(expr.get("spleen_tpm")),
    )


# `safety_cell` prints each key it averaged: "hasSafetyEvent=-1.00" is a
# recorded event, "+0.00*" is the key absent (Open Targets emits it only at the
# non-zero end, so absence is "no event on record", not missing).
_SAFETY_EVENT_RE = re.compile(r"hasSafetyEvent=([+-]?[0-9.]+)")


def _safety_event_from_detail(detail: str | None) -> float | None:
    m = _SAFETY_EVENT_RE.search(detail or "")
    return _num(m.group(1)) if m else None


def caveats(f: Facts) -> list[str]:
    """At most one short line under the verdict, and only for a SEVERE safety
    liability on record.

    Puck, 2026-10-18 18:42 PDT: "can remove druggability caveat. and make it
    much less wordy." So the druggability caveat is gone -- it was two clauses
    of chemistry advice under a verdict that never read it -- and a plain
    `hasSafetyEvent` ring flag is silent too: Open Targets emits that key for
    anything on record, and "a safety flag is on record" under a GO was noise
    the reader could not act on. What survives is the drawer's own
    `severe_liabilities` rows, which name an organ system and an effect.
    Still never a criterion and never an exclusion.
    """
    if f.n_severe_liabilities > 0:
        n = f.n_severe_liabilities
        return [f"Safety: {n} severe liability record{'' if n == 1 else 's'} "
                f"-- not an exclusion."]
    return []


def modality(f: Facts) -> str | None:
    """Puck, 10:28 PDT: "note if it's downregulated, a biologic may be
    preferred". One sentence for a negative logFC; nothing otherwise."""
    if f.logfc is not None and f.logfc < 0:
        return "down-regulated in the study: a biologic may be preferred"
    return None


def _criterion(key: str, label: str, status: str, value: Any, shown: str,
               threshold: str, source: str, bare: str = "") -> dict[str, Any]:
    """`shown` is the sentence (kept: the JSON, the summary prompt and the
    drawer's long form read it). `bare` is the trimmed box's whole right-hand
    side -- the value and its unit and nothing else (Puck, 2026-10-18 18:42
    PDT: "make it much less wordy"). Both are computed from the same number."""
    return {"key": key, "label": label, "status": status, "value": value,
            "shown": shown, "bare": bare or shown, "threshold": threshold,
            "source": source}


#: The region as a one-word tag for the trimmed box ("region: ileum,
#: inferred"). GTEx's own tissue names are long and underscored and the box has
#: no room for one; a name not in this table is printed in full, lower-cased,
#: rather than cut by a rule that could rename the organ.
REGION_SHORT: dict[str, str] = {
    "Small_Intestine_Terminal_Ileum": "ileum",
    "Colon_Transverse": "colon",
    "Colon_Sigmoid": "colon",
    "Whole_Blood": "blood",
    "Spleen": "spleen",
    "Lung": "lung",
    "Liver": "liver",
    "Stomach": "stomach",
    "Pancreas": "pancreas",
    "Esophagus_Mucosa": "oesophagus",
    "Terminal_Ileum": "ileum",
}

#: How the run came by its region, in one word, for the same tag.
REGION_ORIGIN = {True: "inferred", False: "form default", None: "chosen"}


def region_short(region: str | None) -> str:
    if not region:
        return "none"
    return REGION_SHORT.get(region, region.replace("_", " ").lower())


def region_tag(f: Facts) -> dict[str, Any]:
    """"region: ileum, inferred" -- the three-word tag that replaces the
    expression sentence in the box. The long sentence lives in the tooltip and
    in `expression.shown`, which the JSON and the summary still carry."""
    origin = REGION_ORIGIN[f.region_is_disease_tissue]
    return {
        "region": f.region,
        "short": region_short(f.region),
        "origin": origin,
        "shown": f"region: {region_short(f.region)}, {origin}",
        "tooltip": _region_words(f),
    }


def _region_words(f: Facts) -> str:
    """"<region>, the run's chosen tissue" plus the disease-tissue statement
    when one can be made."""
    where = f.region or "the run's region"
    s = f"{where}, the run's chosen tissue"
    if f.region_is_disease_tissue is True:
        s += " (a disease tissue for this contrast)"
    elif f.region_is_disease_tissue is False:
        s += " -- not a disease tissue for this contrast"
    return s


def expression_summary(f: Facts, t: Thresholds) -> dict[str, Any]:
    """The specificity line as the walkthrough asked for it: two absolute TPMs,
    and a "concentrated in" word only above the documented ratio AND in a
    disease tissue. Display only; the verdict never reads it."""
    ratio = (f.region_tpm / f.panel_median_tpm
             if f.region_tpm is not None and f.panel_median_tpm else None)
    # Third guard: a gene whose whole-blood or spleen TPM is at or above the
    # region's is not "concentrated" in the region however high the panel
    # ratio -- CXCL1 reads 11x the panel median in the ileum with a spleen TPM
    # ten times the ileum's. The blood-borne case the walkthrough named.
    immune = immune_origin_hint(f, t)["suggested"] is True
    specific = bool(ratio is not None and ratio > t.specific_ratio
                    and f.region_is_disease_tissue is True and not immune)
    if f.region_tpm is None:
        shown = "no GTEx TPM for this gene in the run's region"
    elif f.panel_median_tpm is None:
        shown = f"{f.region_tpm:.4g} TPM in {_region_words(f)}"
    else:
        n = f"{f.n_tissues:d}" if f.n_tissues else "the GTEx"
        shown = (f"{f.region_tpm:.4g} TPM in {_region_words(f)}; median across "
                 f"{n} tissues {f.panel_median_tpm:.4g} TPM")
        if specific:
            shown += (f"; concentrated in {f.region} ({ratio:.1f}x the panel median, "
                      f"above the documented {t.specific_ratio:g}x line)")
    return {
        "region": f.region,
        "region_tpm": f.region_tpm,
        "panel_median_tpm": f.panel_median_tpm,
        "n_tissues": f.n_tissues,
        "ratio": ratio,
        "region_is_disease_tissue": f.region_is_disease_tissue,
        "specific": specific,
        "specific_ratio_threshold": t.specific_ratio,
        "blood_or_spleen_at_or_above_region": immune,
        "shown": shown,
        "note": ("absolute TPMs, not a fold: a ratio against the panel median reads "
                 "as tissue-specific for anything blood-borne in a blood-rich tissue. "
                 "'Concentrated' is said only above the threshold, in a disease "
                 "tissue, and when neither whole blood nor spleen is at or above "
                 "the region's TPM."),
    }


def disease_area_match(f: Facts) -> dict[str, Any]:
    """Display aid beside the phenotype criterion: do any of the abnormal mouse
    terms fall in the contrast's disease area? Keyword mapping over the term
    labels (`DISEASE_AREAS`). Never a criterion, never in the verdict."""
    area = disease_area_for(f.disease_text)
    n_met = f.n_abnormal_phenotypes
    base = {"disease_area": area, "mapping_version": mapping_version(),
            "note": "display aid, not a criterion (docs/RECOMMEND-SPEC.md)",
            "n_met_terms": n_met}
    if area is None:
        return {**base, "matched": None, "matching_terms": [],
                "shown": "disease-area match: no mapping for this contrast",
                "short": "disease-area terms: no mapping",
                "tooltip": "no disease-area mapping for this contrast's text "
                           "(recommend.DISEASE_AREAS); a display aid, not a criterion"}
    spec = DISEASE_AREAS[area]["areas"]
    matching: list[dict[str, str]] = []
    for term in f.mouse_phenotypes:
        low = term.lower()
        if _NO_ABNORMAL in low:
            continue
        for sub_area, pats in spec.items():
            if any(p in low for p in pats):
                matching.append({"term": term, "area": sub_area})
                break
    if matching:
        shown = (f"disease-area match: yes ({area}) -- "
                 + "; ".join(f"{m['term']} [{m['area']}]" for m in matching))
    elif n_met:
        shown = (f"disease-area match: no -- met on {n_met} term(s), none in the "
                 f"disease area ({area}: {', '.join(spec)})")
    else:
        shown = f"disease-area match: no abnormal terms to match ({area})"
    # The trimmed box prints the count only; the terms and their buckets move
    # into the tooltip (Puck, 18:42 PDT: "disease-area match reduced to
    # 'disease-area terms: n'").
    short = f"disease-area terms: {len(matching)}"
    if matching:
        tooltip = (f"{area}: "
                   + "; ".join(f"{m['term']} [{m['area']}]" for m in matching)
                   + ". Display aid, not a criterion.")
    else:
        tooltip = (f"{area}: none of the {n_met or 0} abnormal term(s) is in this "
                   f"disease area ({', '.join(spec)}). Display aid, not a criterion.")
    return {**base, "matched": bool(matching), "matching_terms": matching,
            "areas": list(spec), "shown": shown, "short": short, "tooltip": tooltip}


def immune_origin_hint(f: Facts, t: Thresholds | None = None) -> dict[str, Any]:
    """For the summary prompt (walkthrough item d): does the gene's GTEx
    profile suggest the change in an inflamed-tissue biopsy may come from
    infiltrating immune cells? The rule is stated with the answer: whole blood
    or spleen median TPM is at or above the expression floor AND at or above
    the region's own TPM. A hint from the facts the drawer carries, not a cell
    type call."""
    t = t or current_thresholds()
    reasons: list[str] = []
    for label, v in (("whole blood", f.whole_blood_tpm), ("spleen", f.spleen_tpm)):
        if v is None or f.region_tpm is None:
            continue
        if v >= t.min_tpm and v >= f.region_tpm:
            reasons.append(f"{label} {v:.4g} TPM >= {f.region or 'region'} {f.region_tpm:.4g} TPM")
    known = f.whole_blood_tpm is not None or f.spleen_tpm is not None
    return {
        "suggested": bool(reasons) if known else None,
        "reasons": reasons,
        "rule": (f"GTEx whole-blood or spleen median TPM >= {t.min_tpm:g} and >= the "
                 f"region's TPM"),
        "inflamed_tissue_contrast": disease_area_for(f.disease_text) is not None,
    }


def _expressed(f: Facts, t: Thresholds) -> dict[str, Any]:
    thr = f"GTEx TPM >= {t.min_tpm:g} in the run's region"
    if f.region_tpm is not None:
        return _criterion(
            "expressed", "expressed", MET if f.region_tpm >= t.min_tpm else NOT_MET,
            f.region_tpm, f"{f.region_tpm:.4g} TPM in {_region_words(f)}", thr,
            "GTEx median TPM", bare=f"{f.region_tpm:.4g} TPM")
    if f.tissue_distribution is not None:
        return _criterion(
            "expressed", "expressed",
            MET if f.tissue_distribution > 0 else NOT_MET, f.tissue_distribution,
            f"no GTEx number; Open Targets tissueDistribution {f.tissue_distribution:+.2f}",
            thr + " (fallback: Open Targets tissueDistribution > 0)",
            "Open Targets prioritisation.tissueDistribution",
            bare=f"tissue distribution {f.tissue_distribution:+.2f}")
    return _criterion("expressed", "expressed", NO_DATA, None,
                      "no GTEx TPM and no tissueDistribution key for this gene",
                      thr, "GTEx median TPM", bare="no TPM")


def _dysregulated(f: Facts, t: Thresholds) -> dict[str, Any]:
    thr = f"|logFC| >= {t.min_abs_logfc:g} and adjusted p <= {t.max_q:g}"
    if f.logfc is None or f.padj is None:
        missing = [n for n, v in (("logFC", f.logfc), ("adjusted p", f.padj)) if v is None]
        return _criterion("dysregulated", "dysregulated in the study", NO_DATA,
                          {"logfc": f.logfc, "padj": f.padj},
                          f"no {' or '.join(missing)} in the submitted table", thr,
                          "submitted DE table",
                          bare=f"no {' or '.join(missing)}")
    # The threshold `thr` above keeps `{t.max_q:g}` -- "adjusted p <= 0.05" is
    # the RULE as Puck wrote it and `criteria_spec` prints the same string, so
    # it is not a measured p and is not reformatted (Puck, 2026-10-19).
    # `value` keeps both numbers exactly; only the two sentences are formatted.
    ok = abs(f.logfc) >= t.min_abs_logfc and f.padj <= t.max_q
    return _criterion("dysregulated", "dysregulated in the study", MET if ok else NOT_MET,
                      {"logfc": f.logfc, "padj": f.padj},
                      f"logFC {f.logfc:g}, adjusted p {format_p(f.padj)}", thr,
                      "submitted DE table",
                      bare=f"logFC {f.logfc:+g}, p {format_p(f.padj)}")


def _phenotype(f: Facts, t: Thresholds) -> dict[str, Any]:
    thr = f"mouse phenotype ring > 0, or >= {t.min_phenotypes:d} abnormal mouse term(s)"
    n = f.n_abnormal_phenotypes
    if f.rodent_ring is not None:
        if f.rodent_ring > 0 or (n is not None and n >= t.min_phenotypes):
            shown = (f"phenotyped, {n} abnormal term(s) on record" if n is not None
                     else "phenotyped, abnormal phenotype on record")
            return _criterion("phenotype", "phenotype in a study", MET,
                              {"ring": f.rodent_ring, "n_abnormal": n}, shown, thr,
                              "Open Targets mousePhenotypes",
                              bare=(f"{n} abnormal term{'' if n == 1 else 's'}"
                                    if n is not None else "abnormal term on record"))
        return _criterion("phenotype", "phenotype in a study", NOT_MET,
                          {"ring": f.rodent_ring, "n_abnormal": n},
                          "phenotyped, no abnormal phenotype detected", thr,
                          "Open Targets mousePhenotypes",
                          bare="0 abnormal terms")
    if n is not None and n >= t.min_phenotypes:
        return _criterion("phenotype", "phenotype in a study", MET,
                          {"ring": None, "n_abnormal": n},
                          f"{n} abnormal mouse term(s) on record (ring hatched)", thr,
                          "Open Targets mousePhenotypes",
                          bare=f"{n} abnormal term{'' if n == 1 else 's'}")
    return _criterion("phenotype", "phenotype in a study", NO_DATA,
                      {"ring": None, "n_abnormal": n},
                      "not phenotyped -- no mouse phenotype records, which is not 'no phenotype'",
                      thr, "Open Targets mousePhenotypes",
                      bare="not phenotyped")


def criteria_spec(t: Thresholds | None = None) -> list[dict[str, Any]]:
    """The three criteria and their bounds, with no gene in front of them.

    v4.2, Puck 2026-10-19: "on the top of the table page the main part should
    say there are 3 go no go criteria, and a numbered list. and the bounds we
    are looking for in them."

    The `met_when` strings are built from the same `Thresholds` the per-gene
    criterion functions above build theirs from, so the list at the top of the
    Table tab and the line inside a drawer can never state two different
    bounds. Moving `RECOMMEND_MIN_TPM` moves both.
    """
    t = t or current_thresholds()
    return [
        {"n": 1, "key": "expressed", "name": "Expressed",
         "met_when": (f"GTEx median TPM >= {t.min_tpm:g} in the run's region "
                      f"(fallback: tissue-distribution ring > 0 if GTEx is absent)"),
         "source": "GTEx median TPM, Open Targets prioritisation"},
        {"n": 2, "key": "dysregulated", "name": "Dysregulated in this study",
         "met_when": f"|logFC| >= {t.min_abs_logfc:g} and adjusted p <= {t.max_q:g}",
         "source": "the submitted DE table"},
        # One name for one criterion. Review of 2026-10-20, item 3: this
        # read "Phenotype in a model" on two surfaces (the criteria block and
        # the flow page) while the four `_criterion` calls below, the 11 baked
        # drawers, the README and Puck's own sentence -- "we need the gene to
        # be expressed, and also misregulated in the study, and have a strong
        # phenotype in a study" (2026-10-18) -- all said "a study". His words
        # win: one name, 37 uses against 1.
        {"n": 3, "key": "phenotype", "name": "Phenotype in a study",
         "met_when": (f"mouse-KO ring > 0, or >= {t.min_phenotypes:d} abnormal "
                      f"mouse phenotype term(s)"),
         "source": "Open Targets mousePhenotypes"},
    ]


def criteria_block(t: Thresholds | None = None) -> dict[str, Any]:
    """`criteria_spec` plus the sentences that frame it: how the verdict
    follows from the three, and the one thing that never changes it."""
    t = t or current_thresholds()
    return {
        "heading": "GO / NO-GO: three criteria, all must hold",
        "criteria": criteria_spec(t),
        "go": "GO when all three are met.",
        "no_go": "NO-GO when any one is not met.",
        "insufficient": "INSUFFICIENT DATA when none is not met but any is no data.",
        # Puck, 2026-10-18: "no toxicity data or low druggability does not
        # exclude". Said at the top of the table because a reader looking at a
        # safety ring beside a GO has to know it was not weighed.
        "never_a_criterion": "Druggability, safety and pipeline never change "
                             "the verdict.",
        "rule_version": rule_version(t),
        "thresholds": t.as_dict(),
    }


def recommend(f: Facts, t: Thresholds | None = None) -> dict[str, Any]:
    """The verdict and the three criteria, as the drawer prints them."""
    t = t or current_thresholds()
    crit = [_expressed(f, t), _dysregulated(f, t), _phenotype(f, t)]
    statuses = [c["status"] for c in crit]
    if NOT_MET in statuses:
        verdict = NO_GO
    elif NO_DATA in statuses:
        verdict = INSUFFICIENT
    else:
        verdict = GO
    area = disease_area_match(f)
    # Beside the phenotype criterion, as the walkthrough asked -- on the
    # criterion dict so the page prints it on that line, and once more at the
    # top level so a reader of the JSON finds it without knowing the layout.
    crit[2]["disease_area"] = area
    return {
        "verdict": verdict,
        "criteria": crit,
        "caveats": caveats(f),
        "modality": modality(f),
        "expression": expression_summary(f, t),
        "region_tag": region_tag(f),
        "disease_area": area,
        "thresholds": t.as_dict(),
        # The box prints `rule_short` on one line and hangs `rule_tooltip` off
        # its title attribute (Puck, 18:42 PDT: "the rule text collapses to one
        # line with a tooltip for thresholds"). `rule_text` is unchanged: the
        # summary prompt and the drawer's long form still carry it.
        "rule_short": f"rule: {RULE_TEXT} — {RULE_NOTE}",
        "rule_tooltip": f"Thresholds: {t.sentence()}. Rule version "
                        f"{rule_version(t)}. {RULE_NOTE}.",
        "rule_text": f"Rule: {RULE_TEXT}; thresholds: {t.sentence()}",
        "rule_version": rule_version(t),
        "mode": "rule",
        "note": RULE_NOTE,
        "basis": "Puck's explicit rule (2026-10-18), separate from the evidence rings",
    }


def recommend_drawer(d: dict[str, Any], t: Thresholds | None = None,
                     manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    return recommend(facts_from_drawer(d, manifest), t)


__all__ = ["Facts", "Thresholds", "current_thresholds", "facts_from_drawer",
           "recommend", "recommend_drawer", "rule_version", "region_tpm_from_detail",
           "panel_median_from_detail", "region_is_disease_tissue_from_manifest",
           "contrast_text_from_manifest", "expression_summary", "disease_area_match",
           "disease_area_for", "immune_origin_hint", "mapping_version",
           "region_short", "region_tag", "caveats", "modality",
           "DISEASE_AREAS", "SILENT_DEFAULT_REGION", "REGION_SHORT",
           "RULE_TEXT", "RULE_NOTE", "GO", "NO_GO", "INSUFFICIENT",
           "MET", "NOT_MET", "NO_DATA"]
