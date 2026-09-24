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
# v2.2 item 2. COUNCIL D3 verified this endpoint live and ruled it "the authority
# on live status and sponsor, but it cannot be keyed on a gene" -- `?query.term=BRAF`
# returns 1,045 studies whose top hits are cfDNA biomarker studies with
# interventions like "Blood sampling". So the hop is per *drug*, with the drug
# names taken from Open Targets' rows.
CTGOV_STUDIES = "https://clinicaltrials.gov/api/v2/studies"
PHAROS_GRAPHQL = "https://pharos-api.ncats.io/graphql"

# GTEx dataset is pinned: gtex_v10 returns 0 rows for the v26 gencode ids that
# /reference/gene serves (probed 2026-09-24), so v8 is the only coherent pair.
GTEX_DATASET = "gtex_v8"

# Bumped by hand when a query or a parse changes shape. Part of every cache key
# (QA N4: gene+source alone serves stale values across a schema change).
SCHEMA_VERSIONS = {
    "opentargets.resolve": "1",
    # v2: the pinned TARGET_QUERY now asks for `clinicalReports.diseases`, so the
    # phase ring can report a stage *for the attributed indication* rather than
    # the drug's global maximum (QA-v2-retest §2). A v1 payload carries no
    # per-indication stage and would read as "cannot be established".
    "opentargets.target": "2",
    "gtex.gene": "1",
    "gtex.median_expression": "1",
    "pubtator.count": "1",
    "chembl.target": "1",
    "chembl.mechanism": "1",
    "europepmc.search": "1",
    # v2: one page per drug holding the whole result, instead of three filtered
    # count calls. The cached shape changed with it (rows + completeness).
    "clinicaltrials.studies": "2",
    "pharos.target": "1",
}

# v2.2 item 2. A CT.gov hop is one call per drug per status bucket, and a
# 60-gene table can carry several hundred distinct drugs (EGFR alone has 82).
# A stated budget, so a large submission cannot turn into a thousand-call run:
# genes whose drugs were not all hopped render `no_data` naming the budget,
# never a partial count. Never hit on either fixture (160 and 115 drugs).
CTGOV_MAX_DRUG_HOPS = 400
# Rows kept per drug per bucket for the drawer. The *counts* are exact
# (`countTotal`), so this bounds only how many examples travel.
CTGOV_ROWS_PER_BUCKET = 20

# AGENTS.md rule 9. Not a measured limit -- an unmeasured-risk cap.
MAX_CONCURRENCY_PER_HOST = 4
HTTP_TIMEOUT_SECONDS = 45.0

# UX B1. Was 500, which was neither readable nor reachable: pycirclize's
# `Circos({...}, space=2)` raises "Too large sector space size is set!!" at
# n >= 181 (181 sectors x 2 degrees exceeds 360), so a 200-gene table cost ~13
# minutes of fetching and then a traceback. Long before that it is unreadable --
# at n=120 each spoke is 8 px wide at the outer edge. Validated before any
# network work.
MAX_GENES = 60

# The hard geometric ceiling, for the message that explains the soft one.
CIRCOS_MAX_SECTORS = 180

# AGENTS.md rule 8.
LLM_SPEND_CAP_USD = 5.00
TRIAGE_TOP_N = 10
