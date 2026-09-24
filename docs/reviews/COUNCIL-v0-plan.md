# Council review — target-prioritisation Circos app

**Date:** 2026-09-23. **Reviewer:** scientific council (subagent).
**Scope:** the original 8-question review, plus two amendments from the principal:
(a) treat every source as a default to argue with, name the *best* free source per ring;
(b) "are there drugs in development against this target" is a first-class requirement.

**Every endpoint, field name, query string and number in this document was produced by a
live API call on 2026-09-23.** Anything I could not verify is listed in §7 and must not be
treated as checked. No application code was written; this is a review.

**Ordering:** BLOCKING → SHOULD-FIX → NOTE. Item IDs are stable: `M*` = main ruling,
`S*` = source-swap addendum, `D*` = drugs-in-development addendum. Cross-references use them.

### Supersessions (read this before building)

- **S2 supersedes M8.** M8 said "use HPA's own Tau score instead of your ratio". S2 replaces
  HPA with GTEx for this ring entirely. If HPA is retained for any reason, M8 still applies.
- **D1 supersedes M1's "just use the right field" advice.** M1 fixes the broken field name;
  D1 specifies the full pipeline query that should actually be built.
- **S1 refines M7.** M7 says ring 1 conflates druggability with study history; S1 gives the
  concrete two-ring split and the complete-coverage source.

---

## 0. Ring-by-ring source verdict

Which proposed rings have no data source behind them **as described**, and what replaces each.

| # | Ring as proposed | Source as described | Verdict | Replacement |
|---|---|---|---|---|
| 1 | Druggability — OT `target.tractability`, ordinal buckets | Exists, but the field is `tractability{label, modality, value}` — **`label`, not `id`** | Exists; conflated + sparse | Split: Pharos **TDL** (complete, 20,412/20,412) for "developed" + pocket/ligand evidence for "tractable" (S1, M7) |
| 2 | Regional specificity — HPA nTPM ÷ median across tissues | Exists but returns `null` for BRAF; search matches synonyms | **Unusable as described** | GTEx `medianGeneExpression` + UBERON ids (S2) |
| 3 | Toxicity — OT `safetyLiabilities` + DepMap + gnomAD | All three exist; `safetyLiabilities` = **1 row** for BRAF | Exists; sparse | Keep OT `safetyLiabilities` + `depMapEssentiality`; take constraint from **gnomAD direct** (S4) |
| 4 | Rodent phenotype **rescue** | IMPC/MGI give knockout phenotypes, **not rescue**; MouseMine 503 | **No source exists for "rescue"** | Alliance + IMPC for phenotypes (S6); "rescue" relabelled per M4 |
| 5 | Max clinical phase / known drugs — `target.knownDrugs` | **Field does not exist** | **No source as named** | `drugAndClinicalCandidates` (M1) + full pipeline query (D1) |
| 6 | Literature volume + "novelty ratio" | Europe PMC works but swings 44% on phrasing | Counts OK; **ratio is not sound** (M6) | PubTator3 `count` + `year` facets (S7); neglect axis = Pharos `novelty` + Tdark |
| 7 | Paralog count | No field returns a paralog **count**. `homologues{homologyType, targetPercentageIdentity, speciesName}` and `prioritisation.paralogMaxIdentityPercentage` exist | Derivable, not readable | Derive by filtering `homologues`; or use `paralogMaxIdentityPercentage` (BRAF = `0`) |
| 8 | Principal's own logFC / adjusted p | Own data | **Sound** | — |
| 9 | Data completeness per gene | Derived | **Sound** | — |
| — | Drugs *in development* (2nd amendment) | not in original plan | New first-class ring(s) | D1–D5; **two** rings per D4 |

---

## 1. BLOCKING

### M1 — `target.knownDrugs` does not exist; ring 5 as specified has no field.

**Evidence.** Introspection of `Target` returns no `knownDrugs`. Live query attempt returned:
`Cannot query field 'knownDrugs' on type 'Target'`. The real field is
`drugAndClinicalCandidates { count rows { maxClinicalStage id drug clinicalReports diseases } }`.
`maxClinicalStage` is a **string enum** (`PHASE_1`, `PHASE_1_2`, `PHASE_2`, `PHASE_3`,
`APPROVAL`), **not an integer**. BRAF: `count: 18`, stages present `PHASE_1, PHASE_1_2,
PHASE_2, PHASE_3`.

**Change.** Use `drugAndClinicalCandidates`. Map the enum to an ordinal with an explicit
lookup table — do **not** `parseInt` it. See D1 for the query you should actually build.

### M2 — You are rebuilding `target.prioritisation`, which already exists and is better normalised than your weighted sum.

**Evidence.** Live for BRAF, 18 keys, each scaled to [-1, 1] with sign = desirability:

```
geneticConstraint  -0.932377275234418     hasHighQualityChemicalProbes  1
hasLigand           1                     hasPocket                     1
hasSafetyEvent     -1                     hasSmallMoleculeBinder        1
isCancerDriverGene -1                     isInMembrane                  1
isSecreted          0                     maxClinicalStage              1
mouseKOScore       -0.9693002804087875    mouseOrthologMaxIdentityPercentage 0.6083550000000002
paralogMaxIdentityPercentage 0            tissueDistribution           -1
tissueSpecificity  -0.5343049526980741    celltypeSpecificity          -0.6084270902272226
celltypeDistribution 0.00588235294117645  geneEssentiality              0
```

That covers rings 1, 3, 5, 7 and most of 2.

**Build gotcha, measured:** these `value`s come back as **strings**, not floats — calling
`round()` on one raised `TypeError: type str doesn't define __round__ method`. Cast explicitly.

**Change.** Ingest `prioritisation` as the backbone. Add only what it lacks: your logFC/padj,
regional expression, literature, and the rodent-evidence lead. Keep the raw per-source fields
for the click-drawer.

### M3 — The weighted sum is unsound over these measures.

**Evidence / three distinct defects.**
1. **Incommensurable scales.** Inputs are ordinal (tractability buckets), ratio (nTPM),
   and binary (safety event present). A weighted sum asserts an exchangeability across these
   that nothing supports.
2. **Missingness is not random.** `safetyLiabilities` = 1 row for BRAF; IMPC covers
   8,429 genes of ~20k; ChEMBL has no target record at all for TMEM229B. Sparse records
   correlate with *under-study*, so **any** imputation (zero, median, anything) systematically
   rewards obscure genes — which is precisely the bias ring 6 claims to correct.
3. **The sliders lie.** User-adjustable weights over unnormalised scales means a weight of
   "0.5" does not mean half as much as "1.0".

**Change — buildable in one night.**
- Rank-transform each ring **within the submitted gene set**, not against a global background.
- Report **(i)** the median available rank, ignoring missing rings, and
  **(ii)** the **Pareto non-dominated set** over 3–4 axes (druggability, safety,
  regional specificity, clinical/evidence).
- Ship **Pareto membership as the headline**; the weighted score is secondary and labelled as
  a convenience.
- **Never impute.** Render missing as a visually distinct hatch and print *k* = number of rings
  available per gene.
- Keep the reweighting-stability flag (a gene whose top-10 position does not survive
  reweighting) — that part of the plan is good.

See also **S9**: swapping in better sources does not fix any of this. TDL (S1) helps most,
because it is the only axis with no missing values.

### M4 — "Rescue" cannot be established from abstracts. The label overstates the evidence.

**Evidence.** A rescue claim requires: a model with a phenotype; an intervention on the target;
the phenotype returning toward wild-type; and controls. Abstracts routinely assert the claim
without reporting the design. An LLM triaging abstracts cannot distinguish rescue from
correlation, from prevention, or from an overexpression artefact.

**Change — the verdict label must be:**

> **"abstract asserts target manipulation altered the phenotype (unverified)"**

It is a **lead, not a verdict**. Exactly three levels, no more:

```
asserted  |  asserted-negative  |  none found
```

Show the supporting sentence and the PMID in the drawer. **Never fold this into the score.**

### S2 — Regional specificity: GTEx replaces HPA.

**Evidence.**
`GET https://gtexportal.org/api/v2/expression/medianGeneExpression?gencodeId=ENSG00000157764.12&datasetId=gtex_v8`
→ `{"median":12.5192,"tissueSiteDetailId":"Adipose_Subcutaneous","ontologyId":"UBERON:0002190","datasetId":"gtex_v8","gencodeId":"ENSG00000157764.12","geneSymbol":"BRAF","unit":"TPM"}`
across 54 tissues (also: `Adipose_Visceral_Omentum` 10.2532 / `UBERON:0010414`;
`Adrenal_Gland` 6.02665 / `UBERON:0002369`; `Artery_Aorta` 12.9639 / `UBERON:0001496`;
`Artery_Coronary` 11.7971 / `UBERON:0001621`).

By contrast HPA returned `"RNA tissue specific nTPM": null` for BRAF, and the same search
returned **HMG20B, KIAA1549, RIPPLY1, ALDH16A1, APELA, BAMBI, CNPY1** — both stated traps
(synonym matching, legitimate nulls) are real.

**Trap, and it is a dangerous one:** the gencode ID must be the exact version GTEx serves.
`ENSG00000157764.14` returned `{"data":[],"paging_info":{...,"totalNumberOfItems":0}}` — a wrong
version **fails silently as "no data"**, which your missing-data rendering will then display as
a legitimate absence. Always resolve first:
`GET https://gtexportal.org/api/v2/reference/gene?geneId=BRAF` → `gencodeId: "ENSG00000157764.12"`,
`gencodeVersion: "v26"`, `genomeBuild: "GRCh38/hg38"`.

**Change.** GTEx for the quantitative ring, against UBERON as the controlled vocabulary.
~2 h including the mapping. **Do it tonight** — the ratio needs comparable medians across a
defined tissue set, and HPA provides neither. Keep HPA for protein-level IHC as a later upgrade.

### S7 — Literature: PubTator3 replaces Europe PMC, and it removes code.

**Evidence.**
`GET https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/?text=%40GENE_BDNF`
→ top-level `count: 86493`, gene-disambiguated (note `@` must be URL-encoded as `%40`).
`?text=%40GENE_BDNF%20hippocampus` → `count: 31934`.
Response shape: `{facets:{facet_fields:{journal:[...], type:[...], year:[...]}}, page_size,
current, count, total_pages}`. The **`year` facet is in the same response**, so papers-per-year
costs **zero extra calls** (e.g. for the combined query: 2022→3957, 2023→3665, 2021→3329,
2024→2598, 2020→2501).

Against Europe PMC's measured 44% phrasing swing (see M6), this is strictly better.

**Honest limit.** PubTator annotates genes, diseases, chemicals, species, variants and cell
lines — **not anatomy**. The region half of the ratio remains free text, so the quantity is
"disambiguated gene, fuzzy region", not a clean novelty measure. Label it exactly that way.

**Change.** Swap in PubTator3 for counts and the trend. ~1 h, net *less* code. **Tonight.**
Use Pharos `novelty` + Tdark as the actual neglect axis (S1), not the ratio (M6).

### D1 — The best free source for the development pipeline is Open Targets' gene-keyed clinical join, and it is not close.

**Evidence.** One call, keyed on the gene, returns the whole pipeline:

```graphql
{ target(ensemblId:"ENSG00000157764") {
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug { id name drugType maximumClinicalStage drugWarnings { warningType description } }
        diseases { diseaseFromSource disease }     # NB: fields are diseaseFromSource/disease, NOT name
        clinicalReports {
          trialPhase clinicalStage trialOverallStatus trialStartDate
          trialWhyStopped trialStopReasonCategories source url
        } } } } }
```

BRAF, live: `count: 18`. Examples:
- `BELVARAFENIB | Small molecule | maximumClinicalStage PHASE_2 | 4 trials` →
  `PHASE2 / PHASE_2 / ACTIVE_NOT_RECRUITING / 2021-01-18 / AACT`;
  `PHASE2 / PHASE_2 / RECRUITING / 2026-02-12 / AACT`
- `PLIXORAFENIB | Small molecule | PHASE_2 | 7 trials` (one row `None / PHASE_2 / None / None / TTD`)
- `SORAFENIB | Small molecule | APPROVAL | 594 trials`

Trial status distribution across all 18 rows:
```
COMPLETED 689 · TERMINATED 198 · UNKNOWN 169 · RECRUITING 125 · ACTIVE_NOT_RECRUITING 98
WITHDRAWN 56 · None 52 · NOT_YET_RECRUITING 30 · NO_LONGER_AVAILABLE 8 · SUSPENDED 7
AVAILABLE 1 · APPROVED_FOR_MARKETING 1
```

I ruled earlier that Open Targets is only a default to be argued with. **On this ring it wins
anyway**, because it is the only free source keyed on the *gene* that also carries per-trial
status and a machine-readable stop reason. Cost: it is already inside a call you make. **Tonight.**

**Known gaps.** (i) No sponsor field — that needs the CT.gov hop (D3). (ii) Rows sourced from
`TTD` and `EMA Human Drugs` carry `trialPhase: None`, `trialOverallStatus: None`,
`trialStartDate: None`; only `AACT`-sourced rows (the CT.gov mirror) have status. Filter on
`source` before computing "current activity", or you will read missing status as inactive.

**Change.** Build the query above. Treat `source` as a required column in any activity logic.

### D2 — Mechanism vs indication: ChEMBL's `direct_interaction` is the only clean filter; DGIdb has none.

**Evidence.**
`GET https://www.ebi.ac.uk/chembl/api/data/mechanism.json?target_chembl_id=CHEMBL5145&only=molecule_chembl_id,action_type,direct_interaction,disease_efficacy,max_phase,mechanism_of_action`
→ `total_count: 14`; rows e.g.
`{"action_type":"INHIBITOR","direct_interaction":1,"disease_efficacy":1,"max_phase":4,
"mechanism_of_action":"Serine/threonine-protein kinase B-raf inhibitor",
"molecule_chembl_id":"CHEMBL2105729"}`.
`direct_interaction` and `disease_efficacy` **are** the mechanism test the principal wants.

**Failure mode, measured.** DGIdb returns **220** interactions for BRAF, against ChEMBL's 14
and OT's 18, because it ingests biomarker and resistance associations as "interactions". It
returned **ENZALUTAMIDE** — an androgen-receptor antagonist — for BRAF, `interactionScore
0.0320799`, `sourceDbName: CKB-CORE`. Using DGIdb for a crowding count inflates it ~15×.

**The subtler trap that no flag fixes.** BRAF's highest-staged row is `SORAFENIB | APPROVAL |
594 trials`. The mechanism link is genuine (multi-kinase inhibitor), but sorafenib is approved
for RCC/HCC, **not** BRAF-driven disease. So "an approved drug hits this target" ≠ "this target
is clinically validated." Polypharmacology defeats a mechanism filter alone.

**Change.** Carry `action_type` and the **indication** next to the phase everywhere. Never let a
polypharmacology hit silently set a ring to "approved". Where the indication is unrelated to the
submitted tissue/disease context, mark the row.

### D5 — "No drugs found" vs "no data": OT yes, ChEMBL only in two steps, DGIdb not at all.

**Evidence.**
- **Open Targets — distinguishable.** `TMEM229B` (`ENSG00000198133`, a Tdark gene) resolves and
  returns `approvedSymbol: TMEM229B, drugAndClinicalCandidates.count = 0` — a **meaningful
  zero**. BRAF returns `18`. A `null` `target` means no data. Use this.
- **ChEMBL — only with two calls.**
  `GET /chembl/api/data/target.json?target_components__accession=Q9NWM3` → `total_count: 0`,
  i.e. **no ChEMBL target record at all**. So you must record *which* call came back empty:
  no target record = **no data**; target record exists *and* `mechanism.json` returns 0 =
  **genuinely undrugged**. One extra call per gene.
- **DGIdb — fails the plan's rule outright.** `TMEM229B` → `nodes: 0`; `NOTAREALGENE123` →
  `nodes: 0`. Identical response shapes. DGIdb **cannot** separate an undrugged gene from an
  unknown one, so under "missing must never render as low" it must never drive a ring value.

**Change.** Drive the ring from OT's `count`. Add the ChEMBL two-step only if you want the
mechanism-filtered count. Confine DGIdb to drawer evidence, never a ring.

---

## 2. SHOULD-FIX

### M5 — IMPC coverage is 42%, and absence is not evidence.

**Evidence.** Live Solr:
- `https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select?q=*:*&rows=0&wt=json` → `numFound: 67350`
- `&json.facet={genes:"unique(marker_symbol)"}` → `genes: 8429` distinct genes
- `?q=marker_symbol:Bdnf&rows=2&wt=json` → `numFound: 0` — yet `Bdnf` **does** exist in the
  gene core: `/solr/gene/select?q=marker_symbol:Bdnf` → `numFound: 1`
- doc fields include `allele_accession_id, allele_symbol, assertion_type, colony_id,
  effect_size, intermediate_mp_term_id`
- **MouseMine (MGI's API) returned HTTP 503 on two separate attempts** —
  `https://www.mousemine.org/mousemine/service/version`. Do not depend on it.
- OT `mousePhenotypes` works as an alternative: **77** terms for BRAF with MP IDs.

**Change.** Render "not phenotyped" as visually distinct from "no phenotype". Note on the figure
that embryonic-lethal lines drop out of adult phenotyping data — which biases exactly the
essential genes ring 3 flags as risky.

### M6 — The novelty ratio measures term ambiguity, not neglect. (Verdict: drop it.)

**Evidence.** Europe PMC works:
`GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=…&format=json&pageSize=1`
→ `hitCount`. But for a single gene, hit count depends on phrasing:

```
BDNF AND hippocampus            48102
GENE:"BDNF" AND hippocampus     33396      <- 44% swing on phrasing alone
"BDNF" AND "hippocampus"        48102
BDNF                           115291
```

(The endpoint also returned a transient `{"errCode":503,...}` once — retry logic needed.)
A ratio of two unstable counts, whose instability differs per gene with symbol ambiguity and
aliasing, is **not comparable across genes**. It measures how ambiguous the symbol is.

**Change.** **Drop the ratio.** Show absolute counts with the exact query string displayed,
as context only, outside the score. Take counts from PubTator3 instead (S7), and use Pharos
`novelty` + Tdark (S1) as the actual neglect axis.

### M7 — Ring 1 conflates druggability with study history.

**Evidence.** BRAF's true tractability labels, live —
`SM: Approved Drug, Structure with Ligand, High-Quality Ligand, High-Quality Pocket, Druggable
Family`; `AB: UniProt loc high conf, GO CC high conf, Human Protein Atlas loc`;
`PR: Literature, UniProt Ubiquitination, Database Ubiquitination, Half-life Data, Small
Molecule Binder`. `Structure with Ligand` and `High-Quality Ligand` are records of **prior
effort**; `Druggable Family` is a **homology prior**. These are not independent evidence.

**Change.** Split "has been drugged" from "family suggests druggable". Note the collinearity
with rings 5 and 6 on the figure itself. See S1 for the concrete two-ring split.

### M8 — Ring 2's ratio vs HPA's own Tau. *(superseded by S2 — applies only if HPA is retained)*

**Evidence.** Confirmed live: BRAF `RNA tissue specific nTPM` = `null`; searching `BRAF`
returns HMG20B, KIAA1549, RIPPLY1 (both stated traps are real). The non-null shape looks like
`RIPPLY1 → {"kidney":"2.8","liver":"5.4"}`. HPA's own search-field list includes published
**Tau scores** (`Tissue Tau score (RNA)`, `Brain Tau score (RNA)`, `Cell type Tau score (RNA)`).

**Change.** If you stay on HPA, use Tau — it is peer-reviewed and comparable across genes. If
you keep a hand-rolled ratio, state the denominator tissue set on the figure. Preferred: S2.

### S1 — Druggability: split the ring. Pharos TDL is the better axis, but it is **not** druggability.

**Evidence.** `POST https://pharos-api.ncats.io/graphql` (no key):
`{target(q:{sym:"BRAF"}){name sym tdl fam novelty publicationCount}}` →
`tdl: "Tclin"`, `fam: "Kinase"`, `novelty: 0.00253795`, `publicationCount: 2631`
(`ppiCounts` values `[184, 26, 21, 187]`).

Coverage, faceted over everything:
`{targets{count facets(include:["Target Development Level"]){facet values{name value}}}}` →
`count: 20412`; `Tbio 12303, Tdark 5501, Tchem 1904, Tclin 704`. **Those sum to exactly
20,412 — zero missing values**, unlike OT tractability.

But TDL is by construction a **development/knowledge** level (Tclin = has an approved drug;
Tdark = nothing known), so it conflates druggability with study depth *more* directly than
tractability does. It is the cleaner axis because it is explicit and complete, not because it
is biophysical druggability.

**Change.** Two rings: **"developed"** = TDL (Pharos), **"tractable"** = pocket/ligand evidence
(OT `hasPocket`/`hasLigand`, or ChEMBL). ~1 h. **Tonight** — one call, complete coverage, and it
removes the missingness that breaks the ranking (M3).

### S4 — Toxicity: gnomAD direct yes, DepMap direct no.

**Evidence — gnomAD.** `POST https://gnomad.broadinstitute.org/api`:
`{gene(gene_symbol:"BRAF",reference_genome:GRCh38){gene_id gnomad_constraint{pli oe_lof oe_lof_upper lof_z}}}`
→ `gene_id ENSG00000157764`, `pli 0.999999999999986`, `oe_lof 0.1529742462665005`,
`oe_lof_upper 0.23234095358685583` (= **LOEUF**), `lof_z 7.348724683473751`.
OT's `geneticConstraint` by contrast returned, for BRAF:
`lof: {exp 92.182, obs 14, score 1, oe 0.15187, upperBin6 0}`, and
`upperBin6: null` for both `syn` and `mis` — with no field explicitly named pLI.
gnomAD direct gives named pLI and LOEUF in one call. ~30 min. **Tonight.**

**Evidence — DepMap.** `https://depmap.org/portal/api/download/files` returns HTTP 200 but the
body is an **HTML bot-verification page** (`<title>DepMap — Verification</title>`), not JSON.
There is no per-gene JSON API. Direct use means downloading the CRISPR gene-effect matrix
(figshare responds: `api.figshare.com/v2/articles/29409915` → 200) and precomputing:
3–4 h plus storage.

**Change.** gnomAD direct tonight. For essentiality use OT `depMapEssentiality{tissueId
tissueName screens{depmapId cellLineName geneEffect expression}}` and `isEssential`
(BRAF: `False`) tonight; bulk DepMap is a **later upgrade**.

### S5 — Drugs/phase sources: ChEMBL is the authority, DGIdb the breadth, OT the shortcut.

**Evidence.** ChEMBL target mapping trap:
`GET /chembl/api/data/target.json?target_components__accession=P15056&limit=1` →
`page_meta.total_count: 6` — six target records for one UniProt accession (single protein plus
families/complexes); the right one is `CHEMBL5145`, `relationship: "SINGLE PROTEIN"`,
`organism: "Homo sapiens"`, with `target_component_synonyms` containing
`{"component_synonym":"BRAF","syn_type":"GENE_SYMBOL"}`. **Filter to `SINGLE PROTEIN` or you
double-count.**

Molecule-level phase is a **string**:
`GET /chembl/api/data/molecule/CHEMBL1229517.json?only=pref_name,max_phase,withdrawn_flag,first_approval,oral,molecule_type`
→ `{"first_approval":2011,"max_phase":"4.0","molecule_type":"Small molecule","oral":true,
"pref_name":"VEMURAFENIB","withdrawn_flag":false}`. Note `max_phase` is the molecule's highest
phase across **all** indications — not per target and not current.

Per-indication phase and a bridge to trials:
`GET /chembl/api/data/drug_indication.json?molecule_chembl_id=CHEMBL1229517` →
`{"efo_id":"EFO:0000756","efo_term":"melanoma","indication_refs":[{"ref_type":"ClinicalTrials",
"ref_id":"NCT00949702,NCT01006980,…"}]}` — a long NCT list per indication.

DGIdb: `POST https://dgidb.org/api/graphql`,
`{genes(names:["BRAF"]){nodes{name interactions{drug{name approved} interactionScore
interactionTypes{type} sources{sourceDbName}}}}}` → 220 interactions; e.g.
`TRAMETINIB DIMETHYL SULFOXIDE, approved true, interactionScore 1.44883, sources
[PharmGKB, ClearityFoundationBiomarkers, CGI, DoCM, OncoKB]`. `interactionScore` is
citation-weighted, so it **rewards well-studied genes**.

**Change.** OT `drugAndClinicalCandidates` tonight (D1). ChEMBL mechanism filter is the
correctness upgrade (D2) — ~2 h. DGIdb: drawer evidence only, never a ring value (D5).

### S6 — Rodent: Alliance beats IMPC on coverage; MouseMine is down.

**Evidence.** `GET https://www.alliancegenome.org/api/gene/MGI:88127/phenotypes?limit=2`
→ `total: 34` — a clean, usable count. Alliance aggregates MGI + RGD, so it covers **rat** as
well as mouse; the ring says "rodent", while IMPC is mouse-only at 8,429 genes (M5).

**Traps.** (i) Payloads are deeply nested (`results[].subject.dataProvider…`) — budget parsing
time. (ii) `GET https://www.alliancegenome.org/api/gene/HGNC:1097` returned an **RGD (Rat Genome
Database)** record with `dataProviderCrossReference.referencedCurie: "RGD:735645"` — passing a
human HGNC id does not get you human data. Resolve species-specific ids explicitly.

**Change.** Alliance for coverage (~2 h, **tonight**); keep IMPC as the separate
"systematically phenotyped, so absence *is* informative" flag.

### D3 — CT.gov v2 is the authority on live status and sponsor, but cannot be keyed on a gene.

**Evidence — works.**
`GET https://clinicaltrials.gov/api/v2/studies?query.intr=vemurafenib&fields=NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,Condition,InterventionName,StartDate,WhyStopped&pageSize=2&countTotal=true`
→ `totalCount: 169`. Response shape is nested under `protocolSection`:
```
identificationModule.nctId / .briefTitle
statusModule.overallStatus            e.g. "COMPLETED"
statusModule.startDateStruct.date     e.g. "2016-05-23"
statusModule.whyStopped
sponsorCollaboratorsModule.leadSponsor.name    e.g. "Hoffmann-La Roche"
conditionsModule.conditions[]         e.g. ["Metastatic Melanoma"]
designModule.phases[]                 e.g. ["PHASE2"]
armsInterventionsModule.interventions[].name
```
Adding `&filter.overallStatus=TERMINATED` → `totalCount: 29`, with free-text reasons:
`"slow accrual led to early closure."` (NCT01683188), `"Slow accrual and changing treatment
landscape"` (NCT02414750).

**Evidence — cannot be keyed on a gene.** `?query.term=BRAF` → `totalCount: 1045`, whose top
hits are cfDNA/liquid-biopsy **biomarker** studies with interventions listed as
`"Blood sampling"` and `"Blood plasma and circulating tumor DNA (ctDNA)"`. Gene-keyed CT.gov
returns diagnostics, not a target pipeline.

**Change.** Get drug names from OT (D1) or ChEMBL (D2), then hop **per drug** to CT.gov for
sponsor and live status. ~2 h plus N extra calls. **Later upgrade**, unless sponsor is needed
tonight. Note OT's `trialStopReasonCategories` (D4) is *better* than CT.gov's free-text
`whyStopped` because it is already categorised.

**NCATS Inxight Drugs — skip.** `https://drugs.ncats.io/api/v1/substances?top=1` responds 200
with `total: 167131`, but it is keyed on substance/UNII with **no gene axis**; the search call
`/api/v1/substances/search?q=vemurafenib&top=1` returned 200 with an empty body. Wrong shape
for this ring.

### D4 — The right scalar is **two rings**, and the stop reasons are why.

**Evidence.** Measured across all of BRAF's trials, `trialStopReasonCategories`:
```
Insufficient_Enrollment 108 · Business_Administrative 50 · Negative 19 · Safety_Sideeffects 13
Study_Design 13 · Logistics_Resources 12 · Invalid_Reason 9 · Another_Study 5
Study_Staff_Moved 4 · Regulatory 3 · No_Context 2 · Uncategorised 1
```
**Only ~13% of stop events (`Negative` + `Safety_Sideeffects` = 32 of ~239) say anything about
the target or the drug.** A single ordinal carrying a bare "discontinued" marker would therefore
mislead on roughly seven of every eight terminations. This is the single most decision-relevant
number in this review.

**Change — two rings, not one ring with a marker.**

**Ring A — furthest phase reached** (ordinal; stable, well populated):
```
0 = none        (target present, drug count 0)
1 = preclinical / chemical probe only
2 = Phase 1
3 = Phase 2
4 = Phase 3
5 = Approved
```
Map from the `PHASE_*` / `APPROVAL` enum with an explicit lookup (M1).

**Ring B — current activity** (categorical, exactly three states):
```
active            = any trial RECRUITING / ACTIVE_NOT_RECRUITING / NOT_YET_RECRUITING
dormant           = drugs exist, nothing active
stopped for cause = any trialStopReasonCategories in {Negative, Safety_Sideeffects}
```
Compute Ring B only over rows where `source` supplies status (`AACT`); see D1's second gap.

This renders the discontinued-phase-3 vs actively-recruiting-phase-1 contrast as two readable
positions. One ring cannot carry it.

---

## 3. NOTE

### M9 / S8 — Licensing. (Full detail in §6.)

**Evidence.** HPA is **CC BY 4.0** and explicitly permits commercial use with citation, but
disclaims third-party constraints onto you. Open Targets is licensed **per source**: gnomAD CC0,
MGI CC-BY 4.0, GTEx CC-BY 4.0, Europe PMC **mixed CC-BY / CC-BY-NC / CC0**, and **Cancer Gene
Census** and **Genomics England PanelApp** are marked *"Commercial use for Open Targets"* — i.e.
**not transitive to you**. **ChEMBL is CC BY-SA 3.0 Unported** — attribution *and* **share-alike**.

**Change.** If the app touches workplace work, a human (legal/IP contact) must rule on
COSMIC/Cancer Gene Census, PanelApp and ChEMBL **before anything leaves the laptop**. Do not
guess this. The share-alike term is an additional reason to defer the ChEMBL swap (S5) past MVP.

### M10 — Omissions a target-prioritisation figure needs.

- No expression in the **target tissue at protein level** (IHC, not just RNA).
- **No ortholog conservation for the rodent ring** — OT `homologues` exposes
  `targetPercentageIdentity` / `queryPercentageIdentity` / `homologyType` / `speciesName` /
  `isHighConfidence`; a mouse knockout is uninterpretable at low identity. BRAF's
  `mouseOrthologMaxIdentityPercentage` = `0.6083550000000002` in `prioritisation`.
- **No provenance or date stamp per ring.** Every ring should carry its source and retrieval date.
- **No directionality** — is up-regulation the therapeutic direction? The figure currently
  cannot say.
- **No multiple-testing context** for the principal's own adjusted p on the figure.

### S3 — Brain subregions: Allen only if the region is one, and not tonight.

**Evidence.** GTEx has 13 coarse brain regions only. Allen's API answers:
`GET http://api.brain-map.org/api/v2/data/query.json?criteria=model::Gene,rma::criteria,[acronym$eq'BRAF']&num_rows=2`
→ `success: true, total_rows: 3`, `entrez_id: 673`, internal `id: 669`, `organism_id: 1`.
But expression values require `structure_unionize` queries against a separate structural
ontology, and the human cortical atlas is a small-donor legacy microarray resource.
**I did not verify the donor count** — my donor query returned empty (§7).

**Change.** Use only if the submitted region is genuinely a brain subregion. ~4–6 h.
**Later upgrade**, and verify donor N first.

### S9 — Better sources do not rescue the weighted sum.

All three defects in M3 survive every swap in this document. TDL (S1) helps most, because it is
the only axis with nothing missing. Do M3's rank/Pareto change regardless of sources.

### D6 — The drug drawer table.

Columns, for someone judging whether a target is already crowded:
```
drug name | modality (drugType) | action_type (ChEMBL) | furthest phase | current status
| indication | sponsor (CT.gov) | start / latest activity date | NCT id (link)
| stop-reason category where stopped
```
Sort by phase, then by recency. Put one summary line **above** the table —
*"18 agents, 5 active, 2 stopped for cause"* — which answers "is this crowded?" faster than
the table does. The drug list sits beside the papers in the same click-drawer.

### D7 — Keep the clinical ring out of the weighted sum.

It is now evidence-rich enough to stand as its own Pareto axis (M3). Averaging "approved" and
"recruiting" into a scalar destroys exactly the distinction D4 exists to preserve.

---

## 4. Sound as specified — build these unchanged

- **Ring 8** — the principal's own logFC / adjusted p.
- **Ring 9** — data completeness per gene.
- **The reweighting-stability flag** — flagging any gene whose top-10 position does not survive
  reweighting. Keep it; it is the best idea in the ranking section.
- **The missing-≠-low-risk rendering rule** — correct, and D5/S2 show exactly where it saves you.
- **All three stated traps are real and correctly anticipated:** HPA synonym matching
  (BRAF → HMG20B, KIAA1549), legitimately-null tissue-specific nTPM, and missing rendering
  distinctly from low.

---

## 5. Verified endpoint reference

All verified live 2026-09-23. No API keys required for any endpoint below.

### Open Targets GraphQL — `POST https://api.platform.opentargets.org/api/v4/graphql`

`Query` root fields: `meta, target, targets, disease, diseases, drug, drugs, search, facets,
mapIds, associationDatasources, interactionResources, geneOntologyTerms, variant, study,
studies, credibleSet, credibleSets, clinicalReport, clinicalReports`.

Confirmed `Target` fields used here:
```
approvedSymbol, approvedName, id, biotype, synonyms, symbolSynonyms, nameSynonyms
tractability            { label, modality, value }      # label, NOT id
prioritisation          { items { key value } }         # 18 keys; value is a STRING
geneticConstraint       { constraintType, exp, obs, score, oe, oeLower, oeUpper, upperBin6 }
depMapEssentiality      { tissueId, tissueName, screens { depmapId cellLineName geneEffect expression } }
isEssential                                             # BRAF: False
chemicalProbes          { id, isHighQuality, origin, mechanismOfAction, probesDrugsScore,
                          scoreInCells, scoreInOrganisms, probeMinerScore, drugId,
                          drugFromSourceId, targetFromSourceId, control, urls }   # BRAF: 20
safetyLiabilities       { event, eventId, datasource, effects, biosamples, studies,
                          literature, url }             # BRAF: 1 row — sparse, as suspected
mousePhenotypes         { modelPhenotypeLabel, modelPhenotypeId, modelPhenotypeClasses,
                          biologicalModels, targetInModel, targetInModelMgiId,
                          targetInModelEnsemblId }       # BRAF: 77
homologues              { homologyType, targetGeneSymbol, targetGeneId, speciesId, speciesName,
                          isHighConfidence, queryPercentageIdentity, targetPercentageIdentity }
drugAndClinicalCandidates { count rows { maxClinicalStage id drug clinicalReports diseases } }
baselineExpression, associatedDiseases, evidences, interactions, literatureOcurrences,
targetClass, subcellularLocations, pathways, tep, hallmarks, pharmacogenomics, credibleSets
```
**Does NOT exist on `Target`:** `knownDrugs`.

`Drug` fields: `id, name, drugType, maximumClinicalStage, description, synonyms, tradeNames,
crossReferences, molblock, parentMolecule, childMolecules, drugWarnings, mechanismsOfAction,
indications, adverseEvents, pharmacogenomics, similarEntities, literatureOcurrences`.
**Does NOT exist on `Drug`:** `isApproved`, `hasBeenWithdrawn`, `blackBoxWarning`,
`maximumClinicalTrialPhase`.

`ClinicalTargetFromTarget` (the `rows` type): `maxClinicalStage, id, drug, clinicalReports,
diseases`. **Does NOT exist:** `drugId`, `maxPhase`.

`ClinicalReport` fields: `trialPhase, phaseFromSource, clinicalStage, trialOverallStatus,
trialWhyStopped, trialStopReasonCategories, trialStartDate, trialOfficialTitle, trialDescription,
trialStudyType, trialPrimaryPurpose, trialNumberOfArms, trialLiterature, drugs, diseases,
sideEffects, countries, source, url, year, type, id, title, hasExpertReview, qualityControls`.

`ClinicalDiseaseListItem` fields: `diseaseFromSource, disease`. **NOT `name`.**

`Tractability` fields: `label, modality, value`. **NOT `id`.**
`Homologue` fields: as listed above. `TargetPrioritisation`: `items`.
`GeneticConstraint` is **not** introspectable by that type name (returned `null`).

BRAF live values — tractability `value: true` entries:
```
SM: Approved Drug | Structure with Ligand | High-Quality Ligand | High-Quality Pocket | Druggable Family
AB: UniProt loc high conf | GO CC high conf | Human Protein Atlas loc
PR: Literature | UniProt Ubiquitination | Database Ubiquitination | Half-life Data | Small Molecule Binder
```
BRAF `geneticConstraint`:
```
syn: exp 321.07000732421875, obs 281, score 1.2187999486923218, oe 0.8751999735832214, upperBin6 null
mis: exp 874.6500244140625,  obs 477, score 4.910299777984619,  oe 0.5453600287437439, upperBin6 null
lof: exp 92.18199920654297,  obs 14,  score 1,                  oe 0.151869997382164,  upperBin6 0
```
BRAF `safetyLiabilities`: `[("heart disease", "Force et al. (2011)")]`.
Zero-drug check: `ENSG00000198133` → `TMEM229B, drugCount = 0`; `ENSG00000157764` → `BRAF, 18`.
Trial status and stop-reason distributions: see D1 and D4.

### Pharos / TCRD — `POST https://pharos-api.ncats.io/graphql`
```graphql
{ target(q:{sym:"BRAF"}) { name sym tdl fam novelty publicationCount ppiCounts{value} } }
→ Tclin | Kinase | novelty 0.00253795 | publicationCount 2631 | ppiCounts [184,26,21,187]

{ targets { count facets(include:["Target Development Level"]) { facet values { name value } } } }
→ count 20412 ; Tbio 12303 | Tdark 5501 | Tchem 1904 | Tclin 704   (sums to 20412 — no gaps)
```
Other `Target` fields available: `tcrdid, uniprot, preferredSymbol, description, seq, dto, props,
synonyms, xrefs, generifCount, publications, nearestTclin, similarity, sequence_variants,
sequence_annotations, drgc_resources, affiliate_links, communityAPIs, communityData,
abstractWordCounts, dataVersions`.

### gnomAD — `POST https://gnomad.broadinstitute.org/api`
```graphql
{ gene(gene_symbol:"BRAF", reference_genome:GRCh38) {
    gene_id gnomad_constraint { pli oe_lof oe_lof_upper lof_z } } }
→ ENSG00000157764 | pli 0.999999999999986 | oe_lof 0.1529742462665005
  | oe_lof_upper (LOEUF) 0.23234095358685583 | lof_z 7.348724683473751
```

### GTEx v2 — `https://gtexportal.org/api/v2`
```
GET /reference/gene?geneId=BRAF
→ gencodeId "ENSG00000157764.12", gencodeVersion "v26", genomeBuild "GRCh38/hg38",
  entrezGeneId 673, chromosome chr7, start 140719327, end 140924764, strand "-"

GET /expression/medianGeneExpression?gencodeId=ENSG00000157764.12&datasetId=gtex_v8
→ data[] { median, tissueSiteDetailId, ontologyId (UBERON), datasetId, gencodeId,
           geneSymbol, unit:"TPM" }     # 54 tissues
  Adipose_Subcutaneous      12.5192  UBERON:0002190
  Adipose_Visceral_Omentum  10.2532  UBERON:0010414
  Adrenal_Gland              6.02665 UBERON:0002369
  Artery_Aorta              12.9639  UBERON:0001496
  Artery_Coronary           11.7971  UBERON:0001621

GET /expression/medianGeneExpression?gencodeId=ENSG00000157764.14&datasetId=gtex_v8
→ {"data":[], "paging_info":{"totalNumberOfItems":0}}    # WRONG VERSION FAILS SILENTLY

GET /dataset/tissueSiteDetail?itemsPerPage=3
→ vocabulary: tissueSiteDetailId, colorHex, colorRgb, datasetId, eGeneCount,
  expressedGeneCount, sGeneCount, samplingSite, rnaSeqSampleSummary{totalCount, male, female}
```

### Human Protein Atlas
```
GET https://www.proteinatlas.org/api/search_download.php?search=BRAF&format=json&columns=g,gs,eg,rnatsm&compress=no
→ [ {"Gene":"BRAF","Gene synonym":["BRAF-1","BRAF1"],"Ensembl":"ENSG00000157764",
     "RNA tissue specific nTPM":null},
    {"Gene":"HMG20B", …}, {"Gene":"KIAA1549", …},
    {"Gene":"RIPPLY1", …,"RNA tissue specific nTPM":{"kidney":"2.8","liver":"5.4"}}, … ]
```
Confirms both stated traps: synonym matching returns wrong genes; `null` ≠ zero.

### Europe PMC
```
GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=<q>&format=json&pageSize=1
→ { version, hitCount, nextCursorMark, nextPageUrl, request, resultList{result[]} }
hitCount:  "BDNF AND hippocampus" 48102 | 'GENE:"BDNF" AND hippocampus' 33396
           '"BDNF" AND "hippocampus"' 48102 | "BDNF" 115291
Also observed once: {"errCode":503,"errMsg":"Search service is temporarily unavailable."}
```

### PubTator3
```
GET https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/?text=%40GENE_BDNF
→ { facets:{ facet_fields:{ journal[], type[], year[] } }, page_size, current,
    count: 86493, total_pages: 8650 }
GET …?text=%40GENE_BDNF%20hippocampus  → count: 31934, total_pages 3194
year facet (gene+region): 2022→3957, 2023→3665, 2021→3329, 2024→2598, 2020→2501, …
Annotates genes, diseases, chemicals, species, variants, cell lines — NOT anatomy.
```

### IMPC Solr — `https://www.ebi.ac.uk/mi/impc/solr`
```
/genotype-phenotype/select?q=*:*&rows=0&wt=json                        → numFound 67350
/genotype-phenotype/select?q=*:*&rows=0&wt=json&json.facet={genes:"unique(marker_symbol)"}
                                                                        → genes 8429
/genotype-phenotype/select?q=marker_symbol:Bdnf&rows=2&wt=json          → numFound 0
/gene/select?q=marker_symbol:Bdnf&rows=1&wt=json                        → numFound 1
/statistical-result/select?q=marker_symbol:Bdnf&rows=1&wt=json           → 200, numFound 0
doc fields: allele_accession_id, allele_name, allele_symbol, assertion_type, colony_id,
            effect_size, intermediate_mp_term_id, marker_symbol
MouseMine: https://www.mousemine.org/mousemine/service/version → 503 (twice). Do not use.
```

### Alliance of Genome Resources
```
GET https://www.alliancegenome.org/api/gene/MGI:88127/phenotypes?limit=2 → total: 34
GET https://www.alliancegenome.org/api/gene/HGNC:1097
→ returns an RGD (rat) record: dataProvider RGD, referencedCurie "RGD:735645"
Payloads deeply nested under results[].subject.* — budget parsing time.
```

### Allen Brain Atlas
```
GET http://api.brain-map.org/api/v2/data/query.json?criteria=model::Gene,rma::criteria,[acronym$eq'BRAF']&num_rows=2
→ success true, total_rows 3, msg[]{ acronym, alias_tags, entrez_id 673, id 669, organism_id 1,
  ensembl_id, name, original_symbol }
Expression requires separate structure_unionize queries. Donor N NOT verified (§7).
```

### DepMap
```
GET https://depmap.org/portal/api/download/files → 200 but HTML bot-verification page, not JSON
GET https://depmap.org/portal/                    → 200
GET https://api.figshare.com/v2/articles/29409915 → 200 (bulk download route)
No per-gene JSON API. Use OT depMapEssentiality, or bulk + precompute.
```

### ChEMBL — `https://www.ebi.ac.uk/chembl/api/data`
```
GET /mechanism.json?target_chembl_id=CHEMBL5145&only=molecule_chembl_id,action_type,
    direct_interaction,disease_efficacy,max_phase,mechanism_of_action
→ total_count 14 ; { action_type "INHIBITOR", direct_interaction 1, disease_efficacy 1,
    max_phase 4, mechanism_of_action "Serine/threonine-protein kinase B-raf inhibitor",
    molecule_chembl_id "CHEMBL2105729" }
GET /target.json?target_components__accession=P15056&limit=1
→ total_count 6 ; CHEMBL5145, relationship "SINGLE PROTEIN", organism "Homo sapiens",
  target_component_synonyms[{component_synonym "BRAF", syn_type "GENE_SYMBOL"}]
GET /target.json?target_components__accession=Q9NWM3 → total_count 0   (no record at all)
GET /molecule/CHEMBL1229517.json?only=pref_name,max_phase,withdrawn_flag,first_approval,oral,molecule_type
→ { pref_name "VEMURAFENIB", max_phase "4.0" (STRING), withdrawn_flag false,
    first_approval 2011, oral true, molecule_type "Small molecule" }
GET /drug_indication.json?molecule_chembl_id=CHEMBL1229517
→ { drugind_id 24041, efo_id "EFO:0000756", efo_term "melanoma",
    indication_refs[{ ref_type "ClinicalTrials", ref_id "NCT00949702,NCT01006980,…" }] }
Licence: CC BY-SA 3.0 Unported  (SHARE-ALIKE — see §6)
```

### DGIdb — `POST https://dgidb.org/api/graphql`
```graphql
{ genes(names:["BRAF"]) { nodes { name interactions {
    drug { name approved } interactionScore interactionTypes { type } sources { sourceDbName } } } } }
→ BRAF: nodes 1, interactions 220
   ENZALUTAMIDE (approved true) score 0.0320799 source CKB-CORE   ← androgen-receptor antagonist!
   AVUTOMETINIB (approved false) score 0.1718571
   TRAMETINIB DIMETHYL SULFOXIDE (approved true) score 1.44883
     sources [PharmGKB, ClearityFoundationBiomarkers, CGI, DoCM, OncoKB]
Zero/missing indistinguishable:  TMEM229B → nodes 0 ; NOTAREALGENE123 → nodes 0
```

### ClinicalTrials.gov API v2 — `GET https://clinicaltrials.gov/api/v2/studies`
```
?query.intr=vemurafenib&fields=NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,
 Condition,InterventionName,StartDate,WhyStopped&pageSize=2&countTotal=true
→ totalCount 169 ; nested under protocolSection:
    identificationModule.nctId / briefTitle
    statusModule.overallStatus | .startDateStruct.date | .whyStopped
    sponsorCollaboratorsModule.leadSponsor.name
    conditionsModule.conditions[] | designModule.phases[]
    armsInterventionsModule.interventions[].name
&filter.overallStatus=TERMINATED → totalCount 29
   NCT01683188 whyStopped "slow accrual led to early closure."
   NCT02414750 whyStopped "Slow accrual and changing treatment landscape"
?query.term=BRAF → totalCount 1045 — top hits are cfDNA/biomarker studies,
   interventions "Blood sampling", "Blood plasma and circulating tumor DNA (ctDNA)"
   → gene-keyed querying does NOT return a target pipeline
```

### NCATS Inxight Drugs
```
GET https://drugs.ncats.io/api/v1/substances?top=1 → 200, total 167131 (substance/UNII keyed)
GET https://drugs.ncats.io/api/v1/substances/search?q=vemurafenib&top=1 → 200, empty body
No gene axis. Skip for this ring.
```

---

## 6. Licensing — what is safe, and what a human must check

**Verified from the licence pages themselves.**

| Source | Licence as published | Use in a workplace app |
|---|---|---|
| Human Protein Atlas | **CC BY 4.0** for all copyrightable parts; page states they "encourage you to use our resource for your research and commercial purposes" with proper citation | OK with citation; HPA explicitly disclaims third-party constraints **onto you** |
| ChEMBL | **CC BY–Share Alike 3.0 Unported** (web interface under EBI Terms of Use) | **Share-alike — needs a decision before shipping.** Extra reason to defer S5 |
| gnomAD (via OT listing) | CC0 1.0 | OK |
| MGI (via OT listing) | CC-BY 4.0 | OK with attribution |
| GTEx (via OT listing) | CC-BY 4.0 | See conflict below |
| Ensembl, Expression Atlas, eQTL Catalogue, Cancer Genome Interpreter | CC-BY 4.0 | OK with attribution |
| ClinGen, FAERS, HPO, IntOGen | CC0 1.0 | OK |
| GWAS Catalog | EMBL-EBI terms of use (summary statistics CC0 1.0) | Read the terms |
| Gene2Phenotype, IntAct | EMBL-EBI terms of use | Read the terms |
| Europe PMC | **mixed** CC-BY / CC-BY-NC / CC0 per publication | Per-record; **CC-BY-NC records are not safe for commercial use** |
| **Cancer Gene Census (COSMIC)** | marked *"Commercial use for Open Targets"* | **NOT transitive to you** |
| **Genomics England PanelApp** | marked *"Commercial use for Open Targets"* | **NOT transitive to you** |

HPA's own third-party list (constraints it passes through): Ensembl user terms, UniProt
CC BY 4.0, **GTEx user terms**, FANTOM CC BY 4.0, TCGA user terms, CPTAC CC-BY 4.0,
Tabula Sapiens user terms, AlphaFold 2 CC-BY 4.0, **AlphaFold 3 CC BY-NC-SA 4.0**,
AlphaMissense CC BY 4.0, IntAct user terms, BioGRID MIT, BioPlex user terms.

**A human must check — do not let the build session guess these:**

1. **COSMIC / Cancer Gene Census** and **Genomics England PanelApp**. Both are marked
   "Commercial use for Open Targets", which reads as a permission scoped to OT and **not**
   inherited by downstream redistributors. If `isCancerDriverGene` or any CGC-derived field
   reaches the app, this needs a ruling.
2. **ChEMBL's share-alike.** Whether redistributing ChEMBL-derived values in an internal
   workplace tool triggers the SA obligation.
3. **The GTEx conflict I could not resolve.** Open Targets' licence page lists GTEx as
   **CC-BY 4.0**; HPA's third-party table lists GTEx under **"user terms"**. These disagree, and
   S2 makes GTEx a primary dependency. `https://gtexportal.org/home/license` returned no
   extractable licence text on my fetch — so **this is unverified and must be read by a human**.
4. **Europe PMC per-record licences** if any abstract text is cached or redisplayed —
   CC-BY-NC records exist in the mix.
5. Whether "may be used at the principal's workplace" counts as commercial use at all. That is
   an institutional question, not a technical one.

**Position:** nothing here blocks an overnight MVP **on the principal's own laptop**. Items 1–3
must be resolved **before the app is shared inside the workplace**.

---

## 7. Not verified — do not treat as checked

- **Allen Brain Atlas human donor count.** My donor query
  (`criteria=model::Donor,rma::criteria,products[abbreviation$eq'HumanMA']`) returned an empty
  result — the criteria were wrong. The gene endpoint works; the donor N and the atlas's
  suitability are unverified (S3).
- **GTEx licence terms.** `https://gtexportal.org/home/license` yielded no extractable licence
  text. Two other sources disagree about it (§6, item 3).
- **HPA Tau scores as data.** Tau is listed in HPA's own search-field list
  (`Tissue Tau score (RNA)`, `Brain Tau score (RNA)`, `Cell type Tau score (RNA)`); I did not
  pull Tau values through the API (M8).
- **Paralog count as a live query.** `homologues` fields are confirmed by introspection and
  `paralogMaxIdentityPercentage` (BRAF = `0`) by live query, but I did not run a `homologues`
  query returning paralog rows. The count must be **derived** by filtering, not read off a field.
- **IMPC `statistical-result` core contents.** Endpoint returns 200; I did not evaluate whether
  it is the better core for this purpose.
- **Whether OT's `prioritisation` keys are stable across releases.** Treat the 18 keys as
  version-dependent and pin/record the release.

---

## 8. One-night build order

1. OT one call per gene: `prioritisation` + `tractability` + `safetyLiabilities` +
   `depMapEssentiality` + `mousePhenotypes` + `drugAndClinicalCandidates{…clinicalReports{…}}` (M2, D1)
2. Pharos one call: `tdl`, `novelty`, `publicationCount` (S1)
3. gnomAD one call: `pli`, `oe_lof_upper` (S4)
4. GTEx two calls: resolve versioned gencodeId, then median expression (S2)
5. PubTator3 one call: `count` + `year` facet (S7)
6. Alliance one call per gene for rodent phenotype count (S6)
7. Ranking: rank-within-set + Pareto non-dominated, no imputation, completeness rendered (M3)
8. Two clinical rings, not one (D4). Rodent ring labelled per M4.

Deferred to a later upgrade: ChEMBL mechanism filter (D2/S5, plus licensing), CT.gov sponsor hop
(D3), bulk DepMap (S4), Allen Brain (S3), HPA protein-level IHC (S2).
