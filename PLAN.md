# targetring — MVP spec

Written 2026-09-23 by Wren II, after the scientific-council and QA reviews in
`docs/reviews/`. Puck approved the build overnight, unattended, and asked that
the council then review the built MVP and that its findings be folded into a
v2. This document is what v1 builds.

Every source claim below was verified live by one of the two reviews. Where the
original plan was wrong, the correction is marked **[was wrong]** with the
review id.

## 1. What it does

Paste or upload a DE table (gene symbol, logFC, adjusted p), pick a body region,
get a Circos figure: the circle is the gene set ordered by rank, each ring is
one measure drawn as a heatmap. Click a gene, a drawer opens with its drug
pipeline and its literature. Export SVG and the CSV of underlying values.

## 2. The rings, and where each number comes from

**Backbone: Open Targets `target.prioritisation`** — 18 keys, each already
scaled to [-1, 1] with sign = desirability, covering druggability, safety,
essentiality, genetic constraint, paralogs, membrane/secreted, tissue
specificity and distribution, mouse KO score, and max clinical stage.
**[was wrong, COUNCIL M2]** the original plan rebuilt this by hand from
separate sources. Values arrive as **strings** — cast explicitly.

Rings v1:

1. **Druggability** — `prioritisation` (`hasLigand`, `hasPocket`,
   `hasSmallMoleculeBinder`, `hasHighQualityChemicalProbes`).
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
   terminations say anything about the target: BRAF's stop reasons are
   `Insufficient_Enrollment 108`, `Business_Administrative 50`, `Negative 19`,
   `Safety_Sideeffects 13`. A single ordinal with a "discontinued" marker would
   mislead on seven of every eight terminations (COUNCIL D4).
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

## 3. Ranking

**No weighted sum as the headline** (COUNCIL M3). It is incommensurable across
ordinal, ratio and binary inputs; missingness is not random, so any imputation
systematically rewards under-studied genes; and sliders over unnormalised
scales do not mean what they appear to.

Instead:
- Rank-transform each ring **within the submitted gene set**, not a global
  background.
- Headline: the **Pareto non-dominated set** over druggability, safety,
  regional specificity, and clinical/evidence.
- Secondary: median available rank, ignoring missing rings, with *k* shown.
- The weighted score stays, clearly labelled a convenience, weights printed.
- Keep the reweighting-stability flag on any gene whose top-10 place does not
  survive reweighting.
- **Never impute.**

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

Failing any invariant renders a banner on the figure.

## 7. Fixtures

Named in `docs/reviews/QA-v0-plan.md` S3: a ubiquitous gene with null
specificity, a well-drugged target, a gene with no safety record, one with no
rodent data, one with an Excel-corrupting symbol, one with paralogs, one
obsolete symbol.

## 8. Explicitly out of scope overnight

Measured rate limits under parallel load; the LLM triage gold set (human work);
UniProt/ChEMBL resolution is unprobed, so any ChEMBL-backed ring is untrusted on
day one; a defensible weighting scheme; and any claim that the ranking is
biologically meaningful. All of these go in the morning report as open, not as
done.

## 9. Licensing

To be checked by a human before workplace use — flagged in the council review.
Open Targets and GTEx are permissive; HPA's terms need reading if it returns to
the stack.
