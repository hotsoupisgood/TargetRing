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

import re
from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import OPENTARGETS_URL
from ..http import Http, describe, graphql
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
          id trialPhase clinicalStage trialOverallStatus trialStartDate
          trialStopReasonCategories trialWhyStopped source url
          trialSponsor{name}
          diseases{diseaseFromSource disease{id name}}
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

# --- COUNCIL D4 Ring B: the trial rows, read off the gene query -------------
#
# **v2.4, and this is the whole point of the change.** Until 2026-09-24 this
# build selected trial rows with `source == "AACT"`. On that day Open Targets
# relabelled the same rows `ClinicalTrials.gov`; the literal matched nothing,
# the ring went dark for every gene, and the conclusion drawn was that the data
# had been dropped. It had not. A per-drug hop to ClinicalTrials.gov was built
# to replace data that was already in the response — 217 HTTP calls on a 50-gene
# run, 48.2 s of a 70.5 s cold run, and one live 429 (`docs/V2-REPORT.md` §v2.2
# item 2, §v2.3 item 3). It is retired.
#
# So no single source literal selects rows any more. **What selects a row is
# that it carries a `trialOverallStatus`**, which is a property of the thing
# this ring counts rather than of who labelled it. The distinct `source` values
# actually seen travel in the cell's `raw`, the drawer panel and the manifest,
# so the *next* relabelling is visible in the artefacts instead of silent.
#
# Live census, BRAF (`ENSG00000157764`), 2026-09-24, 1,433 reports on 18 drug
# rows: `ClinicalTrials.gov` 1381, TTD 23, EMA Human Drugs 8, PMDA 6, ATC 5,
# DailyMed 4, USAN 3, FDA 2, INN 1. Exactly 52 reports carry no status, and
# they are exactly the 52 non-ClinicalTrials.gov rows — which is COUNCIL D1
# gap (ii) ("TTD / EMA rows have no status and would read as inactive") holding
# under the new labelling without naming any label.
#
# Statuses are classified exhaustively. A status in none of the three sets is
# counted in neither bucket, which on a three-state ring silently reads as
# "dormant", so it is a loud `failed` cell instead.
LIVE_STATUSES: tuple[str, ...] = (
    "RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION", "AVAILABLE",
)
STOPPED_STATUSES: tuple[str, ...] = ("TERMINATED", "SUSPENDED", "WITHDRAWN")
INERT_STATUSES: tuple[str, ...] = (
    "COMPLETED", "UNKNOWN", "NO_LONGER_AVAILABLE", "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING", "WITHHELD",
)
# Kept under the old name: `ACTIVE_STATUSES` is what v1 called this set.
ACTIVE_STATUSES = set(LIVE_STATUSES)

# **v2.4 restores this.** Open Targets categorises why a trial stopped, which
# ClinicalTrials.gov does not, so the -1.0 state can go back to meaning "stopped
# **for cause**" instead of v2.2's "stopped early — cause NOT categorised".
# That distinction is the reason this is a second ring at all: COUNCIL D4
# measured that roughly 7 of 8 terminations say nothing about the target, and a
# single ordinal with a "discontinued" marker would mislead on every one of
# them. BRAF's live categories, 2026-09-24: Insufficient_Enrollment 108,
# Business_Administrative 51, Negative 17, Study_Design 13, Safety_Sideeffects
# 13, Logistics_Resources 12, Invalid_Reason 9, Another_Study 5,
# Study_Staff_Moved 4, Regulatory 3, No_Context 2, Uncategorised 1 — so 30 of
# 238 categorised stops are for cause, and the other 208 are not.
FOR_CAUSE_CATEGORIES = {"Negative", "Safety_Sideeffects"}
# Categories that exist but assert nothing. A row carrying only one of these is
# categorised in name only and must not count as evidence either way.
UNINFORMATIVE_CATEGORIES = {"Invalid_Reason", "No_Context", "Uncategorised"}

SOURCE_LABEL = "Open Targets clinicalReports"

# An NCT id and nothing else. Open Targets emits them lower case (`nct00405587`)
# and mixes other registries' ids into the same field.
_NCT_ID = re.compile(r"^NCT\d{4,}$")

# QA S4: BRAF's mousePhenotypes includes this label. Counting rows is not
# counting abnormal phenotypes, and a gene with one such record would otherwise
# outrank a gene with none.
NO_ABNORMAL = "no abnormal phenotype detected"

# The prioritisation keys behind each ring. Stated reductions, not field reads
# (QA B1: there is no single scalar tractability value).
#
# --- v2.3 item 1: COUNCIL M7/V8, the two-ring split ------------------------
# Until 2026-09-24 these four were one "Druggability" ring. M7 and S1 both asked
# for the split -- "has been drugged" and "family suggests druggable" are
# different claims -- and v2.2 item 1 made not splitting them untenable: once an
# absent probe key correctly became `no_data`, the *whole* four-key mean refused,
# and `hasHighQualityChemicalProbes` is absent for 9 of the 14 council targets
# and 11 of 14 on `de_realistic.csv`. Those genes lost a Pareto axis for want of
# a key that describes chemical-probe stock, not tractability, and eligibility
# fell 10 -> 4 (`docs/V2-REPORT.md` §v2.2, "Read this first").
#
# So: three dense keys carry tractability, and the sparse key gets its own ring
# which is allowed to be hatched. Nothing is imputed to achieve this -- the probe
# ring still refuses when the key is absent, it just no longer takes the
# tractability cell down with it. (The Pareto axis the dense ring was split out
# to carry is gone as of v2.5; the split is not, and the reason above is why.)
TRACTABLE_KEYS = ("hasLigand", "hasPocket", "hasSmallMoleculeBinder")
PROBE_KEYS = ("hasHighQualityChemicalProbes",)
SAFETY_KEYS = ("hasSafetyEvent", "geneEssentiality", "geneticConstraint", "isCancerDriverGene")

# COUNCIL V4 / V8. Some prioritisation keys are emitted by Open Targets *only*
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
# A sparse key is a binary field with one end left unwritten, and that end is
# 0.0: {-1, 0} for the two liability flags. So absence is a *measured* value,
# not a missing one, and is mapped to 0.0 -- which makes the denominator 4 for
# every gene and stops it encoding the answer.
#
# What this does NOT claim: that a gene with no `hasSafetyEvent` is safe. Open
# Targets not holding a safety-event record is a fact about Open Targets. The
# ring label and the cell detail say so; see COUNCIL V4 and `docs/V2-REPORT.md`.
SPARSE_KEY_ABSENT_VALUE: dict[str, float] = {
    "hasSafetyEvent": 0.0,          # absent  = no safety event in OT's sources
    "isCancerDriverGene": 0.0,      # absent  = not in OT's cancer-driver list
}

# --- v2.2 item 1: the key that stopped being one-sided ----------------------
# `hasHighQualityChemicalProbes` was in the mapping above until 2026-09-24,
# when `TestSparseKeyCensusStillHolds` went red against the live API: Open
# Targets now writes the key *explicitly as 0.0* as well as omitting it, so
# absence and an explicit zero coexist and mapping absence to 0.0 became
# imputation of exactly the kind AGENTS.md rule 1 forbids.
#
# Puck's decision, 2026-09-24: an explicit 0.0 is a real value meaning **"Open
# Targets looked and there is no high-quality probe"**, and a genuinely absent
# key stays `no_data`. An explicit zero therefore does arithmetic work on the
# druggability ring (it is what separates a probed-and-empty target from a
# probed-and-stocked one); an absent key refuses the mean, because a 3-key mean
# next to a 4-key mean is the V4 defect and a 0.0 substituted for silence is the
# imputation the census test caught.
#
# Re-measured live 2026-09-24 over 23 targets (`/tmp/tr22/probe_prio.py`), every
# prioritisation key, looking for any *other* key that has quietly acquired the
# same explicit-zero behaviour while still being sparse:
#
#     key                                 present absent  emits 0.0 explicitly
#     hasHighQualityChemicalProbes              8     15  YES  <- this decision
#     hasSafetyEvent                           12     11  no   (only -1.0)
#     isCancerDriverGene                        5     18  no   (only -1.0)
#     maxClinicalStage                         17      6   no   (0.7, 1.0)
#     mouseKOScore                             21      2   no
#     mouseOrthologMaxIdentityPercentage       22      1  YES  (unused by any ring)
#     paralogMaxIdentityPercentage             20      3  YES  (unused by any ring)
#     every other key                          23      0   n/a (dense)
#
# So: no key this build *reads* has acquired the behaviour except the one Puck
# ruled on. The two that have are not in TRACTABLE_KEYS or SAFETY_KEYS and
# feed nothing, and they are listed here so the next census has a baseline.
#
# v2.3 item 1: the ruling is unchanged, but the key now sits alone on its own
# ring (`chemical_probe_cell`), so an absent probe key hatches one cell instead
# of refusing a four-key mean that three dense keys could have carried.
# `TestSparseKeyCensusStillHolds` now checks the whole key set rather than only
# the mapped ones, so a future key acquiring it fails loudly instead of quietly
# becoming imputation.
EXPLICIT_ZERO_KEYS: frozenset[str] = frozenset({"hasHighQualityChemicalProbes"})

# Keys Open Targets is observed to emit an explicit 0.0 for but which no ring
# reads. Recorded so the census test can tell "new" from "known".
EXPLICIT_ZERO_KEYS_NOT_READ: frozenset[str] = frozenset({
    "mouseOrthologMaxIdentityPercentage", "paralogMaxIdentityPercentage",
})

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

    def drug_names(self) -> list[str]:
        """Distinct drug names on this target's rows, in first-seen order.

        v2.2 item 2: these are what the ClinicalTrials.gov hop is keyed on.
        COUNCIL D3 ruled out keying CT.gov on the gene itself -- `query.term=BRAF`
        returns cfDNA biomarker studies -- so the drug name is the only join.
        """
        out: list[str] = []
        for row in self.drug_rows:
            name = (row.get("drug") or {}).get("name")
            if name and name not in out:
                out.append(str(name))
        return out

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
                self.failure = describe(e)
                for eid in pending:
                    out[eid] = OTRecord(eid, found=False, error=describe(e))
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
                    rec.associations_error = describe(e)
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

    v2.3 item 1: no key reached by this function is in EXPLICIT_ZERO_KEYS any
    more -- the one that was now has its own single-key ring. The explicit-zero
    branch below is kept armed rather than deleted, because the census test can
    only tell us a *new* key has acquired the behaviour after it already has.
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
        # A key we cannot read went missing. The mean would have a different
        # denominator from every other gene's, which is exactly the V4 defect,
        # so refuse. Two reasons land here and they are not the same fact, so
        # they do not get the same sentence.
        explicit_zero = [k for k in unreadable if k in EXPLICIT_ZERO_KEYS]
        dense = [k for k in unreadable if k not in EXPLICIT_ZERO_KEYS]
        why = []
        if explicit_zero:
            why.append(
                f"{', '.join(explicit_zero)} is absent, and Open Targets now also writes this "
                f"key explicitly as 0.0 — so absence is silence, not a measured zero, and "
                f"filling it with 0.0 would be imputation (v2.2 item 1). An explicit 0.0 "
                f"would have been read as a real value meaning 'looked, none exists'"
            )
        if dense:
            why.append(
                f"{', '.join(dense)} absent for this gene and not a sparse-encoded key"
            )
        return no_data(
            f"{label}: " + "; ".join(why) + f". A {len(keys)}-key mean cannot be formed. "
            f"Refusing a shorter mean — the denominator would encode the answer (COUNCIL V4)."
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


def tractable_cell(rec: OTRecord) -> Cell:
    """v2.3 item 1. The dense half of the old druggability ring.

    `hasLigand`, `hasPocket` and `hasSmallMoleculeBinder` were present for 24/24
    targets in the live census and 23/23 in the re-census, so this cell refuses
    only when Open Targets has no prioritisation record for the target at all --
    which is the one case where refusing is the right answer. That density is
    the whole point of the split: it is what lets the Pareto axis ride here.
    """
    return _mean_of_keys(rec, TRACTABLE_KEYS, "tractability")


def chemical_probe_cell(rec: OTRecord) -> Cell:
    """v2.3 item 1. The sparse half, on its own ring, allowed to be hatched.

    One key, so there is no mean and no denominator to encode the answer. v2.2
    item 1's ruling is unchanged and now applies to exactly one cell instead of
    dragging three dense keys down with it: an explicit 0.0 is a measurement
    ("Open Targets looked, no high-quality probe"), an absent key is `no_data`.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if not has_prioritisation_record(rec):
        return no_data(
            "Open Targets holds no prioritisation record for this target, so the "
            "chemical-probe key cannot be read. This is absence of a record, not "
            "'no probe exists'."
        )
    key = PROBE_KEYS[0]
    if key not in rec.prioritisation:
        return no_data(
            f"{key} is absent for this gene, and Open Targets also writes the key "
            f"explicitly as 0.0 for other genes — so absence here is silence, not a "
            f"measured zero, and filling it would be imputation (v2.2 item 1). This "
            f"ring is allowed to be hatched; the tractability ring beside it is not "
            f"affected (v2.3 item 1, COUNCIL M7/V8)."
        )
    v = rec.prioritisation[key]
    meaning = ("an explicit 0.0: Open Targets looked and holds no high-quality chemical probe"
               if v == 0.0 else f"{key}={v:+.2f}")
    return value(
        v,
        # v3.6. This read "…the tractability ring is the one that carries the
        # Pareto axis (COUNCIL M7/V8)". There is no Pareto axis: v2.5 deleted
        # the front, the score and the axes, and `RETIRED_CLAIM_WORDS` exists to
        # stop exactly this sentence surviving the feature it described. It was
        # missed because `assert_footer_is_true` reads the DRAWN text of the SVG,
        # and a cell detail is not drawn — it reaches `values.csv` and the
        # drawer, and now the table's cell tooltips, which is where it was found.
        # The distinction the sentence is actually making is the one v2.3 item 1
        # made, and it stands on its own.
        f"{key} = {meaning}. Chemical-probe stock is prior tool-compound effort, NOT "
        f"tractability — they are two rings for that reason, and this one is allowed "
        f"to be hatched without taking the three dense keys down with it "
        f"(v2.3 item 1, COUNCIL M7/V8).",
        raw={"keys": {key: v}, "inferred_from_absence": []},
    )


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


def per_indication_stages(row: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    """The furthest stage reached *per indication* in one drug row.

    QA-v2-retest §2, and the structural half of the V1 defect. In the Open
    Targets payload `drugAndClinicalCandidates.rows[]` is **one row per drug**
    and its `maxClinicalStage` is that drug's *global* maximum across every
    indication on the row -- for regorafenib, ~25 of them, every one of which
    carries `APPROVAL` while regorafenib is approved for three. Reading the row
    stage therefore reports a stage belonging to a different disease from the
    one the attribution gate approved. FRK rendered "Approved" on
    REGORAFENIB x age-related macular degeneration that way.

    `clinicalReports[]` is one row per *trial or label*, and it carries both its
    own `clinicalStage` and its own `diseases`. Reducing it by disease id gives
    the quantity the ring claims to show. Regorafenib, measured live 2026-09-24:

        colorectal cancer (MONDO_0005575)              APPROVAL  (DailyMed, PMDA)
        gastrointestinal stromal tumor (MONDO_0011719) PHASE_3
        age-related macular degeneration (MONDO_0005150) PHASE_2  <- the FRK case

    `clinicalStage` is used rather than `trialPhase`: it is non-null, and it is
    the same vocabulary as `maxClinicalStage`, so one ordinal table serves both.
    `trialPhase` is a second vocabulary ("PHASE1", "PHASE1/PHASE2") and a second
    mapping table is a second place to be wrong.

    Returns `({disease_id: ordinal}, {stage strings seen but not mapped})`.
    Stages this build does not map (`EARLY_PHASE_1`, `UNKNOWN`, `WITHDRAWAL`)
    are returned as the second element and never guessed at: an unmapped stage
    is a fact we do not have, not a low one.
    """
    stages: dict[str, int] = {}
    unmapped: set[str] = set()
    for report in row.get("clinicalReports") or []:
        raw = report.get("clinicalStage") or report.get("trialPhase")
        ordinal = CLINICAL_STAGE_ORDINAL.get(str(raw or ""))
        if ordinal is None:
            if raw:
                unmapped.add(str(raw))
            continue
        for d in report.get("diseases") or []:
            did = (d.get("disease") or {}).get("id")
            if did:
                stages[did] = max(stages.get(did, 0), ordinal)
    return stages, unmapped


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

    QA-v2-retest §2 -- the gate was right and the *quantity* was wrong. Having
    established that a drug-target-indication link is genuine, v2 then reported
    `maxClinicalStage`, which is the drug's global maximum over every indication
    on the row. FRK rendered 5.0, "Approved", on REGORAFENIB x age-related
    macular degeneration: the attribution is real (genetic_association 0.41) and
    regorafenib is approved -- for colorectal cancer. The number belonged to a
    different disease. That is the V1 defect surviving the V1 fix.

    The phase reported is now the phase **for the attributed indication**, read
    off `clinicalReports` by disease id (`per_indication_stages`). Where no
    per-indication stage can be established for any attributed link, the cell is
    `no_data` -- never the drug's global number, which is a borrowed one.
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
              if (r.get("maxClinicalStage") or "") in CLINICAL_STAGE_ORDINAL
              or per_indication_stages(r)[0]]
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

    # (stage-for-that-indication, drug, indication). One entry per attributed
    # drug-indication *pair*, because that pair is what the ring claims about.
    attributed: list[tuple[int, str, dict[str, Any]]] = []
    # Attributed links whose per-indication stage could not be established.
    # These must not fall back to the drug's global stage (QA-v2-retest §2).
    unstaged: list[tuple[str, str]] = []
    unmapped_seen: set[str] = set()
    unattributed_top = 0
    # The drug's own global maximum, where that exceeds what it reached for the
    # indication we credited it with. FRK x regorafenib: 5 vs 3.
    global_over_attributed = 0
    for row in staged:
        drug = (row.get("drug") or {}).get("name") or "?"
        stage = CLINICAL_STAGE_ORDINAL.get(row.get("maxClinicalStage") or "") or 0
        per, unmapped = per_indication_stages(row)
        unmapped_seen |= unmapped
        hits = attributable_indications(rec, row)
        if not hits:
            # Nothing on this row is attributable, so its furthest reach is
            # reported only as the excluded finding, never as the ring.
            unattributed_top = max(unattributed_top, stage, *per.values(), 0)
            continue
        for hit in hits:
            own = per.get(hit["id"])
            if own is None:
                unstaged.append((drug, str(hit["name"])))
            else:
                attributed.append((own, drug, hit))
                if stage > own:
                    global_over_attributed = max(global_over_attributed, stage)

    if not attributed and unstaged:
        # The gate opened on a genuine association and there is no per-indication
        # stage behind it. Reporting the drug's global stage here is exactly the
        # FRK defect, so the honest answer is that we could not establish one.
        pairs = "; ".join(f"{d} × {i}" for d, i in unstaged[:4])
        return no_data(
            f"{rec.drug_count} candidates, {len(unstaged)} with an attributable indication "
            f"({pairs}), but Open Targets' clinicalReports carry no stage this build maps "
            f"for that indication, so there is no phase FOR THE ATTRIBUTED INDICATION to "
            f"report. The drug's global maxClinicalStage is deliberately NOT used: it is the "
            f"drug's maximum across every disease it was ever trialled for and belongs to a "
            f"different indication (QA-v2-retest §2)."
            + (f" Stage values seen but unmapped: {sorted(unmapped_seen)[:6]}."
               if unmapped_seen else "")
        )

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

    # Report the strongest-evidence pair among those tied at the top stage. Ties
    # at stage 5 are common (BRAF has 18 approved candidates) and picking the
    # first would name a weak exemplar for a well-attributed ring.
    #
    # QA-v2-retest §3: this tiebreak named SORAFENIB TOSYLATE x lung cancer for
    # BRAF, because its 0.83 beat VEMURAFENIB x melanoma's 0.699 -- and sorafenib
    # is not approved for lung cancer. It named the exact drug the V1 fix exists
    # to reject. It no longer can: the candidates are now ranked on the stage
    # reached *for that indication*, and sorafenib x lung cancer is Phase 2
    # (measured live), so it never reaches the stage-5 tie that vemurafenib x
    # melanoma (APPROVAL, DailyMed/EMA) is in. Among genuine ties -- links that
    # all reached the same stage for their own indication -- strongest evidence
    # is still the exemplar worth naming.
    top = max(t[0] for t in attributed)
    _, drug, hit = max(
        (t for t in attributed if t[0] == top),
        key=lambda t: max(t[2]["evidence"].values()),
    )
    n_drugs = len({t[1] for t in attributed})
    n_at_top = sum(1 for t in attributed if t[0] == top)
    ev = ", ".join(f"{k} {v:.2f}" for k, v in sorted(hit["evidence"].items()))
    # QA-v2-retest §3: the named row is an exemplar, and v2 printed it as though
    # it were the finding. Saying how many links share the top stage stops one
    # arbitrary winner of an evidence tiebreak from reading as "the" drug for
    # this target.
    example = (f", 1 of {n_at_top} attributable link(s) at this stage"
               if n_at_top > 1 else "")
    detail = (
        f"{STAGE_LABEL[top]} for this indication — {drug} reached {STAGE_LABEL[top]} "
        f"for {hit['name']}, which is attributable to this target ({ev}{example}). "
        f"{n_drugs} of {rec.drug_count} candidates have an attributable indication"
    )
    if global_over_attributed > top:
        detail += (
            f"; the drug's own global maxClinicalStage reaches "
            f"{STAGE_LABEL[global_over_attributed]} for a DIFFERENT indication and is "
            f"excluded — this ring is the phase for the attributed indication only"
        )
    if unattributed_top > top:
        detail += (
            f"; {STAGE_LABEL[unattributed_top]} was reached by a candidate whose indication "
            f"is NOT attributable and is excluded from this ring"
        )
    if unstaged:
        detail += (
            f"; {len(unstaged)} further attributable link(s) carry no mapped per-indication "
            f"stage and set nothing"
        )
    return value(float(top), detail,
                 raw={"stage": top, "n_attributed": n_drugs,
                      "n_attributed_links": len(attributed),
                      "attributed_indication": hit["id"],
                      "global_stage_excluded": global_over_attributed or None,
                      "unattributed_max_stage": unattributed_top})


def classify_status(status: str | None) -> str | None:
    """`live` / `stopped` / `inert`, or None for a status this build does not know.

    None is the interesting return: a status in none of the three sets would be
    counted in neither bucket, and on a three-state ring that reads as
    "dormant". It is a loud failure instead, at the cell.
    """
    if not status:
        return None
    s = str(status)
    if s in LIVE_STATUSES:
        return "live"
    if s in STOPPED_STATUSES:
        return "stopped"
    if s in INERT_STATUSES:
        return "inert"
    return None


def nct_id(report: dict[str, Any]) -> str | None:
    """The NCT id on a `clinicalReports` row, or None if it is not one.

    Open Targets puts a registry id in `clinicalReports.id` and it is not always
    a trial: BRAF's 1,433 reports carry 1,381 `nct…` ids and 52 others (`eme…`
    for EMA, `l01…` for ATC, DailyMed set ids, …). Measured 2026-09-24, those 52
    are exactly the rows with no `trialOverallStatus`. Normalised to upper case
    because Open Targets emits them lower case and NCT ids are conventionally
    upper; a distinct count over mixed case would double-count.
    """
    raw = str(report.get("id") or "").strip().upper()
    return raw if _NCT_ID.match(raw) else None


def trial_reports(rec: OTRecord) -> list[dict[str, Any]]:
    """Every `clinicalReports` row that carries a trial status, flattened.

    One row per (drug, report) pair — a *drug-trial pair*, which is why the
    distinct-NCT de-duplication below exists. Free text (`trialWhyStopped`,
    sponsor names, condition strings) is carried as data and never interpreted
    (AGENTS.md rule 5).
    """
    out: list[dict[str, Any]] = []
    for row in rec.drug_rows:
        drug = (row.get("drug") or {}).get("name")
        for r in row.get("clinicalReports") or []:
            status = r.get("trialOverallStatus")
            if not status:
                continue
            cats = [str(c) for c in (r.get("trialStopReasonCategories") or [])]
            conditions = [
                (d.get("disease") or {}).get("name") or d.get("diseaseFromSource")
                for d in (r.get("diseases") or [])
            ]
            nid = nct_id(r)
            out.append({
                "drug": str(drug) if drug else None,
                "nct_id": nid,
                "report_id": str(r.get("id")) if r.get("id") else None,
                "source": str(r.get("source")) if r.get("source") else None,
                "status": str(status),
                "bucket": classify_status(status),
                "sponsor": (r.get("trialSponsor") or {}).get("name"),
                "why_stopped": r.get("trialWhyStopped"),
                "stop_categories": cats,
                "for_cause": bool(FOR_CAUSE_CATEGORIES & set(cats)),
                "phase": r.get("trialPhase") or r.get("clinicalStage"),
                "start": r.get("trialStartDate"),
                "conditions": [str(c) for c in conditions if c][:4],
                "url": r.get("url") or (
                    f"https://clinicaltrials.gov/study/{nid}" if nid else None),
            })
    return out


def source_counts(rec: OTRecord) -> dict[str, int]:
    """Distinct `clinicalReports.source` values on this target, with counts.

    Requirement 1 of v2.4 and the reason the relabelling is not allowed to be
    silent again: whatever Open Targets calls these rows, what it called them on
    the day the figure was drawn is in the cell, the drawer and the manifest.
    """
    counts: dict[str, int] = {}
    for row in rec.drug_rows:
        for r in row.get("clinicalReports") or []:
            s = str(r.get("source") or "—")
            counts[s] = counts.get(s, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def clinical_activity_cell(rec: OTRecord) -> Cell:
    """COUNCIL D4 Ring B — current activity, three states, off the gene query.

    Encoded 0 = dormant, 1 = live, -1 = **stopped for cause**. Kept separate
    from Ring A because only ~13% of stop events say anything about the target:
    a single ordinal with a "discontinued" marker misleads on seven of every
    eight terminations.

    **v2.4.** The -1.0 state means "stopped for cause" again. v2.2 downgraded it
    to "stopped early, cause NOT categorised" because the per-drug
    ClinicalTrials.gov hop that replaced this function could only see free text.
    Open Targets categorises the same stops (`trialStopReasonCategories`), and
    that categorisation is the entire justification for splitting this ring off
    Ring A, so it comes back. The free text (`trialWhyStopped`) still travels to
    the drawer for a human to read.

    Rows are selected by *carrying a status*, never by a source literal — see
    the constants above for why that distinction cost a night.

    Precedence is unchanged from v1 and stated there: a live trial outranks a
    historical stop, because for-cause-first labels BRAF "stopped" while ten of
    its eighteen agents are recruiting.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if not rec.drug_count:
        return not_applicable("no drugs, so there is no activity to report")

    reports = trial_reports(rec)
    sources = source_counts(rec)
    if not reports:
        # Naming what WAS there is the difference between a ring that is blank
        # and a ring that is diagnosable — and on 2026-09-24 it is what made the
        # AACT relabelling visible at all. Kept, with the source literal gone.
        return no_data(
            f"{rec.drug_count} candidate(s) in Open Targets, but not one of their "
            f"clinicalReports rows carries a trialOverallStatus. Report sources present "
            f"for this target: {', '.join(f'{k} {v}' for k, v in list(sources.items())[:6]) or 'none'}. "
            f"A row with no status is a label or registry entry, not a trial (COUNCIL D1 "
            f"gap (ii)), and counting it would read as inactive."
        )

    unknown = sorted({r["status"] for r in reports if r["bucket"] is None})
    if unknown:
        return failed(
            f"Open Targets returned trial statuses this build does not classify: "
            f"{unknown[:6]}. Counting them as neither live nor stopped would silently drop "
            f"them from a three-state ring, which reads as 'dormant'."
        )

    live = [r for r in reports if r["bucket"] == "live"]
    stopped = [r for r in reports if r["bucket"] == "stopped"]
    for_cause = [r for r in stopped if r["for_cause"]]
    distinct = len({r["nct_id"] for r in reports if r["nct_id"]})
    no_id = sum(1 for r in reports if not r["nct_id"])

    cats: dict[str, int] = {}
    for r in stopped:
        for c in r["stop_categories"]:
            cats[c] = cats.get(c, 0) + 1
    top_cats = ", ".join(f"{k} {v}" for k, v in
                         sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[:4])

    raw = {
        "source": SOURCE_LABEL,
        "sources_seen": sources,
        "n_status_rows": len(reports),
        "n_distinct_trials": distinct,
        "n_rows_without_an_nct_id": no_id,
        "live": len(live), "active": len(live),
        "stopped": len(stopped), "for_cause": len(for_cause),
        "stop_categories": cats,
    }
    provenance = (
        f" Read from {SOURCE_LABEL} in the gene query — no per-drug hop (v2.4). "
        f"Source labels on this target's rows: "
        f"{', '.join(f'{k} {v}' for k, v in list(sources.items())[:4])}. "
        f"Counts here are drug-trial pairs over {len(rec.drug_rows)} drug row(s); "
        f"{distinct} distinct NCT id(s) are in the drawer."
    )

    if live:
        detail = (f"live trial — {len(live)} of {len(reports)} trial row(s) recruiting or "
                  f"ongoing, on {len({r['drug'] for r in live if r['drug']})} of "
                  f"{len(rec.drug_rows)} drug(s)")
        if for_cause:
            detail += (f"; also {len(for_cause)} stopped for cause "
                       f"(Negative / Safety_Sideeffects) — see the drawer")
        return value(1.0, detail + provenance, raw=raw)
    if for_cause:
        return value(-1.0,
                     f"stopped for cause, nothing live — {len(for_cause)} of {len(stopped)} "
                     f"stop(s) categorised Negative or Safety_Sideeffects by Open Targets "
                     f"({len(reports)} trial row(s) in total)"
                     + (f"; all stop categories: {top_cats}" if top_cats else "")
                     + provenance, raw=raw)
    detail = f"dormant — drugs exist, none of {len(reports)} trial row(s) live"
    if stopped:
        detail += (f"; {len(stopped)} stopped early but NONE for cause "
                   f"({top_cats or 'no category given'}), which is why this is dormant "
                   f"rather than -1 (COUNCIL D4: ~7 of 8 terminations say nothing about "
                   f"the target)")
    return value(0.0, detail + provenance, raw=raw)


def trials_panel(rec: OTRecord) -> dict[str, Any]:
    """The drawer's trial panel: sponsors, live status, stops, distinct NCT ids.

    Puck's two conditions from v2.2 item 2, unchanged and now met without a
    second API: "the ring must say which source it counted" and "sponsor plus
    live status must reach the drawer, since that is what tells the user a
    target is crowded".

    **v2.3 item 2 is kept exactly**: the target-level totals are *distinct NCT
    ids*, not drug-trial pairs. A trial testing two of a target's drugs appears
    on both drug rows, and BRAF's eighteen agents overlap heavily. Both numbers
    are reported, because they answer different questions, and the panel says
    which is which — it was read once with only the pair count on it.

    What is new is that a row can now be a stop **with a category**, so the
    stopped list carries the Open Targets category beside the sponsor's free
    text rather than the free text alone.
    """
    reports = trial_reports(rec)
    sources = source_counts(rec)
    by_drug: dict[str, list[dict[str, Any]]] = {}
    for r in reports:
        by_drug.setdefault(r["drug"] or "—", []).append(r)

    rows = []
    sponsor_counts: dict[str, int] = {}
    for drug, rs in by_drug.items():
        live = [r for r in rs if r["bucket"] == "live"]
        stopped = [r for r in rs if r["bucket"] == "stopped"]
        drug_sponsors: dict[str, int] = {}
        for r in live:
            if s := r["sponsor"]:
                drug_sponsors[s] = drug_sponsors.get(s, 0) + 1
        for s in drug_sponsors:
            sponsor_counts[s] = sponsor_counts.get(s, 0) + 1
        rows.append({
            "drug": drug,
            "n_trials": len(rs),
            "n_live": len(live),
            "n_stopped": len(stopped),
            "n_for_cause": sum(1 for r in stopped if r["for_cause"]),
            # Distinct ids on this drug's own rows. Unlike the retired per-drug
            # hop, Open Targets can repeat an NCT id within one drug row, so
            # this is a real check rather than a restatement of the count.
            "n_distinct": len({r["nct_id"] for r in rs if r["nct_id"]}),
            "n_distinct_live": len({r["nct_id"] for r in live if r["nct_id"]}),
            "n_distinct_stopped": len({r["nct_id"] for r in stopped if r["nct_id"]}),
            "sponsors": sorted(drug_sponsors, key=lambda s: (-drug_sponsors[s], s))[:6],
            "live": [
                {"nct_id": r["nct_id"], "status": r["status"], "sponsor": r["sponsor"],
                 "phases": [str(r["phase"])] if r["phase"] else [],
                 "conditions": r["conditions"], "url": r["url"]}
                for r in live[:8]
            ],
            "stopped": [
                {"nct_id": r["nct_id"], "status": r["status"], "sponsor": r["sponsor"],
                 "why_stopped": r["why_stopped"],
                 # v2.4: Open Targets' own categorisation, beside the free text.
                 "categories": r["stop_categories"], "for_cause": r["for_cause"],
                 "conditions": r["conditions"], "url": r["url"]}
                for r in stopped[:8]
            ],
            "error": None,
        })
    rows.sort(key=lambda r: (-(r["n_live"] or 0), -(r["n_trials"] or 0)))

    live_all = [r for r in reports if r["bucket"] == "live"]
    stopped_all = [r for r in reports if r["bucket"] == "stopped"]
    ids = {r["nct_id"] for r in reports if r["nct_id"]}
    live_ids = {r["nct_id"] for r in live_all if r["nct_id"]}
    stopped_ids = {r["nct_id"] for r in stopped_all if r["nct_id"]}
    both = len(live_ids & stopped_ids)
    no_id = sum(1 for r in reports if not r["nct_id"])

    cats: dict[str, int] = {}
    for r in stopped_all:
        for c in r["stop_categories"]:
            cats[c] = cats.get(c, 0) + 1
    cats = dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
    n_for_cause = sum(1 for r in stopped_all if r["for_cause"])

    floor = ""
    if no_id:
        floor = (f" {no_id} trial row(s) carry a status but no NCT id, so they are in the "
                 f"pair counts and cannot be in the distinct ones: the distinct figures are "
                 f"a FLOOR.")

    return {
        "source": SOURCE_LABEL,
        "endpoint": OPENTARGETS_URL,
        "note": (
            "Read from `drugAndClinicalCandidates.rows[].clinicalReports` in the same gene "
            "query that draws every other Open Targets ring — no per-drug hop (v2.4). Rows "
            "are selected by carrying a `trialOverallStatus`, NOT by a source literal: on "
            "2026-09-24 Open Targets relabelled these rows from `AACT` to "
            "`ClinicalTrials.gov`, a literal match on the old name returned nothing, and "
            "the data was wrongly declared gone. The labels actually seen on this target "
            "are listed below. Stop reasons ARE categorised by Open Targets; the sponsor's "
            "free text is shown beside the category, never in place of it."
        ),
        "sources_seen": sources,
        "dedup_note": (
            f"TARGET TOTALS ARE DISTINCT NCT IDS (since 2026-09-24). Summing the per-drug "
            f"rows below gives {len(reports)} drug-trial PAIRS; the same trials are "
            f"{len(ids)} DISTINCT studies, because a trial testing two of this target's "
            f"drugs appears in both rows. Live: {len(live_all)} pairs = {len(live_ids)} "
            f"distinct. Stopped early: {len(stopped_all)} pairs = {len(stopped_ids)} "
            f"distinct, of which {n_for_cause} pair(s) are categorised FOR CAUSE "
            f"(Negative / Safety_Sideeffects)."
            + (f" {both} study/studies are live for one drug and stopped for another and "
               f"are counted in both." if both else "")
            + floor
        ),
        "n_pairs": len(reports),
        "n_pairs_live": len(live_all),
        "n_pairs_stopped": len(stopped_all),
        "n_pairs_for_cause": n_for_cause,
        "n_distinct_trials": len(ids),
        "n_distinct_live": len(live_ids),
        "n_distinct_stopped": len(stopped_ids),
        "n_live_and_stopped": both,
        "n_rows_without_an_nct_id": no_id,
        "distinct_is_a_floor": bool(no_id),
        "stop_categories": cats,
        "lead_sponsors": sorted(sponsor_counts, key=lambda s: (-sponsor_counts[s], s))[:12],
        "n_lead_sponsors": len(sponsor_counts),
        "drugs": rows,
    }


def rodent_cell(rec: OTRecord) -> Cell:
    """PLAN §2 ring 7 / QA S4 / COUNCIL M4 / COUNCIL V9. A lead, not a verdict.

    Three states and nothing more:
        no_data     not phenotyped -- which is not "no phenotype"
        value 0.0   phenotyped, no abnormal phenotype detected
        value 1.0   phenotyped, abnormal phenotype terms on record

    v1 put the *count* of abnormal terms on the ring, and the council was right
    that it cannot support that:

      (i)   `RING_SIGN["rodent"] = +1`, so more abnormal phenotypes ranked
            better. Nothing justifies that direction; a severe, pleiotropic
            knockout is at least as plausibly a safety signal. TP53 scored 249,
            the maximum, and was rewarded for it.
      (ii)  the denominator was discarded: 1 abnormal term out of 1 record and
            1 out of 50 were both 1.0.
      (iii) linear over [1, 249] put 11 of 14 cells in one indistinguishable
            band of dark purple.

    So it is "a measure of phenotyping effort multiplied by pleiotropy, pointed
    in an unargued direction". Reduced to the one honest statement M4 asked for.
    The term list stays in the drawer, where the count is context rather than a
    score, and the ring is in no score at all (V5).

    The council explicitly endorsed v1's handling of the informative negative,
    and it is kept exactly as built: `no abnormal phenotype detected` is filtered
    before the test, and a gene whose records are *all* that returns 0.0, which
    is distinct from never having been phenotyped.
    """
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    rows = rec.mouse_phenotypes
    if not rows:
        return no_data("not phenotyped — no mouse phenotype records at all, which is not "
                       "the same as 'no phenotype'")
    abnormal = [
        r for r in rows
        if NO_ABNORMAL not in str(r.get("modelPhenotypeLabel", "")).lower()
    ]
    if not abnormal:
        return value(0.0,
                     f"phenotyped, no abnormal phenotype detected "
                     f"({len(rows)} record(s), all 'no abnormal phenotype')",
                     raw={"state": "phenotyped_normal", "n_abnormal": 0, "n_rows": len(rows)})
    return value(1.0,
                 f"phenotyped, abnormal phenotype on record "
                 f"({len(abnormal)} abnormal term(s) of {len(rows)} record(s); the count is "
                 f"in the drawer and is deliberately NOT on the ring — COUNCIL V9)",
                 raw={"state": "phenotyped_abnormal", "n_abnormal": len(abnormal),
                      "n_rows": len(rows)})


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
        # v2.4: rows are selected by carrying a status, not by a source literal.
        # A row with no status is a label or registry entry, not a trial.
        with_status = [r for r in reports if r.get("trialOverallStatus")]
        statuses = [r["trialOverallStatus"] for r in with_status]
        dates = sorted(r["trialStartDate"] for r in with_status if r.get("trialStartDate"))
        stop_cats: dict[str, int] = {}
        for r in with_status:
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
            "n_with_status": len(with_status),
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
    """COUNCIL D6: one line above the table answers 'is this crowded?'.

    COUNCIL V14: the two counts overlapped, so EPHA2 read
    "3 agents, 3 active, 3 stopped for cause" -- three agents both running and
    killed. `active` counts rows whose dominant status is active; `stopped`
    counts rows with any for-cause stop anywhere in 611 trials, and those are
    not alternatives. Phrased so the counts are read as what they are.
    """
    if not rec.found or rec.drug_count is None:
        return "no Open Targets record"
    if rec.drug_count == 0:
        return "0 agents — target record exists, genuinely undrugged"
    rows = drug_pipeline_rows(rec)
    active = sum(1 for r in rows if r["status"] in ACTIVE_STATUSES)
    stopped = sum(1 for r in rows if any(c in FOR_CAUSE_CATEGORIES for c in r["stop_reasons"]))
    n_attr = sum(1 for row in rec.drug_rows if attributable_indications(rec, row))
    parts = [
        f"{rec.drug_count} agents",
        f"{active} with an active trial",
        f"{stopped} with \u22651 trial stopped for cause",
    ]
    if rec.associations is not None:
        parts.append(f"{n_attr} indicated for a disease attributable to this target")
    return " \u00b7 ".join(parts)


__all__ = [
    "OpenTargetsAdapter", "OTRecord", "TARGET_QUERY", "CLINICAL_STAGE_ORDINAL",
    "ASSOCIATION_QUERY", "MECHANISTIC_DATATYPES", "ATTRIBUTION_MIN_SCORE",
    "GENETIC_ATTRIBUTION_DATATYPES",
    "SPARSE_KEY_ABSENT_VALUE", "EXPLICIT_ZERO_KEYS", "EXPLICIT_ZERO_KEYS_NOT_READ",
    "DENSE_PRIORITISATION_KEYS", "SEVERE_LIABILITY_TERMS",
    "TRACTABLE_KEYS", "PROBE_KEYS", "SAFETY_KEYS",
    "tractable_cell", "chemical_probe_cell",
    "safety_cell", "clinical_phase_cell", "clinical_activity_cell",
    "rodent_cell", "drug_pipeline_rows", "pipeline_summary", "failed",
    "attributable_indications", "mechanistic_evidence", "severe_liabilities",
    "per_indication_stages",
    "has_prioritisation_record",
    # v2.4: the trial rows, read off the gene query instead of a per-drug hop.
    "SOURCE_LABEL", "LIVE_STATUSES", "STOPPED_STATUSES", "INERT_STATUSES",
    "ACTIVE_STATUSES", "FOR_CAUSE_CATEGORIES", "UNINFORMATIVE_CATEGORIES",
    "classify_status", "nct_id", "trial_reports", "source_counts", "trials_panel",
]
