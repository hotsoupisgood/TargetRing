# targetring v3 — three rounds of usability review

Reviewer: Wren II, 2026-09-25. Local only; nothing deployed, nothing pushed, no
money spent (LLM triage still unbuilt, $5.00 of $5.00 unspent); `~/ytpt`
untouched; the OpenClaw gateway not restarted.

Puck's words: *"have ux panel review. do 3 rounds of review the visual once
finished with current work, fix review fix. consider usability. readability of
text. font size. button placement. legend font size and if there is overlap.
annimations to hide non-neccissary info."*

Three rounds, each **review → fix → re-review**. The re-review of round N is the
review that opens round N+1. Findings use `UX-v1-mvp.md`'s scale: **category A**
is "would embarrass him in front of colleagues", **category B** is minor
friction. Nothing below relaxes an invariant or deletes a test; where a fix would
have required that, it is said so and a different fix was taken.

The dev server on :8811 was **restarted onto `18abf75`** before round 1 and
confirmed with a live request (`GET /` → 200, `/api/regions` → the GTEx panel)
before any screenshot was taken. Several past sessions shipped shots against a
stale server; this one did not.

---

## The rule that holds for all three rounds

Puck asked for animations that hide non-essential information in the app. Since
§v3.0 the app render and the export are allowed to differ, so **simplifying the
screen is now free in a way it was not before**: nothing hidden in the browser
touches `figure.svg`, `figure.png`, `figure-slide.*`, `legend.*` or
`caption.txt`. This review uses that freedom, under one rule stated here and
held unchanged through rounds 2 and 3:

> **Two things survive in the app whatever animates away.**
>
> 1. **The four cell-state legend** — `value` / `no_data` / `not_applicable` /
>    `failed` — stays reachable, with no dead end. If it collapses, the control
>    that reopens it is visible in the collapsed state and says what it opens.
> 2. **"Each ring has its own scale; colours do not compare across rings"** is
>    **never behind a click**. A first-time viewer must meet it without guessing
>    that something expands. Collapsed is not the same as absent, and neither is
>    "present at 6 px in a corner" — see A2 below, which is this rule being
>    broken today by the second clause rather than the first.

Both are checked by an assertion, not by care, from round 1 onward.

### The floors this review measures against

A font size in a file is not a finding; what a reader can read is. Every number
below is the size **as seen**, after the canvas has been fitted to the surface.

| surface | floor | why that number |
|---|---|---|
| the app, on a **1440 × 900 laptop** | **12.0 CSS px** | the app's own stylesheet does not go below 12.5 px for a hint or 11.5 px for a table header. 12 px is this build's own smallest-thing-a-human-reads; below ~9 px the strokes land on one device pixel at 1× |
| an exported render at **presentation size** | **10.0 pt** | at final rendered size on the half-slide test `UX-v1-mvp.md` A4 used. 10 pt is the bottom of what survives a projector from the back of a room — a floor, deliberately not a target |

Instruments, so all three rounds measure the same way and an interruption cannot
lose the method: `scripts/ux_v3_measure.py` (type size as seen, per artefact per
surface), `scripts/ux_v3_overlap.py` (every drawn string's box, pairwise), and
`scripts/ux_v3_shots.py` (the same five frames at 1440 × 900 each round). The
morning shots are taken at 1680 × 1250, which flatters the figure: a 240 px wider
panel is a 240 px wider scale column, and the question is what the column does on
the smaller screen.

---

# Round 1

## 1.1 Review

Driven live at 1440 × 900 against `18abf75`: the example table, the broken
table, a gene drawer, the download menus, and the four exported artefacts
measured at half a slide and a full slide.
Evidence: `docs/reviews/ux-v3-shots/r1-*.png`, `logs/ux-v3-r1-typesizes.txt`,
`logs/ux-v3-r1-overlap.txt`.

### A. Would embarrass him in front of colleagues

#### A1. The slide render prints text on top of other text, in two places
**Cost: highest. This is the artefact that gets projected.**

Five pairs of strings intersect on `figure-slide.svg`, measured from the file
rather than judged by eye (`logs/ux-v3-r1-overlap.txt`, pairs marked `EXACT` —
neither run rotated, so the box is the ink):

| overlap | over | by |
|---|---|---|
| `Lung` (11.8 pt) | `15 genes in, 14 rendered` (10.2 pt) | 28.7 × 5.1 pt |
| `the 1 not shown are named in the caption` (8.0 pt) | `1 not shown` (10.2 pt) | 62.3 × 3.2 pt |
| `rings with a real value, of 9` (5.9 pt) | `the safety ring is NOT evidence of safety…` (10 pt) | 80.8 × 5.4 pt |
| `derived` (5.9 pt) | the same caveat line | 22.4 × 2.5 pt |
| `derived` (5.9 pt) | `ring names are on their own scales…` (10 pt) | 22.4 × 1.4 pt |

`r1-slide-centre-overprints.png` is the centre at 2.2×: the word **Lung is
struck straight through "15 genes in, 14 rendered"**, and the amber
"the 1 not shown are named in the caption" is struck through "1 not shown".
`r1-slide-column-over-caveat.png` is the other one: the last two caption lines
of ring 12's scale sit inside the slide's caveat line.

The cause is one line of arithmetic. `_draw_centre` (`figure.py:1450`) places
its lines at **fixed axes fractions** — 0.575, 0.542, 0.512 — and scales only
the *font sizes* by `lay.centre_scale`, which is **1.15** on the slide. The type
grows 15% and the line spacing does not, so the block collides with itself on
exactly one layout. `fit_centre_to_the_hole` cannot see it: it shrinks fonts
until each line's *radial reach* clears the hole, and two lines occupying the
same y is not a reach failure. `assert_centre_fits_the_hole` measures the same
radius and passes for the same reason.

This is **§v2.6 *Still open* 2**, open and unfixed since 2026-09-24 —
*"'Liver' sits on 'targetring v2'… Say the word and it is ten minutes."* It was
never said, so it shipped through v2.7, v2.8, v2.9 and v3.0. Puck has now asked
about overlap by name, so it is in scope, and "ten minutes" understates it: the
fix has to be a layout that cannot do this again on the next `centre_scale`,
not a nudge to three constants.

**Change:** stack the centre block by measured line height instead of at fixed
fractions, and add an assertion that the centre block's own lines do not
intersect — a *new* check, nothing relaxed. Give the slide's scale column a
bottom that clears the caveat line.

#### A2. On a laptop, 148 of the app figure's 149 strings are under the 12 px floor — including both must-survive items
**Cost: very high, and it is the whole of the animation question.**

`figure-web.svg` is a 19.5 × 12.2 in canvas fitted into a 1388 px panel: scale
0.9886, so a point in the file is a CSS pixel on screen. Measured in the live
DOM and confirmed against the file (`logs/ux-v3-r1-typesizes.txt`):

| what | in the file | on the laptop | count |
|---|---|---|---|
| scale-column tick labels | 5.83 pt | **5.76 px** | 86 |
| run id / git SHA stamp | 6.0 pt | 5.93 px | 1 |
| **the comparability sentence** | 6.21 pt | **6.14 px** | 2 |
| scale-column ring titles and sources | 6.54 pt | 6.47 px | 17 |
| ring names in the wedge | 7.4 pt | 7.32 px | 12 |
| gene labels (`⚠ BRAF (k=9/9)`) | 8.0 pt | 7.91 px | 15 |
| **the four cell states** | 9.0 pt | **8.90 px** | 7 |
| the counts in the hole | 9.5 pt | 9.39 px | 2 |
| `targetring v2` | 15 pt | 14.83 px | 1 |

**One string out of 149 clears the floor, and it is the title.**

Both must-survive items are on that list. The comparability sentence is
`178 × 7 CSS px` of amber in the top-right corner of a 1388 px panel — it is not
behind a click, and it is not *seen* either, which is the same outcome by a
different route. The four cell states are at 8.9 px, and they are rows 1–4 of a
seven-row box whose longest row — `Pipeline: current activity (Open Targets
clinicalReports): stopped for cause — OT category Negative / Safety_Sideeffects`
— makes the box **570 px wide**, so the three trial-activity swatches
typographically dominate the four states that Puck's own rule protects.

The layout is the reason. The app panel divides
**22.5% left margin · 47.5% circle · 23.2% scale column** (from
`WEB.axes_box` and `WEB.scale_box`). Nearly a quarter of the screen is a column
of 5.8 px numbers that nobody can read, and another quarter is a margin holding
one oversized box. The circle — the only thing on the page that works at this
size — gets under half.

Before v3.0 this was unfixable: one artefact served the screen and the export,
and the scale column is a **binding** requirement from `UX-v1-mvp.md` A5 that
both renders carry a scale per ring. It is fixable now, and the fix is not to
delete anything: it is to stop making the browser read the scales as 5.8 px
vector text when the page around them is HTML at 13 px.

**Change:** the app render keeps the circle and drops the scale column and the
seven-row box from the *SVG*; the same twelve scales are drawn as HTML beside
the figure at real UI sizes, in a `<details>` that animates open and closed. The
two must-survive items are **exempt from the collapse** — the four cell states
are always-on in the panel, the comparability sentence is promoted out of the
6 px corner to a full-width line above the figure. The export is untouched: A5
still holds on `figure.svg`, `figure-slide.svg` and `legend.svg`.

#### A3. The legend — the artefact invented so type could be sized independently — is the smallest type in the set, and leaves a fifth of its own canvas blank
**Cost: high.**

§v3.0 built `legend.png` / `legend.svg` so the legend could go on its own slide,
*"sized independently"*. Measured at the size it exists for
(`logs/ux-v3-r1-typesizes.txt`):

| surface | runs under the 10 pt floor | largest body text |
|---|---|---|
| **half a slide** | **139 of 139** | ring names at **3.75 pt** |
| a full slide | 137 of 139 | ring names at 7.50 pt |

The caveat block is **6.40 pt — 2.67 pt at half a slide** — and it is 22
unbroken wrapped lines with no headings and no spacing between subjects
(`r1-legend-full.png`, left column). The comparability sentence sits at 8.55 pt,
**smaller than the 9 pt ring names it qualifies**.

And it is small while empty. Ink analysis of `legend.png`: the bounding box of
all ink stops at row 875 of 990, and **the region below y = 70% and left of
x = 35% contains 0.00% ink** — a contiguous 616 × 297 px block, 18.7% of the
canvas, directly under the 6.4 pt caveat wall (`r1-legend-dead-space.png`).
Overall the canvas is 9.3% ink.

**Overlap, which Puck asked about by name: there is none.** All 139 runs
checked pairwise — **0 intersecting pairs**, and the legend has no rotated text,
so that result is exact rather than a bounding-box approximation. The legend's
problem is type size and wasted space, not collision.
`assert_legend_panels_do_not_overlap` only ever compared text against the
cell-state *box*, never text against text, so this is the first time the
question has actually been answered.

**Change:** spend the dead 18.7%. Three columns of four rather than two of six,
the caveat block broken into headed groups, and every size raised until nothing
on the legend is under 10 pt at half a slide — or, where a string genuinely
cannot make that, say which and why rather than shrink it.

### B. Minor friction

**B1. The drawer is 5,621 px of flat scroll with no navigation. (~2 h)**
Measured on BRAF at 1440 × 900: eight `<h4>` sections, 6.2 viewport-heights,
one scrollbar, no contents, no collapse. It is the largest pile of
non-essential-until-you-want-it information in the app and the animation ask
lands on it as squarely as on the figure. Not round 1's fix — A1–A3 are ahead
of it — but it should not survive three rounds.

**B2. The form is 1,292 px tall, so the figure and the controls are never on
screen together. (minutes)** `scrollIntoView` gets you down there; nothing gets
you back, and changing a filter means scrolling up past four sections of help
text. The four hint paragraphs are excellent and none of them is needed twice.

**B3. `All downloads are run 20260926T020457Z-e02d7c.` sits inline between the
button row and `Click a gene label…`, in the same 12.5 px grey as the hint.**
Two sentences with different jobs — a provenance stamp and an instruction —
reading as one grey run-on.

**B4. The cell-state box mixes two taxonomies.** Four cell states and three
trial-activity categories, seven rows, one frame, no rule between them. The
three categoricals belong to ring 8; the four states belong to every ring.

**B5. The close button on the drawer is 28 × 39 px.** Under a 44 px target. A
desktop-only app, so this is genuinely minor.

**B6. `figure.svg` opened at full width on the same laptop is 159/160 runs under
12 px.** Noted for completeness rather than as a defect: it is the *export*, it
is meant to be read at 19.5 in or zoomed, and `UX-v1-mvp.md` A4 is why it has a
separate slide render at all.

## 1.2 Fix

Applied, in this order. Nothing from round 2 or 3 was anticipated.

1. **A1 — the centre block is stacked, not placed.** `_draw_centre` now measures
   each line and advances by its own height × `centre_scale`, so the block
   cannot collide with itself at any scale factor. `assert_centre_lines_do_not_collide`
   is new and runs on every layout: it is the check that was missing, and it
   fails the render rather than shrinking anything. The slide's scale column
   bottom clears `slide_line` by measurement.
2. **A2 — the app render drops the scale column and the state box from the SVG
   and the page draws them as HTML.** `WEB.draw_key` is off, the axes box takes
   the width back, and `app.js` builds a twelve-row scale panel from a new
   `/api/run/{id}/scales` payload at 13 px. The four cell states are rendered
   always-open above it; the comparability sentence is promoted to a full-width
   banner over the figure. `assert_figure_states_the_minimum` still runs on the
   web layout — the sentence is still *in* the SVG, it is simply no longer the
   only place it appears.
3. **A3 — the legend is four columns of three, and the caveats are three
   balanced columns along the bottom.** Type raised throughout; the dead 18.7%
   is what pays for it.

### What the fix turned out to need beyond the above

Recorded because an interruption should not lose it, and because three of the
four are the same shape: a name or a constant that stopped describing what it
decided, and nothing checking that it still did.

4. **`draw_key` had not meant "draw the scale column" since v2.6.** It decides
   the provenance stamp's font size, and it is `False` on `slide` — which draws
   a full column. Read as the layout question, it took the slide's scales away
   and printed an 11 pt banner across its own spoke labels: thirteen tests red,
   all of them the slide. Split into `stamp_size_small` and a new
   `scales_in_svg`, which is `True` on both exported renders and `False` on
   `web` alone, and defaults `True` so a layout added later carries its own
   decode furniture unless it says otherwise.
5. **The new overprint check then refused the web render.** The comparability
   banner is top-left and the run-id stamp top-right; on a 19.5 in canvas that
   is two corners with a continent between them, and at 9.6 in they met by
   62 × 4 px. `_free_width_beside` measures the free width and wraps the banner
   to it, rather than a constant that today's run id clears and a longer one
   walks through.
6. **The centre's y arithmetic was still in the file, dead.** Both
   `_draw_centre` and `_draw_ordering_and_filters` computed positions that
   `stack_centre_blocks` then overwrote. Left in, the file would have had two
   layout models with the live one invisible, and the next person to adjust the
   centre would have adjusted the other.
7. **Ring 8's three states printed as `-1 / 0 / 1` in the new panel**, read off
   the ring's numeric ticks — three colours that are off every ramp, labelled
   with positions on one. COUNCIL V12 in HTML. Found by looking at the
   screenshot, which is the argument for taking one per round.
8. **The two survive-rules were not actually asserted.** §1's rule says "checked
   by an assertion, not by care, from round 1 onward" and nothing checked them.
   They are twenty-six new tests now, and they ask about *ancestry* in the
   parsed page rather than for the presence of a string, because "is this behind
   a click" is a question a grep answers wrongly.
9. **The measurement instrument was measuring a width the app no longer has.**
   `ux_v3_measure.py` fitted the app render to 1388 px; A2's own fix made it
   1040. Corrected, not rebuilt — see §1.3 A2.

## 1.3 Re-review

Driven live at 1440 × 900 against the restarted server on `e7e5aea`
(`GET /` → 200, `/api/regions` → the GTEx panel, `app.js` served with
`renderDecode` in it before any screenshot). Evidence:
`logs/ux-v3-r1fix-typesizes.txt`, `logs/ux-v3-r1fix-overlap.txt`,
`logs/ux-v3-r1fix-live.json`, `logs/v31-matrix.txt`, `r1-1-*` … `r1-5-*`.

**516 tests pass** (490 at `62d40a3`; 26 new). 7/7 invariants on all seven cold
runs. Every existing assertion unchanged and passing: the seven invariants,
`assert_no_text_over_the_rings`, `assert_centre_fits_the_hole`,
`assert_text_within_canvas`, `assert_wedge_labels_fit`,
`assert_legend_panels_do_not_overlap`, `assert_renders_agree`,
`assert_artefacts_are_their_own_layout`, `assert_figure_states_the_minimum`,
`assert_the_id_rule_is_kept`, `assert_greyscale_disjoint`,
`assert_ramp_luminance_is_monotone`, `assert_rings_are_distinguishable`.
Nothing relaxed, nothing deleted.

### A1 — fixed, and it was not ten minutes

**0 EXACT overlapping pairs on all four artefacts**, against five on
`figure-slide.svg` (`logs/ux-v3-r1fix-overlap.txt`):

| artefact | text runs | EXACT pairs, before → after |
|---|---|---|
| `figure.svg` | 158 | 0 → 0 |
| `figure-web.svg` | 41 | 0 → 0 |
| `figure-slide.svg` | 115 | **5 → 0** |
| `legend.svg` | 160 | 0 → 0 |

The eight `aabb-only` hits remaining on the slide are rotated spoke labels
against the scale column's source lines; a label at 37° has an axis-aligned box
far larger than its ink, which is why the instrument says which kind a hit is,
and `assert_no_text_over_the_rings` does the real geometry on those and passes.

§v2.6 *Still open* 2 closes. The estimate in it was wrong in the way that
matters: the collision was a spacing bug and the *fix* was ten minutes, but the
thing that let it live through four releases was that **no check compared a
drawn string with another drawn string**, and that is what took the time.
`assert_text_does_not_overprint_text` refused two renders during this work —
the web one, once the canvas shrank and the corner banner met the run-id stamp,
and the three-gene audit one, where the footer grows under the scale column.
Neither would have been found by eye at fourteen genes.

### A2 — fixed, with three strings left under the floor on purpose

Measured live at 1440 × 900 and confirmed against the file, which is where the
round-1 numbers came from too:

| | round 1 | now |
|---|---|---|
| figure panel | 1388 px | **1040 px** (the decode panel takes 330 + 18) |
| canvas | 19.5 × 12.2 in | **9.6 × 10.6 in** |
| scale, file pt → CSS px | 0.9886 | **1.5046** |
| SVG strings under 12.0 px | **148 of 149** | **15 of 41** |
| the comparability sentence | 6.14 px, in a corner | **14.5 px** HTML band + 16.55 px on the render |
| the four cell states | 8.90 px, in a 570 px box | **13 px** HTML, always open |
| ring scale ticks | 5.76 px | **12.0 px** |
| ring scale titles | 6.47 px | **13 px** |

The fifteen still under the floor are three strings, and each is on the page
again at or above it — which is the only reason they are acceptable:

* **the twelve wedge ring names at 9.07 px.** Geometry-bound, not chosen: at
  twelve rings the binding budget is the band's *height*, so widening the gap
  buys nothing and `assert_wedge_labels_fit` refuses it — v2.7's finding,
  arriving on a third layout. All twelve are numbered and named at 13 px in the
  panel, and the SVG keeps its v2.7.1 tooltips.
* **the spoke-order note at 10.23 px.** The counts line above the figure says
  it at 14 px.
* **`the 1 not shown are named in the Drop ledger tab` at 11.28 px.** The tab
  itself is 14 px and two inches away.

**A correction to round 1's own instrument, which flattered this result before
it was caught.** `ux_v3_measure.py` fitted `figure-web.svg` to 1388 px, the
width the figure had when it owned the whole column. It does not any more. Read
at 1388 the script said *0 of 41 under the floor*; the live DOM says 1040 px and
*15 of 41*. The constant is corrected and split in two, because only one of the
two measurements moved — the export opened full width still gets 1388. An
instrument that reports the app 33% more legible than it is, is wrong in the one
direction this review cannot afford.

**Both must-survive items hold, and by assertion.**
`TestUXv3TheTwoThingsThatSurviveWhateverAnimatesAway` parses the page and asks
about *ancestry*, not about the presence of a string: neither `#comparability`
nor `#cell-states` is inside a `<details>`, the sentence precedes
`.figure-row` in document order, and the one panel that may animate names its
contents and its count in the closed state. Verified live as well as
statically: with `#scales` collapsed, both survivors are still on screen and
the reopen control is visible and reads *"The 12 ring scales — what a colour
means, ring by ring"*.

### A3 — improved by a long way, and the 10 pt floor is **not met at half a slide**

This is the one finding whose stated target was not reached, and it was not
reachable. Round 1 wrote *"every size raised until nothing on the legend is
under 10 pt at half a slide — or, where a string genuinely cannot make that,
say which and why rather than shrink it."* This is that clause being used.

| | v3.0 | now |
|---|---|---|
| ring names in the file | 9.00 pt | **11.04 pt** |
| the comparability sentence | 8.55 pt, under the names it qualifies | **15.00 pt, leading** |
| the caveat block | 6.40 pt, 22 unbroken lines | **9.40 pt, three balanced columns** |
| ink on the canvas | 9.3% | **11.48%** |
| the 616 × 297 px dead block | **0.00% ink** | **9.11% ink** |
| ink bounding box, bottom | row 875 of 990 | **row 977 of 990** |
| clearing 10 pt at a full slide | 2 of 139 | **5 of 160** |
| clearing 10 pt at half a slide | 0 of 139 | **0 of 160** |

Why no string can make it: half a slide is 6.67 in wide, so a 16 in canvas is
scaled 0.4167 and **10.0 pt as seen requires 24.0 pt in the file**. The legend
carries twelve scales with their own ramps, ticks, units and source lines plus
the cell states and the whole caveat block; at 24 pt the ring titles alone would
not fit on 16 × 9, never mind the rest. The arithmetic says the half-slide floor
and a twelve-scale legend on one 16:9 canvas are incompatible, and no amount of
whitespace-spending changes it — the space was spent, and it bought 2.4 pt.

So the honest statement, and it goes on the artefact rather than only here:
**the legend is a full-slide artefact.** At a full slide its largest four
strings clear the floor and its ring names read at 9.20 pt, against 7.50 pt in
v3.0 and 3.75 pt at the half-slide size round 1 measured. Whether that is
acceptable or whether the legend should become *two* slides is Puck's call and
it is in §v3.1's *What you need to decide*. Round 2 does not get to quietly
shrink something to close it.

### The B items, and what round 1 did to them incidentally

Round 1 fixed **B4** as a side effect of A2 rather than by intent — the
cell-state box no longer mixes two taxonomies, because the panel draws the four
states under one heading and ring 8's three under their own, separated by a
rule. It also **introduced and then fixed a defect of its own inside B4's
territory**: the panel's first version printed ring 8's three states as
`-1 / 0 / 1`, read off the ring's numeric tick labels, beside three colours that
are not positions on any ramp. That is COUNCIL V12 exactly, arriving in HTML;
`scale_rows` carries the names now, off the same sort as the colours so a colour
cannot be paired with the wrong state.

**B1, B2, B3, B5 and B6 are untouched, deliberately.** They are round 2's and
round 3's, and mixing them in here would remove the only thing that makes three
rounds a check on each other.

### What this re-review found that round 1 did not, and hands to round 2

Two things, and the first is the more serious. Both are recorded here as the
opening of round 2 rather than fixed now.

1. **The figure no longer fits in one laptop viewport.** A2 traded canvas width
   for type size and the canvas is now taller than wide — 9.6 × 10.6 in at
   1040 px is 1149 px tall against a 900 px viewport, so
   `r1-3-figure-viewport.png` shows the top two thirds of the circle and the
   bottom third is below the fold. Round 1 measured what a reader *can read* and
   did not measure what a reader *can see at once*. This is A2's fix producing
   a new instance of the same class of problem, which is the best possible
   argument for the rounds existing.
2. **The comparability sentence is now on screen twice, ten millimetres apart** —
   once in the amber HTML band and once at 16.55 px in the render's top-left
   corner. Both were added by the same fix, for the same rule, from opposite
   directions: the band because the page must state it, the corner because
   `assert_figure_states_the_minimum` will not let the circle be drawn without
   it. Neither is wrong; together they are a reader wondering which one is the
   real one, and the UX-v1 review's own noise list says the counts appearing
   three times was a defect for the same reason.

---

# Round 2

## 2.1 Review

Round 1 measured the artefacts. This round measures **the page a reader
actually works in** — where things are, how far apart, and how much of it can
be seen at once — because that is where round 1's re-review left two findings
and where three of Puck's six words (*usability*, *button placement*,
*animations*) live.

Driven live at 1440 × 900 against the restarted server on `e1ca4cf`; the same
council list, the same fixture, the same instruments.
Evidence: `logs/ux-v3-r2-probe.json`, `logs/ux-v3-r2-repeats.json`,
`logs/ux-v3-r1fix-typesizes.txt`, `docs/reviews/ux-v3-shots/r1-*`.

The page, measured (document coordinates, 1440 × 900 viewport):

| | y | height |
|---|---|---|
| the input form | 0 | **1,053 px** |
| `#counts` | 1,053 | 26 |
| the tab row | 1,095 | 40 |
| the download row | 1,159 | 39 |
| the amber comparability band | 1,210 | 61 |
| **the figure** | **1,279** | **1,148 px** |
| the decode panel | 1,279 | 1,016 |
| the whole document | | **2,487 px** |

### A. Would embarrass him in front of colleagues

#### A4. The figure no longer fits on the screen, and round 1's fix is the reason
**Cost: high. It is the first thing anyone does with this page.**

The app render is **1,148 px tall in a 900 px viewport**. There is no scroll
position at which the circle is whole: it is 248 px taller than the window, and
it begins at y = 1,279 on a 2,487 px page, so a reader arrives at the top of a
figure whose bottom third — four spoke labels and the caveat line — is below the
fold. `r1-3-figure-viewport.png` is the evidence: `#counts` scrolled to the top
of the window and the circle cut through `GFAP` and `SERPINA1`.

**Round 1 did this, and it did it while fixing A2.** The canvas went
19.5 × 12.2 in to 9.6 × 10.6 in to buy type size — a 0.626 aspect became 0.906
— and at a fixed panel width an aspect change is a height change:

| | canvas | panel | rendered height | fits 900 px? |
|---|---|---|---|---|
| before round 1 | 19.5 × 12.2 in | 1388 px | **869 px** | yes |
| after round 1 | 9.6 × 10.6 in | 1040 px | **1,148 px** | no |

The type size is a function of *width* only — `scale = 1040 / 691.2 = 1.5046`
— so the canvas can lose height without costing a single point of legibility.
What height costs is the circle: the axes box is width-bound at 7.30 in against
7.42 in of height today, and shortening the canvas makes it height-bound.

The arithmetic says this is nearly free. At 8.2 in of canvas the figure is
**888 px** — one viewport — and the circle is about **648 px** across against
**659 px before round 1** and 790 px now. So: a circle the size it was in v3.0,
with type 1.5× the size it was, whole on the screen. That is not a trade, it is
a strictly better point that round 1 walked past because it was measuring
strings rather than the window.

**Change:** shorten the `web` canvas until the figure fits one 1440 × 900
viewport, and let the existing geometry assertions arbitrate the margins.

#### A5. The app calls itself v1. The figure inside it calls itself v2.
**Cost: high for what it costs to fix, which is two words.**

`index.html:5` is `<title>targetring v1</title>` and `index.html:10` is
`<h1>targetring <span class="ver">v1</span></h1>`. Every artefact this build
draws says `targetring v2` — it is a named constant checked by
`assert_figure_states_the_minimum` — so on `r1-2-result-full.png` the page
header reads **v1** and the middle of the circle 1,400 px below it reads **v2**,
in the same screenshot.

This is COUNCIL V5's defect in the chrome rather than on the figure: an artefact
making a false claim about itself. It is also exactly the kind of thing that
gets noticed by the one person in the room who was not going to read the
caveats. Nothing in the repo asserts the two agree, which is why it survived
v2.1 through v3.1.

**Change:** one named version string, read by the page and by the render, and an
assertion that the page and the figure cannot disagree about it.

#### A6. The comparability sentence is now printed twice, eight pixels apart
**Cost: medium-high, and round 1 caused it.**

Round 1's A2 promoted the sentence out of a 6.14 px corner **and** added a
full-width amber HTML band. Both landed. The result on screen
(`logs/ux-v3-r2-repeats.json`, and plainly visible in `r1-2-result-full.png`):

| | where | size | first line |
|---|---|---|---|
| 1 | the HTML band, `#comparability` | 14.5 px | `EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS` |
| 2 | the render's top-left corner | 16.55 px | `EACH RING HAS ITS OWN SCALE AND ITS OWN COLOURS` |

Two amber blocks, both bold, stacked with 8 px of gap, whose first lines are the
same 47 characters. `UX-v1-mvp.md`'s own noise list condemned exactly this shape
— *"the counts appear three times… on screen all three are visible at once"* —
and a reader who sees a claim twice does not read it twice, they wonder which
one is the real one.

Only one of the two fixes was needed. The round-1 objection to the in-SVG copy
was *"178 × 7 CSS px of amber in the top-right corner — not behind a click, and
not seen either"*, and that was an objection to its **size**, which the canvas
change answered by itself: it is 16.55 px now, above the 12.0 px floor, in the
top-left, above the circle, encountered before any cell. The HTML band is the
redundant one.

**Change:** drop the HTML band and let the render's own banner be the sentence
the reader meets. Move the band's extra clause — *"nine of the twelve ranges
move with the gene set you submitted"* — to the decode panel, where the ranges
are. **Rule 2 is tightened rather than loosened**: the assertion stops asking
whether an HTML element exists and starts asking whether the sentence is drawn
**above the 12.0 px floor at the width the app actually gives the figure**,
which is what the rule was about and what the HTML test could not see.

### B. Minor friction

**B7. The controls are 1,053 px above the result, so the figure and the filters
are never on screen together.** Round 1's B2, now measured against the whole
page: the form is 42% of a 2,487 px document, four numbered sections with a
paragraph of help each, and changing one threshold means scrolling up past all
of it and back down 1,279 px. Every word of the help text is good and none of it
is needed twice. This is *button placement*, which Puck named, and it is also
the second-largest pile of information that is non-essential once you have a
figure. **Round 2's fix takes it**, because it is inseparable from A4: the
figure fitting the window is worth much less if the window has to be 1,279 px
down the page to find it.

**B8. The drawer is 5,621 px of flat scroll — 6.25 viewports — with nine
headings, four tables and zero collapsibles, and its close button is 28 × 39 px.**
Round 1's B1 and B5, measured on BRAF at 1440 × 900. It is the largest pile of
hide-until-you-want-it information in the app and the animation ask lands on it
harder than on anything else. Round 1 said it should not survive three rounds.
**Not round 2's** — A4, A5, A6 and B7 are the page, and the drawer is its own
surface. It is round 3's, named here so round 3 inherits it formally.

**B9. Twenty-five HTML strings on the page are under the 12.0 px floor**, and
they are all in the two tabs a reader goes to when something looks wrong: ten
`<th>` and fifteen status pills at 11.5 px, including the `PASS` pills in the
Invariants tab and the `not shown` pill in the Drop ledger. Pre-existing — the
floor's own justification cites 11.5 px table headers as this build's smallest
— but the floor is the floor, and the drop ledger is *"the best thing in this
app"* per `UX-v1-mvp.md`. Round 3's.

**B10. The web render states the spoke-ordering rule twice on one canvas**:
at 10.23 px in the hole (wrapped onto two lines) and again at 15.05 px in the
footer 40 px below. Two of round 1's three deliberate under-floor strings are
this one duplicate, so removing the small copy removes the exception rather
than arguing for it. Round 3's.

**B11. The slide render misses the 10 pt floor at a full slide by 0.42 pt.**
11.5 pt in a 16 in canvas on a 13.33 in slide is 9.58 pt, so 114 of its 115
strings are under — every one of them by a hair, and it is the artefact UX A4
created to solve this. Different finding from A1, which was about collision.
Round 3's.

## 2.2 Fix

Applied in this order. Nothing from round 3 was anticipated; B8–B11 were left
alone on purpose.
