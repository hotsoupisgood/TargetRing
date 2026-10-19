# Decision — the public demo is Crohn's disease only

**Decided:** 2026-10-18, Puck. **Status:** settled.

## What was decided

The public read-only demo (`docs/TABLED-PUBLIC-DEMO-2026-10-09.md`, still
tabled — this resolves one of its three open questions, not the tabling
itself) shows Crohn's disease runs only. Not a spread of diseases, not mixed
regions.

## Why

`docs/TABLED-PUBLIC-DEMO-2026-10-09.md` left this open: *"Whether the demo
runs are Crohn's only, or span regions and diseases. A single-disease demo is
easier to caption honestly; a spread shows more."*

A spread shows more range, but a stranger with no walkthrough reads four
figures faster if they are variations on one disease than if they are four
unrelated diseases. Crohn's is also the figure already on the README
(`docs/showcase/01-figure-from-a-disease.png`, contrast `E-GEOD-101794
g2_g1`), so the demo's flagship run and the README's flagship run are the
same artefact — one honest story, not two that could drift apart.

It is also what the repo's run history actually supports: every finished run
built from an Expression Atlas contrast for Crohn's disease resolves to the
same Atlas experiment, `E-GEOD-101794` (checked against the 142 Crohn's rows
in `fixtures/atlas-index/v2/index.json`, spanning 18 Atlas accessions — only
one of which has ever been run end-to-end in this repo). A multi-disease demo
would have meant running new analyses to get coverage, which is exactly the
live-analysis cost the tabled doc sized and rejected for a $6 box.

## What this fixes

- The demo's caption can say one disease, one contrast, honestly, in one
  sentence — "Crohn's disease, Expression Atlas contrast `E-GEOD-101794
  g2_g1`" — instead of a caveat per figure about which disease, which region,
  which contrast each one is.
- The run allowlist (deliverable 2, `.env.example` /
  `docs/DEMO-RUN-ALLOWLIST.md`) is simple to audit: every listed run_id
  resolves to the same contrast identity, checked live against each run's
  `manifest.json` invariant I8 ("contrast identity").

## What this leaves open

- **Figure-only vs the evidence surface and the ledger.** The second open
  question from the tabled doc is still open. This decision picks the
  *disease*, not what's shown about it. Showing the drawer (drug pipeline,
  literature) and the drop ledger is more of the point of the app and more
  surface to get wrong in front of strangers — e.g. the drawer's phenotype
  summary is LLM-generated text, and a demo box must never hold an API key
  (rule 10), so a public drawer would need to serve the *recorded* summary
  from the run's manifest, not regenerate it. That is a scoping decision
  Puck has not made; this doc does not make it for him.
- **The HPA licence question PLAN §9 flagged.** Resolved separately in
  `docs/LICENCES.md` (deliverable 4). Short answer: HPA is not currently on
  the render path (grep of `targetring/*.py` finds it only in HTTP
  error-handling comments, never fetched), so it does not block this
  decision — but if HPA is ever added back, `docs/LICENCES.md` has the terms
  read in advance.
- **Whether any demo deploys at all.** Rule 10 ("nothing public") is
  unamended and absolute. This document and the three deliverables alongside
  it (run allowlist, nginx config, hosting plan) are preparation only — no
  server was started on a public interface, no DNS or hosting account was
  created, and nothing here authorises a deploy. See
  `docs/DEMO-HOSTING-PLAN.md` for exactly what Puck would need to do, and
  say, to change that.

## Verification

Four Crohn's runs were started locally under `TARGETRING_DEMO=1` on
`127.0.0.1:8911` (localhost only, torn down after) with these four as the
allowlist, and each was confirmed via `/api/runs` to report
`"contrast_name": "'Crohn's disease' vs 'non inflammatory bowel disease
control' in 'A1a Paris classification'"` and `any_invariant_failed: false`:

- `20261009T160226Z-805ee9` (the README showcase run)
- `20261009T013311Z-81aef2`
- `20261009T012955Z-26960a`
- `20261003T034753Z-8f54e7`

Full detail, including why these four and not others, is in
`docs/DEMO-RUN-ALLOWLIST.md`.
