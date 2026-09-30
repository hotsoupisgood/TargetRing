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

from .adapters.atlas import AtlasAdapter
from .adapters.chembl import ChEMBLAdapter, mechanism_summary
from .adapters.literature import EuropePMCAdapter
from .adapters.ols import OLSAdapter
from . import atlas_index
from .cache import Cache
from . import contrast as contrast_mod
from . import dim as dim_mod
from .config import MAX_GENES, RUNS_DIR
from .de_input import InputRejected
from . import sources as sources_mod
from .filters import FilterRejected
from .figure import (COMPARABILITY_CLAIM, STATE_STYLE, WEB_REFERENCE_PANEL_PX,
                     WEB_REFERENCE_VIEWPORT_PX, scale_rows, web_circle_px,
                     web_css_scale)
from .http import HardFailure, Http
from .order import ALPHABETICAL, RING_PREFIX, SUBMITTED
from .resolve import Resolver
from .run import DEFAULT_REGION, FILTERABLE_RINGS, available_regions, run_analysis
from . import table as table_mod
from . import view as view_mod

app = FastAPI(title="targetring", docs_url="/api/docs")

_runs: dict[str, Any] = {}
_regions_cache: list[str] = []
# `(index file mtime_ns, index)`. Keyed on the mtime so a rebuild while the
# server is up is picked up rather than served stale under a fresh build date.
_index_cache: tuple[int | None, "atlas_index.AtlasIndex"] | None = None

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


def _atlas_index() -> atlas_index.AtlasIndex:
    """The contrast index, loaded once per process and reloaded when it changes.

    Mtime, not a flag: `scripts/build_atlas_index.py` rewrites the file while the
    server is running, and a cached index would keep serving yesterday's counts
    under today's build date after a rebuild -- a stale number wearing a fresh
    label, which is this repo's one rule in a cache.
    """
    global _index_cache
    p = atlas_index.index_path()
    stamp = p.stat().st_mtime_ns if p.exists() else None
    if _index_cache is None or _index_cache[0] != stamp:
        _index_cache = (stamp, atlas_index.AtlasIndex.load(p))
    return _index_cache[1]


@app.get("/api/diseases")
async def diseases(q: str = "", limit: int = 8) -> JSONResponse:
    """Disease autocomplete: OLS4 hits, each with its Atlas contrast count.

    Two things this route refuses to do, and both are `AGENTS.md:17` in a
    dropdown:

    * **It never serves a count from a missing index.** "0 contrasts" because
      nothing was ever crawled and "0 contrasts" because Atlas has never studied
      this disease are the same string and completely different facts, so a
      missing index is a 503 naming the script to run, not a list of zeros.
    * **It never sends one id space to the other's source.** Each hit carries
      `current_id` (Open Targets' id) and `atlas_ids` (the ids Atlas samples are
      tagged with, the obsolete ones included), and the count is looked up with
      the second. `EFO_0000384` is obsolete and Open Targets returns null for it;
      `MONDO_0005011` is current and Atlas has never heard of it for these
      experiments. A single-id record would read one of those nulls as absence.

    The order is OLS4's own relevance order, untouched. No re-ranking and no
    "recommended" hit -- rule 7 is about a list of anything.
    """
    query = q.strip()
    if not query:
        return JSONResponse({"query": "", "diseases": [], "note": "type a disease name"})
    try:
        index = _atlas_index()
    except atlas_index.IndexMissing as e:
        return JSONResponse(
            {"error": "index_missing", "message": str(e),
             "note": "no contrast counts are shown rather than zeros, which would "
                     "be indistinguishable from a disease Atlas has not studied"},
            status_code=503)
    cache = Cache()
    try:
        async with Http() as http:
            refs = await OLSAdapter(http, cache).resolve(
                query, limit=max(1, min(int(limit), 20)))
    except HardFailure as e:
        return JSONResponse(
            {"error": "source_failure", "message": str(e),
             "note": "OLS4 failed hard. No disease list is produced rather than a "
                     "partial one."}, status_code=502)
    return JSONResponse({
        "query": query,
        "diseases": [index.autocomplete_entry(r) for r in refs],
        "index": index.provenance(),
        "sources": sources_mod.data_source_rows(["ols.disease", "atlas.contrast"]),
    })


@app.get("/api/contrasts")
async def contrasts(disease: str = "", sort: str = "") -> JSONResponse:
    """Every Atlas contrast for one disease, with every reason to look twice.

    `disease` is a **current** id (`MONDO_0005011`), and the Atlas-side ids are
    re-derived from it here rather than being accepted from the query string. That
    is one OLS4 round trip per request (cached), and it buys the thing this feature
    is most likely to get wrong: the id-space bridge is asserted on every request
    instead of once at autocomplete time, so no amount of editing a URL can get an
    obsolete id sent to Open Targets or a current id used to look up an archive.

    What this route deliberately does not have:

    * **a default pick, or a recommended row.** The spike's verdict is that no
      sensible default exists -- 101 of the 138 Crohn's contrasts come from one
      ustekinumab trial. A default in a payload is a recommendation whatever the
      UI calls it (rule 7).
    * **a composite order.** One column at a time, or the default. A ranking of
      contrasts is a ranking.
    * **a filter that removes microarray.** 121 of the 138 are microarray; greying
      them with a reason and a count is the difference between "not drawable yet"
      and "Atlas has 17 Crohn's contrasts".
    """
    want = disease.strip()
    if not want:
        raise HTTPException(400, "Give a disease id, e.g. disease=MONDO_0005011")
    try:
        index = _atlas_index()
    except atlas_index.IndexMissing as e:
        return JSONResponse({"error": "index_missing", "message": str(e)},
                            status_code=503)
    cache = Cache()
    try:
        async with Http() as http:
            ref = await OLSAdapter(http, cache).resolve_id(want)
    except HardFailure as e:
        # Includes the obsolete-id case: `disease=EFO_0000384` is refused, because
        # that is the Atlas-side id and Open Targets returns null for it.
        return JSONResponse(
            {"error": "disease_not_resolvable", "message": str(e),
             "note": "the disease id is not resolvable as a current term, so no "
                     "contrast list is produced"}, status_code=422)
    try:
        return JSONResponse(atlas_index.picker_payload(index, ref, sort))
    except ValueError as e:
        # Same rule as the figure's order: an order this build cannot honour is
        # refused, never quietly swapped for another while the panel names the first.
        return JSONResponse({"error": "sort_rejected", "message": str(e)},
                            status_code=422)


def _contrast_ref_or_error(accession: str, contrast_id: str) -> tuple[Any, JSONResponse | None]:
    """Shared by the genes and heatmap routes: look the row up in the index,
    refuse an `INVALID — sign unsafe` row and a microarray row the same way
    both routes must, and hand back a hydrated-but-not-yet-fetched `ContrastRef`
    otherwise. Never a default pick and never a guess at the platform."""
    try:
        index = _atlas_index()
    except atlas_index.IndexMissing as e:
        return None, JSONResponse({"error": "index_missing", "message": str(e)},
                                  status_code=503)
    row = index.find(accession, contrast_id)
    if row is None:
        return None, JSONResponse(
            {"error": "contrast_not_found",
             "message": f"{accession} {contrast_id} is not in the index"},
            status_code=404)
    if row.invalid_reason:
        return None, JSONResponse(
            {"error": "invalid_contrast", "message": row.invalid_reason},
            status_code=422)
    ref = row.as_ref(index_built_utc=index.built_utc)
    if not ref.is_rnaseq:
        return None, JSONResponse(
            {"error": "microarray_not_drawable",
             "message": str(contrast_mod.MicroarrayNotDrawable(ref))},
            status_code=409)
    return ref, None


@app.get("/api/contrast/{accession}/{contrast_id}/genes")
async def contrast_genes(accession: str, contrast_id: str, n: int = MAX_GENES,
                         direction: str = "both") -> JSONResponse:
    """Stage 3: the gene table for one contrast, no figure yet.

    RNA-seq only (microarray is HTTP 409, stage 6's reason, never a partial
    table). Selection is always over rows carrying both a p-value and a log2FC
    -- rule 1 as a sort (PLAN §1 item 6) -- and every id then goes through the
    same `Resolver` a pasted id would (rule 5), so `no_match`/`ambiguous` read
    exactly as they do on the paste path.
    """
    ref, err = _contrast_ref_or_error(accession, contrast_id)
    if err is not None:
        return err
    if direction not in ("both", "split"):
        return JSONResponse(
            {"error": "bad_direction", "message": "direction must be 'both' or 'split'"},
            status_code=422)
    cache = Cache()
    try:
        async with Http() as http:
            table = await contrast_mod.build_gene_table(
                AtlasAdapter(http, cache), Resolver(http, cache), ref,
                n=max(1, min(int(n), MAX_GENES)), direction=direction)
    except HardFailure as e:
        return JSONResponse({"error": "source_failure", "message": str(e)},
                            status_code=502)
    return JSONResponse({
        **table.as_dict(),
        "sources": sources_mod.data_source_rows(["atlas.contrast"]),
    })


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
    # v3.5. The window this figure is going to be read in, MEASURED by the page
    # (`app.js`) rather than derived here from a copy of the stylesheet: the panel
    # is a flex child with `min-width: 0`, so its width is not a sum of
    # constants, and a Python guess at it would be a second implementation of
    # `app.css` that could disagree with the real one (COUNCIL V5's shape, in a
    # layout). The defaults are the 1440 x 900 laptop, so a client that sends
    # nothing gets the canvas v3.4 drew.
    panel_px: float = Form(WEB_REFERENCE_PANEL_PX),
    viewport_px: float = Form(WEB_REFERENCE_VIEWPORT_PX),
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
                                    ordering=ordering, filters=filters,
                                    panel_px=panel_px, viewport_px=viewport_px)
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


def _web_render(r) -> dict[str, Any]:
    """v3.5. What canvas the app render was drawn on, and whether this window
    earned the twelve ring names.

    Reported rather than inferred by the page for the same reason `web_caveats` is
    handed over: the arrangement was decided by the render's own assertions, and a
    page that worked out for itself whether the names *should* be there could
    disagree with the figure it is drawing them beside. The sentence a reader sees
    still comes from `wedge_note`, on the figure; this is the measurement, for the
    screenshots, the sweeps and the tests.
    """
    lay, (panel, window_h) = r.web_layout, r.web_window
    scale = web_css_scale(lay, panel)
    return {
        "panel_px": round(panel, 1),
        "viewport_px": round(window_h, 1),
        "canvas_in": [lay.figsize[0], lay.figsize[1]],
        "figure_px": round(lay.figsize[1] * 72.0 * scale, 1),
        "circle_px": round(web_circle_px(lay, panel), 1),
        "type_px": round(lay.label_size * scale, 2),
        "ring_names_in_figure": lay.wedge_labels_in_svg,
        "ring_name_px": (round(lay.wedge_label_size * scale, 2)
                         if lay.wedge_labels_in_svg else None),
    }


def _summarise(r) -> dict[str, Any]:
    m = r.manifest
    return {
        "web_render": _web_render(r),
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
        # v3.1 / UX v3 A2. Not an artefact and not a download: the twelve
        # scales as data, so the page can draw at 13 px what the SVG was
        # drawing at 5.8.
        "scales_url": f"/api/run/{r.run_id}/scales",
        "gene_order": r.gene_order,
        "counts": {
            "input": m.input_row_count,
            "rendered": m.rendered_count,
            "dropped": m.ledger_count,
        },
        "invariants": m.invariants,
        # Stage 0. Both LLM accounts, every run, whether or not either was
        # touched. The page prints the two lines in the caveats tab: a screen that
        # showed one balance would leave a reader unable to check the one thing
        # rule 8 promises, which is that the two budgets are separate.
        "llm": m.llm,
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


@app.post("/api/run/{run_id}/web-figure")
async def rewidth_web_figure(
    run_id: str,
    panel_px: float = Form(...),
    viewport_px: float = Form(...),
) -> JSONResponse:
    """v3.5. Re-draw THIS run's app figure for a window of a different size.

    Without this the feature would be "the names appear if the window was already
    big when you pressed the button", which is not what was asked for: a figure
    read in a window that has since been resized is a figure sized for a window
    that no longer exists. The page calls this when the resize settles
    (`app.js`), and only the app render is redrawn -- no source is fetched, no
    export is rewritten, nothing is re-resolved.

    The run's `web_caveats` are replaced with the new render's, because one of
    those clauses states where the ring names are and the answer is exactly what
    may have just changed. `assert_renders_agree` runs inside the re-render, so a
    re-drawn circle is still cell-for-cell the export's.
    """
    r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, f"run {run_id} is not in memory; re-submit the table")
    if r.rerender_for_window is None:
        raise HTTPException(409, f"run {run_id} cannot be re-drawn in this process")
    try:
        result, lay = r.rerender_for_window(panel_px, viewport_px)
    except AssertionError as e:
        # Same rule as `/api/analyse`: a geometry this window cannot draw honestly
        # is reported, and the figure already on screen is left alone. The one
        # refusal that is NOT an error is the ring names', and it never reaches
        # here -- `render_naming_the_rings_if_they_fit` answers it with the
        # arrangement that fits.
        return JSONResponse(
            {"error": "invariant_abort", "message": str(e),
             "note": "the app render was not redrawn; the figure on screen is the "
                     "one this run drew for the window it was submitted in."},
            status_code=500)
    r.web_caveats, r.web_layout, r.web_window = (result.web_caveats, lay,
                                                 (panel_px, viewport_px))
    return JSONResponse({"run_id": run_id, "web_render": _web_render(r),
                         "web_url": f"/api/run/{run_id}/figure-web.svg",
                         "figure_caveats": r.web_caveats})


@app.get("/api/run/{run_id}/scales")
async def scales(run_id: str) -> JSONResponse:
    """v3.1 / UX v3 A2. The twelve scales, as data, for the page to draw.

    They used to be 5.8-6.5 CSS px of vector text in a column that took 23% of
    the figure panel on a 1440 px laptop -- present, binding (UX-v1-mvp.md A5),
    and unreadable. They are still per-ring, still in the ring's own units, and
    still carry the v2.8 source link; the browser draws them at 13 px now,
    because §v3.0 made it legal for the app render and the export to differ and
    this is the thing that freedom was worth spending on.

    The **export is unchanged**: `figure.svg`, `figure-slide.svg` and
    `legend.svg` all still carry the column, and the tests that hold A5 on those
    renders are untouched.
    """
    r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, f"run {run_id} is not in memory; re-submit the table")
    return JSONResponse({
        "run_id": run_id,
        "comparability": COMPARABILITY_CLAIM,
        # v3.2 / UX v3 round 2 A4. The caveat clauses the app render handed over
        # so that its circle would fit one 1440 x 900 viewport. The page draws
        # them at 13 px directly under the figure and never behind a click --
        # A2's trade for the scale column, applied to the one remaining block
        # that was forcing the canvas taller than the window.
        "figure_caveats": r.web_caveats,
        "states": [{"key": k, "label": s["label"], "fill": s["fc"],
                    "hatch": s["hatch"], "edge": s["ec"]}
                   for k, s in STATE_STYLE.items()],
        "rings": scale_rows(r.cells_by_ring),
    })


@app.get("/api/runs")
async def runs(limit: int = 40) -> dict[str, Any]:
    """The runs on disk, newest first — the table view's way in.

    v3.6. There has never been a deep link to a run: `RUN` is set in-page when an
    analyse completes and is gone on reload, so the 250 runs already produced
    were only reachable as files. The table reads from artefacts, so it does not
    need the run in memory, and this is the list it offers.
    """
    return {"runs": table_mod.recent_runs(max(1, min(int(limit), 200)))}


@app.get("/api/run/{run_id}/values")
async def run_values(run_id: str, sort: str = "", dim: str = "") -> JSONResponse:
    """v3.6. One run's cells as a wide grid — the table view's only data source.

    Registered above `/{filename}` because that route is a catch-all; `values`
    and `values.csv` are different path segments and both still work.

    Two decisions are load-bearing and neither is a matter of taste:

    * **It is a pivot, never a recomputation.** Every state, number, colour and
      detail string comes off the row the renderer wrote into `values.csv`. A
      table that recomputed its cells could disagree with the circle beside it,
      and a wrong table is `AGENTS.md:17`'s wrong figure in another shape.
    * **Sorting is `order.py`'s, not the page's.** The keys are put through the
      same `normalise_ordering` gate the figure uses and ordered by
      `order_by_keys`, whose single-key case is asserted equal to
      `order_for_figure`. Sorting in the browser would be a second definition of
      "sorted by logFC", and the two would eventually differ.

    **v3.8: `dim`.** A per-column dim filter, and the same reasoning puts its
    decision here rather than in the browser — a threshold evaluated in JS would
    be a second definition of `regional_specificity >= 5` and it would drift from
    §3.2's. It is pure view state: it does not touch `order`, `genes`, `cells`,
    `counts` or `ledger`, so `?dim=` and no `dim` at all return the same bytes
    and a dimmed gene is still a rendered gene.

    **v4.0.** The payload itself is `view.table_payload`'s, and this route is the
    HTTP shape around it: the offline export calls the same builder, so the grid in
    a file Puck carries to another machine is not a second description of the run.
    All this function still decides is which refusal gets which status code.
    """
    try:
        body = view_mod.table_payload(run_id, sort, dim,
                                      in_memory=run_id in _runs)
    except table_mod.TableUnavailable as e:
        return JSONResponse({"error": "table_unavailable", "message": str(e)},
                            status_code=404)
    except ValueError as e:
        # Same rule as the figure's: an order this run cannot honour is refused,
        # never quietly swapped for another while the screen names the first.
        # `DimRejected` is a `ValueError` too, and is caught above this by being
        # the narrower class.
        if isinstance(e, dim_mod.DimRejected):
            # Refused, not approximated. A dim filter the run cannot evaluate must
            # not be printed as active over a grid it did not touch.
            return JSONResponse({"error": "dim_rejected", "message": str(e)},
                                status_code=422)
        return JSONResponse({"error": "sort_rejected", "message": str(e)},
                            status_code=422)
    return JSONResponse(body)



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
    # v3.6. `filename` has always been whitelisted; `run_id` was interpolated
    # into a path unchecked. Local loopback only, so nobody has reached it — but
    # it is one call and the table view adds two more routes that take a run id.
    if not table_mod.valid_run_id(run_id):
        raise HTTPException(404, "unknown run")
    path = RUNS_DIR / run_id / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} not found for run {run_id}")
    # The run id is in the DOWNLOADED name, not only inside the file. Puck's
    # condition on a detachable legend is that a figure and its legend must be
    # impossible to mismatch, and the first place they get separated is a
    # downloads folder: `targetring-<run>-legend.png` sorts next to
    # `targetring-<run>-figure.png` and away from yesterday's.
    # v3.5. `figure-web.svg` is the one artefact a run can rewrite after it has
    # been served: `/api/run/{id}/web-figure` redraws it when the window changes
    # size. The browser was already being told not to cache the three page files
    # for a smaller version of this trap (see `_NO_STORE`), and a cached app
    # figure would show a circle sized for the window before last.
    headers = _NO_STORE if filename == "figure-web.svg" else None
    return FileResponse(path, media_type=types[filename], headers=headers,
                        filename=f"targetring-{run_id}-{filename}")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "note": "local only; nothing is published anywhere"}
