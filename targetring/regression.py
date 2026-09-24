"""The five-ID regression test, verbatim from QA B2 / PLAN.md §6.6.

> Submit the exact five IDs above. Assert: 5 spokes rendered; the spoke for
> `ENSG00000999999` carries status `missing`; `BRAF`'s tractability values equal
> those from a single-target query for `ENSG00000157764`.
>
> The second assertion is the one that catches the off-by-one — a count check
> alone passes while the labels are wrong.

Run against the live API on every analysis, and reported as invariant I6.
"""

from __future__ import annotations

import json
from typing import Any

from .adapters.opentargets import OpenTargetsAdapter, TARGET_QUERY
from .cache import Cache
from .config import OPENTARGETS_URL
from .http import Http, graphql

FIVE_IDS: list[str] = [
    "ENSG00000157764",  # BRAF
    "ENSG00000999999",  # well-formed, nonexistent
    "ENSG00000141510",  # TP53
    "ENSG00000012048",  # BRCA1
    "ENSG00000146648",  # EGFR
]
MISSING_ID = "ENSG00000999999"
BRAF = "ENSG00000157764"

SINGLE_QUERY = """query One($id:String!){
  target(ensemblId:$id){
    id approvedSymbol
    tractability{label modality value}
    prioritisation{items{key value}}
    drugAndClinicalCandidates{count}
  }
}"""


async def five_id_regression(http: Http, cache: Cache | None = None) -> dict[str, Any]:
    """Returns {ok, detail, checks}. Never raises — a failure is data."""
    # Cache deliberately bypassed: this test exists to exercise the live batch
    # join, and a warm cache would let a regression in it pass unnoticed.
    adapter = OpenTargetsAdapter(http, Cache(enabled=False))
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    # A source outage and a broken join are different facts. If the API never
    # answered, the regression was not evaluated -- reporting FAIL would tell
    # Puck his join logic regressed when Open Targets simply 502'd at 2 a.m.
    # NOT_EVALUATED still raises the banner, so nothing is quietly excused.
    unavailable: list[str] = []

    try:
        recs = await adapter.fetch(FIVE_IDS)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "unavailable": True, "checks": checks,
                "detail": f"batch fetch could not complete: {e}"}

    # 1. Five spokes -- every requested id got a record.
    add("five_spokes", len(recs) == 5 and set(recs) == set(FIVE_IDS),
        f"{len(recs)} records for 5 requested ids")

    # 2. The nonexistent id is explicitly missing, not omitted and not defaulted.
    missing = recs.get(MISSING_ID)
    add("missing_is_explicit",
        missing is not None and missing.found is False and missing.approved_symbol is None,
        f"{MISSING_ID}: found={getattr(missing, 'found', 'absent')}")

    # 3. The off-by-one catcher: BRAF's values must equal the single-target
    #    query's. A count check alone passes while the labels are wrong.
    try:
        single = await graphql(http, OPENTARGETS_URL, SINGLE_QUERY, {"id": BRAF})
        one = single["target"]
    except Exception as e:  # noqa: BLE001
        unavailable.append(f"single-target query could not complete: {e}")
        one = None

    if one is not None:
        batch = recs.get(BRAF)
        if batch is None or not batch.found:
            add("braf_matches_single_query", False, "BRAF absent from the batch response")
        else:
            want_tract = _tract_key(one["tractability"])
            got_tract = _tract_key(batch.tractability)
            want_prio = {i["key"]: str(i["value"]) for i in one["prioritisation"]["items"]}
            got_prio = {k: _fmt(v) for k, v in batch.prioritisation.items()}
            same_symbol = batch.approved_symbol == one["approvedSymbol"] == "BRAF"
            same_tract = want_tract == got_tract
            same_count = batch.drug_count == one["drugAndClinicalCandidates"]["count"]
            same_prio = all(
                _close(want_prio.get(k), got_prio.get(k)) for k in want_prio
            )
            ok = same_symbol and same_tract and same_count and same_prio
            add("braf_matches_single_query", ok,
                f"symbol={same_symbol} tractability={same_tract} "
                f"({len(got_tract)} entries) drug_count={same_count} "
                f"({batch.drug_count} vs {one['drugAndClinicalCandidates']['count']}) "
                f"prioritisation={same_prio}"
                + ("" if same_tract else
                   f" | batch-only={sorted(set(got_tract) - set(want_tract))[:3]}"))

    # 4. The gap must not have shifted the survivors -- the exact B2 failure.
    shifted = [
        f"{eid}->{recs[eid].approved_symbol}"
        for eid, expect in (("ENSG00000141510", "TP53"), ("ENSG00000012048", "BRCA1"),
                            ("ENSG00000146648", "EGFR"))
        if recs.get(eid) is None or recs[eid].approved_symbol != expect
    ]
    add("survivors_not_shifted", not shifted,
        "TP53 / BRCA1 / EGFR each on their own id" if not shifted
        else f"MISLABELLED: {shifted}")

    ok = all(c["ok"] for c in checks) and not unavailable
    detail = "; ".join(f"{c['check']}={'ok' if c['ok'] else 'FAIL'}" for c in checks)
    if unavailable:
        detail += " | NOT EVALUATED: " + "; ".join(u[:200] for u in unavailable)
    elif not ok:
        detail += " | " + json.dumps([c for c in checks if not c["ok"]])[:400]
    return {"ok": ok, "unavailable": bool(unavailable), "detail": detail, "checks": checks}


def _tract_key(rows) -> dict[str, bool]:
    return {f"{r['modality']}|{r['label']}": bool(r["value"]) for r in rows}


def _fmt(v: float) -> str:
    return f"{v:.10g}"


def _close(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return a == b


__all__ = ["five_id_regression", "FIVE_IDS", "MISSING_ID"]
