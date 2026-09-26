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
from .filters import FilterRejected
from .http import HardFailure, Http
from .order import ALPHABETICAL, RING_PREFIX, SUBMITTED
from .run import DEFAULT_REGION, FILTERABLE_RINGS, available_regions, run_analysis

app = FastAPI(title="targetring", docs_url="/api/docs")

_runs: dict[str, Any] = {}
_regions_cache: list[str] = []

STATIC = Path(__file__).parent / "static"


# v3.0. `no-store` on the three files that make up the page.
#
# This is a local dev server and the browser was caching them: after the v3.0
# change the page kept inlining `figure.svg` instead of `figure-web.svg` and
# rendered the new download menus unstyled, because it still had the previous
# `app.js` and `app.css`. It cost a screenshot run to notice, and the same trap
# is waiting for anyone who edits the front end and reloads -- they would see
# old behaviour and go looking for it in new code. Correctness beats caching on
# a server that only ever serves one person on loopback.
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/", response_class=HTMLResponse)
async def index_nocache() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(), headers=_NO_STORE)


@app.get("/app.js")
async def appjs() -> FileResponse:
    return FileResponse(STATIC / "app.js", media_type="application/javascript",
                        headers=_NO_STORE)


@app.get("/app.css")
async def appcss() -> FileResponse:
    return FileResponse(STATIC / "app.css", media_type="text/css",
                        headers=_NO_STORE)


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


@app.get("/api/orderings")
async def orderings() -> dict[str, Any]:
    """The spoke orders this build offers, and the rings a filter can be set on.

    v2.5. The default is the order of the submitted DE table: Puck has usually
    already sorted it, and it is his measurement rather than a composite we
    invented. Alphabetical and order-by-a-single-ring are beside it — a single
    ring is one source's own number, not a composite, which is why it is allowed
    where a ranking is not.
    """
    from .figure import RING_SPECS

    labels = {s.key: s.label for s in RING_SPECS}
    return {
        "default": SUBMITTED,
        "orderings": [
            {"value": SUBMITTED,
             "label": "the order of your submitted DE table (default)"},
            {"value": ALPHABETICAL, "label": "alphabetical by gene symbol"},
            *[{"value": f"{RING_PREFIX}{key}", "label": f"by ring: {labels.get(key, key)}"}
              for key in FILTERABLE_RINGS],
        ],
        "filterable_rings": [{"ring": key, "label": labels.get(key, key)}
                             for key in FILTERABLE_RINGS],
    }


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
    ordering: str = Form(SUBMITTED),
    filters: str = Form(""),
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
        result = await run_analysis(table=raw, region=region,
                                    ordering=ordering, filters=filters)
    except FilterRejected as e:
        # The filter settings themselves are unusable — a ring that does not
        # exist, a threshold that is not a number. Nothing was fetched and
        # nothing was drawn; the message names what to fix.
        return JSONResponse({"error": "filter_rejected", "message": str(e)},
                            status_code=422)
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
        # v3.0. `web_url` is what the page inlines and is deliberately not in
        # the download set: it is the lean render, and a file that drops the
        # caveat footer must never be the one somebody sends to a colleague.
        "web_url": f"/api/run/{r.run_id}/figure-web.svg",
        "legend_url": f"/api/run/{r.run_id}/legend.svg",
        "legend_png_url": f"/api/run/{r.run_id}/legend.png",
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
        "contracts": m.contracts,
        "warnings": m.warnings,
        "ledger": m.ledger,
        "timings": m.timings,
        "not_attempted": m.not_attempted,
        # v2.5: the two settings that decide which genes are on the figure and in
        # what order. The UI prints them beside the counts for the same reason
        # the figure does — a filtered plot must not read as the whole set.
        "ordering": m.ordering,
        "ordering_note": m.ordering_note,
        "filters": m.filters,
        "filter_counts": m.filter_counts,
        "filterable_rings": list(FILTERABLE_RINGS),
        "genes": {
            g: {
                "k": d["k"], "k_max": d["k_max"],
                "unfiltered_rings": d["unfiltered_rings"],
                "pipeline_summary": d["pipeline_summary"],
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
    # v3.0 adds three: `figure-web.svg`, which the page inlines and nobody
    # downloads, and `legend.svg` / `legend.png`, the legend as its own image.
    types = {
        "figure.svg": "image/svg+xml", "figure.png": "image/png",
        "figure-web.svg": "image/svg+xml",
        "legend.svg": "image/svg+xml", "legend.png": "image/png",
        "values.csv": "text/csv", "manifest.json": "application/json",
        "ledger.csv": "text/csv", "figure-slide.svg": "image/svg+xml",
        "figure-slide.png": "image/png", "caption.txt": "text/plain",
    }
    if filename not in types:
        raise HTTPException(404, "unknown artefact")
    path = RUNS_DIR / run_id / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} not found for run {run_id}")
    # The run id is in the DOWNLOADED name, not only inside the file. Puck's
    # condition on a detachable legend is that a figure and its legend must be
    # impossible to mismatch, and the first place they get separated is a
    # downloads folder: `targetring-<run>-legend.png` sorts next to
    # `targetring-<run>-figure.png` and away from yesterday's.
    return FileResponse(path, media_type=types[filename],
                        filename=f"targetring-{run_id}-{filename}")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "note": "local only; nothing is published anywhere"}
