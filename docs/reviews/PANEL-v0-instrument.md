# Panel review — the choice of instrument

**Verdict, in three lines.** The circos is **right for one job and wrong for
another**. It is the right artefact for carrying a stated result and its
provenance into a room — one object, one gene set, one region, stamped and
auditable — and Puck already built it by hand, which settles the question of
what his colleagues recognise. It is the **wrong instrument for the reading it
is being asked to support**: deciding which targets are worth attention means
comparing genes within one ring and reading one gene across twelve, and this
layout puts the first on the weakest encoding at the worst geometry and prints
a banner forbidding the second.

**Date:** 2026-09-27. **Reviewer:** instrument panel (Wren II).
**Scope:** the choice of instrument, not the implementation. Approved by Puck on
WhatsApp, 2026-09-27.

**Everything numbered below was measured on this Mac today** against the
artefacts under `runs/`, the code in `targetring/`, and the repo's own
instruments (`scripts/ux_v3_overlap.py`, `scripts/check_ring_colours.py`) run
unchanged. Probes were written to `/tmp/panel_probe/`, deliberately outside the
repo. **No code was changed, no git command was run, and this file is
untracked.** No paid call was made; $5.00 of $5.00 is unspent. Local only.

**Ordering:** BLOCKING → SHOULD-FIX → NOTE. Item ids are stable: `P*`.

---

## 0. What this review is answering

COUNCIL v0/v1, QA v0/v2 and UX v1/v3 rounds 1–3 all took the twelve-ring circos
as given and worked on making it correct and legible. They did that well: the
drop ledger, the join discipline, the four cell states, the seven invariants and
the outage/failure split are in better condition than most shipped tools. None
of them asked whether a twelve-ring circos is the right container for twelve
heterogeneous per-gene metrics. That is the only question here.

The task the instrument has to serve, stated as operations rather than as a
mood:

1. **Within one ring, across genes** — "which of these 14 genes is most
   tractable?" A magnitude comparison over a set.
2. **Within one gene, across rings** — "what does BRAF look like overall?" A
   profile read.
3. **Defend it** — "why is that cell bright?" Trace one cell to one number to
   one source, in front of someone.

---

## 1. The strongest case FOR the circos, made properly

This is not balance for its own sake. Four of these are real and one of them is
close to decisive.

**1.1 It is the specified deliverable, not a free choice.** `AGENTS.md:8-9`: *"It
reproduces, automatically, a figure he used to build by hand at work."* The
instrument is part of the requirement. An alternative that reads better and is
not the figure his colleagues already know is not obviously a win, and nothing
in this review overrides that. This is the argument I could not get past, and it
is why the recommendation in §4 is additive rather than a replacement.

**1.2 A closed ring is the only common layout whose primary axis carries no
implicit rank.** Rule 7 removed ranking twice, after the mechanism reproduced
the defect it was prescribed to fix (COUNCIL V2, V10). Every rectangular
alternative puts genes in rows, and row 1 reads as "best" whether or not you say
so — a reader who has been told nothing is ranked will still read the top of a
list as the top of a list. A circle has a stated start and the figure prints it,
but no one reads a ring as a league table. Under rule 7 that is a genuine and
underrated property, and it is the one argument here that no alternative in §4
fully answers.

**1.3 Missingness is legible as a shape.** Hatched `no_data` cells accumulate
into radial streaks, and on the current render `TMEM229B (k=6/9)` and
`MIR29B2CHG (k=3/9)` read as "mostly unmeasured" at a glance, before any label
is read. Rule 1 exists to stop absence reading as low, and the radial streak
does that work pre-attentively. A grid does it too, but not better.

**1.4 The hole is a real asset.** 22.5% of the disc (`r_inner²/r_outer²` =
46²/97², `figure.py:1363`) is a bounded, always-empty region in the middle of
the artefact, and the build spends it on exactly the right things: the counts,
the dropped-gene names, the spoke-ordering rule, the active filters. On a
rectangular layout those become a header block competing with the data for the
top of the canvas. Rule 3 and invariant I7 are better served here than they
would be elsewhere.

**1.5 It is one object.** `input == rendered + ledgered` with k printed per gene
and a run id and git SHA stamped on the canvas means a screenshot is
self-describing. That is the build's best idea and the circle is a good frame
for it.

**So: if the question were "is this a good picture of a result", the answer is
yes.** The rest of this review is about the other question.

---

## 2. BLOCKING

### P1 — No render states which band is ring 1. The correspondence between the numbered key and the twelve bands exists only in a separate file.

**Claim.** On the slide and the app renders there are no ring names inside the
circle. A reader has a numbered list 1–12 in a column and twelve unlabelled
concentric bands, and **no render says whether the numbering runs outward-in or
inward-out.**

**Evidence.** Searched the drawn text of all three renders of run
`20260927T030021Z-f20afd` for every word that could state the direction:

| render | `outermost` | `innermost` | `inward` / `outward` | `from the outside` |
|---|---|---|---|---|
| `figure.svg` (audit) | no | no | no | no |
| `figure-slide.svg` | no | no | no | no |
| `figure-web.svg` | no | no | no | no |

The correspondence is stated in exactly one place, and it is not an artefact a
reader is holding: `caption.txt` line 5 — *"Rings, outermost first (scale is
per-ring; …)"*.

It matters on the slide and the app because neither names its rings in place:
`SLIDE` is declared `wedge_deg=0.0` (`figure.py:1120`) and `WEB` carries
`wedge_labels_in_svg=False` (`figure.py:1237`). Only the 19.5 in `AUDIT` layout
(`figure.py:1024-1027`) puts each name at its own ring's radius, and that is the
render UX v3 records as 154 of 155 strings under the screen floor when opened on
a laptop (`UX-v3-rounds.md` §3.3).

The slide's own footer is precise about everything except this: *"ring names are
on their own scales in the column, right, not inside the circle — at this size a
ring band is 11 px and the names would overlap each other."* It says where the
names went. It does not say which end of the stack is number 1.

**Why this is BLOCKING and not cosmetic.** A reader who guesses the direction
wrong does not get a confusing figure, they get a **complete and plausible wrong
one**: ring 1 (`logFC`, the submitted measurement) reads as ring 12 (`k`), ring
5 (`Safety flags & constraint — NOT evidence of safety`) reads as ring 8 (trial
activity now), ring 3 (tractability) reads as ring 10 (mouse phenotype). Twelve
mirror-image misreadings, every one of them self-consistent, none of them
detectable from the artefact. `AGENTS.md:17` calls that the worst possible
output. The default guess happens to be the correct one, which is precisely why
this has survived: it has been right by luck in every reading so far.

**Change.** State the direction on every render — the slide and web scale
columns are already headed, and *"1 is the outermost band"* is four words at the
top of the column. Assert it in the same place I7 asserts the spoke-ordering
rule: the direction is read back out of the drawn SVG, and a render that does
not state it fails rather than being believed.

### P2 — Radial position is the build's own declared carrier of ring identity, and it is not legible on either of the two renders people use.

**Claim.** The build has already established, in code, that colour cannot
identify twelve rings, and named radial position as what carries identity
instead. Radial position then turns out to be an 11.2 px unlabelled band on the
projected artefact. Both the primary encoding and its declared fallback fail on
the same render.

**Evidence, first half — colour cannot do it, and the build says so.**
`assert_rings_are_distinguishable` (`figure.py:769-812`) asserts only that no two
rings in different families paint the *identical* hex. Separation beyond
identity is deliberately not asserted, and the docstring is unusually honest
about why (`figure.py:779-793`):

> *"Making it an assertion would be dishonest, because it cannot be met and the
> reason is geometric rather than fixable … Twelve rings cannot all be pairwise
> far apart on one axis … the darkest cell of any two rings is two near-blacks
> whatever their hues."*

Measured, `PYTHONPATH=. .venv/bin/python scripts/check_ring_colours.py`, today:

```
worst touching-ring pair anywhere on the two ramps: dE 2.1, against a target of 8.
5 of the 10 scored boundaries clear it
all pairs, worst equally-bright cell pair over the WHOLE band:
  normal  logfc <-> safety             dE 0.8
  protan  clinical_phase <-> completeness  dE 0.1
  deutan  logfc <-> rodent             dE 0.1
```

ΔE 0.1 is the same colour. For a deuteranopic reader two rings can paint
indistinguishable cells, and the docstring is right that no palette fixes it at
twelve.

**Evidence, second half — the fallback is not available where it is needed.**
The same docstring names the remedy: *"the fixed radial position, the ring
number, and the labelled swatch on each ring's own scale in the column."* But:

* On the slide, `figure.py:1039-1050` records the measurement that killed the
  in-circle names: the plotted square is 552 px, twelve rings span
  `(97-46)/105 × 276 = 134 px` of radius — **11.2 px per band** — against one
  line of 8.4 pt type at 13.0 px tall. *"The ring names would overlap each other
  before they came near a sector edge."*
* On the app, `logs/v32-web-fit-sweep2.txt` refuses **every** circle it tried,
  up to 640 px: `'Tractability (suggested)' at 5.9 pt overhangs the 26° gap`.
  UX v3 round 2 item 6 puts the bound at roughly 790 px, which is the canvas A4
  existed to shorten.

So on the two renders a reader actually uses, identifying a ring means **counting
bands inward on an 11.2 px pitch** and then matching the count to a numbered list
in a column — and per P1, without being told which end to start counting from.
This is COUNCIL **V18** (*"Ring identity is only recoverable by counting"*),
raised at ten rings, filed NOTE, never escalated, and now true again at twelve on
two of three layouts after v2.7 fixed it on the third.

**This is the instrument finding, not a rendering finding.** Twelve concentric
bands is what makes the band 11.2 px; 11.2 px is what makes the name not fit;
the name not fitting is what pushes ring identity onto a counted radial index.
Nothing downstream of the choice of instrument can undo it.

**Change.** Either accept that twelve rings cannot be named in place and make the
counted index safe (P1's direction statement, plus a heavier rule line every
third band so counting is chunked rather than serial), or reduce the number of
concentric tracks. Twelve is the number that sets the 11.2 px; six would set
22.4 px, which fits 13.0 px type.

### P3 — The slide render prints spoke labels through its own cell-state legend, and no assertion in the build can see it, because the circos forces rotated labels and the overprint check skips rotated text.

**Claim.** `figure-slide.svg` — the artefact that gets projected — has genuine
glyph-on-glyph overprint on the cell-state legend, today, and it has had it since
at least UX v3's own final run.

**Evidence.** `scripts/ux_v3_overlap.py` on `runs/20260927T030021Z-f20afd/figure-slide.svg`:

```
figure-slide.svg: 115 text runs, 0 EXACT overlapping pairs, 8 aabb-only (> 1.0 pt both axes)
   [aabb-only] 66.9x9.0 pt  12.1pt 'BRCA1 (k=7/9)'  vs 9.0pt 'Pipeline: current activity (Open Targets'
   [aabb-only] 31.6x9.0 pt  12.1pt '⚠ BRAF (k=9/9)' vs 9.0pt 'Pipeline: current activity (Open Targets'
   [aabb-only] 31.2x9.0 pt  12.1pt 'PTEN (k=7/9)'   vs 9.0pt 'Pipeline: current activity (Open Targets'
   … 8 pairs
```

The instrument classifies these `aabb-only` — bounding-box adjacency, not ink.
**It is wrong here, and I checked the pixels rather than trusting it.**
`/tmp/panel_probe/slide-topleft.png` is the region at 2×: `PTEN (k=7/9)` is drawn
straight through *"stopped for cause — OT category Negative / Safety_Sideeffects"*
and *"dormant — drugs exist, no live trial, no for-cause stop"*; `⚠ BRAF (k=9/9)`
crosses `Safety_Sideeffects` and `no for-cause stop`; `BRCA1 (k=7/9)` crosses
*"live trial — recruiting or ongoing"*.

It is not new and it is getting worse. Run by run, same 14-gene input:

| run | date | aabb pairs on the slide |
|---|---|---|
| `20260926T201344Z-64f760` (UX v3 round 3's own re-review) | 09-26 | 4 |
| `20260927T030021Z-f20afd` (current) | 09-27 | 8 |

`/tmp/panel_probe/r3-slide-topleft.png` shows `GAPDH (k=7/9)` printed through all
three pipeline rows on round 3's final artefact. **UX-v3-rounds.md §3.3 records
these 4 pairs and dismisses them** as *"a gene label's bounding box against a
ring caption's … bounding-box adjacency rather than overprinted glyphs, which is
why A1's no-overprint rule still holds."* The instrument's own output names the
second string as a cell-state legend row, not a ring caption, and the pixels say
the glyphs intersect. Round 3's headline — *"0 EXACT overlapping pairs on all
four artefacts"* — is true only of a classifier that cannot see this case.

**Why nothing catches it.** Three checks exist and the gap between them is exact:

* `assert_text_does_not_overprint_text` — `figure.py:3620-3621`,
  `if abs(t.get_rotation() or 0) > 1: continue`. Every rotated string is skipped.
  The docstring (`figure.py:3604-3608`) says so and delegates: *"the thing that
  checks them properly is `assert_no_text_over_the_rings`, which does the real
  geometry."*
* `assert_no_text_over_the_rings` — checks rotated labels against **the rings**.
  The legend box is not a ring.
* `assert_legend_panels_do_not_overlap` — scoped to the legend artefact's own
  canvas by its docstring (`figure.py:3049-3053`: *"On the legend canvas there is
  no circle"*). It is not applied to the slide's spoke labels.

A rotated spoke label over the cell-state legend is checked by none of the three.

**And this is instrument-generated.** Every gene label on a circos must be
rotated — that is what a radial axis is. Rotated text is the one category the
build's overprint check cannot evaluate. The choice of instrument creates a class
of defect and simultaneously removes the ability to detect it. On a layout with
horizontal labels, `assert_text_does_not_overprint_text` would cover every string
on the canvas and this could not have happened.

**Change.** Give the rotated labels real geometry against every block, not only
against the rings — rotate the label's own box and test the oriented rectangles,
which is the same arithmetic `assert_no_text_over_the_rings` already does. Then
move the cell-state legend off the slide's top-left corner, which is inside the
circle's bounding box at n=14.

---

## 3. SHOULD-FIX

### P4 — Radial position assigns 1.97× visual weight by tuple order, and unlike spoke order it is stated nowhere and checked by nothing.

**Evidence.** `figure.py:1363-1376`: `r_outer, r_inner = 97.0, 46.0`,
`band = 51/12 = 4.25`, ring *i* occupies `hi = 97 − 4.25i`, `lo = hi − 3.655`.
A cell's ink is an annular sector, so its area goes as `(r_hi² − r_lo²)`:

| ring | key | `r_lo … r_hi` | area (r² units) |
|---|---|---|---|
| 1 | `logfc` | 93.35 … 97.00 | **695.7** |
| 3 | `tractable` (first external) | 84.85 … 88.50 | 633.6 |
| 11 | `literature` (last external) | 50.85 … 54.50 | 385.0 |
| 12 | `completeness` (k) | 46.60 … 50.25 | **354.0** |

**The outermost ring's cell is 1.97× the ink of the innermost**, and 1.65×
across the nine external evidence rings. Which ring is outermost is the order of
a tuple at `figure.py:367`. The two most prominent rings on every render are the
user's own `logFC` and `−log10 padj` — his input, not the evidence — and
`literature` and `k` get about half their ink. Nothing in the repo argues that
allocation; it is an artefact of declaration order.

**The asymmetry is the finding.** Invariant I7 (`invariants.py:525-531`) holds
the *spoke* order to a hard standard, in its own words:

> *"An order that carries meaning and is not stated is the same defect as an
> unlabelled colour scale; an order that is stated wrongly is worse."*

— and re-derives the drawn order from the CSV so a figure that says
"alphabetical" and draws something else fails. The **ring** order carries a 1.97×
weight the spoke order does not carry at all, and is subject to none of it. The
build's own standard, applied consistently, says radius needs the same treatment
as angle.

**Change.** Either state the ring-order rule on the figure and check it in I7
alongside the spoke rule (cheapest, and it subsumes P1), or equalise the ink —
bands of constant *area* rather than constant radial thickness, which costs a
few lines in the `hi`/`lo` arithmetic and removes the question.

### P5 — Adjacent-ring colour separation got 10× worse on 2026-09-27 with the suite green, because separation is a script and not an assertion.

**Evidence.** Measured on the colours actually painted, from each run's
`values.csv` `fill` column, for the cell pairs that physically touch along a
spoke, under normal / protan / deutan (`targetring.figure.ADJACENT_RING_MIN_DELTA_E`
= 8.0). **Same 14-gene input throughout, so this is controlled:**

| run | touching pairs under ΔE 8 | worst pair |
|---|---|---|
| `20260926T201703Z-a6c60a` … `20260926T202214Z-84d6e9` | **5 of 291** | ΔE 0.90 |
| `20260927T013955Z-92a5fe` | **58 of 291** | ΔE 3.87 |
| `20260927T024407Z-c69510` … `20260927T030021Z-f20afd` (current) | **50 of 291** | ΔE 3.03 |

A palette change on 2026-09-27 took under-target touching boundaries from 1.7% to
17% of all pairs. In fairness it is a trade and not a straight regression: the
*worst* pair improved from ΔE 0.90 (well below a just-noticeable difference) to
ΔE 3.03, and nothing on the current figure is below JND. The floor came up and
the ceiling came down.

**The finding is that nobody knew.** `assert_rings_are_distinguishable` asserts
only hex identity, for the good reason quoted in P2 — the full target cannot be
met at twelve rings. The consequence is that adjacent separation is measured by
`scripts/check_ring_colours.py`, which is a script and not a test, and a palette
edit can move it an order of magnitude without any of the suite's 467 test
functions being able to notice. I did not re-run the suite — this review runs
nothing that writes — so that is a claim about coverage, not a green run.

**Change.** Assert what *can* be met rather than nothing: a hard floor at ΔE 2.0
(a just-noticeable difference) on touching rings under all three vision models —
which the current palette clears and the 2026-09-26 palette did not — plus the
ΔE 8 target recorded as a reported number that a run's manifest carries, so
drift is visible without being a build break.

### P6 — The decode cost is 37 characters of caption per cell, and it is a property of the instrument.

**Evidence.** Run `20260927T030021Z-f20afd`: 12 rings × 14 genes = **168 cells**,
139 of them values. `caption.txt` is **6,312 characters**. The app's decode panel
is twelve entries the reader scrolls (`ux-v3-shots/r3-3-figure-viewport.png`
shows entries 1–3 filling the panel). On the slide the comparability banner is
the largest string on the canvas after the title itself — 12.0 pt in a file whose
title is 17.25 pt (`UX-v3-rounds.md` §3.3).

To answer "is TTR tractable?" a reader must: find TTR's sector; count three bands
inward from the outside; read that colour; find entry 3 in a column up to 20 cm
away or behind a scroll; map the colour to a position on a ramp. Five steps, one
of which is counting at 11.2 px and one of which is a colour judgement against a
non-adjacent reference. Operation 1 of §0 — the commonest thing anyone will do
with this figure — costs that, per cell.

This is not a caption that can be shortened. Twelve incommensurable rings require
twelve scales, and twelve scales cannot be placed next to the cells they decode
when the cells are arcs at twelve different radii. A layout where each metric is
a column can put its scale at the head of its own column, and the caption shrinks
because the artefact is doing the work.

**Change.** Nothing here is fixable inside the instrument; see §4.

---

## 3b. NOTE

### P7 — On the app render the 26° wedge costs 7.2% of every spoke's width and no longer names anything.

The audit render prints the price itself: *"14 spokes at 22.00° against the
23.71° a full circle would give (-7.2%)"*. That is a fair trade on `AUDIT`, where
the gap holds the twelve ring names at their own radii. On `WEB` the names were
removed (`wedge_labels_in_svg=False`, `figure.py:1237`) and the 26° stayed,
because — measured, in UX v3 round 2 item 6 — at `wedge_deg=0` a spoke label
moves to 12 o'clock and the comparability banner lands 20.7 px inside it. So on
the app the gap is now 7.2% of every gene's ink spent on clearance for the
disclaimer. That is a defensible call and it is recorded here only because it is
the same trade P2 and P6 describe, priced: the instrument spends data area on
the apparatus needed to stop it being misread.

### P8 — The 22.5% centre hole is an asset on the export and dead space on the slide.

`r_inner²/r_outer²` = 46²/97² = **22.5%** of the disc. §1.4 argues this is well
spent on the audit render, and it is. On `figure-slide.svg` the hole carries five
short lines — the counts, the not-shown note and the spoke-order rule — in a
region that is 22.5% of the figure's area, while the twelve scales that a reader
actually needs sit outside the circle in a column. The allocation is inverted
relative to what the projected artefact is for. Not raised higher because the
slide has nowhere else to put the counts, and Rule 3 requires them on the
artefact.

---

## 4. Alternatives considered

Costs are grounded in what exists. The reusable part is large: `palette.py` (477
lines), `states.py`, the cell/`norms`/`_scaled`/`assert_within_domain` machinery,
the `cell|<ring>|<index>|<gene>` id scheme that invariants I1/I2/I3/I7 read back
out of the SVG, the CSV writer and the whole adapter layer are **layout-
independent**. Of the 16 `assert_*` functions in `figure.py`, only three are
circle-specific — `assert_wedge_labels_fit`, `assert_centre_fits_the_hole`,
`assert_no_text_over_the_rings`. The real cost in every case below is
`tests/test_presentation.py`: 2,216 lines, 127 of the suite's 467 test functions.

### Alt-A — A gene × ring heatmap grid (rows = genes in the stated order, one column per ring, each column with its own ramp and its own scale strip at the column head)

**Shows better.** Operation 1 becomes a column read with a common baseline and
aligned cells, which is the comparison colour-on-arcs is worst at. Ring identity
becomes a column header in real type, which retires P1, P2 and V18 outright.
Cell area is constant, which retires P4. No rotated text, so
`assert_text_does_not_overprint_text` covers every string on the canvas and P3's
blind spot closes. Each scale sits at the head of the column it decodes, so P6's
caption shrinks and the comparability banner gets *smaller* rather than bigger.
The four states and their hatches carry over unchanged.

**Loses.** (i) §1.2 — rows read as a ranking. Under rule 7 that is a real cost
and it needs the same kind of printed statement the circle does not need.
(ii) §1.1 — it stops being the figure Puck built by hand. (iii) The hole goes, so
the counts, ordering rule and filters need a header block (§1.4). (iv) At the
60-gene cap a 60-row grid is a tall artefact; a circle handles n better, though
`UX-v1-mvp.md` B1 and `figure.py:1341` both already put the circle's own ceiling
at about 60 spokes.

**Cost.** The drawing is close to a transcription of the existing ring loop
(`figure.py:1374-1407`) — same fills, same states, same ids — call it 1–2 days.
The geometry assertions and the 127 presentation tests are the bulk: a week of
sessions at this repo's pace, and it adds a fourth layout rather than retiring
the circos.

### Alt-B — Twelve per-ring dot strips (one horizontal strip per ring; each gene a dot positioned on that ring's own axis; labels only at the extremes)

**Shows better.** Value moves from colour to **position**, the strongest channel,
so the "decode against this ring's own scale" problem disappears entirely — the
axis *is* the scale. COUNCIL V6's collapse cannot happen: nine genes spanning a
41-fold ratio cannot land on one pixel when they are placed by value. `no_data`
becomes an explicit off-axis bin, which is more visible than a hatch, not less.
Each strip carries its own units on its own axis.

**Loses.** Operation 2 — the per-gene profile — goes completely. You cannot trace
one gene across twelve strips without connecting lines, and connecting lines
across incommensurable axes is a parallel-coordinates plot, which draws exactly
the cross-ring comparison the banner forbids. **And a caution I want on the
record:** twelve strips each ordered by value do not smuggle a ranking into the
*artefact* — §3.1 already blesses `ring:<key>` ordering — but they make the
reader's own eyeball-averaging across twelve axes very easy, and COUNCIL M3 and
V10 are about precisely that inference being unsound. It does not violate rule 7;
it lowers the cost of the mistake rule 7 exists to prevent.

**Cost.** Cheapest of the three to draw — twelve strips, no pycirclize, no wedge,
no rotated text — ~2–3 days, plus its own assertion set.

### Alt-C — Keep the circos as the artefact; add a sortable twelve-column values table as the reading surface (recommended)

Accept the split this review's verdict describes. The circos keeps doing what
§1 says it is good at. The reading moves to a table in the app: rows = genes,
twelve columns, cells coloured by the same ramps and carrying the same four
states, sortable per column.

**Shows better.** Everything in §0's three operations. Per-column sort *is*
`ring:<key>` ordering, already permitted and already implemented in `order.py`.
No geometry assertions at all, because the browser does the layout — and UX v3
round 3 already proved the HTML surface holds the floor (*"202 visible HTML text
runs, 0 under the 12.0 px floor"*).

**Loses.** Nothing on the export. The cost is that the app grows a fifth tab and
the figure stops being the only way to read the run — which, given P1, P2 and P6,
is the point.

**Cost.** Lowest by a distance. `/api/run/{id}/scales` exists, `app.js` already
builds a twelve-row scale panel from it, `values.csv` already holds every cell
with its `fill`, `state`, `detail` and `source`, and `_runs` holds it in memory.
This is a day, it asserts nothing the SVG does not already assert, and it does
not touch a settled decision.

### Named and rejected

* **Parallel coordinates over the twelve rings.** Rejected: it draws lines across
  incommensurable axes, which is the cross-ring reading the figure's own banner
  forbids, and a crossing-line profile reads as a composite score. It would
  reintroduce COUNCIL M3's defect as a visual gestalt with no code to delete.
* **Fewer rings.** Six rings would put the band at 22.4 px and fit 13.0 px names
  in place, solving P2 outright. Rejected as a recommendation because choosing
  which six of twelve matter is the weighting judgement rule 7 removed, arriving
  as an editorial decision instead of a slider. If Puck wants it, it is his call
  and not this panel's — but it is the one change that fixes P2 at the root.

---

## 5. What I could not determine, and what would settle it

1. **What Puck's hand-built original actually looked like.** `AGENTS.md:8-9` says
   the app reproduces it; nothing in the repo says how many rings it had, whether
   colleagues decoded it or used it as a cover slide, or whether the twelve came
   from him or from the build. §1.1 is the strongest argument for the instrument
   and I cannot weigh it without this. **Settles it:** the original figure, or
   Puck answering "how many rings, and did anyone ever read a cell off it?"
2. **Whether anyone has ever decoded a cell off the slide render at projection
   size.** `QA-v2-retest.md` §11 records C.9 as NOT-EVALUATED — *"nobody has
   looked at it in a slide"* — and I found nothing later that changes it. Every
   legibility number in this repo, including mine, is arithmetic.
   **Settles it:** one person, one projector, asked to name the ring-7 value for
   one named gene, timed, and asked which band they counted from. That single
   test resolves P1, P2 and P6 together and costs twenty minutes.
3. **Whether the ΔE 0.1 deuteranopia collision is reachable on real data.**
   `check_ring_colours.py` reports it over the whole ramp; on the 291 touching
   pairs actually painted in the current run the worst is ΔE 3.03, above a
   just-noticeable difference. So the ceiling is real and the current figure is
   not sitting on it. **Settles it:** run the touching-pair measurement over
   every run in `runs/` rather than the two I sampled, and record the worst in
   the manifest.
4. **Whether P3's overprint predates UX v3 round 3.** I measured it at round 3's
   own final run and at every run since; I did not walk back further.
   **Settles it:** `ux_v3_overlap.py` over `runs/*/figure-slide.svg`.

---

## 6. What I would do, in order

Cheapest first. None of this changes a settled decision, and none of it
reintroduces a ranking, a score or a weight.

1. **P1 — state the ring-order direction on all three renders and assert it in
   I7.** Four words and a test. It is the only item here that is currently
   capable of producing a plausible wrong figure, and it is the cheapest.
2. **Alt-C — the sortable twelve-column values table in the app.** ~1 day, no new
   invariants, and it is the honest answer to P2 and P6: let the circos be the
   artefact and give the reading a surface that can hold 13 px type.
3. **P3 — real oriented-box geometry for rotated labels against every block, and
   move the cell-state legend out of the slide's top-left.** The slide is
   overprinting now.
4. **P5 — assert a ΔE 2.0 floor on touching rings and put the measured worst pair
   in the manifest.** Under an hour; stops the next palette edit moving it
   silently.

**P4 (constant-area bands) and the six-ring question in §4 are Puck's calls, not
this panel's.** P4 is a real inconsistency against the build's own I7 standard
and I would fix it; reducing the ring count fixes P2 at the root and I am not
recommending it, because choosing which metrics survive is the judgement rule 7
was written to keep out of this tool.
