"""PLAN §5's speed target, measured on a real cold 50-gene run.

"Cold 50 genes under 60 s at concurrency <= 4 per host." First measured in v2.3
at 70.5 s, of which 48.2 s was a per-drug ClinicalTrials.gov hop fetching data
that was already in the Open Targets gene query. v2.4 deleted the hop; this is
the script that says what that was worth.

Cold means an empty cache directory, not the repo's. Nothing is deleted: the
run writes to a fresh directory under `--cache`, which you can throw away.

Run: .venv/bin/python scripts/time_cold_50.py --cache /tmp/tr-cold-50 \
         > logs/cold50.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from targetring.cache import Cache  # noqa: E402
from targetring.config import MAX_CONCURRENCY_PER_HOST  # noqa: E402
from targetring.http import HOST_CONCURRENCY, HOST_PACING_SECONDS  # noqa: E402
from targetring.run import run_analysis  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(ROOT / "fixtures" / "de_50.csv"))
    ap.add_argument("--cache", required=True, help="a directory that does not exist yet")
    ap.add_argument("--region", default="Liver")
    ap.add_argument("--run-id", default="COLD50")
    args = ap.parse_args()

    cache_root = Path(args.cache)
    if cache_root.exists() and any(cache_root.iterdir()):
        raise SystemExit(f"{cache_root} is not empty; a warm cache is not a cold run")

    table = Path(args.table).read_text()
    n_genes = len([r for r in table.splitlines()[1:] if r.strip()])

    t0 = time.time()
    try:
        res = await run_analysis(
            table=table, region=args.region, run_id=args.run_id,
            cache=Cache(root=cache_root), run_regression=True,
        )
    except Exception as e:  # noqa: BLE001 -- the failure IS the measurement
        print(json.dumps({
            "ok": False, "genes": n_genes, "wall_s": round(time.time() - t0, 2),
            "error": f"{type(e).__name__}: {e}",
        }, indent=2))
        raise SystemExit(2) from e

    m = res.manifest
    print(json.dumps({
        "ok": True,
        "genes_submitted": n_genes,
        "genes_rendered": m.rendered_count,
        "ledgered": m.ledger_count,
        "wall_s": round(time.time() - t0, 2),
        "target_s": 60.0,
        "timings": m.timings,
        "cache": {"hits": 0, "misses": "cold"},
        "concurrency": {"default_per_host": MAX_CONCURRENCY_PER_HOST,
                        "overrides": HOST_CONCURRENCY,
                        "pacing_s": HOST_PACING_SECONDS},
        "invariants": {i["id"]: i["outcome"] for i in m.invariants},
        "warnings": m.warnings,
        "run_conditions": m.run_conditions,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
