# targetring v1 — usability review

Reviewer: Wren III (UX), 2026-09-24. Local only; nothing deployed, nothing
pushed, no money spent. Ran the app at `127.0.0.1:8811` against `PLAN.md` and
`docs/V1-REPORT.md`, submitted the realistic and broken fixtures and two of my
own gene lists, measured the exported SVGs directly, and tested the figure at
slide sizes, in greyscale, and under colour-vision simulation.

My own screenshots are in `docs/reviews/ux-shots/`, referenced by path below.

**The short version.** The engineering underneath this is unusually honest —
the drop ledger and the input-rejection messages are the best I have reviewed
in a tool this young, and the contrast is clean throughout. The problems are
almost all at the boundary where the figure leaves the app: it is built for
reading on a 27-inch monitor and it is going into slides. Six things would cost
you in front of colleagues. None of them is deep.

---

## A. Would embarrass him in front of colleagues

### A1. A third-party outage stamps "DO NOT TRUST THIS FIGURE" across the exported figure
**Cost: highest. This is live right now.**

Europe PMC sets no ring value — `PLAN.md` §2 and COUNCIL M6 both say its count
is drawer context only, and `V1-REPORT.md` §2 says it "buys nothing for the
figure". But when EBI is down, invariant I5 returns `NOT_EVALUATED`,
`figure.py:292 _draw_banner` fires, and a full-width red bar reading
**INVARIANT FAILURE — DO NOT TRUST THIS FIGURE** is drawn straight across the
middle of the rings — *in the SVG*, not just in the browser chrome.

I reproduced it on the first uncached list I submitted (`ux-shots/ux-05-banner-on-figure.png`,
run `20260924T064644Z-b7d7ca`). `grep -c 'DO NOT TRUST' figure.svg` → 1. It
obscures the data, it carries a raw truncated URL
(`…europepmc/webservices/rest/search?query=%22S`), and it cannot be removed
without editing the file.

The self-contradiction is the worst part: directly beneath the red bar the app
prints its own amber note, *"Europe PMC unreachable for all 18 genes… The
PubTator ring is unaffected."* The loudest possible alarm, for a condition the
app itself says changes nothing on the figure. It also fires
non-deterministically — the same broken fixture passed I5 later in my session
off cached responses (`ux-shots/ux-12-invariants.png`). Cry wolf twice and the
banner stops working the day it is real.

**Change:** banner on `FAIL` only, never on `NOT_EVALUATED`; and never banner at
all for a source that drives no ring — demote it to a neutral "sources
unavailable this run" line under the footer. Keep the loud banner for the
invariants that mean the *data* is wrong (I1, I3, I4, I6).
**Size: ~1 hour.**

### A2. While a run is in flight, the previous run's figure stays on screen with live download buttons
**Cost: very high.**

`app.js:41 analyse()` sets one status string and disables the button. It does
not clear `#result`. So for the whole of a cold run the page shows a complete,
plausible, finished-looking figure — of the *previous* gene set — with working
**Download SVG** and **Download CSV**.

`ux-shots/ux-03-inflight.png` is the proof: the status reads "Checking source
contracts, resolving identifiers, fetching rings…" while below it sits
"15 genes in · 14 rendered · 1 not shown · Pareto set: EGFR, KCNQ1, MARCHF1"
and the rendered rings — all from the run before. The list actually being
processed was 20 different genes.

At 4 s/gene that window is four minutes for 50 genes. Downloading or
screenshotting during it gives you the wrong figure with no indication anything
is wrong.

**Change:** `$("#result").hidden = true` on submit, plus a visible working
state. The hide alone removes the whole risk.
**Size: minutes for the fix; ~2 hours if you also want real progress (see B2).**

### A3. Nothing in the export names the genes that were dropped
**Cost: high.**

On the broken fixture: 16 in, **7 rendered**, 9 gone
(`ux-shots/ux-08-broken-result.png`). The figure centre says only
`not shown: 2 ambiguous` — counts by category, no names. I checked
`values.csv`: it contains rendered cells only, no dropped gene appears in it.
The names exist in exactly one place, `manifest.json`.

So the slide cannot answer "where's TP53?" — and neither can the CSV you'd send
alongside it. More than half the input vanished and the artefact you present is
silent about which half.

**Change:** list the dropped gene names on the figure (there is room — see A4),
and add them to `values.csv` with their ledger status, or add a "Download drop
ledger (CSV)" button next to the other three.
**Size: ~1 hour.**

### A4. Every text element in the figure is 3–5 pt at full-slide size
**Cost: high. This is the "paste into slides" answer.**

The exported canvas is ~18.3 × 15.2 in. Fitted to the 7.5 in height of a 16:9
slide that is a 0.49 scale factor:

| element | in the file | on a full slide | on half a slide |
|---|---|---|---|
| gene labels (`◆ BRAF (k=9)`) | 8 pt | 3.9 pt | ~2 pt |
| footer caveats | 7.5 pt | 3.7 pt | ~1.9 pt |
| monospace ring key | 6.6 pt | 3.3 pt | ~1.7 pt |
| centre counts | 9.5 pt | 4.7 pt | ~2.4 pt |
| `targetring v1` | 15 pt | 7.4 pt | ~3.8 pt |

`ux-shots/ux-06-slide-tests.png` (panels A and B) and
`ux-shots/ux-07-text-at-slide-scale.png` show it. At full slide the only
near-readable text is "targetring v1" and "Lung" — the two least useful strings
on the figure. **Does it survive being shrunk to half a slide? No — nothing but
the colour pattern survives, and the colour pattern alone is not interpretable
(see A5).**

Compounding it: at 1.20:1 on a 1.78:1 slide the figure fills only the middle
~60% of the width. The wasted columns are exactly where the ring key currently
lives.

**Change:** a separate "presentation" render — ring key and footer moved out to
a caption or a second page, gene labels at 11 pt+, canvas at 16:9. The full
audit stamps stay on the current render, which is the right artefact for the
morning review and a bad one for a slide.
**Size: ~half a day.**

### A5. No colour bar, and no, a reader would not realise the rings aren't comparable
**Cost: high. This is the one that gets a question you can't answer.**

`figure.py:246 _draw_legend` emits the three state patches plus one swatch
labelled `value (per-ring scale, viridis)`. **There is no colour bar anywhere
on the figure.** Nothing tells a reader whether purple or yellow is the high
end of a heatmap.

On comparability: the information is technically present — the ring key lists
`3. Druggability — [0, 1]`, `9. Literature volume — [1.81, 5.62]` and so on —
but it is 6.6 pt monospace in the left margin, which is 3.3 pt on a slide. The
only other cue is the four-word parenthetical "per-ring scale".

A reader will not get there, and the layout actively works against them.
Because `_norm_for` min–max normalises within each ring, **every ring always
contains a full-saturation yellow and a full purple**, whatever the underlying
values are. The natural reading of a Circos plot is radial — down a spoke — and
that reading is invalid here. A gene that is brightest on literature volume
looks exactly as good as a gene that is brightest on druggability.

**Change:** a small vertical colour bar labelled "low → high, **rescaled within
each ring**", and move each ring's `[min, max]` onto the ring's own label rather
than only into the side key.
**Size: ~2 hours.**

### A6. Text collides with data inside the export
**Cost: medium-high.**

Two collisions, both measured in the SVG itself, not just on screen:

- **Ring key over the rings.** The monospace key starts at x=36; its longest
  line (`OT prioritisation: hasLigand, hasPocket, hasSmallMoleculeBinder,
  hasHighQualityChemicalProbes`) runs to x≈429. The axes box starts at x=323
  and the circle's left edge at that height is x≈262. The key overprints the
  coloured arcs. Visible in `ux-shots/ux-07-text-at-slide-scale.png`, crop 3.
- **Footer over the bottom spoke labels.** The footer block occupies
  y=983.3–1028.3. The `CDKN2A (k=8) *` label sits at y=995.4 and `INS (k=8) *`
  at y=998.6 — inside it. Visible in crop 1 of the same file and in
  `ux-shots/ux-05-banner-on-figure.png`.

**Change:** measure the key and footer blocks and shrink `ax.set_position`
(`figure.py:211`) to clear them, or move the key into the caption per A4.
**Size: ~1 hour.**

#### On the footer-clipping question you asked specifically

**The footer is not clipped in the SVG export.** I measured it on both a clean
run (`20260924T062610Z-db5d79`) and a banner-bearing one
(`20260924T064644Z-b7d7ca`): the lowest footer baseline is y=1028.3 against a
viewBox height of 1100.9 — **73 px of clear space below it**. There are no
`clipPath` elements in the file at all.

What reads as clipping on screen is two other things: the footer overprinting
the bottom spoke labels (A6), and the browser viewport cutting the page in a
screenshot. One thing genuinely *is* truncated, but earlier than rendering —
the banner's failure string is cut at ~200 characters and ends mid-URL at
`?query=%22S`, so the mangled text is in the source before it is ever drawn.

---

## B. Minor friction

**B1. 181+ genes: a long wait, then a raw traceback. (minutes to fix)**
`MAX_GENES = 500` (`config.py:47`), but `Circos({...}, space=2)` at
`figure.py:142` raises `ValueError: Too large sector space size is set!!` for
n ≥ 181 — 181 sectors × 2° of spacing exceeds 360°. Verified directly: n=120
constructs, n=181 raises. `app.py:99` catches it as a generic `unexpected` 500
and `app.js:73 fail()` prints the raw message in red. So a 200-gene table costs
~13 minutes of fetching and then a pycirclize traceback. Long before that it is
unreadable anyway — at n=120 each spoke is 8 px wide at the outer edge.
*Change:* set `MAX_GENES` to ~60, say so in the input hint, and validate the
count **before** any network work.

**B2. No progress, no elapsed time, no cancel. (~2 hours)**
Measured live: 20 genes cold = **80.3 s** (adapters 73.6 s), ~4 s/gene,
consistent with the report's 251 s for 50. Warm 15 genes = 2.7–3.1 s. During
the slow case the user gets one static grey sentence that never changes, a
greyed button, and nothing else. `run_analysis` is awaited inline in
`app.py:82` — there is no task handle and no cancel endpoint. At four minutes
with no ticking counter the honest reading is "it has hung". An elapsed-seconds
counter and "resolved 12/50…" is most of the value; a cancel button is the rest.

**B3. "9 not shown" looks like a link and is inert. (minutes)**
Verified: `<span class="drop">`, computed cursor `auto`, no handler. It is
orange, it is the one place the user asks "why?", and it does nothing. Make it
a button that switches to the Drop ledger tab.

**B4. The page doesn't scroll to the result. (minutes)** After a run the figure
is below the fold and the scroll position doesn't move.

**B5. The literature section has no link to a single paper. (minutes)**
`ux-shots/ux-10-drawer-literature.png`: PubTator3 71,379 · Europe PMC 101,223 ·
the query strings · a by-year breakdown — and no way to read anything. The drug
table immediately above links out to DailyMed, ClinicalTrials.gov and TTD. Of
the two journeys you named, "get to a gene's drugs" works well and "get to a
gene's papers" dead-ends at a number. Link the counts to the result pages.

**B6. ChEMBL rows show `CHEMBL2105729`, not drug names. (~1 hour)** Jarring next
to the pipeline table's SORAFENIB / VEMURAFENIB. Needs `pref_name` from the API.

**B7. The drawer dies on server restart. (~2 hours)** `_runs` is an in-memory
dict (`app.py:24`); after a restart `/api/run/{id}/gene/{g}` 404s with
"re-submit the table" — i.e. re-pay the cold run. The SVG and CSV on disk
survive but are no longer clickable. Rehydrate from `manifest.json`.

**B8. `figure.png` is generated and served, with no button for it. (minutes —
best value in this list)** `/api/run/{id}/figure.png` returns 200, 591 KB,
~2000 px wide. It is written on every run and exposed in `app.py:155`, but the
UI only offers SVG/CSV/manifest. For PowerPoint a PNG is often more predictable
than a matplotlib SVG. One `<a>` tag.

**B9. Accepted column aliases aren't shown until you fail. (minutes)** The
rejection message is genuinely excellent — it names what it saw, what it
accepts, and that "positional guessing is not attempted"
(`ux-shots/ux-14-input-rejected.png`). But the up-front hint says only "gene
symbol, logFC, adjusted p". Put the alias list there. The aliases themselves
are well chosen: DESeq2 (`log2FoldChange`/`padj`), edgeR (`logFC`/`FDR`) and
limma (`logFC`/`adj.P.Val`) all parse as-is.

**B10. Nothing says the spokes are in rank order. (minutes)**
`rank.py:219 order_for_figure` sorts Pareto members first, then by median
available rank. The footer explains ◆ and `*` but never says that *position*
carries meaning, or where the circle starts. A stranger assumes input order.
One footer line fixes it.

**B11. The region choice is unexplained. (minutes)** Nothing says it affects
exactly one ring.

**B12. `runs/` grows unboundedly** at ~1.2 MB per run (SVG + PNG). 20 runs
already from one evening's testing.

---

## Can a stranger use it without being told how?

Mostly yes, and further than I expected. Submitting a table, getting a figure,
and reaching a gene's drugs all work without instruction. The three-step
numbered layout is clear and the click target on a gene label is a comfortable
33 × 56 px.

**Every place I had to guess:**

1. How long it would take, and whether it had hung (B2).
2. Whether the results on screen belonged to the run I had just started (A2).
3. Which end of the colour scale is high (A5) — never resolved; there is no
   colour bar.
4. Whether I could compare a colour on one ring with the same colour on another
   (A5) — the answer is no, and I only established it by reading `figure.py`.
5. Whether spoke position meant anything (B10).
6. Why "9 not shown" wasn't clickable (B3).
7. What "k=8" meant — it is defined in the footer at 7.5 pt and in the ring key
   as ring 10, but it appears first and most prominently in every spoke label.
8. What the region selector changed (B11).
9. Where the papers were (B5) — never resolved.

### What earns its place on the figure, and what is noise

**Earns it:**
- The four-state legend. Precise wording — "no data — never measured here" vs
  "not applicable to this gene" vs "call FAILED — not the same as absent" is
  the distinction most tools collapse, and it is worth the space.
- `ranking is provisional and not validated`, in the centre, italic. The single
  most important string on the figure.
- `k=` in each spoke label. It is the missingness signal and it belongs where
  the eye already is.
- The run id / git SHA stamp. Makes a screenshot auditable, costs one line.

**Noise, or in the wrong place:**
- **The counts appear three times** — the page header, the figure centre, and
  the footer. On the exported figure the centre copy is the only one that
  travels; on screen all three are visible at once. Keep the centre, drop the
  rest from the figure.
- **The monospace ring key is doing two jobs badly.** The numbered ring order
  is essential; the API field lists (`hasLigand, hasPocket,
  hasSmallMoleculeBinder, hasHighQualityChemicalProbes`) are provenance, not
  reading aid, and they are what makes the block wide enough to collide with
  the rings (A6). Move the field lists to the manifest and the caption; keep
  the numbered ring names and their ranges, set in the same sans as everything
  else rather than monospace.
- **The six-line footer is a caveats document, not a figure element.** Every
  line is true and most are important, but at 3.7 pt on a slide none of them is
  read by anyone. One line — "ranking is provisional; rings are scaled
  independently; see caption" — does the honest work. The rest belongs in the
  caption you paste under the figure.
- The weight list (`clinical_phase=1, druggability=1, literature=0.5…`) is the
  longest string on the figure and describes a score the figure explicitly does
  not use as its headline.

---

## Editability

**Possible in the UI today: none of the five.** All of them are Python edits
plus a full re-run:

| you want to change | where it lives |
|---|---|
| which rings show | `RING_SPECS` tuple, `figure.py:63` |
| ring order | the order of that same tuple |
| gene order | `order_for_figure`, `rank.py:219` |
| colour scale | `VALUE_CMAP`, `figure.py:36` |
| the title | hardcoded `"targetring v1"`, `figure.py:233` |

Two notes. `assert_fills_disjoint()` will correctly refuse a colormap that
collides with the missing-data fill, which is a good guard on the one edit most
likely to break an invariant. And `render()` does take a `subtitle` argument —
but it is inserted into the *footer notes* (`figure.py:283`), not the title, and
nothing in `run.py` or the API ever passes it.

**Possible in Illustrator: more than you'd expect.** The export is genuinely
editable:

- Text is real `<text>`, not outlines — 52 text elements, zero `<use>` glyph
  references. Retitling and deleting the footer are 30-second jobs.
- Every cell is its own group, named `cell|<ring>|<index>|<gene>` — 140 of them
  for a 14-gene figure, and those names show up in the Layers panel.
- No `clipPath` anywhere, so nothing is hidden or cropped.

Three caveats. **First**, the real-text behaviour is incidental: matplotlib's
own default is `svg.fonttype: 'path'`, and it is *pycirclize* that flips it to
`'none'` on import. Nothing in this repo asserts it. A dependency bump could
silently turn all your text into outlines. Pin it explicitly in `figure.py`.
**Second**, the groups are flat — 140 siblings, no grouping by ring or by gene —
so hiding one ring means hand-selecting 14 scattered groups. **Third**, the font
is DejaVu Sans, which ships *inside* matplotlib
(`.venv/…/mpl-data/fonts/ttf/`) and is not installed system-wide. Illustrator
will show a missing-font dialog and substitute down the fallback chain to
Lucida Grande. The layout was computed with DejaVu metrics, so the A6
collisions will move rather than disappear.

### The three changes that would most reduce Illustrator fiddling

1. **Wrap each ring's cells in one `<g id="ring-<key>">`, and give the spoke
   labels ids.** Turns "hide the literature ring" from fourteen clicks into one,
   and "restyle all gene labels" into a single selection. **~1 hour**, entirely
   inside `figure.py`.
2. **A title/subtitle field in the UI, passed through to `_draw_centre`.** The
   plumbing is half-built already. **~1 hour.**
3. **Checkboxes for which rings to draw, re-rendering from the cached run.** The
   data is already in `_runs`; this needs no network and no re-fetch, so it is
   instant rather than another cold run. It removes most of the reason to open
   Illustrator at all. **~half a day.**

---

## Speed as experienced

Measured live, not quoted: **20 genes cold = 80.3 s** (adapters 73.6 s), ~4 s
per gene, consistent with the report's 251 s for 50. Fully warm, 15 genes =
2.7–3.1 s.

What the user sees for those four minutes: one grey 13 px sentence that never
changes, a greyed-out button, and — below it — the previous run's complete
results looking finished (A2). No elapsed time. No per-gene or per-stage
progress. No estimate. No cancel. Nothing that distinguishes "working" from
"hung", and one strong positive cue that it has already finished.

This is squarely the slow page that does not explain itself. The fix is cheap
and it is mostly B2 plus A2: clear the stale result, tick a counter, name the
stage. A four-minute wait that says "resolving identifiers 12/50 · 48 s elapsed"
is a tolerable product. The same four minutes in silence is a broken one.

---

## Failure states

**The drop ledger is the best thing in this app** (`ux-shots/ux-11-drop-ledger.png`).
Every row names the input symbol, the status, **whether it is on the figure**,
the resolved symbol and Ensembl id, the source row number, and a reason written
for a human — "Excel converted this symbol to a date. Unrecoverable from the
value alone — re-export the source file with the symbol column formatted as
Text." It correctly separates *rendered but flagged* (`renamed`,
`possible_mouse`) from *absent*, and the `Trp53` row explains why it refused to
orthologue-map rather than doing it silently. Readable: unreservedly yes.

**Findable: no.** Nothing pushes you there, and the one affordance that looks
like it would — "9 not shown" — is inert (B3). Worse, **16 in / 7 rendered
raises no banner at all.** More than half the input disappeared and the only
signal is small orange text the user has no reason to click.

**Whole-file rejection is excellent** (`ux-shots/ux-14-input-rejected.png`):
red, specific, names the headers it saw and the ones it accepts, and states
that it will not guess positionally.

**When the invariant banner fires, does the user know what to do? No.** It
names the invariant and prints a truncated raw URL. It does not say what is
affected, what is unaffected, or what action to take — and the amber note
directly beneath it contradicts the red bar above (A1).

**Would they know whether to trust the figure? No, and in the wrong direction
twice.** The one case where the figure was fine (an upstream literature outage
that touches no ring) screamed DO NOT TRUST. The one case where over half the
data was missing said nothing at all.

---

## Accessibility

**Contrast: clean. Nothing to fix.** I computed WCAG ratios for 14 foreground/
background pairs across the UI and the figure. All pass AA, most comfortably:
body text 17.2:1, muted hint text 5.8:1, the four pill styles 5.2–6.2:1, the
figure's smallest stamps 7.5:1, the banner 7.3:1.

**Text size is the real accessibility problem**, and it is A4: 6.6–8 pt in the
figure, 3–5 pt at slide scale.

**Colour-vision deficiency: viridis holds up well.** Under deuteranopia
simulation (`ux-shots/ux-06-slide-tests.png`, panel D) the blue→yellow axis
survives intact and the grey no-data cells stay distinct. This was the right
colormap choice and I have no complaint about it.

**Greyscale is where the missing-data guarantee breaks.**

The `no_data` fill `#b3b3b3` has relative luminance **0.4508**. Viridis step
192/255 — `#5ec962`, a real value at ~0.75 of the scale — has **0.4503**. The
greyscale contrast ratio between them is **1.001**: identical.
**23 of 256 steps (9% of the ramp, normalised 0.71–0.79) sit within 0.05
luminance of the no-data grey.** `ux-shots/ux-13-greyscale-collision.png`,
panel 1, shows the two swatches side by side in colour and then desaturated.

Invariant 2 proves the fills are disjoint *in colour space*. That proof does not
survive desaturation, and `PLAN.md` §6.2's "provably distinct from every value
colour" is therefore true only of the colour render. The remaining cue is the
`////` hatch, which does survive vector zoom — but not a washed-out projector, a
compressed raster paste into PowerPoint, or a photocopied handout. In any of
those, "never measured here" reads as a good score.

*Change:* either give `no_data` a luminance outside the viridis ramp (near-white
or near-black), or add a greyscale-disjointness assertion alongside the existing
colour one so the guarantee means what it says. **~1 hour.**

**The four cell states**, for completeness: on screen the three non-value states
are well differentiated (grey `////`, white `....`, black `xxxx` on red). At
slide scale the hatch patterns collapse and only fill lightness remains — grey,
white, black — which still separates those three from each other. The collision
that matters is grey-against-mid-viridis, above.

---

## Summary of recommended changes

| # | change | size |
|---|---|---|
| A1 | banner only on `FAIL`; never for a source that drives no ring | ~1 h |
| A2 | clear `#result` on submit | minutes |
| A3 | dropped gene names on the figure and in the CSV | ~1 h |
| A4 | a 16:9 presentation render with 11 pt+ labels | ~half a day |
| A5 | a colour bar reading "rescaled within each ring" | ~2 h |
| A6 | shrink the axes so key and footer clear the rings | ~1 h |
| B1 | `MAX_GENES` → ~60, validated before fetching | minutes |
| B2 | elapsed counter, stage name, cancel | ~2 h |
| B3 | make "N not shown" open the ledger | minutes |
| B8 | expose the PNG that is already being written | minutes |
| — | greyscale-disjointness assertion for `no_data` | ~1 h |
| — | `<g id="ring-…">` grouping for Illustrator | ~1 h |
| — | title/subtitle field wired to `_draw_centre` | ~1 h |

Nothing here is overnight work. A2, B3, B8 and B1 together are under an hour
and remove the two ways you could be actively misled, plus the crash.

## What I did not test

Print output (no printer), Illustrator and PowerPoint themselves (not installed
— the vector-editor findings are measured from the SVG source: element types,
group structure, font resolution, coordinates), real projector conditions
(simulated by filter), and any run above 120 genes end-to-end (the 181-spoke
crash is confirmed at the `Circos` constructor, and the UI consequence is read
from `app.py:99` and `app.js:73` rather than observed).
