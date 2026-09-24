"""Orchestration: DE table + region -> figure, CSV, manifest.

Order is fixed and matters: contracts -> parse -> resolve -> dedupe on the
resolved id -> adapters -> rank -> render -> invariants -> manifest. Resolution
comes before every ring (AGENTS.md rule 5) and the accounting assertion runs
before anything is drawn (rule 3).
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import figure, manifest as manifest_mod
from .adapters.clinicaltrials import (
    CTGovAdapter,
    activity_cell as ctgov_activity_cell,
    drawer_trials,
)
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
from .http import Http
from .invariants import InvariantReport, check_all
from .invariants import diamond_genes
from .rank import (
    DEFAULT_WEIGHTS,
    EXTERNAL_RING_COUNT,
    PARETO_AXES,
    order_for_figure,
    pareto_axis_completeness,
    rank_genes,
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

# Rings that feed the ranking. The user's own logFC/padj and k are drawn but not
# ranked against each other -- they are his measurement, not a target property.
#
# COUNCIL V5: `rodent` is gone from here as well as from DEFAULT_WEIGHTS. It was
# in both the weighted score and the median available rank while the figure's
# footer asserted it was "never folded into any score". It is now drawn, read in
# the drawer, and ranked nowhere -- which is what M4 asked for.
#
# v2.3 item 1: `chemical_probe` is drawn but is NOT here. It is sparse, and the
# median available rank would silently be computed over a different ring set for
# a probed gene than for an unprobed one.
RANKED_RINGS = (
    "tractable", "safety", "regional_specificity",
    "clinical_phase", "clinical_activity", "tdl", "literature",
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


async def run_analysis(
    *,
    table: bytes | str,
    region: str = DEFAULT_REGION,
    run_id: str | None = None,
    cache: Cache | None = None,
    runs_dir: Path | None = None,
    run_regression: bool = True,
    with_triage: bool = False,
) -> RunResult:
    t0 = time.time()
    run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    runs_dir = Path(runs_dir or RUNS_DIR) / run_id
    cache = cache or Cache()
    timings: dict[str, float] = {}
    warnings: list[str] = []

    parsed: ParsedInput = parse_de_table(table)

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

        ctgov = CTGovAdapter(http, cache)
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

        # v2.2 item 2. The drug names the ClinicalTrials.gov hop is keyed on.
        # COUNCIL D3: CT.gov cannot be keyed on a gene, so the names come from
        # Open Targets and the hop is per drug, deduplicated across the whole
        # submitted set (EGFR alone carries 82).
        ot_by_eid = {res.ensembl_id: ot_records[res.ensembl_id] for _, _, res in live}
        all_drug_names = sorted({
            n for rec in ot_by_eid.values() for n in rec.drug_names()
        })

        # v2.2 item 3. ChEMBL and Europe PMC are NOT here any more. Both drive
        # no ring and no rank, and on 2026-09-24 they were 99% and 64% of the
        # time spent on sources; they are fetched on gene click instead
        # (`app.gene_drawer`). What stays is one Europe PMC query, cache
        # bypassed, so invariant I5 is still *evaluated* on a live echo rather
        # than quietly becoming NOT_EVALUATED for ever.
        gtex_records, lit_records, ct_by_drug, pharos_records, probe = await asyncio.gather(
            timed("gtex", asyncio.gather(*(gtex.fetch_one(res.gencode_id)
                                           for _, _, res in live))),
            timed("pubtator", asyncio.gather(*(pubtator.fetch_one(d, None)
                                               for d, _, _ in live))),
            timed("clinicaltrials", ctgov.fetch_many(all_drug_names)),
            timed("pharos", pharos.fetch_many(
                [d for d, _, _ in live],
                {d: res.uniprot for d, _, res in live},
            )),
            timed("europepmc_probe", epmc.integrity_probe(live[0][0])),
        )
        timings["adapters_s"] = round(time.time() - t, 2)
        timings["ctgov_drug_hops"] = ctgov.hops

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
    ctgov_budget_hit = set(ctgov.budget_exhausted)

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
        # v2.3 item 1 (COUNCIL M7/V8): the dense three-key ring carries the
        # Pareto axis; the sparse probe key is beside it and may be hatched.
        "tractable": {g: tractable_cell(ot_by_gene[g]) for g in genes},
        "chemical_probe": {g: chemical_probe_cell(ot_by_gene[g]) for g in genes},
        "safety": {g: safety_cell(ot_by_gene[g]) for g in genes},
        "regional_specificity": {
            g: specificity_cell(gtex_by_gene[g], region) for g in genes
        },
        "clinical_phase": {g: clinical_phase_cell(ot_by_gene[g]) for g in genes},
        # v2.2 item 2: ClinicalTrials.gov v2, hopped per drug. A different
        # population from the AACT rows this ring counted before 2026-09-24,
        # and the ring's own label says so on the figure.
        "clinical_activity": {
            g: ctgov_activity_cell(
                drug_names_by_gene[g], ct_by_drug,
                drug_count=ot_by_gene[g].drug_count,
                budget_exhausted=bool(set(drug_names_by_gene[g]) & ctgov_budget_hit),
            )
            for g in genes
        },
        # v2.2 item 4: COUNCIL S1/V8, an explicit ordinal over Tdark/Tbio/Tchem/Tclin.
        "tdl": {g: tdl_cell(pharos_by_gene[g]) for g in genes},
        "rodent": {g: rodent_cell(ot_by_gene[g]) for g in genes},
        "literature": {g: literature_cell(lit_by_gene[g]) for g in genes},
    }

    rankings = rank_genes(cells_by_ring, DEFAULT_WEIGHTS, RANKED_RINGS)
    cells_by_ring["completeness"] = {
        g: value(float(rankings[g].k),
                 f"{rankings[g].k} of {EXTERNAL_RING_COUNT} external rings have a real "
                 f"measurement (COUNCIL V13: your own logFC and padj are not counted)")
        for g in genes
    }
    gene_order = order_for_figure(rankings)

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
    # v2.2 item 4. Pharos is now a Pareto axis, so a Pharos outage changes who
    # is eligible for the headline. That has to be visible.
    pharos_missing = [g for g in genes if cells_by_ring["tdl"][g].state != "value"]
    if pharos_missing:
        conditions.append((
            f"no Pharos TDL for {len(pharos_missing)} of {len(genes)} gene(s) "
            f"({', '.join(pharos_missing[:5])})",
            "TDL is a Pareto axis, so those genes are ineligible for the headline; "
            "Pharos covers 20,412 human targets and absence of a record is not Tdark",
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
                "it drives no ring and no rank (PLAN §8), so this is pure cost on the "
                "render path; failures are never cached, so every run re-pays it",
            ))

    completeness = pareto_axis_completeness(rankings)
    if not completeness["complete"]:
        missing = ", ".join(f"{ax} {n}" for ax, n in
                            completeness["missing_cells_per_axis"].items() if n)
        conditions.append((
            f"{completeness['missing_cells_total']} Pareto-axis cell(s) missing "
            f"({missing})",
            f"{completeness['n_ineligible']} gene(s) are ineligible for the headline "
            f"and carry no diamond; the front is a floor, not a ranking",
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
            rankings=rankings,
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
            weights=DEFAULT_WEIGHTS,
            ranked_rings=RANKED_RINGS,
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
        pareto_audit={
            "axes": list(PARETO_AXES),
            "front": [g for g in gene_order if rankings[g].pareto],
            "front_pareto_k": {g: rankings[g].pareto_k for g in gene_order},
            "n_eligible": completeness["n_eligible"],
            "n_ineligible": completeness["n_ineligible"],
            "diamond_genes": diamond_genes(svg_path),
            "diamonds_suppressed": fig_result.diamonds_suppressed,
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
    do_render(banner, conds, layout="slide",
              svg=runs_dir / "figure-slide.svg", png=runs_dir / "figure-slide.png")
    (runs_dir / "caption.txt").write_text(fig_result.caption + "\n")

    _write_csv(csv_path, fig_result.csv_rows, ledger, parsed)
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
            "k": rankings[g].k,
            "pareto": rankings[g].pareto,
            "pareto_eligible": rankings[g].pareto_eligible,
            "pareto_k": rankings[g].pareto_k,
            # v2.2 item 4: the drawer said "n of 4 axes" in a template literal.
            "n_pareto_axes": len(PARETO_AXES),
            "unstable": rankings[g].unstable,
            "median_rank": rankings[g].median_rank,
            "weighted_score": rankings[g].weighted_score,
            "rank_note": rankings[g].note,
            "pipeline_summary": pipeline_summary(rec),
            "severe_liabilities": severe_liabilities(rec),
            "attribution_error": rec.associations_error,
            "drugs": drug_pipeline_rows(rec),
            "safety_liabilities": rec.safety_liabilities,
            # v2.2 item 2: sponsor and live status, which is what says whether a
            # target is crowded. Puck's second hard condition.
            "trials": drawer_trials(drug_names_by_gene[g], ct_by_drug),
            # What Open Targets' own trial rows say, kept as context now that the
            # ring is sourced elsewhere -- and as the standing record of the AACT
            # disappearance (`docs/V2-REPORT.md` §v2.1, Newly open 2).
            "ot_trial_rows": clinical_activity_cell(rec).detail,
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
        weights=dict(DEFAULT_WEIGHTS), llm=llm_info, timings=timings,
        sources={"gtex_region": region, "cache_hits": cache.hits, "cache_misses": cache.misses},
        warnings=warnings,
        # COUNCIL V3: the headline is not reproducible while a Pareto axis is
        # incomplete, so how incomplete it was is part of the audit trail.
        pareto_completeness=completeness,
        run_conditions=[{"condition": h, "affects": w} for h, w in conditions],
    )
    manifest_path = man.write(runs_dir / "manifest.json")

    return RunResult(
        run_id=run_id, region=region, svg_path=svg_path, csv_path=csv_path,
        manifest_path=manifest_path, manifest=man, invariants=invariants,
        gene_order=gene_order, drawers=drawers, ledger=ledger, warnings=warnings,
    )


def _write_csv(path: Path, rows, ledger, parsed) -> None:
    """The CSV carries the drop ledger as its own section (QA B7 item 3), so the
    dropped genes travel with the figure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
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


__all__ = ["run_analysis", "RunResult", "available_regions", "RANKED_RINGS", "DEFAULT_REGION"]
