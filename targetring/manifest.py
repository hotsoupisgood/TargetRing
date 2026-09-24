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
    sources: dict[str, Any] = field(default_factory=dict)
    schema_versions: dict[str, str] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, str]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    not_attempted: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_invariants_pass(self) -> bool:
        return all(i["outcome"] == "PASS" for i in self.invariants)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, default=str))
        return path


# The reviews' explicit "do not attempt tonight" list, carried into every run so
# a screenshot cannot be mistaken for more than it is.
NOT_ATTEMPTED: list[str] = [
    "Measured rate limits under parallel load — unmeasured for every source. "
    "Concurrency is capped at 4 per host and any 429 is a hard run failure (QA §5).",
    "LLM triage gold set — labelling ~20 abstracts is human work. Triage precision "
    "is UNMEASURED (QA S2 item 5).",
    "Any ChEMBL-backed ring — UniProt/ChEMBL resolution is unprobed at scale, so ChEMBL "
    "is drawer evidence only and drives no ring and no rank (QA B6, COUNCIL D2).",
    "A defensible weighting scheme — the default weights are provisional until a human "
    "signs them off (COUNCIL M3, AGENTS.md).",
    "Any claim that the ranking is biologically meaningful. This figure is internally "
    "consistent and auditable; it is not a validated ranking.",
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
    contracts: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    weights: dict[str, float],
    llm: dict[str, Any],
    timings: dict[str, float],
    sources: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
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
        sources={**SOURCE_URLS, **(sources or {})},
        schema_versions=dict(config.SCHEMA_VERSIONS),
        contracts=contracts,
        invariants=invariants.as_json(),
        llm=llm,
        ledger=ledger,
        weights=weights,
        timings=timings,
        not_attempted=list(NOT_ATTEMPTED),
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "max_concurrency_per_host": str(config.MAX_CONCURRENCY_PER_HOST),
        },
        warnings=warnings or [],
    )


__all__ = ["Manifest", "build", "git_sha", "SOURCE_URLS", "NOT_ATTEMPTED"]
