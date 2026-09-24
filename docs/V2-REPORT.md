# targetring v2 — folding in the two reviews of the v1 MVP

Wren II, 2026-09-24. Built unattended against `docs/reviews/COUNCIL-v1-mvp.md`
and `docs/reviews/UX-v1-mvp.md`. Nothing deployed, nothing pushed, no money
spent, `~/ytpt` untouched, the gateway not restarted.

**All five council BLOCKING items are done with tests. All six UX category-A
items are done. Eleven of the fourteen SHOULD-FIX/NOTE items are done.** Tests:
**267 passed, 2 skipped** (the skips are a live EBI outage, see §1.1). Seven
invariants, all PASS on a clean run — I7 is new.

---

## 1. What is still broken — read this first

### 1.1 A source that drives no ring still dominates the clock. This is v1's open decision, unanswered, recurring.

`V1-REPORT.md` §2 measured Europe PMC at 52 s of a 50-gene run and asked you to
decide whether to move it off the render path. That decision was never made.
It has now happened again with a different source.

Measured on `fixtures/de_realistic.csv`, 2026-09-24, cold:

| source | time | drives a ring? |
|---|---|---|
| **ChEMBL** | **125 s of 126 s (99%)** | no — drawer only (PLAN §8) |
| **Europe PMC** | **80 s (64%)** | no — drawer only (COUNCIL M6) |
| Open Targets | 0.8 s | yes, five rings |
| GTEx | 0.5 s | yes |
| PubTator3 | 1.0 s | yes |

(The two overlap because they run concurrently.) On the council's 15-gene liver
set the same run took **192 s cold and 214 s warm** — warm is *slower*, because
failures are deliberately never cached, so every run re-pays the full cost.
EBI is degraded as I write: ChEMBL returns HTTP 500 with an HTML error page and
a direct `curl` times out at 20 s.

Every source is now timed separately into the manifest, and a drawer-only source
taking more than 40% of source time raises a run condition on the figure. **That
reports the cost; it does not fix it, and fixing it is your decision, not mine**
— PLAN §8 puts ChEMBL in the drawer deliberately and removing it from the render
path changes what the app fetches. See §6.

### 1.2 The speed target is still missed, and by more than v1

`PLAN.md` §5 asks for cold 50 genes under 60 s. With EBI healthy a 15-gene cold
run was **25 s** (measured earlier today, all seven invariants PASS). With EBI
degraded the same run is **192 s**. Neither is a 50-gene measurement; I did not
run one, because it would have measured EBI's outage rather than this build.
**Unproven either way.**

### 1.3 What the V1 attribution rule cannot do

`clinical_phase` now requires human genetic evidence linking the drug's
indication to the target (§2.1). The cost, stated because it is real: **a target
drugged only for indications with no human genetic support renders
`not_applicable`** rather than a phase. That is a visible, labelled state and it
makes the gene ineligible for the headline — conservative, never flattering —
but it is a false negative for a genuinely on-target drug whose disease has no
genetic evidence in Open Targets. I tested the case I thought most likely to
break it (EGFR × NSCLC, where the driver is somatic) and it survives on
`genetic_association` 0.75. I did not find a real false negative; I did not
prove there are none.

### 1.4 Interactions between three separate fixes that shrink the headline

V7 (a 1 TPM expression floor on regional specificity), V2 (all four axes
required for eligibility) and V10 (no diamond below 10 eligible genes) compound.
On `de_realistic.csv` in Liver, 7 of 14 genes fall below the TPM floor, so only
7 are eligible, so **no diamonds are drawn at all**. Each rule is right on its
own and the figure says exactly what happened, but the combined effect is a
headline that often does not appear. That may be correct behaviour or too
conservative; it is a judgement I made and it wants your eye.

### 1.5 Still not built, still not claimed

- **LLM triage** — unbuilt. `with_triage=True` raises `NotImplementedError`.
  $5.00 of the $5.00 cap unspent.
- **Rate limits** — unmeasured under parallel load. Concurrency capped at 4 per
  host, any 429 a hard run failure.
- **ChEMBL** — drawer evidence only, drives no ring and no rank. Verified by a
  test that walks `RING_SPECS` and `RANKED_RINGS`.
- **Weights** — provisional until you sign them off. Printed on the figure.
- **Licensing** (PLAN §9) — still a human job before any workplace use.
- **The ranking is not validated biology.** The figure says so in its own
  footer.

### 1.6 Left undone from the reviews

| item | why |
|---|---|
| V8 (part) — the M7 two-ring druggability split, and Pharos TDL | The arithmetic half is fixed (§2.4). The two-ring split and a new source are scope, not defect repair, and S1's case for TDL — "the only axis with zero missing values" — is now more attractive, not less, given §1.4. Recommended in §6. |
| V18 — ring identity recoverable only by counting | The key is now sans, narrower and no longer overprints the arcs, and a colour bar exists. Radial per-ring labels inside a pycirclize polar axes I could not do safely in the time. |
| B2 (part) — cancel button, per-stage progress | The elapsed counter and the cleared stale result are in (A2). A cancel endpoint needs a task handle in `app.py`; `run_analysis` is awaited inline. |
| B6 — ChEMBL rows show `CHEMBL2105729`, not names | Needs `pref_name` from an API that is currently 500-ing. |
| B7 — the drawer dies on server restart | `_runs` is still in memory. Rehydrating from `manifest.json` is ~2 h. |
| B12 — `runs/` grows unboundedly | Now ~1.6 MB/run (the slide render and caption added ~0.4 MB). Untouched: deleting run artefacts is a data-safety decision. |
| Illustrator `<g id="ring-…">` grouping | Would need restructuring how pycirclize emits patches; the per-cell `gid`s that the invariants depend on are load-bearing and I did not want to disturb them unattended. |

---

## 2. Tier 1 — the five council BLOCKING items

### 2.1 V1 — polypharmacology set the furthest-phase ring to *Approved* ✅

**The defect.** `clinical_phase` took `max(maxClinicalStage)` over every drug row
with no test that the indication had anything to do with the target. EPHA2
rendered `5.0`, *"Approved (3 candidates)"*, at the brightest end of the
colormap, on vandetanib (medullary thyroid, RET/VEGFR), dasatinib (CML,
BCR-ABL1) and regorafenib (colorectal). There is no approved EPHA2 drug. D2 was
implemented as drawer prose; V1 asked for a constraint on the ring.

**What I did.** Two parts, as the council required, and both turned out to be
load-bearing for different cases.

*(i) Attribution.* A new batched `associatedDiseases(Bs: [...])` query asks, for
exactly the diseases a target's drugs are indicated for, what evidence links the
target to them. A stage counts only when at least one indication carries human
genetic evidence at ≥ 0.1.

I chose the datatypes by calibration against known answers, not by reading the
list of names. **This is where I diverge from the review's wording**, which says
"mechanistic". Measured 2026-09-24:

| datatype | verdict | why |
|---|---|---|
| `clinical` | excluded | it *is* the drug; it attributes everything by construction |
| `literature` | excluded | co-mention, inflated by the trials themselves. EPHA2 × colorectal = 0.781 with no genetics |
| `rna_expression` | excluded | being expressed where a disease occurs is not attribution |
| `affected_pathway` | excluded | a property of the pathway. EPHA2 × squamous cell lung carcinoma = 0.760, purely through Eph receptor signalling |
| `somatic_mutation` | **excluded** | **does not discriminate.** BRAF returns 0.456 for thirty-odd unrelated tumour types — gallbladder, myelodysplastic syndrome, rhabdomyosarcoma, adrenal cortex carcinoma. A pan-cancer "BRAF is a cancer gene" floor |
| `animal_model` | excluded | cross-species; EPHA2 reaches 0.499 on uveal melanoma |
| `genetic_association`, `genetic_literature` | **counted** | the only datatypes non-circular with the drug *and* specific to the target–disease pair |

My first implementation used the council's broader "mechanistic" set and **it
did not fix the defect**: EPHA2 still came back Approved via dasatinib for
squamous cell lung carcinoma (`affected_pathway` 0.760), and BRAF was attributed
to renal cell carcinoma via sorafenib on `somatic_mutation` 0.304 — which is the
exact case `PLAN.md` §2 names as the thing to prevent. Narrowing to genetics
fixed both. Evidence, live:

```
EPHA2 × colorectal / CML / med. thyroid / SCC-lung   genetics 0.000  -> not attributed
BRAF  × renal cell carcinoma (SORAFENIB)             genetics 0.000  -> not attributed
BRAF  × hepatocellular carcinoma (SORAFENIB)         genetics 0.000  -> not attributed
BRAF  × melanoma (VEMURAFENIB)                       genetics 0.699  -> attributed
EGFR  × non-small cell lung carcinoma (AFATINIB)     genetics 0.75   -> attributed
KCNH2 × atrial fibrillation (DOFETILIDE)             genetics 0.598  -> attributed
PCSK9 × familial hypercholesterolemia                genetics 0.859  -> attributed
HMGCR × cardiovascular disorder (PRAVASTATIN)        genetics 0.46   -> attributed
```

An unattributed target is **`not_applicable`** — hatched, off-colormap, a
distinct state — not a lower number that would read as "less validated" on the
same scale. A *failed* attribution query is `failed`, not "unattributed": we
asked and could not find out, and guessing either way is a confident wrong
answer.

*(ii) The liability flag.* `⚠` on the spoke label where Open Targets records a
cardiac-conduction, cardiotoxicity or mortality liability, with the matched term
carried into the drawer so the claim is auditable.

**I did not call it "anti-target", and that is a deliberate disagreement.** The
council asked for "an anti-target flag". On the council's own liver set the term
list flags BRAF (`heart disease`) and EGFR (`cardiomyopathy`) as well as KCNH2
and KCNQ1. BRAF and EGFR are not anti-targets; Open Targets genuinely records
those liabilities for them. The flag is named for what the data supports — *this
target has a recorded cardiac or mortality liability* — and the figure says
exactly that.

**Why both parts were needed.** KCNH2's drugs *are* attributable: dofetilide and
ibutilide really do act on hERG, and KCNH2 × cardiac arrhythmia scores
`genetic_association` 0.83. So attribution alone never catches the canonical
anti-target. Part (ii) is what does.

**Evidence it works** (live, the council's liver set, `5-council-list-figure.png`
and `5b-drawer-epha2-unattributed.png`):

```
EPHA2   3 agents · 3 with an active trial · 3 with ≥1 trial stopped for cause
        · 0 indicated for a disease attributable to this target
        clinical_phase = not_applicable        (v1: 5.0, brightest fill)
        not eligible for the headline — 3 of 4 Pareto axes
KCNH2   clinical_phase = 5.0 ⚠  4 recorded liabilities (Torsades de Point,
        Increased Mortality, Reduced survival, prolongation of QT interval)
BRAF    clinical_phase = 5.0    VEMURAFENIB × melanoma, genetics 0.699
```

> **Correction, 2026-09-24 (§v2.1).** That BRAF line was not true of the code
> when it was written, and QA-v2-retest §3 caught it. The value `5.0` is right;
> the exemplar was not. The max-evidence tiebreak named **SORAFENIB TOSYLATE ×
> lung cancer** — genetics 0.83 beat vemurafenib's 0.699 — and sorafenib is not
> approved for lung cancer. That is a false clinical claim about a real drug,
> printed by the figure that exists to prevent exactly that. It is fixed; BRAF
> no longer names it. The exemplar BRAF prints today is not vemurafenib either.
> See **§v2.1 Fix 2** for what it is, and for why the report was corrected
> rather than the tiebreak.

**Tests.** `TestV1IndicationAttribution` (9 tests) reproduces the council's EPHA2
case verbatim, the sorafenib/RCC case `PLAN.md` names, the pan-cancer somatic
floor, pathway membership, circular evidence alone, a trace-level score, and the
failed-query case. `TestV1SevereLiabilityFlag` (4) covers KCNH2 and the figure.

**Also fixed here:** an unmapped `maxClinicalStage` enum returned `value(1.0)`
in v1, silently absorbing any new enum value Open Targets might introduce. It is
now `no_data` naming the values seen.

### 2.2 V2 — one axis could dominate four, and the printed note said the opposite ✅

**The defect.** `_dominates` compared only axes where *both* genes had a value.
Fewer shared axes means fewer conditions to satisfy, so missingness made
domination **easier**. The council's probe: a gene whose only Pareto axis was
`druggability = 1.0` entered the front and **ejected ASGR1 and SERPINA1**, each
of which had all four. The note the app printed — "fewer axes make domination
harder" — stated the opposite of what the code did, and so did the docstring.

**What I did.** Exactly what the review asked. Eligibility is all of
`PARETO_AXES`. Ineligible genes are neither in the front nor able to remove
anything from it, and carry a note naming the missing axes. No fallback to
shared-axis comparison. The note text and the docstring were rewritten in the
same commit.

**Tests.** `TestV2OneAxisCannotDominateFour` reproduces the council's probe:
baseline front `{ASGR1, PCSK9, SERPINA1, TTR}`, add the one-axis gene, assert it
neither enters nor evicts. Plus an assertion that the string "harder" is gone
from the note.

### 2.3 V3 — a transient failure silently rewrote the headline ✅

**The defect.** A 503 on APOB — never in the front either way — deleted ASGR1 and
SERPINA1 from it, because losing its own safety value let APOB dominate on the
three axes that remained. No invariant fired, the manifest recorded nothing, and
the evicted genes carried no note.

**What I did.** V2's eligibility rule removes the mechanism: a gene missing an
axis can no longer dominate anything. What remains is that a *dominator* can
withdraw, which only ever enlarges the front — the safe direction. Plus, as the
review asked: `no_data` cells on Pareto axes are counted per axis, carried into
the manifest as `pareto_completeness`, and surfaced on the figure.

**I did not use the red banner for it, and that is deliberate** — see §3.1. An
incomplete axis is a run condition, not a violated invariant: the data is not
wrong, it is incomplete, and the figure correctly marks the affected genes
ineligible. Instead I added a real invariant for the part that *would* be wrong.

**New invariant I7 — headline eligibility.** Every gene in the front has a real
value on all four axes, *and* the diamonds drawn on the figure are read back out
of the SVG and matched against the ranking. It is the check that would have
caught V2's original defect. It is in the manifest's PASS/FAIL list.

**Tests.** `TestV3TransientFailureCannotRewriteTheHeadline` sweeps every
(gene, axis) cell to `no_data` across a 5-gene set and asserts the front never
loses a gene other than the failed one. `TestI7HeadlineEligibility` (6 tests)
covers the pass case, the one-axis-in-front failure, a figure/ranking
disagreement, the V10-suppressed case, and the manifest listing.

### 2.4 V4 — the safety ring could not separate "no liability" from "nobody looked" ✅

**The defect.** `_mean_of_keys` averaged whichever prioritisation keys were
present. Open Targets emits `hasSafetyEvent` and `isCancerDriverGene` only when
they are negative. Dropping an absent key from a mean is arithmetically
identical to imputing it at the mean of the present keys — so "never impute"
held at the cell level and broke inside the cell. A clean gene's "Safety" ring
was essentiality and constraint alone, a constraint number carrying no safety
information, rendered indistinguishably from a real 4-key mean.

**What I did.** The review offered two options; I took the first (decide per key
what absence means) and settled it by a **live census of 24 targets** rather than
by assumption:

```
key                            present  absent  observed values
hasSafetyEvent                       9      15  -1 x9
isCancerDriverGene                   5      19  -1 x5
hasHighQualityChemicalProbes         5      19  +1 x5
geneEssentiality                    24       0  -1 x4, 0 x20
geneticConstraint                   24       0  continuous
hasLigand / hasPocket / hasSmallMoleculeBinder  24  0  0 or 1
```

All three sparse keys are binary fields with one end left unwritten, and that
end is `0.0` in every case. So absence is a *measured* value, mapped to 0.0, and
the denominator is 4 for every gene. A gene with **no prioritisation record at
all** stays `no_data`; a *dense* key going missing refuses a shorter mean and
returns `no_data` naming the key, because a different denominator is exactly the
V4 defect.

Measured effect: PCSK9 safety +0.225 → **+0.1125**; PCSK9 druggability 1.0 →
**0.75**; KCNH2 druggability 0.667 → **0.50**.

**Where I disagree with the review.** V4 says a constant denominator lets the
ring "separate clean from unstudied". It does not, and I have not claimed it
does. Open Targets not holding a safety-event record is a fact about Open
Targets, not evidence that a target is safe — the app's own drawer has always
said "absence here is weak evidence of safety". What the fix buys is
**comparability**: the numbers now mean the same thing gene to gene. So I also
renamed the ring to what it honestly measures: **"Safety flags & constraint —
NOT evidence of safety"**, and the cell detail names which keys were read and
which were inferred from absence. I did not add the separate binary safety ring
the review offers as its second option, because the UX review is simultaneously
telling me the figure is overcrowded; that is recorded as a live disagreement,
not a silent skip.

**The same defect on druggability (V8)** is fixed by the same mechanism: BRAF
`mean(1,1,1,1)` and PCSK9 `mean(1,1,1)` were both exactly 1.0, so
`hasHighQualityChemicalProbes` was arithmetically inert. Now BRAF 1.0 > PCSK9
0.75 = DDR1 0.75 > KCNH2 0.50, and the probe key does real work.

**Tests.** `TestSparsePrioritisationKeys` (7) covers the denominator, the clean
gene, the inferred-key detail, both V8 cases, the empty record and the missing
dense key. `TestSparseKeyCensusStillHolds` re-runs the census against the live
API and **fails loudly if Open Targets ever emits the other value explicitly** —
at which point absence is no longer a measured negative and the mapping would be
imputation.

### 2.5 V5 — the figure's footer contradicted the code four lines above ✅

**The defect.** The rendered footer read *"Rodent ring: … **Never folded into any
score**"* directly below `Default weights … rodent=0.5`, and `rodent` was in
`RANKED_RINGS` too — so it fed the weighted score *and* the median available
rank. Re-scoring the council's run without it moved TP53 #7 → #9 and BRAF
#4 → #6.

**What I did.** `rodent` out of `DEFAULT_WEIGHTS` and out of `RANKED_RINGS`.
Per D7, `clinical_phase` out of the weights too; it remains a Pareto axis.

Rather than trust a comment to keep the footer and the weights in step,
**`assert_footer_is_true()` checks the claim against `DEFAULT_WEIGHTS` and
`RANKED_RINGS` on every render.** Putting either ring back now fails the render
instead of producing a figure that lies about its own method.

**Tests.** `TestV5TheFooterMustNotContradictTheCode` (5): the exclusion list, the
rodent ring reaching no rank, and two tests that put each ring back and assert
the render refuses.

---

## 3. Tier 2 — UX category A, all six

### 3.1 A1 — a third-party outage stamped "DO NOT TRUST THIS FIGURE" ✅

This is the one you flagged for judgement, and softening the banner would not
have resolved it.

**The defect.** Europe PMC sets no ring value — `PLAN.md` §2 and COUNCIL M6 both
say so, and `V1-REPORT.md` says it "buys nothing for the figure". When EBI was
down, invariant I5 returned `NOT_EVALUATED`, `_draw_banner` fired, and a
full-width red **INVARIANT FAILURE — DO NOT TRUST THIS FIGURE** was drawn across
the middle of the rings *in the SVG*. Directly beneath it the app printed its own
amber note saying the PubTator ring was unaffected. The UX review's judgement:
"Cry wolf twice and the banner stops working the day it is real."

**The resolution.** A source being down and an invariant being violated are
different facts, so they get different channels — different in *every* signal a
reader uses, not just in wording:

| | red banner | amber conditions notice |
|---|---|---|
| fires on | `FAIL` only — an invariant was checked and did not hold | `NOT_EVALUATED`, source outages, incomplete inputs |
| means | **the drawn data is wrong** | **we do not know** |
| placement | across the rings, on purpose: the figure must not be readable | below the rings, outside the data |
| colour / weight | filled red, bold | outlined amber, normal weight |
| wording | "DO NOT TRUST THIS FIGURE" | "RUN CONDITIONS — not an invariant failure" |
| per line | the failure | the failure **and what it affects** |

`InvariantReport.data_is_sound` is the question the red banner answers, and is
deliberately *not* `ok` — which a `NOT_EVALUATED` result also makes false. The
same split is in the web UI (`#banner` vs `#conditions`), in the manifest
(`any_invariant_failed`, `run_conditions`) and in the figure.

Each amber line carries its consequence. I5's says *"Europe PMC sets no ring
value (PLAN §2, COUNCIL M6); the literature ring is PubTator3 and is
unaffected."* I6's says *"the Open Targets batch join was not re-verified this
run; every OT-sourced ring is unchecked, not known wrong."*

**Note the difference from the review's prescription.** UX A1 asks for "banner on
`FAIL` only… and never banner at all for a source that drives no ring". I
implemented the first clause exactly. The second I implemented as *"say which
rings it affects"* rather than *"say nothing"* — a silent outage is how you end
up with a figure whose provenance you cannot reconstruct, and the manifest is
supposed to make a screenshot self-describing.

**Also fixed:** the banner truncated at 200 characters and ended mid-URL at
`?query=%22S`. URLs are now replaced with a pointer to the manifest and what
remains is cut on a word boundary.

**Evidence:** `6-outage-is-not-a-failure.png` (amber alone, no red),
`6b-both-notices-together.png` (both in one frame, unmistakably different),
`4-invariant-banner.png` (red alone).

**Tests.** `TestA1OutageIsNotInvariantFailure` (5) asserts an outage produces no
banner line and one condition line, that a real failure still banners, that the
two reach the *SVG* differently, and that the banner text is not truncated
mid-URL. `TestV11ReplayedEchoIsNotACheckedEcho` (3) covers V11 below.

**The same principle, found in two more places while working:**

- **V11 (council).** I5 reported `PASS — 14 Europe PMC echo(es) byte-identical`
  off a warm cache while EBI 503'd every request. `EuropePMCAdapter` now returns
  an `EPMCResult` with a `live` flag; live and replayed echoes are counted
  separately and **I5 will not PASS on replays alone.** It proved itself on the
  first real run after landing: `I5 NOT_EVALUATED — every one of the 14 echoes
  came from the on-disk cache.`
- **The test suite itself.** Two ChEMBL contract tests reported FAIL while EBI
  was 500-ing, for a source that drives no ring. v1 had the right pattern for
  Europe PMC; it was written for one source. Generalised into
  `SourceUnreachable`: a transport error, a timeout or a 5xx skips with the
  status in the reason; a 200 whose shape is wrong is still a real failure.
  That is why the suite reports 2 skipped rather than 2 failed.
- **A failure with no reason.** Several httpx exceptions carry no message, so
  `str(e)` was `''` and a `failed` record's reason was the empty string. The
  legend promises "call FAILED — not the same as absent"; a failure with no
  reason keeps the distinction and throws away the only actionable part.
  `http.describe()` always names the exception type, and a test walks the
  adapter sources to keep it that way.

### 3.2 A2 — the previous run's figure stayed on screen with live download buttons ✅

`analyse()` set one status string and did not clear `#result`, so for the whole
of a cold run the page showed a complete, plausible, finished-looking figure *of
the previous gene set* with working Download SVG and Download CSV. At ~4 s/gene
that window is four minutes. `#result` is hidden on submit, `RUN` is nulled, and
an elapsed-seconds counter ticks (the cheap half of B2).

### 3.3 A3 — nothing in the export named the dropped genes ✅

The names are now in the centre of the audit render, in the caption, and in a
standalone `ledger.csv` with its own download button.

**A correction to the review.** A3 says *"I checked `values.csv`: it contains
rendered cells only, no dropped gene appears in it."* That is not so — v1's
`_write_csv` appends the full ledger to `values.csv` under a
`# DROP LEDGER` comment, and it is there in every v1 run on disk. But a second
table below a comment line is not something a CSV parser reads, which is
presumably how the reviewer concluded it was absent. **The finding stands, for a
different reason than the one given**, and the fix is a real CSV.

### 3.4 A4 — every text element was 3–5 pt at full-slide size ✅

A second render: `figure-slide.svg` / `.png`, 16:9, gene labels at 11.5 pt.
A 9 in canvas fitted to a 7.5 in slide is a 0.83 scale, so they land at
**9.6 pt** — against v1's 3.9, where an 18.3 × 15.2 in canvas on the same slide
was a 0.49 scale, with the ring key and the six-line
footer moved out to `caption.txt`. The audit render keeps every stamp — it is the
right artefact for the morning review and a bad one for a slide. Both are
download buttons.

### 3.5 A5 — no colour bar, and the rings are not comparable ✅

A colour bar on both renders, labelled **RESCALED WITHIN EACH RING / same colour
on two rings / means nothing in common**. v1 had none, and because every ring is
normalised within itself the natural radial reading of a Circos plot was invalid
with nothing on the figure saying so.

### 3.6 A6 — text collided with data inside the export ✅

Both collisions are geometry, so both are checked geometrically rather than by
eye, on every render:

- `assert_no_text_over_the_rings` — against the *inscribed circle* (the axes box
  is a square that bounds it, so a key may legitimately sit in a corner) **and**
  against the measured spoke-label artists.
- `assert_centre_fits_the_hole` — the centre block is user-driven in width (the
  broken fixture produces seven drop categories and nine names), so it is fitted
  to the hole and the fit is then asserted.
- `assert_text_within_canvas` — nothing may run off the edge.

The API field lists left the ring key for the caption and the manifest; they were
the widest line and the cause of the overprint.

**Two upstream surprises found by these assertions, both now pinned:**

1. **pycirclize leaves a tight layout engine attached**, which re-lays the axes
   out at draw time and silently undid `set_position`. The circle was ~30%
   larger than the box asked for — which is what put the footer under the bottom
   spoke labels in the first place.
2. **pycirclize sets `savefig.bbox = 'tight'` with 0.5 in of padding**, which
   grew v1's 17.5 × 14 canvas to the 18.3 × 15.2 the review measured, and would
   have made the 16:9 render 1.83:1. Passing `bbox_inches=None` does *not* help —
   matplotlib resolves a `None` kwarg from the rcParam. Saved under `rc_context`.
3. Related: **`svg.fonttype='none'`** — the reason the export is editable in
   Illustrator at all — was incidental, set by pycirclize. The review warned a
   dependency bump could silently turn all the text into outlines. Pinned here
   and asserted in `TestExportRemainsEditable`.

---

## 4. Tier 3 — council SHOULD-FIX and NOTE

| # | item | done | note |
|---|---|---|---|
| V6 | per-ring min–max destroying real 40× differences | ✅ | log10 for the specificity ratio; fixed domains for bounded rings; `assert_within_domain` so an out-of-range value raises rather than being clipped to a colour meaning a different number |
| V7 | no expression floor on the specificity ratio | ✅ | 1 TPM floor, stated on the figure. GFAP (0.216 TPM, ratio 0.270) and ACTB (760.4 TPM, ratio 0.344) no longer land in the same place |
| V8 | the probe key arithmetically inert | ◐ | arithmetic fixed with V4. The M7 two-ring split and Pharos TDL not done — see §1.6 |
| V9 | rodent direction asserted, denominator discarded | ✅ | three states. v1 put the count on the ring pointed +1, so TP53 scored 249 and was *rewarded* for the most catastrophic knockout in the set. The count and denominator now travel in the drawer |
| V10 | the front has little discriminating power on short lists | ✅ | diamond suppressed below 10 eligible genes; the fraction printed either way; the figure says membership is a property of the submitted list |
| V11 | I5 PASSes on a warm cache during an outage | ✅ | see §3.1 |
| V12 | three-state ring on a continuous colormap | ✅ | fixed three-colour palette outside the value colormap, three labels in the legend |
| V13 | k inflated by two rings the user supplies | ✅ | k over the 7 external rings only, drawn as `k=n/7` |
| V14 | the pipeline line reads as self-contradictory | ✅ | `3 agents · 3 with an active trial · 3 with ≥1 trial stopped for cause · 0 indicated for a disease attributable to this target` |
| V15 | the "Rodent" ring is mouse-only | ✅ | renamed "Mouse phenotype lead". The review is right that a finding about IMPC was used to retire Alliance of Genome Resources; revisiting Alliance is still open |
| V16 | M5's embryonic-lethal note, M10's ortholog identity | ◐ | M5's note is on the figure. `mouseOrthologMaxIdentityPercentage` is still unused |
| V17 | signed logFC on a sequential colormap with no zero anchor | ✅ | anchored on zero; equal and opposite changes equally far from the middle |
| V18 | ring identity recoverable only by counting | ◐ | see §1.6 |
| V19 | the Europe PMC correction, unverifiable during the review | — | accepted by the council; nothing to do |

**The greyscale finding (UX accessibility), also done.** `no_data` `#b3b3b3` had
relative luminance 0.4508 and viridis step 192 had 0.4503 — a greyscale contrast
ratio of **1.001**, identical, with 23 of 256 steps within 0.05. `PLAN.md` §6.2's
"provably distinct from every value colour" was true only of the colour render,
and the hatch does not survive a projector, a compressed paste or a photocopy.

Three reserved fills need three luminance bands outside the ramp and full
viridis (0.019–0.783) leaves room for two. So **the value ramp is truncated to
viridis[0.16, 0.92]**, putting it in luminance [0.056, 0.663] and leaving black,
near-white and white each clear by ≥ 0.055. It costs the brightest yellow and
the darkest purple and keeps the ordering, the perceptual uniformity and the
deuteranopia safety the review liked. `assert_greyscale_disjoint()` runs on every
render; a test puts the v1 palette back and shows the check fires — it still
passes the *colour* check, which is the whole point.

**Minor friction:** B1 ✅ (`MAX_GENES` 500 → 60, below pycirclize's 181-sector
ceiling, applied before any network work), B3 ✅, B4 ✅, B5 ✅, B8 ✅, B9 ✅,
B10 ✅, B11 ✅. B2 ◐, B6 ✗, B7 ✗, B12 ✗ — see §1.6.

---

## 5. The invariant table

Seven now. I7 is new and joins the manifest's PASS/FAIL list.

| # | invariant | status on a clean run | what would fail it |
|---|---|---|---|
| I1 | cell-count identity, CSV vs rendered SVG | **PASS** — 140 cells across 10 rings | a cell drawn and not recorded, or the reverse |
| I2 | missing-fill disjointness, hatch patterns resolved | **PASS** — 4 reserved fills, off-colormap | a palette change that collides |
| I3 | label correspondence, spoke *n* ↔ spoke *n*'s data | **PASS** — all 140 | the QA B2 off-by-one, in the artefact |
| I4 | `input == rendered + ledgered` | **PASS** — 15 = 14 + 1 | a gene disappearing unaccounted |
| I5 | Europe PMC echo byte-identical, no NCBI `ERROR` key | **PASS** — checked **live**, not replayed | silent query reinterpretation |
| I6 | the five-ID regression, cache bypassed | **PASS** — all four checks | a regression in the batch join |
| **I7** | **headline eligibility** | **PASS** — 5 of 10 eligible in the front | a gene reaching the front without all four axes, or a diamond on a gene the ranking says is ineligible |

Not relaxed, and none of them changed to make anything pass. I2 got *stronger*
(greyscale), I5 got stronger (live vs replayed), I6 is unchanged.

**New assertions that are not invariants** but run on every render and fail it:
`assert_footer_is_true` (V5), `assert_greyscale_disjoint`, `assert_within_domain`
(V6), `assert_no_text_over_the_rings` / `assert_centre_fits_the_hole` /
`assert_text_within_canvas` (A6).

---

## 6. What you need to decide

Separated from everything above, cheapest first.

1. **Move ChEMBL and Europe PMC off the render path.** They drive no ring and no
   rank, and today they were 99% and 64% of the time spent on sources.
   Drawer-on-click removes the whole cost from the render. **This is the same
   decision `V1-REPORT.md` asked for about Europe PMC and did not get** — which
   is how the same failure mode came back wearing ChEMBL's name. My
   recommendation: move both. *Cost: ~2 h. Saves ~120 s per run today.*
2. **Pharos TDL as a fifth Pareto axis** (COUNCIL S1/V8). It is one unkeyed call
   and it is the only axis with zero missing values, which directly addresses
   §1.4 — the headline that often does not appear because too few genes are
   eligible. *Cost: ~3 h.*
3. **Is §1.4's conservatism right?** V7 + V2 + V10 together mean a wrong-region
   submission produces no headline at all. Each rule is defensible; the
   combination is a judgement. *Your call, no cost either way.*
4. **The default weights** — still provisional, still printed on the figure.
5. **Licensing** before any workplace use (PLAN §9).

---

## 7. What a QA pass should re-test

For the separate QA session. Grouped by what a failure would mean.

**A. The Tier-1 fixes, against live data, not fixtures**

1. Submit the council's liver set (in `scripts/screenshots.py` as
   `COUNCIL_LIST`) in region Liver. Assert EPHA2's `clinical_phase` is
   `not_applicable`, BRAF's and PCSK9's are `5.0`, and KCNH2 and BRAF carry `⚠`.
2. Pick five targets *not* in my calibration set with a known on-target approved
   drug and five with a known polypharmacology-only relationship. Check the
   attribution rule on all ten. **This is the highest-value QA item**: §1.3 says
   I did not prove there are no false negatives, and ten fresh cases would go a
   long way.
3. Force a Pareto axis to `no_data` for a gene *in* the front and a gene *not*
   in it. Assert the front never loses a gene other than the failed one.
4. Confirm the safety ring's `mean of 4/4 keys` detail on a gene with no
   `hasSafetyEvent` and on one with it.

**B. The outage/invariant seam** — the thing most likely to regress

5. With a source genuinely unreachable (block `ebi.ac.uk` at the firewall, do
   not fake it): assert the SVG contains `RUN CONDITIONS` and **does not**
   contain `DO NOT TRUST THIS FIGURE`.
6. Force a real I4 failure (edit the ledger count): assert the red banner fires
   and the amber notice is independent of it.
7. Run twice in a row with a warm cache and EBI up. Assert I5 reports live
   echoes on the first and does **not** report PASS on replays alone.
8. Check `manifest.json` carries `any_invariant_failed`, `run_conditions` and
   `pareto_completeness`, and that they disagree with `all_invariants_pass` in
   the right direction during an outage.

**C. The figure at the boundary** — where the UX review said the problems were

9. Export `figure-slide.svg`, paste into a real 16:9 slide, and read the gene
   labels at arm's length. I measured 5.4 pt; I have no PowerPoint.
10. Open `figure.svg` in Illustrator. Confirm the text is editable (not
    outlines), and note what the missing-DejaVu-font substitution does to the
    layout — the UX review predicted the A6 collisions "will move rather than
    disappear" and I could not test it.
11. Print `figure.png` on a mono laser printer and confirm `no_data` cells are
    distinguishable from mid-scale values. The greyscale fix is asserted
    numerically; nobody has held the paper.
12. Submit 60 genes (the new cap) and confirm the figure is still readable and
    nothing collides. My collision tests cover n = 2, 6, 40.

**D. Regression surface**

13. `.venv/bin/python -m pytest -q` — expect 267 passed. Skips should only ever
    be source outages, and the skip reason says which source.
14. Both fixtures, both regions, invariants I1–I7 all PASS.
15. The broken fixture: 16 in / 7 rendered / 9 named on the figure, in
    `ledger.csv`, and in the caption.
16. A 200-gene table: confirm it is capped at 60 *before* any network work, with
    every excluded gene in the ledger, and no pycirclize traceback.

**E. What I would not bother re-testing** — covered by assertions that run on
every render and fail it: footer/weights consistency, greyscale disjointness,
domain clipping, text collisions, canvas bounds.

---

## 8. Run it

```
cd ~/targetring
.venv/bin/python -m uvicorn targetring.app:app --host 127.0.0.1 --port 8811
# then http://127.0.0.1:8811/
.venv/bin/python -m pytest -q              # 267 passed, 2 skipped (EBI outage)
.venv/bin/python scripts/screenshots.py    # needs Chrome on CDP 18800
```

Artefacts per run, in `runs/<run_id>/`: `figure.svg`, `figure.png`,
`figure-slide.svg`, `figure-slide.png`, `caption.txt`, `values.csv`,
`ledger.csv`, `manifest.json`.

## 9. Screenshots

`docs/screenshots/`, retaken on a cleared cache against the committed code.

| file | shows |
|---|---|
| `1-figure.png` | realistic list, clean run, all seven invariants PASS |
| `2-drawer-braf.png`, `2b-…-pipeline.png` | BRAF drawer and pipeline table |
| `3-broken-figure.png`, `3b-broken-ledger.png` | broken input, every drop named on the figure |
| `4-invariant-banner.png`, `4b-invariants-table.png` | a real invariant failure — red, alone |
| `5-council-list-figure.png` | the council's liver set: V1, V2, V4, V13 on live data |
| `5b-drawer-epha2-unattributed.png` | **V1**: three approved drugs, `0 indicated for a disease attributable to this target`, not eligible for the headline |
| `5c-epha2-clinical-phase-cell.png` | the same, at the cell |
| `5d-drawer-kcnh2-liability.png` | **V1 (ii)**: the four recorded cardiac/mortality liabilities |
| `6-outage-is-not-a-failure.png` | **A1**: an outage, amber only, no red banner |
| `6b-both-notices-together.png` | **A1**: both notices in one frame — the distinction, shown |

## 10. What I changed outside the repo

Nothing. `~/.openclaw/openclaw.json` still carries v1's one-line
`browser.ssrfPolicy.allowedHostnames` addition; the gateway was not restarted
and `~/ytpt` was not touched. Screenshots went through the separate Chrome on
CDP 18800, as in v1.

---

# v2.1 — the four defects QA found in the re-test

Wren II, 2026-09-24, overnight, against `docs/reviews/QA-v2-retest.md` at
`1dd3659`. Local only, nothing deployed, nothing pushed, no money spent
(triage still unbuilt, $5.00 of $5.00 unspent), `~/ytpt` untouched, the
OpenClaw gateway not restarted. One commit per fix.

QA's verdict was that the five council BLOCKING fixes hold as mechanisms and
V1 does not hold as an outcome. That is right, and the cause was the one QA
named: the gate tested the right thing and the ring then reported the wrong
quantity.

**Tests: 286 passed, 1 failed, 0 skipped.** The failure is not one of these
four and is not mine — see *Newly open*, item 1. The seven invariants are
PASS on a clean run (`runs/V21-LIVER`), and `manifest.json` now says so in
one field.

---

## Fix 1 — the phase ring reports the phase for the attributed indication ✅

**Commit** `8ea8197`.

**What was wrong.** `drugAndClinicalCandidates.rows[]` is one row per *drug*,
and its `maxClinicalStage` is that drug's global maximum across every
indication on the row — 134 of them for regorafenib. So once the V1
attribution gate correctly established that FRK × age-related macular
degeneration is a genuine link (`genetic_association` 0.40), the ring reported
a stage belonging to colorectal cancer. FRK rendered **5.0, "Approved",
brightest fill**, on a drug with no approval for the disease it was credited
with, and the drawer stated it as fact. QA is right that this is structural
and is the same failure as the council's EPHA2 case.

**What changed.** `clinicalReports[]` is one row per trial or label and carries
its own `clinicalStage` and its own `diseases`. New `per_indication_stages()`
reduces it by disease id; `clinical_phase_cell` now ranks attributed
drug–*indication pairs* on the stage that pair reached. Where a genuine
attribution has no establishable per-indication stage the cell is `no_data`,
naming the drug and the indication and saying explicitly that the global stage
was refused. Never a borrowed number.

`clinicalStage` is used rather than `trialPhase`: it is non-null and it is the
same vocabulary as `maxClinicalStage`, so one ordinal table serves both.
`trialPhase` is a second vocabulary (`PHASE1`, `PHASE1/PHASE2`) and a second
table is a second place to be wrong. Stages this build does not map
(`EARLY_PHASE_1`, `UNKNOWN`, `WITHDRAWAL`) are carried into the detail and
never guessed at.

**Evidence — FRK, live, through `OpenTargetsAdapter.fetch` → `fetch_attribution`
→ `clinical_phase_cell`:**

```
before   5.0   "Approved — REGORAFENIB for age-related macular degeneration"
after    3.0   "Phase 2 for this indication — REGORAFENIB reached Phase 2 for
                age-related macular degeneration, which is attributable to this
                target (genetic_association 0.40). 2 of 6 candidates have an
                attributable indication; the drug's own global maxClinicalStage
                reaches Approved for a DIFFERENT indication and is excluded —
                this ring is the phase for the attributed indication only"
```

Regorafenib per indication, measured: colorectal `APPROVAL` (DailyMed, PMDA),
GIST `PHASE_3`, AMD `PHASE_2`. The excluded approval is **named, not dropped** —
the drug really did reach it, for another disease, and saying so is the
difference between a correction and a disappearance.

**Re-checked, live: the ten targets in §2.1 and the seventeen QA probed.**

| set | result |
|---|---|
| QA §1.1 — the 11 true positives incl. the 5 "most likely to break" | **all still 5.0. Zero new false negatives.** |
| QA §1.2 — the 11 polypharmacology saves (ABCB1, LCK, FYN, CYP3A4, SRC, YES1, HCK, AXL, EPHB4, PTK6, EPHA2) | **all still `not_applicable`** |
| QA §1.3 — NQO2 `value(0.0)`, MAPK14 `value(3.0)` | unchanged |
| §2.1's calibration set (EPHA2, BRAF, EGFR, KCNH2, PCSK9, HMGCR) | EPHA2 `not_applicable`; the other five still 5.0 |
| FRK | **5.0 → 3.0** |

So the fix costs nothing on 27 targets and removes the defect on the one that
had it. Two targets moved from the drug's global stage to a lower
per-indication stage (FRK, and ASGR1 to Phase 2 on VUPANORSEN × inherited lipid
metabolism disorder — correct, vupanorsen is not approved).

**Tests.** `TestV1PerIndicationPhase` (8): FRK × regorafenib is not "Approved"
and *is* Phase 2; the borrowed approval is named as excluded; `per_indication_stages`
reads the reports and not the row; an unmapped report stage is never guessed at;
an attributed-but-unstageable link is `no_data` and not the global stage; the
council's EPHA2 case with per-indication stages in the fixture; and the BRAF
tiebreak (Fix 2). The existing `TestV1IndicationAttribution` (12) is unchanged
in its assertions — its `_row` fixture now carries `clinicalReports`, because a
fixture that cannot express the defect cannot test the fix.

---

## Fix 2 — the report's BRAF evidence line ✅ (report corrected, not the tiebreak)

**Commit** `7cb57d1`. **I corrected the report.** Here is why, and what changed
in the code anyway.

QA §3 is right twice over: the printed claim was false, and a document
describing behaviour the code does not have is the same class of defect as a
figure overstating its data. The tiebreak named **SORAFENIB TOSYLATE × lung
cancer** (genetics 0.83 over vemurafenib's 0.699); sorafenib is not approved
for lung cancer and is the exact drug the V1 fix exists to reject.

**The false claim is gone, as a consequence of Fix 1.** Measured live,
sorafenib reaches `PHASE_2` for lung cancer while vemurafenib reaches
`APPROVAL` for melanoma. Ranking on the stage reached *for that indication*
removes sorafenib × lung cancer from the top tie entirely, so the tiebreak can
no longer reach it. Every claim the ring prints for BRAF is now a true one.

**But it does not name vemurafenib, and I did not force it to.** BRAF today:

```
BRAF  5.0  "Approved for this indication — REGORAFENIB reached Approved for
            colorectal cancer, which is attributable to this target
            (genetic_association 0.77, genetic_literature 0.30, 1 of 9
            attributable link(s) at this stage). 17 of 18 candidates have an
            attributable indication"
```

Regorafenib *is* approved for colorectal cancer, and BRAF × colorectal carries
`genetic_association` 0.77. Both halves are true. To name vemurafenib instead I
would need a tiebreak that prefers the on-mechanism drug, and **Open Targets
gives me nothing to build one from**: every row in `drugAndClinicalCandidates`
is mechanism-annotated to this target by construction, so "does this drug act
through BRAF" does not discriminate between vemurafenib and regorafenib. A
tiebreak invented to make one documented sentence come true would be tuning to
the report, which is how the original claim got written.

So: the code's behaviour is defensible and the sentence was not. §2.1 now
carries an inline correction naming what the line said, what the code did, and
that it was false. The cell detail also now says *how many* attributable links
share the top stage, so one winner of an evidence tiebreak reads as an example
rather than as "the drug for this target".

**Still open, and I want your eye on it:** the exemplar named among genuine ties
is the strongest-evidence one, and that can be a generic parent term — BTK names
`neoplasm` at 0.19, ALK names `cancer` at 0.54. QA §2 flagged the same thinness.
The *value* is right in both cases (pirtobrutinib and lorlatinib are genuinely
approved on-mechanism); it is the printed exemplar that is uninformative.

---

## Fix 3 — `any_invariant_failed` is in `manifest.json`, and tested ✅

**Commit** `964d65b`. QA §4 is exactly right including the diagnosis:
`Manifest.write()` serialised with `asdict()`, which drops `@property`.

`Manifest.as_dict()` writes both `any_invariant_failed` and
`all_invariants_pass`. They stay properties too, so there is one definition of
each and the file cannot drift from the object. From `runs/V21-LIVER/manifest.json`:

```
any_invariant_failed: False
all_invariants_pass : True
```

**Tests.** `TestManifestCarriesTheRedBannerQuestion` (4): both keys are written;
a FAIL shows in the one field; **`NOT_EVALUATED` is not a FAIL** — which is the
UX A1 distinction the two keys exist to keep separate, and the reason
`all_invariants_pass` alone was never enough; and the file matches the object.

---

## Fix 4 — "NOT evidence of safety" renders on both figures ✅

**Commit** `c1500ce`. QA §5 is right that this is the AGENTS.md failure mode —
the fix was real in the data and invisible in the picture.

- **Audit render.** The ring key truncated at 34 characters, which is how the
  caveat became `— NOT e…`. `_key_lines` now wraps a long label onto an
  indented continuation line. Truncation is never the answer for a caveat. The
  block stays inside the width `assert_no_text_over_the_rings` measures, and
  that assertion still holds at n = 2, 6, 40 and 60.
- **Slide render.** The slide's single honest footer line now leads with it:
  *"the safety ring is NOT evidence of safety · ranking is provisional and not
  validated · …"*, at 10 pt — not in the caption, because a caveat you have to
  be handed separately is not on the figure.

`SAFETY_RING_CAVEAT` is one string in one place. In `5-council-list-figure.png`
the key now reads `4. Safety flags & constraint — NOT / evidence of safety
[−1, 1]`.

**Tests.** `TestTheSafetyCaveatReachesBothRenders` (5). The assertion reads the
drawn `<text>` elements out of the exported SVG and collapses whitespace across
them, so a caveat that **wraps** passes and one that is **elided** fails — it
has to be able to tell those apart. Parametrised over both layouts, plus the
specific v2 artefact (`"NOT e…"` must not appear), plus the caption, plus the
wrapping helper in isolation.

---

## The re-run

`runs/V21-LIVER`, the council's liver set, Liver, cold Europe PMC. EBI has
recovered since QA's night (Europe PMC 200 in 1.6 s, ChEMBL 200 in 3.3 s), so
this is a healthy-source run and not comparable to QA's timings.

```
I1 PASS  I2 PASS  I3 PASS  I4 PASS  I5 PASS  I6 PASS  I7 PASS
15 in / 14 rendered / 1 ledgered      any_invariant_failed: False
I5: 14 Europe PMC echo(es) checked live and byte-identical
```

Screenshots retaken on this code: all 13 in `docs/screenshots/`.

**Test suite: 286 passed, 1 failed, 0 skipped.** QA's two ChEMBL skips now run
and pass, because ChEMBL is answering — so ChEMBL's response contract is
verified for the first time since the retest. The one failure is below.

---

## Newly open — read this first

**1. A live test is failing, and it is a real upstream change, not an outage.**

```
tests/test_adapters.py::TestSparseKeyCensusStillHolds::test_sparse_keys_are_still_one_sided
  Open Targets now emits hasHighQualityChemicalProbes=0.0 explicitly
  (observed [0.0, 1.0]). Absence is no longer that value, so mapping it to 0.0
  would be imputation.
```

This is not mine: it fails identically on `0ef53ca` with my changes stashed,
and it is a 200 with data, not a 500. The test is doing its job. It means
`SPARSE_KEY_ABSENT_VALUE["hasHighQualityChemicalProbes"] = 0.0` — one of the
three decisions the V4/V8 fix rests on — no longer has the evidence behind it,
because absence and an explicit 0.0 now coexist. **I did not relax it and I did
not change the mapping.** What absence of that key now means is a judgement
about the source, it moves the druggability ring, and it is yours. Until it is
made, the druggability ring may be treating "not stated" as "no probe" on
targets where Open Targets would have said 0.0 explicitly.

**2. The `clinical_activity` ring is empty for every gene.** Commit `90381e1`.
Open Targets no longer emits `AACT` as a `clinicalReports` source and labels
the same trials `ClinicalTrials.gov`: 0 AACT rows across 109 targets today
against 4,195 in the previous night's cache. The ring reads AACT only, so it is
`no_data` throughout — visible on the figure as hatched cells and a lower `k`,
never imputed. I made the detail name the sources it *did* find, so the rename
is diagnosable instead of mute, and I did **not** change what the ring counts:
whether ClinicalTrials.gov rows are the population AACT was is a
source-semantics decision and not one to take unattended. QA's §2 FRK
transcript shows "2 with an active trial"; that line now reads 0 for the same
reason.

This is also why the cache schema bump mattered: the warm v1 cache was serving
last night's AACT rows and hiding the change.

**3. The exemplar among genuine ties can be a generic parent term** — Fix 2
above. Value right, exemplar uninformative.

**4. QA §6(c), not done.** §1.4 still does not say that the headline also
vanishes on a single transient failure when `n_eligible` sits on the
`PARETO_MIN_ELIGIBLE` threshold, as it does on the council's own liver set.
QA is right; it is a documentation line and I ran out of night before the
four defects were done. On `V21-LIVER` the set is again exactly at 10.

**5. Not re-evaluated, unchanged from QA §11:** the slide in a real
presentation tool, Illustrator font substitution, a mono laser print. No
PowerPoint, no Illustrator, no printer on this machine.

---

## What you need to decide

1. **`hasHighQualityChemicalProbes`** — what does absence mean now that Open
   Targets also writes 0.0? Until you say, that test stays red. *(Newly open 1.)*
2. **The trial-status source** — accept `ClinicalTrials.gov` in place of `AACT`,
   or leave the activity ring dark? *(Newly open 2.)*
3. Everything already in §6, unchanged: ChEMBL and Europe PMC on the render
   path, the weights, the M7 druggability split.

---

# v2.2 — the four changes you approved

Wren II, 2026-09-24. Local only, nothing deployed, nothing pushed, no money
spent (triage still unbuilt, $5.00 of $5.00 unspent), `~/ytpt` untouched, the
OpenClaw gateway not restarted. Six commits, one per change plus two for
defects the changes exposed.

**Tests: 354 passed, 0 skipped.** All seven invariants PASS on a clean cold run
(`runs/V22-COUNCIL-COLD`), and on all six fixture × region combinations.

---

## Read this first: item 1 removed the headline, and item 4 did not bring it back

Two of your four changes pull hard against each other, and the net effect on
`de_realistic.csv` and on the council's liver set is that **the Pareto headline
is no longer drawn at all**. Measured, same gene set, same day:

| build | Pareto axes | eligible / 14 | front | ◆ drawn? |
|---|---|---|---|---|
| v2.1 (yesterday) | 4 | **10** | EGFR, PCSK9, TTR, SERPINA1, ASGR1 | **yes** |
| + item 1 only | 4 | **4** | EGFR, DDR1 | no |
| + item 1 + item 4 (HEAD) | 5 | **4** | EGFR, DDR1 | no |

(Council liver set, region Liver. `de_realistic.csv` in Liver: 7 → 2 → 2
eligible; the diamond was already suppressed there at 7.)

**Item 1 is the cause, and it is your decision working as specified.**
`hasHighQualityChemicalProbes` is absent for **9 of 14** council targets and
**11 of 14** on `de_realistic.csv`. Absent now means `no_data` for the whole
druggability cell — a shorter mean is the V4 defect and a substituted 0.0 is the
imputation the census test caught — so those genes lose a Pareto axis and become
ineligible. Nine hatched cells appeared on the druggability ring; one cell (the
single council target Open Targets writes an explicit `0.0` for) gained a
meaning it did not have before.

**Item 4 cannot offset it, and the arithmetic says so.** Eligibility is a
conjunction over the axes (COUNCIL V2), so *adding* an axis can only hold
eligibility level or lower it. Pharos held a record for every gene that was
already eligible, so it lowered nothing — and it raised nothing either. Measured
on identical cells with only the axis list varied:

```
de_realistic   4 axes: 2 eligible, front {EGFR}          ◆ not drawn
               5 axes: 2 eligible, front {EGFR}          ◆ not drawn
council-liver  4 axes: 4 eligible, front {DDR1, EGFR}    ◆ not drawn
               5 axes: 4 eligible, front {DDR1, EGFR}    ◆ not drawn
```

S1's claim — "the only axis with zero missing values" — is true, and it is not
the same claim as "this will make more genes eligible". §1.4 asked why the
headline keeps not appearing; the answer today is the probe key, not the number
of axes. **Your call**, and the two options I can see are in *What you need to
decide* below.

---

## Item 1 — `hasHighQualityChemicalProbes` semantics ✅

**Commit** `7cbf9a9`. The red test is green, fixed rather than deleted.

Your decision, implemented literally: an explicit `0.0` is a real value meaning
*"Open Targets looked and there is no high-quality probe"* and does arithmetic
work in the 4-key mean; a genuinely absent key is `no_data` and the cell refuses
the mean. The key left `SPARSE_KEY_ABSENT_VALUE` and joined a new
`EXPLICIT_ZERO_KEYS`. The refusal names which of the two reasons applied, so a
hatched druggability cell is diagnosable at the cell.

**No other key this build reads has acquired the behaviour.** Live census,
2026-09-24, 23 targets, every prioritisation key:

| key | present | absent | emits an explicit 0.0 |
|---|---|---|---|
| `hasHighQualityChemicalProbes` | 8 | 15 | **yes** — your decision |
| `hasSafetyEvent` | 12 | 11 | no (only −1.0) |
| `isCancerDriverGene` | 5 | 18 | no (only −1.0) |
| `mouseOrthologMaxIdentityPercentage` | 22 | 1 | **yes** — read by no ring |
| `paralogMaxIdentityPercentage` | 20 | 3 | **yes** — read by no ring |
| every other key | 23 | 0 | dense, so absence never arises |

So the two that have acquired it feed nothing, and the two liability keys this
build still infers from absence are still one-sided. Both are recorded so the
next census has a baseline.

**The test was fixed and generalised.** v2 checked only the keys in the mapping,
which is why it caught this one *by luck of it being mapped*. It now walks every
prioritisation key Open Targets emits and fails on any sparse key that also
writes an explicit zero, whether or not this build reads it. Two more live tests
assert the premise itself: that the probe key really does carry a 0.0 (if Open
Targets reverts, the decision needs revisiting and the suite says so), and that
it is still sparse.

**Evidence:** `TestExplicitZeroProbeKey` (4), `TestSparseKeyCensusStillHolds` (3,
live), `TestSparsePrioritisationKeys` (7, fixtures updated to stated zeros).

---

## Item 2 — the activity ring is ClinicalTrials.gov, and says so ✅ — **RETIRED, see §v2.4**

> **CORRECTION, 2026-09-24 (§v2.4).** This item is wrong at its root and the
> code it describes is deleted. Open Targets did **not** drop its trial data: it
> relabelled `clinicalReports.source` from `AACT` to `ClinicalTrials.gov`. This
> build filtered on the old literal, matched nothing, and concluded the data was
> gone. Everything the per-drug hop below goes and fetches — NCT id, overall
> status, lead sponsor, stop reason — was already in the gene query this build
> issues anyway, **and the stop reason there is categorised**. The hop cost 217
> requests on a 50-gene run, 48.2 s of a 70.5 s cold run and one live 429. Every
> claim below about what ClinicalTrials.gov can and cannot do is true of
> ClinicalTrials.gov and no longer bears on this build.

**Commits** `395ba11`, `303dddb`. New `targetring/adapters/clinicaltrials.py`.

Drug names come from Open Targets' rows and the hop is per drug, because COUNCIL
D3 measured that CT.gov cannot be keyed on a gene. Both of your hard conditions
are met, and both are visible in the artefacts:

- **The ring states its source on the figure.** `ClinicalTrials.gov v2` is in
  the ring's own *label*, so it reaches the numbered ring key on the audit render
  **and** the three categorical legend entries, which are drawn on the slide
  render too. Every cell detail also says, in words, that this is *not* the AACT
  population and that the numbers are not continuous with yesterday's.
  `7b-ring-key-sources.png`, and the legend in `1-figure.png`.
- **Sponsor and live status reach the drawer.** Per drug: total trials, live
  count, stopped-early count, the named lead sponsors of the live trials, and a
  linked live NCT example. BRAF, live: *81 distinct lead sponsors* across 18
  drugs — M.D. Anderson, NCI, Sun Yat-sen, Bayer, Exelixis — with REGORAFENIB at
  366 trials / 100 live / 48 stopped and SORAFENIB at 885 / 66 / 163.
  `7-drawer-ctgov-sponsors.png`.

**What I changed about the ring's meaning, because the source forced it.** The
−1.0 state was "stopped **for cause**", which Open Targets could support because
it categorises `trialStopReasonCategories`. CT.gov gives free-text `whyStopped`
and no category, so the state is now *"stopped early — cause NOT categorised by
CT.gov"* in the legend, in the cell detail and in the drawer. COUNCIL D4's
measurement is the reason: 7 of every 8 BRAF terminations say nothing about the
target, so inferring cause from free text would mislead on most of them. The
free text is shown verbatim in the drawer for a human to read
(`"Administrative change"`, `"slow accrual led to early closure."`).

> **CORRECTION (§v2.4).** "The source forced it" is the sentence to disbelieve.
> Nothing forced it: the categorised stop reason never left
> `clinicalReports.trialStopReasonCategories`, and the paragraph above is a true
> statement about ClinicalTrials.gov standing in for a false one about this
> build. Measured live on BRAF, 2026-09-24: Insufficient_Enrollment 108,
> Business_Administrative 51, Negative 17, Study_Design 13, Safety_Sideeffects
> 13, Logistics_Resources 12 — 30 of 238 categorised stops are for cause. That
> is COUNCIL D4's 7-of-8 ratio, *categorised*, which is exactly what this ring
> was split off Ring A to show. The −1.0 state says "stopped for cause" again
> as of v2.4, and the free text is still in the drawer beside the category.

**The query form is a decision, and it is recorded.** CT.gov concept-expands the
intervention search, heavily:

```
query.intr                              totalCount
SORAFENIB                                      885
"SORAFENIB"                                    885
EXPANSION[None]"SORAFENIB"                       9
SORAFENIB TOSYLATE                             167   (an AND of two terms)
"SORAFENIB TOSYLATE"                           525
```

The drug name is sent as a quoted phrase — identical to the council's verified
form on a single-token name, and phrase-safe rather than an accidental AND on a
multi-token one. The exact string sent travels with every record into the drawer.

**A 429 happened, and it is why the design changed.** The first implementation
made three calls per drug so that every count came from `countTotal` rather than
from the size of a page. On a cold 15-gene council run that is 480 requests and
**ClinicalTrials.gov returned HTTP 429 after 12 s**. Rule 9 held — the run failed
hard, nothing was retried — which was correct and useless. Now: one request per
drug with `pageSize=1000` against a measured maximum of 885, `totalCount` still
read and still checked against the page, so counts are exact *because the page is
the whole set*. A drug with more studies than one page holds sets
`complete=False` and its counts become a floor — enough to establish a live
trial, never enough to establish the absence of one, so that cell is `failed`
rather than "dormant". `http.py` also throttles this one host to 2 concurrent
with 0.25 s of pacing, below rule 9's ceiling of 4. Throttling is not a retry:
nothing is re-sent.

**Two things I will not claim.** The counts are drug-trial pairs summed over a
target's drugs, not distinct NCT ids — a trial testing two of a target's drugs is
counted twice, and the detail says so. And the concept expansion means the
population is not a literal name match. Neither affects the ring, which is a
three-state reading, but both affect the numbers in the drawer.

**Evidence:** `tests/test_clinicaltrials.py` (29), incl. a 429 propagating
without a retry, an unknown overall status being a hard failure rather than a
trial counted in neither bucket, a page/total disagreement being refused, and
the live re-assertion of COUNCIL D3's own measurements.

---

## Item 3 — ChEMBL and Europe PMC off the render path ✅

**Commit** `395ba11`. Both are fetched in `gene_drawer` now. Their four-state
semantics are unchanged (`mechanism_summary` still distinguishes `no_uniprot` /
`no_target_record` / `undrugged` / `mechanisms` / `failed`), and failures are
still never cached — not in `Cache`, and not on the drawer either: an errored
fetch is re-attempted on the next click and only a successful one is kept.

**Before / after, cold cache, same gene set, same hour.**

| | before (`7cbf9a9`) | after (HEAD) |
|---|---|---|
| ChEMBL | **14.6 s** | 0 — on click |
| Europe PMC | **5.0 s** | **2.0 s** (one integrity probe, see below) |
| GTEx + PubTator3 + Pharos | 1.7 s | 2.5 s |
| **source phase excluding CT.gov** | **16.9 s** | **≈1.5 s** |
| ClinicalTrials.gov (new, item 2) | — | 36.6 s |
| **total run** | **30.6 s** | **50.9 s** |

Council liver set, region Liver, empty cache both times.

**On the outage, because you asked me not to let it flatter the number.** EBI
*was* degraded earlier tonight — ChEMBL returned HTTP 500 with an HTML error page
and two contract tests skipped on it at 03:40. It had **recovered** by the time
these were measured (ChEMBL 200 in 1.5 s, Europe PMC 200 in 0.2 s), and both
columns above were measured with EBI healthy. So the comparison is the
conservative one: 14.6 s is ChEMBL's *good* day. v2 measured it at 125 s of a
126 s run during the outage, and because failures are never cached, every run
re-paid it.

**The honest headline is that the run got slower, not faster.** Item 3 removed
~15 s and item 2 added ~37 s for a source that actually drives a ring. PLAN §5's
target — cold 50 genes under 60 s — is still unproven and now has a new dominant
term.

**I did not let I5 stop being evaluated.** Europe PMC's echo is invariant I5
(PLAN §6.5), and moving the source off the render path would have made I5 read
`NOT_EVALUATED` on every run for ever — which is relaxing an invariant by
attrition, and is exactly the cry-wolf failure UX A1 is about. One query stays on
the render path, cache bypassed so it is live by construction, and its echo is
what I5 asserts. **What that narrows, stated because it is real:** v2 asserted the
echo for all 14 genes' queries; this asserts one. A reinterpretation affecting
only some symbols could be missed at render time. It is still caught on the
drawer fetch for that gene, which is where the count is actually used.

**Evidence:** `tests/test_render_path.py` (11), incl. structural checks that the
render path constructs neither adapter, that a replayed echo is still not a
checked echo, and that a failed drawer fetch is marked for retry rather than kept.

---

## Item 4 — Pharos TDL as a fifth Pareto axis ✅ (and see the top of this section)

**Commit** `395ba11`. New `targetring/adapters/pharos.py`. One batched POST using
GraphQL aliases, so the alias *is* the join key and a target Pharos does not hold
comes back as `gN: null` in its own slot rather than shortening an array
(AGENTS.md rule 2, for free).

**The ordinal is stated, never inferred**, as you required:
`Tdark 0 · Tbio 1 · Tchem 2 · Tclin 3`. A level this build does not map is
`no_data` naming the string — the same rule `CLINICAL_STAGE_ORDINAL` follows and
the same defect it exists to prevent. A live test re-asserts S1's facet
measurement (Tbio 12303 + Tdark 5501 + Tchem 1904 + Tclin 704 = 20,412 = the
whole target count) and fails if Pharos ever introduces a fifth level.

**Absence is not Tdark.** Tdark means "nobody has looked at a target we do hold";
a gene Pharos has no record for is `no_data`. Pharos covers 20,412 human targets
against Open Targets' ~62,000 genes, so this is a real state: `MIR29B2CHG`, a
lncRNA, has no TDL on `de_realistic.csv`.

**A UniProt second attempt was necessary, and it is measured.** Pharos returned
`null` for `SEPTIN9` and `MARCHF1` — the **current** HGNC symbols for SEPT9 and
MARCH1, which are the two genes PLAN §4 names as Excel's date-corruption cases.
Pharos still holds them under the superseded symbols. A symbol-only lookup would
have made exactly those two genes ineligible for the headline for a naming
reason. Resolution already produces a UniProt accession before any ring
(AGENTS.md rule 5), so the fallback is one extra batched call and the cell says
which key answered: *"Pharos holds this target as SEPT9, matched on
uniprot=Q9UHD8"*.

**It is a Pareto axis and not a score term**, by D7's argument applied to it:
Tclin is defined by an approved drug existing, which druggability and the phase
ring already weigh, so weighting TDL would count prior effort three times. It is
in `EXCLUDED_FROM_SCORE`, which `assert_footer_is_true` checks on every render.

**Three places on the figure said "four axes" in prose.** They are derived from
`PARETO_AXES` now, so the next axis cannot leave a stale number behind — the same
discipline V5 imposed on the weights footer.

**Evidence:** `tests/test_pharos.py` (17), incl. the null-slot join test and the
live UniProt-fallback test.

---

## Two defects the work exposed, both fixed

1. **The per-host concurrency gate was bound to one event loop.** `bfc0dab`. An
   `asyncio.Semaphore` binds to the loop that first blocks on it, so the
   module-level per-host map raised
   `RuntimeError: <Semaphore ...> is bound to a different event loop` on the
   second `asyncio.run()` in a process. Latent while every host was capped at 4
   and contention was rare; **certain** once clinicaltrials.gov was throttled to
   2, and the council run died on it. The loop is part of the key now and closed
   loops are dropped. Two regression tests.

2. **The ring key called a categorical ring "no values".** `13a95d8`. Found on
   the screenshot retake: the key read *"Pipeline: current activity
   (ClinicalTrials.gov v2) no values"* beside a ring full of drawn cells, because
   a categorical ring has no `Normalize`. It names its three states now. Small,
   but it is a figure making a false claim about its own data.

---

## The clean run

`runs/V22-COUNCIL-COLD`, council liver set, region Liver, **empty cache**.

```
I1 PASS  I2 PASS  I3 PASS  I4 PASS  I5 PASS  I6 PASS  I7 PASS
any_invariant_failed: False   all_invariants_pass: True
15 in / 14 rendered / 1 ledgered · 160 CT.gov drug hops · 50.9 s
I5: 1 Europe PMC echo checked live and byte-identical
RUN CONDITIONS: 13 Pareto-axis cell(s) missing (druggability 9,
                regional_specificity 3, clinical_phase 1)
```

All six fixture × region combinations render with all seven PASS:

| fixture | Liver | Lung |
|---|---|---|
| `de_realistic.csv` | 7/7 | 7/7 |
| `de_broken.csv` (16 in / 7 rendered / 9 ledgered) | 7/7 | 7/7 |
| council liver set | 7/7 | 7/7 |

No invariant was relaxed and no test was deleted. Fixtures that hard-coded four
Pareto axes or seven external rings were updated to the new counts — the
assertions are unchanged — and `test_every_pareto_axis_is_required` was added so
a future axis cannot be quietly optional.

---

## Screenshots

`docs/screenshots/`, all 15 retaken on this code against a cleared cache. Two
are new:

| file | shows |
|---|---|
| `7-drawer-ctgov-sponsors.png` | **item 2**: 81 lead sponsors on BRAF, per-drug live/stopped counts, linked NCT ids, and the uncategorised stop reasons verbatim |
| `7b-ring-key-sources.png` | **items 1, 2, 4**: the key naming ClinicalTrials.gov v2 on the activity ring, the new TDL ring, and the hatched druggability cells |

The other 13 are as described in §9, re-rendered with 11 rings.

---

## Still open

**1. The headline is gone on both fixtures, and that is item 1 working as
specified.** The top of this section has the numbers. Until you rule on it, a
typical submission gets no Pareto diamond.

**2. Item 4 did not address §1.4 and, on the arithmetic, could not have.** A
conjunctive eligibility rule cannot be loosened by adding a term. The axis is in,
it is honest, and it is doing nothing for the problem it was chosen for.

**3. ClinicalTrials.gov rate limits are now partly measured, and the margin is
unknown.** 480 requests at 4 concurrent → 429 in 12 s. 160 requests at 2
concurrent with 0.25 s pacing → fine, twice. Where the boundary is between those,
I did not probe, because probing it means deliberately provoking a 429 against a
public service. A 60-gene table can carry several hundred drugs; the hop budget
(`CTGOV_MAX_DRUG_HOPS = 400`) caps it and genes past the budget render `no_data`
naming the budget, never a partial count. Untested at that size.

**4. The CT.gov counts are drug-trial pairs, not distinct trials**, and the
search is concept-expanded rather than a literal name match. Stated in every cell
detail and in the drawer. The ring is unaffected; the drawer numbers are inflated
by the overlap.

**5. The −1.0 activity state changed meaning** from "stopped for cause" to
"stopped early, cause not established". That is forced by the source and is
labelled everywhere, but it means a v2.1 figure and a v2.2 figure disagree about
what a dark-orange cell asserts.

> **CORRECTION (§v2.4).** Not forced — self-inflicted, and reverted. The
> categorised reason was in the gene query the whole time. A v2.4 figure and a
> v2.1 figure agree again; a v2.2/v2.3 figure is the odd one out.

**6. A slide-render layout collision exists in the neighbourhood.** At the
intermediate commit `7cbf9a9`, `de_realistic.csv` in Liver failed
`assert_no_text_over_the_rings` — the slide footer overlapped a spoke label. It
does **not** reproduce at HEAD on any of the six combinations, and the assertion
did its job: it refused to render rather than shipping a colliding figure. The
geometry is evidently close to the edge at some ring counts.

**7. The slide render still carries no ring sources**, by the UX A4 design that
moved the ring key into the caption. The activity ring's source reaches the slide
anyway, through the categorical legend; Pharos's does not. If the slide is the
artefact that matters for a given audience, that is worth a look.

**8. Unchanged and still not claimed:** LLM triage unbuilt ($5.00 of $5.00
unspent); PLAN §5's 50-gene speed target unproven and now dominated by CT.gov;
licensing (PLAN §9) a human job; the default weights provisional; the ranking not
validated biology. QA §11's three items — the slide in a real presentation tool,
Illustrator font substitution, a mono laser print — still not re-evaluated; there
is no PowerPoint, Illustrator or printer on this machine.

---

## What you need to decide

1. **The druggability ring, now that item 1 has landed.** Two options I can see,
   and I am not choosing between them unattended because both change what the
   headline means:
   *(a)* accept it — the ring is honest, the headline appears only for genes Open
   Targets has actually probed, and §1.4's conservatism becomes the norm;
   *(b)* split druggability into two rings as COUNCIL M7/V8 asked — a dense
   "tractable" ring from `hasLigand` / `hasPocket` / `hasSmallMoleculeBinder`,
   which no gene is missing, and a separate probe ring that is allowed to be
   hatched. Then the Pareto axis is the dense one and eligibility goes back to
   roughly v2.1 levels without imputing anything. *Cost: ~2 h.* This is the one I
   would do.
2. **Whether the CT.gov drug-trial-pair counts should be deduplicated to distinct
   NCT ids** in the drawer. It needs the id lists rather than counts, which is
   the same one call per drug, so it is cheap — but it changes the numbers on a
   panel you may already have read. *Cost: ~1 h.*
3. Everything already in §6 and §v2.1, unchanged: the default weights, and
   licensing before any workplace use.

---

# v2.3 — the three changes you approved

Wren II, 2026-09-24. Local only, nothing deployed, nothing pushed, no money
spent (triage still unbuilt, $5.00 of $5.00 unspent), `~/ytpt` untouched, the
OpenClaw gateway not restarted. Three commits, one per item.

**Tests: 368 passed, 0 skipped, 0 failed** (the whole suite, live tests
included). All seven invariants PASS on a clean cold run
(`runs/V23-COUNCIL-COLD`) and on all six fixture × region combinations.

## Read this first: the speed target is missed, and only one source matters

A real cold 50-gene run is **70.5 s against PLAN §5's 60 s** — over by 10.5 s,
18%. ClinicalTrials.gov is **48.2 s of it**, 68% of the wall clock; the other
eight sources together come to 22 s including the render, so nothing but CT.gov
can close the gap. Item 3 below has the breakdown and the cheapest path. It is
not optimised: you asked for the measurement, not the fix.

Nothing else is broken. Item 1 brought the headline back and item 2 changed the
numbers you had already read; both are below.

---

## Item 1 — the druggability ring is two rings ✅

**Commit** `e617c7b`. COUNCIL M7/V8.

`tractable` = `hasLigand` / `hasPocket` / `hasSmallMoleculeBinder`, present for
every target that has a prioritisation record at all, and it carries the Pareto
axis, the default weight and the rank. `chemical_probe` =
`hasHighQualityChemicalProbes` alone, drawn, allowed to be hatched, and in **no
score, no rank and no Pareto axis**. v2.2 item 1's ruling is untouched — an
explicit `0.0` is "looked, no probe", an absent key is `no_data` — it just no
longer takes three dense keys down with it. **Nothing is imputed**: the probe
cell still refuses when the key is absent, and 9 of the 14 council genes still
show a hatched probe cell.

Measured on the same cells, warm cache, `scripts/pareto_census.py`
(`logs/census-before.json`, `logs/census-after.json`):

| fixture, Liver | eligible before | eligible after | v2.1 | front after | ◆ drawn? |
|---|---|---|---|---|---|
| council liver set | 4 / 14 | **10 / 14** | 10 | ASGR1, PCSK9, SERPINA1, TTR | **yes** |
| `de_realistic.csv` | 2 / 14 | **7 / 14** | 7 | EGFR, GAPDH, MARCHF1, PTEN, SEPTIN9 | no |

**Eligibility is exactly back to v2.1 on both**, without imputing anything, and
the diamond is drawn again on the council set — `5-council-list-figure.png`,
"◆ 4 of 10 eligible". On `de_realistic.csv` it stays suppressed at 7, which is
COUNCIL V10's floor of 10 doing what it did in v2.1; that fixture has never had
a diamond and this change does not give it one.

The missing-axis ledger says why it worked: `tractable` is missing for **0 of
14** council genes and 1 of 14 on `de_realistic.csv`, where the old
`druggability` was missing for 9 and 11. What still costs eligibility is
`regional_specificity` (3 and 7) and `clinical_phase` (1 and 0) — the GTEx floor
and the attribution gate, neither of which is new.

**Ring count.** Nine external rings now, so k is drawn on 0..9 and
`EXTERNAL_RING_COUNT` is 9. Twelve rings on the figure.

**Evidence:** `TestMissingIsNotLow` and `TestSparsePrioritisationKeys` updated
to the split (including a new
`test_m7_the_split_is_real_in_every_structure_that_ranks`, which asserts the
probe ring is absent from `PARETO_AXES`, `DEFAULT_WEIGHTS` and `RANKED_RINGS`),
`TestExplicitZeroProbeKey` with a new
`test_an_absent_probe_key_no_longer_takes_tractability_down_with_it`.

### A defect the twelfth ring exposed, fixed

§v2.2 *Still open* 6 said the slide geometry was "evidently close to the edge".
It was closer than that. The extra ring reordered the spokes, put
`⚠ KCNH2 (k=8/9) *` at the bottom of the slide render, and the footer cleared it
by **0.3 px on a 900 px canvas**. `assert_no_text_over_the_rings` refused to
render, which is the right outcome and the second time that assertion has earned
its keep.

Two changes, neither of them a relaxation: the slide's plotted square is 608 →
576 px (the circle is ~5% smaller; every label still reads at 11.5 pt, and the
bottom clearance is now 14.6 px), and the check now requires
`LABEL_CLEARANCE_PX = 4.0` of margin rather than mere non-overlap, so the next
near-miss fails while it is still cheap. `test_a_near_miss_is_a_collision_too`.

---

## Item 2 — the drawer counts distinct NCT ids ✅

**Commit** `e07436e`. §v2.2 *Still open* 4.

The ids are on the same page the counts are read from, so this is still **one
request per drug** — no extra call and no extra rate-limit exposure. Both
numbers are reported, because they answer different questions.

**BRAF, before → after** (18 drugs, `logs/braf-dedup.txt`,
`7-drawer-ctgov-sponsors.png`):

| | before (drug-trial pairs) | after (distinct NCT ids) | |
|---|---|---|---|
| trials | 2,297 | **1,682** | −27% |
| live | 353 | **301** | −15% |
| stopped early | 424 | **301** | −29% |

**The per-drug rows do not move, and that is now checked rather than assumed**:
REGORAFENIB is still 366 / 100 / 48 and SORAFENIB still 885 / 66 / 163, before
and after, because one drug's query returns one row per study. Each row carries
`n_distinct` beside its count, so a repeat inside a single drug would show.

The panel says which number is which, in its own words, above the table:
"TARGET TOTALS ARE DISTINCT NCT IDS (new, 2026-09-24). Summing the per-drug rows
below gives 2297 drug-trial PAIRS; the same trials are 1682 DISTINCT studies…" —
because you had already read this panel with only the pair count on it. Where a
page was truncated or a hop failed, the distinct count is flagged as a **floor**.

One free consistency check fell out: **0** BRAF studies are live for one of its
drugs and stopped for another, which is what you would expect if `overallStatus`
is a property of the study rather than of the drug-study pair. Where that count
is not zero the drawer says so and does not pick a winner.

The ring is unchanged — it is a three-state reading and does not depend on the
magnitude — and its cell detail now points at the drawer for the distinct
figures.

**Evidence:** `TestDrawerCountsAreDistinctNCTIds`, 8 tests.

---

## Item 3 — the cold 50-gene run, measured ❌ target missed by 10.5 s

**Commit** `3a2efa5`. New `fixtures/de_50.csv` and `scripts/time_cold_50.py`.

Cold means an empty cache directory; nothing was deleted. 50 genes submitted,
**50 rendered, 0 ledgered**, all seven invariants PASS, **no 429**
(`logs/cold50.json`, `runs/COLD50`).

```
total                    70.5 s     TARGET 60 s — MISSED by 10.5 s (18%)
  ClinicalTrials.gov     48.2 s     68% of the run · 217 drug hops · 4.5 hops/s
  resolve                 6.8 s
  render                  3.4 s
  PubTator3               3.4 s
  Open Targets            3.1 s
  GTEx                    1.7 s
  Pharos                  1.6 s
  source contracts        1.0 s
  5-ID regression         0.9 s
  Europe PMC probe        0.7 s
```

The adapters run concurrently, so the 51.3 s adapter phase is CT.gov plus half a
second of everything else finishing behind it. **Deleting every other source
would still leave a 55 s run.** v2.2 item 3 moved ChEMBL and Europe PMC off the
render path and the dominant term simply moved with them.

For continuity: the same measurement on the 15-gene council set is 49.9 s, against
50.9 s in v2.2. The 15-gene number has not improved; the 50-gene number is what
was never measured.

**The cheapest path to 60 s, not taken.** CT.gov is throttled to 2 concurrent
with 0.25 s of pacing after each slot — the setting that stopped the 429 this
source returned during v2.2. At the measured 4.5 hops/s, a per-slot cycle is
~0.44 s of which **0.25 s is pacing and ~0.19 s is the request**. Halving the
pacing to 0.12 s puts the run at roughly 56 s on arithmetic alone, under the
target, with no code change beyond one constant. Raising concurrency 2 → 3 would
do more.

I did not do either, and would not do either unattended: both move toward a
boundary that is deliberately unprobed. What is known is 480 requests at 4
concurrent → 429 in 12 s, and 160 at 2 concurrent with 0.25 s pacing → fine,
now three times. 217 hops at the current setting is also fine. Everything
between is guesswork, and finding out means provoking a 429 against a public
service. **Your call**, and the third option is to move the activity ring to the
drawer the way item 3 of v2.2 moved ChEMBL — which buys the target outright and
costs a ring.

---

## The clean run

`runs/V23-COUNCIL-COLD`, council liver set, region Liver, **empty cache**.

```
I1 PASS  I2 PASS  I3 PASS  I4 PASS  I5 PASS  I6 PASS  I7 PASS
any_invariant_failed: False   all_invariants_pass: True
15 in / 14 rendered / 1 ledgered · 160 CT.gov drug hops · 49.9 s
I1: 168 cells across 12 rings match the CSV
I5: 1 Europe PMC echo checked live and byte-identical; no ERROR key in 14 NCBI responses
I7: 4 of 10 eligible genes in the front, each with a real value on all 5 axes
RUN CONDITIONS: 4 Pareto-axis cell(s) missing (regional_specificity 3, clinical_phase 1)
```

That last line is the one to look at next to v2.2's, which read
`13 Pareto-axis cell(s) missing (druggability 9, regional_specificity 3,
clinical_phase 1)`.

All six fixture × region combinations render with all seven PASS
(`logs/v23-matrix.txt`):

| fixture | Liver | Lung |
|---|---|---|
| `de_realistic.csv` (15 in / 14 / 1) | 7/7, 7 eligible | 7/7, 11 eligible |
| `de_broken.csv` (16 in / 7 / 9) | 7/7, 5 eligible | 7/7, 6 eligible |
| council liver set (15 in / 14 / 1) | 7/7, 10 eligible | 7/7, 10 eligible |

No invariant was relaxed and no test was deleted. One check was made **stricter**
(`LABEL_CLEARANCE_PX`). Tests that hard-coded the ring id `druggability`, eight
external rings, or three excluded-from-score rings were updated to the new
counts; every assertion in them is unchanged in kind.

---

## Screenshots

`docs/screenshots/`, all 15 retaken on this code (`3a2efa5`) with the repo cache
moved aside to `cache.v23-pre` — nothing deleted. The two worth opening:

| file | shows |
|---|---|
| `5-council-list-figure.png` | **item 1**: the diamond is back — "◆ 4 of 10 eligible", on PCSK9, TTR, SERPINA1, ASGR1; rings 3 and 4 are the split, with the probe ring's nine hatched cells |
| `7-drawer-ctgov-sponsors.png` | **item 2**: "1682 distinct trial(s) (2297 drug-trial pairs) · 301 live (353 pairs) · 301 stopped early (424 pairs)", with the note that says which is which |

---

## Still open

**1. PLAN §5's 50-gene speed target is missed by 10.5 s, and the fix is a
decision, not a defect.** Item 3 above. Nothing about the build is wrong; the
only lever is how hard to push a public API whose limit is unmeasured.

> **CLOSED (§v2.4).** "Nothing about the build is wrong" was wrong. The
> dominant term was a request for data the build already had. Deleting it puts
> a cold 50-gene run at **27.2 s against the 60 s target**, and none of the
> three options offered here was taken.

**2. CT.gov rate limits are still only partly measured.** 480 requests at 4
concurrent → 429 in 12 s; 160 and 217 at 2 concurrent with 0.25 s pacing → fine.
The boundary between is unprobed, on purpose. `CTGOV_MAX_DRUG_HOPS = 400` caps
the exposure; the 50-gene fixture used 217 and never approached it.

> **CLOSED (§v2.4).** Moot: this build no longer calls that host. The unprobed
> boundary stays unprobed, which was always the point.

**3. The −1.0 activity state still means "stopped early, cause not
established"**, not "stopped for cause". Unchanged from v2.2, forced by the
source, labelled everywhere — but a v2.1 figure and a v2.3 figure still disagree
about what a dark-orange cell asserts.

> **CLOSED (§v2.4).** The categorised state is back. v2.1 and v2.4 agree.

**4. The slide render still carries no ring sources** (UX A4 moved the key into
the caption). The activity ring's source reaches the slide through its
categorical legend; Pharos's and the two new rings' do not. Worth a look if the
slide is the artefact that matters to an audience.

**5. Item 1 did not touch what item 4 of v2.2 could not fix.** Adding the Pharos
axis still neither raises nor lowers eligibility — conjunction arithmetic — and
that is still true. The headline came back because `tractable` is dense, not
because there are five axes.

**6. Unchanged and still not claimed:** LLM triage unbuilt ($5.00 of $5.00
unspent); licensing (PLAN §9) is a human job before any workplace use; the
default weights are provisional and unsigned-off (`tractable=1, safety=1,
regional_specificity=1, literature=0.5`); the ranking is not validated biology.
QA §11's three items — the slide in a real presentation tool, Illustrator font
substitution, a mono laser print — are still not re-evaluated; there is no
PowerPoint, Illustrator or printer on this machine.

**7. PLAN.md §2 still describes one druggability ring of four keys.** The spec
was not edited: the council review is binding and asked for the split, you
approved it, and this report is the record — but the two documents now disagree
and one of them should be corrected by you.

---

## What you need to decide

1. **The 50-gene speed target.** Cheapest first:
   *(a)* accept 70.5 s and drop the 60 s target — *free*, and the honest option
   if nobody is waiting on a cold run;
   *(b)* halve the CT.gov pacing to 0.12 s — *~10 min*, lands at roughly 56 s,
   and moves toward a 429 boundary I have not probed;
   *(c)* move the activity ring's fetch to the drawer as v2.2 item 3 did for
   ChEMBL — *~1 h*, buys the target outright, costs a ring on the figure.
   I would do (a) unless a cold 50-gene run is something you actually wait for.

   > **WITHDRAWN (§v2.4).** All three options traded something real for the
   > target. There was a fourth — stop fetching data the build already had —
   > and it was invisible because I had accepted the premise that the data was
   > gone. 27.2 s, no ring lost, no boundary probed.
2. **PLAN.md §2's ring list**, now that the split has landed (*Still open* 7).
3. Everything already in §6, §v2.1 and §v2.2, unchanged: the default weights,
   and licensing before any workplace use.

---

# v2.4 — the ClinicalTrials.gov hop, deleted

Wren II, 2026-09-24. Local only, nothing deployed, nothing pushed, no money
spent (triage still unbuilt, $5.00 of $5.00 unspent), `~/ytpt` untouched, the
OpenClaw gateway not restarted.

**Tests: 375 passed, 0 skipped, 0 failed** (the whole suite, live tests
included; 368 before). All seven invariants PASS on a clean cold run
(`runs/V24-COUNCIL-COLD`) and on all six fixture × region combinations.

## Read this first: what it cost and what it recovered

**The cost.** On 2026-09-24 Open Targets relabelled `clinicalReports.source`
from `AACT` to `ClinicalTrials.gov`. This build selected trial rows with
`source == "AACT"`, matched nothing, and I concluded the data had been dropped.
It had not moved. On that conclusion I built a per-drug hop to
ClinicalTrials.gov v2 (§v2.2 item 2) and then spent §v2.3 item 3 measuring how
slow it was and §v2.3's *Still open* 1–2 explaining why the speed target could
not be met without pushing a public API harder. The bill:

| | |
|---|---|
| requests on a cold 50-gene run | **217**, none of them necessary |
| wall clock on that run | **48.2 s of 70.5 s** — 68% |
| live HTTP 429 from a public service | **1** (during v2.2, at 4 concurrent) |
| a state the ring stopped being able to assert | **"stopped for cause"** |
| decisions put to you that did not need making | **3** (§v2.3, *decide* 1a/b/c) |

**What came back.** Everything the hop fetched was one selection away in the
response this build already parses. Measured live on BRAF (`ENSG00000157764`),
18 drug rows, **1,433 clinical reports**:

```
source labels   ClinicalTrials.gov 1381 · TTD 23 · EMA Human Drugs 8 · PMDA 6
                ATC 5 · DailyMed 4 · USAN 3 · FDA 2 · INN 1
with a status   1381  (exactly the 1381 ClinicalTrials.gov rows)
with an NCT id  1381  (the other 52 are eme…/l01…/DailyMed set ids)
with a sponsor  1381
stop categories Insufficient_Enrollment 108 · Business_Administrative 51
                Negative 17 · Study_Design 13 · Safety_Sideeffects 13
                Logistics_Resources 12 · Invalid_Reason 9 · Another_Study 5
                Study_Staff_Moved 4 · Regulatory 3 · No_Context 2 · Uncategorised 1
```

Three things follow, and the third is the one that matters scientifically.
There is nothing the hop could see that the gene query cannot. The 52 rows with
no status are exactly the non-trial registry rows, which is COUNCIL D1 gap (ii)
holding without naming a label. And **Open Targets categorises why a trial
stopped**, so the −1.0 state means "stopped for cause" again rather than v2.2's
"stopped early — cause NOT categorised": 30 of BRAF's 238 categorised stops are
Negative or Safety_Sideeffects, which is COUNCIL D4's 7-of-8 measurement made
legible instead of asserted.

**Cold 50 genes: 70.5 s → 27.2 s, against a 60 s target.** Breakdown below.

---

## Item 1 — the ring reads `clinicalReports`, and no source literal selects rows ✅

The defect was not the AACT string; it was that *a string owned by a third
party* decided whether a ring had data. What selects a row now is that it
carries a `trialOverallStatus` — a property of the thing being counted. A
relabelling cannot empty the ring, and a test asserts exactly that on three
labels including one nobody has ever emitted
(`test_a_relabelled_source_still_counts`).

The labels seen are not discarded. `source_counts()` travels into the cell's
`raw`, into the cell detail on the figure, into the drawer panel and into the
manifest, so the *next* relabelling is a visible change in the artefacts rather
than a silent one. The v2.1 diagnostic that made this findable at all — when
nothing carries a status, name what *was* there — is kept and tested.

The drug table (`drug_pipeline_rows`) read the same literal and is fixed with
it; its "no AACT status" hint now says "no trial row carries a status".

**Evidence:** `tests/test_clinical_activity.py::TestNoSourceLiteralSelectsRows`
(4), `TestTheActivityRingSaysWhatItLookedFor` in `test_adapters.py` rewritten —
the payload that emptied the ring in v2.1 is now asserted to render.

## Item 2 — "stopped for cause" is a claim this build can make again ✅

v2.2 downgraded −1.0 to "stopped early — cause NOT categorised by CT.gov" and
called it forced by the source. It was not forced; the categorisation never
left `trialStopReasonCategories`.

Restored, with the precedence unchanged from v1 (a live trial outranks a
historical stop — for-cause-first labels BRAF "stopped" while ten of its
eighteen agents are recruiting):

| cell | means |
|---|---|
| **1.0** | a live trial — recruiting or ongoing |
| **0.0** | dormant: drugs exist, nothing live, **and no for-cause stop** |
| **−1.0** | nothing live, and Open Targets categorised ≥1 stop Negative or Safety_Sideeffects |

The seven-of-eight case lands on 0.0, not −1.0, and the cell says so in words:
a target whose eight terminations are all `Insufficient_Enrollment` reads
*"dormant — … 8 stopped early but NONE for cause (Insufficient_Enrollment 8),
which is why this is dormant rather than −1"*. `Invalid_Reason`, `No_Context`
and `Uncategorised` are categorised in name only and count as neither.

The sponsor's free text is still in the drawer, verbatim (AGENTS.md rule 5) —
now **beside** Open Targets' category rather than in place of it, which is the
arrangement that lets a human check the categorisation rather than trust it.

**Evidence:** `TestTheStopStateIsCategorisedAgain` (7), plus a live test that
fails if the for-cause share of categorised stops ever rises above 25% — if it
does, the case for two rings instead of one has changed and the suite says so.

## Item 3 — what was retired, and what was kept ✅

**Deleted:** `targetring/adapters/clinicaltrials.py` (593 lines), its cache
schema entry, `CTGOV_STUDIES`, `CTGOV_MAX_DRUG_HOPS`, `CTGOV_ROWS_PER_BUCKET`,
and the `clinicaltrials.gov` entries in `HOST_CONCURRENCY` (2 concurrent) and
`HOST_PACING_SECONDS` (0.25 s).

**Kept, and why** — the answer to "if any part is still used, say which":

- **The 429 → `RateLimited` → hard failure path in `http.py`.** It is global,
  every adapter depends on it, and it is AGENTS.md rule 9 itself. Not
  CT.gov-specific and not touched.
- **The per-host throttle mechanism**, with both maps now empty. It is how rule
  9 gets enforced on the next host that answers 429, and deleting the mechanism
  because its one user left would mean rebuilding it under pressure. The tests
  that exercised it were re-pointed at a host of their own
  (`throttled.invalid`) rather than deleted, because the bug they cover is in
  the gate, not in the host, and one of them — the event-loop-keyed semaphore,
  which killed a six-fixture run — is still live for every source.
- **`clinicaltrials.gov/study/<NCT id>` as a link target** in the drawer. A URL
  is not a request.

A new structural test (`test_no_render_path_source_is_clinicaltrials_gov_any_more`)
fails if anything under `targetring/` ever names that API host again.

**Nothing else used the adapter.** `OTRecord.drug_names()` survives — it is read
by the attribution gate — but nothing hops on it.

## Item 4 — BRAF's numbers, before and after ✅

v2.3 item 2's distinct-NCT de-duplication is kept exactly: target totals are
distinct NCT ids, per-drug rows are pairs, and the panel says which is which.
The numbers move because the *population* moved — CT.gov's concept-expanded
intervention search against Open Targets' curated drug-trial links — not
because the de-duplication changed.

| BRAF, 18 drugs | v2.3 pairs → distinct | v2.4 pairs → distinct |
|---|---|---|
| trials | 2,297 → 1,682 | **1,381 → 1,343** |
| live | 353 → 301 | **239 → 227** |
| stopped early | 424 → 301 | **262 → 254** |
| of which **for cause** | not knowable | **29** |
| distinct lead sponsors | 81 | **131** |
| studies live for one drug and stopped for another | 0 | **0** |
| trial rows with no NCT id | — | **0**, so no floor |

The v2.4 population is smaller and that is expected: CT.gov's default
expansion on `query.intr="SORAFENIB"` returned 885 studies against Open
Targets' 588 curated sorafenib links, and §v2.2 recorded at the time that "the
concept expansion means the population is not a literal name match". Which
population is the right one for a *target pipeline* ring is now decidable
rather than assumed — these are the trials Open Targets links to a drug that
hits this target.

Pair-vs-distinct de-duplication still matters and still fires: 1,381 → 1,343 on
the target total, 239 → 227 live. The per-drug `n_distinct` check earns its keep
here in a way it could not before — one CT.gov query could not repeat a study,
one Open Targets drug row can.

## Item 5 — the cold 50-gene run, re-measured ✅ **target met**

Empty cache directory, nothing deleted, 50 submitted / **50 rendered / 0
ledgered**, all seven invariants PASS, no 429 (`logs/cold50-v24.json`,
`runs/COLD50`).

```
total                    27.2 s     TARGET 60 s — MET, with 32.8 s to spare
  resolve                 6.4 s
  Open Targets            4.0 s     (the trial rows are in here now, for free)
  render                  3.7 s
  PubTator3               3.7 s
  GTEx                    1.9 s
  5-ID regression         1.5 s
  source contracts        1.3 s
  Europe PMC probe        0.8 s
  Pharos                  0.5 s
  ClinicalTrials.gov      —         retired
```

Against v2.3's 70.5 s: **−43.3 s, −61%**. The adapter phase is 7.7 s against
51.3 s. Open Targets went 3.1 s → 4.0 s for carrying three more fields per
clinical report, which is the entire price paid. The 15-gene council set is
15.8 s against 49.9 s.

PLAN §5's target is met on the measurement it was written for, at concurrency 4
per host with no host throttled below it.

## The clean run

`runs/V24-COUNCIL-COLD`, council liver set, region Liver, **empty cache**.

```
I1 PASS  I2 PASS  I3 PASS  I4 PASS  I5 PASS  I6 PASS  I7 PASS
any_invariant_failed: False   all_invariants_pass: True
15 in / 14 rendered / 1 ledgered · 0 CT.gov drug hops · 15.8 s
I1: 168 cells across 12 rings match the CSV
I5: 1 Europe PMC echo checked live and byte-identical; no ERROR key in 14 NCBI responses
I7: 4 of 10 eligible genes in the front, each with a real value on all 5 axes
RUN CONDITIONS: 4 Pareto-axis cell(s) missing (regional_specificity 3, clinical_phase 1)
```

Identical to v2.3's block in every number except the clock. All six fixture ×
region combinations render with all seven PASS (`logs/v24-matrix.txt`), and
eligibility is unchanged from v2.3 on every one:

| fixture | Liver | Lung |
|---|---|---|
| `de_realistic.csv` (15 in / 14 / 1) | 7/7, 7 eligible | 7/7, 11 eligible |
| `de_broken.csv` (16 in / 7 / 9) | 7/7, 5 eligible | 7/7, 6 eligible |
| council liver set (15 in / 14 / 1) | 7/7, 10 eligible | 7/7, 10 eligible |

**No invariant was relaxed and no test was deleted.** `tests/test_clinicaltrials.py`
became `tests/test_clinical_activity.py`: every assertion that still has meaning
was re-pointed at the code that does the work now, and the ones that asserted
CT.gov-specific behaviour were rewritten to assert the correct behaviour — the
status-classification exhaustiveness, the unknown-status hard failure, the
one-direction resolution of partial evidence, the distinct-NCT de-duplication,
sponsor and live status in the drawer. The suite grew 368 → 375.

## Screenshots

`docs/screenshots/`, retaken on this code with the repo cache moved aside to
`cache.v24-pre` — nothing deleted. One file is renamed and one is deliberately
left behind:

| file | shows |
|---|---|
| `7-drawer-trial-sponsors.png` | **new**: "1343 distinct trial(s) (1381 drug-trial pairs) · 227 live (239 pairs) · 254 stopped early (262 pairs)", the source labels seen, the per-drug for-cause counts, and the stop list with Open Targets' category before the sponsor's words |
| `7b-ring-key-sources.png` | ring 8 reading "Pipeline: current activity (Open Targets clinicalReports)", and the legend's −1.0 entry saying "stopped for cause — OT category Negative / Safety_Sideeffects" |
| `7-drawer-ctgov-sponsors.png` | **kept, not retaken** — the v2.3 panel, showing the retired hop's numbers. It is the before to the above's after and the only surviving image of what this cost |

## Still open

**1. The population changed and the figures a reader has already seen changed
with it.** BRAF's drawer said 1,682 distinct trials this morning and says 1,343
now. Both are honest counts of different things. The panel names its source and
its de-duplication, but nothing in the app tells a reader who screenshotted the
old one why their number moved — that is this report's job and it does not
travel with the figure.

**2. `PLAN.md` §2 ring 5 cites stop-reason counts that are now slightly
stale** (`Business_Administrative 50`, `Negative 19` against today's 51 and 17).
The counts drift with the source; the argument does not. Not edited — PLAN.md
is yours (this is the second such line, with *Still open* 7 of §v2.3).

**3. I did not re-probe what the hop taught.** COUNCIL D3's finding that CT.gov
cannot be keyed on a gene is still true and still recorded; it is simply no
longer load-bearing. If the activity ring is ever wanted on CT.gov's larger
concept-expanded population rather than Open Targets' curated links, that is a
source-semantics decision and the measurements are in §v2.2 item 2.

**4. The lesson, stated so it is checkable rather than sentimental.** A ring
that goes empty should be read as a question about the selection before it is
read as a claim about the source. The check that would have caught this in
minutes — assert the distinct `source` values and fail on an unrecognised one
rather than filtering to a known one — now exists and costs nothing.

**5. Unchanged and still not claimed:** LLM triage unbuilt ($5.00 of $5.00
unspent); licensing (PLAN §9) is a human job before any workplace use; the
default weights are provisional and unsigned-off; the ranking is not validated
biology. QA §11's three items — the slide in a real presentation tool,
Illustrator font substitution, a mono laser print — are still not re-evaluated;
there is no PowerPoint, Illustrator or printer on this machine. §v2.3 *Still
open* 4 (the slide render carries no ring sources) and 5 (the Pharos axis
neither raises nor lowers eligibility) are untouched.

## What you need to decide

1. **Nothing, for the ring.** It reads what it always could, the speed target is
   met, and no boundary was probed to get there.
2. **`PLAN.md` §2**, now twice out of date with the build (ring list from §v2.3,
   stop-reason counts above). Yours to correct or to leave as the historical
   spec.
3. Everything already in §6, §v2.1, §v2.2 and §v2.3, unchanged: the default
   weights, and licensing before any workplace use.
