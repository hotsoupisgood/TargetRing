"""ClinicalTrials.gov v2 — live trial status and sponsor, hopped per drug.

**Why this exists.** Open Targets stopped emitting `AACT` as a `clinicalReports`
source on 2026-09-24 and now labels the same trials `ClinicalTrials.gov`: 0 AACT
rows across 109 targets against 4,195 in the previous night's cache
(`docs/V2-REPORT.md` §v2.1, *Newly open* 2). The activity ring read AACT only, so
it went dark for every gene. Puck's decision, 2026-09-24: hop to
ClinicalTrials.gov v2 per drug for live status and sponsor.

**Why per drug.** COUNCIL D3 verified both halves of this live. The endpoint
works: `query.intr=vemurafenib` with
`fields=NCTId,OverallStatus,Phase,LeadSponsorName,Condition,WhyStopped` returns
`totalCount: 169`, and `&filter.overallStatus=TERMINATED` narrows it to 29. But
it **cannot be keyed on a gene**: `?query.term=BRAF` returns 1,045 studies whose
top hits are cfDNA/liquid-biopsy biomarker studies with interventions listed as
"Blood sampling". Gene-keyed CT.gov returns diagnostics, not a target pipeline.
So the drug names come from Open Targets' rows and the hop is per drug.

**The two things this source is not.**

1. *It is not AACT.* CT.gov is a different population from the one the ring
   counted yesterday and the numbers are not continuous with it. Puck's hard
   condition: the ring must say which source it counted, on the figure. It does
   -- `SOURCE_LABEL` is in the ring's own label and in its legend entries, which
   are drawn on both the audit and the slide render.
2. *It does not categorise why a trial stopped.* COUNCIL D4 measured Open
   Targets' `trialStopReasonCategories` for BRAF -- `Insufficient_Enrollment 108,
   Business_Administrative 50, Negative 19, Safety_Sideeffects 13` -- and the
   whole reason this is a second ring is that only ~13% of terminations say
   anything about the target. CT.gov gives free-text `whyStopped`
   ("slow accrual led to early closure.") and nothing categorised, so **this
   build cannot tell a for-cause stop from an administrative one on CT.gov
   data** and does not pretend to. The -1.0 state is therefore "stopped, cause
   not established", not "stopped for cause", and the label says so. The free
   text travels to the drawer where a human can read it.

**Query form, measured 2026-09-24, because it is not innocent.** CT.gov
concept-expands the intervention query and the expansion is large:

    query.intr                              totalCount
    SORAFENIB                                      885
    "SORAFENIB"                                    885
    EXPANSION[None]"SORAFENIB"                       9
    SORAFENIB TOSYLATE                             167   (an AND of two terms)
    "SORAFENIB TOSYLATE"                           525
    EXPANSION[None]"SORAFENIB TOSYLATE"              1

`EXPANSION[None]` is a literal string match on the intervention name and misses
almost everything. The default expansion is what COUNCIL D3 verified. The drug
name is therefore sent as a **quoted phrase**: identical to the council's
verified form on a single-token name (885 = 885) and phrase-safe rather than an
accidental AND on a multi-token one. The exact string sent travels with every
record, into the drawer and the manifest, because a count whose query you cannot
see is not auditable (AGENTS.md rule 4).

**Rate limits.** `http.Http` caps concurrency at 4 per host and turns a 429 into
`RateLimited`, which this adapter re-raises rather than recording as a failed
cell. AGENTS.md rule 9: a 429 is a hard run failure, never a retry loop. There is
no retry anywhere in this file.

**Untrusted text.** `whyStopped`, sponsor names and condition strings are
third-party free text (AGENTS.md rule 5). They are carried as data, displayed,
and never interpreted as instructions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import CTGOV_MAX_DRUG_HOPS, CTGOV_ROWS_PER_BUCKET, CTGOV_STUDIES
from ..http import HardFailure, Http, RateLimited, describe, json_or_raise
from ..states import Cell, failed, no_data, not_applicable, value

SOURCE_LABEL = "ClinicalTrials.gov v2"

# COUNCIL D3's field list, verbatim.
CTGOV_FIELDS = "NCTId,OverallStatus,Phase,LeadSponsorName,Condition,WhyStopped"

# A trial that is running or about to. `AVAILABLE` is expanded-access, which is
# a live programme even though it is not a trial arm.
LIVE_STATUSES: tuple[str, ...] = (
    "RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION", "AVAILABLE",
)
# Stopped before it finished. Deliberately NOT called "for cause": see the
# module docstring. `COMPLETED` is not here -- a completed trial is neither live
# nor stopped early.
STOPPED_STATUSES: tuple[str, ...] = ("TERMINATED", "SUSPENDED", "WITHDRAWN")

_BUCKETS: dict[str, tuple[str, ...]] = {
    "live": LIVE_STATUSES,
    "stopped": STOPPED_STATUSES,
}


@dataclass
class CTDrug:
    """One drug's answer from ClinicalTrials.gov."""

    drug: str
    query: str
    n_total: int | None = None
    n_live: int | None = None
    n_stopped: int | None = None
    live_rows: list[dict[str, Any]] = field(default_factory=list)
    stopped_rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.n_total is not None

    @property
    def sponsors(self) -> list[str]:
        """Lead sponsors of this drug's live trials, most frequent first.

        The crowding signal Puck asked to reach the drawer: who is running a
        trial on this drug *now*.
        """
        counts: dict[str, int] = {}
        for r in self.live_rows:
            s = r.get("sponsor")
            if s:
                counts[s] = counts.get(s, 0) + 1
        return sorted(counts, key=lambda s: (-counts[s], s))


def _study(node: dict[str, Any]) -> dict[str, Any]:
    """Flatten one `protocolSection`. Nested exactly as COUNCIL D3 recorded."""
    p = node.get("protocolSection") or {}
    status = p.get("statusModule") or {}
    conditions = (p.get("conditionsModule") or {}).get("conditions") or []
    return {
        "nct_id": (p.get("identificationModule") or {}).get("nctId"),
        "status": status.get("overallStatus"),
        # Free text from the sponsor. Data, never an instruction (rule 5).
        "why_stopped": status.get("whyStopped"),
        "sponsor": ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name"),
        "conditions": [str(c) for c in conditions][:4],
        "phases": [str(x) for x in ((p.get("designModule") or {}).get("phases") or [])],
        "url": (f"https://clinicaltrials.gov/study/{(p.get('identificationModule') or {}).get('nctId')}"
                if (p.get("identificationModule") or {}).get("nctId") else None),
    }


def intervention_query(drug: str) -> str:
    """The quoted-phrase form. See the module docstring for the measurements."""
    cleaned = " ".join(str(drug).split()).replace('"', "")
    if not cleaned:
        raise HardFailure("empty drug name; refusing to query ClinicalTrials.gov for it")
    return f'"{cleaned}"'


class CTGovAdapter:
    source = "clinicaltrials.studies"

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache
        self.hops = 0
        self.budget_exhausted: list[str] = []

    async def fetch_many(self, drugs: list[str]) -> dict[str, CTDrug]:
        """One record per distinct drug name, in a deterministic order.

        Order matters because of the hop budget: which drugs get dropped when a
        very large table exhausts it must not depend on dict ordering.
        """
        wanted = sorted({d for d in (x.strip() for x in drugs) if d})
        allowed, dropped = wanted[:CTGOV_MAX_DRUG_HOPS], wanted[CTGOV_MAX_DRUG_HOPS:]
        self.budget_exhausted = dropped
        results = await asyncio.gather(*(self._one(d) for d in allowed))
        return {r.drug: r for r in results}

    async def _one(self, drug: str) -> CTDrug:
        try:
            q = intervention_query(drug)
        except HardFailure as e:
            return CTDrug(drug, "", error=describe(e))
        rec = CTDrug(drug, q)
        try:
            total, _ = await self._count(q, None, rows=0)
            rec.n_total = total
            if total == 0:
                # CT.gov knows this drug name not at all. Absence of data about
                # the drug, not evidence that nobody is trialling the target.
                rec.n_live = rec.n_stopped = 0
                return rec
            (rec.n_live, rec.live_rows), (rec.n_stopped, rec.stopped_rows) = await asyncio.gather(
                self._count(q, _BUCKETS["live"], rows=CTGOV_ROWS_PER_BUCKET),
                self._count(q, _BUCKETS["stopped"], rows=CTGOV_ROWS_PER_BUCKET),
            )
        except RateLimited:
            # AGENTS.md rule 9. Never a retry loop, never a quiet empty ring.
            raise
        except Exception as e:  # noqa: BLE001 -- recorded, surfaced as `failed`
            rec.error = describe(e)
        return rec

    async def _count(
        self, query: str, statuses: tuple[str, ...] | None, rows: int
    ) -> tuple[int, list[dict[str, Any]]]:
        """Exact count for one status bucket, plus up to `rows` examples.

        The count comes from `countTotal`, never from `len(studies)`: a page is
        a sample and a sampled count on a ring is a plausible wrong figure.
        """
        bucket = ",".join(statuses) if statuses else "all"
        key = f"{query}|{bucket}|{rows}"
        cached = self.cache.get(self.source, key)
        if cached is None:
            params: dict[str, Any] = {
                "query.intr": query,
                "fields": CTGOV_FIELDS,
                # CT.gov rejects pageSize=0; one row is the cheapest legal page.
                "pageSize": max(rows, 1),
                "countTotal": "true",
            }
            if statuses:
                params["filter.overallStatus"] = ",".join(statuses)
            self.hops += 1
            r = await self.http.get(CTGOV_STUDIES, params)
            body = json_or_raise(r)
            total = body.get("totalCount")
            if not isinstance(total, int):
                raise HardFailure(
                    f"ClinicalTrials.gov returned no integer `totalCount` for {query!r} "
                    f"(keys: {sorted(body)}); the count is the ring, so refusing it"
                )
            studies = body.get("studies")
            if studies is None:
                raise HardFailure(
                    f"ClinicalTrials.gov returned no `studies` key for {query!r}")
            parsed = [_study(s) for s in studies]
            if statuses:
                # The ChEMBL lesson (adapters/chembl.py): a filter that silently
                # fails to apply turns a typo into a confident wrong answer.
                # CT.gov 400s on an unknown *parameter* (probed), which does not
                # prove an unknown *value* narrows anything.
                wrong = sorted({p["status"] for p in parsed
                                if p["status"] and p["status"] not in statuses})
                if wrong:
                    raise HardFailure(
                        f"ClinicalTrials.gov returned statuses {wrong} for a query filtered "
                        f"to {list(statuses)}; the status filter did not apply, so this "
                        f"count is not the count that was asked for"
                    )
            cached = {"total": total, "rows": parsed[:rows]}
            self.cache.put(self.source, key, cached)
        return cached["total"], list(cached.get("rows") or [])


# --- the ring ---------------------------------------------------------------

# Encoded exactly as the AACT ring was, so the palette, the domain and the
# rank direction are unchanged: 1.0 live, 0.0 dormant, -1.0 stopped.
def activity_cell(
    drug_names: list[str],
    by_drug: dict[str, CTDrug],
    *,
    drug_count: int | None,
    budget_exhausted: bool = False,
) -> Cell:
    """COUNCIL D4 Ring B, re-sourced to ClinicalTrials.gov (v2.2 item 2).

    Three states, and the precedence is unchanged from v1 and stated there: a
    live trial outranks a historical stop, because for-cause-first labels BRAF
    "stopped" while ten of its eighteen agents are recruiting.

    What changed is the population and the meaning of -1.0. CT.gov has no
    categorised stop reason, so -1.0 is "stopped early, cause not established"
    and the free-text `whyStopped` goes to the drawer for a human to read. This
    build will not infer a for-cause stop from free text: COUNCIL D4 measured
    that seven of every eight BRAF terminations say nothing about the target.

    Partial answers are resolved in the only safe direction. One live trial
    establishes "active" whatever else failed, because the evidence is positive
    and monotone. "Dormant" and "stopped" are claims about *every* drug, so a
    single failed hop makes them unknowable and the cell is `failed`.
    """
    if not drug_count:
        return not_applicable(f"no drugs, so there is no activity to report ({SOURCE_LABEL})")
    if not drug_names:
        return no_data(
            f"{drug_count} candidate(s) in Open Targets, none carrying a drug name to hop "
            f"to {SOURCE_LABEL} with"
        )
    if budget_exhausted:
        return no_data(
            f"the {SOURCE_LABEL} hop budget ({CTGOV_MAX_DRUG_HOPS} drugs per run) was "
            f"exhausted before this gene's {len(drug_names)} drug(s) were reached. A count "
            f"over some of a target's drugs is not a count over its pipeline, so this is "
            f"absent rather than partial."
        )

    known = [by_drug[d] for d in drug_names if d in by_drug]
    ok = [r for r in known if r.ok]
    broken = [r for r in known if not r.ok]
    missing = [d for d in drug_names if d not in by_drug]

    n_live = sum(r.n_live or 0 for r in ok)
    n_stopped = sum(r.n_stopped or 0 for r in ok)
    n_total = sum(r.n_total or 0 for r in ok)
    live_drugs = [r.drug for r in ok if (r.n_live or 0) > 0]
    sponsors: list[str] = []
    for r in ok:
        for s in r.sponsors:
            if s not in sponsors:
                sponsors.append(s)

    source_note = (
        f" Counted on {SOURCE_LABEL}, per drug via query.intr, NOT the AACT population "
        f"this ring used before 2026-09-24 — the two are different populations and the "
        f"numbers are not continuous with it. Counts are drug-trial pairs summed over "
        f"this target's drugs, not distinct NCT ids: a trial testing two of them is "
        f"counted twice. The RING is a three-state reading and does not depend on the "
        f"magnitude."
    )
    incomplete = ""
    if broken or missing:
        incomplete = (
            f" {len(broken) + len(missing)} of {len(drug_names)} drug(s) did not answer"
            + (f" (first: {broken[0].drug} — {str(broken[0].error)[:90]})" if broken else "")
            + "."
        )

    raw = {
        "source": SOURCE_LABEL,
        "n_live": n_live, "n_stopped": n_stopped, "n_trials": n_total,
        "n_drugs_hopped": len(ok), "n_drugs_failed": len(broken) + len(missing),
        "live_drugs": live_drugs, "sponsors": sponsors[:12],
    }

    if n_live:
        detail = (
            f"live trial — {n_live} live drug-trial pair(s) of {n_total} on "
            f"{len(live_drugs)} of {len(drug_names)} drug(s); {len(sponsors)} lead "
            f"sponsor(s)" + (f", incl. {', '.join(sponsors[:3])}" if sponsors else "")
        )
        if n_stopped:
            detail += (f"; also {n_stopped} stopped early (cause NOT categorised by "
                       f"CT.gov — the free text is in the drawer)")
        return value(1.0, detail + incomplete + source_note, raw=raw)

    # From here on the claim is about every drug, so an unanswered hop is fatal
    # to it. "We asked and could not find out" is not "nothing is running".
    if broken or missing:
        names = [r.drug for r in broken] + missing
        return failed(
            f"{len(names)} of {len(drug_names)} drug(s) did not answer {SOURCE_LABEL}, and "
            f"no live trial was found among the {len(ok)} that did. 'Dormant' and 'stopped' "
            f"are claims about every drug, so this is unknown rather than either: "
            f"{', '.join(names[:4])}."
            + (f" First error: {str(broken[0].error)[:120]}" if broken else "")
        )

    if n_total == 0:
        return no_data(
            f"{SOURCE_LABEL} returned no study at all for any of this target's "
            f"{len(drug_names)} drug name(s) ({', '.join(drug_names[:4])}). Open Targets "
            f"holds the drug(s); CT.gov's intervention search does not match the name(s), "
            f"which is absence of trial data rather than an absence of trials."
            + source_note
        )
    if n_stopped:
        return value(-1.0,
                     f"stopped early — no live trial on any of {len(drug_names)} drug(s), "
                     f"{n_stopped} of {n_total} drug-trial pair(s) terminated, suspended or "
                     f"withdrawn. "
                     f"CT.gov does NOT categorise why (COUNCIL D4: 7 of 8 terminations say "
                     f"nothing about the target); the free-text reasons are in the drawer."
                     + source_note, raw=raw)
    return value(0.0,
                 f"dormant — {n_total} drug-trial pair(s) on record across "
                 f"{len(drug_names)} drug(s), "
                 f"none live and none stopped early." + source_note, raw=raw)


def drawer_trials(drug_names: list[str], by_drug: dict[str, CTDrug]) -> dict[str, Any]:
    """Sponsor and live status for the drawer.

    Puck's second hard condition: "sponsor plus live status must reach the
    drawer, since that is what tells the user a target is crowded." So the
    drawer gets, per drug, the exact counts and the named sponsors of the live
    trials, plus the stopped trials with their free-text reasons.
    """
    rows = []
    sponsor_counts: dict[str, int] = {}
    for name in drug_names:
        rec = by_drug.get(name)
        if rec is None:
            rows.append({"drug": name, "error": "not hopped (budget or no name)"})
            continue
        for s in rec.sponsors:
            sponsor_counts[s] = sponsor_counts.get(s, 0) + 1
        rows.append({
            "drug": rec.drug,
            "query": rec.query,
            "n_trials": rec.n_total,
            "n_live": rec.n_live,
            "n_stopped": rec.n_stopped,
            "sponsors": rec.sponsors[:6],
            "live": [
                {"nct_id": r["nct_id"], "status": r["status"], "sponsor": r["sponsor"],
                 "phases": r["phases"], "conditions": r["conditions"], "url": r["url"]}
                for r in rec.live_rows[:8]
            ],
            "stopped": [
                {"nct_id": r["nct_id"], "status": r["status"], "sponsor": r["sponsor"],
                 "why_stopped": r["why_stopped"], "conditions": r["conditions"],
                 "url": r["url"]}
                for r in rec.stopped_rows[:8]
            ],
            "error": rec.error,
        })
    rows.sort(key=lambda r: (-(r.get("n_live") or 0), -(r.get("n_trials") or 0)))
    return {
        "source": SOURCE_LABEL,
        "endpoint": CTGOV_STUDIES,
        "note": (
            "Counted on ClinicalTrials.gov v2, hopped per drug (COUNCIL D3: CT.gov cannot be "
            "keyed on a gene). This is a DIFFERENT population from the AACT rows this ring "
            "counted before 2026-09-24 and the numbers are not continuous with them. "
            "`whyStopped` is free text and is NOT categorised: a stop shown here may be "
            "administrative, and 7 of 8 BRAF terminations say nothing about the target."
        ),
        "lead_sponsors": sorted(sponsor_counts, key=lambda s: (-sponsor_counts[s], s))[:12],
        "n_lead_sponsors": len(sponsor_counts),
        "drugs": rows,
    }


__all__ = [
    "CTGovAdapter", "CTDrug", "activity_cell", "drawer_trials", "intervention_query",
    "SOURCE_LABEL", "LIVE_STATUSES", "STOPPED_STATUSES", "CTGOV_FIELDS",
]
