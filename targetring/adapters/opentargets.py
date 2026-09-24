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


def _mean_of_keys(rec: OTRecord, keys: tuple[str, ...], label: str) -> Cell:
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    present = [rec.prioritisation[k] for k in keys if k in rec.prioritisation]
    if not present:
        return no_data(f"none of the {label} prioritisation keys are scored for this gene")
    # Stated reduction: unweighted mean of the available [-1,1] keys. Keys that
    # are absent are absent -- they are not averaged in as zero.
    return value(
        sum(present) / len(present),
        f"mean of {len(present)}/{len(keys)} keys: "
        + ", ".join(f"{k}={rec.prioritisation[k]:+.2f}" for k in keys if k in rec.prioritisation),
        raw={k: rec.prioritisation.get(k) for k in keys},
    )


def druggability_cell(rec: OTRecord) -> Cell:
    return _mean_of_keys(rec, DRUGGABILITY_KEYS, "druggability")


def safety_cell(rec: OTRecord) -> Cell:
    return _mean_of_keys(rec, SAFETY_KEYS, "safety")


def clinical_phase_cell(rec: OTRecord) -> Cell:
    """COUNCIL D4 Ring A -- furthest phase reached."""
    if not rec.found:
        return no_data(f"{rec.ensembl_id} not found in Open Targets")
    if rec.drug_count is None:
        return no_data("drugAndClinicalCandidates absent")
    if rec.drug_count == 0:
        # COUNCIL D5: OT distinguishes a meaningful zero from no data. A target
        # record that exists and reports 0 candidates is genuinely undrugged.
        return value(0.0, "target record exists, 0 drug or clinical candidates", raw=0)
    stages = [
        CLINICAL_STAGE_ORDINAL[s]
        for row in rec.drug_rows
        if (s := row.get("maxClinicalStage")) in CLINICAL_STAGE_ORDINAL
    ]
    if not stages:
        return value(1.0, f"{rec.drug_count} candidates, none with a mapped clinical stage",
                     raw=rec.drug_count)
    top = max(stages)
    return value(float(top), f"{STAGE_LABEL[top]} ({rec.drug_count} candidates)", raw=top)


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
    "druggability_cell", "safety_cell", "clinical_phase_cell", "clinical_activity_cell",
    "rodent_cell", "drug_pipeline_rows", "pipeline_summary", "failed",
]
