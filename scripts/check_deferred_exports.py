#!/usr/bin/env python
"""Report runs whose deferred download artefacts never arrived.

    .venv/bin/python scripts/check_deferred_exports.py

`run_analysis(defer_exports=True)` leaves the slide, the legend and the caption
to a background task that runs after the response. The task writes
`runs/<id>/exports.json` BEFORE it starts, because a process killed mid-render
cannot write its own epitaph — so "died" is a `running` marker whose pid is
gone, and that is what this looks for.

IT REPORTS AND CHANGES NOTHING. Two reasons, and both are this repo's own:

* `runs/` is the audit record. A script that rewrote or deleted artefacts on a
  schedule would be mutating audit records nobody asked it to touch.
* Re-rendering here would be a SECOND render path for the same files, built
  from `values.csv` and the manifest rather than from the run's own state. The
  check that exists because one render overwrote another's PNG
  (`figure.assert_artefacts_are_their_own_layout`) is the scar from exactly
  that class of bug. Re-run the analysis instead; the figure and the manifest
  of an affected run are untouched and were validated before the task began.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from targetring.config import RUNS_DIR          # noqa: E402
from targetring.run import DEFERRED_ARTEFACTS   # noqa: E402


def alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)        # type: ignore[arg-type]
        return True
    except (OSError, TypeError, ValueError):
        return False


def state_of(run_dir: Path) -> tuple[str, dict]:
    path = run_dir / "exports.json"
    if not path.exists():
        # Either an eager run (every artefact written inline) or one from
        # before deferral existed. Both are complete; neither is this script's.
        return "not deferred", {}
    try:
        rec = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return "unreadable", {}
    if rec.get("state") == "running" and not alive(rec.get("pid")):
        return "died", rec
    return str(rec.get("state", "?")), rec


def main() -> int:
    counts: dict[str, int] = {}
    problems: list[tuple[str, str, list[str]]] = []
    for d in sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()):
        state, rec = state_of(d)
        counts[state] = counts.get(state, 0) + 1
        if state in ("died", "failed", "unreadable"):
            missing = [a for a in DEFERRED_ARTEFACTS if not (d / a).exists()]
            problems.append((d.name, state, missing))

    for state, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{n:5}  {state}")
    if not problems:
        print("\nno run is missing a deferred artefact.")
        return 0

    print(f"\n{len(problems)} run(s) with artefacts that did not arrive:")
    for run_id, state, missing in problems:
        print(f"  {run_id}  [{state}]  missing {len(missing)}: "
              f"{', '.join(missing) or 'none — the files are there'}")
    print("\nThe figure and the manifest of each are unaffected and were "
          "validated before the deferred task began. To produce the missing "
          "files, re-run the analysis.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
