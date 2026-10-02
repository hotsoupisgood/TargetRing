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

**`--all-designs` answers the next question, which is how often it happens.**
The verdict above settles *that* a collapse rule is needed; it does not say how
much of the index a rule would touch, and Puck cannot choose between "most
significant probe wins", "refuse multi-probe genes and ledger them" and
"highest mean expression wins" without that. So this mode reads the local
`AtlasIndex`, enumerates **every** array design in it, and probes the
experiment carrying the **most contrasts** on each — because a design's
representative should be the file that most of the index's rows would actually
be read from, not whichever experiment happened to be in a hardcoded list. Each
design's result is then weighted by the contrasts it carries, so the summary is
a fraction of the index rather than a count of designs.

The four-target list stays the default: it is the evidence behind
`docs/ATLAS-ARRAY-PROBE-2026-09-30.md` and must stay re-runnable unchanged.

A design whose files cannot be read is reported **as a failed design with its
reason**, not omitted — an unprobed design silently dropped from the weighting
would understate the affected fraction while the table looked complete. A 429
still aborts the whole sweep (rule 9).

    .venv/bin/python scripts/probe_atlas_array.py
    .venv/bin/python scripts/probe_atlas_array.py --json
    .venv/bin/python scripts/probe_atlas_array.py --max-bytes 70000000   # all four whole
    .venv/bin/python scripts/probe_atlas_array.py --all-designs --max-bytes 70000000
    .venv/bin/python scripts/probe_atlas_array.py --designs A-GEOD-13158 --max-bytes 70000000

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
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from targetring import config  # noqa: E402
from targetring.adapters import atlas as atlas_mod  # noqa: E402
from targetring.atlas_index import AtlasIndex  # noqa: E402
from targetring.contrast import GENE_ID_RE, RNASEQ  # noqa: E402
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


def designs_from_index(only: Sequence[str] | None = None) -> list[dict]:
    """Every array design in the local index, with a representative experiment.

    Ordered by contrasts carried, descending, then by design name — so the
    report reads worst-first and two runs over the same index produce the same
    table in the same order. The representative is the experiment with the most
    contrasts on that design, tie-broken by accession for the same reason.

    `n_contrasts` is the weight the summary uses. It is the count of contrasts
    the *design* carries across the whole index, not the representative
    experiment's, because a collapse rule applies to the design's file layout
    wherever it appears.
    """
    index = AtlasIndex.load()
    per_design: dict[str, dict[str, int]] = {}
    for c in index.contrasts:
        if c.platform == RNASEQ:
            continue
        per_design.setdefault(c.platform, {})
        per_design[c.platform][c.accession] = (
            per_design[c.platform].get(c.accession, 0) + 1)

    if only:
        want = {d.strip() for d in only if d.strip()}
        missing = sorted(want - set(per_design))
        if missing:
            raise HardFailure(
                f"{missing}: not array designs in the local index. Refused "
                f"rather than silently probing the rest, because a sweep that "
                f"skipped a requested design would report a fraction of a "
                f"smaller denominator than asked for. Present: "
                f"{sorted(per_design)}")
        per_design = {d: v for d, v in per_design.items() if d in want}

    out: list[dict] = []
    for design, accs in per_design.items():
        acc, n_acc = max(accs.items(), key=lambda kv: (kv[1], kv[0]))
        out.append({
            "platform": design,
            "accession": acc,
            "n_contrasts_on_design": sum(accs.values()),
            "n_experiments_on_design": len(accs),
            "n_contrasts_on_representative": n_acc,
        })
    out.sort(key=lambda r: (-r["n_contrasts_on_design"], r["platform"]))
    return [{**r, "index_built_utc": index.built_utc,
             "n_array_contrasts_in_index": sum(
                 1 for c in index.contrasts if c.platform != RNASEQ)}
            for r in out]


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


async def _probe_one(client: httpx.AsyncClient, acc: str, platform: str,
                     max_bytes: int) -> dict:
    """One design's row: the analytics grain, the methods keys, the matrix size."""
    a_url = atlas_mod.analytics_url(acc, platform)
    text, truncated, declared = await _read(client, a_url, max_bytes)
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
    return row


def _weighted_summary(results: list[dict], targets: list[dict]) -> dict:
    """What fraction of the index's array contrasts sit on an affected design.

    "Affected" means the design's representative file has at least one gene with
    more than one probe — a design where a collapse rule changes which number
    reaches the figure. A design that could not be read is counted into
    `n_contrasts_unmeasured` and into neither of the other two, because
    "measured zero" and "not measured" are different facts and folding the
    second into the first is exactly the understatement rule 1 is about.
    """
    weights = {t["platform"]: t["n_contrasts_on_design"] for t in targets}
    total = sum(weights.values())
    affected = unaffected = unmeasured = 0
    lower_bound = False
    for r in results:
        w = weights.get(r["platform"], 0)
        if r.get("probe_error"):
            unmeasured += w
        elif r["n_genes_with_multiple_rows"] > 0:
            affected += w
        else:
            unaffected += w
            # A zero read off a truncated file is a lower bound, so the
            # unaffected side of this fraction is an upper bound.
            lower_bound = lower_bound or r["multi_probe_count_is_lower_bound"]
    return {
        "n_array_contrasts_weighted": total,
        "n_contrasts_on_affected_designs": affected,
        "n_contrasts_on_unaffected_designs": unaffected,
        "n_contrasts_unmeasured": unmeasured,
        "fraction_affected": (affected / total) if total else 0.0,
        "n_designs_affected": sum(
            1 for r in results
            if not r.get("probe_error") and r["n_genes_with_multiple_rows"] > 0),
        "n_designs_unmeasured": sum(1 for r in results if r.get("probe_error")),
        "affected_fraction_is_lower_bound": lower_bound or unmeasured > 0,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable only")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"per-analytics-file ceiling (default {DEFAULT_MAX_BYTES})")
    ap.add_argument("--all-designs", action="store_true",
                    help="probe every array design in the local AtlasIndex, "
                         "representative = the experiment with the most "
                         "contrasts on that design")
    ap.add_argument("--designs", default="",
                    help="comma-separated design accessions to probe from the "
                         "index instead of all of them (implies --all-designs)")
    args = ap.parse_args()

    only = [d for d in args.designs.split(",") if d.strip()]
    if only or args.all_designs:
        targets = designs_from_index(only or None)
    else:
        # The original four, unchanged, so the 2026-09-30 doc stays reproducible
        # with no index on disk.
        targets = [{"accession": a, "platform": p, "n_contrasts_on_design": 0,
                    "n_experiments_on_design": 0,
                    "n_contrasts_on_representative": 0,
                    "index_built_utc": "", "n_array_contrasts_in_index": 0}
                   for a, p in TARGETS]

    results: list[dict] = []
    async with httpx.AsyncClient(
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True) as client:
        for t in targets:
            acc, platform = t["accession"], t["platform"]
            try:
                row = await _probe_one(client, acc, platform, args.max_bytes)
            except RateLimited:
                # Rule 9: a 429 ends the run. Not caught, not retried, and not
                # turned into a per-design failure that would let the sweep
                # carry on hammering the host.
                raise
            except (HardFailure, httpx.HTTPError) as e:
                row = {"accession": acc, "platform": platform,
                       "probe_error": f"{type(e).__name__}: {e}",
                       "gene_level": False, "n_rows_read": 0,
                       "n_unique_gene_ids": 0, "n_genes_with_multiple_rows": 0,
                       "max_rows_for_one_gene": 0, "read_whole_file": False,
                       "has_design_element": None,
                       "multi_probe_count_is_lower_bound": False}
                print(f"  FAILED {acc} / {platform}: {row['probe_error'][:160]}",
                      file=sys.stderr)
            row.update({k: t[k] for k in
                        ("n_contrasts_on_design", "n_experiments_on_design",
                         "n_contrasts_on_representative")})
            results.append(row)

    probed = [r for r in results if not r.get("probe_error")]
    all_gene_level = bool(probed) and all(bool(r["gene_level"]) for r in probed)
    verdict = ("gene-level ENSG" if all_gene_level
               else "PROBE-LEVEL (one row per Design Element)")
    out = {"verdict": verdict, "all_gene_level": all_gene_level,
           "n_designs_probed": len(probed),
           "n_designs_attempted": len(results),
           "index_built_utc": targets[0]["index_built_utc"] if targets else "",
           "multi_probe_weighted": _weighted_summary(results, targets),
           "results": results}

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if all_gene_level else 4

    print(f"VERDICT: microarray -analytics.tsv is {verdict}")
    print(f"({len(probed)} of {len(results)} array designs read)\n")
    for r in results:
        if r.get("probe_error"):
            print(f"{r['accession']} / {r['platform']}  "
                  f"[{r['n_contrasts_on_design']} contrasts] "
                  f"NOT PROBED: {r['probe_error'][:120]}\n")
            continue
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

    w = out["multi_probe_weighted"]
    if w["n_array_contrasts_weighted"]:
        print("multi-probe rate, weighted by the contrasts each design carries")
        print(f"{'design':15} {'contr':>6} {'exps':>5} {'rows':>8} {'uniq':>8} "
              f"{'multi':>6} {'max':>4}  DE  whole")
        for r in results:
            if r.get("probe_error"):
                print(f"{r['platform']:15} {r['n_contrasts_on_design']:>6} "
                      f"{r['n_experiments_on_design']:>5}  NOT PROBED")
                continue
            print(f"{r['platform']:15} {r['n_contrasts_on_design']:>6} "
                  f"{r['n_experiments_on_design']:>5} {r['n_rows_read']:>8} "
                  f"{r['n_unique_gene_ids']:>8} "
                  f"{r['n_genes_with_multiple_rows']:>6} "
                  f"{r['max_rows_for_one_gene']:>4}  "
                  f"{'y' if r['has_design_element'] else 'n'}   "
                  f"{'y' if r['read_whole_file'] else 'TRUNC'}")
        pct = 100 * w["fraction_affected"]
        bound = (" (a lower bound — see the truncated or unprobed designs above)"
                 if w["affected_fraction_is_lower_bound"] else "")
        print(f"\n{w['n_contrasts_on_affected_designs']} of "
              f"{w['n_array_contrasts_weighted']} array contrasts "
              f"({pct:.1f}%) sit on a design where multi-probe genes occur, "
              f"across {w['n_designs_affected']} design(s){bound}.")
        print(f"{w['n_contrasts_on_unaffected_designs']} sit on designs measured "
              f"1:1, and {w['n_contrasts_unmeasured']} on "
              f"{w['n_designs_unmeasured']} design(s) that could not be read.")
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
