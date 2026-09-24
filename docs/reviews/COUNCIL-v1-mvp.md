# Council review — targetring v1 (built MVP)

**Date:** 2026-09-24. **Reviewer:** scientific council (subagent).
**Scope:** the built application, against `PLAN.md`, `docs/V1-REPORT.md` and our own
`COUNCIL-v0-plan.md`. Local only; nothing deployed, nothing pushed. Only this file is committed.

**Everything below was produced by running the built app on this Mac on 2026-09-24**, not by
reading code. Server started as instructed
(`.venv/bin/python -m uvicorn targetring.app:app --host 127.0.0.1 --port 8811`, `/api/health` ok);
the analysis runs were driven through `targetring.run.run_analysis`, which is the same code path
the web form uses. Test suite: `133 passed, 2 skipped`. Probe scripts are in `/tmp/tr_probe/`
(deliberately outside the repo). Run ids are cited per item and the artefacts are under `runs/`.

**The two inputs I used, and why.** A 15-gene liver set of targets whose druggability, safety,
tissue specificity and clinical status can be stated independently — PCSK9, TTR, ASGR1, SERPINA1,
APOB, HMGCR (known liver targets), BRAF (approved for its own indication), EPHA2 and DDR1
(kinases hit only by promiscuous approved drugs), KCNH2 (hERG, the canonical anti-target), GFAP
and HBB (wrong-tissue controls), ACTB and TMEM229B (housekeeping and Tdark negative controls),
ALB (ambiguous-symbol control). Plus the build's own `fixtures/de_realistic.csv` and
`fixtures/de_broken.csv`.

**Ordering:** BLOCKING → SHOULD-FIX → NOTE. §4 lists calls the build made that I would not have
made and am letting stand. §5 answers the seven questions directly.

---

## 1. BLOCKING

### V1 — The "furthest phase" ring is set to *Approved* by polypharmacology. D2 was implemented as drawer prose, not as a constraint on the ring.

**Claim.** `clinical_phase` takes `max(maxClinicalStage)` over every row with no test that the
drug's indication has anything to do with the target, so a target with no approved drug renders
identically to one with three.

**Evidence** (run `20260924T064712Z-1bcd4f`, liver set, region Liver):

```
EPHA2   clinical_phase = 5.0   "Approved (3 candidates)"   fill #fde725 (brightest)
        the 3 agents: VANDETANIB  (approved: medullary thyroid carcinoma — RET/VEGFR)
                      DASATINIB   (approved: CML, BCR-ABL1 positive)
                      REGORAFENIB (approved: colorectal cancer)
        clinical_activity = 1.0 "active (121 of 611 AACT trials recruiting or ongoing)"
```

There is no approved EPHA2 drug. The 611 trials are dasatinib and regorafenib trials in leukaemia
and colorectal cancer, counted as EPHA2 pipeline activity. This is the sorafenib/BRAF case of D2,
reproduced on a target where nothing rescues it.

`KCNH2` is the same failure with worse consequences: `clinical_phase = 5.0`, *"Approved (17
candidates)"* — sotalol, dofetilide, amiodarone, ibutilide, vernakalant, dalfampridine,
amifampridine. Its own `safetyLiabilities`, fetched in the same call and shown in the drawer, are
`Torsades de Point`, `Increased Mortality`, `Reduced survival`. The figure therefore presents hERG
as druggable (0.67), **fully clinically validated and actively trialled**, and merely mid-range on
safety (−0.54, 5th worst of 14). A target biologist would call hERG the canonical anti-target —
the thing every programme counter-screens to avoid. Nothing in the rendered figure says so.
KCNQ1 (LQT1, the same class of cardiac channel) reaches the **headline Pareto set** on the
build's own `de_realistic.csv` (run `20260924T065120Z-537fd6`: `Pareto set: EGFR, KCNQ1, MARCHF1`,
visible in the app header and in `docs/screenshots/2b-drawer-braf-pipeline.png`).

Fault attribution, as asked: **source** — Open Targets has no on-target/anti-target axis and lists
any mechanism-annotated drug; **transformation** — `clinical_phase_cell()` maxes the stage enum
with no indication or action-direction test
(`targetring/adapters/opentargets.py:213-232`); **rendering** — the ring is the top of the value
colormap with no marker. The drawer does carry the indication column and an explicit warning
banner ("An approved drug that hits this target is not the same as this target being clinically
validated"), which is D2/D6 done well — but it is one click away from the artefact that misleads,
and the build's own reasoning is that "the morning review is a screenshot".

**Change.** Two parts, both required. (i) Split the phase ordinal: a stage only counts toward the
ring when at least one of the row's `diseases` is plausibly attributable to the target, and
otherwise caps the ring at "drugged, indication unattributed" — render that as a distinct state,
not as a lower number. (ii) Carry an anti-target flag: if `safetyLiabilities` contains a
cardiotoxicity/mortality event, the clinical rings must not be readable as validation. Failing
both, the phase ring must be labelled *"furthest phase reached by any drug that hits this target,
for any indication"* on the figure itself, not in the drawer.

### V2 — The Pareto rule lets a gene with one axis dominate and delete genes with four; the note the app prints about this states the opposite of what the code does.

**Claim.** `_dominates()` compares only axes where *both* genes have a value
(`targetring/rank.py:104-114`). Fewer shared axes means fewer conditions to satisfy, so
missingness makes domination **easier**, in both directions.

**Evidence** (`/tmp/tr_probe/stability.py`, on the liver run's real cells). Baseline front over 14
genes: `['ASGR1', 'PCSK9', 'SERPINA1', 'TTR']`. Add one gene whose only Pareto axis is
druggability = 1.0 (tied top) and whose other three axes are `no_data`:

```
  + a gene with ONE Pareto axis (druggability=1.0, everything else no_data):
    k=3 pareto_k=1 pareto=True
    note='Pareto membership judged on 1 of 4 axes — fewer axes make domination harder,
          so this is provisional'
    front=['ONEAXIS', 'PCSK9', 'TTR']
```

It enters the front on one measurement, and **ejects ASGR1 and SERPINA1**, each of which has all
four axes. The note the app prints to the user — "fewer axes make domination harder" — is false;
the same run demonstrates it is easier. The docstring makes the same wrong claim ("Comparing a
gene on an axis it lacks would be imputation by another name: it would let a gene win by having no
data") about the rule that does exactly that. Set the same gene's single axis to 0.7 instead of
1.0 and it drops out (`pareto=False`), confirming the mechanism: top-of-any-single-axis is
sufficient for the headline.

This is M3's defect 2 — missingness is not random, so it must not confer advantage — reappearing
in the mechanism M3 prescribed as the fix. No imputation happened; the reward for being
under-measured arrived anyway.

**Change.** A gene is eligible for the front only if it has a value on **all** of `PARETO_AXES`;
genes below that are reported as *"not eligible — k=n of 4 Pareto axes"* and drawn without a
diamond. Do not fall back to shared-axis comparison. Fix the note text and the docstring in the
same commit. (Leave-one-out over the full set is clean — see V3's evidence — so this is the only
place the front misbehaves.)

### V3 — A transient adapter failure silently rewrites the headline. Same input, same region, different answer, no banner.

**Claim.** Because domination is computed over shared axes, one `no_data` cell changes which
*other* genes are in the front — including cells belonging to genes that are not in the front
themselves.

**Evidence** (same probe, one ring forced to `no_data` for one gene, everything else identical):

```
  TTR.safety   -> no_data:  front=['TTR']
      lost=['ASGR1', 'PCSK9', 'SERPINA1']
  APOB.safety  -> no_data:  front=['PCSK9', 'TTR']
      lost=['ASGR1', 'SERPINA1']        <- APOB is not in the front either way
  PCSK9.regional_specificity -> no_data: front=['ASGR1','PCSK9']  lost=['SERPINA1','TTR']
```

A single 503 on TTR's Open Targets call turns the headline from "four candidates worth arguing
about" into "TTR is the answer". A 503 on **APOB**, which is not and never was in the headline,
deletes two genes from it: losing its own safety value lets APOB dominate ASGR1 and SERPINA1 on
the three axes that remain. Failures are deliberately not cached (`V1-REPORT` §2), so this varies
run to run. None of the six invariants fires, the manifest records no warning, and the evicted
genes carry **no note at all** — `rank_note` is attached only to the gene with the missing axis,
never to the genes it silently dominated. Transient upstream failures are not hypothetical here:
Europe PMC is returning `HTTP 503 text/html` from nginx as I write, and the report records Open
Targets blipping mid-screenshot.

**Change.** With V2's eligibility rule this mostly disappears, because a gene missing an axis can
no longer dominate. Additionally: count `no_data` cells on Pareto axes per run, put the count in
the manifest, and banner the figure when any Pareto axis is incomplete — the headline is not
reproducible while it is.

### V4 — The safety ring cannot distinguish "no liability on record" from "nobody looked", because two of its four keys exist only when the news is bad.

**Claim.** `_mean_of_keys()` averages whichever prioritisation keys are present
(`opentargets.py:189-202`). Open Targets emits `hasSafetyEvent` and `isCancerDriverGene`
**only when they are negative**. Dropping an absent key from a mean is arithmetically identical to
imputing it at the mean of the present keys — so the rule "never impute" holds at the cell level
and is broken inside the cell.

**Evidence** (live `targets(ensemblIds:[...]){prioritisation{items{key value}}}`, 2026-09-24):

```
gene        hasSafetyEv  isCancerDri  geneEssenti  geneticCons  hasHighQualityChemicalProbes
BRAF              -1.00        -1.00         0.00        -0.93         1.00
PCSK9            ABSENT       ABSENT         0.00         0.45       ABSENT
KCNH2             -1.00       ABSENT         0.00        -0.61       ABSENT
TP53              -1.00        -1.00         0.00        -0.74       ABSENT
TMEM229B         ABSENT       ABSENT         0.00        -0.21       ABSENT
```

`hasSafetyEvent` appears only as −1; `isCancerDriverGene` only as −1. So for a clean gene the
"Safety / tolerability" ring is the mean of `geneEssentiality` and `geneticConstraint` alone — a
constraint number carrying **no safety information whatsoever** — while the figure's ring key
states the ring's source as *"OT prioritisation: hasSafetyEvent, geneEssentiality,
geneticConstraint, isCancerDriverGene"*. In the liver run PCSK9 (genuinely LOF-tolerant, no
liability, two approved antibodies) and TMEM229B (Tdark, nothing known about it) both get 2-key
means, +0.227 and −0.103, and both render as ordinary value cells indistinguishable in kind. The
figure ranks TMEM229B 5th of 14 on safety. A target biologist would say we have no idea whether
TMEM229B is safe.

**Change.** Decide, per key, what absence means, and encode it: for the two asymmetric flags,
absence is a measured negative, so map it to the favourable value rather than dropping it. Then
the ring is a 4-key mean for everyone and the denominator stops encoding the answer. If you will
not make that call, drop those two keys, rename the ring to what it then measures (constraint and
essentiality), and move the safety event to its own binary ring where absence is visible.
*The same defect affects the druggability ring — see V8.*

### V5 — The figure asserts, in its own footer, something the code contradicts four lines above.

**Claim.** The rendered footer reads *"Rodent ring: abstract/model evidence is a LEAD, not a
verdict. **Never folded into any score.**"* — and the line above it prints
`Default weights (provisional, unsigned-off): … rodent=0.5 …`. The rodent ring is in
`DEFAULT_WEIGHTS` and in `RANKED_RINGS`, so it is in both the weighted score and the median
available rank. M4 said, explicitly, never fold it into the score.

**Evidence.** Both strings are legible in `runs/20260924T064712Z-1bcd4f/figure.png`. Removing
`rodent` from the weights and re-scoring the same run:

```
gene        with rodent    without    delta   rank moves
TP53             0.5308     0.4786  -0.0521   #7 -> #9
BRAF             0.5840     0.5470  +0.0370   #4 -> #6
EPHA2            0.5837     0.6068  -0.0232   #5 -> #3
ACTB             0.3064     0.2479  +0.0585   #13 -> #13
```

Up to three places. The gene it flatters most is TP53, whose 249 abnormal mouse-phenotype terms
are a measure of how catastrophic and pleiotropic its knockout is — rewarded here as if it were
evidence of target quality (see V9 for the direction problem).

**Change.** Remove `rodent` from `DEFAULT_WEIGHTS` and from `RANKED_RINGS`. Per D7, remove
`clinical_phase` from the weights too. If either stays, delete the footer sentence — the figure
must not make a false claim about its own method.

---

## 2. SHOULD-FIX

### V6 — Per-ring min–max normalisation destroys real 40× differences on one ring and manufactures maximum contrast from a 2-unit difference on another.

**Evidence** (`runs/20260924T064712Z-1bcd4f/values.csv`, `fill` column as rendered):

```
regional_specificity        rendered fill          k — rings available
  TTR       5203.97   #fde725                        9 rings  #fde725   (9 genes)
  APOB      2463.47   #238a8d                        8 rings  #21918c   (4 genes)
  SERPINA1  1827.17   #2f6c8e                        7 rings  #440154   (1 gene)
  ASGR1      295.86   #481668
  PCSK9      196.11   #470e61
  HMGCR        0.928  #440154   <-- these nine cells are
  EPHA2        0.721  #440154       the identical pixel, over
  TP53         0.468  #440154       a 41-fold range of ratios
  ACTB         0.344  #440154
  BRAF         0.317  #440154
  GFAP         0.270  #440154
  TMEM229B     0.152  #440154
  DDR1         0.052  #440154
  KCNH2        0.023  #440154
```

`_norm_for()` is linear min–max within the submitted set. On a ratio spanning five orders of
magnitude, one tissue-restricted gene collapses the ring: 9 of 14 cells are the same darkest
purple, so HMGCR (0.93, i.e. exactly average for liver) is indistinguishable from KCNH2 (0.023).
On the k ring the same rule spends the entire colormap on a 7→9 difference, so TMEM229B's k cell
is maximally dark. The literature ring is already `log10(1+n)` — the specificity and rodent rings
need the same treatment, and the bounded rings ([-1,1], 0–5) need fixed scales so that a colour
means the same thing between two runs.

**Change.** Log-scale the ratio rings; fixed domain for the bounded rings; add a colourbar. The
`[min, max]` text in the corner key is the only quantitative anchor on a figure whose entire
content is colour, and it cannot be read against a specific cell.

### V7 — The regional-specificity ratio has no expression floor, so "not expressed here" and "expressed everywhere including here" land in the same place.

**Evidence.** Same run: GFAP (astrocyte-restricted, 0.216 TPM in liver) = 0.270; ACTB
(housekeeping, 760.4 TPM in liver) = 0.344. Adjacent cells, near-identical colour, opposite
biology. The absolute TPM is in the CSV `detail` and the drawer but not in the ring.

**Change.** Render `no_data` (or a distinct low state) when region TPM is below a stated floor,
and put the floor on the figure. A ratio without a level is not a specificity statement.

### V8 — On the druggability ring, the one key that separates a chemically-probed target from an unprobed one is arithmetically inert.

**Evidence.** `hasHighQualityChemicalProbes` is present only when true (table in V4). BRAF =
mean(1,1,1,1) = **1.0**; PCSK9 = mean(1,1,1) = **1.0**. The key that distinguishes them is dropped,
and adding +1 to a set of +1s changes nothing. It only moves the number when the other keys are
mixed: DDR1 = mean(1,0,1,1) = 0.75 outranks KCNH2 and TP53 at mean(1,0,1) = 0.667 purely because
DDR1's probe key exists. That ordering has no scientific content.

Related, and the reason it matters more than it looks: **M7 and S1's two-ring split was not
built** — "has been drugged" and "family suggests druggable" are still one ring — and **Pharos TDL
was dropped from the plan without argument**. S1's case for TDL was that it is the only axis with
zero missing values (20,412/20,412), "which removes the missingness that breaks the ranking (M3)".
Every Pareto axis in v1 is missable, which is precisely the condition V2 and V3 exploit.

**Change.** Treat absent asymmetric keys as measured (as in V4). Reinstate the M7 split, or state
on the figure that the ring conflates prior effort with tractability. Pharos TDL is one unkeyed
call and would give the ranking one complete axis.

### V9 — The rodent ring's direction is asserted, not argued, and its scale discards the denominator.

**Ruling on the exclusion, as asked.** Excluding `'no abnormal phenotype detected'` from the
*count* is **right** — it is not an abnormal phenotype, and counting it would let a gene with one
such record outrank a gene with none. The build goes further than the question implies and
handles the informative negative correctly: a gene whose records are *all* "no abnormal phenotype"
returns `value(0.0, "phenotyped, no abnormal phenotype detected")`, distinct from `no_data` "no
mouse phenotype records (not phenotyped, not 'no phenotype')". That distinction is M5 done
properly and I would keep it exactly as built.

**What the ring cannot support.** (i) `RING_SIGN["rodent"] = +1`, so more abnormal phenotypes ranks
better. Nothing justifies that direction; a severe, pleiotropic knockout is at least as plausibly
a safety signal. TP53 scores 249, the maximum, and is rewarded for it. (ii) The denominator is
thrown away: a gene with 1 abnormal term out of 1 record and a gene with 1 abnormal out of 50 are
both 1.0. (iii) Linear over [1, 249] — 11 of 14 cells fall within one visually indistinguishable
band of dark purple. (iv) The ring is mouse-only while labelled "Rodent" (see V15).

**So: can the ring be read as evidence of anything?** As drawn, no — it is a measure of
phenotyping effort multiplied by pleiotropy, pointed in an unargued direction. It can support
exactly one honest statement, which is the one M4 asked for: *phenotyped / phenotyped-and-normal /
not phenotyped*. **Change:** reduce it to those three states, drop the count from the ranking
entirely (V5), and keep the term list in the drawer where it already is.

### V10 — The Pareto headline has little discriminating power on the list sizes the app is actually used with, and nothing on the figure says so.

**Evidence.** Fronts measured across three real runs: 4 of 14 (liver set), 3 of 14
(`de_realistic.csv`), and **5 of 7** on `de_broken.csv` (run `20260924T065307Z-56a075`:
`PARETO: ['BRAF', 'KCNQ1', 'GAPDH', 'MARCHF1', 'OR51E2']`). On a 7-gene submission 71% of the
figure gets a headline diamond, including GAPDH — the canonical housekeeping negative control.

My own inspection of the 14-gene front confirms the *arithmetic* is correct in every case I
checked by hand, and on the liver set the biology is genuinely good: PCSK9, TTR and ASGR1 are
three of the best-supported liver targets in existence and SERPINA1 is a real one. But MARCHF1 is
in the headline of `de_realistic.csv` with the **worst** druggability in the set (0.0), no drugs,
no rodent data, and a median available rank of 0.346 (11th of 14) — non-dominated only because no
single gene beats it on all four axes at once. "Non-dominated" is not "good"; with four axes it
mostly identifies unusual *combinations*, including bad ones.

**Change.** Print the front size as a fraction on the figure (`◆ 5 of 7 genes — on a list this
short almost nothing is dominated`), and suppress the diamond entirely below a stated n. Say on
the figure that membership is a property of the submitted list, not of the target: removing PCSK9
from my list promotes DDR1 (0 drugs, 0.05 liver specificity) into the headline.

### V11 — Invariant I5 reports PASS on a warm cache while Europe PMC is down.

**Evidence.** `runs/20260924T065120Z-537fd6` reports `I5 PASS — 14 Europe PMC echo(es)
byte-identical`. At the same time, a direct request returns
`HTTP 503 text/html … <title>503 Service Temporarily Unavailable</title>` from nginx, and
`cache/europepmc.search/v1` holds 26 entries. `EuropePMCAdapter.fetch_one` returns the stored
`echo_ok` without a call, so the PASS is a replay. This sits badly beside the build's own stated
principle ("a check that could not run is not a check that passed") and beside I6, which correctly
bypasses the cache for exactly this reason.

**Change.** Count live vs replayed echoes separately and put both in the detail string; report
`NOT_EVALUATED` when zero were live. Or bypass the cache for I5 as `five_id_regression` already
does for I6.

### V12 — `clinical_activity` is a three-state categorical rendered on a continuous colormap, and its colour depends on which other genes were submitted.

**Evidence.** Encoded −1 / 0 / +1. In the liver run only {0, 1} occur, so the norm is [0, 1] and
"dormant" renders `#440154` — the same darkest purple that means "0 drugs" on the phase ring one
band away, and "least liver-specific" two bands away. Submit one gene with a stopped-for-cause
history and the norm becomes [−1, 1], so *the same dormant gene's cell turns mid-teal* with no
change to that gene's data.

**Change.** Fixed three-colour categorical palette for this ring, outside the value colormap, with
its three labels in the legend.

### V13 — `k` is inflated by two rings the user supplies himself, and tops out at 9 on a 10-ring figure.

**Evidence.** `rank_genes` is called before `completeness` is added, over nine rings of which
`logfc` and `neg_log10_padj` come from the submitted table and are therefore never missing. Every
gene in every run I did has `k ≥ 2` for free; the figure's stamp reads *"k = rings with a real
measurement for that gene"* and the key reads *"10. k — rings available — [7, 9]"* on a figure
with ten rings. TMEM229B, a gene about which essentially nothing is known, shows k=7.

**Change.** Count k over external rings only (max 7), label it as such, and state the maximum on
the figure.

### V14 — The pipeline summary line can read as self-contradictory.

**Evidence.** EPHA2: `"3 agents, 3 active, 3 stopped for cause"` — `active` counts rows whose
dominant status is active, `stopped` counts rows with any for-cause stop anywhere in 611 trials,
so both are 3 of 3. D6 asked this line to answer "is this crowded?" in one glance; it currently
reads as three agents both running and killed.

**Change.** Make the two counts disjoint, or phrase as `3 agents · 3 with active trials · 3 with
≥1 trial stopped for cause`.

---

## 3. NOTE

### V15 — The "Rodent" ring is mouse-only, and S6's source was dropped against the wrong evidence.

`PLAN.md` §2 ring 7 justifies using OT `mousePhenotypes` with *"[was wrong, QA S4] IMPC as planned
returns a silent zero"*. But COUNCIL S6 did not recommend IMPC — it recommended **Alliance of
Genome Resources**, which aggregates MGI *and* RGD and is why the ring is called "rodent"
(verified live in v0: `/api/gene/MGI:88127/phenotypes` → `total: 34`). A finding about IMPC was
used to retire a different source. Either rename the ring "Mouse phenotype lead" or revisit
Alliance. This is a labelling error, not a data error.

### V16 — Two v0 items that were simply not carried over, both NOTE-level then and now.

M5 asked for a line on the figure noting that embryonic-lethal lines drop out of adult
phenotyping — which biases exactly the essential genes the safety ring flags as risky. It is not
on the figure. M10 asked for ortholog identity beside the rodent ring; `mouseOrthologMaxIdentity
Percentage` is already in the `prioritisation` payload the app fetches and is unused, so a mouse
knockout is currently shown without the conservation context that makes it interpretable.

### V17 — M10's directionality point is still open, and the colormap now makes it worse.

`logfc` is a signed quantity on a sequential colormap with no zero anchor: GFAP at −3.3 renders
`#440154` (darkest) and TTR at +3.1 renders `#fde725` (brightest), so a strongly down-regulated
gene reads as "worst" on the ring where every other ring uses dark for "worst". ACTB at +0.15 sits
at an arbitrary mid-teal with nothing marking zero. A diverging colormap centred on 0 fixes the
rendering; M10's actual question — whether up-regulation is the therapeutic direction — remains
one the figure cannot answer and should say it cannot.

### V18 — Ring identity is only recoverable by counting.

The ten rings are labelled solely by a numbered list in the top-left corner. With ten equal-width
concentric bands, no radial labels and no colourbar, reading a specific cell means counting inward
and then reading a range off a corner list. This is the figure the morning review is a screenshot
of.

### V19 — The Europe PMC correction (report §"One correction to a review"): accepted; I could not re-verify it live today.

The build claims the unbalanced `"BRAF` returns `hitCount: 101223` with a byte-identical echo, and
therefore validates queries before sending as well as asserting the echo after. Europe PMC is
503-ing for every request I made on 2026-09-24, so I cannot reproduce the measurement. The
*reasoning* is sound and I accept the change on its merits: an echo detects silent
reinterpretation, not a malformed query the server is willing to run, and these are different
failure modes. Two observations. The claim now sits in the code and the report but is untestable
while EBI is down, so the two skipped tests are correctly skipped rather than passed. And in
practice `validate_query` can never fire on the app's own queries — `f'"{symbol}"'` and
`f"@GENE_{symbol}"` are balanced by construction — so it guards a case the app cannot currently
produce. Keep it; it costs nothing and the next query template may not be safe.

---

## 4. Defensible calls I would not have made, and am letting stand

- **`clinical_activity` precedence: "active" beats "stopped for cause".** D4 gave three states and
  no precedence. The build chose active-wins and documented it with the numbers (BRAF: 31
  for-cause stops out of 1,382 trials against 10 of 18 agents actively recruiting), with the
  for-cause count in the detail and the drawer. I would have made stopped-for-cause sticky. The
  build's reasoning is right that mine would mislabel BRAF, and it wrote down what it did.
- **`NOT_EVALUATED` rather than `FAIL` when a source never answers.** It still raises the banner.
  Distinguishing an outage from a broken join is correct, and I5/I6 apply it consistently.
- **Refusing `ALB` as ambiguous** (2 targets: FBF1, ALB) rather than guessing, and **rendering
  `Trp53` without auto-converting it** to human TP53 with a `possible_mouse` ledger entry. Both
  cost a gene and both are the right call.
- **ChEMBL confined to the drawer**, driving no ring and no rank, per PLAN §8. Verified: the
  records reach `drawers[g]["chembl"]` and nothing else.
- **Failures never cached**, so a transient 503 cannot become a permanent `no_data`. This is right
  even though it is what makes V3 vary run to run; the fix belongs in the ranking, not the cache.
- **The drop ledger.** 16 in / 7 rendered / 9 ledgered on the broken fixture, with a specific
  reason per row including the two Excel date corruptions, and `I4` asserting the identity before
  anything is drawn. This is the best-built part of the app.

---

## 5. Direct answers to the seven questions

1. **Known-answer sets.** Rings that disagree with a target biologist: `clinical_phase` and
   `clinical_activity` (V1 — EPHA2 and KCNH2 read as approved and active with no approved drug
   for either; fault is source *and* transformation *and* rendering), `safety` (V4 — cannot
   separate clean from unstudied; fault is transformation misreading the source's encoding),
   `regional_specificity` (V6/V7 — fault is rendering and transformation, the data is fine),
   `rodent` (V9 — fault is transformation and rendering). Rings that agreed: `druggability`
   ordered the set sensibly apart from V8's inert key; the user's own logFC/padj are faithful; and
   on the liver set the headline picked PCSK9, TTR, ASGR1 and SERPINA1, which is a defensible
   answer arrived at partly for the wrong reasons.
2. **The ranking.** Built as M3 asked: rank-within-set, Pareto headline, median available rank
   with k, weighted score present and labelled. The front survives my inspection arithmetically in
   every case I checked, and leave-one-out is mathematically clean — removing a gene only ever
   *adds* members, never drops one. It is **not** stable in the way the principal will assume:
   membership is a property of the submitted list (removing PCSK9 promotes DDR1), and a single
   missing cell rewrites it (V2, V3). Domination was transitive in every triple of the real run I
   tested (0 intransitive triples of 2,184).
3. **Missingness.** The no-imputation rule holds at the cell level and in the CSV — `no_data`,
   `not_applicable` and `failed` are distinct states, distinctly hatched, provably off-colormap
   (I2 resolves the hatch patterns rather than comparing `url(#…)` strings, which is the right
   check), and k is drawn. It breaks inside the cell (V4, V8) and in the Pareto rule (V2).
   **Yes, a gene with small k can reach the front — on one axis, and it can evict genes with
   four.** What that means for how the figure is read: a diamond currently means "top of at least
   one axis, or unbeaten on the few axes we happened to retrieve", and the reader will read it as
   "best target on this list".
4. **Drug pipeline rings.** Two rings, correctly separated, AACT-filtered for status per D1's
   second gap — that part is right. The polypharmacology trap is **not** avoided: it is warned
   about in the drawer and unguarded in the ring (V1).
5. **The rodent lead.** The exclusion is right and the phenotyped-but-normal case is handled
   better than the question assumes (V9). The ring as drawn is not evidence of anything about the
   target; reduced to three states it would be.
6. **Figure versus support.** The thing a reader will reasonably and wrongly conclude: that the
   bright yellow cells on the clinical rings mean "this target is clinically validated" (V1 —
   hERG and EPHA2 say otherwise), and that a diamond means "best target on this list" (V2, V10 —
   on the broken fixture GAPDH has one). Second order: that colour means the same thing between
   two runs (V6, V12), and that nine dark-purple specificity cells are alike (V6).
7. **v0 items got wrong, ignored or reinterpreted.** M4 "never fold this into the score" —
   violated, and the figure claims otherwise (V5). D2 "never let a polypharmacology hit silently
   set a ring to approved" — half-implemented (V1). D7 "keep the clinical ring out of the weighted
   sum" — `clinical_phase=1.0` is in the weights. M7/S1 two-ring druggability split and Pharos TDL
   — dropped without argument, and the resulting missingness on every Pareto axis is what V2/V3
   exploit (V8). S4 gnomAD direct — dropped; constraint now comes only from OT. S6 Alliance —
   dropped against a finding about IMPC (V15). M5's figure note — absent (V16). The Europe PMC
   correction — accepted, unverifiable today (V19).
