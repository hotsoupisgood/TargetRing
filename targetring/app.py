"""FastAPI app. Local only — no deploy, no public URL (AGENTS.md rule 10).

Submit a DE table and a region, get the figure; click a gene for its drug
pipeline and its literature; download the SVG and the CSV.
"""

from __future__ import annotations

import asyncio
import json as json_mod
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)

from .adapters.atlas import AtlasAdapter
from .adapters.chembl import ChEMBLAdapter, mechanism_summary
from .adapters.literature import EuropePMCAdapter
from .adapters.ols import OLSAdapter
from .adapters.opentargets import OpenTargetsAdapter
from . import atlas_index
from . import autopick as autopick_mod
from . import chooser_llm
from . import region_llm
from .cache import Cache
from . import contrast as contrast_mod
from . import dim as dim_mod
from .config import MAX_GENES, PHENOTYPE_SUMMARY_MAX_ROWS, RUNS_DIR
from .de_input import InputRejected
from . import heatmap as heatmap_mod
from . import invariants as invariants_mod
from . import llm as llm_mod
from . import phenotype as phenotype_mod
from . import sources as sources_mod
from . import spend as spend_mod
from .filters import FilterRejected
from .figure import (COMPARABILITY_CLAIM, STATE_STYLE, WEB_REFERENCE_PANEL_PX,
                     WEB_REFERENCE_VIEWPORT_PX, scale_rows, web_circle_px,
                     web_css_scale)
from .http import HardFailure, Http
from .order import ALPHABETICAL, RING_PREFIX, SUBMITTED
from .resolve import Resolver
from . import run as run_mod
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


@app.get("/header-wash.css")
async def headerwashcss() -> FileResponse:
    """The masthead accent, served separately because it is the one stylesheet
    allowed to name an image -- see `static/header-wash.css` for why, and
    `test_export.py::test_the_stylesheet_is_inlined_and_imports_nothing` for
    the check that forces the split.

    Routes here are per-file and there is no `StaticFiles` mount, so a
    `<link>` added to `index.html` without a route beside it is a 404 and the
    accent is silently absent -- the stylesheet degrades to plain tinted paper
    exactly as a missing PNG does, which is the failure mode that hides it.
    """
    return FileResponse(STATIC / "header-wash.css", media_type="text/css",
                        headers=_NO_STORE)


def _wash(name: str) -> FileResponse:
    """One of the two masthead washes, by exact filename.

    Two explicit routes rather than one `{variant}` route, for the reason every
    other static route here is explicit: a path segment interpolated into
    `STATIC / ...` is how a file server starts serving more than two files, and
    a literal name cannot be traversed. (It is also the only shape that works —
    a path parameter does not match the empty string, so `/header-wash.png`
    404ed while `/header-wash-dark.png` served.)

    `header-wash.css` mounts these with a RELATIVE url(), so it resolves against
    the stylesheet's own URL and both sit at the root here.
    """
    path = STATIC / name
    if not path.exists():
        # The accent is optional by design: `header-wash.css` sets only
        # `background-image`, so a missing file degrades the header to plain
        # tinted paper. A 404 and not a 500 — a missing accent is not an error.
        raise HTTPException(404, "the wash has not been generated; run "
                                 "scripts/make_header_wash.py")
    return FileResponse(path, media_type="image/png", headers=_NO_STORE)


@app.get("/header-wash.png")
async def headerwashpng() -> FileResponse:
    return _wash("header-wash.png")


@app.get("/header-wash-dark.png")
async def headerwashdarkpng() -> FileResponse:
    return _wash("header-wash-dark.png")


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
    # `read_index_path`, not `index_path`: a fresh clone has no cache, and the
    # checked-in copy under fixtures/ is what it reads until a build exists.
    p = atlas_index.read_index_path()
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
      them with a reason and a count is the difference between "Atlas has these
      and this app does not read them" and "Atlas has 17 Crohn's contrasts".
      Those are different facts and the payload must not collapse them.
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

    RNA-seq only (microarray is HTTP 409 with the settled-decision reason, never
    a partial table). Selection is always over rows carrying both a p-value and a log2FC
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


@app.get("/api/contrast/{accession}/{contrast_id}/heatmap")
async def contrast_heatmap(accession: str, contrast_id: str,
                           n: int = MAX_GENES,
                           direction: str = "both") -> Any:
    """Stage 4: the heatmap. Plan §8's rule applies literally here — if I10
    fails, this route refuses rather than serving a heatmap missing any of the
    mandatory strings.

    `direction` was missing until the 2026-10-06 panel (S6). Its absence meant
    this route always selected with the `"both"` default while `/api/genes`
    honoured whatever the caller asked for, so a `direction=split` gene table
    and the heatmap served beside it shared only 31 of 60 genes — 29 different
    genes, under a heatmap sentence reading "after selecting the top 60 by this
    contrast's adjusted p", which was true of what it drew and false of the
    table the reader was reading.
    """
    ref, err = _contrast_ref_or_error(accession, contrast_id)
    if err is not None:
        return err
    if direction not in ("both", "split"):
        return JSONResponse(
            {"error": "bad_direction",
             "message": "direction must be 'both' or 'split'"},
            status_code=422)
    n = max(1, min(int(n), MAX_GENES))
    cache = Cache()
    try:
        async with Http() as http:
            atlas = AtlasAdapter(http, cache)
            resolver = Resolver(http, cache)
            table = await contrast_mod.build_gene_table(
                atlas, resolver, ref, n=n, direction=direction)
            # This re-runs stage 3's selection rather than receiving stage 3's
            # rows, so the honest statement is that it reproduces them: it is
            # deterministic given `(accession, contrast_id, n, direction)`, and
            # those four are therefore what must match what step 3 was asked
            # for. The comment here used to claim it "reuse[s] exactly the rows
            # the gene table already selected ... never a second selection pass
            # that could drift" — which was false twice over, because it IS a
            # second selection pass and, until `direction` was threaded through
            # above, one that could not even be asked for the same gene set.
            top_rows = [contrast_mod.AnalyticsRow(r.ensembl_id, r.atlas_symbol,
                                                  r.pvalue, r.log2fc)
                       for r in table.rows]
            wanted = {r.ensembl_id for r in top_rows}
            raw = await heatmap_mod.load_raw_counts(atlas, table.ref, wanted)
    except HardFailure as e:
        return JSONResponse({"error": "source_failure", "message": str(e)},
                            status_code=502)
    except heatmap_mod.HeatmapRefused as e:
        return JSONResponse({"error": "heatmap_refused", "message": str(e)},
                            status_code=422)

    data = heatmap_mod.compute(table.ref, top_rows, raw, table.accounting)
    out_dir = RUNS_DIR / "heatmaps"
    # `direction` is in the filename because it changes which genes are drawn:
    # without it a `split` render and a `both` render of the same contrast
    # overwrite each other, and a reader could be served the other one's
    # cached file under this one's mandatory sentence.
    stem = f"{accession}_{contrast_id}_{n}_{direction}"
    out_svg = out_dir / f"{stem}.svg"
    out_png = out_dir / f"{stem}.png"
    result = heatmap_mod.render(data, out_svg, out_png)

    # `data` is passed so I10 can check the three mandatory strings that quote
    # computed quantities — the influence sentence, the directional-balance
    # sentence, and the small-n caveat. Without it those three go unchecked,
    # and they are precisely the ones the 2026-10-06 panel found either absent
    # or carrying numbers that had been computed and never printed.
    check = invariants_mod.check_heatmap_states_its_recipe(
        out_svg, table.ref, table.accounting, result.n_samples, data)
    if check.failed:
        # AGENTS.md rule: a plausible wrong figure is worse than a crash.
        # Never serve an SVG this build's own check says is missing a
        # mandatory string.
        return JSONResponse(
            {"error": "invariant_i10_failed", "message": check.detail},
            status_code=500)

    return FileResponse(out_svg, media_type="image/svg+xml", headers=_NO_STORE)


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
    # Stage 5. `source="atlas"` is the internal handoff from the heatmap's
    # selection: `contrast_ref` is never trusted from the client beyond its two
    # keys (accession, contrast_id) -- the `ContrastRef` itself is re-derived
    # from the index and re-hydrated here, exactly as steps 3/4 did, so a
    # tampered or stale client payload cannot change n_ref/n_test/the sign.
    # `ensembl_ids` is a JSON array; every id must be one this run's own top-N
    # actually showed, checked below rather than trusted.
    source: str = Form("paste"),
    accession: str = Form(""),
    contrast_id: str = Form(""),
    n: int = Form(MAX_GENES),
    direction: str = Form("both"),
    ensembl_ids: str = Form(""),
    # A token the client coined and already opened a progress stream for. Empty
    # means nobody is listening and `_progress_sink` returns None, so the run is
    # byte-for-byte what it was before this existed.
    progress_token: str = Form(""),
    # Whether `#region` was filled in by `region_llm` rather than chosen. Sent
    # by the page because the page is the only thing that knows; the figure
    # states it so a screenshot does too.
    region_inferred: str = Form(""),
    region_reason: str = Form(""),
    file: UploadFile | None = None,
) -> JSONResponse:
    import json as json_mod

    parsed_input = None
    contrast_for_run = None
    selection_accounting = None

    if source == "atlas":
        if not accession or not contrast_id:
            raise HTTPException(400, "source=atlas requires accession and contrast_id")
        try:
            selected_ids = json_mod.loads(ensembl_ids) if ensembl_ids.strip() else []
        except json_mod.JSONDecodeError:
            return JSONResponse(
                {"error": "bad_ensembl_ids", "message": "ensembl_ids must be a JSON array"},
                status_code=422)
        if not isinstance(selected_ids, list) or not selected_ids:
            return JSONResponse(
                {"error": "no_selection",
                 "message": "Selecting nothing is a refusal, not an empty figure."},
                status_code=422)

        ref, err = _contrast_ref_or_error(accession, contrast_id)
        if err is not None:
            return err
        cache = Cache()
        try:
            async with Http() as http:
                atlas = AtlasAdapter(http, cache)
                hydrated = await contrast_mod.hydrate(atlas, ref)
                all_rows = await contrast_mod.load_analytics(atlas, hydrated)
        except HardFailure as e:
            return JSONResponse({"error": "source_failure", "message": str(e)},
                                status_code=502)
        top, accounting = contrast_mod.top_n_by_padj(
            all_rows, max(1, min(int(n), MAX_GENES)), direction)

        requested = set(selected_ids)
        top_ids = {r.ensembl_id for r in top}
        outside = requested - top_ids
        if outside:
            return JSONResponse(
                {"error": "selection_outside_top_n",
                 "message": f"{len(outside)} selected id(s) are not in the "
                            f"shown top {accounting.n_top}: {sorted(outside)[:5]}"},
                status_code=422)
        # Order preserved from the top-N-by-p list (most significant first) --
        # the same principled default the heatmap's own rows use, and not a
        # ranking of the SELECTION (rule 7): it is one stated statistic's order,
        # already disclosed as the selection rule itself.
        selected_rows = [r for r in top if r.ensembl_id in requested]
        selection_accounting = accounting.with_selection(len(selected_rows))
        try:
            parsed_input = run_mod.from_contrast(hydrated, selected_rows)
        except ValueError as e:
            return JSONResponse({"error": "no_selection", "message": str(e)},
                                status_code=422)
        contrast_for_run = hydrated
    else:
        raw: bytes | str
        if file is not None and file.filename:
            raw = await file.read()
        elif table.strip():
            raw = table
        else:
            raise HTTPException(400, "Provide a DE table, pasted or uploaded.")

    sink = _progress_sink(progress_token)
    try:
        if source == "atlas":
            result = await run_analysis(
                parsed_input=parsed_input, region=region, ordering=ordering,
                filters=filters, panel_px=panel_px, viewport_px=viewport_px,
                contrast=contrast_for_run,
                selection_accounting=selection_accounting, on_progress=sink,
                region_inferred=region_inferred == "1", region_reason=region_reason,
                defer_exports=True)
        else:
            result = await run_analysis(table=raw, region=region,
                                        ordering=ordering, filters=filters,
                                        panel_px=panel_px, viewport_px=viewport_px,
                                        on_progress=sink,
                                        region_inferred=region_inferred == "1",
                                        region_reason=region_reason,
                                        defer_exports=True)
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
    # The deferred artefacts, after the response rather than before it. A
    # thread because the work is matplotlib, not I/O, and the event loop still
    # has a progress stream and the next request to serve.
    if result.finish_exports is not None:
        _export_tasks[result.run_id] = asyncio.create_task(
            asyncio.to_thread(result.finish_exports))
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
        # Stage 5. `None` on a paste-path run.
        "contrast": m.contrast,
        "selection_accounting": m.selection_accounting,
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


@app.get("/api/run/{run_id}/gene/{gene}/phenotypes")
async def gene_phenotypes(run_id: str, gene: str) -> JSONResponse:
    """Stage 7. §9: per-gene, drawer-only, NOT a step in the stepped flow.

    7a (the table) always runs if the gene resolved to an Ensembl id. 7b (the
    LLM summary) runs only when the table has rows — a gene with zero
    mousePhenotypes rows is `none found` and the page says so, no call made
    (PLAN §3 stage 7a). Every other outcome — cap exhausted, provider refusal,
    I11 refusing a fabricated or unquoted claim — renders the table with a
    banner; the table itself never depends on the LLM call succeeding.
    """
    r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, f"run {run_id} is not in memory; re-submit the table")
    d = r.drawers.get(gene)
    if d is None:
        raise HTTPException(404, f"{gene} is not in run {run_id}")
    ensembl_id = d.get("ensembl_id")
    if not ensembl_id:
        raise HTTPException(
            409, f"{gene} did not resolve to an Ensembl id in this run "
                 f"(resolution_status={d.get('resolution_status')!r}); there is "
                 f"no target to look up mouse phenotypes for")

    cache = Cache()
    try:
        async with Http() as http:
            records = await OpenTargetsAdapter(http, cache).fetch([ensembl_id])
            rec = records[ensembl_id]
            if not rec.found:
                raise HTTPException(
                    404, f"Open Targets has no target record for {ensembl_id} "
                         f"({gene}) — cannot be distinguished from 'phenotyped, "
                         f"no records', so no table is rendered")
            table = await phenotype_mod.build_table(
                rec, gene_symbol=d.get("input_symbol") or gene,
                ensembl_id=ensembl_id, http=http, cache=cache)
    except HardFailure as e:
        raise HTTPException(502, f"phenotype table: {e}") from e

    out: dict[str, Any] = {
        "gene": gene, "ensembl_id": ensembl_id, "mouse_symbol": table.mouse_symbol,
        "state": table.state, "rows": table.rows,
        "n_distinct_pmids": table.n_distinct_pmids,
        "n_articles_fetched": table.n_articles_fetched,
        "max_articles_cap": table.max_articles_cap,
        # The honest counts, each naming what it counts. One row is one
        # (phenotype, model) pair MGI curated -- not a (phenotype, model,
        # paper) triple this app manufactured by crossing the model's
        # literature into the phenotype. `counts_line` and `provenance_note`
        # are built in `phenotype.py` so no surface can word them differently.
        "n_rows": len(table.rows),
        "n_distinct_phenotypes": table.n_distinct_phenotypes,
        "n_distinct_models": table.n_distinct_models,
        "counts_line": table.counts_line(),
        "provenance_note": table.provenance_note(),
        "summary_max_rows_cap": PHENOTYPE_SUMMARY_MAX_ROWS,
        "level_counts": table.level_counts,
        "rule6_asserted_label": phenotype_mod.RULE6_ASSERTED_LABEL,
        "source": sources_mod.data_source_rows(
            ["opentargets.mouse_models"] + (["europepmc.article"] if table.n_articles_fetched else [])),
        "summary": None,
    }
    if table.state == phenotype_mod.LEVEL_NONE_FOUND:
        return JSONResponse(out)

    ledger = spend_mod.default_ledger()
    try:
        client = llm_mod.AnthropicPhenotypeClient()
    except Exception as e:  # noqa: BLE001 -- e.g. no anthropic package, no API key
        out["summary"] = {"attempted": False, "ok": False,
                          "reason": f"phenotype LLM client unavailable: {e}"}
        return JSONResponse(out)

    outcome = phenotype_mod.summarize(table, client=client, ledger=ledger, cache=cache)
    out["summary"] = outcome.as_dict()
    return JSONResponse(out)


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
        # The web scales panel resolves rings 1 and 2 from the run's own
        # manifest, like every other surface, so it cannot be the one place
        # still calling an Atlas contrast's logFC "yours".
        "rings": scale_rows(
            r.cells_by_ring,
            sources_mod.DESource.from_manifest(r.manifest.as_dict())),
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


# --- the progress channel ---------------------------------------------------
#
# `/api/analyse` is one POST that returns when the whole run is done, and on a
# warm cache that is 27 seconds. The measured split (manifest `timings`) is
# wildly uneven -- exports 19.3s, render 4.3s, everything touching a source
# 0.8s -- so a client-side timer pretending to be progress would be a bar that
# lies about where the time goes. This build does not ship those.
#
# So the stages are REAL EVENTS. The client makes up a token, opens this stream
# first, then POSTs with the same token; `run_analysis`'s `on_progress` pushes
# each finished stage into the queue and the stream relays it. Nothing about
# `/api/analyse`'s own logic changes, which is why this is a second route and
# not a rewrite of the first.
_progress: dict[str, "asyncio.Queue[dict[str, Any]]"] = {}

# --- deferred download artefacts -------------------------------------------
#
# `run_analysis(defer_exports=True)` returns the slide, the legend and the
# caption as a callable instead of rendering them inline, worth ~4.5s of a
# ~19s run. The task runs AFTER the response; the download routes wait on it.
#
# In-process only, and deliberately: `runs/<id>/exports.json` is the durable
# record, because a task dict cannot survive the restart it is supposed to
# describe.
_export_tasks: dict[str, "asyncio.Task[None]"] = {}


def _exports_state(run_id: str) -> dict[str, Any]:
    """How the deferral went, from disk, with `running` resolved against the pid.

    A process killed mid-render cannot write its own epitaph, so "died" is a
    `running` marker whose pid is gone. Checked against the pid rather than a
    timeout: a timeout would call a slow render dead, which is a guess.
    """
    import os
    path = RUNS_DIR / run_id / "exports.json"
    if not path.exists():
        return {"state": "absent"}
    try:
        rec = json_mod.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {"state": "unreadable"}
    if rec.get("state") != "running":
        return rec
    pid = rec.get("pid")
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return {**rec, "state": "died"}
    return rec


async def _await_deferred(run_id: str, filename: str) -> None:
    """Block until `filename` is written, or explain why it never will be."""
    task = _export_tasks.get(run_id)
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=120)
        except asyncio.TimeoutError:
            raise HTTPException(503, f"{filename} is still rendering")
        except Exception:  # noqa: BLE001 -- the state file carries the reason
            pass
    if (RUNS_DIR / run_id / filename).exists():
        return
    rec = _exports_state(run_id)
    state = rec.get("state")
    if state == "failed":
        # The figure and the manifest were validated and served before this
        # ran. Saying so is the point: a 404 would read as "this run never had
        # a slide", which is a different and wrong fact.
        raise HTTPException(
            500, f"{filename} was not written: {rec.get('error', 'unknown')}. "
                 f"The figure and its manifest are unaffected and were checked "
                 f"before this ran.")
    if state == "died":
        raise HTTPException(
            503, f"{filename} was not written: the process rendering it exited "
                 f"first. Re-run the analysis to produce it; the existing "
                 f"figure and manifest are unaffected.")
    raise HTTPException(404, f"{filename} not found for run {run_id}")

#: Measured shares of a warm 26.9s run, used ONLY to weight the bar's width.
#: The stage names and their order are facts; these numbers are a historical
#: average and the UI says so rather than presenting them as this run's.
#: Measured over a warm run. The split is wildly uneven and moves with the gene
#: count -- on a 3-gene paste run the first render was 14.5s and the exports
#: near-instant; on a 60-gene contrast run exports was 19.3s. Weights are a
#: rough historical average and the UI says so; the STAGE NAMES are the facts.
PROGRESS_WEIGHTS: dict[str, int] = {
    "start": 0, "contracts": 4, "resolve": 1, "adapters": 3, "regression": 3,
    "render": 45, "checking": 2, "audit_render": 10, "web_render": 10,
    "slide": 10, "legend": 5, "exports": 5, "done": 2,
}


def _progress_sink(token: str):
    """An `on_progress` for `run_analysis`, or `None` when nobody is listening."""
    if not token or token not in _progress:
        return None
    q = _progress[token]

    def sink(key: str, label: str) -> None:
        q.put_nowait({"stage": key, "label": label,
                      "weight": PROGRESS_WEIGHTS.get(key, 0)})
    return sink


@app.get("/api/analyse/progress/{token}")
async def analyse_progress(token: str) -> StreamingResponse:
    """Server-sent stage events for one analyse, by a token the client coined.

    Opened BEFORE the POST, so no event can be missed in the gap. The queue is
    created here and dropped when the stream ends; a token nobody posts against
    simply times out, which is why the generator has an idle deadline rather
    than waiting forever on a client that went away.
    """
    q: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    _progress[token] = q

    async def events():
        try:
            yield (f"data: {json_mod.dumps({'stage': 'open', 'label': 'Connected', 'weight': 0, 'weights': PROGRESS_WEIGHTS})}\n\n")
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=180)
                except asyncio.TimeoutError:
                    break
                yield f"data: {json_mod.dumps(item)}\n\n"
                if item.get("stage") == "done":
                    break
        finally:
            _progress.pop(token, None)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/autopick")
async def autopick(disease: str = "") -> JSONResponse:
    """Choose a contrast for one disease, autonomously and auditably.

    Puck's decision of 2026-10-09 (`docs/DECISION-AUTOPICK-2026-10-09.md`), and
    a deliberate reversal of this build's earlier refusal to name a default.

    A SEPARATE ROUTE, not a field on `/api/contrasts`. `picker_payload`'s
    refusal is about the listing — 138 rows arriving with one already blessed,
    which a reader cannot help but read as the app's opinion — and that refusal
    is untouched and still asserted by its tests. This is an action a caller
    explicitly invokes, and the answer carries the rule that produced it, the
    candidates it chose among and every row it excluded with a reason.

    Resolves the disease through the same `OLSAdapter` the picker does, so an
    obsolete Atlas-side id is refused here exactly as it is there rather than
    being chosen from.
    """
    want = (disease or "").strip()
    if not want:
        raise HTTPException(400, "autopick requires a disease id")
    try:
        index = _atlas_index()
    except IndexMissing as e:
        return JSONResponse({"error": "index_missing", "message": str(e)},
                            status_code=503)
    cache = Cache()
    try:
        async with Http() as http:
            ref = await OLSAdapter(http, cache).resolve_id(want)
    except HardFailure as e:
        return JSONResponse(
            {"error": "disease_not_resolvable", "message": str(e)},
            status_code=422)
    rows = atlas_index.picker_payload(index, ref)["rows"]
    # The model chooser when one can be constructed, the deterministic rule
    # otherwise — and `autopick.pick` falls back to the rule on any failure,
    # recording who actually chose. Constructing the client needs the
    # `anthropic` package; with no key it constructs fine and fails on the
    # call, which is the path `ModelChooser` is written for.
    chooser: Any = None
    try:
        chooser = chooser_llm.ModelChooser(
            client=chooser_llm.AnthropicContrastClient(), disease=ref.label or want,
            cache=Cache(),
            ledger=spend_mod.default_ledger())
    except Exception:  # noqa: BLE001 -- no `anthropic` installed at all
        chooser = None
    result = autopick_mod.pick(rows, chooser)
    body = {"disease": {"label": ref.label, "current_id": ref.current_id},
            "index": index.provenance(), **result.as_dict()}
    if result.chosen is None:
        # A refusal, not an empty pick. 422 rather than 200-with-null so a
        # caller cannot treat "nothing was eligible" as "nothing was found".
        return JSONResponse({**body, "error": "no_eligible_contrast"},
                            status_code=422)
    return JSONResponse(body)


@app.get("/api/region-for")
async def region_for(disease: str = "") -> JSONResponse:
    """Which GTEx tissue to read the specificity ring against, for one disease.

    A 200 with an empty `tissue` is the normal no-answer: the caller keeps its
    own default region and says so. This never substitutes one silently, which
    is why the empty case is not an error status — there is nothing wrong, the
    app simply did not infer a tissue and the figure will name the default it
    used instead.
    """
    want = (disease or "").strip()
    if not want:
        raise HTTPException(400, "region-for requires a disease label")
    cache = Cache()
    try:
        regions = await available_regions(cache)
    except Exception as e:  # noqa: BLE001 -- GTEx unreachable
        return JSONResponse({"tissue": "", "reason": f"the GTEx tissue panel "
                                                     f"is unavailable: {e}",
                             "cached": False, "error": "panel"})
    chooser: Any = None
    try:
        chooser = region_llm.AnthropicRegionClient()
    except Exception:  # noqa: BLE001 -- no `anthropic` installed
        chooser = None
    result = region_llm.RegionChooser(
        client=chooser, cache=cache,
        ledger=spend_mod.default_ledger()).choose(want, regions)
    return JSONResponse({**result.as_dict(), "n_regions": len(regions),
                         "default_region": DEFAULT_REGION})


@app.get("/api/run/{run_id}/evidence")
async def run_evidence(run_id: str) -> JSONResponse:
    """One run as evidence per target — the go/no-go surface.

    Registered above `/{filename}` for the same reason `values` is: that route
    is a catch-all and `evidence` is a different path segment.

    It takes no `sort` and no `dim`. That is not an omission — `view.
    evidence_payload` explains it: the order is the one the run drew its spokes
    in, and letting a caller reorder this surface by anything would make it a
    ranking, which AGENTS.md rule 7 forbids and which this surface exists to not
    be. The Table tab is where a reader changes the order, under a name.

    No `ValueError` arm here, therefore: with no sort and no dim there is no
    ordering to refuse and no threshold to reject, so `TableUnavailable` is the
    only refusal this route can produce.
    """
    try:
        body = view_mod.evidence_payload(run_id, in_memory=run_id in _runs)
    except table_mod.TableUnavailable as e:
        return JSONResponse({"error": "table_unavailable", "message": str(e)},
                            status_code=404)
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
        # One of the five the run deferred? Wait for it, or say why it is not
        # coming. A plain 404 would read as "this run never had a slide", which
        # is a different fact from "it is three seconds away".
        if filename in run_mod.DEFERRED_ARTEFACTS:
            await _await_deferred(run_id, filename)
        else:
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
