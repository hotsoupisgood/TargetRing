#!/usr/bin/env python
"""Bake per-gene drawer JSON for finished runs, so a run read from disk -- a
`?run=<id>` after a restart, or a demo box -- can open a gene's drawer.

Writes `runs/<id>/drawer/<GENE>.json`, served by `/api/run/<id>/drawer/<GENE>`.
See `targetring/drawer_bake.py` for what is read off the artefacts and what is
fetched, and for the four refusals (moved identity, failed source, 429,
filtered run).

Free sources only -- Open Targets, Pharos, PubTator3, ChEMBL, Europe PMC -- all
through `http.Http`'s per-host gate (rule 9). No model call. A 429 stops the
bake and exits 2; nothing is retried.

Usage:
    .venv/bin/python scripts/bake_drawers.py                # config.DEMO_RUNS
    .venv/bin/python scripts/bake_drawers.py <run_id> ...   # these runs
    .venv/bin/python scripts/bake_drawers.py --force ...    # re-bake baked genes
    .venv/bin/python scripts/bake_drawers.py --recommend-only <run_id> ...
        # recompute only the GO / NO-GO `recommendation` from what is on disk

Each drawer also carries `recommendation`: Puck's GO / NO-GO rule
(`targetring/recommend.py`), computed from the drawer's own facts and never
used to order or filter the figure.

Exit 0 if every gene of every run is on disk, 1 if any gene was skipped,
2 on a 429.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import config  # noqa: E402
from targetring import drawer_bake  # noqa: E402
from targetring import table as table_mod  # noqa: E402


async def main(run_ids: list[str], force: bool) -> int:
    worst = 0
    for run_id in run_ids:
        print(f"{run_id}:")
        try:
            s = await drawer_bake.bake_run(run_id, force=force)
        except (drawer_bake.BakeRefused, table_mod.TableUnavailable) as e:
            print(f"  REFUSED: {e}")
            worst = max(worst, 1)
            continue
        req = ", ".join(f"{h} {n}" for h, n in sorted(s["requests"].items())) or "none"
        print(f"  written {len(s['written'])}, already baked {len(s['kept'])}, "
              f"skipped {len(s['skipped'])}; requests: {req}")
        for g, why in s["skipped"].items():
            print(f"  SKIPPED {g}: {why}")
            worst = max(worst, 1)
        if s["rate_limited"]:
            print(f"  RATE LIMITED, bake stopped: {s['rate_limited']}")
            return 2
        on_disk = table_mod.baked_drawer_genes(run_id)
        print(f"  on disk now: {len(on_disk)} drawer(s) -> {table_mod.run_dir(run_id) / table_mod.DRAWER_DIR}")
    return worst


def recommend_only(run_ids: list[str]) -> int:
    """`--recommend-only`: rewrite each baked drawer's `recommendation` (the
    GO / NO-GO rule, `targetring/recommend.py`) from the facts already in the
    file. No network, no model; a baked `summary` is left as it is."""
    worst = 0
    for run_id in run_ids:
        print(f"{run_id}:")
        try:
            s = drawer_bake.restamp_recommendations(run_id, log=lambda _s: None)
        except table_mod.TableUnavailable as e:
            print(f"  REFUSED: {e}")
            worst = 1
            continue
        counts = ", ".join(f"{v} {n}" for v, n in sorted(s["verdicts"].items())) or "none"
        print(f"  restamped {len(s['restamped'])} drawer(s): {counts}")
    return worst


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_ids", nargs="*", help="default: config.DEMO_RUNS")
    ap.add_argument("--force", action="store_true",
                    help="re-bake genes that already have a drawer file")
    ap.add_argument("--recommend-only", action="store_true",
                    help="only recompute the GO / NO-GO `recommendation` in drawers "
                         "already on disk; no request, no model call")
    a = ap.parse_args()
    ids = a.run_ids or list(config.DEMO_RUNS)
    if not ids:
        print("no run ids given and config.DEMO_RUNS is empty", file=sys.stderr)
        sys.exit(1)
    if a.recommend_only:
        sys.exit(recommend_only(ids))
    sys.exit(asyncio.run(main(ids, a.force)))
