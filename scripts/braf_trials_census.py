"""v2.4 item 4: BRAF's trial counts, pairs vs distinct NCT ids, from the code
that ships. Comparable with the v2.3 figures the report already carries."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring.adapters.opentargets import (
    OpenTargetsAdapter, clinical_activity_cell, source_counts, trials_panel)
from targetring.cache import Cache
from targetring.http import Http


async def main() -> None:
    async with Http() as http:
        recs = await OpenTargetsAdapter(http, Cache(enabled=False)).fetch(
            ["ENSG00000157764"])
    braf = recs["ENSG00000157764"]
    panel = trials_panel(braf)
    cell = clinical_activity_cell(braf)
    print(json.dumps({
        "drugs": braf.drug_count,
        "pairs": panel["n_pairs"],
        "distinct": panel["n_distinct_trials"],
        "pairs_live": panel["n_pairs_live"],
        "distinct_live": panel["n_distinct_live"],
        "pairs_stopped": panel["n_pairs_stopped"],
        "distinct_stopped": panel["n_distinct_stopped"],
        "pairs_for_cause": panel["n_pairs_for_cause"],
        "live_and_stopped": panel["n_live_and_stopped"],
        "rows_without_nct": panel["n_rows_without_an_nct_id"],
        "n_lead_sponsors": panel["n_lead_sponsors"],
        "lead_sponsors": panel["lead_sponsors"][:5],
        "stop_categories": panel["stop_categories"],
        "sources": source_counts(braf),
        "ring_state": cell.state,
        "ring_value": cell.value,
        "ring_detail": cell.detail,
    }, indent=1))


asyncio.run(main())
