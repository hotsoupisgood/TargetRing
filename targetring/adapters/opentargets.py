"""Open Targets adapter: prioritisation, clinical pipeline, mouse phenotypes.

The query text is pinned in this module (QA B1) and the startup contract test in
`tests/test_contract.py` issues it verbatim against a known gene, failing the
run on any non-200 or any `errors` key.

Join discipline (QA B2, the highest-risk defect in the plan): `targets()`
returns HTTP 200 with a *shorter* array when it cannot find an id, with the
survivors in their original relative order. Zipping by index therefore shifts
every gene after the gap. This module builds `{returned_id: record}` and looks
up by requested id; anything absent becomes an explicit `missing` record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import OPENTARGETS_URL
from ..http import Http, graphql
from ..states import Cell, failed, no_data, not_applicable, value

# --- the pinned production query -------------------------------------------
# COUNCIL D1's published version of this query is invalid: `diseases{disease}`
# needs a sub-selection and returns HTTP 400. Corrected to `disease{id name}`
# and re-verified live 2026-09-24.
TARGET_QUERY = """query TargetRings($ids:[String!]!){
  targets(ensemblIds:$ids){
    id
    approvedSymbol
    biotype
    prioritisation{items{key value}}
    tractability{label modality value}
    safetyLiabilities{event datasource url}
    isEssential
    obsoleteSymbols{label}
    mousePhenotypes{modelPhenotypeLabel modelPhenotypeId targetInModelMgiId}
    drugAndClinicalCandidates{
      count
      rows{
        maxClinicalStage
        drug{id name drugType maximumClinicalStage}
        diseases{diseaseFromSource disease{id name}}
        clinicalReports{
          trialPhase clinicalStage trialOverallStatus trialStartDate
          trialStopReasonCategories source url
        }
      }
    }
  }
}"""

# COUNCIL M1 / D4 Ring A. A string enum, mapped by explicit lookup. `parseInt`
# on "PHASE_1_2" is the bug this table exists to prevent.
CLINICAL_STAGE_ORDINAL: dict[str, int] = {
    "PHASE_1": 2,
    "PHASE_1_2": 2,
    "PHASE_2": 3,
    "PHASE_2_3": 3,
    "PHASE_3": 4,
    "PHASE_4": 5,
    "APPROVAL": 5,
}
STAGE_LABEL = {0: "none", 1: "preclinical", 2: "Phase 1", 3: "Phase 2", 4: "Phase 3", 5: "Approved"}

# --- COUNCIL V1: indication attribution -------------------------------------
# `clinical_phase` took max(maxClinicalStage) over every row with no test that
# the drug's indication had anything to do with the target, so EPHA2 rendered
# "Approved (3 candidates)" on vandetanib (medullary thyroid, RET/VEGFR),
# dasatinib (CML, BCR-ABL1) and regorafenib (colorectal) -- at the brightest end
# of the colormap, with no approved EPHA2 drug in existence. D2 was implemented
# as a warning in the drawer; this makes it a constraint on the ring.
#
# The test: does Open Targets hold *mechanistic* evidence linking this target to
# the disease the drug is indicated for? `associatedDiseases(Bs: [...])` answers
# exactly that, filtered to the diseases we care about, batched across targets.
#
# Which datatypes count, and why only these two. Calibrated against known
# answers on 2026-09-24 (`/tmp/tr2/calib.py`, `/tmp/tr2/calib2.py`), not chosen
# from the list of names:
#
#   `clinical`         IS the drug. Counting it makes every indication
#                      attributable by construction.
#   `literature`       co-mention, inflated by the trials themselves. EPHA2 x
#                      colorectal scores literature 0.781 with no genetics.
#   `rna_expression`   being expressed where a disease occurs is not attribution.
#   `affected_pathway` a property of the pathway, not of the target-disease
#                      pair. EPHA2 x squamous cell lung carcinoma = 0.760 purely
#                      through Eph receptor signalling.
#   `somatic_mutation` does not discriminate. BRAF returns 0.456 for thirty-odd
#                      unrelated tumour types (gallbladder, myelodysplastic
#                      syndrome, rhabdomyosarcoma, adrenal cortex carcinoma...)
#                      -- a pan-cancer "BRAF is a cancer gene" floor. It would
#                      re-admit sorafenib x renal cell carcinoma, which is the
#                      case PLAN §2 names.
#   `animal_model`     cross-species, and EPHA2 reaches 0.499 on uveal melanoma.
#
# Human genetic evidence is the only datatype that is non-circular with respect
# to the drug AND specific to the target-disease pair. Measured separations:
#
#   EPHA2  x colorectal / CML / med.thyroid / SCC-lung   genetics 0.000  -> no
#   BRAF   x renal cell carcinoma (SORAFENIB)            genetics 0.000  -> no
#   BRAF   x hepatocellular carcinoma (SORAFENIB)        genetics 0.000  -> no
#   BRAF   x melanoma (VEMURAFENIB)                      genetics 0.699  -> yes
#   EGFR   x non-small cell lung carcinoma (AFATINIB)    genetics 0.75   -> yes
#   KCNH2  x atrial fibrillation (DOFETILIDE)            genetics 0.598  -> yes
#   PCSK9  x familial hypercholesterolemia               genetics 0.859  -> yes
#   HMGCR  x cardiovascular disorder (PRAVASTATIN)       genetics 0.46   -> yes
#
# The cost, stated because it is real: a target drugged only for indications
# with no human genetic support renders `not_applicable` rather than a phase.
# That is a visible, labelled state and it makes the gene *ineligible* for the
# headline -- conservative, never flattering. See `docs/V2-REPORT.md`.
GENETIC_ATTRIBUTION_DATATYPES = frozenset({
    "genetic_association", "genetic_literature",
})
# Kept under the old name too: the council's V1 wording is "mechanistic".
MECHANISTIC_DATATYPES = GENETIC_ATTRIBUTION_DATATYPES
# Stated, not tuned. EPHA2 x melanoma carries genetic_association = 0.0 exactly
# and BRAF x cardiofaciocutaneous syndrome somatic_mutation = 0.0; a trace-level
# score is not evidence of attribution.
ATTRIBUTION_MIN_SCORE = 0.1

ASSOCIATION_QUERY = """query Attribution($ids:[String!]!,$bs:[String!]!,$size:Int!){
  targets(ensemblIds:$ids){
    id
    associatedDiseases(page:{index:0,size:$size}, Bs:$bs){
      count
      rows{ disease{id name} score datatypeScores{id score} }
    }
  }
}"""

# COUNCIL V1 part (ii). Open Targets has no on-target/anti-target axis, so hERG
# reads as druggable, clinically validated and actively trialled while its own
# `safetyLiabilities` -- fetched in the same call -- are Torsades de Pointes,
# Increased Mortality and Reduced survival.
#
# A stated keyword list over the `safetyLiabilities` event strings, scoped to
# cardiac conduction, cardiotoxicity and mortality, which is the scope the
# council named. Matched terms travel with the flag so the claim is auditable
# and the free-text source stays visible (AGENTS.md rule 5: these strings are
# data, not instructions).
SEVERE_LIABILITY_TERMS: tuple[str, ...] = (
    "torsade", "qt interval", "long qt", "short qt", "qt prolong",
    "prolongation of qt", "mortality", "survival", "cardiomyopathy",
    "cardiac arrest", "heart disease", "heart failure", "arrhythmi",
    "atrial fibrillation", "ventricular fibrillation", "ventricular tachycardia",
)

# COUNCIL D4 Ring B. Only AACT rows carry status; TTD / EMA rows have
# trialOverallStatus None and would read as inactive.
ACTIVE_STATUSES = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "AVAILABLE"}
FOR_CAUSE_CATEGORIES = {"Negative", "Safety_Sideeffects"}
STATUS_SOURCE = "AACT"

# QA S4: BRAF's mousePhenotypes includes this label. Counting rows is not
# counting abnormal phenotypes, and a gene with one such record would otherwise
# outrank a gene with none.
NO_ABNORMAL = "no abnormal phenotype detected"

# The four prioritisation keys behind each ring. Stated reductions, not field
# reads (QA B1: there is no single scalar tractability value).
DRUGGABILITY_KEYS = ("hasLigand", "hasPocket", "hasSmallMoleculeBinder",
                     "hasHighQualityChemicalProbes")
SAFETY_KEYS = ("hasSafetyEvent", "geneEssentiality", "geneticConstraint", "isCancerDriverGene")

# COUNCIL V4 / V8. Three prioritisation keys are emitted by Open Targets *only*
# when the answer is one particular value. Dropping an absent key from a mean is
# arithmetically identical to imputing it at the mean of the present keys, so
# "never impute" held at the cell level and was broken inside the cell.
#
# Measured live 2026-09-24 over 24 targets (`/tmp/tr2/probe_prio.py`; the value
# census is reproduced in `tests/test_adapters.py::TestSparsePrioritisationKeys`):
#
#     key                            present  absent  observed values
#     hasSafetyEvent                       9      15  -1 x9
#     isCancerDriverGene                   5      19  -1 x5
#     hasHighQualityChemicalProbes         5      19  +1 x5
#     hasLigand                           24       0   0 x11, 1 x13
#     hasPocket                           24       0   0 x14, 1 x10
#     hasSmallMoleculeBinder              24       0   0 x8,  1 x16
#     geneEssentiality                    24       0  -1 x4,  0 x20
#     geneticConstraint                   24       0  continuous
#
# The three sparse keys are binary fields with one end left unwritten, and that
# end is 0.0 in every case: {-1, 0} for the two liability flags, {0, 1} for the
# probe flag. So absence is a *measured* value, not a missing one, and is mapped
# to 0.0 -- which makes the denominator 4 for every gene and stops it encoding
# the answer.
#
# What this does NOT claim: that a gene with no `hasSafetyEvent` is safe. Open
# Targets not holding a safety-event record is a fact about Open Targets. The
# ring label and the cell detail say so; see COUNCIL V4 and `docs/V2-REPORT.md`.
SPARSE_KEY_ABSENT_VALUE: dict[str, float] = {
    "hasSafetyEvent": 0.0,          # absent  = no safety event in OT's sources
    "isCancerDriverGene": 0.0,      # absent  = not in OT's cancer-driver list
    "hasHighQualityChemicalProbes": 0.0,  # absent = no high-quality probe known
}

# Keys Open Targets scores for every target it has a prioritisation record for.
# If none of these is present the gene has no prioritisation record at all, and
# that is genuine `no_data` rather than a sparse encoding.
DENSE_PRIORITISATION_KEYS = (
    "geneEssentiality", "geneticConstraint", "hasLigand", "hasPocket",
    "hasSmallMoleculeBinder", "tissueSpecificity", "tissueDistribution",
)


@dataclass
class OTRecord:
    ensembl_id: str
    found: bool
    approved_symbol: str | None = None
    biotype: str | None = None
    prioritisation: dict[str, float] = field(default_factory=dict)
    tractability: list[dict[str, Any]] = field(default_factory=list)
    safety_liabilities: list[dict[str, Any]] = field(default_factory=list)
    is_essential: bool | None = None
    mouse_phenotypes: list[dict[str, Any]] = field(default_factory=list)
    drug_count: int | None = None
    drug_rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    # COUNCIL V1: {disease_id: {datatype: score}} for the diseases this target's
    # drugs are indicated for. None means the attribution query was not run or
    # did not answer -- which is not the same as "no association".
    associations: dict[str, dict[str, float]] | None = None
    associations_error: str | None = None

    def indication_disease_ids(self) -> list[str]:
        """Every disease id any of this target's drug rows is indicated for."""
        out: list[str] = []
        for row in self.drug_rows:
            for d in row.get("diseases") or []:
                did = (d.get("disease") or {}).get("id")
                if did and did not in out:
                    out.append(did)
        return out


def _as_float(raw: Any) -> float | None:
    """COUNCIL M2, measured: prioritisation values arrive as strings. Calling
    round() on one raises TypeError. Cast explicitly, and refuse silently."""
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


class OpenTargetsAdapter:
    source = "opentargets.target"

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache
        self.failure: str | None = None

    async def fetch(self, ensembl_ids: list[str]) -> dict[str, OTRecord]:
        wanted = list(dict.fromkeys(ensembl_ids))
        out: dict[str, OTRecord] = {}
        pending: list[str] = []
        for eid in wanted:
            hit = self.cache.get(self.source, f"rings|{eid}")
            if hit is None:
                pending.append(eid)
            else:
                out[eid] = _from_payload(eid, hit)

        if pending:
            try:
                data = await graphql(self.http, OPENTARGETS_URL, TARGET_QUERY, {"ids": pending})
            except Exception as e:  # noqa: BLE001 -- recorded, then surfaced as `failed`
                self.failure = str(e)
                for eid in pending:
                    out[eid] = OTRecord(eid, found=False, error=str(e))
                return {eid: out[eid] for eid in wanted}

            returned = data["targets"]
            by_id = {t["id"]: t for t in returned}

            # QA B2: the response must be a subset of the request. A superset
            # means the join key is not what we think it is.
            extra = set(by_id) - set(pending)
            if extra:
                raise AssertionError(
                    f"Open Targets returned ids that were not requested: {sorted(extra)!r}. "
                    f"Refusing to join."
                )
            for eid in pending:
                payload = by_id.get(eid)
                self.cache.put(self.source, f"rings|{eid}", payload)
                out[eid] = _from_payload(eid, payload)

        return {eid: out[eid] for eid in wanted}

    async def fetch_attribution(self, records: dict[str, OTRecord]) -> None:
        """COUNCIL V1: fill `associations` for every record that has drug rows.

        One query per target, cached, and only for targets that actually have
        drugs -- on a typical DE list most genes have none, so this is cheap.
        Batching the Bs union across targets would need paging to stay exact
        (BRAF has 3,139 associations), and an exact answer matters more here
        than a round trip: an unattributed indication is about to change a ring.

        A failure is recorded, never swallowed. `associations = None` means the
        question was not answered, and `clinical_phase_cell` reports that as
        `failed` rather than guessing in either direction.
        """
        import asyncio

        async def one(eid: str, rec: OTRecord) -> None:
            bs = rec.indication_disease_ids()
            if not bs:
                rec.associations = {}
                return
            key = f"assoc|{eid}|" + ",".join(sorted(bs))
            hit = self.cache.get(self.source, key)
            if hit is None:
                try:
                    data = await graphql(
                        self.http, OPENTARGETS_URL, ASSOCIATION_QUERY,
                        {"ids": [eid], "bs": bs, "size": min(len(bs) + 5, 500)},
                    )
                except Exception as e:  # noqa: BLE001
                    rec.associations_error = str(e)
                    return
                rows = (data["targets"][0]["associatedDiseases"]["rows"]
                        if data.get("targets") else [])
                count = (data["targets"][0]["associatedDiseases"]["count"]
                         if data.get("targets") else 0)
                if count > len(rows):
                    # The filtered page did not hold every row we asked about.
                    # An incomplete answer here silently reads as "unattributed".
                    rec.associations_error = (
                        f"associatedDiseases returned {len(rows)} of {count} filtered rows; "
                        f"the attribution test would be incomplete"
                    )
                    return
                hit = {
                    r["disease"]["id"]: {x["id"]: float(x["score"])
                                         for x in r["datatypeScores"]}
                    for r in rows
                }
                self.cache.put(self.source, key, hit)
            rec.associations = hit

        targets = [(eid, r) for eid, r in records.items() if r.found and r.drug_rows]
        if targets:
            await asyncio.gather(*(one(eid, r) for eid, r in targets))
        for r in records.values():
            if r.found and not r.drug_rows and r.associations is None:
                r.associations = {}


def _from_payload(eid: str, payload: dict[str, Any] | None) -> OTRecord:
    if payload is None:
        # Requested, absent from the response. Explicitly missing, never
        # omitted and never defaulted (QA B2).
        return OTRecord(eid, found=False)
    prio = {
        i["key"]: v
        for i in (payload.get("prioritisation") or {}).get("items", [])
        if (v := _as_float(i.get("value"))) is not None
    }
    dac = payload.get("drugAndClinicalCandidates") or {}
    return OTRecord(
        ensembl_id=eid,
        found=True,
        approved_symbol=payload.get("approvedSymbol"),
        biotype=payload.get("biotype"),
        prioritisation=prio,
        tractability=payload.get("tractability") or [],
        safety_liabilities=payload.get("safetyLiabilities") or [],
        is_essential=payload.get("isEssential"),
        mouse_phenotypes=payload.get("mousePhenotypes") or [],
        drug_count=dac.get("count"),
        drug_rows=dac.get("rows") or [],
    )


# --- ring reductions --------------------------------------------------------


def has_prioritisation_record(rec: OTRecord) -> bool:
    """Does Open Targets score this target at all?

    The discriminator between "sparsely encoded" and "nothing here". Every key in
    DENSE_PRIORITISATION_KEYS was present for 24/24 targets in the live census,
    so if none of them is present the target has no prioritisation record and
    the sparse-key defaults must not be applied to it.
    """
    return any(k in rec.prioritisation for k in DENSE_PRIORITISATION_KEYS)


def _mean_of_keys(rec: OTRecord, keys: tuple[str, ...], label: str) -> Cell:
    """COUNCIL V4/V8: a 4-key mean for every gene, with absence decided per key.

    v1 averaged whichever keys happened to be present. Because `hasSafetyEvent`
    and `isCancerDriverGene` exist only when the news is bad, a clean gene's
    "Safety" ring was the mean of essentiality and constraint alone -- a
    constraint number carrying no safety information -- and was rendered
    identically to a 4-key mean. On druggability the mirror image made
    `hasHighQualityChemicalProbes` arithmetically inert: BRAF mean(1,1,1,1) and
    PCSK9 mean(1,1,1) were both exactly 1.0.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if not has_prioritisation_record(rec):
        return no_data(
            f"Open Targets holds no prioritisation record for this target, so none of the "
            f"{label} keys can be read. This is absence of a record, not a clean result."
        )

    used: dict[str, float] = {}
    inferred: list[str] = []
    unreadable: list[str] = []
    for k in keys:
        if k in rec.prioritisation:
            used[k] = rec.prioritisation[k]
        elif k in SPARSE_KEY_ABSENT_VALUE:
            used[k] = SPARSE_KEY_ABSENT_VALUE[k]
            inferred.append(k)
        else:
            unreadable.append(k)

    if unreadable:
        # A dense key went missing. The mean would have a different denominator
        # from every other gene's, which is exactly the V4 defect, so refuse.
        return no_data(
            f"{label}: {', '.join(unreadable)} absent for this gene and not a sparse-encoded "
            f"key, so a {len(keys)}-key mean cannot be formed. Refusing a shorter mean — the "
            f"denominator would encode the answer (COUNCIL V4)."
        )

    detail = f"mean of {len(keys)}/{len(keys)} keys: " + ", ".join(
        f"{k}={used[k]:+.2f}" + ("*" if k in inferred else "") for k in keys
    )
    if inferred:
        detail += (
            f" | * = key absent from the response. Open Targets emits "
            f"{', '.join(inferred)} only at the non-zero end, so absence is that field's "
            f"other value, not a missing measurement. Absence of a record is not evidence."
        )
    return value(
        sum(used.values()) / len(keys),
        detail,
        raw={"keys": dict(used), "inferred_from_absence": inferred},
    )


def druggability_cell(rec: OTRecord) -> Cell:
    return _mean_of_keys(rec, DRUGGABILITY_KEYS, "druggability")


def safety_cell(rec: OTRecord) -> Cell:
    return _mean_of_keys(rec, SAFETY_KEYS, "safety")


def mechanistic_evidence(rec: OTRecord, disease_id: str) -> dict[str, float]:
    """The non-circular datatype scores linking this target to this disease."""
    dts = (rec.associations or {}).get(disease_id) or {}
    return {
        k: v for k, v in dts.items()
        if k in MECHANISTIC_DATATYPES and v >= ATTRIBUTION_MIN_SCORE
    }


def attributable_indications(rec: OTRecord, row: dict[str, Any]) -> list[dict[str, Any]]:
    """The diseases in one drug row that are mechanistically linked to the target."""
    out = []
    for d in row.get("diseases") or []:
        did = (d.get("disease") or {}).get("id")
        if not did:
            continue
        ev = mechanistic_evidence(rec, did)
        if ev:
            out.append({
                "id": did,
                "name": (d.get("disease") or {}).get("name") or d.get("diseaseFromSource"),
                "evidence": ev,
            })
    return out


def severe_liabilities(rec: OTRecord) -> list[dict[str, str]]:
    """COUNCIL V1 (ii). Cardiac / mortality safety liabilities on record.

    Free text from Open Targets matched against a stated keyword list, with the
    matched term carried so the claim is auditable. This is *not* an
    "anti-target" verdict -- see `docs/V2-REPORT.md` for why that name was not
    used. It is a statement about what Open Targets records.
    """
    out = []
    for r in rec.safety_liabilities:
        event = str(r.get("event") or "")
        low = event.lower()
        hit = next((t for t in SEVERE_LIABILITY_TERMS if t in low), None)
        if hit:
            out.append({"event": event, "matched": hit,
                        "datasource": str(r.get("datasource") or "")})
    return out


def clinical_phase_cell(rec: OTRecord) -> Cell:
    """COUNCIL D4 Ring A / COUNCIL V1 — furthest phase reached *for this target*.

    v1 took `max(maxClinicalStage)` over every row, so EPHA2 rendered
    "Approved (3 candidates)" at the brightest end of the colormap on the
    strength of vandetanib, dasatinib and regorafenib -- three approved drugs,
    none of them an EPHA2 drug -- and a target with no approved drug rendered
    identically to one with three.

    A stage now counts only when at least one of the row's diseases carries
    mechanistic (non-clinical, non-co-mention) evidence linking it to this
    target. A target whose drugs are all unattributed is `not_applicable`: a
    distinct state, hatched and off-colormap, not a lower number that would read
    as "less validated" on the same scale.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if rec.drug_count is None:
        return no_data("drugAndClinicalCandidates absent")
    if rec.drug_count == 0:
        # COUNCIL D5: OT distinguishes a meaningful zero from no data. A target
        # record that exists and reports 0 candidates is genuinely undrugged.
        return value(0.0, "target record exists, 0 drug or clinical candidates", raw=0)

    if rec.associations_error is not None:
        # We asked and could not find out. Not "unattributed", not a phase.
        return failed(
            f"{rec.drug_count} candidates, but the indication-attribution query failed, so "
            f"whether any of them targets a disease attributable to this gene is unknown: "
            f"{rec.associations_error[:160]}"
        )
    if rec.associations is None:
        return no_data(
            f"{rec.drug_count} candidates, indication attribution not evaluated for this run"
        )

    staged = [r for r in rec.drug_rows
              if (r.get("maxClinicalStage") or "") in CLINICAL_STAGE_ORDINAL]
    if not staged:
        # v1 returned value(1.0) — "preclinical" — here, which quietly absorbed
        # any new enum value Open Targets might introduce. An unmapped stage is
        # a fact we do not have, not a low one.
        seen = sorted({str(r.get("maxClinicalStage")) for r in rec.drug_rows})
        return no_data(
            f"{rec.drug_count} candidates, none with a clinical stage this build maps. "
            f"maxClinicalStage values seen: {seen[:6]}; mapped: "
            f"{sorted(CLINICAL_STAGE_ORDINAL)}."
        )

    attributed: list[tuple[int, str, dict[str, Any]]] = []
    unattributed_top = 0
    for row in staged:
        stage = CLINICAL_STAGE_ORDINAL.get(row.get("maxClinicalStage") or "")
        if stage is None:
            continue
        hits = attributable_indications(rec, row)
        if hits:
            attributed.append((stage, (row.get("drug") or {}).get("name") or "?", hits[0]))
        else:
            unattributed_top = max(unattributed_top, stage)

    if not attributed:
        names = [(r.get("drug") or {}).get("name") for r in rec.drug_rows[:3]]
        return not_applicable(
            f"{rec.drug_count} candidates hit this target, but NONE is indicated for a disease "
            f"with mechanistic evidence linking it to this target, so there is no clinical "
            f"phase *for this target* to report. Furthest phase for any indication would have "
            f"been {STAGE_LABEL.get(unattributed_top, 'none')} "
            f"({', '.join(str(n) for n in names if n)}). "
            f"Attribution = human genetic evidence "
            f"({', '.join(sorted(GENETIC_ATTRIBUTION_DATATYPES))}) at score >= "
            f"{ATTRIBUTION_MIN_SCORE} in Open Targets' target-disease associations. "
            f"'clinical' and 'literature' are circular; 'somatic_mutation' and "
            f"'affected_pathway' do not discriminate (COUNCIL V1/D2).",
            raw={"unattributed_max_stage": unattributed_top, "attributed": 0},
        )

    # Report the strongest-evidence row among those tied at the top stage. Ties
    # at stage 5 are common (BRAF has 18 approved candidates) and picking the
    # first would name a weak exemplar for a well-attributed ring.
    top = max(t[0] for t in attributed)
    _, drug, hit = max(
        (t for t in attributed if t[0] == top),
        key=lambda t: max(t[2]["evidence"].values()),
    )
    ev = ", ".join(f"{k} {v:.2f}" for k, v in sorted(hit["evidence"].items()))
    detail = (
        f"{STAGE_LABEL[top]} — {drug} for {hit['name']}, attributable to this target "
        f"({ev}). {len(attributed)} of {rec.drug_count} candidates have an attributable "
        f"indication"
    )
    if unattributed_top > top:
        detail += (
            f"; {STAGE_LABEL[unattributed_top]} was reached by a candidate whose indication "
            f"is NOT attributable and is excluded from this ring"
        )
    return value(float(top), detail,
                 raw={"stage": top, "n_attributed": len(attributed),
                      "unattributed_max_stage": unattributed_top})


def clinical_activity_cell(rec: OTRecord) -> Cell:
    """COUNCIL D4 Ring B -- current activity, three states, AACT rows only.

    Encoded 0=dormant, 1=active, -1=stopped for cause. Kept separate from Ring A
    because only ~13% of stop events say anything about the target: a single
    ordinal with a 'discontinued' marker misleads on seven of every eight
    terminations.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if not rec.drug_count:
        return not_applicable("no drugs, so there is no activity to report")

    statuses: list[str] = []
    for_cause = 0
    with_status = 0
    for row in rec.drug_rows:
        for cr in row.get("clinicalReports") or []:
            if cr.get("source") != STATUS_SOURCE:
                continue  # COUNCIL D1 gap (ii): TTD/EMA rows have no status
            with_status += 1
            if st := cr.get("trialOverallStatus"):
                statuses.append(st)
            cats = cr.get("trialStopReasonCategories") or []
            if any(c in FOR_CAUSE_CATEGORIES for c in cats):
                for_cause += 1

    if with_status == 0:
        return no_data(f"{rec.drug_count} candidates but no {STATUS_SOURCE}-sourced trial status")

    active = sum(1 for s in statuses if s in ACTIVE_STATUSES)
    raw = {"active": active, "for_cause": for_cause, "n_status_rows": with_status}

    # COUNCIL D4's three states overlap and the review gives no precedence.
    # Taking 'stopped for cause' first labels BRAF stopped-for-cause on 31
    # for-cause stops out of 1382 trials, while 10 of its 18 agents are actively
    # recruiting -- a plainly wrong reading of a ring called "current activity".
    # Current activity therefore wins, and the for-cause count travels in the
    # detail and the drawer rather than overriding it. Flagged in V1-REPORT.
    if active:
        detail = f"active ({active} of {with_status} {STATUS_SOURCE} trials recruiting or ongoing)"
        if for_cause:
            detail += f"; also {for_cause} trials stopped for cause — see the drawer"
        return value(1.0, detail, raw=raw)
    if for_cause:
        return value(-1.0,
                     f"stopped for cause, nothing active ({for_cause} of {with_status} trials "
                     f"stopped Negative / Safety_Sideeffects)", raw=raw)
    return value(0.0, f"dormant (drugs exist, none of {with_status} trials active)", raw=raw)


def rodent_cell(rec: OTRecord) -> Cell:
    """PLAN §2 ring 7 / QA S4. A lead, not a verdict.

    'no abnormal phenotype detected' is filtered before counting and surfaced as
    its own state -- phenotyped-but-normal is real information and is not the
    same as never phenotyped.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    rows = rec.mouse_phenotypes
    if not rows:
        return no_data("no mouse phenotype records (not phenotyped, not 'no phenotype')")
    abnormal = [
        r for r in rows
        if NO_ABNORMAL not in str(r.get("modelPhenotypeLabel", "")).lower()
    ]
    if not abnormal:
        return value(0.0, "phenotyped, no abnormal phenotype detected", raw=0)
    return value(float(len(abnormal)),
                 f"{len(abnormal)} abnormal phenotype terms "
                 f"({len(rows) - len(abnormal)} 'no abnormal phenotype' rows excluded)",
                 raw=len(abnormal))


def drug_pipeline_rows(rec: OTRecord) -> list[dict[str, Any]]:
    """The drawer table (COUNCIL D6). Indication travels beside the phase.

    COUNCIL D2: BRAF's top row is sorafenib, approved for RCC/HCC, not
    BRAF-driven disease. An approved multi-kinase inhibitor must never silently
    read as 'this target is clinically validated', so the indication is a
    required column, not a nicety.
    """
    out: list[dict[str, Any]] = []
    for row in rec.drug_rows:
        drug = row.get("drug") or {}
        reports = row.get("clinicalReports") or []
        aact = [r for r in reports if r.get("source") == STATUS_SOURCE]
        statuses = [r.get("trialOverallStatus") for r in aact if r.get("trialOverallStatus")]
        dates = sorted(r["trialStartDate"] for r in aact if r.get("trialStartDate"))
        stop_cats: dict[str, int] = {}
        for r in aact:
            for c in r.get("trialStopReasonCategories") or []:
                stop_cats[c] = stop_cats.get(c, 0) + 1
        indications = []
        for d in row.get("diseases") or []:
            name = (d.get("disease") or {}).get("name") or d.get("diseaseFromSource")
            if name and name not in indications:
                indications.append(name)
        url = next((r["url"] for r in reports if r.get("url")), None)
        out.append({
            "drug": drug.get("name"),
            "drug_id": drug.get("id"),
            "modality": drug.get("drugType"),
            "max_stage": row.get("maxClinicalStage"),
            "phase_ordinal": CLINICAL_STAGE_ORDINAL.get(row.get("maxClinicalStage") or "", 0),
            "status": _dominant(statuses),
            "n_trials": len(reports),
            "n_with_status": len(aact),
            "indications": indications[:6],
            "latest_start": dates[-1] if dates else None,
            "stop_reasons": stop_cats,
            "url": url or (f"https://platform.opentargets.org/drug/{drug['id']}"
                           if drug.get("id") else None),
        })
    out.sort(key=lambda r: (-r["phase_ordinal"], r["latest_start"] or ""), reverse=False)
    out.sort(key=lambda r: -r["phase_ordinal"])
    return out


def _dominant(statuses: list[str]) -> str | None:
    if not statuses:
        return None
    for s in ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING"):
        if s in statuses:
            return s
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1
    return max(counts, key=lambda k: counts[k])


def pipeline_summary(rec: OTRecord) -> str:
    """COUNCIL D6: one line above the table answers 'is this crowded?'."""
    if not rec.found or rec.drug_count is None:
        return "no Open Targets record"
    if rec.drug_count == 0:
        return "0 agents — target record exists, genuinely undrugged"
    rows = drug_pipeline_rows(rec)
    active = sum(1 for r in rows if r["status"] in ACTIVE_STATUSES)
    stopped = sum(1 for r in rows if any(c in FOR_CAUSE_CATEGORIES for c in r["stop_reasons"]))
    return f"{rec.drug_count} agents, {active} active, {stopped} stopped for cause"


__all__ = [
    "OpenTargetsAdapter", "OTRecord", "TARGET_QUERY", "CLINICAL_STAGE_ORDINAL",
    "ASSOCIATION_QUERY", "MECHANISTIC_DATATYPES", "ATTRIBUTION_MIN_SCORE",
    "GENETIC_ATTRIBUTION_DATATYPES",
    "SPARSE_KEY_ABSENT_VALUE", "DENSE_PRIORITISATION_KEYS", "SEVERE_LIABILITY_TERMS",
    "druggability_cell", "safety_cell", "clinical_phase_cell", "clinical_activity_cell",
    "rodent_cell", "drug_pipeline_rows", "pipeline_summary", "failed",
    "attributable_indications", "mechanistic_evidence", "severe_liabilities",
    "has_prioritisation_record",
]
