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
