# QA review — target-prioritisation Circos app

Reviewer: QA lead. Date: 2026-09-23 (probes run 2026-09-24T04:00Z).
All status codes, response bodies and timings below are from **live calls made during this review**, not from documentation. Anything I did not verify is marked **UNVERIFIED** in bold.

Scope note: the principal has since said the data sources are not fixed — Pharos/TCRD, DGIdb, ChEMBL, GTEx, Allen Brain Atlas, DepMap and gnomAD direct, Alliance of Genome Resources, IMPC and PubTator are all in play. Section 4 at the end classifies every finding as source-specific or portable. The BLOCKING/SHOULD-FIX items below are written against the plan as submitted.

---

## 1. BLOCKING

### B1. The Open Targets fields in the plan do not exist. Every OT-backed ring returns nothing, and the failure is a hard 400, not an empty result.

**Evidence.** The plan's query, sent verbatim:

```
POST https://api.platform.opentargets.org/api/v4/graphql
Content-Type: application/json
{"query":"{target(ensemblId:\"ENSG00000157764\"){approvedSymbol tractability{id modality value} knownDrugs{count} safetyLiabilities{event}}}"}
```

→ **HTTP 400**, 673 B, 0.245 s:

```json
{"errors":[
 {"message":"Cannot query field 'id' on type 'Tractability'. (line 1, column 66)","locations":[{"line":1,"column":66}]},
 {"message":"Cannot query field 'knownDrugs' on type 'Target'. (line 1, column 85)","locations":[{"line":1,"column":85}]}
]}
```

Fields the plan assumed, and their real status:

| Plan assumed | Real status |
|---|---|
| `knownDrugs{count}` | **does not exist** on `Target` |
| `tractability{id}` | **does not exist** on `Tractability` |
| `maxClinicalPhase` | **does not exist** on `Target` |
| `safetyLiabilities{event}` | exists, works |
| DepMap essentiality | exists as `depMapEssentiality`, `isEssential` |
| gnomAD constraint | exists as `geneticConstraint` |

Real `Target` fields (from `{__type(name:"Target"){fields{name}}}`):

```
approvedSymbol, nameSynonyms, tractability, functionDescriptions, homologues,
subcellularLocations, geneticConstraint, hallmarks, alternativeGenes, pathways,
obsoleteSymbols, obsoleteNames, canonicalTranscript, approvedName, transcriptIds,
genomicLocation, targetClass, id, tep, chemicalProbes, safetyLiabilities, biotype,
synonyms, geneOntology, transcripts, proteinIds, symbolSynonyms, dbXrefs,
credibleSets, similarEntities, literatureOcurrences, evidences, interactions,
mousePhenotypes, baselineExpression, associatedDiseases, prioritisation,
isEssential, depMapEssentiality, pharmacogenomics, proteinCodingCoordinates,
drugAndClinicalCandidates, associationTimeSeries
```

Real `Tractability` fields: `label`, `modality`, `value` (no `id`).

Real `Query` root fields:

```
meta, target, targets, disease, diseases, drug, drugs, search, facets, mapIds,
associationDatasources, interactionResources, geneOntologyTerms, variant, study,
studies, credibleSet, credibleSets, clinicalReport, clinicalReports
```

Working replacement query, confirmed HTTP 200:

```
{target(ensemblId:"ENSG00000157764"){
  approvedSymbol
  tractability{label modality value}
  safetyLiabilities{event}
  geneticConstraint{constraintType upperBin6}
  isEssential
  depMapEssentiality{tissueName}
  obsoleteSymbols{label}
  mousePhenotypes{targetInModelMgiId modelPhenotypeLabel}
  drugAndClinicalCandidates{count}
}}
```

`tractability` returns a flat array of `{label, modality, value}` across three modalities — SM (small molecule), AB (antibody), PR (PROTAC/other). For BRAF: `{"Approved Drug","SM",true}`, `{"Advanced Clinical","SM",false}`, `{"Phase 1 Clinical","SM",false}`, `{"Structure with Ligand","SM",true}`, `{"High-Quality Ligand","SM",true}`, `{"High-Quality Pocket","SM",true}`, `{"Med-Quality Pocket","SM",false}`, `{"Druggable Family","SM",true}`, then AB entries (`"UniProt loc high conf"` true, `"GO CC high conf"` true, `"Human Protein Atlas loc"` true, others false), then PR entries. **There is no single scalar "tractability" value** — the ring must define its own reduction over this array (e.g. highest SM bucket reached), and that reduction is a stated methods choice, not a field read.

**Change / test.** Pin the query text in source. Add a startup contract test that issues the exact production query for one known gene and **fails the run loudly on any non-200 or any `errors` key**. A 400 must abort, never render an empty ring. Re-run introspection as part of that test and diff the field list against a checked-in snapshot.

---

### B2. `targets()` silently drops genes it cannot find, so positional zipping mislabels every ring.

This is the single highest-risk defect in the plan: it produces a plausible, fully-rendered, wrong figure.

**Evidence.** Requested five Ensembl IDs, deliberately not in sorted order, one of them nonexistent-but-well-formed:

```
requested: ENSG00000157764, ENSG00000999999, ENSG00000141510, ENSG00000012048, ENSG00000146648
returned : (ENSG00000157764, BRAF), (ENSG00000141510, TP53), (ENSG00000012048, BRCA1), (ENSG00000146648, EGFR)
counts   : requested 5, returned 4
```

HTTP 200. No error, no null placeholder, no warning. The missing element is **removed from the array**; the relative order of survivors is preserved. Zip-by-index therefore shifts every gene after the gap by one position: TP53's values land on BRAF's spoke, BRCA1's on TP53's, and the last spoke gets no data.

Contrast, same endpoint, single-target form: a well-formed nonexistent ID with valid field names returns **HTTP 200 `{"data":{"target":null}}`** — an explicit null. The batch form is the dangerous one; the singular form is safe.

Batch capacity is fine: 50 IDs via `targets(ensemblIds:[...])` → **HTTP 200, 0.59 s, 50 returned for 50 requested**, `errors: None`. Duplicate IDs are echoed back once per occurrence. Latency is not the problem; alignment is.

**Change / test.** Never zip. Build `{returned_id: record}` and look up by requested ID. Assert `set(returned) ⊆ set(requested)`. Any requested ID absent from the response is rendered as explicit `missing`, never omitted and never defaulted.

Regression test, must be in the overnight suite:

> Submit the exact five IDs above. Assert: 5 spokes rendered; the spoke for `ENSG00000999999` carries status `missing`; `BRAF`'s tractability values equal those from a single-target query for `ENSG00000157764`.

The second assertion is the one that catches the off-by-one — a count check alone passes while the labels are wrong.

---

### B3. Europe PMC returns HTTP 200 with a silently and massively wrong hit count for a malformed query.

**Evidence.** Base: `https://www.ebi.ac.uk/europepmc/webservices/rest/search`

| Request | Result |
|---|---|
| `?query=BRAF%20AND%20knockout&format=json&pageSize=1` | HTTP 200, 1291 B, **`hitCount: 8418`**, echoes `request.queryString: "BRAF AND knockout"` |
| `?query=%22BRAF&format=json` (unbalanced double quote) | **HTTP 200**, **`hitCount: 101223`**, echoes `request.queryString: "\"BRAF"` |
| `?query=ZZZNOTAGENE123&format=json&pageSize=1` | HTTP 200, `hitCount: 0`, `resultList.result: []` |
| `?format=json` (no query at all) | HTTP 400 `{"timestamp":"2026-09-24T04:03:55.015+00:00","status":400,"error":"Bad Request","path":"/europepmc/webservices/rest/search"}` |

**8418 → 101223 is 12.0× inflation from one unbalanced quote, with a 200 and no error field.** A gene symbol containing an apostrophe, a stray quote from CSV parsing, or a naive f-string would inflate the literature ring for that gene and the figure would look entirely reasonable. API version echoed: 6.9.

PubMed is worse in the same direction: `esearch.fcgi?db=notadb&term=BRAF&retmode=json` → **HTTP 200** with `{"esearchresult":{"ERROR":"Invalid db name specified: notadb"}}`. The error is in the body of a 200. Valid control: `db=pubmed&term=BRAF` → HTTP 200, `count: "28832"`, `querytranslation: "\"BRAF\"[All Fields]"`.

**Change / test.** Three rules, all cheap:

1. Europe PMC echoes your query back in `request.queryString`. **Assert it is byte-identical to what you sent.** This is the only reliable malformed-query detector the API offers.
2. Treat the presence of an `ERROR` key anywhere in an NCBI JSON response as a hard failure regardless of status code.
3. Never branch on HTTP status alone for any literature source.

Test: submit `"BRAF` (leading quote) and assert the run fails rather than returning 101223.

Also assert on PubMed's `querytranslation` — if it does not contain the symbol you asked for, the term was silently reinterpreted.

---

### B4. HPA free-text search returns other genes, explodes on short symbols, and blows the 60 s budget on a single gene.

**Evidence.** Base: `https://www.proteinatlas.org/api/search_download.php`

| Request | Result |
|---|---|
| `?search=BRAF&format=json&columns=g,gs,eg,rnatsm,rnatss&compress=no` | HTTP 200, 4159 B — returns **BRAF, HMG20B, KIAA1549, RIPPLY1, ALDH16A1, APELA, BAMBI, CNPY1, COL22A1, COQ6, DOCK5, FOXJ1, GSE1, KLHL40, NCLN, OTOGL, PES1, PHF21A, PLXNA3, RAB18, SLC18B1, TBC1D32, TMX…** (HMG20B matches on synonyms `BRAF25`/`BRAF35`) |
| `?search=INS&…` | HTTP 200, **8441 rows, 7.89 s** — for one gene |
| `?search=ALB&…` | HTTP 200, 106 rows, 0.96 s |
| `?search=GAPDH&…` | HTTP 200, 3 rows, 0.92 s |
| `?search=ZZZNOTAGENE123&…` | HTTP 200, **2 B, `[]`** |
| `?search=BRAF&columns=g,notacolumn&…` | **HTTP 200**, requested column **silently omitted**, no error: `[{"Gene":"BRAF"},{"Gene":"HMG20B"},…]` |
| no parameters at all | **HTTP 400 with 117059 B of HTML** (the full Human Protein Atlas web page, not JSON) |

Two independent hazards. First, `INS` alone costs 7.9 s — a 50-gene run with several short symbols cannot meet the 60 s cold target if HPA calls are serialised. Second, a typo in a column name produces a 200 with that column quietly absent, which reaches the renderer as "no data for every gene" and paints an entire ring as missing.

**Change / test.**
- Exact-symbol filtering is confirmed mandatory (but see B5 — it must be applied to the *resolved current* symbol).
- Run HPA calls fully parallel with a per-call timeout; treat a timeout as `no_data`, not as zero.
- **Assert the set of keys on the returned rows equals the set of requested columns**, and fail the run on mismatch. This is a two-line check that prevents a whole blank ring.
- Parse defensively: a 400 from this endpoint is HTML, so never `json.loads` without checking `Content-Type` and status first.
- **UNVERIFIED: rate limiting.** My 25-rapid-call probe did not complete (exec host dropped: `COMPANION_APP_UNAVAILABLE`). **No source in this review has a measured rate limit.** With parallel fetches this must be measured before the unattended run — a 429 storm at 2 a.m. produces an all-missing figure.

---

### B5. Exact-symbol filtering silently deletes renamed genes. The plan's two "already handled" traps combine into silent loss.

**Evidence.** `?search=SEPT9&format=json&columns=g,eg,rnatss&compress=no` → HTTP 200, exactly **one row**:

```json
[{"Gene":"SEPTIN9","Ensembl":"ENSG00000184640","RNA tissue specificity score":null}]
```

The row exists, is correct, and is **the only row** — but its `Gene` value is `SEPTIN9`. An exact match on the user's input string `SEPT9` yields **zero rows**, so the gene disappears from the figure with no error anywhere. Measured: `SEPT9` → rows=1, exact=0. `SEPTIN9` → rows=1, exact=1.

Same class: `MARCH1` → `MARCHF1`. These are the 2016 and 2020 HGNC renames, and they are exactly the symbols most likely to appear in an older DE table.

**Change / test.** Resolve to the current approved symbol **before** querying HPA, then exact-match on the resolved symbol. Verified resolver behaviour (OT `mapIds`, see B6):

```
'BRAF'            -> 1 hit  ENSG00000157764 BRAF
'SEPT9'           -> 1 hit  ENSG00000184640 SEPTIN9
'MARCH1'          -> 1 hit  ENSG00000145416 MARCHF1
'TP53'            -> 1 hit  ENSG00000141510 TP53
'CDKN2A'          -> 1 hit  ENSG00000147889 CDKN2A
'HGNC:1097'       -> 1 hit  ENSG00000157764 BRAF
'ENSG00000157764' -> 1 hit  ENSG00000157764 BRAF
'braf'            -> 1 hit  ENSG00000157764 BRAF   (case-insensitive)
'9-Sep'           -> 0 hits
```

Test: a DE table containing `SEPT9` and `MARCH1` must render both, each flagged `renamed`, with old and new symbol shown.

---

### B6. Identifier resolution: one resolver, before any ring, with explicit ambiguity and no-match states.

**Verified.** OT `mapIds` resolves symbols, aliases, obsolete symbols, HGNC IDs, Ensembl IDs and lowercase input to Ensembl gene IDs, all with 1 hit, in one call — see the table in B5. Query form:

```
{mapIds(queryTerms:["SEPT9"],entityNames:["target"]){mappings{term hits{id name entity}}}}
```

**Design.**
- **One resolver for the whole app.** Resolution is the join key for every ring, not a per-ring concern. Ensembl gene ID is the canonical internal key.
- Resolve once per run, cache by input string, and carry `(input_string, resolved_ensembl_id, approved_symbol, status)` through every downstream call.
- **On multiple hits (ambiguity):** do not pick the first. Status `ambiguous`, gene excluded from ranking, listed in the ledger with all candidate IDs and symbols so the user can disambiguate and re-run. Silently choosing a winner here is how a figure becomes about the wrong gene.
- **On zero hits:** status `unresolved`, rendered as an explicit `missing` spoke, listed in the ledger. Never dropped.
- **On Excel corruption specifically** (`9-Sep` → 0 hits): detect by regex *before* resolution and assign status `excel_corrupt` with its own message, because the user's fix is different — they must re-export the source file, not correct a symbol. See B7.
- **UNVERIFIED: symbol → UniProt and symbol → ChEMBL.** I did not probe these. OT `Target` exposes `proteinIds` and `dbXrefs` (both present in the introspected field list) which are the likely route, and UniProt's own ID-mapping service is the alternative. If ChEMBL is adopted for the drug ring, **probe this before the build** — a second ID space is a second silent-loss surface (see section 4).

---

### B7. DE input: the full failure list, with the required behaviour for each, and a mandatory drop ledger.

Silent gene loss is the worst outcome in this app. The rule below is non-negotiable and makes every case auditable.

**The invariant: `input_rows == rendered_spokes + ledger_entries`, asserted at render time, run aborts if it fails.**

| Case | Behaviour | Detail |
|---|---|---|
| Gene symbol synonyms / aliases | **Coerce** → `ok` | Resolver maps to approved symbol; record both |
| Deprecated symbols (`SEPT9`, `MARCH1`, `CDKN2`) | **Coerce** → flag `renamed` | Verified working; show old → new in the ledger |
| Excel date corruption (`9-Sep`, `1-Mar`, `2-Mar`, `1-Dec`) | **Reject** → flag `excel_corrupt` | **Unrecoverable** — `9-Sep` → 0 hits. Detect by regex `^\d{1,2}-(Jan\|Feb\|Mar\|Apr\|May\|Jun\|Jul\|Aug\|Sep\|Oct\|Nov\|Dec)$` (and the `Sep-09` variant). Message must say "re-export with the symbol column as Text", not "gene not found" |
| Ensembl IDs mixed with symbols | **Accept directly** → `ok` | Verified: `ENSG00000157764` resolves. Strip any version suffix (`.14`) before use |
| Mouse symbols pasted (`Braf`, `Trp53`) | **Flag** `possible_mouse`, do not auto-convert | Title-case-with-lowercase-tail heuristic. `braf` resolves to human BRAF case-insensitively, so casing alone cannot be trusted to reject — warn the user rather than silently orthologue-mapping, which would change the science |
| Duplicate rows (same gene twice) | **Coerce** → keep max abs(logFC), flag `dropped_dup` | Report which row was kept and its logFC |
| Blank / missing padj | **Reject the row** → `dropped_no_padj` | **Do not coerce to 0 or 1.** A padj coerced to 0 ranks the gene top; coerced to 1 buries it. Either is a silent scientific error |
| Non-numeric logFC (`NA`, `#DIV/0!`, `Inf`, empty) | **Reject the row** → `dropped_bad_logfc` | `Inf` and `-Inf` rejected explicitly — they break both ranking and colour scaling |
| Comma decimal separators (`1,23`) | **Coerce** | Detect per column: if every value matches `^-?\d+,\d+$`, treat comma as decimal. If a column mixes both, **reject the file** — guessing per-row is how you get 123 from 1,23 |
| 5,000-row lists | **Reject with a clear cap** | Cap at ~500 genes. 5000 spokes is not a readable circle and not a 60 s run. Offer "top N by padj" as an explicit, labelled user choice |
| Single gene | **Accept** | Degenerate circle must render without divide-by-zero in the angular layout; explicitly test n=1 and n=2 |
| BOM (`﻿`) | **Coerce** — strip | Otherwise the first header becomes `﻿gene` and the whole column is lost |
| Encoding (UTF-8 / UTF-16 / Latin-1) | **Coerce** — sniff, decode, else reject with a named encoding error | Never silently replace undecodable bytes |
| Whitespace, case, quotes around symbols | **Coerce** — strip | |
| Header row missing or misnamed | **Reject the file** | Require explicit column mapping rather than positional guessing |

**How the user learns which genes were dropped.** Every run emits a per-gene ledger — `input → resolved → status` where status ∈ `{ok, renamed, ambiguous, excel_corrupt, possible_mouse, unresolved, dropped_dup, dropped_no_padj, dropped_bad_logfc}`. It appears:

1. as a banner above the figure — "50 genes in, 46 rendered, 4 not shown" — with the count of each non-`ok` status, never collapsed to a single number;
2. as a table in the UI, sorted with problems first;
3. **as a sheet/section in the CSV export**, so the dropped genes travel with the figure;
4. stamped on the figure itself: the rendered gene count and the dropped count, so a screenshot alone is self-describing. This matters specifically because the morning review is a screenshot.

---

## 2. SHOULD-FIX

### S1. "Missing" vs "low" must be proven by an automated check, not asserted — and HPA's nulls do not mean one thing.

**Evidence.** Null is not a single state in HPA:

| Gene | RNA tissue specificity score | RNA tissue specific nTPM |
|---|---|---|
| GAPDH | `"4"` | `{"skeletal muscle":"25843.3","tongue":"17635.1"}` |
| INS | `"135"` | `{"pancreas":"12197.1"}` |
| ALB | `"134"` | `{"liver":"198523.8"}` |
| **MARCHF1** | **`null`** | **`{"lymphoid tissue":"29.3","retina":"21.3"}`** |
| **SEPTIN9** | **`null`** | **`null`** |
| BRAF | `null` | `null` |

MARCHF1 has a null score but real expression data. SEPTIN9 has neither. **These are different states and must not collapse to the same cell colour.** The plan's stated trap ("ubiquitous genes have null tissue-specific nTPM which is not a zero") is real but incomplete — it treats null as one thing.

**Change.** Model four distinct cell states, each with its own visual treatment and legend entry:

- `value` — a number, on the colormap
- `zero` — measured, genuinely zero, on the colormap's low end
- `not_tissue_enriched` — measured, no tissue-specific enrichment (ubiquitous); a distinct neutral fill, **not** the low end
- `no_data` — never measured / gene absent from the source; hatched or grey, **outside the colormap entirely**

**Automated check (this is the answer to "can an SVG be checked without eyes"): yes.** The SVG is XML. Parse it and assert:

1. **Cell count identity** — the number of `<path>`/`<rect>` cells per ring equals the number of rows in the exported CSV for that ring. Catches dropped and duplicated cells.
2. **Missing-fill disjointness** — collect the fill colour of every cell whose CSV status is `no_data`; collect the set of fills produced by the colormap; assert the intersection is empty. This is the mechanical proof that missing is visually distinct from low, and it survives any future palette change.
3. **Value → colour monotonicity** — for each ring, sort cells by CSV value and assert their fills follow the colormap in order. Catches a miscolouring or a reversed scale.
4. **Label correspondence** — the *k*-th spoke label in the SVG equals the *k*-th gene in the CSV. **This is the check that catches B2 in the rendered artefact** even if the join logic regresses.
5. **Legend completeness** — every distinct state present in the CSV has a legend entry in the SVG.
6. Golden-file test: a fixed fixture input must produce a byte-stable SVG (with any timestamp/UUID masked). Any diff is a deliberate review, not a surprise.

Run checks 1–5 at the end of every render, including the unattended overnight run, and write PASS/FAIL into the run log next to the figure. A failed invariant must be visible on the morning screenshot, not buried in a log.

### S2. LLM triage is a nondeterministic scientific claim and must be reproducible and falsifiable.

**What must be pinned:** exact model ID (not an alias that silently re-points), `temperature=0`, `top_p`, max tokens, seed if the provider offers one, and the **SHA-256 of the prompt template**. All of it written into the run manifest and stamped in the figure's provenance footer.

**What must be logged, per verdict:** input PMID, the exact abstract text passed (or its hash plus a retrievable reference), the full raw model response before parsing, the parsed verdict, the prompt hash, the model ID, and a UTC timestamp. Without the raw response you cannot distinguish a model error from a parser error.

**How a hallucinated rescue claim gets caught.** The triage output is `rescue / attempted-no-rescue / irrelevant` — a claim about what an experiment showed. Requirements:

1. **Every `rescue` verdict must carry a supporting quoted span copied verbatim from the abstract.** Post-verify by exact substring match against the source text; if the quote is not literally present, the verdict is **discarded as hallucinated**, not shown. This is the single highest-value check and it is cheap.
2. **Closed-world constraint** — the model sees only the abstract and is instructed to answer `irrelevant` when the abstract does not address rescue. Any verdict citing a fact absent from the input fails check 1 automatically.
3. **Display the verdict as a claim with its evidence, never as a fact.** The drawer shows the quoted span, the PMID as a resolvable link, and the model ID inline.
4. **Never let triage feed the ranking.** It is a click-through annotation only. A nondeterministic step must not move a gene's rank, or the figure is not reproducible.
5. **A labelled gold set of ~20 abstracts** with human verdicts, run every build; log precision/recall on `rescue`. Recall matters less than precision here — a missed paper is a gap, a false rescue is a wrong scientific claim.
6. **Reproducibility test:** run the same 10 abstracts twice and assert identical verdicts. At temperature 0 this should hold; if it does not, that fact itself must be surfaced on the figure as a caveat rather than hidden.
7. Cache keyed on `(pmid, prompt_hash, model_id)` so a prompt or model change invalidates cleanly instead of serving stale verdicts.

### S3. Named gene fixtures, with verified values.

All values below were measured in this review and are usable as expected-value baselines for the OT/HPA stack.

| Gene | Ensembl | What it tests | Verified values |
|---|---|---|---|
| **BRAF** | ENSG00000157764 | Well-drugged target; the happy path; also the synonym-pollution case | safety=1, drugs=18, isEssential=False, depmap=29, mousePheno=77, obsolete=[]; HPA score null / nTPM null; free-text search returns 23+ other genes |
| **KCNQ1** | ENSG00000053918 | Rich safety record — the safety ring's high end | **safety=11**, drugs=7, isEssential=False, depmap=29, mousePheno=72, obsolete=['LQT','KCNA9'] |
| **OR51E2** | ENSG00000167332 | No safety record, no drugs, minimal rodent data — the "sparse but present" case | safety=0, drugs=0, isEssential=False, depmap=29, mousePheno=3 |
| **SEPTIN9** (input as `SEPT9`) | ENSG00000184640 | Deprecated symbol + the Excel-corrupting symbol + null/null specificity | obsolete=['MSF','SEPT9']; HPA `search=SEPT9` → 1 row named SEPTIN9, exact-match=0; score null, nTPM null; safety=0, drugs=0, mousePheno=3 |
| **MARCHF1** (input as `MARCH1`) | ENSG00000145416 | Second rename; **null score with non-null nTPM** — the null≠null case for S1 | score null, nTPM `{lymphoid tissue:29.3, retina:21.3}` |
| **`9-Sep`** | — | Excel corruption, unrecoverable | `mapIds` → **0 hits**; must be regex-detected, never "not found" |
| **CDKN2A** | ENSG00000147889 | Obsolete aliases, paralog/locus complexity, heavy rodent data | obsolete=['CDKN2','MLM'], mousePheno=87, safety=0, drugs=0 |
| **MIR29B2CHG** (input as `C1orf53`) | ENSG00000203709 | Obsolete symbol resolving to a **different current name**; non-coding; **no data in any ring** | obsolete=['C1orf132'], **isEssential=None**, **depmap=0**, **mousePheno=0**, safety=0, drugs=0 |
| **GAPDH** | ENSG00000111640 | Ubiquitous housekeeping, low specificity score | score="4", nTPM `{skeletal muscle:25843.3, tongue:17635.1}`, 3 rows |
| **INS** | ENSG00000254647 | Highly tissue-specific **and** the short-symbol performance bomb | score="135", nTPM `{pancreas:12197.1}`, **8441 rows, 7.89 s** |
| **ALB** | ENSG00000163631 | Tissue-specific, very high nTPM — colour-scale outlier | score="134", nTPM `{liver:198523.8}`, 106 rows |
| **ENSG00000999999** | — | Well-formed but nonexistent — the B2 batch-drop case | batch: dropped silently; single: `{"target":null}` |
| **TP53 / BRCA1 / EGFR** | ENSG00000141510 / ENSG00000012048 / ENSG00000146648 | Batch-alignment companions for the B2 regression test | — |

`MIR29B2CHG` is the most valuable single fixture: `isEssential=None`, `depmap=0`, `mousePheno=0`, `drugs=0`, `safety=0` — it exercises every ring's missing-data path at once, and its `no_data` cells are the ones check 2 in S1 tests against the colormap.

### S4. IMPC/MGI as planned does not work, and returns a silent zero.

**Evidence.**

| Request | Result |
|---|---|
| `https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select?q=marker_symbol:Braf&rows=2&wt=json` | HTTP 200, 192 B, **`numFound: 0`, `docs: []`**, `responseHeader.status: 0` — no error |
| `https://www.gentar.org/publicapi/genes/BRAF` | HTTP 200, **55707 B of HTML** (Angular app shell), not JSON |
| `https://www.informatics.jax.org/api/v1/marker/summary?symbol=Braf` | HTTP 200, **58275 B of HTML**, not JSON |
| OT `target(...){mousePhenotypes{targetInModelMgiId modelPhenotypeLabel}}` | **works** — BRAF → MGI:88190 with 77 phenotype rows |

Two of the three planned endpoints return HTML with a 200 — `json.loads` will throw at 2 a.m., or worse, a lenient parser will yield an empty result. The Solr endpoint returns a clean, well-formed **zero**, which is indistinguishable from "this gene has no knockout phenotype" but is in fact a query or endpoint problem.

**Also, a counting trap.** BRAF's OT `mousePhenotypes` includes the label **`"no abnormal phenotype detected"`** alongside real phenotypes. **Counting rows is not counting abnormal phenotypes.** A naive `len(mousePhenotypes)` ring scores a gene with a single "no abnormal phenotype detected" record identically to a gene with one real phenotype, and both above a gene with none — which inverts the meaning for the reader.

**Change.** Use OT `mousePhenotypes` for the rodent ring; it is one call, already in the batch, and verified working. Filter out `"no abnormal phenotype detected"` before counting, and expose "phenotyped but normal" as its own state distinct from `no_data`. If a direct IMPC source is wanted later, it needs its own endpoint discovery and its own silent-zero test — do not attempt it overnight.

---

## 3. NOTE

- **N1. Every source in this review violates "HTTP status tells you what happened."** Confirmed 200-with-wrong-answer: Europe PMC (inflated count), PubMed (`ERROR` in body), HPA (silently dropped column), IMPC Solr (silent zero), OT batch (silently short array). Confirmed 400-with-HTML: HPA with no params. **House rule: validate response shape on every call; status code is necessary, never sufficient.**
- **N2. OT error shapes are not uniform.** Field errors return `{"errors":[…]}`; a GraphQL syntax error returns a **different top-level key**, `{"syntaxError":"…","locations":[…]}`. An error handler that only checks for `errors` will treat a syntax error as success with no data.
- **N3. OT `targets()` at 50 IDs took 0.59 s.** The 60 s cold budget is threatened by HPA (7.9 s for one short symbol) and the literature calls, not by Open Targets. Parallelise HPA and literature; OT can stay a single batched call.
- **N4. Cache keys must include the source schema version and the prompt hash**, not just `gene+source`. A schema change or prompt edit otherwise serves stale values from a warm cache and the 1 s warm path hides the bug.
- **N5. `mapIds` resolves `braf` case-insensitively to human BRAF.** Case is therefore not a reliable mouse-symbol discriminator — hence "flag, do not convert" in B7.
- **N6. Strip Ensembl version suffixes** (`ENSG00000157764.14`) on input. OT accepted the bare ID; **UNVERIFIED** whether it accepts the versioned form. GTEx, if adopted, uses versioned IDs natively.
- **N7. Europe PMC `pageSize` defaults to 25** when omitted, and `hitCount` is independent of it — do not infer counts from returned result length.

---

## 4. Source portability — what survives if the council swaps sources

The principal has confirmed Open Targets and HPA were examples, not requirements. Classification of the findings above:

### Source-specific — must be re-verified from scratch on swap

- **B1 (OT schema)** — dies entirely with Open Targets. Field names carry nothing to Pharos/TCRD, DGIdb or ChEMBL. What is portable: *the plan named fields that did not exist, and the API answered with a hard 400*. Pharos and DGIdb are also GraphQL and will likely 400 the same way, which is the safe failure. **ChEMBL is REST and is the higher risk** — REST APIs typically ignore unknown query parameters and return 200, converting a loud failure into a blank ring. **UNVERIFIED for ChEMBL; probe before adopting.**
- **B3 (Europe PMC inflation)** — dies if the literature ring moves to PubTator, which has a different query grammar and different silent-success modes. **Make query echo-back a selection criterion for the literature source**: Europe PMC echoes `request.queryString`, which is the only thing that made the 12× inflation detectable. A source that does not echo leaves the ring unauditable.
- **B4 and S1 (HPA behaviours)** — die with HPA. GTEx and Allen Brain Atlas are queried by Ensembl ID, so free-text explosion likely disappears and is replaced by **versioned Ensembl IDs** that will silently zero-match an unversioned join. Assume the trap moved, not that it is gone.
- **S3 fixture values** — the *reasons* for each fixture are portable; the *measured numbers* are OT/HPA baselines and must be re-baselined per adopted source. The fixture list itself needs no rework.
- **Rate limits** — unmeasured for every source; per-source work regardless of the ruling.

### Portable — hold whatever the council rules

- **B2 (silent batch drop)** — the pattern is universal to every multi-ID endpoint under consideration (DGIdb, ChEMBL, gnomAD, Alliance). The numbers are OT's; the rule (never zip, join on returned ID, assert subset, render absent as `missing`) and the regression test are source-independent and must run per adopted source.
- **B5 (Excel corruption)** — fully portable and **no source will ever fix it**. It is a property of the user's file.
- **B6 (resolution discipline)** — portable as a requirement, with one sharp exception below.
- **B7 (input contract and drop ledger)** — fully portable; entirely upstream of every source.
- **S1 (figure/CSV invariants)** — fully portable; entirely downstream of every source. **Build these first** — they are the only work a re-ruling cannot invalidate.
- **S2 (LLM triage reproducibility)** — fully portable; only the abstract supply changes.
- **N1 ("never trust 200")** — portable house rule, confirmed against five independent APIs.

### The trap in the amendment

**My resolver is itself Open Targets.** `mapIds` is an OT endpoint. If OT is dropped from the tractability ring, **it must be retained as the resolver or deliberately replaced** (HGNC REST or Ensembl REST). Resolution is the join key for all rings, not a ring.

**Per-ring source choice multiplies identifier spaces**, and each one is a new silent-loss surface: ChEMBL keys on UniProt/ChEMBL target IDs, DGIdb on symbols, GTEx on versioned Ensembl, DepMap on symbol+Entrez, gnomAD on Ensembl, Alliance on HGNC IDs, IMPC on MGI IDs and mouse orthologues. **Require every ring's adapter to declare its input ID type and to report its own unmapped genes into the same ledger** — otherwise the B7 invariant (`input == rendered + ledgered`) passes while genes are quietly gone from individual rings.

---

## 5. Ranking for a trustworthy morning demo

**Must exist before the overnight run starts — without these the figure cannot be trusted at all:**

1. **B1** — corrected, pinned OT query with a startup contract test that aborts on 400. Without it there is no data.
2. **B2** — join-by-ID instead of zip, plus the five-ID regression test. This is the difference between a wrong figure and a visibly incomplete one.
3. **B7** — input contract and drop ledger with the `input == rendered + ledgered` assertion, and the counts stamped on the figure. **Without this, a screenshot cannot be evaluated at all**, which is the specific failure mode the principal is worried about.
4. **S1 checks 1, 2 and 4** — cell-count identity, missing-fill disjointness, label correspondence. Roughly an hour of work and they catch the entire class of silently-wrong figures.
5. **B3 rules 1 and 2** — query echo-back assertion and NCBI `ERROR`-key detection. Two lines each.
6. **B5 + B6** — resolve before HPA, exact-match on the resolved symbol. Otherwise renamed genes vanish.
7. **A run manifest**: UTC timestamp, git SHA, every source URL and schema version, model ID and prompt hash, PASS/FAIL for each invariant. Written next to the figure so the morning screenshot is self-describing.
8. **Fail loudly, not quietly.** If any invariant fails, render a large banner on the figure itself rather than a clean-looking chart. The overnight run's job is to be honest, not to produce output.

**Can wait past the demo:**

- Full four-state colour design and legend polish (S1's state model) — ship `no_data` as hatched grey now, refine later.
- S1 checks 3, 5 and 6 (monotonicity, legend completeness, golden file).
- The tractability reduction over the SM/AB/PR array being methodologically defensible — state the rule on the figure and revisit.
- IMPC direct integration (**S4** — use OT `mousePhenotypes` overnight).
- Weight-sensitivity flagging for genes whose top-10 position does not survive reweighting.
- PDF and 300 dpi PNG export — SVG plus CSV is enough to evaluate correctness.
- Warm-cache 1 s target.
- Per-ring source swaps from the council's ruling.

**Not achievable overnight — do not attempt, and say so in the morning:**

- **Measured rate limits for every source under parallel load.** Unverified here; needs a deliberate, slow, daytime probe. Until then, cap concurrency conservatively (≤4 per host) and treat every 429 as a hard run failure rather than a retry loop.
- **The LLM triage gold set** (S2 item 5) — labelling ~20 abstracts is human work, not build work. Ship triage with the quote-verification check (S2 item 1) and the "claim, not fact" presentation, and mark precision as unmeasured on the figure.
- **UniProt/ChEMBL resolution** (B6) — unprobed; if the council picks ChEMBL, that ring cannot be trusted on day one.
- **A defensible weighting scheme.** The defaults are a scientific choice, not a QA item. Print them on the figure and treat the ranking as provisional until a human signs off.
- **Any claim that the ranking is biologically meaningful.** Overnight gets you a figure that is *internally consistent and auditable*. That is the honest deliverable for the morning.
