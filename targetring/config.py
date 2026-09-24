"""Pinned endpoints, schema versions and limits.

Every constant here is part of the cache key or the run manifest. Changing one
must invalidate the cache, so SCHEMA_VERSIONS is bumped by hand.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("TARGETRING_CACHE", REPO_ROOT / "cache"))
RUNS_DIR = Path(os.environ.get("TARGETRING_RUNS", REPO_ROOT / "runs"))

USER_AGENT = "targetring/1.0 (local research prototype; contact: repo owner)"

# --- endpoints, all verified live 2026-09-23/24 -----------------------------
OPENTARGETS_URL = "https://api.platform.opentargets.org/api/v4/graphql"
GTEX_BASE = "https://gtexportal.org/api/v2"
PUBTATOR_SEARCH = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"

# GTEx dataset is pinned: gtex_v10 returns 0 rows for the v26 gencode ids that
# /reference/gene serves (probed 2026-09-24), so v8 is the only coherent pair.
GTEX_DATASET = "gtex_v8"

# Bumped by hand when a query or a parse changes shape. Part of every cache key
# (QA N4: gene+source alone serves stale values across a schema change).
SCHEMA_VERSIONS = {
    "opentargets.resolve": "1",
    "opentargets.target": "1",
    "gtex.gene": "1",
    "gtex.median_expression": "1",
    "pubtator.count": "1",
    "chembl.target": "1",
    "chembl.mechanism": "1",
    "europepmc.search": "1",
}

# AGENTS.md rule 9. Not a measured limit -- an unmeasured-risk cap.
MAX_CONCURRENCY_PER_HOST = 4
HTTP_TIMEOUT_SECONDS = 45.0

# QA B7: 5,000-row lists are neither readable nor a 60 s run.
MAX_GENES = 500

# AGENTS.md rule 8.
LLM_SPEND_CAP_USD = 5.00
TRIAGE_TOP_N = 10
