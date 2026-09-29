#!/usr/bin/env python
"""Write one run's table view as a single self-contained `.html` file.

    .venv/bin/python scripts/export_table.py <run_id> [<run_id> ...]
    .venv/bin/python scripts/export_table.py --latest
    .venv/bin/python scripts/export_table.py <run_id> --out docs/exports

Puck wants to read a run's table on another computer, on another network. Nothing
may be published (`AGENTS.md` rule 10), so this is the export he carries: one file,
opened from `file://`, zero network requests and zero external assets.

The work is `targetring/export.py`'s; this is the command line around it. It reads
`runs/<id>/values.csv` and `manifest.json` through `table.load` and writes one
file. It fetches nothing, spends nothing, runs no analysis, and changes no
artefact of the run it exports.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import export as export_mod          # noqa: E402
from targetring import table as table_mod            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="*", help="run id(s) under runs/")
    ap.add_argument("--latest", action="store_true",
                    help="also export the most recent run on disk")
    ap.add_argument("--out", type=Path, default=None,
                    help="directory to write into (default: the run's own "
                         "directory, beside its figure and its manifest)")
    args = ap.parse_args()

    ids = list(args.run_id)
    if args.latest:
        recent = table_mod.recent_runs(1)
        if not recent:
            print("no run under runs/ to export", file=sys.stderr)
            return 2
        ids.insert(0, recent[0]["run_id"])
    if not ids:
        ap.error("name at least one run id, or pass --latest")

    bad = 0
    for run_id in dict.fromkeys(ids):
        try:
            path = export_mod.write_export(run_id, out_dir=args.out)
        except table_mod.TableUnavailable as e:
            # The same refusal the table view makes. A run whose artefacts do not
            # add up gets no export, rather than an export that does not add up.
            print(f"{run_id}: NOT EXPORTED — {e}", file=sys.stderr)
            bad += 1
            continue
        except AssertionError as e:
            print(f"{run_id}: NOT EXPORTED — {e}", file=sys.stderr)
            bad += 1
            continue
        size = path.stat().st_size
        print(f"{run_id}  {path}  {size:,} bytes ({size / 1024:.0f} KiB)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
