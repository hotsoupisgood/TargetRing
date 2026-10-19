# The static demo — `docs/demo/`

The read-only Crohn's demo (`docs/DECISION-DEMO-CROHNS-2026-10-18.md`,
`docs/DEMO-RUN-ALLOWLIST.md`) as a folder of files. No server, no process, no
port, no API key — because there is nothing running to hold one.

`docs/` is what GitHub Pages serves from `main`; `docs/index.html` redirects to
`docs/demo/`, and `docs/.nojekyll` keeps Pages from re-processing the tree.

**It is not deployed.** Nothing here turns Pages on, and AGENTS.md rule 10's
"nothing public" is unamended beyond the source and a read-only demo. This is
the artefact; publishing it is Puck's to do.

## Regenerate

```sh
.venv/bin/python scripts/export_static_demo.py
```

It starts `uvicorn` on a loopback port with `TARGETRING_DEMO=1` and
`ANTHROPIC_API_KEY` **popped from the child's environment**, crawls every route
`static/app.js` can ask for, and writes the answers under `docs/demo/`
(replacing it). Before it exports anything it asks for the six forbidden routes
and an unlisted run id, and aborts unless all seven are still refused — so a run
of the script is also a check that `app._demo_gate` holds.

It reads `targetring/static/` and `runs/`; it writes only `docs/`.

`--keep-slides` also exports `figure-slide.svg` / `.png` (+1.4 MB per run). They
are off by default: no page references them.

## How the page works without a server

`scripts/static_demo_shim.js` is copied in as `static-demo.js` and injected
ahead of `app.js` in the *exported* copy of `index.html`. `targetring/static/`
is untouched, so the served app's demo behaviour is exactly what
`tests/test_demo.py` enumerates.

The shim does three things:

1. **Rebases.** Reads the page's own directory off `location`, so the site works
   at `/targetring/demo/`, at `/`, or anywhere else. Nothing is configured.
2. **Names the file.** `/api/runs` is a directory on a static host, so the
   export writes `api/runs.json` and the shim maps onto it. Artefacts
   (`figure.png`, `manifest.json`) keep their own names. Absolute `src`/`href`
   in HTML the app builds are rewritten in the `innerHTML` setter, *before* the
   parser starts the request — an observer afterwards leaves one 404 per image
   in the console and then a correct second request.
3. **Refuses, in the server's own words.** `app.DEMO_REFUSAL`, verbatim (a test
   holds the two strings equal), at status 403, in the `{error, message}` shape
   the page already renders. Nothing reaches the console as an error.

## What works

- The landing: four run cards with their `figure.png`, the same list
  `/api/runs` gave.
- Opening a run (`?run=<id>`): its grid, its drop ledger, its invariants, its
  "What this is not" caveats, its evidence surface, and its own exported
  `figure.png` / `figure.svg`.
- Every gene the grid makes clickable opens its **baked drawer**: the GO /
  NO-GO / INSUFFICIENT verdict with its three criteria, the `phenotype
  (Claude)` flag and its per-term MGI models, the LLM evidence summary with its
  model, date and prompt version, the drug pipeline, trials, safety
  liabilities, ChEMBL mechanisms and the Europe PMC count.
- Every download: `values.csv`, `ledger.csv`, `manifest.json`, `caption.txt`,
  the legend as its own image.

## What is stubbed, and what it says instead

| | |
|---|---|
| the disease search, the contrast picker, `/api/analyse` | already hidden by the demo CSS; the routes behind them answer 403 with the refusal sentence |
| the live `/gene/` drawer and the phenotype table button | 403 with the refusal sentence, rendered in the panel — the baked drawer above it already carries the phenotype terms and their models |
| `/web-figure` (the figure redrawn for your window) | 403; the run's exported `figure.png` is what is shown, as it is for any run read from disk |
| re-sorting the grid, dim filters | 403 with its own sentence. The sort is `order.py`'s and the dim rule is §3.2's, both server-side on purpose; a static copy serves the order the run drew its spokes in and says so rather than defining "sorted by logFC" a second time in JS |

## Size

16.1 MB, 102 files, 4 runs, 46 baked drawers. Most of it is the five images per
run (`figure.png`, `figure.svg`, `figure-web.svg`, `legend.png`, `legend.svg`
≈ 3.2 MB each).

## What is published, that somebody should look at before it is

`manifest.json` carries `llm.accounts` — the five spend accounts with their
caps and **dollars spent to date**, folded out of the spend ledger. The ledger
file itself is not exported by any route, and the export strips
`llm.ledger_path` (an absolute home-directory path; the page falls back to the
relative name). The totals are left in, because the "Paid calls" box exists to
be honest about them and a box showing caps with no spend would be a worse
claim than no box. Whether those five numbers belong on a public URL is a
decision for Puck, not for the export script.

Checked, 2026-10-19: no `/Users/` path, no key-shaped string and no
`spend-ledger*` file anywhere under `docs/demo/`
(`tests/test_static_demo.py::test_nothing_published_names_this_machine_or_a_key`).
The four `ledger.csv` files are the **drop** ledger — which genes left the
figure and why — and are the point.

## Verifying a fresh export

Serve `docs/` under a path prefix, the way Pages will:

```sh
mkdir -p /tmp/pages && ln -s "$PWD/docs" /tmp/pages/targetring
python -m http.server 8911 --bind 127.0.0.1 --directory /tmp/pages
open http://127.0.0.1:8911/targetring/demo/
```

Then `.venv/bin/python -m pytest tests/test_static_demo.py` over the tree.
`docs/showcase/06-static-demo.png` is that page: a run's grid with MUC1's
drawer open, taken from the static copy, nothing running behind it.
