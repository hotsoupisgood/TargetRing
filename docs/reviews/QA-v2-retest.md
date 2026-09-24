# QA re-test — targetring v2

Reviewer: QA lead (the author of `QA-v0-plan.md`). Date: 2026-09-24, overnight.
Scope: `docs/V2-REPORT.md` §7, worked in the order it gives, plus a re-run of my
own v0 BLOCKING items B1–B7.

Everything below was produced by running the built app on this Mac against live
APIs. Probe scripts are in `/tmp/qa_v2_probe/` (deliberately outside the repo);
run artefacts are under `runs/QA-V2-*`. Local only, nothing deployed, nothing
pushed, no money spent, `~/ytpt` untouched, the gateway not restarted. Only this
file is committed. **I fixed nothing.**

Git SHA under test: `0ef53ca`.

---

## 0. Verdict

**The five council BLOCKING fixes hold as mechanisms. One of them does not hold
as an outcome: the V1 defect is still reachable, and I reproduced it.** The
seven invariants are real assertions, not decoration — I broke each one on
purpose and every mutation was caught. Four v2 claims are not true as written;
one of them is in the report's own evidence block.

The single most useful sentence in this document: **`FRK` renders
`clinical_phase = 5.0`, "Approved", brightest fill, on a drug that is not
approved for the indication it is credited with.** That is the V1 defect, on a
different gene, after the V1 fix. See §2.

| item | verdict |
|---|---|
| §7 A.1 council liver set | **PASS** (with a wrong exemplar named — §3) |
| §7 A.2 attribution on fresh targets | **PASS on false negatives / FAIL on false positives** — §1, §2 |
| §7 A.3 forced Pareto `no_data` | **PASS** |
| §7 A.4 safety ring 4/4 keys | **PASS** (label does not reach the figure — §5) |
| §7 B.5 outage ≠ invariant failure | **PASS**, against a genuine outage |
| §7 B.6 real invariant failure banners | **PASS** |
| §7 B.7 I5 live vs replayed | **PASS**, demonstrated in both directions |
| §7 B.8 manifest keys | **FAIL** — two of the three are absent — §4 |
| §7 C.9–C.11 slide / Illustrator / print | **NOT-EVALUATED** — §8 |
| §7 C.12 60 genes | **PASS** — §7 |
| §7 D.13 test suite | **PASS** — 267 passed, 2 skipped |
| §7 D.14–D.16 fixtures, cap | **PASS** |
| v0 B1, B2, B5, B6, B7 | **PASS**, no regression |
| v0 B3 | **PASS** on the correction; live re-measurement **NOT-EVALUATED** |
| seven invariants real? | **PASS** — 11 mutations, 11 caught |

---

## 1. The attribution rule, on ten-plus targets I chose (§7 A.2 — the highest-value item)

**Command.** `.venv/bin/python /tmp/qa_v2_probe/attribution.py`
(real code path: `OpenTargetsAdapter.fetch` → `fetch_attribution` →
`clinical_phase_cell`; no reimplementation of the rule).

Seventeen targets, none in the build's calibration set (EPHA2, BRAF, EGFR,
KCNH2, PCSK9, HMGCR). I wrote the expected answer for each before running.

### 1.1 False negatives — the cost §1.3 admits to

**None. 0 of 11.**

| gene | expected | rendered | attributed via |
|---|---|---|---|
| CFTR | phase | **5.0** | DEUTIVACAFTOR × cystic fibrosis, genetic_association 0.97 |
| NPC1L1 | phase | **5.0** | EZETIMIBE × hyperlipidemia, 0.59 |
| IL6R | phase | **5.0** | SATRALIZUMAB × rheumatoid arthritis, 0.82 |
| TNF | phase | **5.0** | ETANERCEPT × psoriatic arthritis, genetic_literature 0.61 |
| TYK2 | phase | **5.0** | FILGOTINIB × rheumatoid arthritis, 0.93 |
| ANGPTL3 | phase | **5.0** | EVINACUMAB × familial hypercholesterolemia, 0.30 |
| **ERBB2** | phase | **5.0** | LAPATINIB × gastric cancer, 0.55 |
| **ALK** | phase | **5.0** | LORLATINIB × neoplasm, 0.28 |
| **ABL1** | phase | **5.0** | PONATINIB × CML BCR-ABL1+, 0.86 |
| **KIT** | phase | **5.0** | SORAFENIB × GIST, 0.91 |
| **BTK** | phase | **5.0** | PIRTOBRUTINIB × neoplasm, 0.19 |

The five in bold are the class §1.3 predicts will break — targets whose driver
is somatic (HER2 amplification, ALK fusion, BCR-ABL1 translocation, KIT
mutation) or whose germline disease is unrelated to the indication (BTK →
XLA, not CLL). **Every one survives.** §1.3 says "I did not find a real false
negative; I did not prove there are none." Nor have I, but I now have eleven
fresh cases and zero false negatives, including the five most likely to fail.
**The stated cost of the rule is smaller than the report fears.**

### 1.2 Saves — false "Approved" the rule prevents

**4 of 6 polypharmacology cases, three of which would have read *Approved*.**

| gene | rendered | v1 would have rendered |
|---|---|---|
| ABCB1 | `not_applicable` | Phase 3 (zosuquidar, tariquidar, valspodar) |
| LCK | `not_applicable` | **Approved** (dasatinib) |
| FYN | `not_applicable` | **Approved** (dasatinib) |
| CYP3A4 | `not_applicable` | **Approved** (ritonavir, cobicistat) |
| SRC, YES1, HCK, AXL, EPHB4, PTK6, EPHA2 | `not_applicable` | — (`/tmp/qa_v2_probe/generic_term.py`) |

**So: 0 wrong-conservative against 4 saves, 3 of them at the top of the
colormap.** The trade the rule makes is strongly favourable on my sample.

### 1.3 Two cases where I was wrong, not the app

Reported for completeness, because they were in my first summary as
disagreements and they are not defects:

- **NQO2** rendered `value(0.0)`, not `not_applicable`. OT holds 0 drug
  candidates for it, so there is no polypharmacology to catch; "target record
  exists, 0 candidates" is the right answer and my case was badly chosen.
- **MAPK14** rendered `value(4.0)` (Phase 3) on ARRY-797 × ankylosing
  spondylitis, genetic_association 0.41. I expected `not_applicable` because no
  p38α inhibitor was ever approved — but the ring reports the furthest
  *attributable* phase, not approval, and p38α inhibitors genuinely reached
  Phase 3 in RA/AS. **The app is right and my expected answer was wrong.**

---

## 2. FAIL — the V1 defect is still reachable. FRK renders "Approved".

**Command.** `.venv/bin/python /tmp/qa_v2_probe/generic_term.py`, confirmed in
detail by `/tmp/qa_v2_probe/frk.py`.

```
FRK   ENSG00000111816   drug_count = 6
  clinical_phase = value 5.0
  detail = "Approved — REGORAFENIB for age-related macular degeneration,
            attributable to this target (genetic_association 0.41).
            2 of 6 candidates have an attributable indication"
  pipeline line = "6 agents · 2 with an active trial · 2 with ≥1 trial stopped
                   for cause · 2 indicated for a disease attributable to this target"
```

FRK (Fyn-related kinase) has no approved drug. Regorafenib is approved for
colorectal cancer, GIST and hepatocellular carcinoma; it was *trialled* for AMD
and is not approved for it. The ring says Approved, at the brightest end of the
colormap, and the drawer states as fact that regorafenib is approved for AMD.

**Root cause, and it is structural, not a tuning problem.** In the OT payload
`drugAndClinicalCandidates.rows[]` is **one row per drug**, carrying a list of
~25 indications and **a single `maxClinicalStage` that is the drug's global
maximum across all of them**. I verified this directly: every one of
regorafenib's indications on FRK's row — Ewing sarcoma, glioblastoma, AMD —
carries `maxClinicalStage = APPROVAL`, and regorafenib is approved for none of
them.

`clinical_phase_cell` therefore asks the right question of the wrong quantity:
it tests whether *any* of the drug's indications is attributable to the target,
and then reports the drug's *global* max stage. One incidental genetic
association between an off-target and any disease the drug was ever trialled
for promotes an unrelated approval onto the ring. With 25 indications per
promiscuous drug, an incidental hit is not rare.

It is the same drug, regorafenib, and the same failure mode as the council's
original EPHA2 case. EPHA2 itself still renders `not_applicable` — the fix holds
there — but it holds by the margin of FRK×AMD scoring 0.41 and EPHA2×colorectal
scoring 0.000. ALK is attributed at **0.28 on the generic parent term
`neoplasm`** and BTK at **0.19 on `neoplasm`**, against
`ATTRIBUTION_MIN_SCORE = 0.1`. The right answers in §1.1 arrived partly for a
thin reason.

**This is not covered by any test the build wrote.** `TestV1IndicationAttribution`
reproduces the EPHA2 and sorafenib/RCC cases, where the genetics score is 0.000
and the gate closes on the *score*. It does not test the case where the gate
opens on a genuine association and then reports a stage belonging to a different
indication.

---

## 3. FAIL — V2-REPORT §2.1's own evidence line for BRAF does not reproduce

The report's "Evidence it works" block states:

```
BRAF    clinical_phase = 5.0    VEMURAFENIB × melanoma, genetics 0.699
```

Live, on the council's liver set
(`.venv/bin/python /tmp/qa_v2_probe/liverset.py`, run `QA-V2-LIVER`):

```
BRAF  state=value  value=5.0
      "Approved — SORAFENIB TOSYLATE for lung cancer, attributable to this
       target (genetic_association 0.83, ...)"
```

The value 5.0 is right — vemurafenib genuinely is approved for BRAF V600E
melanoma. But the exemplar the figure and drawer name is **sorafenib for lung
cancer**: sorafenib is not approved for lung cancer and is not a BRAF-mechanism
drug for it. The cause is the tiebreak in `clinical_phase_cell` — among rows
tied at the top stage it picks `max(evidence.values())`, and sorafenib×lung
(0.83) beats vemurafenib×melanoma (0.699). The stated rationale was "picking the
first would name a weak exemplar"; picking max-evidence names a *wrong* one, and
it names the exact drug the fix was built to reject.

Same mechanism as §2, milder consequence: here only the displayed claim is
wrong, not the number.

---

## 4. FAIL — §3.1's manifest claim. `any_invariant_failed` is not in `manifest.json`.

§3.1 says the red-banner/amber split is "in the manifest
(`any_invariant_failed`, `run_conditions`)". §7 B.8 asks QA to check exactly
this.

```
$ .venv/bin/python -c "import json; print(list(json.load(open('runs/QA-V2-LIVER/manifest.json'))))"
['run_id','timestamp_utc','git_sha','region','input_row_count','rendered_count',
 'ledger_count','gene_order','sources','schema_versions','contracts','invariants',
 'llm','ledger','weights','timings','not_attempted','environment','warnings',
 'pareto_completeness','run_conditions']
```

`run_conditions` and `pareto_completeness` are there. **`any_invariant_failed`
and `all_invariants_pass` are not.** Both are `@property` on `Manifest`
(`manifest.py:75`, `:79`) and `Manifest.write()` serialises with `asdict()`,
which does not include properties. The values are recoverable by reading the
`invariants` array, so nothing is *wrong* on disk — but the report's claim is
not true, and the specific promise that a screenshot's manifest answers the
red-banner question in one field is unmet.

---

## 5. FAIL — the honest safety-ring rename does not reach the figure

§2.4's central move is renaming the ring to
**"Safety flags & constraint — NOT evidence of safety"**. Where that string
actually appears:

| artefact | full label present? |
|---|---|
| `values.csv` (`ring_label`) | yes |
| `caption.txt` | yes |
| `figure.svg` (audit render) | **no** — truncated in the ring key to `Safety flags & constraint — NOT e…` |
| `figure-slide.svg` (the slide render) | **no** — the slide carries no ring key at all (by design, §3.4) |

The half of the label that does the honest work is elided on the audit figure
and absent from the artefact that goes in front of colleagues. The fix is real
in the data and invisible in the picture — which is the failure mode AGENTS.md
names ("Say this on the figure, not only in the report").

---

## 6. The five BLOCKING fixes, verified independently of the build's own tests

**Command.** `.venv/bin/python /tmp/qa_v2_probe/headline.py` — driven from the
real cells of the live `QA-V2-LIVER` run, not from fixtures.

**V1 — polypharmacology setting the phase ring.** **PASS as a mechanism,
FAIL as an outcome.** The attribution gate works and is well calibrated (§1).
It does not prevent a false "Approved" (§2, §3).

**V2 — one axis dominating four.** **PASS.** The council's exact probe:

```
baseline front (14 genes) : ['ASGR1','EGFR','PCSK9','SERPINA1','TTR']
+ ONEAXIS (druggability=1.0, other three no_data):
    pareto=False  pareto_k=1  eligible=False
    front unchanged: ['ASGR1','EGFR','PCSK9','SERPINA1','TTR']
    evicted vs baseline: NONE
    note contains "harder": False
  (0.7 variant): pareto=False
```

It neither enters nor evicts, and the note that stated the opposite of the code
is gone.

**V3 — a transient failure rewriting the headline.** **PASS**, three ways.

*(a) Exhaustive sweep.* Every one of the 52 valued (gene, axis) cells set to
`no_data` in turn: **no run lost a gene from the front other than the one that
failed.** The reason is sound and I checked it rather than taking it: removing a
gene from a ring renormalises the other genes' ranks but preserves their
ordering, so no pairwise domination can flip.

*(b) End to end, twice, with a source forced to fail.* §7 item 3, the one that
most threatens trust. `.venv/bin/python /tmp/qa_v2_probe/v3_twice.py` — four
full runs of the council's liver set through `run_analysis`, headline read back
out of each `figure.svg`:

| run | forced | diamonds in SVG | red banner |
|---|---|---|---|
| base | — | ASGR1, EGFR, PCSK9, SERPINA1, TTR | no |
| failA | PCSK9 safety → 503 | *(none)* | no |
| failB | PCSK9 safety → 503 | *(none)* | no |
| failC | APOB safety → 503 | *(none)* | no |

`failA == failB`: **the same input with the same failure gives the same answer.**
And the change from `base` is not silent — the figure says so in its own words:

```
◆ not drawn — only 9 gene(s) are eligible on all four axes
• 5 Pareto-axis cell(s) missing (safety 1, regional_specificity 3, clinical_phase 1)
  — 5 gene(s) are ineligible for the headline and carry no diamond
```

**So V3's defect — same input, different answer, no banner — does not
reproduce.** The headline cannot silently change.

*(c) One thing the report does not say.* It can silently **vanish**, at a
knife-edge. The liver set has exactly **10** eligible genes and
`PARETO_MIN_ELIGIBLE` is **10**. One transient 503 on one cell of one gene takes
it to 9 and removes **all five diamonds**. It is declared on the figure, so it
is honest — but §1.4 attributes the disappearing headline to the TPM floor and
does not mention that a single upstream blip does it too. On this input the
headline is one failed HTTP call away from not existing. Worth a line in §1.4.

**V4 — "no liability on record" vs "nobody looked".** **PASS.** Live, liver set:

```
BRAF     mean of 4/4 keys: hasSafetyEvent=-1.00, geneEssentiality=+0.00,
                           geneticConstraint=-0.93, isCancerDriverGene=-1.00
PCSK9    mean of 4/4 keys: hasSafetyEvent=+0.00*, geneEssentiality=+0.00,
                           geneticConstraint=+0.45, isCancerDriverGene=+0.00*
         * = key absent from the response … absence is that field's other value
TMEM229B mean of 4/4 keys: hasSafetyEvent=+0.00*, … isCancerDriverGene=+0.00*
```

Constant denominator, absence marked per key in the cell detail, on a gene with
`hasSafetyEvent` and on one without (§7 A.4 satisfied). The report's own caveat
— that this buys comparability, not the ability to separate clean from
unstudied — is correct and I agree with it. The rename that carries that caveat
does not reach the figure (§5).

**V5 — the footer contradicting the code.** **PASS.**

```
DEFAULT_WEIGHTS : {'druggability':1.0,'safety':1.0,'regional_specificity':1.0,'literature':0.5}
RANKED_RINGS    : (druggability, safety, regional_specificity, clinical_phase,
                   clinical_activity, literature)
rodent in weights: False    rodent in ranked: False
clinical_phase in weights: False   (still a Pareto axis: True)
figure.svg says: "Excluded from every score: clinical_phase, rodent"
                 "Mouse-phenotype ring: a LEAD … ranked nowhere and weighted nowhere,
                  asserted against the weights at draw time rather than just claimed"
```

The footer and the data structures agree, and `assert_footer_is_true` makes them
unable to drift.

---

## 7. The seven invariants — real, not cosmetic (§7 item 5)

**Command.** `.venv/bin/python /tmp/qa_v2_probe/mutate.py` — takes the artefacts
of the real `QA-V2-LIVER` run, breaks one thing at a time, re-runs `check_all`,
and requires the invariant to stop saying PASS.

```
BASELINE: {I1:PASS, I2:PASS, I3:PASS, I4:PASS, I5:PASS, I6:PASS, I7:PASS}

I1  drop one CSV cell row                                 -> FAIL           REAL
I2  paint a no_data cell in a value colour                -> FAIL           REAL
I3  swap spoke 0 and spoke 1 in gene_order                -> FAIL           REAL
I4  input count +1 with no extra ledger entry             -> FAIL           REAL
I5  Europe PMC echoed a different query                   -> FAIL           REAL
I5  NCBI ERROR key in a 200 body                          -> FAIL           REAL
I5  14 replayed echoes, 0 live (warm cache, EBI down)     -> NOT_EVALUATED  REAL
I6  five-ID regression reports a shifted join             -> FAIL           REAL
I7  a front gene with only 1 of 4 axes                    -> FAIL           REAL
I7  a diamond drawn on a gene not in the front            -> FAIL           REAL
I7  figure drew 1 diamond, ranking says 5                 -> FAIL           REAL
```

**Eleven mutations, eleven caught. No invariant is a cosmetic PASS.**

**V11 specifically — can I5 report PASS on a warm cache while a source is down?
No.** Demonstrated in both directions on real runs tonight, not just by mutation:

- `QA-V2-LIVER` (warm cache, EBI 503-ing): `I5 NOT_EVALUATED — every one of the
  14 echo(es) came from the on-disk cache. They were replayed, not checked:
  nothing was asserted against Europe PMC this run.`
- `QA-V2-SIXTY` (cold, 60 genes, Europe PMC intermittently answering):
  `I5 PASS`, with live echoes.

This is the V11 fix working, and it closes §7 B.7 in both halves.

**§7 B.5 / B.6 — outage vs failure.** I did not need to block `ebi.ac.uk` at the
firewall: **EBI is genuinely degraded**, so this was tested against a real
outage. Every run tonight: `RUN CONDITIONS` present in the SVG,
`DO NOT TRUST THIS FIGURE` absent, `data_is_sound=True` with `ok=False`. A
forced I-failure banners red (mutation test above, and the build's
`4-invariant-banner.png`). **The two channels are genuinely separate.**

---

## 8. My own v0 BLOCKING items, re-run (§7 item 4)

**Command.** `.venv/bin/python /tmp/qa_v2_probe/b1_b7.py`

**B1 — OT schema. PASS.** The pinned `TARGET_QUERY` returns 200 live (BRAF: 28
tractability entries, 18 drugs). A deliberately bad query
(`knownDrugs{count}`) raises `HardFailure` with the GraphQL errors, so a 400
aborts rather than rendering an empty ring.

**B2 — the five-ID regression. PASS, and the hazard is still live.**

```
five_spokes=ok; missing_is_explicit=ok; braf_matches_single_query=ok;
survivors_not_shifted=ok
  braf_matches_single_query: symbol=True tractability=True (28 entries)
                             drug_count=True (18 vs 18) prioritisation=True
```

And the underlying API behaviour, re-measured tonight: requested 5 IDs, **4
returned**, `ENSG00000999999` silently dropped. The defect that would produce a
plausible wrong figure is still present upstream and still caught here. This is
the check I care most about and it is in good shape.

**B3 — Europe PMC malformed query. PASS on the correction; the live
re-measurement is NOT-EVALUATED.**

The build claims it *corrected* my B3 finding — that the echo alone is not
sufficient and queries must be validated before sending. **Verified:**

```
validate_query('"BRAF')             : REJECTED before sending
validate_query('BRAF AND (knockout'): REJECTED before sending
validate_query('BRAF"S"MITH"')      : REJECTED before sending
validate_query('')                  : REJECTED before sending
```

The correction is right and I accept it: an echo detects silent
*reinterpretation*; it cannot detect a malformed query the server is willing to
run. Both checks are needed and both are present.

What I **could not** do tonight is re-measure the 12× inflation live —
`?query="BRAF` returns `HTTP 503` from EBI. So the *numbers* in my v0 B3 are
unverified as of today; the *behaviour of this build* against them is verified.

**B4 — HPA.** N/A. HPA was replaced by GTEx; the finding does not apply.

**B5 / B6 — renames, ambiguity, Excel corruption. PASS.**

```
SEPT9              -> renamed        ENSG00000184640 SEPTIN9
MARCH1             -> renamed        ENSG00000145416 MARCHF1
9-Sep              -> excel_corrupt  (re-export as Text)
ALB                -> ambiguous      (refused, not guessed)
braf               -> ok             ENSG00000157764 BRAF
ENSG00000157764.14 -> ok             (version suffix stripped)
Trp53              -> possible_mouse ENSG00000141510 TP53
ZZZNOTAGENE123     -> no_match
```

**B7 — input contract and drop ledger. PASS.** On `fixtures/de_broken.csv`
(run `QA-V2-BROKEN`): **16 in / 7 rendered / 9 ledgered**, `I4 PASS`, and all
nine dropped names appear on the figure itself. `ledger.csv` carries 12 rows —
the nine drops plus `SEPT9 renamed`, `MARCH1 renamed`, `Trp53 possible_mouse`,
which were rendered, not dropped. That is a fuller audit trail than §7 D.15
promises, not a discrepancy.

### A NOTE-level finding in B7's neighbourhood

`MOUSE_LIKE_RE` (`resolve.py:46`) flags the entire human `Cnorfnn` family as
`possible_mouse`:

```
C9orf72  possible_mouse    C1orf53  possible_mouse    C19orf12 possible_mouse
```

`C9orf72` is the commonest genetic cause of ALS/FTD and will appear in real DE
tables. The gene still renders (`possible_mouse` is `usable`) and the flag is a
warning rather than a conversion — which is what my v0 B7 asked for — so nothing
is lost. But a flag that fires on ~200 legitimate human symbols is a flag people
learn to ignore. This is a v1 carry-over, not a v2 regression.

---

## 9. Regression surface (§7 D) and the figure at n = 60 (§7 C.12)

**D.13 — the test suite. PASS.** `.venv/bin/python -m pytest -q -rs`

```
267 passed, 2 skipped in 189.50s
SKIPPED tests/test_contract.py:148: ChEMBL unreachable, contract NOT evaluated:
        HTTP 500 from .../chembl/api/data/target.json (content-type='text/html')
SKIPPED tests/test_contract.py:220: ChEMBL unreachable, contract NOT evaluated:
        HardFailure: HTTP 500 from .../target.json?target_components__accession=P15056
```

267 as claimed, and both skips are source outages naming the source, as §7 D.13
requires.

**D.15 — the broken fixture. PASS.** See §8 B7.

**D.16 — the 200-gene cap. PASS.** 200 rows in, 140 ledgered `dropped_over_cap`
**before any network call**, the surviving 60 resolved, 1 rendered, 199
ledgered, `I4 PASS`, no pycirclize traceback, whole run 16.6 s. `ledger.csv`
carries all 200. (The figure itself names only the first handful of drops — it
cannot print 199 names — and the export carries them all. Correct, worth
knowing.)

**C.12 — 60 real genes at the new cap. PASS.**
`.venv/bin/python /tmp/qa_v2_probe/sixty.py`, run `QA-V2-SIXTY`:

```
60 in / 58 rendered / 2 ledgered
I1..I7: PASS PASS PASS PASS PASS NOT_EVALUATED PASS
diamonds: ANGPTL3 ASGR1 EGFR ERBB2 ESR1 MAPK14 PCSK9 SERPINA1 TTR  (9 of 28 eligible)
no collision assertion raised
```

`assert_no_text_over_the_rings`, `assert_centre_fits_the_hole` and
`assert_text_within_canvas` all held at n = 60. §1.6 notes collision tests cover
only n = 2, 6, 40; **n = 60 is now covered and clean.**

---

## 10. EBI is degraded — what is this build and what is the outage (§7 item 6)

Measured per-source, from the manifests of tonight's runs:

| run | genes | chembl | europepmc | OT | GTEx | PubTator | total |
|---|---|---|---|---|---|---|---|
| `QA-V2-LIVER` (warm) | 14 | 46.7 s | 0.0 s | 0.03 s | 0.0 s | 0.0 s | 64.9 s |
| `QA-V2-BROKEN` (warm) | 7 | 45.5 s | 0.0 s | 0.01 s | 0.0 s | 0.0 s | 57.9 s |
| **`QA-V2-SIXTY` (cold)** | **58** | **125.1 s** | **98.3 s** | 4.2 s | 1.4 s | 2.8 s | **148.0 s** |

**Outage artefacts — not this build's behaviour:**

- the 2 skipped ChEMBL contract tests, so **ChEMBL's response contract is
  unverified tonight**;
- `chembl_s` dominating every run (ChEMBL 500s on an HTML error page);
- `europepmc_s = 0.0` on the warm runs — Europe PMC 503s instantly rather than
  hanging, so §1.1's "Europe PMC 80 s (64%)" is a *different* outage mode from
  tonight's;
- `I5 NOT_EVALUATED` on warm runs;
- B3's live 12× inflation, unmeasurable.

**Genuinely this build:** everything in §1–§9 above. The attribution rule, the
Pareto eligibility rule, the invariants, the ledger, the cap, the footer
assertion, the FRK defect and the manifest omission are all independent of EBI.

**What changes when EBI recovers:** the two skipped tests run (and ChEMBL's
contract becomes evaluated for the first time tonight); B3's numbers become
re-measurable; I5 reports PASS more often. **No verdict in this document
reverses.**

### One measurement the report says it does not have

§1.2 says the speed target is "unproven either way" because no 50-gene run was
made. I made a 58-gene cold run. **148 s wall, of which 129 s is `adapters_s`
and 125 s of that is ChEMBL — a source that drives no ring and no rank.** The
five sources that actually draw rings cost **8.4 s combined** (OT 4.2 + GTEx 1.4
+ PubTator 2.8).

So PLAN §5's "cold 50 genes under 60 s" is missed by 2.5×, and **the entire miss
is two drawer-only sources.** Moving them off the render path (§6 decision 1)
would put a 60-gene cold run at roughly 20 s — comfortably inside the target.
That is the strongest evidence I can give for a decision the report has now
asked for twice.

---

## 11. What I could not evaluate, and why

| item | why |
|---|---|
| §7 C.9 — paste `figure-slide.svg` into a real 16:9 slide and read labels at arm's length | No PowerPoint or Keynote on this machine. The 16:9 geometry and the 11.5 pt type are asserted in `TestA4SlideRender`; **nobody has looked at it in a slide.** |
| §7 C.10 — open `figure.svg` in Illustrator, confirm text is editable, see what DejaVu substitution does to the layout | No Illustrator. `svg.fonttype='none'` is pinned and asserted, so the text *is* text; the UX review's prediction that the A6 collisions "will move rather than disappear" under font substitution **remains untested**. |
| §7 C.11 — print `figure.png` on a mono laser printer | No printer. The greyscale disjointness is asserted numerically on every render; **nobody has held the paper.** |
| §7 B.5 with a firewall block | Not needed and not done — EBI was genuinely 503/500-ing all night, which is a better test than a simulated one. |
| B3 live 12× inflation | Europe PMC 503. |
| ChEMBL response contract | ChEMBL 500. |
| LLM triage | Unbuilt by design; `with_triage=True` raises `NotImplementedError`. $0.00 spent, cap untouched. |

---

## 12. What I would fix before Puck opens the app

Ordered by what a wrong figure costs, not by effort.

1. **§2 — FRK. `maxClinicalStage` is the drug's global stage, not the stage for
   the attributed indication.** Until that is separated, the ring can say
   "Approved" for a target with no approved drug, which is the exact defect V1
   was raised to kill. If OT does not expose a per-indication stage, the honest
   fallback is to cap the ring at the highest stage *evidenced for the
   attributed indication* via `clinicalReports.trialPhase`, or to label the ring
   "furthest phase reached by any drug with an attributable indication" — which
   is what it actually measures.
2. **§3 — the tiebreak.** Prefer the row whose indication is attributable *and*
   whose drug's mechanism matches, or name the on-target exemplar, not the
   highest-scoring one. As it stands the figure prints a false clinical claim
   about a real drug.
3. **§4 — put `any_invariant_failed` in `manifest.json`.** One line; the report
   already claims it is there.
4. **§5 — get "NOT evidence of safety" onto both renders, unelided.**
5. **§6(c) — add a line to §1.4:** the headline also vanishes on a single
   transient failure when `n_eligible` sits on the threshold, as it does on the
   council's own liver set.

Nothing in 1–5 is a reason to stop using the app. The accounting, the ledger,
the join discipline, the invariants and the outage/failure split are in good
condition, and the attribution rule is a real improvement that costs less than
its author feared.

---

**Artefacts.** `runs/QA-V2-LIVER`, `QA-V2-V3-{base,failA,failB,failC}`,
`QA-V2-BROKEN`, `QA-V2-REALISTIC`, `QA-V2-200`, `QA-V2-SIXTY`.
**Probes.** `/tmp/qa_v2_probe/{attribution,generic_term,frk,liverset,headline,v3_twice,b1_b7,mutate,final,sixty}.py`.
