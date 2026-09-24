"""FastAPI app. Local only — no deploy, no public URL (AGENTS.md rule 10).

Submit a DE table and a region, get the figure; click a gene for its drug
pipeline and its literature; download the SVG and the CSV.
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .adapters.chembl import ChEMBLAdapter, mechanism_summary
from .adapters.literature import EuropePMCAdapter
from .cache import Cache
from .config import RUNS_DIR
from .de_input import InputRejected
from .http import HardFailure, Http
from .run import DEFAULT_REGION, available_regions, run_analysis

app = FastAPI(title="targetring", docs_url="/api/docs")

_runs: dict[str, Any] = {}
_regions_cache: list[str] = []

STATIC = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC / "index.html").read_text()


@app.get("/app.js")
async def appjs() -> FileResponse:
    return FileResponse(STATIC / "app.js", media_type="application/javascript")


@app.get("/app.css")
async def appcss() -> FileResponse:
    return FileResponse(STATIC / "app.css", media_type="text/css")


@app.get("/api/regions")
async def regions() -> dict[str, Any]:
    global _regions_cache
    if not _regions_cache:
        try:
            _regions_cache = await available_regions(Cache())
        except Exception as e:  # noqa: BLE001
            return {"regions": [DEFAULT_REGION], "error": str(e),
                    "note": "GTEx tissue list unavailable; falling back to the default region"}
    return {"regions": _regions_cache, "default": DEFAULT_REGION}


@app.get("/api/example")
async def example() -> dict[str, str]:
    p = Path(__file__).parent.parent / "fixtures" / "de_realistic.csv"
    broken = Path(__file__).parent.parent / "fixtures" / "de_broken.csv"
    return {
        "realistic": p.read_text() if p.exists() else "",
        "broken": broken.read_text() if broken.exists() else "",
    }


@app.post("/api/analyse")
async def analyse(
    table: str = Form(""),
    region: str = Form(DEFAULT_REGION),
    file: UploadFile | None = None,
) -> JSONResponse:
    raw: bytes | str
    if file is not None and file.filename:
        raw = await file.read()
    elif table.strip():
        raw = table
    else:
        raise HTTPException(400, "Provide a DE table, pasted or uploaded.")

    try:
        result = await run_analysis(table=raw, region=region)
    except InputRejected as e:
        # The whole file is unusable. Distinct from per-row drops, and the
        # message is the user's instruction for fixing it.
        return JSONResponse({"error": "input_rejected", "message": str(e)}, status_code=422)
    except HardFailure as e:
        return JSONResponse(
            {"error": "source_failure", "message": str(e),
             "note": "A source failed hard. No figure is produced rather than an empty one."},
            status_code=502,
        )
    except AssertionError as e:
        return JSONResponse(
            {"error": "invariant_abort", "message": str(e),
             "note": "Gene accounting failed before rendering; the run was aborted."},
            status_code=500,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": "unexpected", "message": f"{type(e).__name__}: {e}",
             "traceback": traceback.format_exc()[-2000:]}, status_code=500)

    _runs[result.run_id] = result
    return JSONResponse(_summarise(result))


def _summarise(r) -> dict[str, Any]:
    m = r.manifest
    return {
        "run_id": r.run_id,
        "region": r.region,
        "svg_url": f"/api/run/{r.run_id}/figure.svg",
        "csv_url": f"/api/run/{r.run_id}/values.csv",
        "manifest_url": f"/api/run/{r.run_id}/manifest.json",
        "png_url": f"/api/run/{r.run_id}/figure.png",
        "slide_url": f"/api/run/{r.run_id}/figure-slide.svg",
        "slide_png_url": f"/api/run/{r.run_id}/figure-slide.png",
        "caption_url": f"/api/run/{r.run_id}/caption.txt",
        "ledger_url": f"/api/run/{r.run_id}/ledger.csv",
        "gene_order": r.gene_order,
        "counts": {
            "input": m.input_row_count,
            "rendered": m.rendered_count,
            "dropped": m.ledger_count,
        },
        "invariants": m.invariants,
        "all_invariants_pass": m.all_invariants_pass,
        # UX A1: distinct from all_invariants_pass. A NOT_EVALUATED run is not
        # a pass, but it is not a failure either, and the UI must not conflate
        # the two.
        "any_invariant_failed": m.any_invariant_failed,
        "run_conditions": m.run_conditions,
        "pareto_completeness": m.pareto_completeness,
        "contracts": m.contracts,
        "warnings": m.warnings,
        "ledger": m.ledger,
        "weights": m.weights,
        "timings": m.timings,
        "not_attempted": m.not_attempted,
        "pareto": [g for g in r.gene_order if r.drawers.get(g, {}).get("pareto")],
        "genes": {
            g: {
                "k": d["k"], "pareto": d["pareto"], "unstable": d["unstable"],
                "median_rank": d["median_rank"], "pipeline_summary": d["pipeline_summary"],
                "n_drugs": len(d["drugs"]), "resolution_status": d["resolution_status"],
            }
            for g, d in r.drawers.items()
        },
    }


async def fill_drawer_only_sources(d: dict[str, Any]) -> dict[str, Any]:
    """v2.2 item 3 — ChEMBL and Europe PMC, fetched on click.

    Both drive no ring and no rank (PLAN §8, COUNCIL M6) and on 2026-09-24 they
    were 99% and 64% of the time spent on sources. They are off the render path
    now and land here instead, so the figure draws without waiting for EBI.

    Two things are deliberately unchanged:

    * **The four-state semantics.** `mechanism_summary` still distinguishes
      `no_uniprot` / `no_target_record` / `undrugged` / `mechanisms` / `failed`,
      and a failure still says so rather than reading as "no drugs".
    * **Failures are never cached** (`cache.Cache`, and nothing is memoised on
      the drawer either). A transient EBI 500 must not become a permanent
      answer, so an errored fetch is re-attempted on the next click; only a
      successful one is kept on the run.
    """
    if d.get("chembl") is not None and d.get("literature", {}).get("europepmc") is not None:
        return d
    cache = Cache()
    async with Http() as http:
        chembl_rec, epmc = await asyncio.gather(
            ChEMBLAdapter(http, cache).fetch_one(d.get("uniprot")),
            EuropePMCAdapter(http, cache).fetch_one(d["gene"], None),
        )
    summary = mechanism_summary(chembl_rec)
    d["literature"] = {
        **d.get("literature", {}),
        "europepmc_query": epmc.query,
        "europepmc_count": epmc.count,
        "europepmc_error": epmc.error,
        "europepmc_echo_live": epmc.live,
        "europepmc_echo_ok": epmc.echo_ok,
        "europepmc": "fetched",
        "europepmc_note": "fetched on gene click (v2.2 item 3)",
    }
    d["chembl"] = summary
    d["chembl_note"] = "fetched on gene click (v2.2 item 3)"
    if summary["state"] == "failed" or epmc.error:
        # Not kept: the next click asks again. Caching a failure here would
        # reproduce, in the drawer, the thing `cache.Cache` refuses to do.
        d = dict(d)
        d["_retry_on_next_click"] = True
    return d


@app.get("/api/run/{run_id}/gene/{gene}")
async def gene_drawer(run_id: str, gene: str) -> JSONResponse:
    r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, f"run {run_id} is not in memory; re-submit the table")
    d = r.drawers.get(gene)
    if d is None:
        raise HTTPException(404, f"{gene} is not in run {run_id}")
    try:
        filled = await fill_drawer_only_sources(d)
    except HardFailure as e:
        # The gene's own panels still open; the drawer-only sources say they
        # could not be reached, which is a different fact from "no drugs".
        filled = {**d, "chembl": {"state": "failed", "note": f"ChEMBL/Europe PMC: {e}"}}
    else:
        if not filled.pop("_retry_on_next_click", False):
            r.drawers[gene] = filled
    return JSONResponse(filled)


@app.get("/api/run/{run_id}/{filename}")
async def artefact(run_id: str, filename: str) -> FileResponse:
    if filename not in ("figure.svg", "figure.png", "values.csv", "manifest.json",
                        "ledger.csv", "figure-slide.svg", "figure-slide.png",
                        "caption.txt"):
        raise HTTPException(404, "unknown artefact")
    path = RUNS_DIR / run_id / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} not found for run {run_id}")
    media = {
        "figure.svg": "image/svg+xml", "figure.png": "image/png",
        "values.csv": "text/csv", "manifest.json": "application/json",
        "ledger.csv": "text/csv", "figure-slide.svg": "image/svg+xml",
        "figure-slide.png": "image/png", "caption.txt": "text/plain",
    }[filename]
    return FileResponse(path, media_type=media, filename=f"targetring-{run_id}-{filename}")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "note": "local only; nothing is published anywhere"}
