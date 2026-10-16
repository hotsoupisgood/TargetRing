"""Live probe: what `clinicalReports` actually carries, per target.

Written for v2.4. Not part of the build; it exists so the claims in the report
are measurements rather than recollections.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring.config import OPENTARGETS_URL
from targetring.http import Http, graphql

Q = """query P($id:String!){
  target(ensemblId:$id){
    approvedSymbol
    drugAndClinicalCandidates{count rows{drug{name}
      clinicalReports{id trialSponsor{name} trialOverallStatus
                      trialStopReasonCategories trialWhyStopped trialPhase
                      clinicalStage source}}}}
}"""


async def main(eid: str) -> None:
    async with Http() as http:
        body = await graphql(http, OPENTARGETS_URL, Q, {"id": eid})
    t = body["target"]
    rows = (t.get("drugAndClinicalCandidates") or {}).get("rows") or []
    reports = [r for row in rows for r in (row.get("clinicalReports") or [])]
    out = {
        "symbol": t["approvedSymbol"],
        "n_drug_rows": len(rows),
        "n_reports": len(reports),
        "n_with_id": sum(1 for r in reports if r.get("id")),
        "n_distinct_ids": len({r["id"] for r in reports if r.get("id")}),
        "n_with_sponsor": sum(1 for r in reports if (r.get("trialSponsor") or {}).get("name")),
        "sources": Counter(str(r.get("source")) for r in reports).most_common(),
        "statuses": Counter(str(r.get("trialOverallStatus")) for r in reports).most_common(),
        "stop_categories": Counter(
            c for r in reports for c in (r.get("trialStopReasonCategories") or [])
        ).most_common(),
        "n_why_stopped_text": sum(1 for r in reports if r.get("trialWhyStopped")),
        "top_sponsors": Counter(
            (r.get("trialSponsor") or {}).get("name") for r in reports
            if (r.get("trialSponsor") or {}).get("name")
        ).most_common(8),
        "id_prefixes": Counter(
            str(r.get("id"))[:3] for r in reports if r.get("id")
        ).most_common(),
    }
    print(json.dumps(out, indent=2))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ENSG00000157764"))
