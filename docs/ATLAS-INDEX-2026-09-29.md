# The Atlas contrast index — first live build, 2026-09-29

`scripts/build_atlas_index.py`, run once against
`https://www.ebi.ac.uk/gxa/json/experiments` and the per-experiment FTP files.
Read-only GETs, four concurrent per host (`AGENTS.md` rule 9), no 429, no paid
call. The build record is `logs/atlas-index-build.json`; the index itself is
`cache/atlas.index/v1/index.json`.

| | |
|---|---|
| built | `2026-09-29T22:15:59+00:00` (schema v1), rebuilt `22:37:58` at schema **v2** |
| catalogue | 4,562 experiments (`Differential` 4,187, `Baseline` 375) |
| population | **369** human `Differential` experiments carrying a `disease` factor |
| requests | 738 (`-configuration.xml` + `.condensed-sdrf.tsv` per experiment) |
| elapsed | 42.2 s cold; 0.4 s warm (739 cache hits, 0 misses) for the v2 rebuild |
| experiments indexed | 369 of 369, **0 failed to parse** |
| contrasts | **1,126** — 272 RNA-seq, 854 microarray across 20 array designs |
| distinct disease ids on a test group | 247 |

## Three numbers worth carrying forward

**1. Eight contrasts have an id that is not `{reference}_{test}`.** The spike
verified the identity on 139/139 contrasts — but that was the eleven Crohn's
experiments only. Over all 1,126 it fails eight times:

```
E-GEOD-32175 g1_g2      ref=g2  test=g1   A-AFFY-44
E-GEOD-65127 g2_g4      ref=g4  test=g2   A-AFFY-44
E-GEOD-67333 g3_g1      ref=g2  test=g1   rnaseq
E-GEOD-71595 g5_g3      ref=g4  test=g3   rnaseq
E-GEOD-7307  g88_g101   ref=g78 test=g101 A-AFFY-44
E-MEXP-1416  g2_g1      ref=g1  test=g2   A-AFFY-54
E-MTAB-11354 g1_g2      ref=g2  test=g1   rnaseq
E-PROT-59    g1_g3      ref=g2  test=g3   rnaseq
```

I8 is therefore not a formality. None of the eight is a Crohn's contrast, so the
spike could not have seen them. They are indexed with `invalid_reason` set and
the picker shows them as `INVALID — sign unsafe`; `ContrastRef` refuses to be
constructed from one, so no figure can be drawn from a contrast whose sign is
unestablished.

**2. 147 of 1,126 contrasts carry no `disease` factor id on their test group.**
They appear in no disease's picker, which is right — a contrast with no disease
id is not evidence about a disease — and it is also a silent 13% of the index, so
the number is on the picker rather than being a property of the filter nobody
states.

**3. Factors and characteristics disagree on 6 of 1,126.** The index files a
contrast by the ontology ids on its test group's **`factor`** rows, not its
`characteristic` rows: a sample's donor may have a disease that is not what the
experiment varied. Four of the six are `E-GEOD-67196` (ALS, `MONDO_0004976`). For
Crohn's the two methods agree exactly, at 138.

## Crohn's disease — the real numbers

Resolved through OLS4: `Crohn's disease` → **`MONDO_0005011`** (label
`Crohn disease`), with `EFO_0000384` as an Atlas-side id. The bridge is asserted,
not assumed: MONDO's `database_cross_reference` offers `EFO:0000384`, and EFO's
own record for that term says `is_obsolete: true`, `obsoleted_in_version:
3.88.0`, `term replaced by: …/MONDO_0005011`.

| | index (ontology id on the test group) | spike (regex + prose label) |
|---|---|---|
| contrasts | **138** | 123 |
| experiments | **17** | 9 |
| microarray | **121** | 101 |
| RNA-seq | **17** | 22 |
| a side with n<8 | **75** | 66 |
| a side with n<4 | **18** (min n=3) | 17 (min n=3) |
| from one experiment | **101 of 138** (`E-GEOD-100833`) | 101 of 123 |

**The difference is the method, and the plan mandated the method.** The spike
found its experiments by regex-matching `crohn` in the catalogue JSON and then
filtered on the assay-group label prose; the plan's §3 stage 1 requires the
opposite ("excluded here by *ontology id on the test group*, not by regex on the
prose"). Six experiments tag samples `EFO_0000384` without the word "Crohn"
appearing anywhere in their catalogue row, so the spike's population was short by
six experiments and 15 contrasts:

```
E-GEOD-16879 8   E-MTAB-5464 3   E-GEOD-112057 1   E-GEOD-59071 1
E-GEOD-83687 1   E-MEXP-2083 1   E-MTAB-2967   1
```

The spike's "101 microarray" and its "101 of 123 from `E-GEOD-100833`" were the
same number by coincidence rather than the same fact: 101 is the dominance
figure, and the microarray total is 121.

`E-GEOD-6731` is the cleanest demonstration that the prose cannot do this job.
It has two contrasts against one reference group; the test groups are tagged
`EFO_0000384` and `EFO_0000729` (ulcerative colitis) and their labels are the
bare strings `g2` and `g3`. There is nothing in the prose to filter on.

## `E-GEOD-57945 g6_g4` belongs in the picker

The spike's null-ish contrast is labelled `Crohn's disease; normal` and its
disease factor really is `EFO_0000384` — the `normal` is the *ileal inflammation*
factor, not the disease. So it is a Crohn's contrast (n=24 vs 42) and the picker
lists it. What makes it dangerous is what its heatmap looks like, which is stage
4's problem; what the picker owes a reader is the label, the n per side and the
flags.

## Schema v2

Stage 2 added the `organism part` values on both sides of every contrast — the
picker's tissue column. A v1 row has no tissue in it, and an empty cell would
read as "this contrast has no tissue" rather than "this index did not record
one", so the schema version was bumped and the index rebuilt rather than the
field being added silently. The rebuild came off the warm HTTP cache: 739 hits,
0 misses, 0.4 s, no request to EBI, same 1,126 contrasts.

Where an experiment annotates no `organism part` at all — `E-TABM-733`, for
instance — the column is empty and says so. No tissue is inferred from the prose
name.

## Rebuilding

    .venv/bin/python scripts/build_atlas_index.py            # ~740 GETs, ~45 s
    .venv/bin/python scripts/build_atlas_index.py --dry-run  # count only
    .venv/bin/python scripts/make_atlas_fixtures.py          # re-cut fixtures/atlas/

An index older than 30 days is served with a `STALE` flag the UI prints, never
refused: a month-old count is still the count that was measured, and refusing
would leave the picker with no numbers rather than with dated ones.
