#!/usr/bin/env python
"""Build the local Expression Atlas contrast index. Live, once, then cached.

Why this is a script and not something the app does on demand: it is ~740 GETs
over ~369 experiments and takes minutes. The app *reads* the index; only this
builds it.

    .venv/bin/python scripts/build_atlas_index.py
    .venv/bin/python scripts/build_atlas_index.py --only E-GEOD-101794 E-GEOD-6731
    .venv/bin/python scripts/build_atlas_index.py --dry-run     # count, fetch nothing

Rule 9 is not negotiated here. Concurrency is the shared `Http`'s four per host,
and a 429 aborts with a message and writes nothing — a half-crawled index would
under-count every disease in it while looking complete, which is the report's
worst failure mode in a cache file.

Exit codes: 0 built, 2 rate-limited, 3 any other hard failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import atlas_index as idx  # noqa: E402
from targetring.adapters import atlas as atlas_mod  # noqa: E402
from targetring.adapters.atlas import AtlasAdapter  # noqa: E402
from targetring.cache import Cache  # noqa: E402
from targetring.http import HardFailure, Http, RateLimited  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None,
                    help="accessions to index instead of the whole population")
    ap.add_argument("--dry-run", action="store_true",
                    help="select the population and stop; fetches the catalogue only")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"where to write the index (default {idx.index_path()})")
    ap.add_argument("--json", type=Path, default=Path("logs/atlas-index-build.json"),
                    help="the build record, for docs/")
    args = ap.parse_args()

    cache = Cache()
    t0 = time.time()
    async with Http() as http:
        if args.dry_run:
            catalogue = await AtlasAdapter(http, cache).catalogue()
            pop = atlas_mod.select_index_population(catalogue)
            print(f"catalogue: {len(catalogue)} experiments")
            print(f"population: {len(pop)} human differential with a disease factor")
            print(f"would issue {2 * len(pop)} GETs at "
                  f"<= 4 concurrent per host")
            return 0
        index = await idx.build_index(http, cache, accessions=args.only,
                                     progress=lambda m: print(m, flush=True))

    path = index.save(args.out)
    elapsed = round(time.time() - t0, 1)

    # The counts a report can quote, and the ones the picker prints.
    by_platform: dict[str, int] = {}
    for c in index.contrasts:
        by_platform[c.platform] = by_platform.get(c.platform, 0) + 1
    invalid = [c for c in index.contrasts if c.invalid_reason]
    record = {
        "built_utc": index.built_utc,
        "schema_version": index.schema_version,
        "elapsed_s": elapsed,
        "n_experiments_indexed": index.n_experiments,
        "n_experiments_in_population": len(index.population),
        "n_contrasts": index.n_contrasts,
        "n_contrasts_rnaseq": sum(1 for c in index.contrasts if c.is_rnaseq),
        "n_contrasts_microarray": sum(1 for c in index.contrasts if not c.is_rnaseq),
        "n_contrasts_invalid_id": len(invalid),
        "invalid_ids": [f"{c.accession} {c.contrast_id}" for c in invalid][:20],
        "distinct_platforms": dict(sorted(by_platform.items())),
        "failed": index.failed,
        "index_path": str(path),
        "cache_hits": cache.hits,
        "cache_misses": cache.misses,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(record, indent=2))

    print(f"\nwrote {path}")
    print(f"  {index.n_experiments}/{len(index.population)} experiments, "
          f"{index.n_contrasts} contrasts, {len(index.failed)} failed, "
          f"{len(invalid)} with an id that is not ref_test, in {elapsed}s")
    print(f"  build record -> {args.json}")
    if index.failed:
        print("\nexperiments that did not parse (named, never silently skipped):")
        for f in index.failed:
            print(f"  {f['accession']}: {f['reason'][:200]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except RateLimited as e:
        print(f"\nABORTED: {e}\n\nAGENTS.md rule 9: a 429 is a hard run failure, "
              f"never a retry loop. Nothing was written -- a half-crawled index "
              f"would under-count every disease in it. Wait, then run again; the "
              f"experiments already fetched are in the cache and cost nothing.",
              file=sys.stderr)
        raise SystemExit(2) from e
    except HardFailure as e:
        print(f"\nABORTED: {e}", file=sys.stderr)
        raise SystemExit(3) from e
