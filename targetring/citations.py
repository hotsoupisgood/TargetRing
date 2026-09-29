"""Where each column's number comes from, for a run read off its own artefacts.

v3.7. Puck: *"can you make sure links to sources are reachable."*

`sources.py` is the registry — one entry per ring, checked live by
`scripts/check_source_links.py`. This module is the registry applied to **one
run**, and it exists because the only on-screen copy of those links was in the
decode panel beside the figure, which is populated from
`/api/run/{id}/scales` — an endpoint that reads `_runs`, the in-process dict.
That dict holds the run you just analysed and nothing else, so `/scales`
answers 404 for every run on disk: measured 2026-09-28, **200 of 200 sampled
runs under `runs/`**. A reader who opened a historical run got a grid of 168
numbers with no citation anywhere in the session.

The table view reads artefacts rather than `_runs`, so the citation strip it
feeds is reachable for every run on disk, including by `?run=<id>` on a fresh
page. That is the whole fix; nothing here needs the run in memory.

Four decisions, each of which could have gone the other way:

1. **The URL always comes from this build's registry, never from the
   manifest.** The brief for v3.7 said every manifest on disk carries
   `ring_sources`. It does not: of 247 loadable runs, **97 carry it and 150 do
   not** — `ring_sources` arrived in v2.8 and most of `runs/` predates it. So a
   strip rendered from the manifest alone would have left the majority of the
   very runs it was written for uncited. Reading the registry and keying it on
   the ring the artefact actually drew covers all of them, and it is also the
   only URL with a guarantee behind it: `check_source_links.py` fetches the
   registry's URLs and `sources.assert_registry_is_coherent()` checks their
   shape. A URL copied out of a year-old manifest escapes both, and *a dead
   link in a legend is worse than no link*.

2. **Where the manifest recorded a different URL, the row says so** rather than
   silently preferring one. Today nothing drifts (checked across all 97
   manifests that carry the rows), which is the point: the check is silent until
   somebody corrects a citation, and then it is visible instead of invisible.

3. **A ring this build no longer draws gets its own citation, never a
   neighbour's link.** 69 runs on disk draw `druggability`, retired in v2.3.
   `tractable`'s citation documents three of its four keys, so pointing ring 3
   at it would be a citation for a different number — a plausible wrong
   citation, which is `AGENTS.md:17` in a new place.

   v3.7 drew the only conclusion available to it and left the ring unlinked.
   v3.9 does the thing that was actually missing: `druggability` has its **own**
   registry entry, cited to the prioritisation page that carries both of the
   sections its four keys are defined under, and qualified in print as the
   retired 4-key form so a reader cannot mistake a cell here for a `tractable`
   cell. The retirement itself is still stated — `RETIRED_RINGS` below — but it
   is now a note beside a citation rather than a reason for the absence of one.
   "This build changed its rings" was never a reason a reader could not have
   the provenance of a number on their screen.

4. **`failed` does not resolve to the source.** See `cell_provenance`.

What is deliberately NOT here: a link per cell. 168 cells carry 11 distinct
source strings, and a per-cell link implies a per-cell upstream record that does
not exist. The column is the unit of provenance because the ring is what has a
source; the strip is numbered to match the column numbers, and that is the join.
"""

from __future__ import annotations

from typing import Any

from . import sources

# What the column head prints on the source line for a ring this build has no
# entry for. Short because the line is ~9 px in an 86-110 px column; the strip
# under the grid carries the sentence.
RETIRED_RING_NAME = "retired ring"

# Why this build does not draw the ring any more, for a run that does. Named per
# ring rather than generated, for the same reason `UNLINKED_RINGS` is: "we changed
# the rings" is a different fact from "there is nothing to cite", and a reader
# looking at a column this build has no scale for is owed the first one.
#
# v3.9: a note, not a substitute for a citation. `druggability` now carries its own
# registry entry (`sources.RING_SOURCE_DOCS`), and this sentence is appended to it
# — one copy of each fact, in one place, reaching both the strip and the manifest.
# A ring that is retired AND genuinely has nothing to cite still goes through
# `unlinked_reason` and prints this on its own.
RETIRED_RINGS: dict[str, str] = {
    "druggability": (
        "This build no longer draws this ring: v2.3 split it into Tractability (the "
        "three dense keys) and Chemical probe (the sparse fourth), because an absent "
        "probe key was taking three present keys down with it. So this column has no "
        "scale in this build's legend, and its citation above is the retired 4-key "
        "measure's own — not Tractability's."),
}

# Why a `failed` cell is not sent to the ring's source documentation. This is the
# sentence the strip prints, and it is the whole argument for item 3 of v3.7.
FAILED_NOTE = (
    "A failed call is not absence. The source above documents a field that is "
    "working, so sending a reader there answers a question they did not ask and "
    "leaves them with the false conclusion that the gene has no value — the "
    "plausible wrong output this project's first rule exists to prevent. These "
    "cells point at this run's own record instead: hover the cell for the reason "
    "the call failed, and open manifest.json for what the run recorded."
)

# The strip's own opening sentence. Written here rather than in `app.js` for the
# same reason `figure.caveats_for_the_page` hands its clauses over: the page draws
# claims about provenance, it does not author them.
NO_DATA_NOTE = (
    "A link is the column's, not a cell's — the ring is what has a source. "
    "It answers a measured cell and a hatched no-data one alike: “is this gene "
    "really absent upstream?” is a question you check at the source. It does "
    "not answer a black FAILED cell; those rows say where to look instead."
)


def unlinked_reason(ring_key: str, short_source: str = "") -> str:
    """Why this ring has no citation, in this build's own words.

    Three kinds of gap and they are different facts: a ring whose number is
    Puck's own or derived here (`sources.UNLINKED_RINGS`), a ring this build
    retired (`RETIRED_RINGS`), and a ring nobody has accounted for — which is a
    bug, and says so rather than printing a blank.
    """
    if ring_key in sources.UNLINKED_RINGS:
        return sources.UNLINKED_RINGS[ring_key]
    if ring_key in RETIRED_RINGS:
        return RETIRED_RINGS[ring_key]
    return (f"this build's source registry has no entry for {ring_key!r} and no "
            f"reason on record for not having one"
            + (f" (the run recorded its source as {short_source!r})"
               if short_source else "")
            + ". No URL is invented for it.")


def cell_provenance(state: str, ring_key: str, run_id: str,
                    short_source: str = "",
                    record_url: str = "") -> dict[str, str]:
    """Where a reader checks ONE cell — the one function both surfaces read.

    v3.7 item 3. `failed` and `no_data` are the two states that look alike on a
    grid and must not resolve alike:

      * `no_data` — and `value`, and `not_applicable` — resolve to **the ring's
        source documentation**. The question a hatched cell raises is whether the
        gene is really absent upstream, and that is checked at the source.

      * `failed` resolves to **this run's own record**: the cell's `detail` and
        `manifest.json`. The call did not come back, so the upstream field's
        documentation says nothing about this cell; it is a healthy doc page that
        would tell a reader the measure exists and leave them concluding the gene
        has no value.

    The citation strip is built through this function rather than beside it, so
    the two targets cannot drift apart in one surface and stay together in the
    other.

    `record_url` is where "this run's own record" LIVES, and it is a parameter
    because v4.0's offline export has no server to serve `/api/…` from: a link to
    a route that does not answer is the dead link this module refuses on the other
    branch, and it would be worse here, on the one row whose whole job is to send
    a reader somewhere that answers. The export passes the anchor of the
    manifest it inlines into itself; the app passes nothing and gets its route.
    """
    if state == "failed":
        return {"kind": "run_record",
                "url": record_url or f"/api/run/{run_id}/manifest.json",
                "text": "manifest.json",
                "why": FAILED_NOTE}
    doc = sources.RING_SOURCE_DOCS.get(ring_key)
    if doc is None:
        return {"kind": "unlinked", "url": "", "text": "",
                "why": unlinked_reason(ring_key, short_source)}
    # v3.9. A ring can have a citation AND be one this build stopped drawing, and
    # both matter to a reader of an old run: the first says where the number came
    # from, the second says why there is no scale for the column beside it.
    note = RETIRED_RINGS.get(ring_key, "")
    return {"kind": "source_doc", "url": doc.url, "text": doc.legend_text,
            "why": f"{doc.why}. {note}" if note else doc.why}


def column_label(spec: Any, artefact_label: str) -> str:
    """The name at the top of this ring's column, for a run read off disk.

    v3.7 item 4. The written short name (`RingSpec.table_label`) is used only
    where this build's full label for the ring is still the label the run was
    drawn with. Where it is not — an older run drew "Regional specificity (log10
    ratio)" and this build calls that ring "Regional specificity" — the run's own
    label is printed verbatim.

    The alternative was to head an old run's column with today's name, and that
    would put a disagreement *inside* the table: the head saying one thing and
    the citation strip under it, which prints what the run drew, saying another.
    Aligning two surfaces is the job; introducing a third mismatch to do it is
    not. Nothing is truncated to fit either — QA-v2-retest §5 is the record of
    what truncating a ring label does — so a legacy column head is as long as the
    legacy label and wraps.
    """
    if spec is None or spec.label != artefact_label:
        return artefact_label
    return spec.column_label


def citation_rows(run_id: str, rings: list[dict[str, str]],
                  cells: dict[str, dict[str, Any]],
                  specs: dict[str, Any],
                  manifest_rows: list[dict[str, str]] | None = None,
                  record_url: str = "") -> list[dict[str, Any]]:
    """One row per ring, numbered to match the grid's column numbers.

    `rings` and `cells` are `table.RunTable`'s — the rings the run actually drew,
    in the figure's own radial order, and the cells the renderer wrote. `specs`
    is `{key: RingSpec}` from this build, passed in rather than imported so this
    module stays free of the render stack. `manifest_rows` is the run's own
    `ring_sources` where it has them, used **only** to notice drift (decision 2
    in the module docstring) and never as the source of a URL.
    """
    recorded = {r.get("ring"): r for r in (manifest_rows or []) if r.get("ring")}
    out: list[dict[str, Any]] = []
    for n, ring in enumerate(rings, start=1):
        key = ring["key"]
        label = ring.get("label") or key
        spec = specs.get(key)
        short_source = spec.short_source if spec is not None else ""
        # Both targets from one function, so the row cannot say the source link
        # answers a failed cell while `cell_provenance` says it does not.
        source = cell_provenance("no_data", key, run_id, short_source)
        failed_genes = sorted(g for g, c in (cells.get(key) or {}).items()
                              if c.state == "failed")
        row: dict[str, Any] = {
            "n": n,
            "key": key,
            # The run's own label, as the renderer wrote it. An old run is shown
            # with the rings it was drawn with (`table.load`'s rule, PANEL P1).
            "label": label,
            "units": spec.units if spec is not None else "",
            "source_name": short_source or RETIRED_RING_NAME,
            "linked": source["kind"] == "source_doc",
            "anchor_text": source["text"],
            "url": source["url"],
            "reason": source["why"],
            "failed_genes": failed_genes,
            "failed_target": (cell_provenance("failed", key, run_id, short_source,
                                              record_url)
                              if failed_genes else None),
            "recorded_drift": _drift(key, recorded.get(key) or {}),
        }
        out.append(row)
    return out


def _drift(ring_key: str, recorded: dict[str, str]) -> str:
    """Said, not resolved silently. See decision 2 in the module docstring."""
    was = (recorded.get("doc_url") or "").strip()
    if not was:
        return ""
    doc = sources.RING_SOURCE_DOCS.get(ring_key)
    if doc is None:
        return (f"this run's manifest recorded {was} for this ring; this build's "
                f"registry has no entry for it, so no link is offered here. An "
                f"unchecked URL in a legend is the dead link this project refuses.")
    if doc.url != was:
        return (f"this run's manifest recorded {was}; this build's registry now "
                f"cites {doc.url}. The link above is the registry's, because that "
                f"is the URL scripts/check_source_links.py checks.")
    return ""


__all__ = ["RETIRED_RING_NAME", "RETIRED_RINGS", "FAILED_NOTE", "NO_DATA_NOTE",
           "unlinked_reason", "cell_provenance", "column_label", "citation_rows"]
