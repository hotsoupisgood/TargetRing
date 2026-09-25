"""Orchestration: DE table + region -> figure, CSV, manifest.

Order is fixed and matters: contracts -> parse -> resolve -> dedupe on the
resolved id -> adapters -> filter -> order -> render -> invariants -> manifest.
Resolution comes before every ring (AGENTS.md rule 5) and the accounting
assertion runs before anything is drawn (rule 3) and again after filtering,
because a filter removes genes from the plot and rule 3 does not care why a gene
is missing.

v2.5 removed ranking: no Pareto front, no weighted score, no weights, no
eligibility, no rank within the submitted set and no reweighting-stability flag.
What is left is a gene set, rings of evidence and a drawer per gene.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import figure, manifest as manifest_mod
from .adapters.gtex import GTExAdapter, GTExRecord, list_regions, specificity_cell
from .adapters.literature import (
    EuropePMCAdapter,
    LiteratureRecord,
    PubTatorAdapter,
    literature_cell,
)
from .adapters.pharos import PharosAdapter, PharosRecord, tdl_cell
from .adapters.opentargets import (
    OpenTargetsAdapter,
    OTRecord,
    chemical_probe_cell,
    clinical_activity_cell,
    clinical_phase_cell,
    drug_pipeline_rows,
    pipeline_summary,
    rodent_cell,
    safety_cell,
    severe_liabilities,
    tractable_cell,
    trials_panel,
)
from .cache import Cache
from .config import LLM_SPEND_CAP_USD, RUNS_DIR
from .contract import check_contracts
from .de_input import (
    LedgerEntry,
    ParsedInput,
    assert_accounted,
    dedupe_on_resolved,
    dropped_entries,
    parse_de_table,
)
from .filters import Filter, parse_filters
from . import filters as filters_mod
from .http import Http
from .invariants import InvariantReport, check_all
from . import order as order_mod
from .order import (
    EXTERNAL_RING_COUNT,
    SUBMITTED,
    gene_stats,
    normalise_ordering,
    order_for_figure,
    ordering_label,
)
from .regression import five_id_regression
from .resolve import Resolver, ledger_reason
from .states import Cell, no_data, value

DEFAULT_REGION = "Lung"

# Sources whose values reach the drawer and never a ring or a rank.
#
# v2.2 item 3: both are now fetched on gene click instead of on the render path,
# so neither should appear in the render's timings at all. The condition below
# is kept as the guard that says so if one ever comes back: it fired correctly
# on v2 (ChEMBL at 99% of source time) and the cheapest way to keep that alarm
# working is to leave it armed.
DRAWER_ONLY_SOURCES = ("chembl", "europepmc")
# Above this share of the time spent on sources, a drawer-only source is
# reported as a run condition.
COST_SHARE_WARN = 0.40

# v2.5 item 2. The rings a threshold filter may be set on: every ring that
# carries a number a user could reasonably threshold, including his own two.
# `clinical_activity` is here and is a three-state categorical, so a threshold on
# it is a threshold on the stated ordinal (-1 stopped for cause, 0 dormant,
# 1 live) and nothing else; `completeness` is derived after filtering and is
# deliberately absent, because filtering on k would be filtering on how much
# anybody has studied a gene, which is M3's bias with a slider on it.
FILTERABLE_RINGS = (
    "logfc", "neg_log10_padj", "tractable", "chemical_probe", "safety",
    "regional_specificity", "clinical_phase", "clinical_activity", "tdl",
    "rodent", "literature",
)


@dataclass
class RunResult:
    run_id: str
    region: str
    svg_path: Path
    csv_path: Path
    manifest_path: Path
    manifest: manifest_mod.Manifest
    invariants: InvariantReport
    gene_order: list[str] = field(default_factory=list)
    drawers: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger: list[LedgerEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ordering: str = SUBMITTED
    active_filters: list[dict[str, Any]] = field(default_factory=list)


async def run_analysis(
    *,
    table: bytes | str,
    region: str = DEFAULT_REGION,
    run_id: str | None = None,
    cache: Cache | None = None,
    runs_dir: Path | None = None,
    run_regression: bool = True,
    with_triage: bool = False,
    ordering: str | None = None,
    filters: Any = None,
) -> RunResult:
    t0 = time.time()
    run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    runs_dir = Path(runs_dir or RUNS_DIR) / run_id
    cache = cache or Cache()
    timings: dict[str, float] = {}
    warnings: list[str] = []

    parsed: ParsedInput = parse_de_table(table)
    # Before any network work: a filter naming a ring this build does not have is
    # a mistake the user can fix in a second, and finding out after a 27 s cold
    # run is not a kindness.
    active_filters: list[Filter] = parse_filters(filters, FILTERABLE_RINGS)

    async with Http() as http:
        t = time.time()
        contracts = await check_contracts(http)
        timings["contracts_s"] = round(time.time() - t, 2)
        contract_failures = [c for c in contracts if not c["ok"]]
        for c in contract_failures:
            warnings.append(f"source contract FAILED — {c['check']}: {c['detail'][:200]}")

        # --- resolve, before any ring -----------------------------------
        t = time.time()
        resolver = Resolver(http, cache)
        resolutions = await resolver.resolve_all([r.symbol for r in parsed.rows])
        timings["resolve_s"] = round(time.time() - t, 2)

        ledger: list[LedgerEntry] = list(parsed.ledger)
        resolved_key: dict[int, str | None] = {}
        for row in parsed.rows:
            res = resolutions[row.symbol]
            if res.usable:
                resolved_key[row.row_number] = res.ensembl_id
            else:
                resolved_key[row.row_number] = None
                ledger.append(LedgerEntry(
                    row.symbol,
                    "excel_corrupt" if res.status == "excel_corrupt"
                    else "ambiguous" if res.status == "ambiguous" else "unresolved",
                    ledger_reason(res), row_number=row.row_number,
                ))

        kept, dup_drops = dedupe_on_resolved(
            [r for r in parsed.rows if resolved_key.get(r.row_number)], resolved_key
        )
        ledger.extend(dup_drops)

        # Genes that survive to the figure, keyed by approved symbol.
        live: list[tuple[str, Any, Any]] = []  # (display, DERow, Resolution)
        for row in kept:
            res = resolutions[row.symbol]
            display = res.approved_symbol or row.symbol
            live.append((display, row, res))
            if res.status in ("renamed", "possible_mouse"):
                ledger.append(LedgerEntry(
                    row.symbol, res.status, res.note,
                    resolved_symbol=res.approved_symbol, ensembl_id=res.ensembl_id,
                    row_number=row.row_number,
                ))

        rendered_count = len(live)
        # AGENTS.md rule 3: assert before anything is drawn.
        assert_accounted(parsed.input_row_count, rendered_count, ledger)

        if not live:
            raise ValueError(
                f"No gene survived resolution. {parsed.input_row_count} rows in, all ledgered. "
                f"Reasons: {parsed.status_counts}"
            )

        # --- adapters ----------------------------------------------------
        t = time.time()
        ot_adapter = OpenTargetsAdapter(http, cache)
        gtex = GTExAdapter(http, cache)
        pubtator = PubTatorAdapter(http, cache)
        epmc = EuropePMCAdapter(http, cache)

        pharos = PharosAdapter(http, cache)

        ot_records = await ot_adapter.fetch([res.ensembl_id for _, _, res in live])
        # COUNCIL V1: the indication-attribution query needs the drug rows, so it
        # runs after the main fetch and only for targets that have drugs.
        await ot_adapter.fetch_attribution(ot_records)
        timings["opentargets_s"] = round(time.time() - t, 2)

        # Per-source timings. v1 reported one `adapters_s` number, which is why
        # "Europe PMC is 52 s of a 50-gene run" had to be measured by hand
        # afterwards -- and why nothing noticed when ChEMBL started costing
        # 177 s of a 15-gene run. A source that drives no ring must not be able
        # to dominate the clock invisibly.
        async def timed(name: str, coro):
            t0 = time.time()
            try:
                return await coro
            finally:
                timings[f"{name}_s"] = round(time.time() - t0, 2)

        # v2.4. There is no ClinicalTrials.gov hop here any more. The activity
        # ring's trial rows -- status, sponsor, categorised stop reason, NCT id
        # -- are in `drugAndClinicalCandidates.rows[].clinicalReports` in the
        # Open Targets call above, and were the whole time: the hop existed
        # because a `source == "AACT"` filter stopped matching when Open Targets
        # relabelled the rows `ClinicalTrials.gov`. It cost 217 requests and
        # 48.2 s of a 70.5 s cold 50-gene run (`docs/V2-REPORT.md` §v2.3 item 3).

        # v2.2 item 3. ChEMBL and Europe PMC are NOT here. Both drive no ring
        # and no rank, and on 2026-09-24 they were 99% and 64% of the time spent
        # on sources; they are fetched on gene click instead
        # (`app.gene_drawer`). What stays is one Europe PMC query, cache
        # bypassed, so invariant I5 is still *evaluated* on a live echo rather
        # than quietly becoming NOT_EVALUATED for ever.
        gtex_records, lit_records, pharos_records, probe = await asyncio.gather(
            timed("gtex", asyncio.gather(*(gtex.fetch_one(res.gencode_id)
                                           for _, _, res in live))),
            timed("pubtator", asyncio.gather(*(pubtator.fetch_one(d, None)
                                               for d, _, _ in live))),
            timed("pharos", pharos.fetch_many(
                [d for d, _, _ in live],
                {d: res.uniprot for d, _, res in live},
            )),
            timed("europepmc_probe", epmc.integrity_probe(live[0][0])),
        )
        timings["adapters_s"] = round(time.time() - t, 2)

        # --- the five-ID regression test, against the same live API -------
        regression = None
        if run_regression:
            t = time.time()
            regression = await five_id_regression(http, cache)
            timings["regression_s"] = round(time.time() - t, 2)

    # --- assemble cells --------------------------------------------------
    genes = [d for d, _, _ in live]
    by_gene = {d: (row, res) for d, row, res in live}
    ot_by_gene: dict[str, OTRecord] = {d: ot_records[res.ensembl_id] for d, _, res in live}
    gtex_by_gene: dict[str, GTExRecord] = dict(zip(genes, gtex_records))
    lit_by_gene: dict[str, LiteratureRecord] = dict(zip(genes, lit_records))
    pharos_by_gene: dict[str, PharosRecord] = {g: pharos_records[g] for g in genes}
    drug_names_by_gene = {g: ot_by_gene[g].drug_names() for g in genes}

    import math

    cells_by_ring: dict[str, dict[str, Cell]] = {
        "logfc": {g: value(by_gene[g][0].logfc, f"logFC {by_gene[g][0].logfc:g} (submitted)")
                  for g in genes},
        "neg_log10_padj": {
            g: (value(-math.log10(by_gene[g][0].padj),
                      f"adjusted p {by_gene[g][0].padj:g} (submitted)")
                if by_gene[g][0].padj > 0
                else no_data("adjusted p is exactly 0; -log10 is undefined, not infinite"))
            for g in genes
        },
        # v2.3 item 1 (COUNCIL M7/V8): two rings, because an absent probe key
        # must not take three dense keys down with it. The split was made so the
        # dense ring could carry the Pareto axis; v2.5 removed the axis and the
        # split still stands on its own -- "has been drugged" and "family
        # suggests druggable" are different claims.
        "tractable": {g: tractable_cell(ot_by_gene[g]) for g in genes},
        "chemical_probe": {g: chemical_probe_cell(ot_by_gene[g]) for g in genes},
        "safety": {g: safety_cell(ot_by_gene[g]) for g in genes},
        "regional_specificity": {
            g: specificity_cell(gtex_by_gene[g], region) for g in genes
        },
        "clinical_phase": {g: clinical_phase_cell(ot_by_gene[g]) for g in genes},
        # v2.4: back on Open Targets' own `clinicalReports`, read out of the
        # gene query above. Rows are selected by carrying a status rather than
        # by a source literal, and the distinct source labels seen travel into
        # the cell so the next relabelling is visible instead of silent.
        "clinical_activity": {g: clinical_activity_cell(ot_by_gene[g]) for g in genes},
        # v2.2 item 4: COUNCIL S1/V8, an explicit ordinal over Tdark/Tbio/Tchem/Tclin.
        "tdl": {g: tdl_cell(pharos_by_gene[g]) for g in genes},
        "rodent": {g: rodent_cell(ot_by_gene[g]) for g in genes},
        "literature": {g: literature_cell(lit_by_gene[g]) for g in genes},
    }

    # --- filters (v2.5 item 2) -------------------------------------------
    # Applied here: after every ring has its real value, before k is counted and
    # before anything is drawn. Two rules, both promised and both asserted in
    # `filters.apply`: a threshold never silently drops a gene with no data on
    # that ring (it is kept and marked, and only an explicit per-filter toggle
    # removes it), and every filtered gene lands in the drop ledger under its own
    # status so `input == rendered + ledgered` still holds.
    outcome = filters_mod.apply(genes, cells_by_ring, active_filters)
    for d in outcome.drops:
        row, res = by_gene[d.gene]
        ledger.append(LedgerEntry(
            row.symbol, d.status, d.reason,
            resolved_symbol=d.gene, ensembl_id=res.ensembl_id,
            row_number=row.row_number,
        ))
    if not outcome.kept:
        raise ValueError(
            f"every one of the {len(genes)} gene(s) was removed by the "
            f"{len(active_filters)} active filter(s): "
            f"{'; '.join(f.label() for f in active_filters)}. No figure is drawn rather "
            f"than an empty circle."
        )
    genes = outcome.kept
    cells_by_ring = filters_mod.restrict(cells_by_ring, genes)
    rendered_count = len(genes)
    # AGENTS.md rule 3 again. A gene removed by a slider is as missing from the
    # figure as a gene that failed to resolve, and the counts stamped on the
    # figure have to add up either way.
    assert_accounted(parsed.input_row_count, rendered_count, ledger)

    stats = gene_stats(cells_by_ring)
    for g, rings_unfiltered in outcome.kept_unevaluated.items():
        if g in stats:
            stats[g].unfiltered_rings = rings_unfiltered
    cells_by_ring["completeness"] = {
        g: value(float(stats[g].k),
                 f"{stats[g].k} of {EXTERNAL_RING_COUNT} external rings have a real "
                 f"measurement (COUNCIL V13: your own logFC and padj are not counted)")
        for g in genes
    }
    ordering = normalise_ordering(ordering, cells_by_ring)
    ring_labels = {s.key: s.label for s in figure.RING_SPECS}
    ring_key = order_mod.is_ring_ordering(ordering)
    ordering_note = ordering_label(ordering, ring_labels.get(ring_key or ""))
    filter_notes = filters_mod.filter_lines(active_filters, ring_labels)
    gene_order = order_for_figure(genes, cells_by_ring, ordering)

    # COUNCIL V1 (ii): genes whose Open Targets safety record includes a cardiac
    # or mortality liability. Carried to the figure so the clinical rings cannot
    # be read as a safety statement.
    severe_liability_flags = {
        g: [x["event"] for x in sl]
        for g in genes if (sl := severe_liabilities(ot_by_gene[g]))
    }

    # --- literature audit for invariant 5 --------------------------------
    # COUNCIL V11: live and replayed echoes are counted separately. An echo that
    # was asserted three days ago and read back off disk is not an echo this run
    # checked, and I5 must not PASS on replays alone.
    #
    # v2.2 item 3: the per-gene Europe PMC fetch is now on the drawer, so the
    # render path issues exactly one query -- cache bypassed, live by
    # construction -- whose echo is what I5 asserts. See
    # `EuropePMCAdapter.integrity_probe` for what that narrows.
    echo_live = echo_replayed = ncbi_checked = 0
    echo_mismatches: list[str] = []
    unreachable: list[str] = []
    for g in genes:
        if lit_by_gene[g].error is None:
            ncbi_checked += 1
    if probe.echo_ok is True and probe.live:
        echo_live += 1
    elif probe.echo_ok is True:
        echo_replayed += 1
    elif probe.echo_ok is False:
        echo_mismatches.append(f"{genes[0]}: {probe.error}")
    elif probe.error:
        unreachable.append(f"{genes[0]}: {probe.error[:90]}")
    lit_audit = {
        "echo_live": echo_live, "echo_replayed": echo_replayed,
        # Kept for anything reading the old key; it is the sum, and it is not
        # what I5 decides on.
        "echo_checked": echo_live + echo_replayed,
        "ncbi_checked": ncbi_checked,
        "echo_mismatches": echo_mismatches, "unreachable": unreachable[:10],
        "ncbi_errors": [],
    }

    # --- run conditions: facts about the run that are not invariant failures --
    # UX A1. These render amber and below the rings, never as the red
    # DO-NOT-TRUST banner. See `figure._draw_conditions`.
    conditions: list[tuple[str, str]] = []
    if unreachable:
        conditions.append((
            "the Europe PMC integrity probe did not answer",
            "it sets no ring value (PLAN §2, COUNCIL M6); the literature ring is "
            "PubTator3 and is unaffected, and Europe PMC counts are fetched on gene "
            "click where the echo is asserted again",
        ))
    # v2.2 item 4. A Pharos outage no longer changes a headline -- there is none
    # -- but it still empties a whole ring, and a ring of hatching is a fact
    # about Pharos rather than about the genes.
    pharos_missing = [g for g in genes if cells_by_ring["tdl"][g].state != "value"]
    if pharos_missing:
        conditions.append((
            f"no Pharos TDL for {len(pharos_missing)} of {len(genes)} gene(s) "
            f"({', '.join(pharos_missing[:5])})",
            "those cells are hatched, never zeroed; Pharos covers 20,412 human targets "
            "and absence of a record is not Tdark",
        ))
    # A source that drives no ring should not be able to dominate a run without
    # saying so. ChEMBL is drawer evidence only (PLAN §8) and was measured at
    # 177 s of a 192 s 15-gene run while EBI was 500-ing; failures are
    # deliberately never cached, so every run re-pays it. Reported as a run
    # condition, which is what it is -- not an invariant failure, and not
    # something to silently work around.
    for source in DRAWER_ONLY_SOURCES:
        spent = timings.get(f"{source}_s", 0.0)
        total = max(timings.get("adapters_s", 0.0), 1e-9)
        if spent > COST_SHARE_WARN * total and spent > 10:
            conditions.append((
                f"{source} took {spent:.0f}s of the {total:.0f}s spent on sources "
                f"({spent / total:.0%})",
                "it drives no ring (PLAN §8), so this is pure cost on the render path; "
                "failures are never cached, so every run re-pays it",
            ))

    # v2.5 item 2, rule 1 made visible. A gene kept because a threshold could not
    # be evaluated against a cell it has no data for is on the figure and marked,
    # and how many there are is a fact about the filter, not about the genes.
    if outcome.kept_unevaluated:
        per_ring: dict[str, int] = {}
        for rings_unfiltered in outcome.kept_unevaluated.values():
            for r in rings_unfiltered:
                per_ring[r] = per_ring.get(r, 0) + 1
        conditions.append((
            f"{len(outcome.kept_unevaluated)} gene(s) kept unfiltered: no data on a "
            f"filtered ring ({', '.join(f'{r} {n}' for r, n in sorted(per_ring.items()))})",
            "a threshold never drops a gene for being unmeasured (COUNCIL v0 M3); they are "
            "drawn, marked ~, and only the explicit per-filter 'drop no-data' toggle "
            "removes them",
        ))
    if outcome.drops:
        conditions.append((
            f"{len(outcome.drops)} gene(s) removed by {len(active_filters)} threshold "
            f"filter(s): {'; '.join(filter_notes)}",
            "this figure is not your whole submitted set; every filtered gene is in "
            "ledger.csv with the filter it failed",
        ))
    # Deliberately NOT added to `warnings`: the conditions have their own
    # channel now (#conditions on the page, the amber notice on the figure,
    # `run_conditions` in the manifest). Copying them into `warnings` printed
    # every line twice on screen.

    # --- render ----------------------------------------------------------
    svg_path = runs_dir / "figure.svg"
    csv_path = runs_dir / "values.csv"
    t = time.time()

    def do_render(failures: list[str], conds: list[str] = [],
                  layout: str = "audit", svg: Path | None = None,
                  png: Path | None = None) -> figure.FigureResult:
        return figure.render(
            genes=gene_order,
            cells_by_ring=cells_by_ring,
            stats=stats,
            region=region,
            out_svg=svg or svg_path,
            out_png=png if png is not None else runs_dir / "figure.png",
            layout=layout,
            input_count=parsed.input_row_count,
            rendered_count=rendered_count,
            ledger_counts={
                s: n for s, n in
                ((e.status, sum(1 for x in ledger if x.status == e.status)) for e in ledger)
            },
            dropped_names=[e.input_symbol for e in dropped_entries(ledger)],
            invariant_failures=failures,
            run_conditions=conds,
            ordering_note=ordering_note,
            filter_notes=filter_notes,
            gene_flags=severe_liability_flags,
            provenance=f"run {run_id} · {manifest_mod.git_sha()[:12]}",
        )

    fig_result = do_render([])
    timings["render_s"] = round(time.time() - t, 2)

    invariants = check_all(
        svg_path=svg_path,
        csv_rows=fig_result.csv_rows,
        gene_order=gene_order,
        input_count=parsed.input_row_count,
        rendered_count=rendered_count,
        ledger_count=len(dropped_entries(ledger)),
        regression=regression,
        literature_audit=lit_audit,
        figure_claims={
            "ordering": ordering,
            # The order his table arrived in, which is both the default ordering
            # and the documented tie-break for the others. I7 re-derives the
            # expected spoke order from this and the drawn values.
            "submitted_order": genes,
            "ordering_note": fig_result.ordering_note,
            "filter_notes": list(fig_result.filter_notes),
            "n_filtered": len(outcome.drops),
            "n_kept_unevaluated": len(outcome.kept_unevaluated),
        },
        conditions=conditions,
    )

    # UX A1 / COUNCIL V3 / V11: two channels, because a source being down and an
    # invariant being violated are different facts.
    #
    # Red, across the rings: an invariant was checked and did not hold, or a
    # source contract failed -- the API is not the shape this build parses, so
    # the numbers may be wrong.
    banner = invariants.banner_lines() + [
        f"source contract FAILED: {c['check']}" for c in contract_failures
    ]
    # Amber, below the rings: a check could not be run, or a source was down.
    conds = invariants.condition_lines()
    if banner or conds:
        # Re-render. The figure must not look clean when it is not -- and must
        # not look broken when it is not either.
        fig_result = do_render(banner, conds)

    # UX A4: the slide render. Same data, 16:9, type that survives projection,
    # with the ring key and the caveats moved into the caption beside it. The
    # audit render above keeps every stamp and stays the morning artefact.
    slide_result = do_render(banner, conds, layout="slide",
                             svg=runs_dir / "figure-slide.svg",
                             png=runs_dir / "figure-slide.png")
    # v2.7: the caption is the SLIDE's caption, not the audit's. It always was
    # the slide's artefact -- "everything the slide render drops, as text to
    # paste underneath" -- and until now the two were word for word identical,
    # so taking the audit's cost nothing. They are not identical any more: the
    # audit reserves a 26 degree gap for its ring names and the slide does not,
    # and each render's caption states its own geometry. The audit's sentence
    # pasted under the slide is COUNCIL V5 in the one file that exists to be
    # read beside the figure.
    (runs_dir / "caption.txt").write_text(slide_result.caption + "\n")

    _write_csv(csv_path, fig_result.csv_rows, ledger, parsed,
               ordering_note, active_filters, outcome.kept_unevaluated)
    # UX A3. The ledger is already appended to values.csv as a second table, but
    # a second table below a comment line is not something a CSV parser reads --
    # which is why the UX review concluded the dropped genes were absent from
    # the file altogether. It is also written here as a CSV in its own right.
    (runs_dir / "ledger.csv").write_text(ledger_csv(ledger))

    # --- drawers ---------------------------------------------------------
    drawers: dict[str, dict[str, Any]] = {}
    for g in genes:
        rec = ot_by_gene[g]
        row, res = by_gene[g]
        drawers[g] = {
            "gene": g,
            "input_symbol": row.symbol,
            "ensembl_id": res.ensembl_id,
            "uniprot": res.uniprot,
            "gencode_id": res.gencode_id,
            "resolution_status": res.status,
            "resolution_note": res.note,
            "logfc": row.logfc,
            "padj": row.padj,
            "k": stats[g].k,
            "k_max": EXTERNAL_RING_COUNT,
            "k_note": stats[g].note,
            # v2.5 item 2, rule 1: the rings where a filter was active and this
            # gene had nothing to compare, so it was kept rather than dropped.
            "unfiltered_rings": list(stats[g].unfiltered_rings),
            "pipeline_summary": pipeline_summary(rec),
            "severe_liabilities": severe_liabilities(rec),
            "attribution_error": rec.associations_error,
            "drugs": drug_pipeline_rows(rec),
            "safety_liabilities": rec.safety_liabilities,
            # v2.2 item 2's two conditions -- sponsor and live status reach the
            # drawer, and the panel says which source it counted -- met in v2.4
            # off the gene query, with v2.3 item 2's distinct-NCT de-duplication
            # kept and the Open Targets stop categories added beside the
            # sponsor's free text.
            "trials": trials_panel(rec),
            "tdl": {
                "state": cells_by_ring["tdl"][g].state,
                "value": cells_by_ring["tdl"][g].value,
                "detail": cells_by_ring["tdl"][g].detail,
                "raw": cells_by_ring["tdl"][g].raw,
            },
            "literature": {
                "pubtator_count": lit_by_gene[g].count,
                "pubtator_query": lit_by_gene[g].query,
                "years": lit_by_gene[g].years,
                "error": lit_by_gene[g].error,
                # v2.2 item 3: fetched on click, not on the render path.
                "europepmc": None,
                "europepmc_note": "fetched on gene click (v2.2 item 3)",
            },
            # v2.2 item 3: likewise. `null` means not fetched yet, which is not
            # the same as ChEMBL having nothing -- the four-state semantics of
            # `mechanism_summary` are unchanged and arrive on click.
            "chembl": None,
            "chembl_note": "fetched on gene click (v2.2 item 3)",
            "mouse_phenotypes": [
                p.get("modelPhenotypeLabel") for p in rec.mouse_phenotypes[:25]
            ],
            "cells": {
                ring: {
                    "state": cells_by_ring[ring][g].state,
                    "value": cells_by_ring[ring][g].value,
                    "detail": cells_by_ring[ring][g].detail,
                }
                for ring in cells_by_ring if g in cells_by_ring[ring]
            },
            "triage": None,
        }

    llm_info: dict[str, Any] = {
        "attempted": with_triage,
        "spend_usd": 0.0,
        "note": "not run",
    }
    if with_triage:
        # LLM triage is not built in v1. Fail loudly rather than ImportError --
        # a caller asking for triage must not get a figure that silently has none.
        raise NotImplementedError(
            "LLM triage is not implemented in v1. AGENTS.md rule 8 caps it at $5 and "
            f"limits it to the top 10 genes; ${LLM_SPEND_CAP_USD:.2f} is unspent. "
            "Nothing on the current figure depends on it."
        )

    timings["total_s"] = round(time.time() - t0, 2)

    man = manifest_mod.build(
        run_id=run_id, region=region,
        input_row_count=parsed.input_row_count,
        rendered_count=rendered_count, ledger_count=len(dropped_entries(ledger)),
        gene_order=gene_order, invariants=invariants, contracts=contracts,
        ledger=[{
            "input": e.input_symbol, "status": e.status, "reason": e.reason,
            "resolved_symbol": e.resolved_symbol, "ensembl_id": e.ensembl_id,
            "row": e.row_number,
        } for e in ledger],
        llm=llm_info, timings=timings,
        sources={"gtex_region": region, "cache_hits": cache.hits, "cache_misses": cache.misses},
        warnings=warnings,
        # v2.5 item 2: the figure is reproducible from its artefacts only if the
        # settings that shaped it travel with them.
        ordering=ordering,
        ordering_note=ordering_note,
        filters=filters_mod.as_dicts(active_filters),
        filter_counts={
            "filtered": len(outcome.drops),
            "kept_unevaluated": len(outcome.kept_unevaluated),
            "kept_unevaluated_by_gene": {g: list(rs) for g, rs
                                         in outcome.kept_unevaluated.items()},
        },
        run_conditions=[{"condition": h, "affects": w} for h, w in conditions],
        # v2.8. In the figure's own radial order, so the manifest's citation list
        # reads in the same order as the legend column a reader is comparing it
        # against.
        ring_keys=[s.key for s in figure.RING_SPECS],
    )
    manifest_path = man.write(runs_dir / "manifest.json")

    return RunResult(
        run_id=run_id, region=region, svg_path=svg_path, csv_path=csv_path,
        manifest_path=manifest_path, manifest=man, invariants=invariants,
        gene_order=gene_order, drawers=drawers, ledger=ledger, warnings=warnings,
        ordering=ordering, active_filters=filters_mod.as_dicts(active_filters),
    )


def _write_csv(path: Path, rows, ledger, parsed, ordering_note: str = "",
               active_filters: Sequence[Filter] = (),
               kept_unevaluated: dict[str, list[str]] | None = None) -> None:
    """The CSV carries the drop ledger as its own section (QA B7 item 3), so the
    dropped genes travel with the figure.

    v2.5 item 2: and the filter settings, so the figure is reproducible from its
    artefacts. A `values.csv` that does not say which thresholds were active
    describes a gene set nobody can rebuild.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    kept_unevaluated = kept_unevaluated or {}
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        fh.write(f"\n# SPOKE ORDER — {ordering_note}\n")
        fh.write("\n# FILTERS ACTIVE ON THIS FIGURE — it is not the whole submitted set\n")
        fw = csv.writer(fh)
        fw.writerow(["ring", "comparison", "threshold", "drop_no_data", "expression"])
        for f in active_filters:
            fw.writerow([f.ring, f.symbol, f"{f.threshold:g}", f.drop_no_data,
                         f"{f.ring} {f.symbol} {f.threshold:g}"])
        if not active_filters:
            fw.writerow(["", "", "", "", "none"])
        if kept_unevaluated:
            fh.write("\n# KEPT, NOT FILTERED — no data on a filtered ring, so the "
                     "threshold could not be evaluated (marked ~ on the figure)\n")
            uw = csv.writer(fh)
            uw.writerow(["gene", "rings_not_evaluated"])
            for g, rings_unfiltered in sorted(kept_unevaluated.items()):
                uw.writerow([g, " ".join(rings_unfiltered)])
        fh.write("\n# DROP LEDGER — every input row is above or below this line\n")
        lw = csv.writer(fh)
        lw.writerow(["input_symbol", "status", "resolved_symbol", "ensembl_id", "row", "reason"])
        for e in ledger:
            lw.writerow([e.input_symbol, e.status, e.resolved_symbol or "",
                         e.ensembl_id or "", e.row_number or "", e.reason])
        fh.write(f"\n# input_rows={parsed.input_row_count} "
                 f"rendered={len({r['gene'] for r in rows})} ledgered={len(ledger)}\n")


def ledger_csv(ledger) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["input_symbol", "status", "resolved_symbol", "ensembl_id", "row", "reason"])
    for e in ledger:
        w.writerow([e.input_symbol, e.status, e.resolved_symbol or "", e.ensembl_id or "",
                    e.row_number or "", e.reason])
    return buf.getvalue()


async def available_regions(cache: Cache | None = None) -> list[str]:
    """The GTEx tissue panel, taken from a gene we know is broadly expressed."""
    cache = cache or Cache()
    async with Http() as http:
        rec = await GTExAdapter(http, cache).fetch_one("ENSG00000157764.12")
    return list_regions(rec)


__all__ = ["run_analysis", "RunResult", "available_regions", "FILTERABLE_RINGS",
           "DEFAULT_REGION"]
