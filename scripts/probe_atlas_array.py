#!/usr/bin/env python
"""Live probe: is microarray `-analytics.tsv` gene-level ENSG, or probe-level?

The one thing gating stage 6. RNA-seq's `{ACC}-analytics.tsv` carries
`Gene ID, Gene Name, {contrast}.p-value, {contrast}.log2foldchange` and is
`ENSG`-shaped in 65,217/65,217 rows, one row per gene (spike §5). Nothing
verified said the microarray layout `{ACC}_{PLATFORM}-analytics.tsv` does the
same, and the two possibilities need different work:

* **gene-level** — stage 6 is the second file layout plus a second heatmap
  recipe, and nothing else changes.
* **probe-level** — a probe→gene collapse is needed, and *which probe wins for a
  gene* (max? mean? refuse ties?) is a study-design decision for Puck. Guessing
  it silently produces a figure where every number is right and the gene it is
  attributed to is wrong, which is `AGENTS.md:17`'s worst output.

**The id column's shape does not answer this**, which is the trap this probe
exists to avoid walking into. Microarray's first column *is* `Gene ID` and every
value in it *is* ENSG-shaped, exactly like RNA-seq's. A probe that measured only
the identifier pattern would report "gene-level" and be wrong. What settles it is
the row grain, and there are two independent signals:

1. a **`Design Element`** column — the probe id — which RNA-seq does not have;
2. **repeated `Gene ID` values**, one row per probe, each with its own p and
   log2FC for the same gene in the same contrast.

Four distinct array designs are probed, because whether a gene has more than one
probe turns out to vary by experiment — the same "sporadic per-experiment
property, assert every time, never predict" shape the spike found for duplicated
raw-counts columns. One experiment would have answered the question wrongly:
A-GEOD-13158 and A-AFFY-1 are 1:1 and look gene-level; A-AFFY-44 and A-AFFY-141
are not.

    .venv/bin/python scripts/probe_atlas_array.py
    .venv/bin/python scripts/probe_atlas_array.py --json
    .venv/bin/python scripts/probe_atlas_array.py --max-bytes 70000000   # all four whole

Rule 9 holds: requests are sequential (well under the four-per-host cap) and a
429 aborts with a message rather than retrying. Nothing is written to `cache/` —
a probe's job is to observe today's files, not to seed the app's cache with
partial reads. Read-only GETs, zero spend.

Exit codes: 0 gene-level on every design probed, 4 probe-level on any of them
(stage 6 stops and reports), 2 rate-limited, 3 any other hard failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from targetring import config  # noqa: E402
from targetring.adapters import atlas as atlas_mod  # noqa: E402
from targetring.contrast import GENE_ID_RE  # noqa: E402
from targetring.http import USER_AGENT, HardFailure, RateLimited  # noqa: E402

#: (accession, array design) pairs, one per distinct design in the Crohn's
#: index, most-contrasts-first. Hardcoded rather than read from the index so the
#: probe can be re-run against a fresh checkout with no index built.
TARGETS = [
    ("E-GEOD-100833", "A-GEOD-13158"),   # 108 of 166 indexed Crohn's contrasts
    ("E-GEOD-102133", "A-AFFY-141"),     # 5
    ("E-GEOD-16879", "A-AFFY-44"),       # 12
    ("E-GEOD-6731", "A-AFFY-1"),         # 2
]

#: Default ceiling per analytics file. 12 MB reads three of the four designs
#: whole; E-GEOD-100833's analytics is 61.5 MB and is truncated at this default,
#: which the report states per design rather than leaving the reader to assume a
#: complete count. The `Design Element` finding is in the header and so is
#: unaffected by truncation; only the multi-probe *count* becomes a lower bound.
DEFAULT_MAX_BYTES = 12_000_000

#: An Affymetrix probe set id (`1007_s_at`, `AFFX-BioB-5_at`) and an Illumina /
#: GEO platform probe id (`ILMN_1343291`, `A_23_P100001`). Only used to describe
#: what the `Design Element` column contains.
PROBEISH_RE = re.compile(r"(_at$|^AFFX-|^ILMN_|^A_\d+_P\d+|^\d{7,}$)")

#: The column that settles the question.
DESIGN_ELEMENT_COL = "Design Element"


async def _read(client: httpx.AsyncClient, url: str,
                max_bytes: int) -> tuple[str, bool, int]:
    """`(text, was_truncated, content_length)`, streamed and closed early.

    Streamed rather than fetched whole because these files run from 0.8 MB to
    474 MB and nothing here needs more than the ceiling the caller sets.
    """
    async with client.stream("GET", url) as r:
        if r.status_code == 429:
            raise RateLimited(f"429 from {url}; rule 9 — no retry loop")
        if r.status_code != 200:
            raise HardFailure(f"HTTP {r.status_code} from {url}")
        ctype = (r.headers.get("content-type") or "").lower()
        if not any(e in ctype for e in ("tab-separated", "text/plain")):
            raise HardFailure(
                f"expected a TSV from {url}, got content-type {ctype!r}")
        declared = int(r.headers.get("content-length") or 0)
        chunks: list[bytes] = []
        total = 0
        async for chunk in r.aiter_bytes(1 << 20):
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
    truncated = bool(declared) and total < declared
    return b"".join(chunks).decode("utf-8", errors="replace"), truncated, declared


def _classify(text: str, url: str, truncated: bool, declared: int) -> dict:
    """Whether this analytics file is one row per gene or one row per probe."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        raise HardFailure(f"{url} gave {len(lines)} usable line(s); expected a "
                          f"header and data rows")
    header = lines[0].split("\t")
    # A truncated stream's last line is a partial row -- dropped, never parsed.
    data = lines[1:-1] if truncated else lines[1:]
    ids = [ln.split("\t")[0].strip() for ln in data]

    counts: dict[str, int] = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    dup_ids = {k: v for k, v in counts.items() if v > 1}
    has_design_element = DESIGN_ELEMENT_COL in header
    de_i = header.index(DESIGN_ELEMENT_COL) if has_design_element else None

    # The whole decision, made concrete: one gene, one contrast, several probes,
    # several different answers. This is what a collapse rule has to resolve.
    example: list[dict] = []
    if dup_ids:
        worst = max(dup_ids, key=lambda k: dup_ids[k])
        first_contrast = header[de_i + 1].split(".")[0] if de_i is not None else ""
        cols = {c: header.index(c) for c in header
                if c.startswith(f"{first_contrast}.")}
        for ln in data:
            parts = ln.split("\t")
            if parts[0].strip() != worst:
                continue
            example.append({
                "gene_id": parts[0].strip(),
                "gene_name": parts[1].strip() if len(parts) > 1 else "",
                "design_element": (parts[de_i].strip()
                                   if de_i is not None and len(parts) > de_i
                                   else ""),
                "contrast": first_contrast,
                **{c.split(".", 1)[1]: (parts[i].strip() if len(parts) > i else "")
                   for c, i in cols.items()},
            })

    probe_col_values = ([ln.split("\t")[de_i].strip() for ln in data[:200]
                         if len(ln.split("\t")) > de_i]
                        if de_i is not None else [])
    return {
        "url": url,
        "declared_bytes": declared,
        "read_whole_file": not truncated,
        "id_column_name": header[0],
        "header_prefix": header[:4],
        "n_columns": len(header),
        "has_design_element": has_design_element,
        "design_element_examples": probe_col_values[:4],
        "n_design_elements_probeish": sum(1 for v in probe_col_values
                                          if PROBEISH_RE.search(v)),
        "n_rows_read": len(ids),
        "n_ensg_in_id_column": sum(1 for i in ids if GENE_ID_RE.match(i)),
        "n_unique_gene_ids": len(set(ids)),
        "n_genes_with_multiple_rows": len(dup_ids),
        "max_rows_for_one_gene": max(dup_ids.values()) if dup_ids else 1,
        "multi_probe_count_is_lower_bound": truncated,
        "multi_probe_example": example,
        "statistic_suffixes": sorted({c.split(".", 1)[1] for c in header
                                      if "." in c}),
        # Gene-level requires BOTH: no probe column, and no repeated gene id.
        "gene_level": (len(ids) > 0
                       and sum(1 for i in ids if GENE_ID_RE.match(i)) == len(ids)
                       and not has_design_element and not dup_ids),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable only")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"per-analytics-file ceiling (default {DEFAULT_MAX_BYTES})")
    args = ap.parse_args()

    results: list[dict] = []
    async with httpx.AsyncClient(
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True) as client:
        for acc, platform in TARGETS:
            a_url = atlas_mod.analytics_url(acc, platform)
            text, truncated, declared = await _read(client, a_url, args.max_bytes)
            row = _classify(text, a_url, truncated, declared)
            row["accession"], row["platform"] = acc, platform

            # What the pipeline row is actually called, and whether an Ensembl
            # release is stated at all. Both differ from RNA-seq (see the doc).
            m_text, _, _ = await _read(
                client, atlas_mod.analysis_methods_url(acc), 200_000)
            methods = atlas_mod.parse_analysis_methods(m_text, acc)
            row["analysis_methods_keys"] = sorted(methods)
            row["pipeline_row"] = next(
                (f"{k}: {v[:90]}" for k, v in methods.items()
                 if "differential" in k.lower()), "")
            row["states_ensembl_release"] = any(
                "Ensembl release" in v for v in methods.values())

            ne_url = atlas_mod.normalized_expressions_url(acc, platform)
            head = await client.head(ne_url)
            row["normalized_expressions"] = {
                "url": ne_url, "status": head.status_code,
                "declared_bytes": int(head.headers.get("content-length") or 0),
            }
            results.append(row)

    all_gene_level = all(bool(r["gene_level"]) for r in results)
    verdict = ("gene-level ENSG" if all_gene_level
               else "PROBE-LEVEL (one row per Design Element)")
    out = {"verdict": verdict, "all_gene_level": all_gene_level,
           "n_designs_probed": len(results), "results": results}

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if all_gene_level else 4

    print(f"VERDICT: microarray -analytics.tsv is {verdict}")
    print(f"({len(results)} array designs probed)\n")
    for r in results:
        whole = "whole file" if r["read_whole_file"] else "TRUNCATED"
        print(f"{r['accession']} / {r['platform']}  "
              f"[{r['declared_bytes'] / 1e6:.1f} MB, {whole}]")
        print(f"  header          {r['header_prefix']} ({r['n_columns']} cols)")
        print(f"  Design Element  {r['has_design_element']}  "
              f"e.g. {r['design_element_examples'][:3]}")
        print(f"  rows read       {r['n_rows_read']}  "
              f"({r['n_unique_gene_ids']} unique gene ids, "
              f"{r['n_ensg_in_id_column']} ENSG-shaped)")
        bound = " (lower bound)" if r["multi_probe_count_is_lower_bound"] else ""
        print(f"  multi-probe     {r['n_genes_with_multiple_rows']} gene(s), "
              f"max {r['max_rows_for_one_gene']} rows for one gene{bound}")
        print(f"  stat suffixes   {r['statistic_suffixes']}")
        print(f"  pipeline row    {r['pipeline_row'][:80]!r}")
        print(f"  Ensembl release stated: {r['states_ensembl_release']}")
        ne = r["normalized_expressions"]
        print(f"  normalized-expressions: HTTP {ne['status']}, "
              f"{ne['declared_bytes'] / 1e6:.1f} MB")
        for ex in r["multi_probe_example"]:
            print(f"    {ex['gene_name']} {ex['gene_id']} probe "
                  f"{ex['design_element']}: contrast {ex['contrast']} "
                  f"p={ex.get('p-value')} log2FC={ex.get('log2foldchange')}")
        print()
    if not all_gene_level:
        print("STOP. The row grain is the probe, not the gene. A probe→gene "
              "collapse (which probe wins for a gene: most significant? mean? "
              "refuse ties?) is a study-design decision for Puck, not an "
              "improvisation — so stage 6 does not proceed on a guess.")
    return 0 if all_gene_level else 4


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except RateLimited as e:
        print(f"RATE LIMITED: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    except HardFailure as e:
        print(f"HARD FAILURE: {e}", file=sys.stderr)
        raise SystemExit(3) from e
