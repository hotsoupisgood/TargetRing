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
from .adapters.chembl import ChEMBLAdapter, mechanism_summary
from .adapters.gtex import GTExAdapter, GTExRecord, list_regions, specificity_cell
from .adapters.literature import (
    EuropePMCAdapter,
    LiteratureRecord,
    PubTatorAdapter,
    literature_cell,
)
from .adapters.opentargets import (
    OpenTargetsAdapter,
    OTRecord,
    clinical_activity_cell,
    clinical_phase_cell,
    drug_pipeline_rows,
    druggability_cell,
    pipeline_summary,
    rodent_cell,
    safety_cell,
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
from .rank import DEFAULT_WEIGHTS, order_for_figure, rank_genes
from .regression import five_id_regression
from .resolve import Resolver, ledger_reason
from .states import Cell, no_data, value

DEFAULT_REGION = "Lung"

# Rings that feed the ranking. The user's own logFC/padj and k are drawn but not
# ranked against each other -- they are his measurement, not a target property.
RANKED_RINGS = (
    "druggability", "safety", "regional_specificity",
    "clinical_phase", "clinical_activity", "rodent", "literature",
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
        chembl = ChEMBLAdapter(http, cache)

        ot_records = await ot_adapter.fetch([res.ensembl_id for _, _, res in live])
        gtex_records, lit_records, chembl_records, epmc_results = await asyncio.gather(
            asyncio.gather(*(gtex.fetch_one(res.gencode_id) for _, _, res in live)),
            asyncio.gather(*(pubtator.fetch_one(d, None) for d, _, _ in live)),
            asyncio.gather(*(chembl.fetch_one(res.uniprot) for _, _, res in live)),
            asyncio.gather(*(epmc.fetch_one(d, None) for d, _, _ in live)),
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
    chembl_by_gene = dict(zip(genes, chembl_records))

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
        "druggability": {g: druggability_cell(ot_by_gene[g]) for g in genes},
        "safety": {g: safety_cell(ot_by_gene[g]) for g in genes},
        "regional_specificity": {
            g: specificity_cell(gtex_by_gene[g], region) for g in genes
        },
        "clinical_phase": {g: clinical_phase_cell(ot_by_gene[g]) for g in genes},
        "clinical_activity": {g: clinical_activity_cell(ot_by_gene[g]) for g in genes},
        "rodent": {g: rodent_cell(ot_by_gene[g]) for g in genes},
        "literature": {g: literature_cell(lit_by_gene[g]) for g in genes},
    }

    rankings = rank_genes(cells_by_ring, DEFAULT_WEIGHTS, RANKED_RINGS)
    cells_by_ring["completeness"] = {
        g: value(float(rankings[g].k), f"{rankings[g].k} rings with a real measurement")
        for g in genes
    }
    gene_order = order_for_figure(rankings)

    # --- literature audit for invariant 5 --------------------------------
    echo_checked = ncbi_checked = 0
    echo_mismatches: list[str] = []
    unreachable: list[str] = []
    for g in genes:
        if lit_by_gene[g].error is None:
            ncbi_checked += 1
        q, count, echo_ok, err = epmc_results[genes.index(g)]
        if echo_ok is True:
            echo_checked += 1
        elif echo_ok is False:
            echo_mismatches.append(f"{g}: {err}")
        elif err:
            unreachable.append(f"{g}: {err[:90]}")
    if unreachable and echo_checked == 0:
        warnings.append(
            f"Europe PMC unreachable for all {len(unreachable)} genes, so invariant I5's echo "
            f"half could not be evaluated. The PubTator ring is unaffected."
        )
    lit_audit = {
        "echo_checked": echo_checked, "ncbi_checked": ncbi_checked,
        "echo_mismatches": echo_mismatches, "unreachable": unreachable[:10],
        "ncbi_errors": [],
    }

    # --- render ----------------------------------------------------------
    svg_path = runs_dir / "figure.svg"
    csv_path = runs_dir / "values.csv"
    t = time.time()

    def do_render(failures: list[str]) -> figure.FigureResult:
        return figure.render(
            genes=gene_order,
            cells_by_ring=cells_by_ring,
            rankings=rankings,
            region=region,
            out_svg=svg_path,
            out_png=runs_dir / "figure.png",
            input_count=parsed.input_row_count,
            rendered_count=rendered_count,
            ledger_counts={
                s: n for s, n in
                ((e.status, sum(1 for x in ledger if x.status == e.status)) for e in ledger)
            },
            invariant_failures=failures,
            weights=DEFAULT_WEIGHTS,
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
    )

    banner = invariants.banner_lines() + [
        f"source contract FAILED: {c['check']}" for c in contract_failures
    ]
    if banner:
        # Re-render with the banner. The figure must not look clean when it is not.
        fig_result = do_render(banner)

    _write_csv(csv_path, fig_result.csv_rows, ledger, parsed)

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
            "unstable": rankings[g].unstable,
            "median_rank": rankings[g].median_rank,
            "weighted_score": rankings[g].weighted_score,
            "rank_note": rankings[g].note,
            "pipeline_summary": pipeline_summary(rec),
            "drugs": drug_pipeline_rows(rec),
            "safety_liabilities": rec.safety_liabilities,
            "literature": {
                "pubtator_count": lit_by_gene[g].count,
                "pubtator_query": lit_by_gene[g].query,
                "years": lit_by_gene[g].years,
                "error": lit_by_gene[g].error,
                "europepmc_query": epmc_results[genes.index(g)][0],
                "europepmc_count": epmc_results[genes.index(g)][1],
                "europepmc_error": epmc_results[genes.index(g)][3],
            },
            "chembl": mechanism_summary(chembl_by_gene[g]),
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
