"""The table view's payload — the one description of a run-as-a-grid in this build.

v4.0. This was the body of `app.run_values`, and it moved here for one reason:
Puck asked for the table as a **single-file offline export** he can read on
another machine, and the export must not be a second answer to "what does run
`X` look like as a table?". Two builders of one grid is how the app and the file
he carries come to disagree about a number, which is `AGENTS.md:17`'s plausible
wrong output with the additional property that only one of the two is on the
machine where the disagreement is noticed.

So there is one builder. `/api/run/{id}/values` calls it and serialises the
result; `export.py` calls it and freezes the result into an HTML file. Both get
the same dict, from the same pivot of `values.csv`, sorted by the same
`order.order_by_keys` and classified by the same `dim.classify`.

Three parameters exist only because the two surfaces genuinely differ, and each
one is a fact about the surface rather than about the run:

  * `in_memory` — whether a drawer can be opened at all. False for every run read
    off disk, and false for the export always.
  * `drawer_note` / `artefact_links` — what to say instead, and where a reader can
    go. The app sends them to its own `/api/…` routes; the export has no server,
    so it sends them nowhere and says why. Authored here rather than in `app.js`
    because a claim about what this artefact cannot do is a claim, and the page
    draws claims rather than writing them.
  * `record_url` — where `manifest.json` IS, for the citation strip's FAILED rows
    (see `citations.cell_provenance`).
"""

from __future__ import annotations

from typing import Any

from . import citations as citations_mod
from . import dim as dim_mod
from . import table as table_mod
from . import sources as SOURCES
from .figure import RING_SPECS, STATE_STYLE, ring_specs_for
from .order import missing_on, order_by_keys, parse_sort_keys, sort_label
from .run import FILTERABLE_RINGS

# What the app says beside a grid whose run IS in memory, and what it says when it
# is not. The second one is the sentence that has always been there, moved out of
# `app.js` so that the export can say something different and true instead of
# printing three links to routes that nothing is serving.
DRAWER_AVAILABLE_NOTE = "Click a gene to open its drawer."
DRAWER_ON_DISK_NOTE = (
    "Read from disk — the per-gene drawer needs the run in memory, so it is not "
    "available for this one.")


def app_artefact_links(run_id: str) -> list[dict[str, str]]:
    """Where the app sends a reader whose run is on disk. Server routes, so the
    export does not use them."""
    return [
        {"label": "its figure", "url": f"/api/run/{run_id}/figure.svg"},
        {"label": "values.csv", "url": f"/api/run/{run_id}/values.csv"},
        {"label": "manifest.json", "url": f"/api/run/{run_id}/manifest.json"},
    ]


def dimmable_rings(rings: list[dict[str, str]]) -> list[str]:
    """The columns a dim filter may be set on: the ones this run drew that this
    build is willing to threshold at all. `completeness` is absent for the reason
    PLAN §2 gives — dimming by k de-emphasises by how much anybody has studied a
    gene, and opacity instead of removal does not change what that is.
    """
    return [r["key"] for r in rings if r["key"] in FILTERABLE_RINGS]


def table_payload(run_id: str, sort: str = "", dim: str = "", *,
                  in_memory: bool = False,
                  record_url: str = "",
                  drawer_note: str = "",
                  artefact_links: list[dict[str, str]] | None = None,
                  ) -> dict[str, Any]:
    """One run's cells as a wide grid.

    Raises `table.TableUnavailable` if the run cannot be shown at all, `ValueError`
    if a sort key is not one this run can honour, and `dim.DimRejected` if the dim
    settings are unusable. Every one of those is a refusal rather than an
    approximation: an order or a bound this run cannot honour must never be printed
    as active over a grid that did not use it.
    """
    t = table_mod.load(run_id)
    cells_by_ring = t.cells_by_ring()
    keys = parse_sort_keys(sort, cells_by_ring)
    dimmable = dimmable_rings(t.rings)
    dims = dim_mod.parse_dims(dim, dimmable)

    # No keys means the order this run drew its spokes in, so the table and the
    # circle open agreeing. With keys, the base list is the submitted order,
    # because that is the documented tie-break and the order of the no-value
    # block (PLAN §3.1).
    order = (order_by_keys(t.submitted_order, cells_by_ring, keys) if keys
             else list(t.genes))
    primary = keys[0].ordering if keys else ""
    block = missing_on(order, cells_by_ring, primary) if primary else []

    m = t.manifest
    # Resolved from the run's OWN manifest, not from whatever this process is
    # holding: a run read back off disk has to say the same thing it said when
    # it was written. `contrast` is null on a paste-path run and populated on
    # every contrast run, which is what makes this exact.
    #
    # Without this, the Table tab, its citation strip and the export built from
    # it all described an Atlas run's rings 1 and 2 as "logFC (yours)" / "the
    # DE table you submitted — your own measurement", on the same page as the
    # accession the genes actually came from. See `sources.DESource`.
    de = SOURCES.DESource.from_manifest(m)
    specs = {s.key: s for s in ring_specs_for(de)}

    # Classified over `order`, which is the rendered gene set and is decided
    # above and unaffected by this. `assert_nothing_left` is the guard that this
    # stayed a view state: if dimming ever starts removing a gene it fails here
    # rather than on screen (AGENTS.md rule 3 — a dimmed gene is still rendered).
    dim_out = dim_mod.classify(order, cells_by_ring, dims)
    dim_mod.assert_nothing_left(order, dim_out)
    ring_labels_for_dim = {r["key"]: citations_mod.column_label(
        specs.get(r["key"]), r["label"]) for r in t.rings}

    if not drawer_note:
        drawer_note = (DRAWER_AVAILABLE_NOTE if in_memory else DRAWER_ON_DISK_NOTE)
    if artefact_links is None:
        artefact_links = [] if in_memory else app_artefact_links(t.run_id)

    return {
        "run_id": t.run_id,
        "region": t.region,
        "timestamp_utc": m.get("timestamp_utc", ""),
        "git_sha": m.get("git_sha", ""),
        # Ring 1 is the outermost band (PANEL P1). On a table that stops being a
        # thing a reader has to know, because the column is named — but the
        # number is carried so the two renderings can be checked against each
        # other cell by cell.
        "rings": [
            {"n": i, "key": r["key"], "label": r["label"],
             "short_label": (specs[r["key"]].wedge_label
                             if r["key"] in specs else r["key"]),
             # v3.7 item 4. The column head's name, which is the scale column's
             # name for the same ring rather than the wedge's -- ten of twelve
             # rings were called different things on the two surfaces. See
             # `citations.column_label` for what happens on a legacy run.
             "column_label": citations_mod.column_label(specs.get(r["key"]),
                                                        r["label"]),
             # v3.7 item 2. The source as a LABEL, small and muted, on a third
             # header line. Not a link: it would put a click target inside a
             # click-to-sort target, and `platform-docs.opentargets.org` renders
             # 261 px at 12 px in a column 86-110 px wide. The link lives in the
             # citation strip under the grid, once per column.
             "source_name": (specs[r["key"]].short_source if r["key"] in specs
                             else citations_mod.RETIRED_RING_NAME),
             "units": specs[r["key"]].units if r["key"] in specs else "",
             # Sortable exactly where `/api/orderings` offers a `ring:` ordering,
             # so the table can never sort by something the circle cannot.
             # `completeness` is deliberately absent: ordering by k would order
             # by how much anybody has studied a gene (PLAN §2, M3).
             "sortable": r["key"] in FILTERABLE_RINGS,
             # v3.8. Kept as its own flag rather than reusing `sortable`: the two
             # happen to be the same list today and they are not the same claim —
             # one is about an order the circle can also draw, the other about a
             # threshold a reader can set on a raw value.
             "dimmable": r["key"] in dimmable}
            for i, r in enumerate(t.rings, start=1)
        ],
        "genes": [
            {"gene": g, "ensembl_id": t.ensembl.get(g, ""), "k": t.k.get(g, 0),
             "no_value_on_sort_key": g in block,
             # v3.8. One of `match` / `dimmed` / `unevaluable`, and `match` for
             # every gene when no dim filter is set — so a cleared filter is the
             # same payload as one that was never applied. There is deliberately
             # no per-gene count of filters matched: that number, sorted, is a
             # composite score under another name (PLAN §3).
             "dim_state": dim_out.state.get(g, dim_mod.MATCH),
             "dim_reason": dim_out.reason.get(g, ""),
             "cells": {ring["key"]: t.cells[ring["key"]][g].as_dict()
                       for ring in t.rings}}
            for g in order
        ],
        "k_max": t.k_max,
        "order": order,
        "sort": [{"ordering": k.ordering, "reverse": k.reverse} for k in keys],
        "sort_note": sort_label(keys, {k: v.label for k, v in specs.items()},
                                de.table_phrase),
        "missing_block": block,
        # v3.7 item 1. Where these N columns come from, one row per ring, read
        # from `sources.RING_SOURCE_DOCS` and keyed on the rings this run drew.
        #
        # This is the fix for the defect two reviewers found independently:
        # `/api/run/{id}/scales` reads `_runs`, so it 404s for every run on disk
        # and the decode panel it feeds holds the only on-screen source links in
        # the app. A disk-loaded run had no citation anywhere in the session. The
        # table reads artefacts, so these rows are reachable for all ~250 runs
        # under `runs/` and for `?run=<id>` on a fresh page.
        "citations": citations_mod.citation_rows(
            run_id=t.run_id, rings=t.rings, cells=t.cells, specs=specs,
            manifest_rows=m.get("ring_sources") or [], record_url=record_url,
            de=de),
        "citation_note": citations_mod.NO_DATA_NOTE,
        # AGENTS.md rule 3. Re-asserted in `table.load` — a run whose counts do
        # not add up raises there and never reaches this response — and repeated
        # here so the page can print the identity rather than assert it again.
        "counts": {"input": m.get("input_row_count"),
                   "rendered": m.get("rendered_count"),
                   "dropped": m.get("ledger_count"),
                   "identity_holds": True},
        "ledger": m.get("ledger", []),
        "invariants": m.get("invariants", []),
        # v4.0. The source contracts, beside the invariants, because the export
        # stamps both on its face and `renderInvariants` has always drawn both —
        # it was reading them off the analyse response, which a disk-loaded run
        # does not have.
        "contracts": m.get("contracts", []),
        "any_invariant_failed": bool(m.get("any_invariant_failed")),
        "all_invariants_pass": bool(m.get("all_invariants_pass")),
        # What the CIRCLE was drawn with, which is a different fact from how the
        # table is currently sorted, and both are on screen.
        "ordering": m.get("ordering", ""),
        "ordering_note": m.get("ordering_note", ""),
        "filters": m.get("filters", []),
        "filter_counts": m.get("filter_counts", {}),
        # v3.8, and the naming is load-bearing: `filters` above are PLAN §3.2's,
        # which REMOVED genes from this run and named them in the ledger, and they
        # are a property of the run. `dim` is a property of the current view, set
        # after the fact, and it removes nothing. Two keys, never one.
        "dim": [d.as_dict() for d in dims],
        "dim_counts": dim_out.counts,
        "dim_dimmable": dimmable,
        # The count line the page prints above the grid, so a screenshot of a
        # filtered table states what was filtered — authored here rather than in
        # JS so the sentence and the numbers come from the same place.
        "dim_count_line": dim_mod.count_line(dim_out, dims, ring_labels_for_dim),
        # The same sentence without the three counts, because the page prints those
        # as its own headline and a block stating them twice in four lines is
        # `UX-v3-rounds.md` B14 — one claim at two weights — in a new place.
        "dim_count_note": dim_mod.count_note(dim_out, dims, ring_labels_for_dim),
        "submitted_order_source": t.submitted_order_source,
        "states": [{"key": k, "label": s["label"], "fill": s["fc"],
                    "edge": s["ec"]} for k, s in STATE_STYLE.items()],
        # The drawer needs the run in memory; a run read off disk has no
        # adapters behind it. Said rather than discovered by clicking.
        "in_memory": in_memory,
        # v4.0. What to say instead, and where a reader may go — both authored in
        # Python, because an artefact that states what it cannot do is stating a
        # claim and the page does not write claims.
        "drawer_note": drawer_note,
        "artefact_links": artefact_links,
    }


__all__ = ["table_payload", "dimmable_rings", "app_artefact_links",
           "DRAWER_AVAILABLE_NOTE", "DRAWER_ON_DISK_NOTE"]
