"""Bake per-gene drawers for a finished run, so a run read from disk can open one.

2026-10-18. A run's drawers live only in `app._runs`: the dict `run_analysis`
builds per gene (drug pipeline, trials, safety, TDL, literature, every cell) is
never written to disk, and ChEMBL and Europe PMC are fetched on click. So a
run opened after a restart -- every `?run=<id>`, and every run a demo box
serves -- had no drawer, while the page kept saying "click a gene". This
module rebuilds that dict for a run from its artefacts plus the same adapters
the run used, adds ChEMBL and Europe PMC, and writes one JSON per gene under
`runs/<id>/drawer/`. `app.baked_drawer` serves the file; `view.table_payload`
names the genes that have one.

What it will not do, and why:

* **It never invents the run's numbers.** `logfc`, `padj`, `k` and every cell's
  state, value and detail are read off `values.csv` -- the artefact invariant 1
  already holds cell-for-cell against the figure. The adapters are asked only
  for what the drawer shows beyond the rings: the Open Targets drug rows,
  trials and safety liabilities, the Pharos TDL label, the PubTator3 count and
  query, ChEMBL mechanisms, the Europe PMC count.
* **It refuses a gene whose identity moved.** The symbol is re-resolved, and
  if the Ensembl id is not the one in `values.csv` the gene is skipped and
  named: a drawer for a different gene under the right label is `AGENTS.md:17`.
* **It does not bake a failure.** If ChEMBL or Europe PMC failed for a gene
  the file is not written -- a transient EBI 500 must not become the permanent
  answer, which is the same rule `cache.Cache` and `app.gene_drawer` apply.
* **A 429 stops the whole bake** (rule 9), after writing whatever genes
  completed before it. Nothing is retried.
* **No model call, no paid route.** The four paid calls are the LLM calls of
  rule 8; none is on this path. `triage` is `None`, as it is on every run.
* **Filtered runs are refused.** The drawer's `unfiltered_rings` comes from a
  per-gene record the manifest does not carry; rather than print `[]` for a
  gene a threshold kept-but-could-not-evaluate, a run with filters is skipped.

Requests are counted per host and reported, so a bake can say what it cost
the sources. Everything goes through `http.Http`, so the per-host gate applies.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import manifest as manifest_mod
from . import recommend as recommend_mod
from . import table as table_mod
from .adapters.gtex import GTExAdapter
from .adapters.literature import PubTatorAdapter
from .adapters.opentargets import (OpenTargetsAdapter, drug_pipeline_rows,
                                   pipeline_summary, severe_liabilities,
                                   trials_panel)
from .adapters.pharos import PharosAdapter, tdl_cell
from .cache import Cache
from .http import Http, RateLimited
from .order import EXTERNAL_RING_COUNT
from .resolve import Resolver

_PADJ_RE = re.compile(r"adjusted p ([0-9.eE+-]+)")

BAKED_NOTE = (
    "Drug pipeline, trials and safety liabilities from Open Targets; TDL from "
    "Pharos; the PubTator3 count; ChEMBL mechanisms and the Europe PMC count "
    "-- fetched, or read from this machine's cache, when this drawer was "
    "baked, not when you clicked. Every ring's state, value and reason is the "
    "run's own values.csv.")


class BakeRefused(RuntimeError):
    """This run cannot be baked honestly; the message says why."""


def _counting(http: Http) -> dict[str, int]:
    """Wrap one `Http`'s two request methods to count calls per host."""
    counts: dict[str, int] = {}
    get, post = http.get, http.post_json

    async def counted_get(url: str, params: dict[str, Any] | None = None):
        counts[urlsplit(url).netloc] = counts.get(urlsplit(url).netloc, 0) + 1
        return await get(url, params)

    async def counted_post(url: str, payload: dict[str, Any], check_status: bool = True):
        counts[urlsplit(url).netloc] = counts.get(urlsplit(url).netloc, 0) + 1
        return await post(url, payload, check_status=check_status)

    http.get = counted_get  # type: ignore[method-assign]
    http.post_json = counted_post  # type: ignore[method-assign]
    return counts


def _input_symbols(manifest: dict[str, Any]) -> dict[str, str]:
    """Rendered symbol -> the symbol that was submitted, for the genes the
    ledger says were renamed on the way in. Everything else was submitted as
    it is drawn."""
    out: dict[str, str] = {}
    for e in manifest.get("ledger") or []:
        if e.get("status") in ("renamed", "possible_mouse") and e.get("resolved_symbol"):
            out[e["resolved_symbol"]] = e.get("input_symbol") or e["resolved_symbol"]
    return out


def _padj(cell: Any) -> float | None:
    """The submitted adjusted p, as the run printed it in the cell's own detail
    (`run.py`: "adjusted p {padj:g} (submitted)"), falling back to the cell's
    value. None when the cell has neither -- never 0 and never 1."""
    m = _PADJ_RE.search(cell.detail or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    if cell.state == "value" and cell.value is not None:
        return 10 ** (-cell.value)
    return None


def _write_atomic(path: Path, body: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1, default=str))
    os.replace(tmp, path)


#: The two GTEx tissues the summary reads as an immune-origin hint
#: (`recommend.immune_origin_hint`). Named here so the drawer says which.
IMMUNE_HINT_TISSUES = {"whole_blood_tpm": "Whole_Blood", "spleen_tpm": "Spleen"}


async def _blood_and_spleen_tpm(gtex: Any, gencode_id: str | None) -> dict[str, Any]:
    """GTEx median TPM in whole blood and spleen for one gene, from the same
    record the run's specificity ring was read from (cached on this machine
    after the run; a miss is one free GET). None, never 0, when GTEx has no
    row -- rule 1."""
    out: dict[str, Any] = {k: None for k in IMMUNE_HINT_TISSUES}
    out["immune_hint_tissues"] = dict(IMMUNE_HINT_TISSUES)
    if not gencode_id:
        return out
    try:
        rec = await gtex.fetch_one(gencode_id)
    except Exception:  # noqa: BLE001 -- a hint, never a reason to fail the bake
        return out
    if rec.error or not rec.tissues:
        return out
    for key, tissue in IMMUNE_HINT_TISSUES.items():
        v = rec.tissues.get(tissue)
        out[key] = float(v) if isinstance(v, (int, float)) else None
    return out


async def bake_run(run_id: str, *, cache: Cache | None = None, force: bool = False,
                   log: Callable[[str], None] = print) -> dict[str, Any]:
    """Bake every gene of one run. Returns a summary:

        {"run_id", "written": [genes], "kept": [genes already baked],
         "skipped": {gene: reason}, "requests": {host: n},
         "rate_limited": str | None}

    `rate_limited` set means the bake stopped early; the genes in `written`
    are complete and on disk, the rest were not attempted.
    """
    t = table_mod.load(run_id)
    m = t.manifest
    if m.get("filters"):
        raise BakeRefused(
            f"run {run_id} has threshold filters {m['filters']!r}; the drawer's "
            f"per-gene `unfiltered_rings` is not in the manifest, so it cannot be "
            f"baked without inventing it.")
    run_dir = table_mod.run_dir(run_id)
    # `table.recent_runs` orders the picker by the run DIRECTORY's mtime, and
    # creating `drawer/` inside it bumps that -- so a bake made a 2026-10-03 run
    # the "newest" (found by `test_the_run_list_is_newest_first…`). Baking
    # does not make a run newer; the directory's times are put back afterwards.
    st = run_dir.stat()
    dir_times = (st.st_atime_ns, st.st_mtime_ns)
    out_dir = run_dir / table_mod.DRAWER_DIR
    out_dir.mkdir(exist_ok=True)
    summary: dict[str, Any] = {"run_id": run_id, "written": [], "kept": [],
                               "skipped": {}, "requests": {}, "rate_limited": None}
    try:
        return await _bake_genes(t, run_id, out_dir, summary, cache=cache,
                                 force=force, log=log)
    finally:
        os.utime(run_dir, ns=dir_times)


async def _bake_genes(t: Any, run_id: str, out_dir: Path, summary: dict[str, Any],
                      *, cache: Cache | None, force: bool,
                      log: Callable[[str], None]) -> dict[str, Any]:
    m = t.manifest

    genes = list(t.genes)
    if not force:
        already = set(table_mod.baked_drawer_genes(run_id))
        summary["kept"] = [g for g in genes if g in already]
        genes = [g for g in genes if g not in already]
    if not genes:
        return summary

    to_input = _input_symbols(m)
    # Re-resolve by the Ensembl id `values.csv` recorded where it has one --
    # that IS the identity the run drew, and `Resolver` accepts ids (PLAN §4).
    # By symbol alone, FCGR1BP came back `ambiguous` on 2026-10-18 for a run
    # that had drawn it from an Atlas row carrying its id. The symbol as
    # submitted is still what the drawer prints as `input_symbol`.
    inputs = {g: t.ensembl.get(g) or to_input.get(g, g) for g in genes}
    submitted = {g: to_input.get(g, g) for g in genes}
    cache = cache or Cache()
    baked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = manifest_mod.git_sha()

    # Imported here: `app` imports `table` and `view`, and this module is
    # imported by the script, not by the app.
    from .app import fill_drawer_only_sources

    async with Http() as http:
        counts = _counting(http)
        summary["requests"] = counts
        try:
            resolutions = await Resolver(http, cache).resolve_all(
                sorted(set(inputs.values())))
            ok: list[str] = []
            res_by_gene: dict[str, Any] = {}
            for g in genes:
                res = resolutions.get(inputs[g])
                want = t.ensembl.get(g)
                if res is None or not res.usable:
                    summary["skipped"][g] = (
                        f"did not resolve now (status={getattr(res, 'status', None)!r})")
                elif want and res.ensembl_id != want:
                    summary["skipped"][g] = (
                        f"resolves to {res.ensembl_id} now; the run drew {want}. "
                        f"Not baked: a drawer for a different gene under this label "
                        f"is the wrong figure in another shape.")
                else:
                    ok.append(g)
                    res_by_gene[g] = res
            if not ok:
                return summary

            ot = OpenTargetsAdapter(http, cache)
            records = await ot.fetch([res_by_gene[g].ensembl_id for g in ok])
            await ot.fetch_attribution(records)
            pubtator = PubTatorAdapter(http, cache)
            lit = dict(zip(ok, await asyncio.gather(
                *(pubtator.fetch_one(g, None) for g in ok))))
            pharos = await PharosAdapter(http, cache).fetch_many(
                ok, {g: res_by_gene[g].uniprot for g in ok})
            gtex = GTExAdapter(http, cache)

            for g in ok:
                res = res_by_gene[g]
                rec = records[res.ensembl_id]
                cells = {ring: t.cells[ring][g].cell for ring in t.cells if g in t.cells[ring]}
                run_tdl = cells.get("tdl")
                live_tdl = tdl_cell(pharos[g])
                # The run's TDL cell is the truth; Pharos's label is taken only
                # when it still agrees with it.
                tdl_raw = (live_tdl.raw if run_tdl is not None
                           and (live_tdl.state, live_tdl.value) == (run_tdl.state, run_tdl.value)
                           else None)
                completeness = cells.get("completeness")
                d: dict[str, Any] = {
                    "gene": g,
                    "input_symbol": submitted[g],
                    "ensembl_id": res.ensembl_id,
                    "uniprot": res.uniprot,
                    "gencode_id": res.gencode_id,
                    "resolution_status": res.status,
                    "resolution_note": res.note,
                    "logfc": cells["logfc"].value if "logfc" in cells else None,
                    "padj": _padj(cells["neg_log10_padj"]) if "neg_log10_padj" in cells else None,
                    "k": t.k.get(g),
                    "k_max": t.k_max or EXTERNAL_RING_COUNT,
                    "k_note": completeness.detail if completeness is not None else "",
                    "unfiltered_rings": [],
                    "pipeline_summary": pipeline_summary(rec),
                    "severe_liabilities": severe_liabilities(rec),
                    "attribution_error": rec.associations_error,
                    "drugs": drug_pipeline_rows(rec),
                    "safety_liabilities": rec.safety_liabilities,
                    "trials": trials_panel(rec),
                    "tdl": {
                        "state": run_tdl.state if run_tdl else "no_data",
                        "value": run_tdl.value if run_tdl else None,
                        "detail": run_tdl.detail if run_tdl else "no TDL cell in values.csv",
                        "raw": tdl_raw,
                    },
                    "literature": {
                        "pubtator_count": lit[g].count,
                        "pubtator_query": lit[g].query,
                        "years": lit[g].years,
                        "error": lit[g].error,
                        "europepmc": None,
                        "europepmc_note": "fetched on gene click (v2.2 item 3)",
                    },
                    "chembl": None,
                    "chembl_note": "fetched on gene click (v2.2 item 3)",
                    "mouse_phenotypes": [
                        p.get("modelPhenotypeLabel") for p in rec.mouse_phenotypes[:25]],
                    "cells": {
                        ring: {"state": c.state, "value": c.value, "detail": c.detail}
                        for ring, c in cells.items()},
                    "triage": None,
                }
                # The GO / NO-GO rule's expression inputs (`recommend.py`): the
                # absolute region TPM and the panel median the specificity cell
                # printed, and Open Targets' tissueDistribution key as the
                # fallback when the run has no GTEx number for the gene. Read,
                # not re-fetched. Plus, 2026-10-18 walkthrough: whole-blood and
                # spleen TPM from the same GTEx record (this machine's cache,
                # or one free GET), so the summary can say when a change in an
                # inflamed biopsy may be infiltrating immune cells; and whether
                # the run's region was inferred for the disease (the manifest's
                # run condition) or is the form's silent default.
                spec = cells.get("regional_specificity")
                spec_detail = spec.detail if spec is not None else None
                tpm, where = recommend_mod.region_tpm_from_detail(spec_detail)
                median, n_tissues = recommend_mod.panel_median_from_detail(spec_detail)
                blood_spleen = await _blood_and_spleen_tpm(gtex, res.gencode_id)
                d["expression"] = {
                    "region": where or m.get("region"),
                    "region_tpm": tpm,
                    "region_tpm_source": "GTEx median TPM, as printed in the regional_specificity cell",
                    "panel_median_tpm": median,
                    "n_tissues": n_tissues,
                    "tissue_distribution": recommend_mod._num(
                        rec.prioritisation.get("tissueDistribution")),
                    "region_is_disease_tissue":
                        recommend_mod.region_is_disease_tissue_from_manifest(m),
                    "region_is_disease_tissue_source":
                        "manifest run_conditions: 'region ... was inferred from the "
                        "disease' = True; region == the form's silent default and "
                        "not inferred = False; chosen by hand = null (no claim)",
                    **blood_spleen,
                }
                d["contrast_text"] = recommend_mod.contrast_text_from_manifest(m)
                d["recommendation"] = recommend_mod.recommend_drawer(d, manifest=m)
                filled = await fill_drawer_only_sources(d, http=http)
                if filled.pop("_retry_on_next_click", False):
                    summary["skipped"][g] = (
                        f"ChEMBL or Europe PMC failed "
                        f"({(filled.get('chembl') or {}).get('note') or filled['literature'].get('europepmc_error')}); "
                        f"not baked -- a failure is not an answer. Re-run to retry.")
                    continue
                filled["baked"] = {
                    "at_utc": baked_at, "git_sha": sha, "note": BAKED_NOTE,
                    "run_id": run_id,
                    "sources": ["opentargets.target", "pharos.target",
                                "pubtator.count", "chembl.target",
                                "chembl.mechanism", "europepmc.search"],
                }
                _write_atomic(out_dir / f"{g}.json", filled)
                summary["written"].append(g)
                log(f"  {run_id} {g}: baked")
        except RateLimited as e:
            summary["rate_limited"] = str(e)
            log(f"  {run_id}: STOPPED on 429 -- {e}")
    return summary


def restamp_recommendations(run_id: str, *, log: Callable[[str], None] = print
                            ) -> dict[str, Any]:
    """Recompute `recommendation` for every baked drawer of one run from the
    facts already in the file -- no network, no model, nothing else in the
    file touched (a baked `summary` survives). For a threshold change, or for
    drawers baked before the rule existed. Returns
    {"run_id", "restamped": [genes], "verdicts": {verdict: n}}.
    The run directory's times are put back afterwards, as in `bake_run`."""
    run_dir = table_mod.run_dir(run_id)
    st = run_dir.stat()
    dir_times = (st.st_atime_ns, st.st_mtime_ns)
    out: dict[str, Any] = {"run_id": run_id, "restamped": [], "verdicts": {}}
    # The manifest supplies what an older drawer does not carry itself: the
    # contrast text (disease-area aid) and whether the region was inferred.
    try:
        manifest: dict[str, Any] | None = table_mod.load(run_id).manifest
    except table_mod.TableUnavailable:
        manifest = None
    try:
        for g in table_mod.baked_drawer_genes(run_id):
            path = table_mod.baked_drawer_path(run_id, g)
            if path is None:
                continue
            d = json.loads(path.read_text())
            d["recommendation"] = recommend_mod.recommend_drawer(d, manifest=manifest)
            _write_atomic(path, d)
            v = d["recommendation"]["verdict"]
            out["restamped"].append(g)
            out["verdicts"][v] = out["verdicts"].get(v, 0) + 1
            log(f"  {run_id} {g}: {v}")
    finally:
        os.utime(run_dir, ns=dir_times)
    return out


__all__ = ["bake_run", "restamp_recommendations", "BakeRefused", "BAKED_NOTE"]
