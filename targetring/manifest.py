"""The run manifest, written next to every figure.

AGENTS.md: "A morning screenshot that cannot be audited is not a deliverable."
UTC timestamp, git SHA, every source URL and schema version, model id and prompt
hash, and PASS/FAIL per invariant.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from . import sources as sources_mod
from .invariants import InvariantReport


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(config.REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(config.REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return f"{sha}{'-dirty' if dirty else ''}"
    except Exception as e:  # noqa: BLE001
        return f"unavailable: {e}"


SOURCE_URLS: dict[str, str] = {
    "opentargets": config.OPENTARGETS_URL,
    "gtex": f"{config.GTEX_BASE}/expression/medianGeneExpression?datasetId={config.GTEX_DATASET}",
    "gtex_reference": f"{config.GTEX_BASE}/reference/gene",
    "pubtator3": config.PUBTATOR_SEARCH,
    "europepmc": config.EUROPEPMC_SEARCH,
    "chembl": config.CHEMBL_BASE,
    # v2.8: absent since the TDL ring was built in v2.2. AGENTS.md requires
    # "every source URL" in the manifest, and the ring that drives four
    # discrete levels on the figure was the one source the manifest did not
    # name. Found while making the legend's links and the manifest agree --
    # they could not agree about Pharos, because the manifest never mentioned
    # it.
    "pharos": config.PHAROS_GRAPHQL,
    # Stage 1. AGENTS.md requires "every source URL" in the manifest, and these
    # two are now read on a path a figure can be reached from: the disease
    # autocomplete and the contrast picker. The Atlas entry is the per-experiment
    # directory with the accession left as a placeholder, because the URL a run
    # actually fetched is in its `ContrastRef` (`configuration_url`,
    # `analytics_url`) -- naming one experiment here would name the wrong one.
    "atlas_experiment": config.ATLAS_EXPERIMENT_BASE.format(acc="{ACC}"),
    "atlas_catalogue": config.ATLAS_CATALOGUE_URL,
    "ols4_select": config.OLS4_SELECT,
    "ols4_terms": config.OLS4_ONTOLOGIES,
}


@dataclass
class Manifest:
    run_id: str
    timestamp_utc: str
    git_sha: str
    region: str
    input_row_count: int
    rendered_count: int
    ledger_count: int
    gene_order: list[str] = field(default_factory=list)
    # v3.6. The order his table arrived in, which the figure has always used as
    # the tie-break and as the order of the no-value block (PLAN §3.1) but never
    # wrote down. `figure_claims["submitted_order"]` carried it to invariant 7
    # and no further, so it did not survive the run: a `ring:` -ordered figure's
    # artefacts recorded the order it was DRAWN in and nothing else, and the
    # table view could not reproduce the documented tie-break from them. Runs
    # made before this stay readable — `table.load` says which list it is using.
    submitted_order: list[str] = field(default_factory=list)
    sources: dict[str, Any] = field(default_factory=dict)
    schema_versions: dict[str, str] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, str]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    not_attempted: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # v2.5. `weights` and `pareto_completeness` are gone with ranking. What
    # replaces them is the two settings that now shape which genes are on the
    # figure and in what order: without these a run is not reproducible from its
    # artefacts, which is the whole point of writing a manifest beside it.
    ordering: str = ""
    ordering_note: str = ""
    filters: list[dict[str, Any]] = field(default_factory=list)
    filter_counts: dict[str, Any] = field(default_factory=dict)
    # COUNCIL V3 / UX A1. Run conditions are not invariant failures and are
    # recorded separately so the two can never be confused in an audit.
    run_conditions: list[dict[str, str]] = field(default_factory=list)
    # v2.8. Per ring: the documentation URL the legend links to, the endpoint
    # that was called, and the pinned schema or dataset version. Built from the
    # same `sources.RING_SOURCE_DOCS` the figure draws from, so a figure and its
    # manifest cannot disagree about where a number came from. The access date
    # is `timestamp_utc` above -- one clock per run, not a second one here that
    # could drift from it.
    ring_sources: list[dict[str, str]] = field(default_factory=list)
    # Stage 5. `None` on a pasted-table run. `contrast` is `ContrastRef.as_dict()`
    # verbatim -- the whole reference, not a reconstruction of it, so the
    # manifest and the figure cannot come to describe two different contrasts.
    # `selection_accounting` is I9's three-stage count, with `n_selected` now the
    # real number of genes this run carried past the heatmap.
    contrast: dict[str, Any] | None = None
    selection_accounting: dict[str, Any] | None = None

    @property
    def all_invariants_pass(self) -> bool:
        return all(i["outcome"] == "PASS" for i in self.invariants)

    @property
    def any_invariant_failed(self) -> bool:
        """UX A1: the question the red banner answers. Distinct from
        `all_invariants_pass`, which a NOT_EVALUATED result also makes false."""
        return any(i["outcome"] == "FAIL" for i in self.invariants)

    def as_dict(self) -> dict[str, Any]:
        """QA-v2-retest §4. `asdict()` serialises fields and drops properties, so
        the two questions a screenshot's manifest is supposed to answer in one
        field -- did anything FAIL, did everything PASS -- were computable from
        the `invariants` array and absent from the file, while V2-REPORT §3.1
        claimed they were in it. They are written now.

        They stay `@property` as well, so there is exactly one definition of
        each and the file cannot drift from the object.
        """
        return {
            **asdict(self),
            "any_invariant_failed": self.any_invariant_failed,
            "all_invariants_pass": self.all_invariants_pass,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2, default=str))
        return path


# The reviews' explicit "do not attempt tonight" list, carried into every run so
# a screenshot cannot be mistaken for more than it is.
NOT_ATTEMPTED: list[str] = [
    "Measured rate limits under parallel load — unmeasured for every source. "
    "Concurrency is capped at 4 per host and any 429 is a hard run failure (QA §5).",
    "LLM triage gold set — labelling ~20 abstracts is human work. Triage precision "
    "is UNMEASURED (QA S2 item 5).",
    "Any ChEMBL-backed ring — UniProt/ChEMBL resolution is unprobed at scale, so ChEMBL "
    "is drawer evidence only and drives no ring (QA B6, COUNCIL D2).",
    # v2.5: the two ranking entries are gone, because the thing they declined to "
    # claim no longer exists. Nothing here is ranked, scored or weighted.
    "Any ordering of the genes by merit. The spokes are in the order this run states on "
    "the figure and nothing on it is a ranking, a score or a recommendation.",
]


def build(
    *,
    run_id: str,
    region: str,
    input_row_count: int,
    rendered_count: int,
    ledger_count: int,
    gene_order: list[str],
    invariants: InvariantReport,
    submitted_order: list[str] | None = None,
    contracts: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    llm: dict[str, Any],
    timings: dict[str, float],
    sources: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    ordering: str = "",
    ordering_note: str = "",
    filters: list[dict[str, Any]] | None = None,
    filter_counts: dict[str, Any] | None = None,
    run_conditions: list[dict[str, str]] | None = None,
    ring_keys: list[str] | None = None,
    contrast: dict[str, Any] | None = None,
    selection_accounting: dict[str, Any] | None = None,
) -> Manifest:
    return Manifest(
        run_id=run_id,
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        git_sha=git_sha(),
        region=region,
        input_row_count=input_row_count,
        rendered_count=rendered_count,
        ledger_count=ledger_count,
        gene_order=gene_order,
        submitted_order=list(submitted_order if submitted_order is not None
                             else gene_order),
        sources={**SOURCE_URLS, **(sources or {})},
        schema_versions=dict(config.SCHEMA_VERSIONS),
        contracts=contracts,
        invariants=invariants.as_json(),
        llm=llm,
        ledger=ledger,
        timings=timings,
        not_attempted=list(NOT_ATTEMPTED),
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "max_concurrency_per_host": str(config.MAX_CONCURRENCY_PER_HOST),
        },
        warnings=warnings or [],
        ordering=ordering,
        ordering_note=ordering_note,
        filters=filters or [],
        filter_counts=filter_counts or {},
        run_conditions=run_conditions or [],
        ring_sources=sources_mod.as_manifest_rows(list(ring_keys or [])),
        contrast=contrast,
        selection_accounting=selection_accounting,
    )


__all__ = ["Manifest", "build", "git_sha", "SOURCE_URLS", "NOT_ATTEMPTED"]
