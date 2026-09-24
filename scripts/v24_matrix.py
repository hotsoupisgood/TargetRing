"""v2.4: the clean cold council run, then all six fixture x region combinations.

Writes one line per combination and a JSON blob per run; nothing is deleted and
the repo cache is not touched (each run gets its own cache directory).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.screenshots import COUNCIL_LIST  # noqa: E402
from targetring.cache import Cache  # noqa: E402
from targetring.run import run_analysis  # noqa: E402

FIXTURES = {
    "de_realistic.csv": (ROOT / "fixtures" / "de_realistic.csv").read_text(),
    "de_broken.csv": (ROOT / "fixtures" / "de_broken.csv").read_text(),
    "council-liver": COUNCIL_LIST,
}


async def one(name: str, table: str, region: str, run_id: str, cache_root: Path):
    res = await run_analysis(table=table, region=region, run_id=run_id,
                             cache=Cache(root=cache_root), run_regression=True)
    m = res.manifest
    inv = {i["id"]: i["outcome"] for i in m.invariants}
    n_pass = sum(1 for v in inv.values() if v == "PASS")
    elig = (m.pareto_completeness or {}).get("n_eligible")
    print(f"{name:20s} {region:6s} {m.input_row_count:2d} in / {m.rendered_count:2d} "
          f"rendered / {m.ledger_count} ledgered · {n_pass}/7 PASS · "
          f"{elig} eligible · {m.timings.get('total_s')}s")
    return {"fixture": name, "region": region, "invariants": inv,
            "eligible": elig, "timings": m.timings,
            "conditions": m.run_conditions}


async def main() -> None:
    out = []
    # The clean cold council run, on its own empty cache.
    cold = Path("/tmp/tr-v24-council-cold")
    if cold.exists():
        raise SystemExit(f"{cold} exists; a warm cache is not a cold run")
    out.append(await one("council-liver", COUNCIL_LIST, "Liver",
                         "V24-COUNCIL-COLD", cold))
    # The matrix, sharing one cache after the cold run above.
    warm = Path("/tmp/tr-v24-matrix")
    for name, table in FIXTURES.items():
        for region in ("Liver", "Lung"):
            if name == "council-liver" and region == "Liver":
                continue
            out.append(await one(name, table, region,
                                 f"V24-{name.split('.')[0]}-{region}", warm))
    (ROOT / "logs" / "v24-matrix.json").write_text(json.dumps(out, indent=1))


asyncio.run(main())
