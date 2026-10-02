# Probe — how often does a microarray gene have more than one probe?

**Date:** 2026-10-02. **Probe:** `scripts/probe_atlas_array.py --all-designs`,
run live today. **Cost:** $0.00, read-only GETs, nothing written to `cache/`.
**Companion to** `ATLAS-ARRAY-PROBE-2026-09-30.md`, which established *that*
the grain is the probe. This one measures *how much it matters*.

> **Outcome, later the same day: stage 6 is closed as "won't build" — RNA-seq
> only.** Puck decided it on WhatsApp after reading this probe; see
> [`DECISION-RNASEQ-ONLY-2026-10-02.md`](DECISION-RNASEQ-ONLY-2026-10-02.md).
> Note that this probe's finding argues the *other* way — the collapse rule it
> was commissioned to price turned out to be nearly free — and the decision was
> taken on the five other array-only behaviours it documents, not on this one.
> "Stage 6 stays blocked", below, was true when written and is now superseded.

**The number:** **683 of 854 array contrasts (80.0%)** sit on a design where
multi-probe genes occur. **But the per-gene rate is at most 0.118% on any
design, and the maximum is 3 probes.** Across all twenty representative files —
283,399 rows read, none truncated — there are **175 multi-probe genes in
total**.

Both halves matter and they point the same way. Four fifths of the index is
exposed to the collapse rule, so the rule cannot be skipped. But it bites on
roughly one gene in a thousand, so **the cheapest rule is also nearly free**:
refusing multi-probe genes and ledgering them costs a 200-gene submitted set an
expected **0.24 genes**.

---

## E-GEOD-100833's zero held

The 2026-09-30 probe read 3,278 of its 16,053 rows and reported 0 multi-probe
genes as an explicit lower bound. Read whole today — 61,469,691 bytes,
`read_whole_file: true` — it is **exactly 0**: 16,053 rows, 16,053 unique gene
ids, max 1 probe for any gene. A-GEOD-13158 is genuinely 1:1 on the experiment
that carries 108 of the 166 indexed Crohn's contrasts.

So Puck's cheapest option costs **nothing at all** on the dominant Crohn's
experiment, and the "only if that zero is exact" caveat is discharged.

Two corrections to the earlier doc that the full read turned up:

* **It took 45 minutes, not the estimated ~8.** EBI served this file at
  ~1.4 MB/min today. Budget accordingly; the whole 20-design sweep moves
  157.1 MB.
* **16,052 of 16,053 ids are ENSG-shaped — not all of them.** The earlier doc
  says "ENSG-shaped in 45,035 of 45,035 rows read", which was true of the
  truncated read and is not true of the whole file. One row in E-GEOD-100833 is
  not ENSG-shaped. See also the three miRNA designs below, where *none* are.

## Every design, weighted by the contrasts it carries

Representative experiment per design = the one with the **most contrasts** on
that design (tie-broken by accession, so the table is deterministic). Index
built 2026-09-29T22:37:58+00:00: 854 array contrasts, 269 experiments, 20
designs; RNA-seq is a separate 272 over 100.

| design | contrasts | exps | representative | MB | read whole | rows | unique genes | genes >1 probe | max | % of genes | `Design Element` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A-AFFY-44 | 452 | 147 | E-GEOD-48433 | 50.9 | yes | 21,051 | 21,030 | **20** | 3 | 0.095% | y |
| A-GEOD-13158 | 111 | 2 | E-GEOD-100833 | 61.5 | yes | 16,053 | 16,053 | 0 | 1 | 0.000% | y |
| A-AFFY-33 | 94 | 43 | E-GEOD-3307 | 5.8 | yes | 12,072 | 12,059 | **13** | 2 | 0.108% | y |
| A-AFFY-141 | 63 | 22 | E-GEOD-65270 | 6.1 | yes | 22,359 | 22,354 | **5** | 2 | 0.022% | y |
| A-AFFY-37 | 35 | 14 | E-GEOD-32407 | 3.2 | yes | 12,061 | 12,061 | 0 | 1 | 0.000% | y |
| A-AFFY-34 | 25 | 9 | E-GEOD-3307 | 4.7 | yes | 9,858 | 9,853 | **5** | 2 | 0.051% | y |
| A-AGIL-28 | 22 | 11 | E-GEOD-43767 | 4.0 | yes | 19,935 | 19,927 | **8** | 2 | 0.040% | y |
| A-AFFY-1 | 11 | 6 | E-GEOD-1402 | 1.2 | yes | 8,171 | 8,171 | 0 | 1 | 0.000% | y |
| A-GEOD-16686 | 7 | 3 | E-GEOD-65114 | 4.7 | yes | 32,321 | 32,281 | **38** | 3 | 0.118% | y |
| A-MEXP-2183 | 6 | 2 | E-MTAB-6094 | 3.1 | yes | 21,069 | 21,053 | **16** | 2 | 0.076% | y |
| A-MEXP-1663 | 5 | 2 | E-GEOD-25820 | 0.1 | yes | 723 | 723 | 0 | 1 | 0.000% | y † |
| A-GEOD-10558 | 4 | 2 | E-GEOD-102008 | 3.1 | yes | 22,307 | 22,295 | **11** | 3 | 0.049% | y |
| A-MEXP-2246 | 4 | 3 | E-MTAB-910 | 0.01 | yes | 116 | 116 | 0 | 1 | 0.000% | y ‡ |
| A-MEXP-2320 | 4 | 2 | E-MTAB-8887 | 3.1 | yes | 25,947 | 25,926 | **20** | 3 | 0.077% | y |
| A-AFFY-54 | 3 | 2 | E-MEXP-1416 | 2.0 | yes | 20,983 | 20,960 | **22** | 3 | 0.105% | y |
| A-MEXP-2102 | 3 | 2 | E-MTAB-4006 | 2.2 | yes | 21,659 | 21,642 | **17** | 2 | 0.079% | y |
| A-AFFY-9 | 2 | 1 | E-GEOD-1919 | 0.8 | yes | 8,170 | 8,170 | 0 | 1 | 0.000% | y |
| A-AFFY-41 | 1 | 1 | E-GEOD-20333 | 0.4 | yes | 7,606 | 7,606 | 0 | 1 | 0.000% | y |
| A-GEOD-6955 | 1 | 1 | E-GEOD-13316 | 0.03 | yes | 469 | 469 | 0 | 1 | 0.000% | y † |
| A-MEXP-1551 | 1 | 1 | E-TABM-713 | 0.03 | yes | 469 | 469 | 0 | 1 | 0.000% | y † |

**No design was unprobeable.** All twenty analytics files answered HTTP 200 and
all twenty were read whole at `--max-bytes 70000000`. No row in the table is a
lower bound. `Design Element` is present in all twenty, `p-value`,
`t-statistic` and `log2foldchange` in all twenty, the pipeline row is
`Differential expression statistics` in all twenty, and **not one of the twenty
states an Ensembl release** — so the 2026-09-30 doc's divergences 1 and 2 hold
across the whole index, not just four designs.

* **683 of 854 contrasts (80.0%)** on 11 affected designs.
* **171 of 854 (20.0%)** on 9 designs measured exactly 1:1.
* **0 unmeasured.**

## What this means for the three candidate rules

The collapse rule applies to 80% of the index, so it has to be chosen. What
the measurement adds is that all three rules touch almost nothing:

| rule | genes affected, worst design | on a 200-gene set |
|---|---|---|
| refuse multi-probe genes and ledger them | 38 of 32,281 (0.118%) | ≈ 0.24 genes dropped |
| most significant probe wins | same genes, min over 2-3 draws | same count, biased upward |
| highest mean expression probe wins | same genes | same count, needs the 474 MB matrix |

**Refuse-and-ledger is the cheapest and it is now measured, not assumed.** It
drops at most about one gene per 850 in the submitted set, each one named in the
drop ledger with its probe count — which satisfies rule 3 without inventing a
statistic. "Most significant wins" costs the same genes but applies a
minimum-over-draws selection effect to 0.1% of genes and not the rest, and
"highest mean expression" additionally requires reading
`-normalized-expressions.tsv`, which is 473.7 MB for E-GEOD-100833 alone.

This is Puck's call and this probe does not make it. Stage 6 stays blocked and
the 121 greyed Crohn's rows stay greyed.

## Two findings that are not about probes at all

**† Three designs are miRNA arrays, not gene arrays.** A-MEXP-1663,
A-GEOD-6955 and A-MEXP-1551 (7 contrasts between them) put a **miRBase mature
accession** in `Gene ID` — `MIMAT0000062` — and leave `Gene Name` **empty**:

```
Gene ID        Gene Name   Design Element   g2_g1.p-value  ...
MIMAT0000062               A_25_P00011584   0.311218647343535
```

They are 1:1 probe-to-feature, so they sit in the unaffected 20% above, and
reading that as "drawable" would be wrong. A submitted gene set resolves to
ENSG (rule 5); a `MIMAT` id joins to none of it and there is no symbol to fall
back on. **Stage 6 must refuse these three designs on identifier namespace,
before any collapse rule is reached** — not collapse them and draw an empty
ring. This is why the sweep reports `n_ensg_in_id_column` per design rather
than assuming the 2026-09-30 finding generalised: **1,662 of the 283,399 rows
read today have a first column that is not an ENSG** — 1,661 of them on these
three designs, plus the one stray row in E-GEOD-100833.

**‡ A-MEXP-2246's representative is a 116-row file.** E-MTAB-910 is 10,653
bytes and carries 116 genes. ENSG-shaped and well-formed, but a 116-gene panel
cannot populate a ring for an arbitrary submitted set, and a heatmap built from
it would be mostly `no_data`. Worth a flag on the picker row rather than a
surprise at render time.

## Limitation, stated

**One representative experiment per design.** A design marked 0 is 0 *on that
experiment*, and multi-probe-ness is a per-experiment property — the
2026-09-30 doc's own "sporadic, assert every time, never predict". A-AFFY-44
measured 20 multi-probe genes on E-GEOD-16879 (21,058 rows) and 20 on
E-GEOD-48433 (21,051 rows); close, but not the same file, so the agreement is
suggestive and not a guarantee. Nothing here licenses skipping the per-file
assertion at read time; what it licenses is choosing a rule knowing its cost is
~0.1% of genes rather than unknown.

Covering all 269 experiments rather than 20 would cost ~2,000 GETs and
several GB. That was not done, and the 80.0% is therefore a statement about
designs, weighted by contrasts — not a census of every file.

## Reproduce

```
.venv/bin/python scripts/probe_atlas_array.py --all-designs --max-bytes 70000000
.venv/bin/python scripts/probe_atlas_array.py --all-designs --max-bytes 70000000 --json
```

157.1 MB and roughly two hours at 2026-10-02's throughput; sequential, one
request at a time, well inside rule 9's cap of 4 per host. A 429 aborts the
sweep rather than being retried or downgraded to a per-design failure. Today's
run was split into `--designs A-GEOD-13158` plus the other nineteen so the
61.5 MB file was not pulled twice; the per-design results are identical either
way, and the 80.0% is over all twenty.

The original four-target behaviour is unchanged and needs no index on disk:

```
.venv/bin/python scripts/probe_atlas_array.py      # exit 4 = probe-level
```

## What was built

Only the probe mode: `designs_from_index()`, `--all-designs`, `--designs`,
per-design failure capture, and the contrast-weighted summary. No app change —
`heatmap.py`, `contrast.py` and the picker are untouched, and stage 6 is still
blocked on Puck's decision.

**Test baseline, for the record:** `pytest -q` at `da87fff` is **3 failed,
1,007 passed, 15 skipped** (1,025 collected). The three failures are in
`test_export.py` (2) and `test_table.py` (1), are **pre-existing** — they
reproduce with this change stashed, and no test imports this script — and are
stage-7 work outside this probe's scope. `test_export.py` run alone fails a
fourth test that passes in the full run, so that file also has test-order
coupling.
