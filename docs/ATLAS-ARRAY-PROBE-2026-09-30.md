# Probe — is microarray `-analytics.tsv` gene-level or probe-level?

**Date:** 2026-09-30. **Probe:** `scripts/probe_atlas_array.py`, run live today.
**Verdict:** **PROBE-LEVEL.** Stage 6 stopped before any of it was built.
**Cost:** $0.00. Read-only GETs, four array designs, nothing written to `cache/`.

> **Outcome, 2026-10-02: stage 6 is closed as "won't build" — RNA-seq only.**
> Puck decided it on WhatsApp; the decision and its full basis are in
> [`DECISION-RNASEQ-ONLY-2026-10-02.md`](DECISION-RNASEQ-ONLY-2026-10-02.md).
> This document is evidence for that decision, not a pending work item. Nothing
> below is retracted — "stage 6 must …" throughout now reads as *what building it
> would have required*, and is kept in that form deliberately, because it is the
> scope estimate any reopening would start from.

Stage 6 of `/tmp/plan-search-phenotype.md` (§3 stage 6, and its own "unverified
and must be probed first") is gated on this one question. The plan's instruction
if the answer is probe-level is to stop and report, because a probe→gene collapse
is a study-design decision for Puck and not an implementation detail. That is
what happened.

---

## The answer

**The row grain of `{ACC}_{PLATFORM}-analytics.tsv` is the probe, not the gene.**
The microarray layout inserts a third column, **`Design Element`** — the probe
set id — and carries one row per probe. A gene with three probes is three rows,
each with its own p-value, t-statistic and log2FC for the same contrast.

| | RNA-seq | microarray |
|---|---|---|
| columns 1-3 | `Gene ID`, `Gene Name`, `{c}.p-value` | `Gene ID`, `Gene Name`, **`Design Element`** |
| per-contrast statistics | `.p-value`, `.log2foldchange` | `.p-value`, **`.t-statistic`**, `.log2foldchange` |
| one row is | one gene | one **probe** |
| `Gene ID` unique | yes (65,217/65,217) | **no — varies by experiment** |

`Gene ID` is ENSG-shaped in **45,035 of 45,035** rows read across the four
designs, so the *identifier* question the plan asked ("gene-level `ENSG` as the
RNA-seq files are, or probe-level") has a misleading answer on its own: the id
column looks exactly like RNA-seq's. The grain is what differs, and it is
`Design Element` plus repeated `Gene ID`s that establish it.

## Measured, four array designs

| accession | design | Crohn's contrasts | analytics | rows | unique gene ids | genes with >1 probe |
|---|---|---|---|---|---|---|
| E-GEOD-100833 | A-GEOD-13158 | 108 of 166 | 61.5 MB | 3,278 read (truncated) | 3,278 | 0 *(lower bound)* |
| E-GEOD-102133 | A-AFFY-141 | 5 | 2.7 MB | 12,529 | 12,528 | **1** (max 2 rows) |
| E-GEOD-16879 | A-AFFY-44 | 12 | 9.1 MB | 21,058 | 21,037 | **20** (max 3 rows) |
| E-GEOD-6731 | A-AFFY-1 | 2 | 0.8 MB | 8,170 | 8,170 | 0 |

`Design Element` is present in **all four**, including the two where every gene
happens to have exactly one probe. Re-run with `--max-bytes 70000000` to read
E-GEOD-100833 whole; its multi-probe count above is a lower bound and the
`Design Element` finding does not depend on it.

**Probing one experiment would have answered this wrongly.** A-GEOD-13158 and
A-AFFY-1 are 1:1 and look gene-level; A-AFFY-44 and A-AFFY-141 are not. This is
the same shape as the spike's correction on duplicated raw-counts columns — a
sporadic per-experiment property, so assert every time and never predict.

## Why this is Puck's decision and not a default

One gene, one contrast, three probes, three different answers
(E-GEOD-16879 `g13_g1`, read from the file today):

| gene | probe | p-value | log2FC |
|---|---|---|---|
| DGCR5 `ENSG00000273032` | `1563243_at` | 0.0675 | -0.3 |
| DGCR5 `ENSG00000273032` | `215003_at` | **NA** | 0 |
| DGCR5 `ENSG00000273032` | `228804_at` | 0.1630 | -0.3 |

And in E-GEOD-102133 `g6_g1`: NKAIN3 `ENSG00000185942` is p=NA/log2FC=0 on probe
`8146625` and p=0.190/log2FC=-0.2 on probe `8146633`.

Stage 3's selection is **top N by adjusted p**. With the file as it is, DGCR5's
rank depends entirely on which of its three rows is read — and "whichever came
first in the file" is not a stated statistic. The three candidate rules disagree
on this gene:

* **most significant probe wins** — p=0.0675. Biases every multi-probe gene
  upward against single-probe genes by taking a minimum over 2-3 draws, which is
  a selection effect applied to some genes and not others.
* **mean over probes** — averages a p-value, which is not a meaningful operation,
  and would have to average across an `NA` that is not a low value (rule 1).
* **refuse ties / drop multi-probe genes** — honest, and silently removes 20
  genes from E-GEOD-16879 unless they are ledgered.

Each is defensible and they produce different gene sets, so each produces a
different figure from the same data with no error anywhere. That is exactly
`AGENTS.md:17`'s worst output, and it is why the plan put a stop here.

**What Puck needs to decide:** which probe represents a gene, and what the
figure says about the ones that were collapsed. A per-gene ledger entry naming
the winning `Design Element` and the count of probes collapsed would keep rule 3
intact whichever rule is chosen.

## Two further divergences, found on the way

Not the gate, but stage 6 will hit both and neither is in the plan:

1. **The pipeline row has a different key.** RNA-seq
   `-analysis-methods.tsv` says `Differential Expression`; microarray says
   **`Differential expression statistics`**. `contrast.pipeline_from()` reads
   the former and returns `''` for every array experiment, so
   `padj_column_label()` would print "pipeline not read" on an array figure.
   limma *is* confirmed — "Moderated t-statistics computed with limma version
   3.28.21 … p-values adjusted using Benjamini & Hochberg (1995) FDR
   correction", identical across all four designs — so the plan's "limma
   expected, assert rather than assume" holds; the assertion just has to look
   under the right key.

2. **Microarray states no Ensembl release at all.** There is no `Read Mapping`
   row — the array methods file has only `Normalization`,
   `Differential expression statistics` and `Gene Set Enrichment Analysis`.
   `ensembl_release_from()` raises `HardFailure` on all four. Rule 5 and the
   plan both say "Atlas is Ensembl 95 regardless of platform", but the array
   files do not say so, so stage 6 must record the release as **not stated by
   the source** rather than filling in `95` from the RNA-seq files. Writing a
   release the file never stated onto a figure is the rule-5 trap pointing the
   other way.

3. **`-normalized-expressions.tsv` for the dominant experiment is 473.7 MB**
   (E-GEOD-100833, 1,748 sample columns). RNA-seq's largest counterpart is
   49.9 MB and the spike already flagged that as big. `Http` reads whole bodies
   into memory and `Cache` writes them to disk, so the array heatmap path needs
   a streaming column-subset read rather than stage 4's `raw_counts()` shape.
   The other three designs are 4.7-47.6 MB.

## What was built

Nothing of stage 6. One helper the probe needed —
`adapters/atlas.normalized_expressions_url(acc, platform)`, which refuses an
RNA-seq platform rather than building a URL that would 404 — plus its test. No
`heatmap.py` array recipe, no picker change, no fixture: the 121 greyed rows in
the picker stay greyed, honestly, with their existing reason.

## Reproduce

```
.venv/bin/python scripts/probe_atlas_array.py            # exit 4 = probe-level
.venv/bin/python scripts/probe_atlas_array.py --json
```
