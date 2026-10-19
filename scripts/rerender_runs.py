#!/usr/bin/env python3
"""Re-draw a finished run's figure files from its own artefacts. No re-analysis.

v4.0. The chromosome suffix (`figure._chromosome_suffix`) belongs on the four
demo runs' figures, and those runs are **finished**: re-running the analysis
would produce new run ids, new timestamps, a fresh Open Targets release's worth
of ring values and a different `docs/DEMO-RUN-ALLOWLIST.md`. What is wanted is
the same figure plus one label, so this re-renders rather than re-runs.

**What it is allowed to read, and why that is the whole design.** Every number
on the new figure comes out of the run's own `values.csv` -- the artefact
invariant 1 already asserts is cell-for-cell identical to the rendered rings --
through `table.load`, which is the same pivot the Table tab and the static
export read. The manifest supplies the sentences (region, ordering note, filter
notes, run conditions, contrast, invariant outcomes) and the drawer files supply
the severe-liability flags. Nothing is recomputed from a source. The ONE new
fact fetched from the network is the chromosome, from Open Targets, free and
cached like everything else.

So a ring value cannot move under this script. If Open Targets has relabelled a
trial status since 2026-10-18, this figure still says what the run said; only
the rim gains a number.

**What it writes:** `figure.svg`, `figure.png`, `figure-web.svg`,
`figure-slide.svg`, `figure-slide.png`, `legend.svg`, `legend.png`,
`caption.txt`, and the `chromosome` block in each `drawer/<GENE>.json`. It
deletes nothing, and it does not touch `values.csv`, `ledger.csv`,
`manifest.json` or `exports.json` -- the manifest is the record of the ANALYSIS,
and no analysis happened here.

Usage:

    .venv/bin/python scripts/rerender_runs.py                 # config.DEMO_RUNS
    .venv/bin/python scripts/rerender_runs.py <run_id> ...
    .venv/bin/python scripts/rerender_runs.py --check         # no writes
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import figure, invariants as inv_mod, sources as SOURCES, table as table_mod
from targetring.adapters.opentargets import OpenTargetsAdapter, chromosome_cell
from targetring.cache import Cache
from targetring.config import DEMO_RUNS
from targetring.contrast import Accounting
from targetring.de_input import RENDERED_STATUSES
from targetring.http import Http
from targetring.order import gene_stats
from targetring.states import TextCell, text_failed


def _report(m: dict[str, Any]) -> inv_mod.InvariantReport:
    """The run's own invariant outcomes, so the re-render stamps what the run
    stamped. Rebuilt from the manifest rather than re-checked: the checks are
    about an analysis, and this is not one."""
    return inv_mod.InvariantReport(
        results=[inv_mod.Result(id=r["id"], name=r["name"], outcome=r["outcome"],
                                detail=r.get("detail", ""), affects=r.get("affects", ""))
                 for r in m.get("invariants", [])],
        conditions=[(c["condition"], c["affects"]) for c in m.get("run_conditions", [])],
    )


def _contrast_notes(m: dict[str, Any]) -> list[str]:
    """The three contrast sentences, in the order `run.py` builds them.

    `sign_sentence` and the three-stage selection count are taken from the
    manifest verbatim and never reworded here -- which is the whole point of
    `ContrastRef.sign_sentence` existing (I8): the figure must not describe the
    sign a different way than the manifest does.
    """
    c = m.get("contrast")
    if not c:
        return []
    notes = [f"{c['name']} · {c['accession']} · {c['contrast_id']}", c["sign_sentence"]]
    acc = m.get("selection_accounting")
    if acc:
        notes.append(Accounting(**acc).selection_line())
    return notes


def _gene_flags(run_id: str, genes: list[str]) -> dict[str, list[str]]:
    """The ⚠ marker, out of the baked drawers.

    Read from disk rather than re-derived from a fresh Open Targets record on
    purpose: the flag is a statement about what THIS run saw, and the drawer is
    where this run wrote it down. A liability added upstream last week does not
    belong on a figure dated 2026-10-18.
    """
    out: dict[str, list[str]] = {}
    for g in genes:
        path = table_mod.baked_drawer_path(run_id, g)
        if path is None:
            continue
        d = json.loads(path.read_text())
        events = [x["event"] for x in (d.get("severe_liabilities") or []) if x.get("event")]
        if events:
            out[g] = events
    return out


async def _chromosomes(ensembl: dict[str, str]) -> dict[str, TextCell]:
    """`{gene: TextCell}` from Open Targets, by id, looked up by the id asked for.

    Rule 2, and it is not a formality here: `targets()` drops ids it cannot find
    and answers 200 with a shorter array, and one of these four runs has exactly
    such a gene in its ledger. `OpenTargetsAdapter.fetch` returns a dict keyed
    by the requested id with an explicit `found=False` record for the misses, so
    a gene Open Targets no longer holds becomes `no_data` under its own name
    rather than its neighbour's chromosome.
    """
    wanted = {g: eid for g, eid in ensembl.items() if eid}
    out: dict[str, TextCell] = {
        g: text_failed(
            f"{g} has no Ensembl id in this run's values.csv, so Open Targets could not "
            f"be asked where it is")
        for g in ensembl if not ensembl.get(g)
    }
    if not wanted:
        return out
    cache = Cache()
    async with Http() as http:
        recs = await OpenTargetsAdapter(http, cache).fetch(sorted(set(wanted.values())))
    for g, eid in wanted.items():
        out[g] = chromosome_cell(recs[eid])
    return out


def _write_drawer_chromosomes(run_id: str, chroms: dict[str, TextCell]) -> int:
    """Put the same reading in each baked drawer, beside the id it was read for.

    The gene's identity is the drawer's own `ensembl_id` (the same rule
    `drawer_bake.restamp_phenotype_models` follows): a drawer whose id is not
    the id this run drew is skipped rather than stamped with a chromosome for a
    different gene.
    """
    n = 0
    for g, cell in chroms.items():
        path = table_mod.baked_drawer_path(run_id, g)
        if path is None:
            continue
        d = json.loads(path.read_text())
        d["chromosome"] = cell.as_dict()
        path.write_text(json.dumps(d, indent=2) + "\n")
        n += 1
    return n


def rerender(run_id: str, *, check_only: bool = False) -> dict[str, Any]:
    t = table_mod.load(run_id)
    m = t.manifest
    runs_dir = table_mod.run_dir(run_id)
    cells = t.cells_by_ring()
    genes = list(t.genes)

    stats = gene_stats(cells)
    for g, rings in (m.get("filter_counts", {}).get("kept_unevaluated_by_gene") or {}).items():
        if g in stats:
            stats[g].unfiltered_rings = list(rings)

    ledger = m.get("ledger", [])
    ledger_counts: dict[str, int] = {}
    for e in ledger:
        ledger_counts[e["status"]] = ledger_counts.get(e["status"], 0) + 1
    # The ledger carries two kinds of entry and conflating them breaks the
    # accounting in both directions (`de_input.dropped_entries`): a `renamed`
    # gene is ON the figure and listed beside it.
    dropped = [e["input"] for e in ledger if e["status"] not in RENDERED_STATUSES]

    report = _report(m)
    banner = report.banner_lines() + [
        f"source contract FAILED: {c['check']}"
        for c in m.get("contracts", []) if not c.get("ok", True)
    ]
    conds = report.condition_lines()
    inv_summary = report.summary_line()
    de_source = SOURCES.DESource.from_manifest(m)
    notes = _contrast_notes(m)
    flags = _gene_flags(run_id, genes)
    chroms = asyncio.run(_chromosomes(t.ensembl))

    drawn = sorted(c.text for c in chroms.values() if c.state == "value")
    summary = {
        "run_id": run_id, "genes": len(genes),
        "chromosomes": drawn,
        "missing": {g: c.state for g, c in chroms.items() if c.state != "value"},
    }
    if check_only:
        return summary

    def _render_once(layout, svg: Path | None = None, png: Path | None = None,
                     write_png: bool = True) -> figure.FigureResult:
        return figure.render(
            genes=genes, cells_by_ring=cells, stats=stats, region=t.region,
            out_svg=svg or runs_dir / "figure.svg",
            out_png=(None if not write_png
                     else png if png is not None else runs_dir / "figure.png"),
            layout=layout,
            input_count=m["input_row_count"], rendered_count=m["rendered_count"],
            ledger_counts=ledger_counts, dropped_names=dropped,
            invariant_failures=banner, run_conditions=conds,
            ordering_note=m["ordering_note"],
            filter_notes=[f["label"] for f in m.get("filters", [])],
            gene_flags=flags,
            provenance=f"run {run_id} · {m['git_sha'][:12]}",
            gene_ids=t.ensembl, gene_chromosomes=chroms,
            invariant_summary=inv_summary, contrast_notes=notes, de_source=de_source,
        )

    def do_render(layout, **kw) -> figure.FigureResult:
        """`_render_once` with the chromosome suffix offered to the geometry --
        the same wrapper `run.do_render` is, for the same reason."""
        lay = layout if isinstance(layout, figure.Layout) else figure.LAYOUTS[layout]
        result, _ = figure.render_with_the_chromosomes_if_they_fit(
            lambda candidate: _render_once(candidate, **kw), lay)
        return result

    audit = do_render("audit")
    web_svg = runs_dir / "figure-web.svg"
    _, web_layout = figure.render_naming_the_rings_if_they_fit(
        lambda candidate: _render_once(candidate, svg=web_svg, write_png=False))
    # The claim that makes two renders of one run safe to ship: they may differ
    # in furniture, never in a cell.
    figure.assert_renders_agree(runs_dir / "figure.svg", web_svg)
    slide = do_render("slide", svg=runs_dir / "figure-slide.svg",
                      png=runs_dir / "figure-slide.png")
    figure.render_legend(
        cells_by_ring=cells, region=t.region,
        out_svg=runs_dir / "legend.svg", out_png=runs_dir / "legend.png",
        run_id=f"run {run_id}", provenance=f"run {run_id} · {m['git_sha'][:12]}",
        ordering_note=m["ordering_note"],
        filter_notes=[f["label"] for f in m.get("filters", [])],
        input_count=m["input_row_count"], rendered_count=m["rendered_count"],
        gene_flags=flags, de_source=de_source)
    figure.assert_artefacts_are_their_own_layout(runs_dir, web=web_layout)
    (runs_dir / "caption.txt").write_text(slide.caption + "\n")

    # The spoke order the re-render drew, against the order the run recorded.
    # A figure whose spokes moved is a different figure, whatever its cells say.
    if m.get("gene_order") and list(m["gene_order"]) != genes:
        raise AssertionError(
            f"{run_id}: the re-render drew {genes[:4]}… and the manifest records "
            f"{list(m['gene_order'])[:4]}…. The spoke order is stated on the figure; "
            f"a re-render that reorders it is not the same figure.")

    summary["cells"] = audit.n_cells
    summary["drawers_stamped"] = _write_drawer_chromosomes(run_id, chroms)
    return summary


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    ids = [a for a in argv if not a.startswith("-")] or list(DEMO_RUNS)
    for run_id in ids:
        s = rerender(run_id, check_only=check_only)
        print(f"{s['run_id']}: {s['genes']} genes, chr "
              f"{','.join(s['chromosomes'])}"
              + (f", {len(s['missing'])} without ({s['missing']})" if s["missing"] else "")
              + (f", {s.get('drawers_stamped', 0)} drawers stamped"
                 if not check_only else " [check only, nothing written]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
