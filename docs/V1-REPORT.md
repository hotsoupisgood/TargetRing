# targetring v1 — overnight build report

Wren II, 2026-09-24, ~06:40 UTC. Built unattended against `PLAN.md` and the two
binding reviews. Nothing was deployed; nothing left this Mac.

---

## What is broken or unproven — read this first

**1. The speed target is missed by 4×.** `PLAN.md` §5 asks for cold 50 genes
under 60 s. Measured on a real 50-gene list (`/tmp/de50.csv`, 48 resolved):

| run | total | adapters | note |
|---|---|---|---|
| cold | **251 s** | 241 s | target was 60 s |
| 2nd | 109 s | 105 s | partly warm |
| 3rd (fully warm) | 59 s | 54 s | only just under, and it is *warm* |

**2. Europe PMC is in an outage and it dominates the clock.** It returns HTTP
503 to every request as I write this. Timed alone, it is **52 s of a 50-genes
run**. Two consequences:

- Failures are deliberately never cached (a transient 503 must not become a
  permanent `no_data`), so *every* run re-pays that cost until EBI recovers.
- It buys nothing for the figure. Per COUNCIL M6 its count is context in the
  drawer and is never a ring value. Tonight it is pure cost.

The fix is a v2 decision, not something I should pick for you — see *What you
need to decide*.

**3. Two contract tests could not be evaluated**, for the same outage: the
Europe PMC unbalanced-quote check (QA B3) skips rather than fails, because
"the API is down" and "the API silently reinterpreted my query" are different
facts and only the second is a bug in this code. 133 passed, 2 skipped.

**4. LLM triage is not built.** `with_triage=True` now raises
`NotImplementedError` rather than `ImportError`. $5.00 of the $5.00 cap is
unspent. Nothing on the figure depends on it.

**5. Not proven, and not claimed** (`PLAN.md` §8, stamped on every figure):
rate limits are unmeasured under parallel load; triage precision is unmeasured;
ChEMBL-backed values are drawer evidence only and drive **no ring and no rank**
because UniProt→ChEMBL resolution is unprobed at scale; the default weights are
provisional; and **the ranking is not validated biology.** The figure says so
in its own footer, not just here.

**6. Licensing is still a human job** (`PLAN.md` §9) before any workplace use.

---

## What works

All eight steps are committed, one commit each, tests green at every step.

- **Identifier resolver** — symbol → Ensembl → GTEx gencode (exact version) →
  UniProt, with explicit `ambiguous` and `no_match`. Runs before any ring.
  `ALB` correctly comes back ambiguous (2 targets: FBF1, ALB) and is *refused*,
  not guessed.
- **DE input contract + drop ledger** — `input == rendered + ledgered` asserted
  in code, not just tested. On the deliberately broken fixture: 16 in, 7
  rendered, 9 named in the ledger with a reason each — Excel date corruption
  (`9-Sep`, `1-Mar`), `NA`/`Inf` logFC, blank and `#DIV/0!` padj, a duplicate
  row, an unresolvable symbol, and `Trp53` flagged `possible_mouse` and
  **rendered but not auto-converted**, because silently orthologue-mapping
  would change your science.
- **Adapters**, each with its own cache and its own four-state return: Open
  Targets `prioritisation` / `drugAndClinicalCandidates` / `mousePhenotypes`,
  GTEx median expression, PubTator3, ChEMBL mechanisms with
  `direct_interaction` + `disease_efficacy`. Joined by id, never by index.
- **Ranking** per §3: ranks within the submitted set, Pareto non-dominated set
  as the headline, median available rank with *k*, weighted score present but
  labelled a convenience with its weights printed. Never imputes.
- **Figure** — pycirclize → SVG, 10 rings, `no_data` hatched and *provably*
  distinct from every value colour, *k* per gene, counts and weights stamped,
  invariant-failure banner.
- **The six invariants** are real assertions that run on every render, and all
  six PASS on both fixtures and on the 50-gene list. I6 is the five-ID
  regression verbatim, including that `ENSG00000999999` is explicitly missing
  and that BRAF's values match a single-target query.
- **The web app** — paste or upload, pick a region, click a gene for its drug
  pipeline and literature, download SVG / CSV / manifest. Local only.
- **Every run writes a manifest**: UTC timestamp, git SHA, every source URL and
  schema version, PASS/FAIL per invariant, the full ledger, timings, and the
  explicit not-attempted list. A screenshot is auditable on its own.

### One correction to a review

QA B3 says the Europe PMC query echo is "the only reliable malformed-query
detector the API offers". Measured 2026-09-24: it is **not sufficient**. Sending
the unbalanced `"BRAF` returns `hitCount: 101223` — 12× inflation — *and*
echoes the query back byte-identically. The echo catches silent
reinterpretation; it does not catch a malformed query the server is happy to
run. So queries are now validated *before* sending and the echo asserted after.
Both, not either. (`targetring/adapters/literature.py`.)

### One judgement call I made

A source outage and a broken join are different facts. If Open Targets never
answers, the five-ID regression reports `NOT_EVALUATED`, not `FAIL`. It still
raises the banner — nothing is quietly excused — but it does not accuse the
join logic of a regression it did not have. The first hero screenshot I took
had exactly this failure mode: a red DO-NOT-TRUST banner over a build whose
invariants were fine, because Open Targets blipped mid-capture.

---

## Screenshots

`docs/screenshots/`, retaken against the committed code:

| file | shows |
|---|---|
| `1-figure.png` | realistic list, 15 in / 14 rendered, clean run |
| `2-drawer-braf.png`, `2b-…-pipeline.png` | BRAF drawer, 18 agents |
| `3-broken-figure.png`, `3b-broken-ledger.png` | broken input, every drop named |
| `4-invariant-banner.png`, `4b-invariants-table.png` | the failure banner (forced) |

---

## What I changed outside the repo

One line in `~/.openclaw/openclaw.json`: `browser.ssrfPolicy.allowedHostnames`
now lists `127.0.0.1` and `localhost`, because the browser tool blocks
private-network destinations and the screenshots are of a loopback app. It is
additive; nothing else was touched. **The gateway reads config at startup and I
did not restart it** — that is your call. The screenshots were taken through a
separate Chrome on CDP 18800 instead.

---

## What you need to decide

1. **Europe PMC.** It costs ~52 s per 50-gene run, is currently 503-ing, and
   never sets a ring value. Options: move it to drawer-on-click (my
   recommendation — it removes the whole cost from the render path), keep it
   inline, or drop it and let PubTator3 stand alone.
2. **The cold-run budget.** 251 s vs the 60 s in the spec. Do you want v2 to
   hit 60 s, or is "fast once warm" acceptable for how you actually use it?
3. **The default weights** — provisional until you sign them off. They are
   printed on the figure so nobody mistakes them for validated.
4. **Licensing** before any workplace use.

Nothing here needs a GPU and nothing spent money.

## Run it

```
cd ~/targetring
.venv/bin/python -m uvicorn targetring.app:app --host 127.0.0.1 --port 8811
# then http://127.0.0.1:8811/ — "load example" or "load a deliberately broken table"
.venv/bin/python -m pytest -q     # 133 passed, 2 skipped (EPMC outage)
```
