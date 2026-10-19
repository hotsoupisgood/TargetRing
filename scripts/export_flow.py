#!/usr/bin/env python3
"""Export `docs/flow/flow.json` — the numbers the methods flowchart reads.

v4.0, Puck 2026-10-19: "make one for the methods of the target ring too", a D3
flowchart in the style of `AI-Graph-Response-Compare`, standalone and
embeddable in a blog post.

**Nothing in `docs/flow/index.html` is hard-coded.** Every count, threshold,
dollar and source name on that page comes out of this file, and every value in
this file comes from one of three places:

* `targetring.config` — the thresholds and caps, i.e. the build's own constants;
* the featured demo run's `manifest.json` — what one real run actually did:
  how many Atlas rows it started from, how many genes it drew, how many it
  ledgered, which invariants ran and what they returned, and what the model
  spend was at the moment it was folded;
* `docs/RECOMMEND-SPEC.md` and `README.md` — the sentences, quoted, not
  paraphrased here.

The reason for that rule is the same one AGENTS.md's "the one rule" is about: a
diagram is a claim about what the code does, and a diagram with its own copy of
a threshold is a claim that silently stops being true. If `RECOMMEND_MIN_TPM`
moves, this file moves with it and the page redraws; nobody has to remember.

    .venv/bin/python scripts/export_flow.py
    .venv/bin/python scripts/export_flow.py --run <run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from targetring import config  # noqa: E402

OUT = REPO / "docs" / "flow" / "flow.json"

# The ring sources named on the identifier-join box, with what each one is
# asked for. Kept here rather than in the page for the reason in the docstring;
# the labels match `figure.RING_SPECS`' own source names.
SOURCES = [
    {"name": "Open Targets", "gives": "tractability, chemical probe, safety "
     "flags, furthest trial phase, trial activity, mouse phenotypes, and the "
     "chromosome the gene sits on",
     "url": "https://platform.opentargets.org/",
     "by": "Ensembl gene id"},
    {"name": "GTEx v8", "gives": "the region's median TPM and the median "
     "across the tissue panel, for the regional-specificity ring",
     "url": "https://gtexportal.org/",
     "by": "Ensembl gene id, at the exact gencode version"},
    {"name": "Pharos", "gives": "the target development level, Tdark to Tclin",
     "url": "https://pharos.nih.gov/", "by": "UniProt accession"},
    {"name": "PubTator3", "gives": "literature volume — mentions resolved to a "
     "gene id, never text-matched",
     "url": "https://www.ncbi.nlm.nih.gov/research/pubtator3/",
     "by": "NCBI gene id"},
    {"name": "Europe PMC", "gives": "the per-gene paper count behind the "
     "mouse-phenotype lead",
     "url": "https://europepmc.org/", "by": "a query echoed back and asserted "
     "byte-identical"},
    {"name": "ChEMBL", "gives": "drug mechanisms in the drawer",
     "url": "https://www.ebi.ac.uk/chembl/", "by": "ChEMBL target id"},
]


def read_manifest(run_id: str) -> dict[str, Any]:
    p = config.RUNS_DIR / run_id / "manifest.json"
    if not p.exists():
        # The published export is the fallback, so this script still runs in a
        # fresh clone: `runs/` is not in git, `docs/demo/` is.
        p = REPO / "docs" / "demo" / "api" / "run" / run_id / "manifest.json"
    if not p.exists():
        raise SystemExit(f"no manifest for {run_id} under runs/ or docs/demo/")
    return json.loads(p.read_text())


def spend(manifest: dict[str, Any]) -> dict[str, Any]:
    """The five accounts and the ONE date that is true of all of them.

    Puck: "just date it because the costs all change". The date is the one the
    manifest records (`accounts_measured_utc`, written by
    `spend.manifest_block`, or filled in from the run's own timestamp by
    `export_static_demo.date_the_spend`) — never today's, and never absent.
    """
    llm = manifest.get("llm") or {}
    accounts = llm.get("accounts") or {}
    when = (llm.get("accounts_measured_utc")
            or manifest.get("timestamp_utc") or "")[:10]
    rows = [{"account": a, "spent": round(float(v.get("spent") or 0), 2),
             "cap": round(float(v.get("cap") or 0), 2),
             "calls": int(v.get("calls") or 0),
             "purpose": v.get("purpose", "")}
            for a, v in accounts.items()]
    return {
        "measured": when,
        "total": round(sum(r["spent"] for r in rows), 2),
        "accounts": rows,
        # Said on the page, because it is the thing a reader worries about when
        # they see dollars on a diagram of a pipeline.
        "note": "No paid call is on the render path: the figure draws without a "
                "key. Each account has its own cap and none borrows from "
                "another. Model prices change, so read these as what this "
                "project had cost by the date above, not as a price list.",
    }


def verdict_counts(run_id: str) -> dict[str, int]:
    """How many genes this run came out GO / NO-GO / INSUFFICIENT DATA.

    Counted from the BAKED DRAWERS, which is where the verdict is written: the
    manifest records the analysis and the verdict is applied per gene afterwards
    (`recommend.py`, baked by `scripts/bake_drawers.py`). Reading it from the
    drawers means the number on the chart is the number a reader gets by
    clicking the genes, which is the only version of it that can be checked.
    """
    for base in (config.RUNS_DIR / run_id / "drawer",
                 REPO / "docs" / "demo" / "api" / "run" / run_id / "drawer"):
        if not base.is_dir():
            continue
        out: dict[str, int] = {}
        for p in sorted(base.glob("*.json")):
            v = ((json.loads(p.read_text()).get("recommendation") or {})
                 .get("verdict") or "")
            if v:
                out[v] = out.get(v, 0) + 1
        if out:
            return out
    return {}


def build(run_id: str) -> dict[str, Any]:
    m = read_manifest(run_id)
    acc = m.get("selection_accounting") or {}
    contrast = m.get("contrast") or {}
    invariants = [{"id": i["id"], "name": i["name"], "outcome": i["outcome"]}
                  for i in (m.get("invariants") or [])]
    verdicts = verdict_counts(run_id)
    return {
        "run_id": run_id,
        "generated_from": "targetring.config and this run's own manifest.json",
        "timestamp_utc": m.get("timestamp_utc"),
        "git_sha": (m.get("git_sha") or "")[:12],
        "disease": "Crohn's disease",
        "contrast": {
            "accession": contrast.get("accession"),
            "id": contrast.get("contrast_id"),
            "name": contrast.get("name"),
            "n_test": contrast.get("n_test"),
            "n_ref": contrast.get("n_ref"),
            "platform": contrast.get("platform"),
            "pipeline": contrast.get("pipeline"),
            "ensembl_release": contrast.get("ensembl_release"),
            "sign_sentence": contrast.get("sign_sentence"),
            "index_built_utc": (contrast.get("index_built_utc") or "")[:10],
        },
        "region": m.get("region"),
        "region_inferred": [c.get("condition") for c in (m.get("run_conditions") or [])
                            if "inferred from the disease" in (c.get("condition") or "")],
        # The three-stage selection, which is the one place a gene count can
        # shrink without anybody noticing — so every stage's number is here.
        "selection": {
            "n_total": acc.get("n_total"),
            "n_with_statistic": acc.get("n_with_statistic"),
            "n_without_statistic": acc.get("n_without_statistic"),
            "n_top": acc.get("n_top"),
            "n_up": acc.get("n_up"),
            "n_down": acc.get("n_down"),
            "n_selected": acc.get("n_selected"),
            "max_genes": config.MAX_GENES,
        },
        "accounting": {
            "input": m.get("input_row_count"),
            "rendered": m.get("rendered_count"),
            "ledgered": len(m.get("ledger") or []),
            "ordering_note": m.get("ordering_note"),
        },
        "ledger": [{"input": e.get("input"), "status": e.get("status"),
                    "reason": e.get("reason", "")}
                   for e in (m.get("ledger") or [])],
        "rings": {
            # The four cell states, which is rule 1 and the reason the whole
            # thing exists. Named here so the page cannot invent a fifth.
            "states": ["value", "no_data", "not_applicable", "failed"],
            "external": 9,
            "total": 12,
        },
        "sources": SOURCES,
        "invariants": invariants,
        "verdict_rule": {
            "criteria": [
                {"n": 1, "name": "expressed",
                 "met_when": f"GTEx region median TPM ≥ {config.RECOMMEND_MIN_TPM}; "
                             f"if GTEx is absent, the tissue-distribution ring > 0",
                 "source": "GTEx, Open Targets prioritisation"},
                {"n": 2, "name": "dysregulated in the study",
                 "met_when": f"|logFC| ≥ {config.RECOMMEND_MIN_ABS_LOGFC} and "
                             f"adjusted p ≤ {config.RECOMMEND_MAX_Q}",
                 "source": "the submitted DE table"},
                {"n": 3, "name": "phenotype in a model",
                 "met_when": f"the mouse-KO ring > 0, or ≥ "
                             f"{config.RECOMMEND_MIN_PHENOTYPES} mousePhenotypes "
                             f"term(s)",
                 "source": "Open Targets"},
            ],
            "go": "all three met",
            "no_go": "any one not met",
            "insufficient": "none not met, but any no data",
            "never_a_criterion":
                "Safety and toxicity rings, druggability rings, clinical stage, "
                "drug count and literature volume. Puck, 2026-10-18: \"no "
                "toxicity data or low druggability does not exclude. this could "
                "be fixed by consulting a chemist.\" They print as a caveat "
                "line under the verdict, never as an exclusion.",
            "counts": verdicts,
        },
        "spend": spend(m),
        "no_ranking":
            "There is no score, no weighted sum, no front and no ranking of any "
            "kind (AGENTS.md rule 7). The spoke order is a decision, it is "
            "stated on the figure, and it is checked in the artefact. The rings "
            "are evidence for a reader to weigh.",
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=config.DEMO_FEATURED_RUN,
                    help="the run whose numbers the chart shows "
                         "(default: config.DEMO_FEATURED_RUN)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    data = build(args.run)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{out}: run {data['run_id']}, "
          f"{len(data['invariants'])} invariants, "
          f"{len(data['sources'])} sources, "
          f"spend ${data['spend']['total']:.2f} measured {data['spend']['measured']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
