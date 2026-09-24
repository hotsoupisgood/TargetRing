"""Eligibility and the Pareto front on the two reference gene sets.

The before/after evidence for a change that moves a Pareto axis. Prints, per
fixture: how many genes are eligible for the headline, the front, whether the
diamond is drawn at all (COUNCIL V10's PARETO_MIN_ELIGIBLE), and which axis
each ineligible gene is missing -- so a drop in eligibility names its cause
instead of being a number that moved.

Run: .venv/bin/python scripts/pareto_census.py [--label NAME] > logs/<file>

Warm cache by design: it measures the ranking, not the network.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.screenshots import COUNCIL_LIST  # noqa: E402
from targetring.figure import PARETO_MIN_ELIGIBLE  # noqa: E402
from targetring.rank import PARETO_AXES  # noqa: E402
from targetring.run import run_analysis  # noqa: E402

FIXTURES = {
    "council-liver": (COUNCIL_LIST, "Liver"),
    "de_realistic": ((ROOT / "fixtures" / "de_realistic.csv").read_text(), "Liver"),
}


async def census(name: str, table: str, region: str) -> dict:
    res = await run_analysis(
        table=table, region=region, run_id=f"CENSUS-{name}", run_regression=False,
    )
    audit = res.manifest.pareto_completeness
    return {
        "fixture": name,
        "region": region,
        "axes": list(PARETO_AXES),
        "n_genes": audit["n_genes"],
        "n_eligible": audit["n_eligible"],
        "front": sorted(g for g, d in res.drawers.items() if d["pareto"]),
        "diamond_drawn": audit["n_eligible"] >= PARETO_MIN_ELIGIBLE,
        "missing_cells_per_axis": audit["missing_cells_per_axis"],
        "ineligible": audit["ineligible_genes"],
        "invariants": {i["id"]: i["outcome"] for i in res.manifest.invariants},
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="free text stamped on the output")
    args = ap.parse_args()

    out = {"label": args.label, "axes": list(PARETO_AXES), "fixtures": []}
    for name, (table, region) in FIXTURES.items():
        out["fixtures"].append(await census(name, table, region))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
