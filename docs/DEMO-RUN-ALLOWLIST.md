# The Crohn's demo run allowlist

Four finished runs, all Crohn's disease, all the same Expression Atlas
contrast, all drawn in the terminal ileum. Picked for `TARGETRING_DEMO_RUNS`
per `docs/DECISION-DEMO-CROHNS-2026-10-18.md`. All four are on disk under
`runs/<id>/` with baked drawers, verdicts and summaries — this doc does not
add, delete or modify any run.

## 2026-10-18 (later): re-run in the right tissue

A geneticist's walkthrough of the previous four (`20261009T160226Z-805ee9`,
`20261009T013311Z-81aef2`, `20261009T012955Z-26960a`,
`20261003T034753Z-8f54e7`) found every gene judged "expressed in Lung": the
runs had been submitted with the analyse form's silent default region, while
the region chooser's cached answer for Crohn disease is
`Small_Intestine_Terminal_Ileum`. The four were re-run through the normal
`/api/analyse` contrast path with **no region posted**, so the server used the
disease's cached chooser answer and stamped the figure's run condition
"region Small_Intestine_Terminal_Ileum was inferred from the disease, not
chosen" with the chooser's reason (the route now refuses an empty region when
no answer exists — see `docs/RECOMMEND-SPEC.md`, "Display aids added after the
geneticist walkthrough"). Same contrast, same gene sets, same orderings, new
run ids. The old four stay on disk untouched and are no longer listed.

## How the contrast was found

`fixtures/atlas-index/v2/index.json` has 142 Crohn's-disease contrast rows
across 18 Expression Atlas accessions. Grepping every `runs/*/manifest.json`
for those 18 accession strings found only one that has ever been run
end-to-end in this repo: `E-GEOD-101794`, contrast `g2_g1` — *"Crohn's
disease" vs "non inflammatory bowel disease control" in "A1a Paris
classification"* (61 vs 13 samples; the autopick decision's own worked
example, `docs/DECISION-AUTOPICK-2026-10-09.md`). All four below passed every
invariant (`any_invariant_failed: false`) with no QA concerns.

## Verification performed

Local server, `TARGETRING_DEMO=1`, `127.0.0.1:8924` (localhost only; no
public interface; process killed after). For each run: `GET
/api/run/{id}/manifest.json`, `figure.png`, `ledger.csv`, `values.csv` and
`drawer/<GENE>` returned `200`; `GET /api/runs` listed all four with
`any_invariant_failed: false`, region `Small_Intestine_Terminal_Ileum` and the
Crohn's contrast name; `/api/analyse`, `/api/autopick`, `/api/region-for` and
`/api/analyse/progress/*` returned `403`; a run outside the list returned the
"unknown run" refusal.

## The four

| run id | source | gene order | n (input→rendered, ledgered) | verdicts (GO / NO-GO / INSUFFICIENT) | what it shows |
|---|---|---|---|---|---|
| `20261018T190526Z-94ef5f` | Atlas `E-GEOD-101794 g2_g1` | submitted-table order (Atlas's own top-by-p order, unchanged) | 12 → 11, 1 | 5 / 5 / 1 | **The flagship run.** Region inferred from the disease by the cached chooser answer and stamped on the figure; the drawer's verdict box names the ileum as the run's chosen tissue and a disease tissue for this contrast. |
| `20261018T190549Z-81475c` | Atlas `E-GEOD-101794 g2_g1` | submitted-table order | 14 → 13, 1 | 5 / 6 / 2 | Same contrast, two more genes (CXCR2P1, APOBEC3A) — shows the ledger/accounting invariant (`input == rendered + ledgered`) holding at a different n without being a near-duplicate of the flagship run. |
| `20261018T190619Z-e52d65` | Atlas `E-GEOD-101794 g2_g1` | **alphabetical by gene symbol** | 12 → 11, 1 | 5 / 5 / 1 | Same gene set and contrast as the flagship run, reordered — the spoke order is a stated, changeable choice (rule 7), not baked into the data. |
| `20261018T190650Z-df1587` | Atlas `E-GEOD-101794 g2_g1` | submitted-table order | 12 → 11, 1 | 5 / 5 / 1 | A second run of the flagship's set and order, kept so the demo has a same-day replicate: identical verdicts, identical summaries (deduplicated by facts hash), different run id and timestamp. |

The one ledgered gene in every run is `ENSG00000279487` (Atlas Ensembl 95 id
not in the current Open Targets release — unresolved, not absent).

## What this allowlist does not show

- **No filtered Crohn's run exists on disk.** None of the Crohn's runs has a
  threshold filter applied (`filters: []` in every manifest).
  `20260926T001147Z-054913` (one of `config.py`'s current defaults) shows a
  filtered figure honestly, but it is a pasted Liver-region fixture, not
  Crohn's, so it is out of scope for a Crohn's-only demo. If a filtered
  Crohn's figure is wanted, it has to be rendered fresh (locally, not on the
  demo box) and added to `runs/` before it can join this list.
- **No run outside `E-GEOD-101794`.** Every other Crohn's Atlas accession in
  the index (17 of them) has never been rendered in this repo. Widening the
  demo to show contrast diversity within Crohn's would mean running new
  analyses first.
- **The region chooser's answer is one tissue.** Crohn's disease also affects
  the colon; the chooser picked the terminal ileum and said why, and the
  figure prints that sentence. A colon run would be a new analysis.
