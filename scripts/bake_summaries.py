#!/usr/bin/env python
"""Bake an LLM-written evidence summary into each gene's baked drawer, so a
highlighted gene on a disk-loaded or demo run can show one.

Reads `runs/<id>/drawer/<GENE>.json` (written by `scripts/bake_drawers.py`,
which must run first), builds the facts object from the drawer and the run's
manifest, makes ONE model call per distinct fact set, and writes the text, the
model id, the prompt hash, the date, the usage and the exact facts back into the
same file under `summary`. See `targetring/summary_llm.py`.

This is a PAID call -- the fifth, on its own `summary` spend account with its
own cap (`config.SUMMARY_LLM_SPEND_CAP_USD`). Worst case is reserved before
each call and the actual committed after, in `runs/spend-ledger.jsonl`. No
fallback model, no retry. Needs an Anthropic credential in the environment;
refuses to run under TARGETRING_DEMO=1.

Usage:
    .venv/bin/python scripts/bake_summaries.py <run_id> ...      # these runs
    .venv/bin/python scripts/bake_summaries.py --force <run_id>  # re-bake
    .venv/bin/python scripts/bake_summaries.py --dry-run <run_id> # print the
        facts and the prompt hash for each gene, make no call, spend nothing
    .venv/bin/python scripts/bake_summaries.py --stop-at 3.00 ...

With no run ids the default is config.DEMO_RUNS -- which is NOT the Crohn's
allowlist in docs/DEMO-RUN-ALLOWLIST.md; pass those four ids explicitly.

Exit 0 if every gene of every run has a summary, 1 if any was skipped, 2 if the
bake stopped on the cap or the stop-at line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import config  # noqa: E402
from targetring import spend as spend_mod  # noqa: E402
from targetring import summary_llm  # noqa: E402
from targetring import table as table_mod  # noqa: E402


def dry_run(run_ids: list[str]) -> int:
    print(f"prompt_version {summary_llm.prompt_hash()}  model {config.SUMMARY_LLM_MODEL}")
    keys: set[str] = set()
    for run_id in run_ids:
        t = table_mod.load(run_id)
        for gene in t.genes:
            path = table_mod.baked_drawer_path(run_id, gene)
            if path is None:
                print(f"{run_id} {gene}: no baked drawer")
                continue
            facts = summary_llm.build_facts(json.loads(path.read_text()), t.manifest)
            k = summary_llm.facts_key(facts, config.SUMMARY_LLM_MODEL)
            keys.add(k)
            print(f"{run_id} {gene}: key {k}, ~{summary_llm._estimate_input_tokens(facts)} "
                  f"input tokens, {len(facts['rings_without_a_value'])} ring(s) without a value")
    print(f"{len(keys)} distinct fact set(s) -> {len(keys)} call(s); nothing was sent")
    return 0


def main(run_ids: list[str], force: bool, stop_at: float | None) -> int:
    if config.DEMO_MODE:
        print("refusing: TARGETRING_DEMO=1 -- a demo box makes no model call", file=sys.stderr)
        return 1
    ledger = spend_mod.default_ledger()
    before = ledger.spent(spend_mod.SUMMARY)
    client = summary_llm.AnthropicSummaryClient()
    rep = summary_llm.bake_summaries(run_ids, client=client, ledger=ledger,
                                     force=force, stop_at_usd=stop_at)
    worst = 0
    for run_id, r in rep.runs.items():
        print(f"{run_id}: written {len(r['written'])}, kept {len(r['kept'])}, "
              f"skipped {len(r['skipped'])}")
        for s in r["skipped"]:
            print(f"  SKIPPED {s}")
            worst = max(worst, 1)
    for run_id, e in rep.errors.items():
        print(f"{run_id}: ERROR {e}")
        worst = max(worst, 1)
    after = ledger.spent(spend_mod.SUMMARY)
    print(f"calls {rep.calls}; this bake ${rep.spent_usd:.4f}; summary account "
          f"${before:.4f} -> ${after:.4f} of ${config.SUMMARY_LLM_SPEND_CAP_USD:.2f}")
    if rep.stopped:
        print(f"STOPPED: {rep.stopped}")
        return 2
    return worst


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_ids", nargs="*", help="default: config.DEMO_RUNS")
    ap.add_argument("--force", action="store_true",
                    help="re-bake genes that already have a summary at this prompt hash")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the facts; make no call")
    ap.add_argument("--stop-at", type=float, default=None,
                    help="make no further call once the summary account has spent this much")
    a = ap.parse_args()
    ids = a.run_ids or list(config.DEMO_RUNS)
    if not ids:
        print("no run ids given and config.DEMO_RUNS is empty", file=sys.stderr)
        sys.exit(1)
    sys.exit(dry_run(ids) if a.dry_run else main(ids, a.force, a.stop_at))
