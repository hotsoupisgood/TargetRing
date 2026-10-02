# Decision — this app reads RNA-seq only. Microarray will not be built.

**Decided:** 2026-10-02, Puck, WhatsApp. **Status:** settled (`AGENTS.md` hard
rule 3 — propose a change with reasons and wait; do not quietly reopen it).
**Supersedes:** stage 6 of `/tmp/plan-search-phenotype.md` §3, which is now
**won't build** rather than *not yet built*.

This file exists so the decision is **reversible with the numbers already in
hand**. Everything below was measured live, not estimated. If microarray is ever
reconsidered, start here and not from a fresh probe: the two probe docs cost
about 157 MB of EBI reads and one 45-minute single-file download, and none of it
needs repeating.

Evidence: [`ATLAS-ARRAY-PROBE-2026-09-30.md`](ATLAS-ARRAY-PROBE-2026-09-30.md)
(the grain is the probe) and
[`ATLAS-ARRAY-MULTIPROBE-2026-10-02.md`](ATLAS-ARRAY-MULTIPROBE-2026-10-02.md)
(how often that matters, all 20 designs).

---

## 1. What is given up, in numbers

| | |
|---|---|
| diseases still served | **88 of 246 (35.8%)** |
| diseases now **entirely unavailable** | **158** |
| array contrasts excluded | **854 of 1,126 (76%)** |
| across | 269 experiments, 20 array designs |
| RNA-seq retained | 272 contrasts over 100 experiments |

This is the expensive half of the decision and it is not hidden: **the cost is
158 diseases, not a rounding error.** For Crohn's — the disease this build was
developed against — 121 of 138 indexed contrasts are array, so the picker shows
17 drawable rows out of 138.

## 2. Why: test surface, not coverage

**Coverage is not the reason.** 64% of diseases is a real loss and dropping
arrays does not improve anything a reader sees. The reason is that microarray is
not one feature, it is **six independent behaviours that must each keep passing
forever**, and every one of them gets re-tested on every future RNA-seq change:

1. **A second file layout.** `{ACC}_{PLATFORM}-analytics.tsv` plus a third
   column, `Design Element`, and a `.t-statistic` the RNA-seq files do not have.
   Two layouts behind one `analytics_url()`.
2. **A second heatmap recipe.** Array normalized expressions are already
   log-scale: no CPM, no `log2(x+1)`, z across shown samples as before — and the
   recipe string on the figure has to say which of the two was used, or the
   figure stops being self-describing.
3. **Probe-collapse behaviour.** The row grain is the probe. One gene, one
   contrast, three probes, three different answers (DGCR5 `ENSG00000273032` in
   E-GEOD-16879 `g13_g1`: p=0.0675, p=NA, p=0.1630). Stage 3 selects top N by
   adjusted p, so the gene set depends on which row is read. **683 of 854 array
   contrasts (80%) sit on a design where multi-probe genes occur**, so the rule
   cannot be skipped — and whichever of the three candidate rules is chosen, it
   is a permanently tested branch with its own ledger entries.
4. **A miRNA exclusion.** 3 of the 20 designs (A-MEXP-1663, A-GEOD-6955,
   A-MEXP-1551; 7 contrasts) are miRNA arrays: `Gene ID` holds a miRBase mature
   accession (`MIMAT0000062`) and `Gene Name` is **empty**. A `MIMAT` joins to
   nothing a submitted gene set resolves to (rule 5) and there is no symbol to
   fall back on. These must be refused *on identifier namespace, before* any
   collapse rule — i.e. a second refusal path, ahead of the first.
5. **An unstated-release path.** **Not one of the 20 designs states an Ensembl
   release at all** — the array methods file has no `Read Mapping` row, so
   `ensembl_release_from()` raises on every array experiment. Rule 5 says Atlas
   is Ensembl 95 regardless of platform, but the array files do not say so, so
   the release would have to be recorded as *not stated by the source* rather
   than filled in from the RNA-seq files. Writing a release the file never
   stated onto a figure is rule 5's trap pointing the other way.
6. **A differently-named pipeline row.** RNA-seq's `-analysis-methods.tsv` says
   `Differential Expression`; microarray says `Differential expression
   statistics`, in all 20 designs. `contrast.pipeline_from()` reads the former
   and returns `''` for every array experiment, so `padj_column_label()` would
   print "pipeline not read" on an array figure.

Each of those is cheap once. Together they roughly double the number of
behaviours this build must hold true, and they double it in the branch that is
*hardest to notice breaking*, because no one looks at array figures daily. A
rule that is tested but unwatched is how a plausible wrong figure gets shipped.

## 3. What the measurement says *against* the decision, stated fairly

The multi-probe probe was commissioned to price the collapse rule, and its
answer was that the rule is **nearly free**:

* per-gene multi-probe rate is **at most 0.118% on any design**, max 3 probes;
* **175 multi-probe genes in 283,399 rows** read across all 20 designs;
* refuse-and-ledger costs a 200-gene submitted set about **0.24 genes**;
* **E-GEOD-100833's zero is exact, not a lower bound** — read whole at
  61,469,691 bytes, 16,053 rows, 16,053 unique gene ids, max 1 probe. It carries
  108 of the 166 indexed Crohn's contrasts, so the cheapest rule costs *nothing*
  on the dominant experiment.

So the *statistical* objection that stopped stage 6 on 2026-09-30 largely
dissolved on 2026-10-02. The decision is being taken anyway, on item 2 above:
the collapse rule is cheap, and the five other behaviours around it are not.
**That is the trade, and it is a judgement call, not a measurement.**

## 4. What would reopen it

Any one of these, and the evidence above is enough to re-scope without a new
probe:

* a target disease whose contrasts are *all* array — then the question is one
  disease's worth of work, not 158's;
* a decision to accept probe-level rows as their own grain (one row per probe,
  never collapsed, `Design Element` on the figure) — which removes behaviour 3
  entirely and leaves four;
* Atlas restating array analytics at gene level, or stating an Ensembl release.

Two things would still have to be re-measured, because they were not:
`-normalized-expressions.tsv` is **473.7 MB** for E-GEOD-100833 (1,748 sample
columns) against 49.9 MB for RNA-seq's largest, so the array heatmap path needs a
streaming column-subset read rather than stage 4's `raw_counts()` shape; and
A-MEXP-2246's representative is a **116-row panel**, which cannot populate a ring
for an arbitrary submitted set whatever the collapse rule is.

## 5. What changes in the app

Nothing is deleted. The array contrasts stay **indexed, counted and visible** —
hiding them would make the picker look like Atlas has 17 Crohn's contrasts, which
is the silent-absence failure these rules exist to prevent. What changes is that
the refusal is now **permanent and says so**, where it used to say "until
stage 6":

* the picker's greyed rows and the disease autocomplete now distinguish *nothing
  exists in Atlas* from *exists in Atlas, not read by this app* — see the
  commit for task 2;
* `MicroarrayNotDrawable` is a settled refusal, not a placeholder;
* `scripts/probe_atlas_array.py` is kept. It is the evidence for this file and
  the starting point if §4 ever fires.

## 6. What is *not* claimed

That arrays are bad data, that limma is wrong, or that 0.118% is too high.
limma is confirmed on every design ("Moderated t-statistics computed with limma
version 3.28.21 … Benjamini & Hochberg (1995) FDR correction"). The claim is only
that **this app** will not read them, and that a reader can tell.
