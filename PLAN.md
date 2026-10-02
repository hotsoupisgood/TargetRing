# targetring — MVP spec

Written 2026-09-23 by Wren II, after the scientific-council and QA reviews in
`docs/reviews/`. Puck approved the build overnight, unattended, and asked that
the council then review the built MVP and that its findings be folded into a
v2. This document is what v1 builds.

**Corrected 2026-09-24 (v2.5)** where the build had moved past it: §1's "ordered
by rank", the ring list and the stop-reason counts in §2, the retired
ClinicalTrials.gov hop, §3 (ranking, now *Gene order and filters*), the invariant
list in §6 and the out-of-scope list in §8. Everything else stands as written.

Where the *original plan* was wrong against a live source, the correction is
marked **[was wrong]** with the review id. Where the *build* has since diverged
by decision, it is marked **[changed, vN]**.

## 1. What it does

Paste or upload a DE table (gene symbol, logFC, adjusted p), pick a body region,
get a Circos figure: the circle is the submitted gene set in a stated order,
each ring is one measure drawn as a heatmap. Click a gene, a drawer opens with
its drug pipeline and its literature. Export SVG and the CSV of underlying
values.

**[changed, v2.5]** This read "the circle is the gene set ordered by rank".
Nothing is ranked any more (§3). The rings are evidence, not a score, and
threshold filters may remove genes from the circle — when they have, the figure
says so and the removed genes are in the drop ledger.

## 2. The rings, and where each number comes from

**Backbone: Open Targets `target.prioritisation`** — 18 keys, each already
scaled to [-1, 1] with sign = desirability, covering druggability, safety,
essentiality, genetic constraint, paralogs, membrane/secreted, tissue
specificity and distribution, mouse KO score, and max clinical stage.
**[was wrong, COUNCIL M2]** the original plan rebuilt this by hand from
separate sources. Values arrive as **strings** — cast explicitly.

**[changed, v2.3 / v2.5]** The list below is the v1 list of nine. The build draws
**twelve rings, nine of them external**, and the differences are decisions taken
since, each recorded in `docs/V2-REPORT.md`:

| # | ring (as built) | against the v1 list |
|---|---|---|
| 1 | `logfc` | ring 8, split in two |
| 2 | `neg_log10_padj` | ring 8, split in two |
| 3 | `tractable` | ring 1, minus the probe key (v2.3 item 1, COUNCIL M7/V8) |
| 4 | `chemical_probe` | ring 1's fourth key, drawn separately and allowed to be hatched |
| 5 | `safety` | ring 3 |
| 6 | `regional_specificity` | ring 2 |
| 7 | `clinical_phase` | ring 4 |
| 8 | `clinical_activity` | ring 5 |
| 9 | `tdl` | **new**: Pharos TDL (v2.2 item 4, COUNCIL S1) |
| 10 | `rodent` | ring 7, renamed "Mouse phenotype lead" (COUNCIL V15) |
| 11 | `literature` | ring 6 |
| 12 | `completeness` | ring 9, k over the nine EXTERNAL rings only (COUNCIL V13) |

`k` counts rings 3–11. It is a completeness gauge and nothing else: since v2.5
removed ranking it orders nothing and selects nothing, and the figure says so.
`completeness` is deliberately **not** filterable — filtering on k would filter
on how much anybody has studied a gene, which is M3's bias with a slider on it.

Rings v1:

1. **Druggability** — `prioritisation` (`hasLigand`, `hasPocket`,
   `hasSmallMoleculeBinder`, `hasHighQualityChemicalProbes`). **[changed, v2.3
   item 1]** Two rings: the three dense keys carry the ring named
   *Tractability*, and `hasHighQualityChemicalProbes` — absent for 9 of 14
   council targets — is drawn beside it and allowed to be hatched, so an absent
   probe key no longer takes three dense keys down with it.
2. **Regional specificity** — GTEx v2 `medianGeneExpression`, region TPM ÷
   median across the 54 tissues. **[was wrong, COUNCIL S2]** HPA's free-text
   search returns other genes, explodes on short symbols, legitimately nulls
   ubiquitous genes, and is slow enough to blow the whole time budget on one
   gene (QA B4). Resolve the gencode version first via
   `/api/v2/reference/gene?geneId=<symbol>`; a wrong version returns empty and
   looks like real absence.
3. **Safety / tolerability** — `prioritisation` (`hasSafetyEvent`,
   `geneEssentiality`, `geneticConstraint`, `isCancerDriverGene`), with the raw
   `safetyLiabilities` rows kept for the drawer.
4. **Drug pipeline — furthest phase reached** — `drugAndClinicalCandidates`.
   **[was wrong, COUNCIL M1]** `target.knownDrugs` does not exist; the query
   errors. `maxClinicalStage` is a **string enum** (`PHASE_1`, `PHASE_1_2`,
   `PHASE_2`, `PHASE_3`, `APPROVAL`) — map it with an explicit lookup, never
   `parseInt`.
5. **Drug pipeline — current activity** — trial status from
   `clinicalReports.trialOverallStatus`. Two rings not one, because only ~13% of
   terminations say anything about the target. BRAF's stop reasons, **measured
   live 2026-09-24 over 1,433 clinical reports** (the v1 counts here were
   `Business_Administrative 50` and `Negative 19`; they drift with the source and
   the argument does not): `Insufficient_Enrollment 108`,
   `Business_Administrative 51`, `Negative 17`, `Study_Design 13`,
   `Safety_Sideeffects 13`, `Logistics_Resources 12`, `Invalid_Reason 9`,
   `Another_Study 5`, `Study_Staff_Moved 4`, `Regulatory 3`, `No_Context 2`,
   `Uncategorised 1`. A single ordinal with a "discontinued" marker would
   mislead on seven of every eight terminations (COUNCIL D4).

   **[changed, v2.4]** Rows are selected by **carrying a `trialOverallStatus`**,
   never by a `source` literal. On 2026-09-24 Open Targets relabelled
   `clinicalReports.source` from `AACT` to `ClinicalTrials.gov`; a literal filter
   matched nothing, the ring went empty, and the build concluded the data was
   gone and grew a per-drug hop to the ClinicalTrials.gov v2 API to replace it.
   **That hop is deleted.** Everything it fetched — NCT id, overall status, lead
   sponsor, categorised stop reason — was already in the gene query, and the hop
   cost 217 requests and 48.2 s of a 70.5 s cold 50-gene run, plus one live 429.
   A ring that goes empty is a question about the selection before it is a claim
   about the source; the check that would have caught it in minutes — assert the
   distinct `source` values and fail on an unrecognised one rather than filtering
   to a known one — now exists and costs nothing.
6. **Literature volume** — PubTator3, which resolves gene mentions to NCBI Gene
   ids instead of matching text, so common-word symbols do not inflate.
7. **Rodent evidence lead** — Open Targets `mousePhenotypes` for v1. **[was
   wrong, QA S4]** IMPC as planned returns a silent zero; direct integration
   waits.
8. **Puck's own logFC and adjusted p** — his measurement belongs on his figure.
9. **Data completeness** — *k* rings available per gene, drawn explicitly.

Mechanism vs indication (COUNCIL D2): ChEMBL's `direct_interaction` and
`disease_efficacy` flags are the only clean mechanism filter. DGIdb returns 220
"interactions" for BRAF against ChEMBL's 14 — including enzalutamide, an
androgen-receptor antagonist — so using it for a crowding count inflates ~15×.
And polypharmacology still lies: BRAF's top row is sorafenib, approved for
RCC/HCC, not BRAF-driven disease. Carry `action_type` and the indication beside
the phase; never let an approved multi-kinase inhibitor silently set a ring to
"approved".

## 3. Gene order, and filters

**[changed, v2.5] There is no ranking.** This section specified a Pareto
non-dominated headline over four axes, rank-transformation within the submitted
set, a median available rank, a weighted convenience score with printed weights,
and a reweighting-stability flag. All of it is deleted — the code, the figure
elements, the footer sentences, the manifest fields, the CSV columns and the
tests that served them — along with the caveats that existed only because ranking
did ("ranking is provisional and not validated").

The reasons are in `docs/V2-REPORT.md` §v2.2–§v2.4 and the two council findings
this section's own mechanism kept reproducing: COUNCIL V2 (missingness made
domination *easier*, so a gene with one axis ejected genes with four) and COUNCIL
V10 ("non-dominated" is not "good"; on a 7-gene list 71% of the figure got a
headline diamond, including GAPDH). What is left is what was asked for
originally: **a gene set, rings of evidence, and a click-through to a gene's
drugs and papers.**

### 3.1 Gene order around the circle

The order carries meaning, so it is **decided, stated on the figure, and checked
in the artefact** (invariant 7). The circle runs clockwise from the ring-name
gap at 12 o'clock. v2.7 reserves a fixed 26° wedge there on the audit render and
names each ring inside it, at that ring's own radius, so 12 o'clock is now the
middle of the gap and the first spoke begins 13° clockwise of it. The gap does
not shrink as genes are added — the spokes carry the cost and the figure prints
what it cost. The slide render reserves no wedge: its circle is half as wide, so
twelve names inside it would overlap each other, and its ring names stay on the
scales in the column.

- **Default: the order of the submitted DE table, unchanged.** It is usually
  already sorted, and it is his measurement rather than a composite this build
  invented.
- **Alphabetical** by gene symbol.
- **By a single ring** (`ring:<key>`), best first, using that one source's own
  number. One ring at a time combines nothing, so it cannot reintroduce the
  incommensurability M3 objected to. Genes with no value on that ring go last as
  a visible block, in submitted order — never interleaved and never treated as
  the lowest value.

An ordering this figure cannot honour is rejected rather than silently swapped
for another while the figure prints the first.

### 3.2 Manual threshold filters, per data source

One filter per ring per comparison, `ring >= n` or `ring <= n`, compared against
the **ring's own raw value** — the number in `values.csv`, in the units the
ring's label states — never against a rank or a scaled colour position. Two rules
govern them and both are asserted rather than described:

1. **A threshold never silently drops a gene with no data on that ring.** The
   filter cannot be evaluated, so the gene is kept and marked. Dropping it would
   make filtering exclusion-by-missingness, which systematically deletes the
   under-studied genes — `docs/reviews/COUNCIL-v0-plan.md` M3's bias, arriving
   through a UI control instead of through an imputation. A separate, explicit
   per-filter `drop_no_data` toggle removes them, off by default, and then the
   figure says that is what happened.
2. **Filtered genes go in the drop ledger**, under `filtered` and
   `filtered_no_data` — distinguishable from parse failures and unresolvable
   symbols — so `input == rendered + ledgered` still holds and the counts on the
   figure add up.

The settings go into the run manifest and the CSV export, so a figure is
reproducible from its artefacts, and the active filters are printed on both
renders: a reader must not mistake a filtered plot for the whole submitted set.

**Never impute.** That rule is older than the ranking and outlives it.

## 4. Input contract

The DE table is a user's spreadsheet, so: symbol synonyms and deprecated
symbols, Excel's date corruption (`SEPT9` → `9-Sep`, `MARCH1` → `1-Mar`),
Ensembl ids mixed with symbols, mouse symbols pasted for human, duplicate rows,
blank padj, non-numeric logFC, 5,000-row lists, comma decimal separators, BOM
and encoding. Each case: reject, coerce or flag — and every affected gene lands
in the **drop ledger** with the reason. Exact-symbol matching alone silently
deletes renamed genes (QA B5), so resolution comes first and matching happens on
the resolved symbol.

## 5. Stack and speed

FastAPI + pycirclize (matplotlib) → SVG, thin frontend, on-disk cache keyed by
gene + source + schema version. Cold 50 genes under 60 s at concurrency ≤ 4 per
host; the figure renders before the literature ring fills. SVG is the export
that matters for v1 — it is editable in Illustrator, Inkscape and PowerPoint —
plus the CSV. PNG at 300 dpi and PDF can wait.

**[changed, v2.4] The 60 s target is met and the per-drug ClinicalTrials.gov hop
is gone from the stack.** Measured cold 50-gene runs: 70.5 s with the hop (48.2 s
of it the hop), 27.2 s without. The sources on the render path are Open Targets,
GTEx, PubTator3, Pharos and one Europe PMC integrity probe; ChEMBL and the
per-gene Europe PMC fetch moved to the drawer in v2.2 item 3.

## 6. Invariants the build must assert

1. Cell-count identity between the CSV of underlying values and the rendered
   rings.
2. Missing-fill disjointness: the `no_data` fill must be provably distinct from
   every value colour.
3. Label correspondence: spoke *n*'s label matches spoke *n*'s data.
4. `input == rendered + ledgered`.
5. Europe PMC query echo byte-identical; no `ERROR` key in any NCBI response.
6. The five-ID regression test: submit `ENSG00000157764`, `ENSG00000999999`,
   `ENSG00000141510`, `ENSG00000012048`, `ENSG00000146648`; assert 5 spokes,
   that `ENSG00000999999` is `missing`, and that BRAF's values equal those from
   a single-target query. A count check alone passes while the labels are wrong.
7. **[added, v2; re-pointed, v2.5] The figure states what it did.** Read back out
   of the rendered SVG: no headline marker survives the ranking (a `◆` anywhere
   is a claim with nothing behind it), the spoke-ordering rule is printed on the
   figure, the printed rule is the one the run used, and every active threshold
   filter is named on the figure.

   I7 was introduced in v2 as *headline eligibility* (COUNCIL V2/V3): every gene
   in the Pareto front had a real value on all the Pareto axes, and the diamonds
   drawn were exactly that front. Its first half was a ranking check and went
   with ranking. Its second half was a figure-honesty check — the artefact must
   not assert what the run cannot support — and that outlives the feature it was
   written for, so I7 keeps its id, its place in the manifest's PASS/FAIL list,
   and that job. **No invariant was relaxed or dropped; there are still seven.**

Failing any invariant renders a banner on the figure.

## 7. Fixtures

Named in `docs/reviews/QA-v0-plan.md` S3: a ubiquitous gene with null
specificity, a well-drugged target, a gene with no safety record, one with no
rodent data, one with an Excel-corrupting symbol, one with paralogs, one
obsolete symbol.

## 8. Explicitly out of scope overnight

Measured rate limits under parallel load; the LLM triage gold set (human work);
UniProt/ChEMBL resolution is unprobed, so any ChEMBL-backed ring is untrusted on
day one. Both go in the morning report as open, not as done.

**[changed, v2.5]** Two entries are gone rather than still open: "a defensible
weighting scheme" and "any claim that the ranking is biologically meaningful".
There are no weights and no ranking to defend. What replaces them is narrower and
still worth saying: **nothing on this figure is a ranking, a score or a
recommendation.** It is a gene set, in a stated order, with the evidence that
exists for each gene drawn honestly beside it — including where none exists.

**[added 2026-10-02, Puck on WhatsApp] Expression Atlas microarray — won't
build, not "not yet".** Atlas contrasts are read for RNA-seq only. This costs
**854 of 1,126 indexed contrasts (76%)** and leaves **88 of 246 diseases
(35.8%)** served, 158 entirely unavailable — a real loss, taken on test-surface
cost rather than on coverage. The array contrasts stay indexed, counted and
visible in the picker, and the app says *which* of "Atlas has nothing" and "this
app does not read this format" applies. Full reasoning and the measured numbers
any reopening would start from:
`docs/DECISION-RNASEQ-ONLY-2026-10-02.md`.

## 9. Licensing

To be checked by a human before workplace use — flagged in the council review.
Open Targets and GTEx are permissive; HPA's terms need reading if it returns to
the stack.
