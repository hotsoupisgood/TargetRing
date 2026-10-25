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
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
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
    chromosome_cell,
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
    DERow,
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
from . import phenotype as phenotype_mod
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
from . import sources as sources_mod
from . import spend
from .states import Cell, no_data, value

DEFAULT_REGION = "Lung"

#: The artefacts `run_analysis(defer_exports=True)` leaves for the caller to
#: render after responding. Download-only, every one of them: the page inlines
#: `figure-web.svg` and the manifest attests `figure.svg`, and neither is here.
DEFERRED_ARTEFACTS: tuple[str, ...] = (
    "figure-slide.svg", "figure-slide.png", "legend.svg", "legend.png",
    "caption.txt",
)

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


def _ledger_reason_for(res: Any, contrast: Any) -> str:
    """AGENTS.md rule 5, Atlas's own version: an id Open Targets does not
    return is Ensembl 95 not being in the current release, never "absence".
    Plain `ledger_reason(res)` is generic (it also serves the paste path,
    which has no Ensembl-95 fact to state); this adds the one sentence that is
    only true when the id came from Atlas."""
    base = ledger_reason(res)
    if contrast is None or res.status not in ("no_match", "ambiguous"):
        return base
    return (f"{base} Ensembl release {contrast.ensembl_release or '95'} "
           f"(Atlas) id not in the current Open Targets release. Not in the "
           f"current release, not evidence of absence.")


def from_contrast(ref: Any, rows: Sequence[Any]) -> ParsedInput:
    """Stage 5's internal handoff: a `ParsedInput` built server-side from
    already-fetched Atlas analytics rows, never from a textarea (plan §1
    item 12, "provenance evaporates at the paste boundary").

    `rows` is the client's selection -- a subset of the top-N table stage 3/4
    already showed -- each an `AnalyticsRow` (`ensembl_id`, `log2fc`,
    `pvalue`). The symbol column carries the **Ensembl id**, not a resolved
    symbol: `parse_de_table`'s own `ENSEMBL_RE` branch in `resolve.py`
    recognises it and sends it through `Resolver.resolve_all` exactly as a
    pasted Ensembl id would be, which is what makes this the same rule-5 path
    rather than a second one. Column names are the two Atlas fields this
    contrast's `-analytics.tsv` actually carries.

    Selecting nothing is a refusal (stage 4), so an empty `rows` raises here
    rather than reaching `run_analysis` and failing later with a vaguer error.
    """
    if not rows:
        raise ValueError(
            "no genes were selected for this contrast; selecting nothing is a "
            "refusal, not an empty figure")
    return ParsedInput(
        rows=[DERow(i, r.ensembl_id, r.log2fc, r.pvalue)
             for i, r in enumerate(rows, start=1)],
        input_row_count=len(rows),
        columns={"symbol": "Ensembl id (Atlas, Ensembl 95)",
                "logfc": f"{ref.contrast_id}.log2foldchange",
                "padj": f"{ref.contrast_id}.p-value"},
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
    # v3.1 / UX v3 A2. The page draws the twelve scales as HTML now, at a size
    # a browser can draw legibly, so `/api/run/{id}/scales` needs the cells the
    # ring norms are computed from. Kept rather than recomputed: a panel whose
    # ranges came from a second pass over the data could disagree with the
    # figure beside it, which is the failure mode a detachable legend has and
    # an attached one should not.
    cells_by_ring: dict[str, Any] = field(default_factory=dict)
    # v3.2 / UX v3 round 2 A4. The caveat clauses the app render stopped drawing
    # under its own circle so that the circle would fit one 1440 x 900 viewport.
    # Taken from the web render's own `FigureResult` rather than rebuilt here, for
    # the same reason `cells_by_ring` is: one of these sentences states this run's
    # own spoke angles, and a second implementation of it could describe a
    # geometry the figure does not have (COUNCIL V5).
    web_caveats: list[str] = field(default_factory=list)
    #: Set only when `defer_exports=True`. The caller runs it AFTER responding;
    #: until it has, the five download-only artefacts do not exist. `None` on
    #: the eager path, where they were written before this object was built.
    finish_exports: Any = None
    #: The files `finish_exports` is responsible for, so a route can tell a
    #: deferred artefact from one that was never going to exist.
    deferred_artefacts: tuple[str, ...] = ()
    # v3.5. The canvas the app render was actually drawn on, which is a function
    # of the window that asked for it rather than one constant, and therefore not
    # something the page or a later check may assume. It also carries the answer
    # to the only question this feature exists to answer:
    # `web_layout.wedge_labels_in_svg` is whether this window earned the twelve
    # ring names, and it was decided by rendering them and keeping the render only
    # if the geometry assertions accepted it.
    web_layout: figure.Layout = figure.WEB
    # The window that canvas was sized for, as `(panel_px, viewport_px)`, kept
    # beside it because every as-seen number -- the circle in CSS px, the type in
    # CSS px -- is a property of the pair and not of the canvas alone.
    web_window: tuple[float, float] = (figure.WEB_REFERENCE_PANEL_PX,
                                       figure.WEB_REFERENCE_VIEWPORT_PX)
    # v3.5. Re-draw the app render for a window of a different size, for the same
    # run: `(panel_px, viewport_px) -> (Path, web_caveats, Layout)`.
    #
    # A closure over the call that drew it, rather than a second call site with
    # the twenty arguments copied: `assert_renders_agree` is what makes "the
    # export and the app render may differ" safe, and a hand-copied argument list
    # is exactly how the two would come to differ in something other than
    # furniture. Not serialisable, so it is absent from the manifest and from
    # `_summarise` -- it is a live handle on a run that is still in memory.
    rerender_for_window: Any = None


async def run_analysis(
    *,
    table: bytes | str = b"",
    region: str = DEFAULT_REGION,
    # Set when this region was INFERRED from the disease rather than chosen
    # (`region_llm`). The figure must say so: "enriched in ileum" and "enriched
    # in whole blood" are different statements about a gene, and a reader
    # holding a screenshot cannot see that a dropdown was filled in for them.
    region_inferred: bool = False,
    region_reason: str = "",
    # Return the slide, the legend and the caption as a callable for the caller
    # to run AFTER it has responded, instead of rendering them inline.
    #
    # Measured: the figure the page shows and everything the manifest attests
    # are done ~8s in; the remaining ~19s is four image files and a caption
    # nobody has asked for yet. Deferring moves WHEN that work happens and
    # nothing else -- every file is still rendered, still checked by
    # `assert_artefacts_are_their_own_layout`, and the manifest is still
    # written once with everything it attests already validated.
    defer_exports: bool = False,
    run_id: str | None = None,
    cache: Cache | None = None,
    runs_dir: Path | None = None,
    run_regression: bool = True,
    with_triage: bool = False,
    # Called at each stage boundary with (key, human label). Optional and a
    # no-op by default, so every existing caller is unchanged.
    #
    # The STAGES ARE REAL EVENTS, not a timer: each fires when the work before
    # it finished. That matters because the shares are wildly uneven — measured
    # on a warm 26.9s run, exports is 19.3s (72%) and everything that touches a
    # source is 0.8s — so an evenly-paced bar would be a lie about where the
    # time goes, and this build does not ship those.
    on_progress: "Any | None" = None,
    ordering: str | None = None,
    filters: Any = None,
    # v3.5. The window the figure is going to be read in, measured by the browser
    # and handed over (`app.js`). The defaults are the 1440 x 900 laptop round 2
    # A4 measured, so a caller that says nothing -- a test, a script, `make qa` --
    # gets exactly the canvas v3.4 drew.
    panel_px: float = figure.WEB_REFERENCE_PANEL_PX,
    viewport_px: float = figure.WEB_REFERENCE_VIEWPORT_PX,
    # Stage 5. When given, `table` is ignored entirely and this is used as the
    # parsed input instead -- the Atlas-sourced entry point, built server-side
    # by `from_contrast()`, never a textarea round trip. `contrast` and
    # `selection_accounting` travel alongside it into the invariants (I8/I9)
    # and the manifest; both are `None` on every paste-path run, which is what
    # keeps that path's seven-invariant manifest shape unchanged.
    parsed_input: ParsedInput | None = None,
    contrast: Any = None,
    selection_accounting: Any = None,
) -> RunResult:
    t0 = time.time()
    run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    runs_dir = Path(runs_dir or RUNS_DIR) / run_id
    cache = cache or Cache()
    timings: dict[str, float] = {}
    warnings: list[str] = []

    parsed: ParsedInput = parsed_input if parsed_input is not None else parse_de_table(table)
    # Before any network work: a filter naming a ring this build does not have is
    # a mistake the user can fix in a second, and finding out after a 27 s cold
    # run is not a kindness.
    active_filters: list[Filter] = parse_filters(filters, FILTERABLE_RINGS)

    def _say(key: str, label: str) -> None:
        """Announce a finished stage. Never allowed to break the run: a
        progress channel that can fail the analysis it is reporting on is worse
        than no progress channel."""
        if on_progress is None:
            return
        try:
            on_progress(key, label)
        except Exception:  # noqa: BLE001
            pass

    _say("start", "Starting")

    async with Http() as http:
        t = time.time()
        contracts = await check_contracts(http)
        timings["contracts_s"] = round(time.time() - t, 2)
        _say("contracts", "Checked the source contracts")
        contract_failures = [c for c in contracts if not c["ok"]]
        for c in contract_failures:
            warnings.append(f"source contract FAILED. {c['check']}: {c['detail'][:200]}")

        # --- resolve, before any ring -----------------------------------
        t = time.time()
        resolver = Resolver(http, cache)
        resolutions = await resolver.resolve_all([r.symbol for r in parsed.rows])
        timings["resolve_s"] = round(time.time() - t, 2)
        _say("resolve", "Resolved the gene identifiers")

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
                    _ledger_reason_for(res, contrast), row_number=row.row_number,
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
        _say("adapters", "Fetched every ring from its source")

        # --- the five-ID regression test, against the same live API -------
        regression = None
        if run_regression:
            t = time.time()
            regression = await five_id_regression(http, cache)
            timings["regression_s"] = round(time.time() - t, 2)
            _say("regression", "Ran the regression check")

    # --- assemble cells --------------------------------------------------
    genes = [d for d, _, _ in live]
    by_gene = {d: (row, res) for d, row, res in live}
    ot_by_gene: dict[str, OTRecord] = {d: ot_records[res.ensembl_id] for d, _, res in live}
    gtex_by_gene: dict[str, GTExRecord] = dict(zip(genes, gtex_records))
    lit_by_gene: dict[str, LiteratureRecord] = dict(zip(genes, lit_records))
    pharos_by_gene: dict[str, PharosRecord] = {g: pharos_records[g] for g in genes}
    drug_names_by_gene = {g: ot_by_gene[g].drug_names() for g in genes}
    # v4.0. Deliberately NOT in `cells_by_ring`: that dict is what k is counted
    # over, what the invariants pivot and what `values.csv` is written from, and
    # a chromosome is a label on the spoke rather than a measurement on a scale.
    # Its own dict, with the same four states.
    chromosome_by_gene = {g: chromosome_cell(ot_by_gene[g]) for g in genes}

    import math

    cells_by_ring: dict[str, dict[str, Cell]] = {
        "logfc": {g: value(by_gene[g][0].logfc,
                           f"log2 fold change {by_gene[g][0].logfc:g} "
                           f"(submitted)")
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
                 # v4.3: "8/9", the one printed form for k (review of
                 # 2026-10-20, item 5). This read "8 of 9 external rings" in
                 # the drawer beside a grid cell reading "8/9".
                 f"k = {stats[g].k}/{EXTERNAL_RING_COUNT} external rings with a "
                 f"real measurement (your own log2 fold change and "
                 f"adjusted p are not counted)")
        for g in genes
    }
    ordering = normalise_ordering(ordering, cells_by_ring)
    # Rings 1 and 2 are this run's OWN DE input, so what they are called and
    # where they are said to come from is a property of the run, not a
    # constant. On a contrast run `RING_SPECS`'s "logFC (yours)" / "submitted
    # DE table" is false, and it was reaching the figure, the caption, the
    # manifest, the Table tab, the export and the web scales panel together.
    # Resolved once, here, and handed to each of them.
    de_source = (
        sources_mod.DESource.from_contrast(
            contrast.accession, contrast.contrast_id,
            contrast.ensembl_release, contrast.pipeline)
        if contrast is not None else sources_mod.DESource.pasted())
    ring_specs = figure.ring_specs_for(de_source)
    ring_labels = {s.key: s.label for s in ring_specs}
    ring_key = order_mod.is_ring_ordering(ordering)
    # `table_phrase_short`, not `table_phrase`: this sentence is drawn in the
    # circos's centre hole, and the full phrase pushed the centre stack past
    # `CENTRE_MIN_SCALE`, which aborted every contrast render on the default
    # ordering. See `sources.DESource.table_phrase_short`.
    ordering_note = ordering_label(ordering, ring_labels.get(ring_key or ""),
                                   de_source.table_phrase_short)
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
    # FIRST, beside the small-n caveat, and for the same reason: this one says
    # the reader did not choose an input that one ring is entirely computed
    # against. `_draw_conditions` draws `conditions[:6]`, so it must not be
    # pushed off the block by a source outage.
    if region_inferred:
        conditions.append((
            f"region {region} was inferred from the disease, not chosen",
            (f"the regional-specificity ring only: every other ring is "
             f"unaffected. {region_reason} Change the region in the form and "
             f"re-run if this is the wrong tissue.").strip()))
    # Panel S1, the half of it that was still open. `heatmap.py`'s own
    # docstring stated the scope -- "a mandatory string on the heatmap ... and
    # it becomes a `run_condition` on the circos and in the manifest" -- and
    # only the heatmap half was wired: `small_n_caveat` was referenced in
    # `heatmap.py` and `invariants.py` and nowhere in this module, `figure.py`
    # or `manifest.py`. So the heatmap of a 3-sample contrast carried an amber
    # banner while the circos built from the same 3 samples rendered clean,
    # and the circos is the headline output. A reader who went from the picker
    # straight to "analyse" never saw it.
    #
    # FIRST in the list on purpose: `figure._draw_conditions` draws
    # `conditions[:6]`, so a run that also lost Pharos and overspent on ChEMBL
    # must not be able to push the one condition about the trustworthiness of
    # every number on the figure off the bottom of the block.
    #
    # Amber and not the red banner, and `figure._draw_banner`'s own reasoning
    # is why: nothing here failed a check. The numbers are Atlas's own and are
    # correct -- what is wrong is the reading. Rendering a condition of the
    # data identically to a violated invariant is how the red banner stops
    # working the day it is real.
    if contrast is not None and contrast.small_n_headline:
        conditions.append((contrast.small_n_headline, contrast.small_n_affects))
    if unreachable:
        conditions.append((
            "the Europe PMC integrity probe did not answer",
            "it sets no ring value; the literature ring is "
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
            "those cells are hatched, never zeroed; Pharos covers 20,412 human genes "
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
            "a threshold never drops a gene for being unmeasured; they are "
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

    # Stage 5 / I8. Whole sentences, built once from the `ContrastRef` so the
    # figure cannot reword the sign a different way than the manifest states
    # it. Empty on a paste-path run -- `contrast is None` there.
    contrast_notes: list[str] = []
    if contrast is not None:
        contrast_notes = [
            f"{contrast.name} · {contrast.accession} · {contrast.contrast_id}",
            contrast.sign_sentence,
        ]
        if selection_accounting is not None:
            # I9: the same three-stage count the manifest carries, printed on
            # the artefact itself so a screenshot answers "selected from how
            # many" without the manifest open beside it.
            contrast_notes.append(selection_accounting.selection_line())

    def _render_once(failures: list[str], conds: list[str] = [],
                     layout: "str | figure.Layout" = "audit", svg: Path | None = None,
                     png: Path | None = None, write_png: bool = True,
                     invariant_summary: str = "") -> figure.FigureResult:
        return figure.render(
            genes=gene_order,
            cells_by_ring=cells_by_ring,
            stats=stats,
            region=region,
            out_svg=svg or svg_path,
            # v3.0: `write_png=False` is a THIRD state and it has to be, because
            # `png=None` already means "the default, figure.png". The web render
            # passed None meaning "no PNG" and silently overwrote the export's
            # PNG with the lean render -- `figure.svg` was the export and
            # `figure.png` was not, and `assert_renders_agree` compared the SVGs
            # so nothing objected. `assert_artefacts_are_their_own_layout`
            # exists because of this bug.
            out_png=(None if not write_png
                     else png if png is not None else runs_dir / "figure.png"),
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
            # v2.9. The resolver already has these; the figure only needs them
            # for `values.csv` and for the footer sentence that says where they
            # are. They are deliberately NOT on the rim -- see `_ID_NOTE`.
            gene_ids={g: by_gene[g][1].ensembl_id for g in gene_order},
            # v4.0: on the rim, next to the symbol. Four states; only a value
            # draws a suffix, and the figure states that rule itself.
            gene_chromosomes={g: chromosome_by_gene[g] for g in gene_order},
            invariant_summary=invariant_summary,
            contrast_notes=contrast_notes,
            de_source=de_source,
        )

    def do_render(failures: list[str], conds: list[str] = [],
                  layout: "str | figure.Layout" = "audit", **kw) -> figure.FigureResult:
        """`_render_once`, with the chromosome suffix offered to the geometry.

        v4.0. A spoke label runs radially, so whether ` · 22` fits beside the
        longest gene name is a property of the canvas, not of the run. The
        suffix is drawn and kept only if every geometry assertion accepts it;
        where one refuses, the rim loses it and the figure says where the
        chromosome is instead. The app render goes through
        `render_naming_the_rings_if_they_fit`, which does the same retry itself,
        so it calls `_render_once` directly rather than nesting two of them.
        """
        lay = layout if isinstance(layout, figure.Layout) else figure.LAYOUTS[layout]
        result, _ = figure.render_with_the_chromosomes_if_they_fit(
            lambda candidate: _render_once(failures, conds, layout=candidate, **kw), lay)
        return result

    fig_result = do_render([])
    timings["render_s"] = round(time.time() - t, 2)
    _say("render", "Drew the circle")

    _say("checking", "Checking the invariants")
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
        contrast=contrast,
        selection_accounting=selection_accounting,
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
    # v3.0. The export always re-renders, where it used to re-render only when
    # something was wrong. What it gains is `invariant_summary`: a downloaded
    # PNG could always SHOUT a failure in red and had no way to say a pass, so
    # on paper a clean run and an unchecked one looked identical. The second
    # audit render costs ~1.0 s on a 50-gene run against a 60 s budget.
    inv_summary = invariants.summary_line()
    t_exports = time.time()
    fig_result = do_render(banner, conds, invariant_summary=inv_summary)

    # v3.0, Puck: "download figure and the online figure can be different."
    # The app inlines THIS one. Same cells, no footer block and no dropped-gene
    # list in the hole, because the page carries both in its own furniture and
    # the 1.9 in they cost goes to the circle instead.
    #
    # v3.5: on the canvas this window has room for, with the twelve ring names
    # back inside the 26° gap if -- and only if -- the geometry assertions accept
    # them there at a size a reader can read. Puck: *"the names should appear
    # automatically when the viewport allows a circle big enough to hold them
    # honestly, and stay off when it does not."*
    web_svg = runs_dir / "figure-web.svg"

    def draw_the_app_render(panel: float, window_h: float):
        """One call site for the app render, used by the run and again by a resize.

        Returns `(FigureResult, Layout)`. `assert_renders_agree` is inside it on
        purpose: a re-drawn app figure is a new file, and the claim it has to keep
        is the same claim -- every cell identical to the export's, whatever the
        canvas. That is what makes "the download and the online figure can be
        different" safe to have said yes to.
        """
        result, lay = figure.render_naming_the_rings_if_they_fit(
            lambda candidate: _render_once(banner, conds, layout=candidate, svg=web_svg,
                                           write_png=False,
                                           invariant_summary=inv_summary),
            panel_px=panel, viewport_px=window_h)
        figure.assert_renders_agree(svg_path, web_svg)
        return result, lay

    _say("audit_render", "Rendered the audit figure")
    web_result, web_layout = draw_the_app_render(panel_px, viewport_px)
    _say("web_render", "Rendered the figure for the page")

    def _mark(state: str, **extra: Any) -> None:
        """Write `exports.json`, the only record of how the deferral went.

        Written BEFORE the work starts, not after: a process killed mid-render
        cannot write its own epitaph, so "died" has to be inferred from a
        `running` marker whose pid is gone. The pid is recorded for exactly
        that — a timeout would call a slow run dead, which is a guess, and this
        is a fact.
        """
        import json as _json
        import os as _os
        (runs_dir / "exports.json").write_text(_json.dumps({
            "state": state, "pid": _os.getpid(),
            "artefacts": list(DEFERRED_ARTEFACTS),
            "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **extra}, indent=2) + "\n")

    def finish_exports() -> None:
        """The five download-only artefacts, and the check that covers them.

        Kept together because they travel together: the slide render produces
        the caption, and `assert_artefacts_are_their_own_layout` cannot pass
        judgement on a directory that is still missing half its files. It runs
        HERE rather than on the critical path so that it still sees everything
        it was written to see -- the bug it earned (one render writing over
        another's PNG, both SVGs fine, nothing noticing) is exactly the kind a
        partial check would miss.
        """
        # UX A4: the slide render. Same data, 16:9, type that survives
        # projection, with the ring key and the caveats moved into the caption
        # beside it. The audit render above keeps every stamp and stays the
        # morning artefact.
        slide = do_render(banner, conds, layout="slide",
                          svg=runs_dir / "figure-slide.svg",
                          png=runs_dir / "figure-slide.png",
                          invariant_summary=inv_summary)
        _say("slide", "Rendered the 16:9 slide")

        # v3.0, Puck: "we could have the download download legend as a separate
        # image" -- circle on one slide, legend on another. The run id is on
        # both, large, because a legend paired with the wrong run misreads every
        # cell on the nine rings whose range moves with the submitted set.
        figure.render_legend(
            cells_by_ring=cells_by_ring, region=region,
            out_svg=runs_dir / "legend.svg", out_png=runs_dir / "legend.png",
            run_id=f"run {run_id}",
            provenance=f"run {run_id} · {manifest_mod.git_sha()[:12]}",
            ordering_note=ordering_note, filter_notes=filter_notes,
            input_count=parsed.input_row_count, rendered_count=rendered_count,
            gene_flags=severe_liability_flags, de_source=de_source)
        _say("legend", "Rendered the legend")

        # Seven files, four layouts, one data set. This says each file is the
        # render its name promises -- see the function for the bug that earned
        # it.
        figure.assert_artefacts_are_their_own_layout(runs_dir, web=web_layout)
        # v2.7: the caption is the SLIDE's caption, not the audit's.
        (runs_dir / "caption.txt").write_text(slide.caption + "\n")

    def finish_exports_tracked() -> None:
        """`finish_exports` with its state on disk either way.

        A failure here is NOT re-raised past the marker: the figure and the
        manifest were validated and served before this started, and crashing a
        background task after the fact changes nothing the reader can see. What
        it must do is leave a record, so the download route can say why a file
        is absent instead of 404ing as though it never existed.
        """
        _mark("running")
        try:
            finish_exports()
        except Exception as e:  # noqa: BLE001
            _mark("failed", error=f"{type(e).__name__}: {e}")
            raise
        _mark("done")

    if not defer_exports:
        finish_exports()
    # v3.0 renders four layouts where v2.9 rendered two, and `render_s` above
    # only ever measured the first pass. Timed separately rather than folded in,
    # so the cost of the new artefacts is visible in every manifest instead of
    # being discovered later as "the run got slower".
    timings["exports_s"] = round(time.time() - t_exports, 2)
    _say("exports", "Wrote the downloads")
    # v2.7: the caption is the SLIDE's caption, not the audit's. It always was
    # the slide's artefact -- "everything the slide render drops, as text to
    # paste underneath" -- and until now the two were word for word identical,
    # so taking the audit's cost nothing. They are not identical any more: the
    # audit reserves a 26 degree gap for its ring names and the slide does not,
    # and each render's caption states its own geometry. The audit's sentence
    # pasted under the slide is COUNCIL V5 in the one file that exists to be
    # read beside the figure.
    # (caption.txt is written by `finish_exports`, with the slide it comes from)

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
            # v4.0. The same four-state reading the rim's suffix was drawn
            # from, with its sentence -- so a gene whose rim carries no suffix
            # says here which of the three non-values it was.
            "chromosome": chromosome_by_gene[g].as_dict(),
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
                p.get("modelPhenotypeLabel")
                for p in rec.mouse_phenotypes[:phenotype_mod.DRAWER_TERMS_LIMIT]
            ],
            # Which model each term came from (Puck, 2026-10-18), over the same
            # records as the flat label list above -- which stays, unchanged,
            # for everything that reads it (`recommend.facts_from_drawer`,
            # `phenotype_llm.build_facts`).
            "phenotype_terms": phenotype_mod.terms_with_models(
                rec, phenotype_mod.DRAWER_TERMS_LIMIT),
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

    # Stage 0. This block used to be the literal `{"attempted": False,
    # "spend_usd": 0.0}` -- a zero that read like a measurement and was not one,
    # sitting under a $5 cap with no counter behind it. It is folded out of the
    # append-only `runs/spend-ledger.jsonl` now, and it names BOTH accounts
    # whether or not this run touched either: rule 8's promise is that the two do
    # not borrow from each other, and that is a claim about two numbers.
    #
    # Nothing on the render path makes a paid call, so `spent` here is a reading
    # rather than a result of this run.
    llm_info: dict[str, Any] = spend.default_ledger().manifest_block(
        attempted=with_triage,
        note=("no paid call is on the render path; both accounts are folded from "
              "the append-only spend ledger, not assumed to be zero"),
    )
    if with_triage:
        # LLM triage is not built in v1. Fail loudly rather than ImportError --
        # a caller asking for triage must not get a figure that silently has none.
        raise NotImplementedError(
            "LLM triage is not implemented in v1. AGENTS.md rule 8 caps it at $5 and "
            f"limits it to the top 10 genes; ${LLM_SPEND_CAP_USD:.2f} is the cap and "
            f"{llm_info['accounts']['triage']['spent']:.2f} has been spent. "
            "Nothing on the current figure depends on it."
        )

    _say("done", "Done")
    timings["total_s"] = round(time.time() - t0, 2)

    man = manifest_mod.build(
        run_id=run_id, region=region,
        input_row_count=parsed.input_row_count,
        rendered_count=rendered_count, ledger_count=len(dropped_entries(ledger)),
        gene_order=gene_order, invariants=invariants, contracts=contracts,
        # v3.6. The same list invariant 7 re-derives the spoke order from, now
        # written beside the figure so the table view can honour the documented
        # tie-break on a run that was not drawn in submitted order.
        submitted_order=genes,
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
        ring_keys=[s.key for s in ring_specs],
        de_source=de_source,
        contrast=contrast.as_dict() if contrast is not None else None,
        selection_accounting=(asdict(selection_accounting)
                              if selection_accounting is not None else None),
    )
    manifest_path = man.write(runs_dir / "manifest.json")

    return RunResult(
        finish_exports=finish_exports_tracked if defer_exports else None,
        deferred_artefacts=(DEFERRED_ARTEFACTS if defer_exports else ()),
        run_id=run_id, region=region, svg_path=svg_path, csv_path=csv_path,
        manifest_path=manifest_path, manifest=man, invariants=invariants,
        gene_order=gene_order, drawers=drawers, ledger=ledger, warnings=warnings,
        ordering=ordering, active_filters=filters_mod.as_dicts(active_filters),
        cells_by_ring=cells_by_ring, web_caveats=web_result.web_caveats,
        web_layout=web_layout, web_window=(panel_px, viewport_px),
        rerender_for_window=draw_the_app_render,
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
        fh.write(f"\n# SPOKE ORDER: {ordering_note}\n")
        fh.write("\n# FILTERS ACTIVE ON THIS FIGURE: it is not the whole submitted set\n")
        fw = csv.writer(fh)
        fw.writerow(["ring", "comparison", "threshold", "drop_no_data", "expression"])
        for f in active_filters:
            fw.writerow([f.ring, f.symbol, f"{f.threshold:g}", f.drop_no_data,
                         f"{f.ring} {f.symbol} {f.threshold:g}"])
        if not active_filters:
            fw.writerow(["", "", "", "", "none"])
        if kept_unevaluated:
            fh.write("\n# KEPT, NOT FILTERED: no data on a filtered ring, so the "
                     "threshold could not be evaluated (marked ~ on the figure)\n")
            uw = csv.writer(fh)
            uw.writerow(["gene", "rings_not_evaluated"])
            for g, rings_unfiltered in sorted(kept_unevaluated.items()):
                uw.writerow([g, " ".join(rings_unfiltered)])
        fh.write("\n# DROP LEDGER: every input row is above or below this line\n")
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
