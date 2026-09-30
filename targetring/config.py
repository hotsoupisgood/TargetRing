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
# v2.4: `CTGOV_STUDIES` is gone with the per-drug hop. Open Targets' own
# `clinicalReports` rows carry the NCT id, the status, the sponsor and a
# *categorised* stop reason, in the gene query this build already issues, so
# there is nothing left for a second endpoint to fetch. NCT ids still resolve to
# clinicaltrials.gov/study/<id> in the drawer as links, which costs no request.
PHAROS_GRAPHQL = "https://pharos-api.ncats.io/graphql"

# --- Expression Atlas and OLS4, verified live 2026-09-29 --------------------
# The experiments catalogue. 4,562 rows and NO ontology ids anywhere in it: the
# EFO ids live per-assay in `{ACC}.condensed-sdrf.tsv` and the contrast prose
# names in `{ACC}-configuration.xml`, which is why the contrast counts need a
# local index and cannot be served from here (plan V4).
ATLAS_CATALOGUE_URL = "https://www.ebi.ac.uk/gxa/json/experiments"
# Per-experiment files. The brace is filled with an accession.
ATLAS_EXPERIMENT_BASE = (
    "https://ftp.ebi.ac.uk/pub/databases/microarray/data/atlas/experiments/{acc}/")
# `{ACC}.condensed-sdrf.tsv` uses a DOT before `condensed`, unlike every other
# file in the directory, which uses a dash. The `-condensed-sdrf.tsv` form
# answers HTTP 404 with an Apache HTML body (verified 2026-09-29), so a
# dash-built URL fails the content-type check rather than returning nothing --
# which is the good outcome, and is still one character from a wasted run.
ATLAS_CONDENSED_SDRF_SUFFIX = ".condensed-sdrf.tsv"

# OLS4. `/api/select` is used and `/api/search` is NOT, for one reason:
# `select` returns `responseHeader.params.q` and `search` returns a
# `responseHeader` with only `QTime` and `status` in it (both verified live
# 2026-09-29, with and without `echoParams=all`). AGENTS.md rule 4 wants the
# query echoed back and asserted, and only one of the two endpoints offers that.
OLS4_SELECT = "https://www.ebi.ac.uk/ols4/api/select"
OLS4_ONTOLOGIES = "https://www.ebi.ac.uk/ols4/api/ontologies"
# EFO carries the Atlas-era ids (including the obsolete ones) and MONDO carries
# the current disease terms Open Targets uses. Both are searched; which id goes
# to which source is `adapters/ols.DiseaseRef`'s whole job.
OLS4_ONTOLOGY_LIST = "efo,mondo"

# An index older than this is served with a stale flag the UI prints. It is not
# refused: a month-old contrast count is still the count that was measured, and
# refusing would leave the picker with no numbers at all.
ATLAS_INDEX_STALE_DAYS = 30

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
    #
    # v3 (v2.4): the query also asks for `clinicalReports.id`, `trialSponsor{name}`
    # and `trialWhyStopped`, which is everything the retired per-drug
    # ClinicalTrials.gov hop went and fetched separately. A v2 payload carries
    # none of the three, so the activity ring and the drawer's trial panel would
    # read as empty rather than as absent. Bumped so no v2 cache entry is reused.
    #
    # v4 (stage 7): `mousePhenotypes` gains `biologicalModels{id
    # allelicComposition geneticBackground literature}` — the PMIDs behind each
    # MGI-curated phenotype call. Re-verified live 2026-09-30 (plan's V1 was
    # 2026-09-29): BRAF returns 77 mousePhenotypes rows, each with one or more
    # biologicalModels, each carrying a `literature` list of PMID strings. A v3
    # payload has no PMIDs under this key, and the phenotype page would read
    # "no papers" for a gene that MGI has fully curated — the exact failure the
    # v2/v3 bumps above exist to prevent, one field over.
    "opentargets.target": "4",
    "gtex.gene": "1",
    "gtex.median_expression": "1",
    "pubtator.count": "1",
    "chembl.target": "1",
    "chembl.mechanism": "1",
    "europepmc.search": "1",
    "pharos.target": "1",
    # --- stage 1. OLS4 and Expression Atlas -------------------------------
    # `ols.select` is the autocomplete query; `ols.term` is one term's full
    # record, which is where `is_obsolete` and `term_replaced_by` live (the
    # select payload carries neither, checked live 2026-09-29).
    "ols.select": "1",
    "ols.term": "1",
    "atlas.catalogue": "1",
    "atlas.configuration": "1",
    "atlas.condensed_sdrf": "1",
    # The contrast index built from the two above. Its schema version is also in
    # the on-disk index's own `schema_version` field, so an index written by an
    # older parse is refused rather than read with today's field names.
    #
    # v2 (stage 2): each row gains the `organism part` values on both sides. The
    # picker has a tissue column, and a v1 row has no tissue in it -- which would
    # render as an empty cell and read as "this contrast has no tissue" rather
    # than "this index did not record one".
    "atlas.index": "2",
    # Stage 3: fetched only once a contrast is actually drawn (contrast.py
    # module docstring), never by the index build.
    "atlas.analytics": "1",
    "atlas.raw_counts": "1",
    "atlas.analysis_methods": "1",
    # Stage 7. One article, fetched once per distinct PMID a gene's
    # mousePhenotypes rows name, reusing EuropePMCAdapter's rule-4 echo
    # assertion (`fetch_article`). Separate cache source from `europepmc.search`
    # (the PubTator/ring-adjacent count query) — different query shape, same
    # adapter.
    "europepmc.article": "1",
    # The LLM summary, keyed on sha256(rows_json + prompt_hash + model) by the
    # caller (`phenotype.py`) — this schema version is part of that hash's
    # input indirectly, via `prompt_hash`, so a prompt change does not need a
    # bump here. Bumped only if the cached *shape* of a summary changes.
    "llm.phenotype_summary": "1",
}

# v2.4: `CTGOV_MAX_DRUG_HOPS` and `CTGOV_ROWS_PER_BUCKET` are gone with the hop.
# The hop budget existed because a 60-gene table can carry several hundred
# distinct drugs (EGFR alone has 82) and each one cost a request; the trial rows
# now arrive with the gene, so the number of drugs costs nothing and there is no
# budget left to exhaust or to render `no_data` for.

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

# AGENTS.md rule 8 as amended 2026-09-29: two paid calls, two caps, two
# counters, neither borrowing from the other. `LLM_SPEND_CAP_USD` above is the
# triage account's and is deliberately UNCHANGED -- a second feature must not be
# able to move the first feature's ceiling.
PHENOTYPE_LLM_SPEND_CAP_USD = 5.00

# Stage 7. One call per gene, one distinct PMID at a time from Europe PMC.
# Capped and the cap printed on the page (plan §3 stage 7a) -- a heavily
# phenotyped gene can carry far more than 40 PMIDs across its mousePhenotypes
# rows and this is the ceiling on how many of them get an abstract fetch.
PHENOTYPE_MAX_ARTICLES = 40

# Stage 7b. A second, distinct cap, found necessary while building this stage
# and not in the original plan: PLAN.md §7's cost table assumes the LLM sees
# "<=40 rows" of the phenotype table, on the (unstated) assumption that a
# gene's phenotype rows deduplicate down to roughly one per PMID. Measured
# live 2026-09-30 on BRAF, the plan's own fixture: 77 mousePhenotypes rows
# produce 1,800 (phenotype, model, PMID) table rows, and even deduplicating to
# (mp_label, pmid) pairs still leaves 958 -- most of BRAF's phenotypes cite
# most of the same ~20 papers, because a pleiotropic knockout paper reports
# many organ systems at once and MGI attaches its PMID to each one. Sending
# 958 rows to the model would cost roughly $1.10 on the one live call this
# stage is budgeted for, about 24x the plan's own estimate, on the first paid
# call this repo has ever made.
#
# So the LLM input is capped at this many rows (reusing the number
# PHENOTYPE_MAX_ARTICLES already uses, rather than inventing a second
# magnitude), taken in table order. The cap is stated on the summary block
# every time it bites, never silent -- the same discipline as every other cap
# in this repo. The on-screen TABLE is never truncated by this; only what is
# sent to the model is.
PHENOTYPE_SUMMARY_MAX_ROWS = 40

# Stage 7b. Plan §3: "Default model claude-opus-5 ... output_config.effort:
# low ... max_tokens: 1200." Pinned here, not inline in llm.py, for the same
# reason every other pinned constant in this file is a constant: one
# definition, read by the caller and by the manifest.
PHENOTYPE_LLM_MODEL = "claude-opus-5"
PHENOTYPE_LLM_EFFORT = "low"
PHENOTYPE_LLM_MAX_TOKENS = 1200

# The append-only spend ledger lives under `runs/`, not under `cache/`, and that
# is the whole point: `cache/` is deleted routinely (there are five
# `cache.v2*-pre` copies in this repo already) and a spend counter that a cache
# reset zeroes is not a spend counter. One line per call, appended, never
# rewritten.
SPEND_LEDGER_PATH = Path(os.environ.get(
    "TARGETRING_SPEND_LEDGER", RUNS_DIR / "spend-ledger.jsonl"))

# Anthropic first-party price table, USD per million tokens, cached 2026-06-24.
# A pinned constant and therefore in every manifest: if the published prices
# move, the ledger under-counts silently, and the only defence is that a reader
# can see which table this run priced itself against.
#
# A model that is NOT in here cannot be called. `spend.price_usd` raises rather
# than guessing a price, because a guessed price is a spend figure that looks
# real and is not -- `AGENTS.md:17` in the accounting.
# `cache_read` is present only for the model whose cache-read price this table
# actually records. `claude-fable-5-1`'s input and output prices are the ones
# stated in the plan's cost section; its cache-read price was not, so it is
# absent rather than assumed to be a tenth of input, and a fable call that
# reports cache-read tokens is refused for want of a price. An absent price is
# not a price of zero.
LLM_PRICE_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-fable-5-1": {"input": 10.00, "output": 50.00},
}
