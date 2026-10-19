# Value-presentation review — 2026-10-19

Read-only review of every number a biologist sees, judged on: would they read
it correctly in five seconds without the docs? Ranked worst first. No change
proposed to data, thresholds, or the figure centre text.

Surfaces read: `targetring/figure.py` `RING_SPECS`, `targetring/static/app.js`
(`renderTable`, `cellHtml`, `num`, `recommendationHtml`, `drawerHtml`,
`renderLedger`, `renderInvariants`), the demo run `20261018T190526Z-94ef5f`
(`values.csv`, `scales.json`, `drawer/*.json`), `docs/flow/flow.json`.

---

## 1. The Literature column prints the logarithm, not the count — **moderate**

**Where** Table ring 11, and `values.csv` `value`. **Now** `4.609` under a
header reading *Literature volume* whose tooltip says the units are
*PubTator3 records*. The real count is **40,606**. **Why it misleads** A log
value sitting in a column labelled in records is a log scale presented as
linear, and `4.609` reads as "about five papers" — the single worst misread on
the page. The ring's scale strip gets this right (ticks are records); the table
does not. **Fix** Print the record count in the cell: `40,606`, thousands
separator, `title` keeping `log10(1+n) = 4.61`. Header → `Literature —
PubTator3 records`. The cell has `detail` with the raw count already; this is a
display branch in `cellHtml`, not new data.

## 2. Categorical and ordinal rings print bare integers — **moderate**

**Where** Table rings 8 (trial activity), 9 (TDL), 10 (mouse phenotype), 4
(chemical probe); also the scale strip ticks for the same four.

**Now**

| ring | cell / tick | means |
|---|---|---|
| Pipeline: current activity | `-1`, `0`, `1` | stopped for cause, dormant, live |
| Target development level | `0…3` | Tdark, Tbio, Tchem, Tclin |
| Mouse phenotype lead | `0`, `1` | no abnormal phenotype, abnormal phenotype |
| Chemical probe available | `0`, `1` | no probe, probe exists |

**Why it misleads** `-1` on activity is the clearest case: the ring's own units
line says *three named states, no order implied by colour*, and then the table
prints a signed number, which reads as a negative score and as ordered.
`1` on mouse phenotype reads as "one phenotype" (CXCL1 has six). `2` on TDL
reads as a rank out of something. All four have the words in `category_labels` /
`detail` already. **Fix** `cellHtml` prints the state word for any ring with
`categorical` or `category_labels`, and for the two binaries prints `yes`/`no`
and `abnormal`/`no abnormal`; numeric value stays in the `title`. Scale ticks
likewise: `stopped · dormant · live`, `Tdark · Tbio · Tchem · Tclin`,
`no · yes`, `no abnormal · abnormal`.

## 3. Regional specificity is a ratio with no base on the cell — **trivial**

**Where** Table ring 6, scale ticks. **Now** `1.33`, `0.669`; ticks `0.669`
and `11`. **Why** The unit is "× median across the GTEx panel" and the `×`
appears only in the tooltip — `11` reads as 11 TPM. And nothing on the cell
says 1.0 is the no-enrichment line, so `0.669` does not read as *below* the
panel median. **Fix** Format `11.0×`, `0.67×`; ticks `0.67×` … `11×`; add a
`1× = panel median` marker or the words to the strip note. Header →
`Regional specificity (× panel median)`.

## 4. Abbreviations unexpanded on first sight — **trivial**

**Where** Table headers and the drawer. **Now** `logFC`, `padj`, `TDL`, `TPM`,
`k`, `OT` (in `detail` strings and ledger reasons), `KO` (`flow.json`
`met_when`: *the mouse-KO ring > 0*). **Why** A biologist knows logFC and TPM;
`padj`, `TDL` and `k` as column heads, and `OT`/`KO` in prose, are ours not
theirs. **Fix** Headers: `−log10 adjusted p`, `Target development level (TDL)`.
Expand `OT` → `Open Targets` in every `detail` and ledger reason. `KO` →
`knockout` in `flow.json`. `k` keeps its letter (it is on the figure) but the
header becomes `k — rings measured` and the tooltip opens *how many of the 9
external rings have a real measurement for this gene; it is a completeness
count, not a score*.

## 5. `−log10 adjusted p` reads as a whole-number score — **needs a decision**

**Where** Table ring 2, scale ticks `13`…`22`, the owner's own complaint.
**Now** `13.4`, `22.1`; nothing says these are logs, let alone of what.
**Why** The two highest-looking numbers on the figure are a transform of the
smallest p-values, and a reader who does not expand the header sees a 13–22
score. **Fix, two options, owner's call**: (a) keep the ring value and add the
raw p to the cell text — `13.4` with `p = 4.0e-14` beneath or in the hover, and
the scale note *higher = smaller p; 13 ≈ 1e-13, 22 ≈ 1e-22*; or (b) print the
adjusted p itself in the table cell (`4.0e-14`) and leave the −log10 only as
the ring's colour. (b) is the five-second read; (a) keeps the table cell and
the ring value the same number, which is an invariant of the current design.
Not decidable without you.

## 6. `p 4e-14` in the verdict box drops "adjusted" — **trivial**

**Where** Drawer / highlight box, `criterion.bare` for `dysregulated`
(`recommend.py`). **Now** `logFC +2.2, p 4e-14`. **Why** The adjacent `shown`
string says *adjusted p 4.02e-14*; the bare line, which is the one on screen,
says `p`. A raw p and an FDR-adjusted q are different claims and a reviewer
will read `p` as raw. **Fix** `logFC +2.2, adj p 4e-14`.

## 7. Tractability and Safety print 4 significant figures on means of 3 and 4 keys — **moderate**

**Where** Table rings 3 and 5. **Now** Tractability `0.3333`, `0.6667`; Safety
`0.01368`, `-0.3774`. **Why** `0.3333` is *one of three keys true* and the
decimal implies a continuous score; `0.01368` implies four-digit resolution on
a quantity whose keys are mostly −1/0/+1. Rounding here exaggerates precision.
**Fix** Tractability cell `1 of 3` (`title` keeps `0.333`). Safety to two
decimals, `+0.02`, `−0.38`, with the sign spelled out in the header tooltip —
*negative = a liability flag is set (amber arm); this ring is NOT evidence of
safety*. The "amber arm is the bad one" sentence exists only on the scale
strip, where the table reader never goes.

## 8. Positive logFC has no direction statement away from the figure — **moderate**

**Where** Table ring 1; drawer *your measurement* (`<dt>logFC</dt>`). **Now**
`3.1`, `2.2` — no sign convention, and the drawer's own `contrast_text` and the
run's `sign_sentence` are in the JSON but never rendered in the drawer.
**Why** "positive log2FC = up in Crohn's disease (n=61) vs control (n=13)" is
on the figure centre and on the contrast panel, and a reader who arrives at the
table or opens a drawer from the exported page sees neither. Sign conventions
are the classic DE misread. **Fix** Table header tooltip for ring 1 gains the
run's `sign_sentence` verbatim. Drawer: one line under *your measurement*
printing `contrast_text` + the sign sentence. Display-only, data already there.

## 9. `no_data` on a gene that WAS measured — **needs a decision**

**Where** Table ring 6 for FCGR1A, FCN1. **Now** cell reads `no data`; the
hover says `0.5814 TPM in Small_Intestine_Terminal_Ileum is below the 1 TPM
floor`. **Why** The legend defines `no data` as *never measured here*, and GTEx
did measure this gene — it measured it as near-zero. A reader concludes GTEx is
silent on FCGR1A, which is false, and the expressed criterion then reads as
unevaluable rather than as failed. **Fix, your call** either a fourth word for
this case — `below floor` with the same hatch, so it is still never a value and
never zero — or keep the state and reword the hover's first clause to *measured
at 0.58 TPM, below the 1 TPM floor for a ratio — not absence*. The second is
trivial; the first touches the state vocabulary, which is rule 1 territory.

## 10. Flow page stat lines name the method, not the meaning — **trivial**

**Where** `docs/flow/flow.json` → flow page. **Now** `n_with_statistic
25,232`, `n_without_statistic 39,985`, `ledgered 1`, `n_up 59 / n_down 1`.
**Why** "with a statistic" is our word for "has an adjusted p in the submitted
table"; `ledgered` is pure internal vocabulary on a page written for readers;
`n_up 59` is a direction with no group named. **Fix** *25,232 genes have an
adjusted p in this contrast · 39,985 do not* · `ledgered` → *accounted for off
the figure* · *59 up in Crohn's disease, 1 down* (reuse `sign_sentence`).

## 11. Drop-ledger statuses are raw enum tokens — **trivial**

**Where** Ledger tab, `ledger.csv` `status`. **Now** `unresolved`,
`dropped_no_padj`, `filtered_no_data`, `possible_mouse`, `excel_corrupt`.
**Why** Underscored identifiers read as machine state, and two are actively
confusing: `filtered_no_data` does not say whether the gene was dropped *for*
having no data (it was), and `possible_mouse` names a suspicion with no
sentence. The `reason` column carries the English, but the `Status` pill is
what a reader scans. **Fix** A display map: `unresolved` → *no match in Open
Targets*, `dropped_no_padj` → *no adjusted p in your table*,
`filtered_no_data` → *dropped: no value on the filtered ring*,
`possible_mouse` → *symbol may be the mouse gene*, `excel_corrupt` → *Excel had
changed the symbol*. Keep the token in the CSV and in the pill's `title`.

## 12. Invariants tab names checks, not what passing means — **trivial**

**Where** Invariants tab. **Now** `I2 missing-fill disjointness`,
`I6 five-ID regression`, `I9 three-stage accounting`. **Why** These are method
names; a reader cannot tell what a FAIL would mean for the figure in front of
them. The existing `NOT_EVALUATED is not a pass` note is exactly right and the
names should match that register. **Fix** Append a plain clause to each `name`:
*missing-fill disjointness — no missing cell shares a colour with a value*;
*five-ID regression — the five known-hard genes still land correctly*;
*three-stage accounting — the gene counts add up from the whole table to the
twelve drawn*.

## 13. `values.csv` carries no units column — **moderate**

**Where** The exported CSV. **Now** `ring,ring_label,state,value,…`; `value`
holds a log for literature, a −1/0/1 code for activity, an ordinal for TDL and
a ratio for specificity, with nothing in the row to say which. **Why** This is
the artefact that leaves the tool, and in Excel it is twelve different quantity
kinds in one column. **Fix** Add `units` (copy `RingSpec.units`) and
`value_shown` (the string the table cell prints after items 1–3). Needs a
decision only because it is an artefact-schema change and `I1`'s cell-count
identity reads this file.

---

### Already read well, for the record

The three missing states and their legend sentences; `input = rendered +
ledgered` with its arithmetic spelled out; the `last, not lowest` divider;
`cannot be evaluated` written out as a word rather than left to opacity; the
distinct-trials vs drug-trial-pairs double count and its note; the
`EACH RING HAS ITS OWN SCALE` strip header; the dated spend lines; the baked
drawer's date-before-content note; `NOT_EVALUATED is not a pass`. The pattern
in every one of these is that the honest sentence already exists somewhere in
the codebase — in `units`, `detail`, `category_labels` or `transform` — and the
items above are mostly cases where the surface a reader actually looks at shows
the number instead of the sentence.
