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
3. **A3 — the legend is three columns of four, and the caveats are headed
   groups.** Type raised throughout; the dead 18.7% is what pays for it.

## 1.3 Re-review

Below, after the work.

---
