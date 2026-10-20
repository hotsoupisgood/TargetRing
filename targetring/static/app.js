"use strict";

let RUN = null;

// v4.0. The one run a tool-mode demo opens in (`/api/health.featured_run`).
//
// It is NOT assigned to `RUN`, and the distinction is load-bearing. `RUN` means
// "this session drew this figure and the server still holds its cells": it is
// what `redrawForThisWindow` POSTs `/web-figure` for on every resize, what
// `showTab` reloads the grid from, and what the live `/gene/` drawer is fetched
// against. None of that is true of a run read off disk — two of those three
// routes are refused outright on a demo box — so a disk run gets its own name
// and the code that needs a live run keeps asking for one.
let TOOL_RUN = "";

// v4.2. The run this page is SHOWING from disk, whether it came from `?run=` or
// from `TOOL_RUN`. Distinct from both: `RUN` means "this session computed it"
// and `TOOL_RUN` means "the server named it as the one to open", and neither
// answers the question "is there a figure on screen that a button must not
// blank?". That question is this variable, and it is what makes "Draw the
// figure" a REDRAW rather than a refusal (Puck, 2026-10-19: "draw the figure
// breaks everything").
let DISK_RUN = "";

// v3.5. The window the last figure was drawn for, as [panel px, viewport px], so
// a resize that changes nothing costs nothing.
let DREW_FOR = null;
let RESIZE_TIMER = null;
// A resize is settled after this long, and 20 px is the smallest change worth a
// redraw: the canvas is quantised to 0.01 in, which is 1.08 px, and a figure
// redrawn on every intermediate width of a window drag would be twenty renders.
const RESIZE_SETTLE_MS = 450;
const RESIZE_MIN_PX = 20;

// The width `#figure` actually gets, measured rather than computed. `#figure-col`
// is a flex child with `min-width: 0` beside a 330 px panel inside a padded
// `main`, so its width is not the sum of four constants -- which is exactly why
// the server is told this number instead of working it out from a copy of the
// stylesheet. Measured with `#result` momentarily un-hidden when there is no
// figure yet: synchronous, so nothing paints, and `hidden` is the only thing
// standing between the element and its real width.
function figurePanelPx() {
  const result = $("#result");
  const wasHidden = result.hidden;
  if (wasHidden) result.hidden = false;
  const w = $("#figure").getBoundingClientRect().width;
  if (wasHidden) result.hidden = true;
  return w;
}

// What to tell the server about this window, or null if the measurement is not
// usable -- in which case the server draws the 1440 x 900 canvas it has always
// drawn rather than trusting a zero.
function windowMeasurement() {
  const panel = figurePanelPx();
  if (!(panel > 200) || !(window.innerHeight > 200)) return null;
  return [panel, window.innerHeight];
}

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------- naming the input: "Gene expression", then the comparison ----------
//
// Puck, 2026-10-19, asked whether "contrast" is the first word a biologist
// should meet: "Maybe just title it gene expression and then say
// Differential... or Contrast underneath". So every place the tool names its
// input is three layers, in this order:
//
//   Gene expression                                   <- the heading
//   Differential expression: <group A> vs <group B>   <- what was compared
//   Expression Atlas <acc> · contrast <id> · n=… · …  <- small-print provenance
//
// `contrast` stays where it is an IDENTIFIER (`g2_g1`) or a column name in a
// data file. It is never a heading word, and no JSON key or CSV column is
// renamed — a key that reads nicely and a key that joins are different jobs.
//
// Built HERE and nowhere else, for the reason `collapsedContrastLine()` exists:
// the moment there are two of these strings they drift, and a card that
// disagrees with the figure is the plausible-wrong-output this build refuses.

//: "'Crohn's disease' vs 'non inflammatory bowel disease control' in 'A1a
//: Paris classification'" -> "Crohn's disease vs non inflammatory bowel
//: disease control". The factor clause is dropped: it is the Atlas variable
//: the groups were cut on, not one of the two groups, and reading it as a
//: third term is the kind of small misreading this layering is for.
//
// Split on the SEPARATOR, never on the quotes — `/'([^']+)'/` reads "Crohn"
// out of "'Crohn's disease'", the same defect `showToolInputs` was fixed for.
// Returns "" when the name is not in that shape, and every caller falls back
// rather than printing half a comparison.
function plainComparison(name) {
  const s = String(name || "");
  const sep = s.indexOf("' vs '");
  if (sep < 0) return "";
  const a = s.slice(0, sep).replace(/^'/, "");
  let b = s.slice(sep + "' vs '".length);
  const inAt = b.lastIndexOf("' in '");
  b = (inAt >= 0 ? b.slice(0, inAt) : b.replace(/'$/, ""));
  return a && b ? `${a} vs ${b}` : "";
}

//: The manifest stores the platform as Atlas spells it (`rnaseq`) and the
//: pipeline as the configuration file spells it ("DESeq2 version: 1.10.1").
//: Both are shown as a reader writes them. The stored values are untouched.
const platformWord = (p) =>
  String(p || "").toLowerCase() === "rnaseq" ? "RNA-seq" : String(p || "");
const pipelineWord = (p) => String(p || "").replace(/\s*version:\s*/i, " ");

//: The small-print line, from a manifest-shaped contrast record. Each field is
//: dropped when absent rather than printed as an empty separator-delimited gap.
function provenanceLine(c) {
  const parts = [];
  if (c.accession) parts.push(`Expression Atlas <code>${esc(c.accession)}</code>`);
  const id = c.contrast_id || c.id;
  if (id) parts.push(`contrast <code>${esc(id)}</code>`);
  if (c.n_test != null && c.n_ref != null) {
    parts.push(`n=${esc(c.n_test)} vs ${esc(c.n_ref)}`);
  }
  const plat = platformWord(c.platform);
  if (plat) parts.push(esc(plat));
  const pipe = pipelineWord(c.pipeline);
  if (pipe) parts.push(esc(pipe));
  if (c.ensembl_release) parts.push(`Ensembl ${esc(c.ensembl_release)}`);
  return parts.join(" · ");
}

//: The two grid columns that ARE the submitted differential expression, in the
//: payload's own ring-key spelling. Mirrors `figure.DE_INPUT_RINGS`; the grid's
//: grouped header is computed from it rather than from a hard-coded colspan,
//: and `tests/test_table.py` asserts the two lists are the same.
const DE_RING_KEYS = ["logfc", "neg_log10_padj"];

//: The one ring whose grid column prints something other than the number the
//: band draws: the column shows the adjusted p (`fmtP`), the ring stays on its
//: -log10 colour scale, and the hover carries both. Mirrors
//: `figure.RingSpec.table_restates` — Puck, 2026-10-19, after the drawer and
//: the GO box got `<0.05`: "i didn't see it in the table".
const PADJ_RING = "neg_log10_padj";

//: The whole compact block: the heading, the comparison, the provenance line
//: and the sign sentence, from a manifest-shaped contrast record.
//:
//: v4.2, Puck 2026-10-19: "that gene expression expansion should be organised
//: with the gene expression data not arbitrary". Two surfaces print these four
//: strings now — `#disease-chosen`, where a chosen run's input is echoed, and
//: `#table-de`, directly above the grid columns the numbers are IN. So it is
//: ONE builder, for the same reason `provenanceLine` above is one: the moment
//: there are two of these the pair drifts, and a card that disagrees with the
//: figure is the plausible-wrong-output this build refuses.
//:
//: Nothing here rewords Atlas. `name`, `accession`, `contrast_id`, `n_test`,
//: `n_ref`, `platform`, `pipeline`, `ensembl_release` and `sign_sentence` are
//: printed as the configuration file states them; `plainComparison`,
//: `platformWord` and `pipelineWord` only choose which part is shown.
function geneExpressionNoteHtml(c, cls) {
  if (!c || !(c.accession || c.name || c.contrast_id)) return "";
  // The comparison falls back to the manifest's own `name` if it is not in the
  // `'A' vs 'B' in 'factor'` shape, because a card that silently drops what
  // was compared is worse than a quoted one.
  const comparison = plainComparison(c.name) || c.name || "";
  const sign = c.sign_sentence || "";
  return `<div class="note${cls ? " " + cls : ""}"><b>Gene expression</b><br>
    Differential expression: ${esc(comparison)}
    <br><span class="hint">${provenanceLine(c)}</span>
    ${sign ? `<br><span class="hint">${esc(sign)}</span>` : ""}</div>`;
}

// The server reports a refusal two ways and the page must read both. The
// hand-built `JSONResponse` paths — 422 no_selection, 502 source_failure —
// carry `message`. Every `raise HTTPException` instead serialises as FastAPI's
// `{"detail": ...}`, which this page did not read: for the whole of the build
// a 400, 404, 409 or 503 arrived with a real sentence attached and was shown
// as the bare word "failed". Caught 2026-10-09 when "Provide a DE table,
// pasted or uploaded." — which names the fix — reached the user as "failed".
const errText = (body, fallback) =>
  (body && (body.message || body.detail)) || fallback;

// ---------- setup ----------

async function init() {
  const r = await fetch("/api/regions").then((x) => x.json());
  const sel = $("#region");
  DEFAULT_REGION = r.default || "";
  sel.innerHTML = r.regions.map((t) =>
    `<option ${t === r.default ? "selected" : ""}>${esc(t)}</option>`).join("");
  sel.addEventListener("change", () => { REGION_TOUCHED = true; });
  if (r.error) $("#status").textContent = "GTEx region list unavailable: " + r.error;

  // v2.5. The spoke orders and the rings a threshold can be set on both come
  // from the build, so a control can never offer a ring the figure does not have.
  const o = await fetch("/api/orderings").then((x) => x.json());
  $("#ordering").innerHTML = o.orderings.map((x) =>
    `<option value="${esc(x.value)}" ${x.value === o.default ? "selected" : ""}>
      ${esc(x.label)}</option>`).join("");
  $("#filterable").innerHTML = "Filterable rings: " +
    o.filterable_rings.map((f) => `<code>${esc(f.ring)}</code>`).join(", ");

  // Demo mode. Asked of the server, never assumed: the same page is served on
  // this machine and on a demo box, and the only thing that differs is what the
  // server will do. `?run=` deep links still work -- they read artefacts -- so
  // every card is one.
  try {
    const h = await fetch("/api/health").then((x) => x.json());
    if (h.demo) {
      document.body.classList.add("demo");
      // The masthead's "nothing leaves this machine" is a claim about the
      // reader's machine, and on a demo box it is somebody else's. Say what
      // this box actually is.
      const note = $("#local-note");
      // v4.2, Puck 2026-10-19: "no published version please" — no sentence on
      // this page may call it a demo, a read-only copy, a static export or a
      // published version, or explain that there is no server behind it. The
      // page is the tool.
      //
      // So in tool mode the clause is EMPTY rather than reworded: the masthead's
      // default ("nothing leaves this machine") is a claim about the reader's
      // machine and is not this box's to make, and anything that replaced it
      // would be a sentence about hosting above a figure. What a control cannot
      // do is said by that control, in its own tooltip
      // (`disableServerOnlyControls`), where a reader meets it.
      if (note) note.textContent = h.featured_run
        ? ""
        : "Finished runs, served from their artefacts.";
      // v4.0, Puck 2026-10-19: "i want the target ring to be interactive too
      // also make it the same as the final site will be so just have one
      // example" and "its supposed to be a tool, explanations will be on my
      // blog post".
      //
      // So a demo box that names ONE run opens IN it. `body.tool` is what the
      // CSS hangs the difference on: no card index to choose from, no
      // "read-only demo" paragraph above the work, and the analyse controls
      // left VISIBLE rather than hidden — they are what the tool is, and the
      // server already answers each of them with `DEMO_REFUSAL` in words
      // (`app._demo_gate`, `tests/test_demo.py`). A refusal a reader can read
      // is a truer description of this box than a control that is not there.
      //
      // `featured_run` is the server's, not this page's: a page that picked
      // its own run could open one the gate would then refuse.
      if (h.featured_run) {
        document.body.classList.add("tool");
        TOOL_RUN = h.featured_run;
        relabelToolTabs();
      }
      const r = await fetch("/api/runs").then((x) => x.json());
      $("#demo-runs").innerHTML = (r.runs || []).map((x) => {
        const src = x.contrast
          ? `genes from Expression Atlas <code>${esc(x.contrast)}</code>${
              x.contrast_name ? " — " + esc(x.contrast_name) : ""}`
          : "genes from a pasted differential-expression table";
        return `<div class="demo-card">
          <p><b>${esc(x.region || "")}</b> · ${esc(x.n_genes ?? "")} genes ·
            ${src}${x.filtered ? " · <b>filtered</b>, and the figure says so" : ""}
            ${x.any_invariant_failed ? " · <b>an invariant failed</b>" : ""}</p>
          <div class="row">
            <a class="btn" href="?run=${encodeURIComponent(x.run_id)}">Open the run</a>
            <a class="btn ghost" href="/api/run/${encodeURIComponent(x.run_id)}/figure.png"
               target="_blank" rel="noopener">figure.png</a>
            <a class="btn ghost" href="/api/run/${encodeURIComponent(x.run_id)}/manifest.json"
               target="_blank" rel="noopener">manifest.json</a>
          </div>
          <img src="/api/run/${encodeURIComponent(x.run_id)}/figure.png"
               alt="targetring figure, run ${esc(x.run_id)}" loading="lazy">
        </div>`;
      }).join("") || `<p class="hint">No demo runs are configured on this server.</p>`;
    }
  } catch (e) { /* not a demo, or health unreachable: the normal landing stands */ }

  // v4.2. Both loaders fill the textarea and touch nothing else — in
  // particular they never clear `#result`, so a figure already on screen
  // survives loading an example beneath it. A payload without the key is said
  // rather than written into the box as the string "undefined".
  const loadExample = async (which) => {
    let body;
    try {
      const res = await fetch("/api/example");
      body = res.ok ? await res.json() : null;
    } catch (e) { body = null; }
    const text = body && typeof body[which] === "string" ? body[which] : "";
    if (!text) { failSoft("The example table could not be loaded."); return; }
    $("#table").value = text;
    $("#status").className = "";
    $("#status").textContent = "";
    leaveLanding();
  };
  $("#load-example").onclick = () => loadExample("realistic");
  $("#load-broken").onclick = () => loadExample("broken");
  wireDiseaseSearch();
  $("#go").onclick = analyse;
  $("#drawer-close").onclick = closeDrawer;
  $("#scrim").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); if (!$("#summary-box").hidden) hideSummary(true); }
  });
  $("#summary-close").onclick = () => hideSummary(true);
  $("#summary-box").addEventListener("mouseleave", () => hideSummary(false));

  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => goTab(t.dataset.tab);
  });
  // The Back button. Wired once, here, before anything paints — see the block
  // above `goTab` for what it is for and why the drawer is a history entry.
  addEventListener("popstate", applyHash);

  // v3.6. The table reads artefacts, so it can open any run on disk — which is
  // the first time this app has had a way back to a run you already made.
  const picker = $("#table-run");
  fetch("/api/runs").then((x) => x.json()).then((r) => {
    $("#table-runs").innerHTML = (r.runs || []).map((x) =>
      `<option value="${esc(x.run_id)}">${esc(x.region || "")} · ${
        esc(x.n_genes ?? "")} genes · ${esc(x.timestamp_utc || x.note || "")}</option>`
    ).join("");
    $("#table-run-note").textContent =
      `${(r.runs || []).length} recent run(s) on this machine.`;
    // The evidence pane's picker offers the same runs from the same fetch —
    // two lists built from two calls could disagree about what is on disk.
    $("#evidence-runs").innerHTML = $("#table-runs").innerHTML;
  });
  $("#evidence-load").onclick = () => loadEvidence($("#evidence-run").value);
  $("#evidence-run").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadEvidence($("#evidence-run").value);
  });
  $("#table-load").onclick = () => { TABLE.sort = []; TABLE.dim = []; TABLE.dimDraft = null;
    loadTable(picker.value); };
  picker.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { TABLE.sort = []; TABLE.dim = []; TABLE.dimDraft = null;
      loadTable(picker.value); }
  });

  // `?run=<id>` opens that run's table directly. There is no figure in memory
  // for it, so the figure pane says so and points at the run's own artefacts
  // rather than showing the previous run's circle (UX A2's finding).
  // Pasting is the other way in, and the landing hides that panel. This leaves
  // the landing without choosing a disease, so the paste path is reachable in
  // one click rather than not at all.
  const paste = $("#go-paste");
  if (paste) paste.onclick = () => {
    leaveLanding();
    $("#table").focus();
  };

  // v4.0. In tool mode the featured run IS the landing, so a demo with no
  // `?run=` opens the same page a deep link does rather than a list of cards.
  // The query parameter still wins: a reader who asked for a particular run
  // gets that one.
  const wanted = new URLSearchParams(location.search).get("run") || TOOL_RUN;
  if (wanted) {
    // A deep link lands on a result, so it was never a landing.
    leaveLanding();
    // And it is ONE run's page: the demo index -- every listed run's card with
    // its figure.png -- must not sit above this run's tabs, where the first
    // card's figure (a Liver paste fixture, in the default list) read as this
    // run's figure (2026-10-18 walkthrough, item g). `body.viewing-run` hides
    // the index; the run's own figure, if it exported one, is shown instead.
    document.body.classList.add("viewing-run");
    // v4.2. From here on there is a figure on screen that no control may blank;
    // see `redrawLoadedRun`.
    DISK_RUN = wanted;
    $("#result").hidden = false;
    $("#banner").hidden = true;
    $("#counts").innerHTML = "";
    $("#figure").innerHTML =
      `<p class="hint">No figure is drawn in this session. You are looking at run
       <code>${esc(wanted)}</code> as a table, read from its artefacts on disk.
       Its rendered figure is
       <a href="/api/run/${esc(wanted)}/figure.svg" target="_blank" rel="noopener">
       figure.svg</a>.</p>`;
    picker.value = wanted;
    // The evidence pane's picker too, so switching to that tab opens the run
    // the reader deep-linked rather than asking for one again.
    $("#evidence-run").value = wanted;
    loadTable(wanted);
    // v4.0, Puck: "i want the target ring to be interactive too". The run's own
    // `figure-web.svg` is INLINED rather than shown as an `<img>`, which is the
    // whole of the interactivity: the cells in that file carry
    // `id="cell|<ring>|<i>|<gene>"` — the same handles the invariant checks
    // used — so `wireFigure` can make every cell and every spoke label open
    // that gene's baked drawer. A PNG cannot be clicked, and the sentence above
    // was all a disk-loaded run used to get.
    //
    // Nothing is redrawn and nothing is fetched from a source: the SVG on disk
    // is the one this run drew, and the drawer is the baked file beside it.
    // v4.1: the hash wins over the default. This resolves after `startHistory`
    // below, so a `#ledger` or `#figure/BRAF` link would otherwise be painted
    // and then immediately overwritten by the figure tab.
    showToolFigure(wanted).then(() => showTab(currentView().tab),
                                () => showTab(DEFAULT_TAB));
    if (TOOL_RUN === wanted) showToolInputs(wanted);
  }

  // v3.5. The figure is sized for the window it is read in, so the window
  // changing size is an input. Debounced, and a no-op when the change is smaller
  // than the canvas quantum or when there is no run in memory to redraw.
  window.addEventListener("resize", () => {
    clearTimeout(RESIZE_TIMER);
    RESIZE_TIMER = setTimeout(redrawForThisWindow, RESIZE_SETTLE_MS);
  });

  // v4.2. The three GO / NO-GO criteria and their bounds, at the top of the
  // Table tab. Fetched once at boot: it is the build's rule, not the run's, and
  // it does not change while the page is open.
  fetch("/api/criteria").then((x) => (x.ok ? x.json() : null))
    .then((c) => { if (c) renderCriteria(c); })
    .catch(() => { /* an older server has no such route; the tab is unchanged */ });

  // After every control is wired, so the disabling is not undone by a later
  // assignment.
  disableServerOnlyControls();

  // Last, so the hash is read after every control above is wired and can be
  // painted into. It replaces rather than pushes; the comment on it says why.
  startHistory();
}

// v4.2, Puck 2026-10-19: "on the top of the table page the main part should say
// there are 3 go no go criteria, and a numbered list. and the bounds we are
// looking for in them. the other stuff is less important. and say a note like
// full descriptions are at the bottom."
//
// Two renderings of one payload: the numbered list with the bounds at the top of
// the Table tab, and the rule in full at the bottom, under the anchor the note
// links to. Both come from `/api/criteria`, i.e. `recommend.criteria_block()`,
// which reads `current_thresholds()` — so neither can state a bound the drawers
// were not computed against.
function renderCriteria(c) {
  const top = $("#table-criteria");
  const bottom = $("#full-descriptions");
  if (!c || !(c.criteria || []).length) return;
  if (top) {
    top.innerHTML =
      `<h3 class="pane-h">${esc(c.heading)}</h3>
       <ol class="criteria">` +
      c.criteria.map((x) => `<li><b>${esc(x.name)}</b> —
         <span class="bound">${esc(x.met_when)}</span></li>`).join("") +
      `</ol>
       <p class="pane-lede">${esc(c.never_a_criterion)}</p>
       <p class="pane-lede"><a href="#full-descriptions" id="to-full-descriptions">Full
         descriptions are at the bottom of this page</a>.</p>`;
    // An in-page anchor, scrolled rather than navigated: the hash is the tab
    // router's (`#<tab>` / `#<tab>/<gene>`), and writing `#full-descriptions`
    // into it would send the router to its unknown-tab fallback.
    const link = $("#to-full-descriptions");
    if (link) link.onclick = (ev) => {
      ev.preventDefault();
      bottom.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }
  if (bottom) {
    bottom.innerHTML =
      `<h3 class="pane-h">The GO / NO-GO rule in full</h3>
       <table><thead><tr><th>#</th><th>Criterion</th><th>Met when</th>
         <th>Source</th></tr></thead><tbody>` +
      c.criteria.map((x) => `<tr><td>${esc(x.n)}</td><td>${esc(x.name)}</td>
        <td class="reason">${esc(x.met_when)}</td>
        <td class="reason">${esc(x.source)}</td></tr>`).join("") +
      `</tbody></table>
       <p class="pane-lede">${esc(c.go)} ${esc(c.no_go)} ${esc(c.insufficient)}
         ${esc(c.never_a_criterion)}
         <span class="hint">Rule version <code>${esc(c.rule_version)}</code>.</span></p>
       <p class="pane-lede">Each column's full name, its units and its source
         are in the strip below; the colour each value was drawn as is on the
         Circos tab, scale by scale.</p>`;
  }
}

// v4.2. True when this copy of the page has no analysis server behind it —
// `scripts/static_demo_shim.js` sets the flag, so the served app never takes
// this branch and `tests/test_demo.py` still enumerates the server's behaviour
// unchanged.
function noServerHere() {
  return !!window.TARGETRING_STATIC_DEMO;
}

// v4.2, Puck 2026-10-19: a control that cannot act says so in a short plain
// tooltip, on itself. Not a banner, not a paragraph, and never a sentence about
// what kind of copy of the page this is.
//
// The list is only what genuinely needs the server: a disease search is a live
// query, a pasted table is a new analysis. "Draw the figure" is deliberately
// NOT here — it redraws the run on screen, which is a real thing it can do.
function serverOnly(sel, why) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (!el) return;
  el.disabled = true;
  el.title = why;
}

function disableServerOnlyControls() {
  if (!noServerHere()) return;
  serverOnly("#disease", "Searching for a disease needs the analysis server.");
  serverOnly("#table", "Drawing a new figure needs the analysis server.");
  serverOnly("#file", "Drawing a new figure needs the analysis server.");
  serverOnly("#load-example", "Drawing a new figure needs the analysis server.");
  serverOnly("#load-broken", "Drawing a new figure needs the analysis server.");
  serverOnly("#ordering", "The spoke order is set when a figure is drawn.");
  serverOnly("#filters", "Threshold filters are applied when a figure is drawn.");
  serverOnly("#drop-no-data", "Threshold filters are applied when a figure is drawn.");
  serverOnly("#region", "The region is set when a figure is drawn.");

  // v4.2, Puck 2026-10-19: "that gene expression expansion should be organised
  // with the gene expression data not arbitrary". On the published page the
  // run is already chosen, so the top `Gene expression` box is not an input
  // form — it is this run's contrast facts sitting above everything, away from
  // the two columns they describe. Those facts are now printed in `#table-de`,
  // directly above grid columns 1-2 and under the same grouped header, so the
  // box is REMOVED rather than disabled: a disabled search field is still a
  // search field, and there is nothing here to search.
  //
  // Removed only on this branch. The local app keeps `#find`, where it is the
  // input form and the only way in.
  const find = $("#find-panel") || $("#find");
  if (find) find.remove();
}

// ---------- stage 1: disease search ----------
//
// Debounced typing, one request per settled query, and the dropdown is a list of
// buttons rather than a `<datalist>`: a datalist can only carry a string, and
// every hit here has to carry two ids, a count, a build date and a mapping note.
// A hit whose count is zero stays clickable — "Atlas has never studied this" is a
// real answer and the picker page is where it gets said.

const DISEASE_SETTLE_MS = 300;
let DISEASE_TIMER = null;
// The last query whose response was rendered, so an out-of-order reply from a
// slower earlier request cannot overwrite a newer one's hits.
let DISEASE_QUERY = "";
let CHOSEN_DISEASE = null;

function wireDiseaseSearch() {
  const box = $("#disease");
  if (!box) return;
  box.addEventListener("input", () => {
    clearTimeout(DISEASE_TIMER);
    DISEASE_TIMER = setTimeout(() => searchDiseases(box.value), DISEASE_SETTLE_MS);
  });
}

async function searchDiseases(q) {
  const query = (q || "").trim();
  DISEASE_QUERY = query;
  if (!query) {
    $("#disease-hits").innerHTML = "";
    $("#disease-status").textContent = "";
    return;
  }
  $("#disease-status").textContent = "searching OLS4…";
  let r;
  try {
    r = await fetch("/api/diseases?q=" + encodeURIComponent(query));
  } catch (e) {
    $("#disease-status").textContent = "disease search failed: " + e;
    return;
  }
  const b = await r.json();
  if (DISEASE_QUERY !== query) return;   // a newer query is already in flight
  if (!r.ok) {
    // A 503 here means there is no contrast index. The counts are NOT shown as
    // zeros in that case, because "0 contrasts" from a missing index and "0
    // contrasts" from a disease Atlas has not studied are the same string and
    // completely different facts.
    $("#disease-status").textContent = "";
    $("#disease-hits").innerHTML =
      `<div class="note bad"><b>${esc(b.error || "failed")}</b><br>
       ${esc(errText(b, ""))}<br><span class="hint">${esc(b.note || "")}</span></div>`;
    return;
  }
  $("#disease-status").textContent = `${b.diseases.length} hit(s), in OLS4's own order`;
  $("#disease-hits").innerHTML = b.diseases.length
    ? `<ul class="hits">${b.diseases.map((d, i) => diseaseHitHtml(d, i)).join("")}</ul>`
    : `<p class="hint">OLS4 has no EFO or MONDO term matching that.</p>`;
  b.diseases.forEach((d, i) => {
    const el = $(`#hit-${i}`);
    if (el) el.onclick = () => chooseDisease(d, b.index);
  });
  $("#index-note").innerHTML = indexNoteHtml(b.index);
  $("#find-sources").innerHTML = sourcesHtml(b.sources);
}

// `count_text` and the `availability` word both come from the server
// (`DiseaseContrastSummary`). The styling branches on the word, never on the
// prose, and the three states are styled DIFFERENTLY on purpose: a disease Atlas
// has never studied and a disease whose contrasts are all microarray must not
// look the same in this list, because they are not the same answer. The second
// one is the app's own choice, and a reader deciding whether to go and look
// elsewhere needs to know which it is.
function diseaseHitHtml(d, i) {
  const c = d.counts;
  const obsolete = (d.obsolete_atlas_ids || []).length
    ? `<span class="hint">Atlas-side id${d.obsolete_atlas_ids.length > 1 ? "s" : ""}:
       <code>${d.obsolete_atlas_ids.map(esc).join("</code> <code>")}</code>
       (obsolete; never sent to Open Targets)</span>` : "";
  return `<li><button class="hit" id="hit-${i}">
      <b>${esc(d.label)}</b> <code>${esc(d.current_id)}</code>
      <span class="hit-count ${esc(c.availability)}">${esc(c.count_text)}</span>
      <span class="hint">· ${esc(c.built_text)}</span><br>${obsolete}
    </button></li>`;
}

// The index's own provenance, under the hits rather than behind a click. Three of
// these four numbers are things a count alone hides: how many experiments failed
// to parse, how many contrasts carry no disease id at all (so appear under no
// disease), and how many have an id that is not `{ref}_{test}`.
function indexNoteHtml(ix) {
  if (!ix) return "";
  return `Local Atlas contrast index: <b>${ix.n_contrasts}</b> contrasts from
    <b>${ix.n_experiments}</b> of ${ix.n_experiments_in_population} human
    differential experiments carrying a <code>disease</code> factor, built
    <b>${esc((ix.built_utc || "").slice(0, 10))}</b>${
      ix.stale ? ` — <b class="bad-text">STALE</b> (older than ${
        ix.stale_after_days} days; rebuild with
        <code>scripts/build_atlas_index.py</code>)` : ""}.
    ${ix.n_failed_experiments} experiment(s) failed to parse.
    ${ix.n_contrasts_without_disease_id} contrast(s) carry no
    <code>disease</code> factor id on the test group, so they appear under no
    disease at all. ${ix.n_contrasts_invalid_id} contrast(s) have an id that is
    not <code>{reference}_{test}</code> and are shown as sign-unsafe rather than
    drawn.`;
}

function sourcesHtml(rows) {
  if (!rows || !rows.length) return "";
  return `<details class="src"><summary><span class="chev" aria-hidden="true">▾</span>
      Where these come from — ${rows.length} source(s)</summary>
    <div class="reveal"><table><tbody>${rows.map((s) => `<tr>
      <td><a href="${esc(s.doc_url)}" target="_blank" rel="noopener">${
        esc(s.legend_text)}</a></td>
      <td class="reason">reads ${esc(s.reads)}</td>
      <td class="reason">${esc(s.version)}</td></tr>`).join("")}
    </tbody></table></div></details>`;
}

function chooseDisease(d, ix) {
  CHOSEN_DISEASE = d;
  // PLAN §9: changing step 1 explicitly invalidates every step after it —
  // never let a stale gene table or selection survive a changed disease.
  invalidateFromStep(3);
  const notes = (d.mapping_notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
  const rejected = (d.rejected_ids || []).map((n) => `<li>${esc(n)}</li>`).join("");
  $("#disease-chosen").hidden = false;
  $("#disease-chosen").innerHTML =
    `<div class="note"><b>${esc(d.label)}</b>
      — <code>${esc(d.current_id)}</code> for Open Targets,
      <code>${d.atlas_ids.map(esc).join("</code> <code>")}</code> for Expression
      Atlas. ${esc(d.counts.count_text)}, ${esc(d.counts.built_text)}.
      ${notes ? `<ul class="tight">${notes}</ul>` : ""}
      ${rejected ? `<p class="hint">Cross-references that did not verify and were
        NOT used: <ul class="tight">${rejected}</ul></p>` : ""}
    </div>`;
  loadContrasts(d, ix);
}

// ---------- stage 2: the contrast picker ----------
//
// Three rules this table follows and one it refuses.
//
// * **A row is a contrast.** `E-GEOD-100833` appears 101 times, because the sign,
//   the groups and the n per side differ per contrast. An experiment is never a
//   row.
// * **Every flag is visible and no flag removes a row.** 121 of the 138 Crohn's
//   contrasts are microarray; they are greyed with the reason and still counted,
//   because hiding them would make this panel look like Atlas has 17. The reason
//   names where the absence is: the data is in Atlas and this app declines to
//   read it (RNA-seq only, 2026-10-02 — settled, not pending). A reader must be
//   able to tell that from "Atlas has nothing" without asking anyone.
// * **Single-column sort only**, and an order the server will not honour is
//   reported rather than quietly swapped for another.
//
// And the refusal: **nothing is preselected.** 101 of the 138 come from one
// ustekinumab trial, and the spike's verdict is that no sensible default exists.

let PICKER = { disease: null, sort: "" };

// Leaving the landing state. Called the moment a disease is chosen — that is
// the point the page stops being a search box and becomes the working surface.
// One-way within a page load: going back to one box with a contrast picked
// below it would be a landing page that is not landing on anything.
function leaveLanding() {
  document.body.classList.remove("landing");
}

// The figure a disk-loaded run exported, or nothing. Asked of the server (a
// HEAD on the artefact route), never assumed: a run whose exports were cut off
// has no figure.png, and an <img> that 404s is a broken-image icon where a
// figure should be. Nothing else -- never another run's figure -- goes here.
async function showDiskRunFigure(runId) {
  const url = `/api/run/${encodeURIComponent(runId)}/figure.png`;
  let ok = false;
  try {
    const r = await fetch(url, { method: "HEAD" });
    ok = r.ok;
  } catch (e) { ok = false; }
  if (!ok) return;
  const slot = document.createElement("p");
  slot.className = "disk-run-figure";
  slot.innerHTML = `<img src="${url}" alt="figure.png exported by run ${esc(runId)}"
    loading="lazy"><span class="hint">This run's own exported figure.png, read
    from disk; the interactive circle is drawn only in the session that ran it.</span>`;
  $("#figure").appendChild(slot);
}

// v4.0. The one tab whose name stops being true in tool mode. `renderCaveats`
// draws only the dated spend table there, so the tab says so.
//
// Relabelled in JS rather than in `index.html` because the local app keeps the
// pane it has: the prose in it is this build's own statement of what it does not
// claim (AGENTS.md, "What is not claimed"), and the name is asserted on the
// figure's own footer by `figure.caveats_for_the_page` ("every caveat this run
// carries is in the What this is not tab"), which the local render still draws.
function relabelToolTabs() {
  const t = document.querySelector('.tab[data-tab="caveats"]');
  if (t) t.textContent = "Paid calls";
}

// v4.0. A run read off DISK, drawn as the live circle it was, and clickable.
// Puck, 2026-10-19: "i want the target ring to be interactive too".
//
// Three facts make this possible without re-running or re-rendering anything:
//
// 1. `figure-web.svg` is already on disk beside the run, and its cells carry
//    `id="cell|<ring>|<index>|<gene>"` — the same handles `invariants.py`
//    audited. Inlined as SVG rather than shown in an `<img>`, every one of them
//    is an element this page can bind a click to. That is the whole mechanism:
//    no canvas, no re-layout, no second renderer.
// 2. the drawer each click opens is the BAKED file (`/api/run/{id}/drawer/
//    {gene}`), a file read, which is why this works on a box that refuses every
//    route that fetches.
// 3. `/api/run/{id}/scales` answers from `values.csv` since v4.0, so the decode
//    panel beside the circle — the four cell states and the ring scales — is
//    the same data the Table tab pivots.
//
// It returns a promise that REJECTS when the run exported no web SVG, so the
// caller can fall back to the grid instead of leaving an empty figure pane.
// `RUN` is deliberately not set; see its declaration.
async function showToolFigure(runId) {
  const url = `/api/run/${encodeURIComponent(runId)}/figure-web.svg`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${runId} exported no figure-web.svg`);
  const svg = await res.text();
  $("#figure").innerHTML = svg;
  // The genes this circle actually drew, read off the spoke labels in the file
  // rather than taken from a payload: `wireFigure` is deciding which text
  // elements are gene labels, and the SVG in front of it is the only thing that
  // knows what it drew. A gene in the manifest that the render dropped must not
  // become a clickable label here.
  wireFigure(spokeGenesInSvg());
  // The decode panel, so the two boxes beside the circle are not empty. Puck's
  // phone screenshot, 2026-10-19: "an empty white rounded box and an empty dark
  // box above The 12 ring scales" — they were the cell-state box and the wedge
  // note (named by id in exactly one place each, which a test counts),
  // populated only by `renderDecode`, which only the live path ever called.
  try {
    const s = await fetch(`/api/run/${encodeURIComponent(runId)}/scales`)
      .then((x) => (x.ok ? x.json() : null));
    if (s) renderDecode(s);
  } catch (e) { /* the panel keeps whatever it had; the figure is the point */ }
  hideEmptyDecodeBoxes();
  // The sentence that says what to do with the circle now that it is one.
  // Set here rather than in `loadTable` because this function is the only thing
  // that knows the inline succeeded — and because the sentence it replaces
  // ("No figure is drawn in this session") was false on this surface.
  const hint = $("#figure-click-hint");
  const cells = $("#figure").querySelectorAll('[id^="cell|"]').length;
  if (hint && cells) {
    hint.classList.remove("unavailable");
    hint.textContent = "Click a gene label or any of its cells to open the "
      + "drawer — the verdict, the summary and the phenotype models, read from "
      + "this run's own files.";
  }
}

// v4.0. The controls, filled in with what this run was actually asked.
//
// The brief: "Disease field pre-filled with Crohn's disease, chosen contrast
// shown". The point is not decoration — it is that the figure below did not
// appear from nowhere. A reader seeing the disease, the contrast, the region and
// the gene order that produced the circle can tell what the tool takes as input,
// which is the one thing a static page otherwise cannot show.
//
// Every value comes from the RUN'S OWN MANIFEST, never from a constant here.
// Hard-coding "Crohn's disease" in the page would be a label that stops being
// true the first time `DEMO_FEATURED_RUN` points at another disease — a
// plausible wrong figure made out of HTML.
//
// The controls stay live. Typing in the box, or pressing Draw the figure, goes
// to the server and comes back as `DEMO_REFUSAL` in the page's own error
// surface; that refusal is what this box is, said by the thing that enforces it.
async function showToolInputs(runId) {
  let m;
  try {
    m = await fetch(`/api/run/${encodeURIComponent(runId)}/manifest.json`)
      .then((x) => (x.ok ? x.json() : null));
  } catch (e) { return; }
  if (!m) return;
  const c = m.contrast;
  const box = $("#disease");
  // The disease as the run recorded it, in the order the manifest is most
  // specific. `contrast.name` is built by `ContrastRef` as `'<test>' vs
  // '<reference>' in '<factor>'`, so the clause before `' vs '` is the test
  // group's own disease name -- where `test_label` is the Atlas group label and
  // carries the classification clause with it ("A1a Paris classification;
  // Crohn's disease").
  //
  // Split on the SEPARATOR, not on the quotes. A `/'([^']+)'/` match reads
  // "Crohn" out of "'Crohn's disease' vs ..." because the apostrophe in the
  // disease's own name closes the group — and a disease field that says "Crohn"
  // is a wrong label on a correct figure, which is the class of defect this
  // build exists to refuse. Found by the UI audit, 2026-10-19.
  //
  // Parsed from the manifest rather than written here: a literal would stop
  // being true the first time `DEMO_FEATURED_RUN` names another disease.
  const name = (c && c.name) || "";
  const head = name.includes("' vs '") ? name.slice(0, name.indexOf("' vs '")) : "";
  const disease = m.disease_label || m.disease
    || head.replace(/^'/, "") || (c ? c.test_label : "");
  if (box && disease) box.value = disease;
  // Heading, comparison, provenance, sign — the four layers, from the one
  // builder (`geneExpressionNoteHtml`). `#disease-chosen` lives inside `#find`,
  // which the published page removes now that these facts sit above the grid
  // columns they belong to, so the element is looked up rather than assumed.
  const chosen = $("#disease-chosen");
  if (c && chosen) {
    chosen.hidden = false;
    chosen.innerHTML = geneExpressionNoteHtml(c);
  }
  // The region select shows the tissue the ring was actually read against, and
  // `#region-inferred` the chooser's sentence if that is how it was decided --
  // the same note the live path draws, for the same reason: a reader who did not
  // pick the tissue will not notice it was picked unless it is said.
  const region = m.region || "";
  const sel = $("#region");
  if (sel && region) {
    if (![...sel.options].some((o) => o.value === region || o.text === region)) {
      sel.add(new Option(region, region));
    }
    sel.value = region;
  }
  const inferred = (m.run_conditions || [])
    .map((x) => x.condition || "")
    .filter((s) => s.toLowerCase().includes("inferred from the disease"));
  if (inferred.length) {
    $("#region-inferred").hidden = false;
    $("#region-inferred").textContent = inferred.join(" ");
  }
  // And the gene order, which is rule 7's "decided, stated on the figure and
  // checked in the artefact" -- so the control says the same thing the figure's
  // own footer does.
  const ord = $("#ordering");
  if (ord && m.ordering) {
    const hit = [...ord.options].find((o) => o.value === m.ordering);
    if (hit) ord.value = m.ordering;
  }
}

// Every gene symbol this SVG drew a spoke label for. The same pattern
// `wireFigure` matches with, applied once so the two cannot disagree about what
// a label looks like — the chromosome suffix (v4.1, `NOD2 · chr16`) sits between
// the symbol and the `(k=` that anchors it, and this is the regex that has to
// know that.
function spokeGenesInSvg() {
  const out = [];
  $("#figure").querySelectorAll("text").forEach((t) => {
    const m = (t.textContent || "").match(SPOKE_LABEL_RE);
    if (m) out.push(m[1]);
  });
  return out;
}

// A box with a border and nothing in it reads as a broken panel, and two of
// them were the finding in Puck's screenshot. The rule is one line: if it has no
// text, it is not drawn.
//
// The wedge note is deliberately NOT in this list, and is deliberately not
// named here either. It has exactly one producer — `drawFigureCaveats`, which
// hides it when the render gave it no `where` clause — and
// `test_it_is_drawn_once_and_the_strip_below_no_longer_has_it` enforces that by
// counting the places in this file that name its id at all, for the reason
// round 2's A6 exists. Its empty case is handled in CSS instead, by `:empty`,
// which adds no second producer and no second mention.
function hideEmptyDecodeBoxes() {
  ["#cell-states", "#figure-caveats", "#scales"].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    const inner = sel === "#scales" ? $("#scale-rows") : el;
    el.hidden = !(inner && inner.textContent.trim());
  });
}

// The region the app falls back to, captured at init so a disease with no
// inference can be reset to it rather than silently keeping the PREVIOUS
// disease's tissue — which would draw one disease's specificity ring against
// another disease's organ.
let DEFAULT_REGION = "";
let REGION_WAS_INFERRED = false;
//: Whether the reader has touched the region dropdown at all. A contrast run
//: posts a region only when somebody -- the reader or the chooser -- actually
//: chose one; an untouched default is posted as EMPTY, and the server infers
//: the disease's tissue or refuses with "choose a region". The four Crohn's
//: demo runs were judged "expressed in Lung" because the form's default went
//: through unnoticed (2026-10-18 walkthrough, item a).
let REGION_TOUCHED = false;
//: The disease the in-flight region request is for. Two diseases chosen in
//: quick succession leave two requests outstanding, and without this the slower
//: one wins — setting the first disease's tissue over the second's.
let REGION_ASKED_FOR = "";
//: The model's sentence for the inferred region, sent with the run so the
//: FIGURE says the tissue was inferred and can be changed — a screenshot
//: cannot show that a dropdown was filled in for the reader.
let REGION_REASON = "";

// Which GTEx tissue to read the specificity ring against, inferred from the
// disease and cached server-side (`region_llm`). Best-effort and never
// blocking: the select keeps whatever it had if this fails, and the figure
// names the region it used either way.
//
// It SETS the control rather than bypassing it, so there is one region in the
// app and the user can override it with the same dropdown they always had.
async function inferRegion(label) {
  const box = $("#region-inferred");
  const sel = $("#region");
  if (!box || !sel || !label) return;
  REGION_ASKED_FOR = label;
  let r;
  try {
    r = await fetch("/api/region-for?disease=" + encodeURIComponent(label))
      .then((x) => x.json());
  } catch (e) { return; }
  if (!r || !r.tissue) {
    // No inference is not an error — the default stands and the figure says
    // which region it used. Nothing is claimed here.
    clearInferredRegion();
    return;
  }
  // A region the user chose by hand is theirs. `loadContrasts` runs again on a
  // sort change and on stepping back, and re-setting the select each time
  // silently reverted their choice.
  if (sel.value !== DEFAULT_REGION && !REGION_WAS_INFERRED) return;
  // Two diseases picked quickly leave two requests in flight; the slower one
  // must not land on top of the newer disease's region.
  if (label !== REGION_ASKED_FOR) return;
  const match = [...sel.options].find((o) => o.value === r.tissue);
  if (!match) {
    // The panel the select was built from and the panel the chooser was given
    // disagree. Refuse rather than force a value the dropdown does not have.
    clearInferredRegion();
    return;
  }
  sel.value = r.tissue;
  REGION_WAS_INFERRED = true;
  REGION_REASON = r.reason || "";
  box.hidden = false;
  box.innerHTML =
    `<b>Region set to <code>${esc(r.tissue)}</code> for this disease.</b><br>
     <span class="hint">${esc(r.reason)}${
       r.cached ? " (remembered from an earlier run on "
                  + esc((r.chosen_utc || "").slice(0, 10)) + ")" : ""}
     — change it above if that is the wrong tissue.</span>`;
}

// Drop an inference made for a PREVIOUS disease. Only resets the select if we
// were the ones who set it: a region the user chose by hand is theirs, and
// clobbering it would be this function deciding it knows better.
function clearInferredRegion() {
  const box = $("#region-inferred");
  if (box) box.hidden = true;
  if (REGION_WAS_INFERRED && DEFAULT_REGION) {
    const sel = $("#region");
    if (sel) sel.value = DEFAULT_REGION;
  }
  REGION_WAS_INFERRED = false;
  REGION_REASON = "";
}

async function loadContrasts(d, _ix) {
  leaveLanding();
  PICKER.disease = d;
  inferRegion(d.label || d.current_id);
  const panel = $("#contrast-panel");
  panel.hidden = false;
  panel.innerHTML = `<p class="hint">loading contrasts for ${esc(d.current_id)}…</p>`;
  const r = await fetch("/api/contrasts?disease=" + encodeURIComponent(d.current_id) +
                        (PICKER.sort ? "&sort=" + encodeURIComponent(PICKER.sort) : ""));
  const b = await r.json();
  if (!r.ok) {
    panel.innerHTML = `<div class="note bad"><b>${esc(b.error || "failed")}</b><br>
      ${esc(errText(b, ""))}<br><span class="hint">${esc(b.note || "")}</span></div>`;
    return;
  }
  // Render the listing NOW, and let the pick arrive when it arrives.
  //
  // Measured 2026-10-09: `/api/contrasts` answers in 0.01s and `/api/autopick`
  // takes 7.5s on a disease it has not seen (0.01s cached, but a first visit is
  // the one that matters). Awaiting the pick before rendering meant 7.5 seconds
  // of blank panel with the whole table already in hand — the distinct slowness
  // of a page that waits on a model for something it does not need yet.
  //
  // A SEPARATE request, not a field on the listing. `picker_payload` stays
  // neutral and its test still asserts no `selected` key exists — the listing
  // describes, it does not bless. A failure is not fatal either: the table is
  // the thing that always works, so the pick is best-effort over it.
  renderContrasts(b, null);
  const asked = d.current_id;
  try {
    const pr = await fetch("/api/autopick?disease=" + encodeURIComponent(asked));
    const pb = await pr.json();
    // Two diseases picked quickly leave two picks in flight; the slower one
    // must not land on the newer disease's listing.
    if (pr.ok && PICKER.disease && PICKER.disease.current_id === asked) {
      renderContrasts(b, pb);
    }
  } catch (e) { /* the listing stands on its own */ }
}

// The chosen contrast, as the first thing on the panel.
//
// `docs/DECISION-AUTOPICK-2026-10-09.md`: the app chooses by default and the
// 138-row table moves behind a disclosure. Everything the reader would have
// seen while choosing is still here — the rule as a sentence, how many
// candidates there were, why the rest were not eligible, and what the control
// side actually is — one click away rather than in the way.
function pickedContrastHtml(picked) {
  if (!picked || !picked.chosen) return "";
  const p = picked.chosen;
  const n = picked.n_considered;
  return `<div class="note picked">
    <p class="picked-line"><b>Gene expression</b> — chosen for you</p>
    <p class="picked-line">Differential expression:
      ${esc(p.test_label)} <b>(${p.n_test})</b> vs
      ${esc(p.ref_label)} <b>(${p.n_ref})</b></p>
    <p class="hint">${provenanceLine(p)}</p>
    <p class="hint">${esc(picked.reason)}.</p>
    ${picked.control_caveat
      ? `<p class="hint"><b>The control side:</b> ${esc(picked.control_caveat)}.</p>`
      : ""}
    <div class="row">
      <button id="use-picked" class="primary">
        Use this comparison — step 3: the gene table →</button>
      <button id="show-all-contrasts" class="ghost">choose a different one</button>
    </div>
    <details class="ev-why"><summary>how this was chosen</summary>
      <p class="reason">${esc(picked.stated_rule)}.</p>
      ${/* Who ACTUALLY chose. A fallback is honest only if the reader is told
            it happened: showing a model's name over a rule's choice would be a
            wrong claim about the figure's own provenance, which is the defect
            this build spends most of its effort on. */""}
      ${picked.fell_back_from
        ? `<p class="hint"><b>This fell back to the rule above.</b>
           Asked first — ${esc(picked.fell_back_from)}</p>` : ""}
      <p class="hint">${n} contrast(s) were eligible;
        ${picked.excluded.length} were not —
        ${esc([...new Set(picked.excluded.map((e) => e.excluded_reason))].join(" "))}</p>
    </details></div>`;
}

function renderContrasts(b, picked) {
  const f = b.flag_counts;
  const c = b.counts;
  // With a pick on screen the full table is collapsed; without one it is the
  // only way forward and stays open.
  // Open while the pick is pending (`null`) or absent, closed once one lands.
  // Collapsing the table before the pick arrives would leave a reader with
  // neither for as long as the call takes.
  const open = picked && picked.chosen ? "" : " open";
  $("#contrast-panel").innerHTML = `
    <h2>Gene expression</h2>
    <p class="sub-head">Differential expression — which comparison to draw</p>
    <p class="picker-head"><b>${esc(c.count_text)}</b> · ${esc(c.built_text)}</p>
    ${picked === null
      ? `<p class="hint" id="pick-pending">Choosing a comparison for you…
         the full list is below and works now.</p>`
      : pickedContrastHtml(picked)}
    <details id="all-contrasts"${open}>
    <summary><b>All ${b.rows.length} comparisons</b> — choose a different one,
      or check how the choice above was made</summary>
    <div class="reveal">
    <div class="note ${c.availability === "none_supported" ? "warn" : ""}"
      ><b>What this app excludes:</b> ${esc(b.excludes)}</div>
    ${b.dominance ? `<div class="note warn"><b>${esc(b.dominance.text)}</b> —
      these rows are not independent evidence.</div>` : ""}
    <p class="hint">${esc(b.row_unit)}<br><b>${esc(b.no_default)}</b></p>
    <div class="row">
      <label for="picker-sort">Order</label>
      <select id="picker-sort">${b.sorts.map((s) =>
        `<option value="${esc(s.value)}" ${s.value === b.order ? "selected" : ""}>${
          esc(s.label)}</option>`).join("")}</select>
      <span class="hint">shown: ${esc(b.order_text)}. One column at a time — a
        composite order over n and platform would be a ranking of contrasts.</span>
    </div>
    <p class="hint">Flagged: ${f.n_under_8_total} with a side under n=8
      (${f.n_lt_4} of them under n=4), ${f.microarray} microarray,
      ${f.invalid_id} with an id that is not <code>{reference}_{test}</code>,
      ${f.unannotated} with assays missing from the condensed SDRF.
      Every one of those rows is still in the table below.</p>
    ${/* What the rows COMPARE, which is the question a reader arrives with and
          had to re-derive from two label strings on every row. Counts, not a
          pick — `picker_payload` refuses to name a default and still does. */""}
    <p class="hint">Compares: ${
      Object.entries(b.comparison_counts || {})
        .filter(([, v]) => v.total)
        .map(([k, v]) => `<b>${v.total}</b> ${esc(b.comparison_labels[k])}${
          v.drawable === v.total ? "" : ` (${v.drawable} drawable)`}`)
        .join(" · ")}.
      Classified from the disease ids on each side, not from the labels.</p>
    <div class="scroll-x"><table class="picker"><thead><tr>
      <th>accession</th><th>contrast</th><th>test (n)</th><th>reference (n)</th>
      <th>tissue</th><th>platform</th><th>compares</th><th>drawable?</th><th>flags</th>
    </tr></thead><tbody>${b.rows.map((r, i) => contrastRowHtml(r, i)).join("")}
    </tbody></table></div>
    <div id="contrast-detail"></div>
    </div></details>`;
  $("#picker-sort").onchange = (e) => {
    PICKER.sort = e.target.value;
    loadContrasts(PICKER.disease, b.index);
  };
  b.rows.forEach((r, i) => {
    const el = $(`#crow-${i}`);
    if (el) el.onclick = () => showContrast(r);
  });

  // The chosen contrast goes through `selectContrast` like any other, from the
  // row the listing already carries — so the pick cannot send a `ContrastRef`
  // the picker never built, and step 3 behaves identically either way.
  if (picked && picked.chosen) {
    const match = b.rows.find((r) => r.accession === picked.chosen.accession
                                  && r.contrast_id === picked.chosen.contrast_id);
    const use = $("#use-picked");
    if (use && match) use.onclick = () => selectContrast(match);
    else if (use) {
      // The listing and the pick disagree about what exists. Refuse rather than
      // proceed: they are built from the same index, so this means one of them
      // is stale.
      use.disabled = true;
      use.textContent = "the chosen contrast is not in this listing";
    }
    const show = $("#show-all-contrasts");
    if (show) show.onclick = () => {
      const d = $("#all-contrasts");
      d.open = true;
      d.scrollIntoView({ block: "start" });
    };
  }
}

function contrastRowHtml(r, i) {
  const cls = [r.drawable ? "" : "greyed",
               r.flag_keys.includes("invalid_id") ? "invalid" : ""].join(" ");
  const tissue = r.tissue.length ? esc(r.tissue.join(", "))
    : `<span class="hint">not annotated</span>`;
  const drawable = r.drawable
    ? `<span class="pill ok">yes</span>`
    : `<span class="pill ${r.flag_keys.includes("invalid_id") ? "bad" : "grey"}">no</span>`;
  return `<tr id="crow-${i}" class="${cls}">
    <td><code>${esc(r.accession)}</code></td>
    <td><code>${esc(r.contrast_id)}</code></td>
    <td>${esc(r.test_label || "—")} <b>(${r.n_test})</b></td>
    <td>${esc(r.ref_label || "—")} <b>(${r.n_ref})</b></td>
    <td>${tissue}</td>
    <td>${r.is_rnaseq ? "RNA-seq" : `<code>${esc(r.platform)}</code>`}</td>
    ${/* Neutral styling on purpose. A within-disease contrast is not a worse
          contrast, it is a different question — drawing it as a warning would
          say this app disapproves of inflamed-vs-adjacent-normal, which it does
          not. The pill states what the row compares and stops there. */""}
    <td><span class="pill ${r.comparison === "case_vs_unaffected" ? "ok" : "grey"}">${
      esc(r.comparison === "case_vs_unaffected" ? "case / control"
          : r.comparison === "within_one_disease" ? "within disease"
          : r.comparison === "disease_vs_disease" ? "disease / disease"
          : "unannotated")}</span></td>
    <td>${drawable}</td>
    <td>${r.flags.map((x) =>
      `<span class="flag ${esc(x.key)}">${esc(x.label)}</span>`).join(" ")}</td>
  </tr>`;
}

// Clicking a row shows the whole `ContrastRef` and the sign sentence BEFORE any
// statistic is fetched: the sign is the one thing about a contrast that is a
// single token away from a plausible wrong figure, so it is read first.
function showContrast(r) {
  const ref = r.contrast_ref;
  // `ensembl_release` and `pipeline` are empty on an index-built ref: the index
  // is two GETs per experiment and `-analysis-methods.tsv` would be a third, for
  // two strings that only matter once a contrast is drawn. An empty cell would
  // read as "this contrast has no pipeline", so the reason is printed instead.
  const NOT_YET = {
    ensembl_release: "not fetched yet — stage 3 reads -analysis-methods.tsv",
    pipeline: "not fetched yet — stage 3 reads -analysis-methods.tsv",
  };
  const rows = ref
    ? Object.entries(ref).filter(([k]) => k !== "sign_sentence")
        .map(([k, v]) => `<tr><td><code>${esc(k)}</code></td><td>${
          (v === "" || v === null || v === undefined)
            ? `<span class="hint">${esc(NOT_YET[k] || "not stated by this source")}</span>`
            : esc(v)}</td></tr>`)
        .join("")
    : "";
  // A `<p>`, not an `<h2>`: `h2` is uppercased by the stylesheet and `g2_g1`
  // uppercased is `G2_G1`, which is not this contrast's id. An identifier printed
  // in the wrong case is a small version of the thing this whole panel exists to
  // prevent.
  $("#contrast-detail").innerHTML = `
    <p class="detail-head">Gene expression</p>
    <p class="prose-name">Differential expression: ${
      esc(plainComparison(r.name) || r.name
          || "(this experiment states no prose name)")}</p>
    <p class="hint">Expression Atlas <code>${esc(r.accession)}</code> ·
      contrast <code>${esc(r.contrast_id)}</code></p>
    ${r.sign_sentence
      ? `<div class="note"><b>${esc(r.sign_sentence)}</b><br>
         The contrast id is <code>{reference}_{test}</code>, so
         <code>${esc(r.contrast_id)}</code> means reference
         <code>${esc(ref.ref_group)}</code> and test
         <code>${esc(ref.test_group)}</code>. Reading it left to right would invert
         every log2 fold change.</div>`
      : `<div class="note bad"><b>The sign of this contrast is unestablished.</b>
         ${esc(r.flags.map((f) => f.reason).join(" "))} No figure is drawn from it.
         </div>`}
    ${r.drawable ? "" : `<p class="hint"><b>Not drawable:</b> ${
      esc(r.not_drawable_reason)}</p>`}
    ${rows ? `<table class="kvtable"><tbody>${rows}</tbody></table>` : ""}
    <p class="hint">No statistic has been fetched for this contrast yet.
      <a href="${esc(r.configuration_url)}" target="_blank" rel="noopener">its
      <code>-configuration.xml</code></a> is where every string above comes from.</p>
    ${r.drawable
      ? `<div class="row"><button id="use-contrast" class="primary">
           Use this comparison — step 3: the gene table →</button></div>`
      : ""}`;
  if (r.drawable) $("#use-contrast").onclick = () => selectContrast(r);
  $("#contrast-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------- stage 3/4, PLAN §9: the stepped flow's steps 3 and 4 ----------
//
// One rule ties both steps to the figure they eventually feed: every
// collapsed line below is built from the SAME numbers written into the
// gene-table / heatmap responses, never a hand-typed paraphrase of them, so a
// mismatch between a collapsed step and the artefact is a bug rather than a
// styling choice (§9).
//
// The other rule is invalidation: picking a different contrast (or a new
// disease) must clear GENE_TABLE and SELECTED entirely. `selectContrast` is
// the only entry point into step 3, and it always starts from a blank slate.

let GENE_TABLE = null;     // the /genes response for the current contrast
let TERSE_TABLE = true;    // PLAN §10: terse from the start, not bolted on
let SELECTED = new Set();  // ensembl ids checked in step 4

// PLAN §9: "changing step N explicitly invalidates N+1 onward — the later
// steps clear and say so." Called with 3 from a changed disease or contrast
// (clears the gene table and the heatmap/selection); the gene-table step
// itself clears SELECTED without needing this (a new table is a new set of
// valid ids).
// §1 applies to the paste path and to nothing else, so it is shown on the paste
// path and nowhere else.
//
// THE DEFECT THIS CLOSES. The page had two "go" buttons. `makeSpokes` (step 4)
// posts `source=atlas` with the contrast's own gene ids; `#go` posts
// `source=paste` with whatever is in the textarea. They sit a few hundred
// pixels apart, and §1 stayed open and inviting through the whole Atlas flow —
// so after picking a Crohn's contrast and selecting genes, clicking "Draw the
// figure" discarded the contrast and drew the textarea instead. With the
// example still loaded from `load example` that returns 200 and produces a
// complete, internally consistent figure of BRAF/EGFR/TP53 while the reader
// believes they are looking at their contrast. `AGENTS.md:17`, from a button.
//
// Hidden rather than disabled: a disabled control still says "this is part of
// your route". Hidden rather than populated with the contrast's own logFC and
// padj, because a filled textarea reads as editable and an edited value would
// draw a figure whose provenance says Expression Atlas over numbers that were
// typed — the lie `sources.DESource` exists to stop.
//
// Sections 2-4 stay: `makeSpokes` genuinely reads the region, the gene order
// and the filters from them.
function syncPasteSection() {
  const t = GENE_TABLE;
  const driving = !!(t && t.contrast);
  const sec = $("#paste-section");
  const note = $("#paste-superseded");
  const go = $("#go");
  if (sec) sec.hidden = driving;
  if (go) go.hidden = driving;
  if (!note) return;
  note.hidden = !driving;
  if (!driving) { note.innerHTML = ""; return; }
  // Says where the genes ARE coming from and which control draws them. A
  // section that silently vanishes reads as a bug; one that says why it is
  // gone reads as the app knowing what route you are on.
  note.innerHTML =
    `<b>1. Differential expression table — not used on this path.</b><br>
     Your genes come from <code>${esc(t.contrast.accession)} ${
       esc(t.contrast.contrast_id)}</code>, selected in step 3 above, and the
     figure is drawn with <b>Make spokes</b> in step 4. The region, the gene
     order and the filters below still apply.`;
}

function invalidateFromStep(n) {
  if (n <= 3) {
    GENE_TABLE = null;
    SELECTED = new Set();
    const p3 = $("#gene-table-panel");
    if (p3) { p3.hidden = true; p3.innerHTML = ""; }
  }
  const p4 = $("#heatmap-panel");
  if (p4) { p4.hidden = true; p4.innerHTML = ""; }
  syncPasteSection();
}

function collapsedContrastLine(r) {
  // One line, so the three layers run inline: what it is, what was compared,
  // where it came from. `contrast` appears only against the id.
  const comparison = plainComparison(r.name) || r.name;
  return `Differential expression: ${esc(comparison)} · ` +
    `Expression Atlas ${esc(r.accession)} · contrast ${esc(r.contrast_id)} · ` +
    `n=${r.n_test} vs ${r.n_ref} · ${r.is_rnaseq ? "RNA-seq" : esc(r.platform)}`;
}

function selectContrast(r) {
  // Invalidation: a fresh step 3/4 every time, even if this is the same
  // contrast clicked twice.
  GENE_TABLE = null;
  SELECTED = new Set();
  $("#heatmap-panel").hidden = true;
  $("#heatmap-panel").innerHTML = "";
  syncPasteSection();

  // Step 2 collapses to one line, generated from the same `ContrastRef` the
  // gene table and the figure will use.
  $("#contrast-panel").innerHTML = `
    <p class="step-line"><b>2</b> ${collapsedContrastLine(r)}
      <button class="linkish" id="change-contrast">[change]</button></p>`;
  $("#change-contrast").onclick = () => {
    GENE_TABLE = null;
    SELECTED = new Set();
    $("#gene-table-panel").hidden = true;
    $("#heatmap-panel").hidden = true;
    syncPasteSection();
    loadContrasts(PICKER.disease);
  };
  loadGeneTable(r);
}

async function loadGeneTable(r) {
  const panel = $("#gene-table-panel");
  panel.hidden = false;
  panel.innerHTML = `<p class="hint">loading the gene table for
    ${esc(r.accession)} ${esc(r.contrast_id)}…</p>`;
  const url = `/api/contrast/${encodeURIComponent(r.accession)}/` +
    `${encodeURIComponent(r.contrast_id)}/genes?n=60`;
  let body;
  try {
    const res = await fetch(url);
    body = await res.json();
    if (!res.ok) {
      panel.innerHTML = `<div class="note bad"><b>${esc(body.error || "failed")}</b><br>
        ${esc(errText(body, ""))}</div>`;
      return;
    }
  } catch (e) {
    panel.innerHTML = `<div class="note bad">gene table request failed: ${esc(String(e))}</div>`;
    return;
  }
  GENE_TABLE = body;
  // Everything the server already selected, preselected.
  //
  // Not a recommendation and not a pick among alternatives: the selection
  // happened server-side in `contrast.top_n_by_padj`, whose own docstring calls
  // it "Rule 1 as a sort, rule 7's one permitted rank-based selection". Taking
  // the whole of that set is the opposite of curating it — ticking a subset is
  // the judgement, and that stays exactly where it was.
  //
  // Starting empty meant re-entering by hand a decision the server had just
  // made, up to 60 checkboxes at a time, with a "select none" button and no
  // "select all". The runs on disk show what that produced in practice: 12
  // ticked of 12 shown, then 14 of 14.
  SELECTED = new Set((body.rows || []).map((r) => r.ensembl_id).filter(Boolean));
  renderGeneTable();
  syncPasteSection();
}

// PLAN §10: stays in every mode vs goes in terse mode. Nothing here combines
// two metrics into one column and there is no "overall" sort — logFC and
// adjusted p are each sortable only on their own single-source value (rule 7).
function renderGeneTable() {
  const t = GENE_TABLE;
  const panel = $("#gene-table-panel");
  const acc = t.accounting;
  const rows = t.rows;
  const sortKey = renderGeneTable.sortKey || "pvalue";
  const sorted = [...rows].sort((a, b) => {
    if (sortKey === "pvalue") return a.pvalue - b.pvalue;
    if (sortKey === "log2fc") return Math.abs(b.log2fc) - Math.abs(a.log2fc);
    return 0;
  });

  const verboseProse = TERSE_TABLE ? "" : `
    <p class="hint">Each row is one gene in this CONTRAST's own
      <code>-analytics.tsv</code>, resolved through the same identifier
      resolver a pasted id would go through (rule 5). <code>no_match</code> and
      <code>ambiguous</code> are real states, never blank. Selection is
      ${esc(acc.n_top)} of ${esc(acc.n_with_statistic)} genes that carry BOTH a
      p-value and a log2FC — never a fallback to log2FC alone, which would
      surface exactly the ${esc(acc.n_without_statistic)} genes DESeq2's own
      independent filtering already excluded (rule 1 as a sort).</p>`;

  panel.innerHTML = `
    <h2>3. Gene table</h2>
    <p class="step-line"><b>${esc(t.selection_rule_line)}</b> ·
      n=${t.contrast.n_test} vs ${t.contrast.n_ref} ·
      ${t.contrast.is_rnaseq ? "RNA-seq" : esc(t.contrast.platform)} ·
      Ensembl ${esc(t.contrast.ensembl_release || "?")}</p>
    <p class="hint"><i>${esc(t.sign_sentence)}</i></p>
    ${verboseProse}
    <div class="row">
      <label><input type="checkbox" id="terse-toggle" ${TERSE_TABLE ? "checked" : ""}>
        terse mode</label>
      <span class="hint">top genes, easy metrics, no explanatory prose —
        the load-bearing facts (contrast, n, sign) never leave, whichever mode.</span>
    </div>
    <div class="scroll-x"><table class="picker gene-table"><thead><tr>
      <th>gene</th>
      <th class="sortable" data-sort="log2fc">logFC</th>
      <th class="sortable" data-sort="pvalue">adj p</th>
      ${TERSE_TABLE ? "" : "<th>resolution</th>"}
      <th>select</th>
    </tr></thead><tbody>${sorted.map((row) => geneRowHtml(row)).join("")}
    </tbody></table></div>
    <p class="hint">${esc(acc.n_top)} shown ·
      <span id="sel-count">${SELECTED.size}</span> selected —
      all of them, by this contrast's own adjusted p; untick to narrow ·
      <button class="ghost" id="select-all">select all</button>
      <button class="ghost" id="select-none">select none</button></p>
    <div class="row">
      <button id="continue-heatmap" class="primary">
        Continue — step 4: heatmap &amp; selection →</button>
    </div>`;

  $("#terse-toggle").onchange = (e) => { TERSE_TABLE = e.target.checked; renderGeneTable(); };
  $("#select-none").onclick = () => { SELECTED = new Set(); renderGeneTable(); syncHeatmapCount(); };
  // The counterpart to "select none", and the way back after narrowing. Over
  // the shown rows only — it can never add an id outside the top-N the server
  // selected, which is what `/api/analyse` checks on arrival anyway
  // (`selection_outside_top_n`).
  $("#select-all").onclick = () => {
    SELECTED = new Set((GENE_TABLE.rows || []).map((r) => r.ensembl_id).filter(Boolean));
    renderGeneTable(); syncHeatmapCount();
  };
  panel.querySelectorAll("th.sortable").forEach((th) => {
    th.onclick = () => { renderGeneTable.sortKey = th.dataset.sort; renderGeneTable(); };
  });
  sorted.forEach((row) => {
    const cb = $(`#gsel-${cssId(row.ensembl_id)}`);
    if (cb) cb.onchange = () => {
      if (cb.checked) SELECTED.add(row.ensembl_id); else SELECTED.delete(row.ensembl_id);
      $("#sel-count").textContent = SELECTED.size;
      syncHeatmapCount();
    };
  });
  $("#continue-heatmap").onclick = () => loadHeatmapStep();
}

function cssId(ensemblId) { return ensemblId.replace(/[^A-Za-z0-9]/g, "_"); }

function geneRowHtml(row) {
  // Rule 5: never the literal "nan". `no_match`/`ambiguous` are states shown
  // as themselves, not blanks (§10's "real cell states" requirement).
  // v4.2. The adj p cell is `fmtP` — `<.0001` rather than `2.2e-22`. The SORT
  // is unchanged and still compares `row.pvalue`, the number
  // (`renderGeneTable`'s comparator), so the rows this collapses to one string
  // are still ordered by the p they were ordered by before.
  const stateNote = row.resolution_status === "ok" || row.resolution_status === "renamed"
    ? "" : `<span class="pill ${row.resolution_status === "ambiguous" ? "warn" : "bad"}">
        ${esc(row.resolution_status)}</span>`;
  return `<tr>
    <td><code>${esc(row.display_symbol)}</code> ${stateNote}
      ${TERSE_TABLE ? "" : `<br><span class="hint">${esc(row.ensembl_id)}</span>`}</td>
    <td>${row.log2fc >= 0 ? "+" : ""}${row.log2fc.toFixed(2)}</td>
    <td>${fmtP(row.pvalue)}</td>
    ${TERSE_TABLE ? "" : `<td class="hint">${esc(row.resolution_note || row.resolution_status)}</td>`}
    <td><input type="checkbox" id="gsel-${cssId(row.ensembl_id)}"
        ${SELECTED.has(row.ensembl_id) ? "checked" : ""}></td>
  </tr>`;
}

function syncHeatmapCount() {
  const el = $("#make-spokes-count");
  if (el) el.textContent = SELECTED.size;
  const btn = $("#make-spokes");
  if (btn) btn.disabled = SELECTED.size === 0;
  const label = $("#make-spokes-label");
  if (label) label.textContent = SELECTED.size
    ? `— send ${SELECTED.size} gene(s) to the circos`
    : "— select at least one gene";
}

// Step 3 collapses; step 4 shows the heatmap and the selection UI. Selecting
// nothing is a refusal, not an empty figure (stage 4) — `#make-spokes` stays
// disabled at zero, it is never silently allowed to submit an empty set.
function loadHeatmapStep() {
  const t = GENE_TABLE;
  $("#gene-table-panel").innerHTML = `
    <p class="step-line"><b>3</b> ${esc(t.selection_rule_line)} ·
      ${esc(t.accounting.n_with_statistic)} of ${esc(t.accounting.n_total)} had a statistic
      <button class="linkish" id="change-gene-table">[change]</button></p>`;
  $("#change-gene-table").onclick = () => {
    $("#heatmap-panel").hidden = true;
    SELECTED = new Set();
    renderGeneTable();
  };

  const panel = $("#heatmap-panel");
  panel.hidden = false;
  const heatmapUrl = `/api/contrast/${encodeURIComponent(t.contrast.accession)}/` +
    `${encodeURIComponent(t.contrast.contrast_id)}/heatmap?n=${t.rows.length}`;
  // `export.py` inlines this whole file verbatim into every offline export
  // (`TestNothingIsFetchedAndNothingIsExternal`), which greps the exported
  // text for an image tag carrying a fetchable attribute and refuses to write
  // the file if it finds one -- the export must be provably unable to fetch
  // anything, checked on the source text rather than on which code paths
  // happen to run. So the heatmap picture is never spelled as an HTML tag in
  // a template literal here; it is built with the DOM API below instead.
  panel.innerHTML = `
    <h2>4. Heatmap &amp; selection</h2>
    <p class="hint">Rows are z-scored across the samples shown — block structure is
      guaranteed by construction and is not evidence on its own; the raw-scale
      panel and the per-sample influence strip beside it are what make a
      near-null contrast look different rather than merely say it is.</p>
    <div id="heatmap-img-slot"></div>
    <p class="hint">Selection is the same checkboxes as step 3 —
      <b id="make-spokes-count">${SELECTED.size}</b> gene(s) selected.</p>
    <div class="row">
      <button id="make-spokes" class="primary" disabled>
        Make spokes <span id="make-spokes-label"></span> →</button>
    </div>
    <p id="make-spokes-status" class="hint"></p>`;
  const img = document.createElement("img");
  img.className = "heatmap-img";
  img.alt = `heatmap for ${t.contrast.accession} ${t.contrast.contrast_id}`;
  img.src = heatmapUrl;
  $("#heatmap-img-slot").appendChild(img);
  $("#make-spokes").onclick = () => makeSpokes();
  syncHeatmapCount();
}

async function makeSpokes() {
  const t = GENE_TABLE;
  // v4.2, the same guard as "Draw the figure": with no contrast loaded there is
  // nothing to analyse, and on a page showing a run from disk this read
  // `t.contrast` off null and threw where a figure was already on screen.
  if (!t || !t.contrast) {
    if (DISK_RUN) return redrawLoadedRun(DISK_RUN);
    const s = $("#make-spokes-status");
    if (s) s.textContent = "No comparison is loaded — press “Use this comparison” first.";
    return;
  }
  const btn = $("#make-spokes");
  btn.disabled = true;
  const prog = await startProgress("#make-spokes-status");

  const fd = new FormData();
  fd.append("progress_token", prog.token);
  fd.append("region_inferred", REGION_WAS_INFERRED ? "1" : "");
  fd.append("region_reason", REGION_REASON);
  fd.append("source", "atlas");
  fd.append("accession", t.contrast.accession);
  fd.append("contrast_id", t.contrast.contrast_id);
  fd.append("n", String(t.rows.length));
  fd.append("ensembl_ids", JSON.stringify([...SELECTED]));
  // A region somebody chose -- the reader (REGION_TOUCHED) or the chooser
  // (REGION_WAS_INFERRED, and then the flag and reason above say so). An
  // untouched default is sent empty; the server infers from the disease or
  // refuses. The disease label goes with it for that inference.
  fd.append("region", (REGION_TOUCHED || REGION_WAS_INFERRED) ? $("#region").value : "");
  fd.append("disease", (PICKER.disease && (PICKER.disease.label || PICKER.disease.current_id)) || "");
  fd.append("ordering", $("#ordering").value);
  const drop = $("#drop-no-data").checked;
  fd.append("filters", (($("#filters").value) || "").split("\n")
    .filter((l) => l.trim())
    .map((l) => l.trim() + (drop ? " drop_no_data" : "")).join("\n"));
  const win = windowMeasurement();
  if (win) { fd.append("panel_px", win[0]); fd.append("viewport_px", win[1]); }

  let res, body;
  try {
    res = await fetch("/api/analyse", { method: "POST", body: fd });
    body = await res.json();
  } catch (e) {
    prog.stop();
    $("#make-spokes-status").textContent = "request failed: " + e;
    btn.disabled = false;
    return;
  }
  prog.stop();
  btn.disabled = false;
  if (!res.ok) {
    $("#make-spokes-status").textContent = errText(body, "failed");
    return;
  }
  // Step 4 collapses too, once the figure exists — the same numbers, read
  // back from the manifest the run just wrote rather than re-typed.
  $("#make-spokes-status").textContent = "";
  $("#heatmap-panel").innerHTML = `
    <p class="step-line"><b>4</b> ${esc(SELECTED.size)} gene(s) selected —
      figure built below.</p>`;
  RUN = body;
  $("#result").hidden = false;
  render(body);
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------------------
// THE BACK BUTTON (v4.1, Puck: "check the button flow and back buttons")
//
// Until now this page had no history at all. Six tabs and a full-height drawer
// were states of one URL, so the browser's Back button did the only thing it
// could: leave the site. On a phone, where Back is the system gesture, that
// meant a reader who opened a gene drawer and swiped back lost the figure —
// and on the published site landed on the `docs/index.html` redirect stub,
// which reloads the tool from scratch and drops the run.
//
// THE RULES, because getting these wrong is how a Back button half-works:
//
// * Painters never push. `showTab` and `openDrawer` only draw. `goTab` and
//   `goGene` are the user's routes and are the only two that push. A painter
//   that pushed would answer `popstate` with a new entry, which is a Back
//   button that goes forward.
// * One entry per view, not per click. Re-selecting the tab you are on, or
//   re-opening the gene already in the drawer, replaces nothing and pushes
//   nothing — otherwise Back walks a stack of identical views.
// * The drawer is a view, so closing it is Back. `closeDrawer` delegates to
//   `history.back()` when the drawer owns the current entry, which is also
//   what restores the reader's scroll position: the browser kept it, and it
//   does a better job than anything this file could save and reapply.
// * `?run=` is NOT in the hash. It names which run is loaded and survives a
//   reload; the hash names which view of that run is on screen. Mixing them
//   would make a tab switch re-fetch a run.
//
// The hash is `#<tab>` or `#<tab>/<gene>`. Unknown tabs and unknown genes fall
// back to the figure rather than painting a blank pane, because a stale link
// in someone's notes should degrade to the tool, not to nothing.
// ---------------------------------------------------------------------------
// v4.2, Puck: "the table should be default then the 2nd tab should just be
// circlos". The grid is the landing view and the default hash, so a bare
// `#` or an unknown tab lands on the numbers rather than on the circle.
const TABS = ["table", "figure", "ledger", "evidence", "invariants", "caveats"];
const DEFAULT_TAB = "table";

function currentView() {
  const [tab, gene] = decodeURIComponent(location.hash.slice(1)).split("/");
  return {tab: TABS.includes(tab) ? tab : DEFAULT_TAB, gene: gene || null};
}

function hashFor(tab, gene) {
  return "#" + encodeURIComponent(tab) + (gene ? "/" + encodeURIComponent(gene) : "");
}

// `popstate` and the opening paint both land here. It paints, and it is the
// one function in this block that must never push.
function applyHash() {
  const v = currentView();
  showTab(v.tab);
  if (v.gene) openDrawer(v.gene, false);
  else closeDrawerPaint();
}

function goTab(name) {
  // Leaving a gene open while switching tabs would put two views in one entry.
  if (hashFor(name, null) === location.hash) { showTab(name); return; }
  history.pushState({tab: name}, "", hashFor(name, null));
  showTab(name);
  closeDrawerPaint();
}

function goGene(gene) {
  const v = currentView();
  if (v.gene === gene) { openDrawer(gene, false); return; }
  history.pushState({tab: v.tab, gene}, "", hashFor(v.tab, gene));
  openDrawer(gene, false);
}

// Called once at boot, after the figure or table has settled. `replaceState`,
// not `pushState`: the first view must not be a history entry of its own, or
// one Back from a freshly opened tool lands on the tool again and the reader
// has to press it twice to leave.
function startHistory() {
  const v = currentView();
  history.replaceState({tab: v.tab, gene: v.gene}, "", hashFor(v.tab, v.gene));
  if (v.gene) openDrawer(v.gene, false);
}

function showTab(name) {
  // v4.2. The pointer can leave the circle without a `mouseleave` — a tab
  // switch, a drawer opening, a keyboard route — and a tooltip left behind
  // describes a cell that is no longer on screen.
  hideCellTip();
  document.querySelectorAll(".tab").forEach((x) =>
    x.classList.toggle("active", x.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => { p.hidden = true; });
  $("#pane-" + name).hidden = false;
  // Fetched on first view rather than on every render: the grid is the same run
  // the figure is, and a reader who never opens the tab should not pay for it.
  // With no run either way the pane says what to do — an empty grid and no
  // sentence reads as a broken tab.
  if (name === "table") {
    if (RUN && TABLE.run_id !== RUN.run_id) loadTable(RUN.run_id);
    else if (!TABLE.run_id) loadTable("");
  }
  // Same rule for the evidence surface, and for the same reason: it is the run
  // the figure is, a reader who never opens the tab should not pay for it, and
  // with no run either way the pane has to say what to do.
  //
  // The picker's own value is the fallback, and it is not optional: on the
  // `?run=<id>` deep-link path there is no figure in memory, so `RUN` is null
  // and the only thing that knows which run the reader asked for is the input.
  // Without this the tab said "No run loaded" about a run the Table tab beside
  // it was already showing.
  if (name === "evidence") {
    const wanted = (RUN && RUN.run_id) || $("#evidence-run").value || "";
    if (wanted && EVIDENCE.run_id !== wanted) loadEvidence(wanted);
    else if (!EVIDENCE.run_id) loadEvidence("");
  }
}

// ---------- run ----------

let TICKER = null;

// The staged progress panel.
//
// `/api/analyse` is one POST that returns when everything is done, and on a
// warm cache that is 27 seconds behind a single grey sentence. The stages here
// are REAL EVENTS from `run_analysis` over SSE, not a timer: each one appears
// when the work before it actually finished.
//
// The BAR is weighted by measured history (`app.PROGRESS_WEIGHTS`, from a warm
// 26.9s run: exports 68%, render 16%, everything touching a source 3%) and the
// panel says so. An evenly-paced bar would sit at 50% through the two cheapest
// stages and then stall for 19 seconds, which is the exact thing that makes a
// progress bar feel like a lie.
const RUN_STAGES = [
  ["contracts",    "Checking the source contracts"],
  ["resolve",      "Resolving gene identifiers"],
  ["adapters",     "Gathering the evidence rings"],
  ["regression",   "Running the regression check"],
  ["render",       "Drawing the circle"],
  ["checking",     "Checking the invariants"],
  ["audit_render", "Rendering the audit figure"],
  ["web_render",   "Rendering the figure for this window"],
  ["slide",        "Rendering the 16:9 slide"],
  ["legend",       "Rendering the legend"],
  ["exports",      "Writing the downloads"],
];

let PROGRESS_ES = null;

function newToken() {
  return (crypto.randomUUID ? crypto.randomUUID()
                            : String(Math.random()).slice(2)) + "";
}

function renderProgress(doneKeys, currentKey, weights, elapsed, stageElapsed) {
  const total = RUN_STAGES.reduce((a, [k]) => a + (weights[k] || 0), 0) || 1;
  const got = RUN_STAGES.reduce(
    (a, [k]) => a + (doneKeys.has(k) ? (weights[k] || 0) : 0), 0);

  // Ease ACROSS the current stage rather than freezing at its start.
  //
  // Measured: one stage is 14.8s of an 18.8s run, so a bar that only moves on
  // stage boundaries sits dead for 79% of the wait — which reads as hung, the
  // exact thing the stage list was added to fix. This advances within the
  // current stage's own share, asymptotically, so it approaches that stage's
  // end and can never claim the next one has started. The curve is 1-e^-t,
  // which is honest about having no idea when it will finish: it slows down
  // the longer it takes rather than promising a deadline it cannot keep.
  const share = weights[currentKey] || 0;
  const creep = currentKey ? (1 - Math.exp(-stageElapsed / 9)) * share * 0.92 : 0;
  const pct = Math.min(99, Math.round(((got + creep) / total) * 100));

  const rows = RUN_STAGES.map(([k, label]) => {
    const state = doneKeys.has(k) ? "done" : (k === currentKey ? "now" : "todo");
    const mark = state === "done" ? "✓" : "";
    // Seconds on the live stage only: a number that is still moving is the
    // cheapest possible proof that the thing has not hung.
    const secs = state === "now" && stageElapsed >= 3
      ? ` <span class="pg-secs">${stageElapsed}s</span>` : "";
    return `<li class="pg-${state}">${
      state === "now" ? `<span class="pg-spin" aria-hidden="true"></span>` : ""
      }<span class="pg-mark">${mark}</span>${esc(label)}${secs}</li>`;
  }).join("");
  return `<div class="pg">
    <div class="pg-bar"><div class="pg-fill" style="width:${pct}%"></div></div>
    <p class="pg-head"><b>${pct}%</b> · ${elapsed}s elapsed</p>
    <ul class="pg-list">${rows}</ul>
    <p class="hint">Each stage above is a real event — it appears when that work
      finished. The bar is an <b>estimate</b>, weighted by how long each stage
      took on past runs, so it moves unevenly and slows down inside a long
      stage. Which stage dominates depends on the run: drawing the circle on a
      small gene set, writing the downloads on a large one.</p>
  </div>`;
}

// Opens the stream and returns the token to POST with. Resolves as soon as the
// server has the queue, so no stage event can land in the gap before the POST.
function startProgress(slot) {
  const token = newToken();
  const done = new Set();
  let current = "contracts";
  let weights = {};
  const t0 = Date.now();
  let stageStart = Date.now();
  const paint = () => {
    const el = $(slot);
    if (el) el.innerHTML = renderProgress(
      done, current, weights, Math.round((Date.now() - t0) / 1000),
      Math.round((Date.now() - stageStart) / 1000));
  };
  paint();
  // 400ms: fast enough that the eased bar reads as motion rather than
  // as a jump, cheap enough to be invisible next to the work.
  const timer = setInterval(paint, 400);

  return new Promise((resolve) => {
    let settled = false;
    const es = new EventSource(`/api/analyse/progress/${encodeURIComponent(token)}`);
    PROGRESS_ES = es;
    es.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.stage === "open") {
        weights = d.weights || {};
        if (!settled) { settled = true; resolve({ token, stop }); }
        return;
      }
      if (d.stage === "done") { done.add("exports"); paint(); return; }
      done.add(d.stage);
      const next = RUN_STAGES.find(([k]) => !done.has(k));
      current = next ? next[0] : "";
      stageStart = Date.now();
      paint();
    };
    es.onerror = () => { if (!settled) { settled = true; resolve({ token, stop }); } };
    // Clearing the slot is not tidying, it is the point. `stop()` used to end
    // the repaint timer and close the stream and leave the LAST PAINTED FRAME
    // on the page: a part-filled bar with a live-looking spinner on whichever
    // stage was current. Seen live 2026-10-09 on a run that 400'd in under a
    // second — the page sat on "0% · Checking the source contracts" with the
    // real error up in #status, out of view. A progress bar that keeps
    // spinning after the work has stopped is a plausible wrong state of
    // exactly the kind the cardinal rule is about: it asserts, falsely, that
    // something is still happening. Nothing is. Say nothing instead.
    function stop() {
      clearInterval(timer);
      try { es.close(); } catch (e) {}
      PROGRESS_ES = null;
      const el = $(slot);
      if (el) el.innerHTML = "";
    }
    // A server that never opens the stream must not block the run.
    setTimeout(() => { if (!settled) { settled = true; resolve({ token, stop }); } }, 2000);
  });
}

// v4.2. The figure on screen, drawn again from the run's own files.
//
// Puck, 2026-10-19: "draw the figure breaks everything". On a page showing a run
// read off disk, pressing the button cleared `#result`, nulled `RUN` and then
// failed with the paste message, so the figure and all six tabs vanished until
// a reload. The button is the most prominent control on the page and it
// destroyed the thing the page exists to show.
//
// So on that page the button does the nearest REAL thing instead: it re-inlines
// the run's own `figure-web.svg`, resets the view to the default tab and scrolls
// back to it. Nothing is fetched from a source, nothing is computed, nothing is
// cleared — a redraw of what is already there is both honest and what "draw the
// figure" means when the figure is already drawn.
async function redrawLoadedRun(runId) {
  const btn = $("#go");
  if (btn) btn.disabled = true;
  $("#status").className = "";
  $("#status").textContent = "";
  $("#result").hidden = false;
  try {
    await showToolFigure(runId);
  } catch (e) {
    // The run exported no web SVG. The pane keeps whatever it already had —
    // which is the one thing this function must never take away.
  }
  goTab(DEFAULT_TAB);
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  if (btn) btn.disabled = false;
}

// The status line, without clearing the figure. `fail()` hides `#result`, which
// is right when a run was attempted and wrong for a pre-check that happens
// before anything was touched.
function failSoft(msg) {
  $("#status").className = "err";
  $("#status").textContent = msg;
}

async function analyse() {
  const btn = $("#go");

  // THE PRE-CHECK COMES FIRST, before anything on the page is cleared.
  //
  // It used to come after `#result` was hidden and `RUN` nulled, so the one
  // path that cannot produce a figure was also the one path that destroyed the
  // figure already on screen (Puck: "draw the figure breaks everything"). The
  // order is the whole fix: decide whether this button can do anything, and
  // only then start taking the previous answer down.
  //
  // This button draws the PASTED table. On the disease path the genes come
  // from the picked contrast instead and `syncPasteSection` hides this button
  // — but only once the gene table has loaded, and the gene table only loads
  // when the reader clicks "Use this comparison". Between the pick landing and
  // that click, this button is visible, enabled, and posts an empty table.
  // Observed 2026-10-09: the reader scrolled past the chosen contrast, pressed
  // the button they could see, and got a 400 and a spinner.
  //
  // Naming the button that does work is the whole content of the message. A
  // bare "give me a table" would be true and useless: there is no table to
  // give, the app already has the genes, and the reader is one click away.
  if (!$("#table").value.trim() && !$("#file").files[0]) {
    // A run is on screen: redraw it rather than refuse. Nothing is cleared.
    if (DISK_RUN) return redrawLoadedRun(DISK_RUN);
    const picked = $("#use-picked");
    failSoft(picked
      ? "This button draws a table you pasted, and there isn't one. Your "
        + "genes are coming from the gene expression comparison chosen above "
        + "— press “Use this comparison” there to load them."
      : "Paste a differential expression table, or upload one, first.");
    if (picked) picked.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  btn.disabled = true;
  $("#status").className = "";

  // UX A2. v1 set one status string and left #result on screen, so for the
  // whole of a cold run the page showed a complete, plausible, finished-looking
  // figure of the PREVIOUS gene set, with working Download buttons. At ~4 s per
  // gene that window is four minutes. Clearing it removes the whole risk.
  $("#result").hidden = true;
  RUN = null;

  // UX B2's successor. One static grey sentence read as "it has hung"; the
  // one-line ticker that replaced it read as "still going" without ever saying
  // what it was doing. This is the stage list, driven by real events.
  clearInterval(TICKER);
  $("#status").textContent = "";

  const prog = await startProgress("#run-progress");

  const fd = new FormData();
  fd.append("progress_token", prog.token);
  fd.append("region_inferred", REGION_WAS_INFERRED ? "1" : "");
  fd.append("region_reason", REGION_REASON);
  fd.append("table", $("#table").value);
  fd.append("region", $("#region").value);
  fd.append("ordering", $("#ordering").value);
  // The no-data toggle is a property of every active filter, and it is off
  // unless the user says otherwise — rule 1 of the two that govern filtering.
  const drop = $("#drop-no-data").checked;
  fd.append("filters", ($("#filters").value || "").split("\n")
    .filter((l) => l.trim())
    .map((l) => l.trim() + (drop ? " drop_no_data" : "")).join("\n"));
  if ($("#file").files[0]) fd.append("file", $("#file").files[0]);
  // v3.5. The canvas is a function of this window: the circle grows with it, and
  // the twelve ring names come back into the 26° gap when the render's own
  // geometry assertions accept them there at 12 CSS px. Measured here because the
  // page is the only thing that knows how wide the panel came out.
  const win = windowMeasurement();
  if (win) {
    fd.append("panel_px", win[0]);
    fd.append("viewport_px", win[1]);
  }

  let res, body;
  try {
    res = await fetch("/api/analyse", { method: "POST", body: fd });
    body = await res.json();
  } catch (e) {
    clearInterval(TICKER);
    prog.stop();
    fail("Request failed: " + e);
    btn.disabled = false;
    return;
  }
  clearInterval(TICKER);
  prog.stop();
  btn.disabled = false;

  if (!res.ok) {
    fail(errText(body, "failed") + (body.note ? "\n\n" + body.note : ""));
    return;
  }
  RUN = body;
  $("#status").textContent =
    `done in ${body.timings.total_s}s (adapters ${body.timings.adapters_s}s)`;
  render(body);
}

function fail(msg) {
  $("#status").className = "err";
  $("#status").textContent = msg;
  $("#result").hidden = true;
}

// ---------- render ----------

function render(b) {
  $("#result").hidden = false;

  // UX A1: two channels, matching the figure. Red = an invariant was checked
  // and did not hold, so the data is wrong. Amber = a check could not be run or
  // a source was down, which is not the same fact and must not look the same.
  const failed = b.invariants.filter((i) => i.outcome === "FAIL");
  const unchecked = b.invariants.filter((i) => i.outcome === "NOT_EVALUATED");
  const contractFails = (b.contracts || []).filter((c) => !c.ok);
  const banner = $("#banner");
  if (failed.length || contractFails.length) {
    banner.hidden = false;
    banner.textContent =
      "DO NOT TRUST THIS FIGURE — " +
      failed.map((i) => `${i.id} ${i.name}: FAIL`).join(" · ") +
      contractFails.map((c) => ` · source contract ${c.check} FAILED`).join("");
  } else {
    banner.hidden = true;
  }

  const conds = $("#conditions");
  const condLines = unchecked
    .map((i) => `${i.id} ${i.name}: not checked${i.affects ? " — " + i.affects : ""}`)
    .concat((b.run_conditions || []).map((c) => `${c.condition} — ${c.affects}`));
  if (condLines.length) {
    conds.hidden = false;
    conds.innerHTML =
      `<b>Run conditions — not an invariant failure.</b> These checks could not be run;
       each line says what it affects.<ul class="tight">` +
      condLines.map((l) => `<li>${esc(l)}</li>`).join("") + `</ul>`;
  } else {
    conds.hidden = true;
  }

  $("#counts").innerHTML =
    `<b>${b.counts.input}</b> genes in · <b>${b.counts.rendered}</b> rendered · ` +
    `<button class="drop linkish" id="show-ledger"><b>${b.counts.dropped}</b>` +
    ` not shown — why?</button> · ` +
    `region <b>${esc(b.region)}</b> · ${esc(b.ordering_note || "")}` +
    // v2.5 item 2. A filtered figure must never read as the whole submitted set,
    // so the counts line — the one thing everybody reads — says so first.
    ((b.filters || []).length
      ? `<div class="note warn"><b>Filtered — this is not your whole submitted set.</b>
         ${b.filters.map((f) => `<code>${esc(f.expression)}</code>${f.drop_no_data
           ? " <span class='pill warn'>+ drop no-data</span>" : ""}`).join(" · ")}
         · <b>${b.filter_counts.filtered || 0}</b> gene(s) filtered into the ledger` +
        (b.filter_counts.kept_unevaluated
          ? ` · <b>${b.filter_counts.kept_unevaluated}</b> kept and marked ~ (no data on a
             filtered ring — a threshold never drops a gene for being unmeasured)` : "") +
        `</div>`
      : "") +
    (b.warnings.length ? `<div class="note warn">${b.warnings.map(esc).join("<br>")}</div>` : "");

  // v3.0. Three menus, one link each. `web_url` is deliberately NOT here: it is
  // the lean render this page inlines, and a file that drops the caveat footer
  // must never be the one somebody sends to a colleague.
  const dl = {
    "dl-svg": b.svg_url, "dl-png": b.png_url,
    "dl-slide": b.slide_url, "dl-slide-png": b.slide_png_url,
    "dl-caption": b.caption_url,
    "dl-legend": b.legend_url, "dl-legend-png": b.legend_png_url,
    "dl-csv": b.csv_url, "dl-ledger": b.ledger_url,
    "dl-manifest": b.manifest_url,
  };
  for (const [id, href] of Object.entries(dl)) {
    const el = $("#" + id);
    if (el && href) el.href = href;
  }
  // Puck's condition: a figure and its legend must be impossible to mismatch.
  // The id is on both images and in both filenames; this is the third place,
  // beside the buttons, where the two get separated in the first place.
  $("#dl-run").textContent = `All downloads are run ${b.run_id}.`;

  renderLedger(b);
  renderInvariants(b);
  renderCaveats(b);

  // v3.6. A new run invalidates the grid and the sort keys with it — a key is
  // named against a run's own rings, and carrying it across would sort the new
  // table by a column chosen for the old one. Redrawn now only if the reader is
  // already looking at it; otherwise `showTab` fetches it on first view.
  // v3.8: the dim filters go with them, for a sharper reason. A dim filter is a
  // bound on one run's raw values; carried onto another run it would fade rows
  // against a threshold nobody set for them, and the count line would state it as
  // if somebody had.
  TABLE = { run_id: null, sort: [], dim: [], data: null };
  $("#table-run").value = b.run_id;
  if (!$("#pane-table").hidden) loadTable(b.run_id);

  // UX B3: "N not shown" looked like a link and did nothing. It is the one
  // place the user asks "why?".
  const sl = $("#show-ledger");
  // v4.1: `goTab`, not `showTab` — this is a user-driven tab change like any
  // other, so Back from the ledger has to return to the figure it was asked
  // about rather than off the site.
  if (sl) sl.onclick = () => goTab("ledger");

  // v3.2 / UX v3 round 2 B7. The form is 1,053 px of a 2,487 px page and every
  // word of its help text is worth reading exactly once. Closed on the first
  // successful render — not on load, because before you have a figure the help
  // IS the page — so the result starts near the top of the document instead of
  // 1,279 px down it. The summary stays visible and says what it reopens.
  const ctl = $("#controls");
  if (ctl) ctl.open = false;

  // UX B4: after a run the figure was below the fold and the scroll did not move.
  //
  // v3.3 / UX v3 round 3 B15. Where it moves TO is the finding round 2's
  // re-review handed here: `#counts` at the top of the window leaves 155 px of
  // tab row and download row above the figure, so a figure that is itself
  // 894 px and whole was still clipped by 149 px at the position this page
  // chose for the reader. The figure is what the reader came for, so once the
  // SVG is in and its height is known, it gets the viewport if it fits in one.
  // Measured rather than assumed: if a future canvas does not fit, the scroll
  // falls back to `#result` and nothing silently crops.
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });

  // v3.0. The page inlines the WEB render, not the export. Same cells --
  // `assert_renders_agree` fails the run if a single one differs -- but no
  // footer block and no dropped-gene list in the hole, because this page
  // already carries both in the `What this is not` and `Drop ledger` tabs. The
  // circle gets the space instead. Falls back to the export if an older run is
  // still in memory without a web render.
  DREW_FOR = b.web_render ? [b.web_render.panel_px, b.web_render.viewport_px] : null;
  inlineFigure(b.web_url || b.svg_url, true);

  // v3.1 / UX v3 A2. The scale column and the cell-state box are no longer in
  // the app's SVG; the page draws them, from the same norms and swatches the
  // renders use. Same information, 13 px instead of 5.8.
  if (b.scales_url) fetch(b.scales_url).then((r) => r.json()).then(renderDecode);
}

// v3.5. Inlining the SVG is now done twice -- once per run, and again whenever
// the window is resized enough to change the canvas -- so it is one function.
// `scroll` is false on a redraw: the reader is looking at the figure, and moving
// the page under them because it got 80 px taller is not a service.
async function inlineFigure(url, scroll) {
  // The run rewrites `figure-web.svg` in place on a redraw, so the URL alone is
  // not a new resource. The server sends `no-store` for this one artefact and
  // this is the second belt: a stale circle sized for the window before last is
  // the exact failure v3.0's `_NO_STORE` comment is about.
  const svg = await fetch(url + (url.includes("?") ? "&" : "?") + "v=" + Date.now())
    .then((r) => r.text());
  $("#figure").innerHTML = svg;
  wireFigure();
  // B15, continued: now the figure's height is a measurement rather than a
  // guess about a canvas constant.
  const fig = $("#figure");
  if (scroll && fig.getBoundingClientRect().height <= window.innerHeight) {
    fig.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// v3.5. The window changed size; this run's app figure was drawn for the old one.
//
// Only the app render is redrawn -- no source is fetched and no export is
// rewritten -- and only when the change is big enough to change the canvas. If
// the server refuses the new geometry, the figure on screen stays exactly as it
// is: a circle drawn for a window 200 px shorter is a worse thing to look at than
// no update, and it is not a wrong figure.
async function redrawForThisWindow() {
  if (!RUN || !RUN.run_id) return;
  const win = windowMeasurement();
  if (!win) return;
  if (DREW_FOR && Math.abs(win[0] - DREW_FOR[0]) < RESIZE_MIN_PX
      && Math.abs(win[1] - DREW_FOR[1]) < RESIZE_MIN_PX) return;
  const was = $("#status").textContent;
  $("#status").textContent = "redrawing the figure for this window…";
  const fd = new FormData();
  fd.append("panel_px", win[0]);
  fd.append("viewport_px", win[1]);
  let res, body;
  try {
    res = await fetch(`/api/run/${RUN.run_id}/web-figure`, { method: "POST", body: fd });
    body = await res.json();
  } catch (e) {
    $("#status").textContent = was;
    return;
  }
  if (!res.ok) {
    $("#status").className = "err";
    $("#status").textContent = errText(body, "the figure could not be redrawn")
      + (body.note ? "\n\n" + body.note : "");
    return;
  }
  DREW_FOR = [body.web_render.panel_px, body.web_render.viewport_px];
  RUN.web_render = body.web_render;
  await inlineFigure(body.web_url, false);
  // The caveat clauses come from the render, and one of them says where the ring
  // names are — which is the thing that may have just changed.
  drawFigureCaveats(body.figure_caveats);
  $("#status").textContent = was;
}

// v3.1. The comparability sentence, the four cell states, and the twelve
// scales. Read the CSS beside `.figure-row` for why only one of the three is
// allowed to collapse.
function renderDecode(s) {
  // The legend's own words for each ring, which are what a cell's hover says
  // it measures (v4.2). The scales route carries the linked source text, so it
  // is the better of the two sources for `RING_INFO`.
  rememberRings(s.rings);
  // v3.2 / round 2 A6: no amber band here any more. The render draws the
  // comparability sentence at 16.55 px in its own top-left corner and that is
  // the copy the reader meets; a second one 8 px above it was the same claim
  // twice. What the band had that the render does not is the range-drift
  // clause, and it belongs beside the ranges.
  const act = s.rings.find((r) => r.key === "clinical_activity");
  $("#cell-states").innerHTML =
    `<h5>the four cell states</h5>` +
    s.states.map((st) => `<div class="state">
       <span class="sw" style="background:${esc(st.fill)};border-color:${esc(st.edge)}"></span>
       <span>${esc(st.label)}</span></div>`).join("") +
    `<div class="state"><span class="sw"
       style="background:#fff;border-style:dashed"></span>
       <span>value — decode it against that ring's own scale, below</span></div>` +
    // COUNCIL V12: named, not numbered. `r.categories` comes off the same
    // `sorted(spec.categorical)` as `r.colours`, so a colour cannot be paired
    // with the wrong state; the tick labels this used to read are the ring's
    // numeric positions and printing them said "-1 / 0 / 1" beside three
    // colours that are not on a ramp at all.
    // v3.7 item 4: `column_label`, not `short_label`. This heading names a ring
    // on the same surface as the scale rows below it and the grid in the next
    // tab; the wedge's name for it ("Trial activity now") is a third one.
    (act ? `<div class="grp"><h5>ring ${act.n} · ${esc(act.column_label)}</h5>` +
      act.colours.map((c, i) => `<div class="state">
        <span class="sw" style="background:${esc(c)}"></span>
        <span>${esc(act.categories[i] || "")}</span></div>`).join("") +
      `</div>` : "") +
    `<p class="drift">Nine of the twelve ranges move with the gene set you
     submitted, so a colour is only true of this run.</p>`;

  // v3.2 / UX v3 round 2 A4. The caveat clauses the app render handed to the
  // page so that its circle would fit one 1440 x 900 viewport. Read from the
  // render's own `FigureResult`, not rebuilt here: one of them states this run's
  // spoke angles, and a second implementation of that sentence could describe a
  // geometry the figure does not have (COUNCIL V5). 13 px, under the circle, no
  // disclosure around it.
  drawFigureCaveats(s.figure_caveats);

  $("#scale-rows").innerHTML = s.rings.map((r) => `
    <div class="scale">
      <div class="nm" title="${esc(r.tooltip || "")}">${r.n}. ${esc(r.label)}</div>
      <div class="bar${r.stepped ? " stepped" : ""}">${
        r.colours.map((c) => `<i style="background:${esc(c)}"></i>`).join("")}</div>
      <div class="ticks">${r.ticks.map((t, i) => {
        const cls = t.at <= 0.01 ? "lo" : t.at >= 0.99 ? "hi" : "";
        return `<span class="${cls}" style="left:${(t.at * 100).toFixed(1)}%">${
          esc(t.label)}</span>`;
      }).join("")}</div>
      <div class="un">${esc(r.units)}${r.transform ? " · " + esc(r.transform) : ""}</div>
      ${r.source_url
        ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener">${
            esc(r.source_text)}</a>`
        : `<span class="un">${esc(r.source_text)}</span>`}
    </div>`).join("");
  $("#scales").querySelector("summary b").textContent =
    `The ${s.rings.length} ring scales`;
}

// v3.2 / UX v3 round 2 A4, its own function since v3.5 because a resize redraws
// the figure and these clauses with it.
//
// Read from the render's own `FigureResult`, not rebuilt here: one of them states
// this run's spoke angles and says whether the ring names are inside the circle or
// beside it, and a second implementation of that sentence could describe a
// geometry the figure does not have (COUNCIL V5). 13 px, no disclosure around it.
//
// v3.3 / round 3 B14: two groups, not five equal lines. `kind` comes from the
// render, so what is a caveat and what is a note about the drawing is decided
// once, where the sentences are written.
// v3.5 item 2: `where` — the one clause that explains the 12 o'clock gap — is
// drawn at the top of the decode panel beside the figure instead, because it is
// the answer to a question a reader asks while looking at the gap, and under the
// circle it was below the fold. Split by the kind the render gave it, so each
// clause is drawn exactly once and `app.js` never has to recognise a sentence.
function drawFigureCaveats(clauses) {
  const all = clauses || [];
  const cav = $("#figure-caveats");
  if (cav) {
    cav.innerHTML = all.filter((c) => c.kind !== "where")
      .map((c) => `<span class="${esc(c.kind)}">${esc(c.text)}</span>`).join("");
  }
  const note = $("#wedge-note");
  if (note) {
    const where = all.filter((c) => c.kind === "where");
    note.innerHTML = where.map((c) => esc(c.text)).join(" ");
    note.hidden = where.length === 0;
  }
}

// A drawn spoke label, as a pattern: the ⚠ marker, the symbol, v4.0's optional
// ` · <chromosome>` suffix, then the `(k=` that makes it a spoke label and not
// some other text in the file. The chromosome group is non-capturing — this
// regex exists to name the GENE, and `figure._chromosome_suffix` is the only
// thing that decides what the suffix looks like.
const SPOKE_LABEL_RE = /^[⚠\s]*([A-Za-z0-9._-]+)(?:\s*·\s*[A-Za-z0-9._-]+)?\s*\(k=/;

// `genes`: the gene symbols a label may name. Defaults to the live run's order,
// which is every caller but the disk path — where there is no `RUN` and the
// list comes from the SVG itself (`showToolFigure`).
function wireFigure(genes) {
  const known = genes || (RUN ? RUN.gene_order : []);
  wireFigureTooltips();
  // Cells carry id="cell|<ring>|<index>|<gene>" — the same handle the invariant
  // checks use, so what is clickable is exactly what was audited.
  $("#figure").querySelectorAll('[id^="cell|"]').forEach((el) => {
    const gene = el.id.split("|")[3];
    el.style.cursor = "pointer";
    el.addEventListener("click", () => openDrawer(gene));
  });
  $("#figure").querySelectorAll("text").forEach((t) => {
    const m = (t.textContent || "").match(SPOKE_LABEL_RE);
    if (!m) return;
    if (!known.includes(m[1])) return;
    t.style.cursor = "pointer";
    t.addEventListener("click", () => openDrawer(m[1]));
  });
}

function renderLedger(b) {
  if (!b.ledger.length) {
    $("#ledger").innerHTML = "<p class='hint'>No gene was dropped, renamed or flagged.</p>";
    return;
  }
  // Problems first, then the deliberate removals, then the genes that are on the
  // figure and only listed. A filtered gene is not a parse failure and must not
  // sort among them.
  const order = { excel_corrupt: 0, ambiguous: 1, unresolved: 2, dropped_no_padj: 3,
                  dropped_bad_logfc: 4, dropped_dup: 5, dropped_over_cap: 6,
                  filtered: 7, filtered_no_data: 8, possible_mouse: 9, renamed: 10 };
  const rows = [...b.ledger].sort((x, y) => (order[x.status] ?? 11) - (order[y.status] ?? 11));
  const shown = new Set(["renamed", "possible_mouse"]);
  $("#ledger").innerHTML =
    `<p class="hint">Problems first. <b>renamed</b> and <b>possible_mouse</b> genes
     <em>are</em> on the figure — they are listed so you can see what was changed.
     Everything else is absent from it.</p>` +
    `<table><thead><tr><th>Input</th><th>Status</th><th>On the figure?</th>
     <th>Resolved</th><th>Row</th><th>Reason</th></tr></thead><tbody>` +
    rows.map((e) => `<tr>
      <td><code>${esc(e.input)}</code></td>
      <td><span class="pill ${e.status === "renamed" ? "warn"
        : shown.has(e.status) ? "warn" : "bad"}">${esc(e.status)}</span></td>
      <td>${shown.has(e.status) ? "<span class='pill ok'>rendered</span>"
        : "<span class='pill grey'>not shown</span>"}</td>
      <td>${esc(e.resolved_symbol || "")}${e.ensembl_id
        ? ` <code>${esc(e.ensembl_id)}</code>` : ""}</td>
      <td>${esc(e.row ?? "")}</td>
      <td class="reason">${esc(e.reason)}</td></tr>`).join("") +
    `</tbody></table>`;
}

function renderInvariants(b) {
  const pill = (o) => o === "PASS" ? "ok" : o === "FAIL" ? "bad" : "warn";
  $("#invariants").innerHTML =
    `<p class="hint">Checked against the rendered SVG, not the in-memory model.
     <b>NOT_EVALUATED</b> is not a pass.</p>` +
    `<table><thead><tr><th>#</th><th>Invariant</th><th>Result</th><th>Detail</th></tr></thead>
     <tbody>` +
    b.invariants.map((i) => `<tr><td>${esc(i.id)}</td><td>${esc(i.name)}</td>
      <td><span class="pill ${pill(i.outcome)}">${esc(i.outcome)}</span></td>
      <td class="reason">${esc(i.detail)}</td></tr>`).join("") +
    `</tbody></table>` +
    `<h4>source contracts</h4><table><tbody>` +
    (b.contracts || []).map((c) => `<tr><td>${esc(c.check)}</td>
      <td><span class="pill ${c.ok ? "ok" : "bad"}">${c.ok ? "PASS" : "FAIL"}</span></td>
      <td class="reason">${esc(c.detail)}</td></tr>`).join("") +
    `</tbody></table>`;
}

function renderCaveats(b) {
  // v4.0. In tool mode this pane is the SPEND pane and nothing else. Puck,
  // 2026-10-19: "its supposed to be a tool, explanations will be on my blog
  // post" — and, separately, that the spend accounts stay visible and dated.
  // So the prose about what the figure is not goes (the blog post is where it
  // belongs, and every caveat that is about THIS run is already on the figure's
  // own footer, in `#figure-caveats` and in the Invariants tab), and the one
  // thing in here that is a measurement rather than an explanation stays.
  //
  // The tab is relabelled to match, in `relabelToolTabs`: a tab called "What
  // this is not" over a spend table is a worse label than no label.
  if (document.body.classList.contains("tool")) {
    $("#caveats").innerHTML = llmAccountsHtml(b.llm) ||
      `<p class="hint">This run's manifest records no spend accounts.</p>`;
    return;
  }
  $("#caveats").innerHTML =
    `<div class="note warn"><b>This figure is internally consistent and auditable.
     Nothing on it is ranked or scored.</b> It is your gene set, the evidence rings,
     and a drawer per gene.</div>
     <h4>Not attempted — and deliberately so</h4>
     <ul class="tight">${(b.not_attempted || []).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
     <h4>Spoke order</h4>
     <p>${esc(b.ordering_note || "")}</p>
     <p class="hint">The order carries meaning, so it is printed on the figure too.
     The default is your own table's order — it is your measurement, not a composite
     this app invented.</p>
     ${llmAccountsHtml(b.llm)}`;
}

// ---------- evidence (the go/no-go surface) ----------
//
// It reads `/api/run/{id}/evidence`, which is `view.evidence_payload` — the same
// pivot of `values.csv` the Table tab draws, plus `evidence.py`'s per-gene
// readings. Nothing here computes a finding: the rules, their outcomes and the
// definition printed beside each one all arrive decided, for `view.py`'s standing
// reason — the page draws claims, it does not write them.
//
// THE LAYOUT IS THE HONESTY. Three rules govern it:
//
// 1. Unevaluable is never drawn like clear, and sits ABOVE it. A reader scanning
//    for red must not have to notice an absence of green. `evidence.py` makes
//    missing inputs a third outcome; this is that outcome having somewhere to go.
// 2. No card gets a green headline. A gene with one cleared rule and five
//    unevaluable ones would read as a pass, which is rule 1's defect wearing a
//    tick. Cleared rules collapse into a count behind a disclosure.
// 3. No card shows a number of findings, and the cards are never reordered.
//    `order` is the run's own spoke order (rule 7); sorting by findings here
//    would be the ranking the whole surface exists to not be.

const EVIDENCE = { run_id: "" };

async function loadEvidence(runId) {
  const id = (runId || "").trim();
  if (!id) {
    $("#evidence-status").textContent =
      "No run loaded. Draw a figure, or type a run id above — every run under " +
      "runs/ can be opened by id.";
    return;
  }
  $("#evidence-status").className = "hint";
  $("#evidence-status").textContent = `loading ${id}…`;
  let res, d;
  try {
    res = await fetch(`/api/run/${encodeURIComponent(id)}/evidence`);
    d = await res.json();
  } catch (e) {
    $("#evidence-status").className = "err";
    $("#evidence-status").textContent = "Request failed: " + e;
    return;
  }
  if (!res.ok) {
    // Refused, not approximated, and the previous surface stays on screen rather
    // than being replaced by an empty one that looks like "nothing to report".
    $("#evidence-status").className = "err";
    $("#evidence-status").textContent =
      errText(d, "the evidence surface could not be loaded");
    return;
  }
  EVIDENCE.run_id = d.run_id;
  $("#evidence-status").className = "hint";
  $("#evidence-status").textContent = "";
  $("#evidence-run").value = d.run_id;
  renderEvidence(d);
}

//: The three non-value states, worded as what a reader should do about each —
//: `failed` is the one a re-run fixes, and collapsing it into `no_data` would
//: tell somebody to stop looking where the honest answer is "ask again".
const GAP_WORDS = {
  failed: ["the call broke", "bad"],
  no_data: ["the source says nothing", "grey"],
  not_applicable: ["does not apply to this gene", "grey"],
};

function evidenceFindingHtml(f, labels) {
  const rings = (f.rings || []).map((r) =>
    `<code>${esc(labels[r] || r)}</code>`).join(" + ");
  // The definition that makes this a finding, behind a disclosure so the card
  // stays scannable — but always present, never summarised by the page.
  //
  // The summary is worded off the OUTCOME, not off the kind: "why this is a
  // disagreement" over a reading that could not be run claims a disagreement
  // was found, which is the opposite of what `unevaluable` means.
  const summary = f.outcome !== "fires" ? "what this check is"
    : f.kind === "contradiction" ? "why this is a disagreement" : "why this is flagged";
  const why = `<details class="ev-why"><summary>${summary}</summary>
    <p class="reason">${esc(f.why)}</p>
    <p class="hint">Reads ${rings}</p></details>`;

  if (f.outcome === "fires") {
    return `<li class="ev-fires ev-${esc(f.kind)}">
      <span class="pill ${f.kind === "contradiction" ? "bad" : "warn"}">${
        f.kind === "contradiction" ? "sources disagree" : "flag"}</span>
      <span class="ev-headline">${esc(f.headline)}</span>${why}</li>`;
  }
  if (f.outcome === "unevaluable") {
    const missing = (f.missing_rings || []).map((r) => {
      const st = (f.missing_states || {})[r];
      const nd = (f.not_drawn || []).includes(r);
      const word = nd ? "this run did not draw it"
        : (GAP_WORDS[st] ? GAP_WORDS[st][0] : esc(st));
      return `<code>${esc(labels[r] || r)}</code> — ${esc(word)}`;
    }).join("; ");
    // The rule's NAME, muted and prefixed — not phrased as the headline a
    // firing rule gets. "approved but not tclin" set like a finding reads as
    // one, which is the one thing an unevaluable reading has not established.
    return `<li class="ev-unevaluable">
      <span class="pill grey">cannot be checked</span>
      <span class="ev-rulename">check: ${esc(f.rule.replace(/_/g, " "))}</span>
      <p class="reason">Needs a measurement for: ${missing}.</p>${why}</li>`;
  }
  return "";  // `clear` is counted, not listed — see rule 2 above.
}

function evidenceGeneHtml(g, d) {
  const labels = d.ring_labels || {};
  const findings = d.findings[g.gene] || [];
  const gaps = d.gaps[g.gene] || {};
  const fires = findings.filter((f) => f.outcome === "fires");
  const unevaluable = findings.filter((f) => f.outcome === "unevaluable");
  const clear = findings.filter((f) => f.outcome === "clear");

  // Contradictions before flags before unevaluable. Within the firing group a
  // source disagreement outranks a history, because only one of the two says
  // the figure's own numbers may be wrong.
  const ordered = [
    ...fires.filter((f) => f.kind === "contradiction"),
    ...fires.filter((f) => f.kind === "flag"),
    ...unevaluable,
  ];

  const gapHtml = Object.entries(gaps)
    .filter(([, keys]) => keys.length)
    .map(([state, keys]) => `<li><span class="pill ${
      GAP_WORDS[state] ? GAP_WORDS[state][1] : "grey"}">${esc(state)}</span>
      ${esc(GAP_WORDS[state] ? GAP_WORDS[state][0] : "")} —
      ${keys.map((k) => `<code>${esc(labels[k] || k)}</code>`).join(", ")}</li>`)
    .join("");

  // A <div>, NOT a <header>: `app.css`'s masthead rule is the bare element
  // selector `header`, so a <header> here inherits min-height 128px, the
  // masthead padding and its bottom rule — and would inherit the watercolour
  // wash too, once `header-wash.css` has its PNG. It rendered as a 65 px void
  // and a stray line above every card.
  return `<article class="ev-card">
    <div class="ev-head">
      <h4>${esc(g.gene)}</h4>
      ${g.ensembl_id ? `<code class="ev-ens">${esc(g.ensembl_id)}</code>` : ""}
      <span class="ev-k">k = ${esc(g.k)} of ${esc(d.k_max)} external rings
        measured</span>
    </div>
    ${/* The heading names its scope. The nine external rings are what `k` on
          the line above counts and what every rule reads; logFC and adjusted p
          are this run's INPUT rather than evidence about the target, so they
          are not scanned here and the card does not imply they were. */""}
    ${gapHtml ? `<div class="ev-gaps"><h5>No measurement — of the ${
      esc(d.k_max)} external rings</h5>
      <ul class="tight">${gapHtml}</ul></div>` : ""}
    ${ordered.length ? `<ul class="ev-findings">${
      ordered.map((f) => evidenceFindingHtml(f, labels)).join("")}</ul>`
      : `<p class="hint">No rule fired and none was unevaluable.</p>`}
    ${clear.length ? `<details class="ev-clear"><summary>${clear.length} reading${
      clear.length === 1 ? "" : "s"} checked and did not hold</summary>
      <ul class="tight">${clear.map((f) =>
        `<li>${esc(f.rule.replace(/_/g, " "))}</li>`).join("")}</ul></details>`
      : ""}
  </article>`;
}

function renderEvidence(d) {
  const fc = d.finding_counts || {};
  // The run-level line. Counts of GENES, three of them, never a total of
  // findings — a target with two disagreements is not twice one.
  const counts = `<ul class="tight ev-counts">
    <li><b>${esc(fc.genes)}</b> target(s) on this figure</li>
    <li><b>${esc(fc.with_contradiction)}</b> where two sources disagree</li>
    <li><b>${esc(fc.with_flag)}</b> carrying a flag</li>
    <li><b>${esc(fc.with_unevaluable)}</b> with at least one reading that
      cannot be checked</li></ul>`;

  $("#evidence").innerHTML =
    `<div class="note warn"><b>${esc(d.evidence_note)}</b></div>
     <p class="hint">${esc(d.evidence_empty_note)}</p>
     <div class="ev-meta">
       <code>${esc(d.run_id)}</code> · ${esc(d.region || "")} ·
       ${esc(d.timestamp_utc || "")}
       <p class="hint">Order: ${esc(d.ordering_note || d.ordering || "")}
       — the order this run drew its spokes in. This surface does not reorder
       it.${d.ordering_note_drift
          ? ` <span class="src">${esc(d.ordering_note_drift)}</span>` : ""}</p>
     </div>
     ${counts}
     <h4>What was checked</h4>
     <ul class="tight ev-rules">${(d.rules || []).map((r) =>
       `<li><span class="pill ${r.kind === "contradiction" ? "bad" : "warn"}">${
         esc(r.kind)}</span> ${esc(r.key.replace(/_/g, " "))}</li>`).join("")}</ul>
     <div class="ev-cards">${d.order.map((name) => {
       const g = d.genes.find((x) => x.gene === name);
       return g ? evidenceGeneHtml(g, d) : "";
     }).join("")}</div>`;
}

// Stage 0. Two paid features, two caps, two counters, and BOTH lines every time.
//
// This block used to be nothing at all, because `manifest.llm` was a hard-coded
// `{"attempted": false, "spend_usd": 0.0}` — a zero that read like a measurement.
// It is folded out of the append-only ledger now, and the reason both accounts
// are printed even when a run touched neither is that rule 8's promise is about
// the pair: "neither borrows from the other" is unverifiable from one number.
function llmAccountsHtml(llm) {
  if (!llm || !llm.accounts) return "";
  const rows = Object.entries(llm.accounts).map(([name, a]) =>
    `<tr><td><code>${esc(name)}</code></td>
      <td>$${Number(a.spent).toFixed(2)} of $${Number(a.cap).toFixed(2)}</td>
      <td>${a.calls} call${a.calls === 1 ? "" : "s"}</td>
      <td class="reason">${esc(a.purpose)}</td></tr>`).join("");
  const prices = Object.entries(llm.price_table || {}).map(([m, p]) =>
    `<code>${esc(m)}</code> $${p.input}/$${p.output} per MTok in/out`).join("; ");
  // Counted from the payload, not written down: this said "two caps, two
  // counters" above a four-row table after rule 8 was amended twice on
  // 2026-10-09. A hardcoded count beside a rendered list is a caption that
  // goes stale the moment the list grows.
  const nAcc = Object.keys(llm.accounts || {}).length;
  // v4.0, Puck 2026-10-19: "just date it because the costs all change". The
  // date goes in the HEADING, beside the word "spent", rather than in the
  // paragraph of small print below — a reader who reads one line of this box
  // reads the heading, and an undated dollar figure is the thing being fixed.
  // `accounts_measured_utc` is written by `spend.manifest_block`; a manifest
  // from before v4.0 has no such key and the heading simply carries no date
  // rather than inventing one from the run's timestamp.
  const when = (llm.accounts_measured_utc || "").slice(0, 10);
  return `<h4>Paid calls — ${nAcc} cap${nAcc === 1 ? "" : "s"}, ${nAcc} counter${
    nAcc === 1 ? "" : "s"}${when ? `, measured ${esc(when)}` : ""}</h4>
    ${when ? `<p class="hint">Every dollar below is the total spent up to
      <b>${esc(when)}</b>, folded from the ledger on that date. Model prices
      change and this build's are a pinned constant, so read these as a record
      of what this project cost by then, not as a current price list.</p>` : ""}
    <table><thead><tr><th>account</th><th>spent</th><th>calls</th>
      <th>what it pays for</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">No account borrows from another and all are separate
    from any other budget on this machine. Spend is folded from
    <code>${esc(llm.ledger_path || "runs/spend-ledger.jsonl")}</code>, which is
    append-only and lives outside <code>cache/</code> so a cache reset cannot zero
    it. A refused call is recorded too, at $0.00, so "why is there no summary" is
    answerable from the file. Prices are a pinned constant (cached
    ${esc(llm.price_table_cached_utc || "")}): ${prices}. A model with no pinned
    price cannot be called — the ledger refuses rather than guessing.</p>`;
}

// ---------- drawer ----------

// v3.3 / UX v3 round 3 A8. The three sections the drawer exists for: PLAN §1 is
// "clicking a gene opens a drawer with its drug pipeline and its literature",
// and a reader's own measurement is what they arrived holding.
const DRAWER_OPEN = ["your measurement", "drug pipeline", "literature"];

// The two sections whose caveat is load-bearing. Collapsed to a bare heading,
// COUNCIL M4's "a lead, not a verdict" and PLAN §8's "any ChEMBL-backed ring is
// untrusted on day one" would be one click further from the numbers they
// qualify than the numbers are. So the caveat rides on the summary, outside the
// collapse, and only the rows go behind it. Matched on a prefix because the
// trials heading carries its source and that source changed under this build
// once already (v2.4).
const DRAWER_CAVEATS = [
  ["mouse phenotype lead", "a lead, not a verdict"],
  ["mouse phenotype table", "per-paper raw evidence; the LLM summary paraphrases only these rows"],
  ["ChEMBL mechanisms", "untrusted — resolution unprobed"],
  ["safety liabilities", "absence is weak evidence of safety"],
];

function sectionCount(body) {
  const rows = (body.replace(/<thead>[\s\S]*?<\/thead>/g, "").match(/<tr>/g) || []).length;
  const items = (body.match(/<li>/g) || []).length;
  const pairs = (body.match(/<dt>/g) || []).length;
  const n = rows || items || pairs;
  const what = rows ? "row" : items ? "item" : pairs ? "field" : "";
  return n ? `${n} ${what}${n === 1 ? "" : "s"}` : "a note";
}

// One `<details>` per `<h4>`, built by splitting the finished HTML rather than
// by threading a wrapper through nine `h +=` sites. The sections are written in
// nine places and closed in none of them; doing it here is why this fix could
// not introduce an unbalanced tag into one of them.
function sectionise(html) {
  return html.split(/(?=<h4>)/).map((part) => {
    const m = part.match(/^<h4>([\s\S]*?)<\/h4>([\s\S]*)$/);
    if (!m) return part;                       // the gene header, before any <h4>
    const title = m[1].replace(/<[^>]+>/g, "").trim();
    const body = m[2];
    const open = DRAWER_OPEN.some((t) => title.startsWith(t)) ? " open" : "";
    const hit = DRAWER_CAVEATS.find(([t]) => title.startsWith(t));
    const caveat = hit ? ` <span class="caveat">— ${esc(hit[1])}</span>` : "";
    return `<details class="sect"${open}>` +
      `<summary><span class="chev" aria-hidden="true">▾</span> <b>${esc(title)}</b>` +
      `${caveat} <span class="count">${esc(sectionCount(body))}</span></summary>` +
      `<div class="reveal">${body}</div></details>`;
  }).join("");
}

async function openDrawer(gene, push = true) {
  // `push` defaults true so every existing call site — the spoke labels, the
  // figure cells, the grid rows — becomes a history entry without being
  // touched. `applyHash` and `goGene` pass false: they are already answering
  // or writing the entry themselves.
  if (push) { goGene(gene); return; }
  hideCellTip();
  // v4.2. Whichever surface opened it — a row, the gene button, a cell on the
  // circle or a `#table/<gene>` link — the grid marks the row the drawer is
  // about. One highlight, set in one place, so the two routes cannot disagree.
  highlightRowFor(gene);
  // Two routes, one drawer. A run in memory has adapters behind it and
  // `/gene/` fetches ChEMBL and Europe PMC on this click. A run read from disk
  // has none; `/drawer/` is the file `scripts/bake_drawers.py` wrote beside the
  // run, and the drawer says so on its face (`d.baked`). Which one is never
  // guessed from the page's state alone: `renderTable` only makes a label
  // clickable when the payload named it (`in_memory` or `baked_drawers`).
  // v4.0: `TOOL_RUN` is the last fallback, for the one window where the grid's
  // own fetch has not landed yet. In tool mode the circle is clickable as soon
  // as its SVG is inlined, and `loadTable` — which is what sets `TABLE.run_id`
  // — is a separate request in flight beside it. Without this, a click in that
  // window asked `/api/run/undefined/drawer/MUC1`.
  const runId = RUN ? RUN.run_id : (TABLE.run_id || TOOL_RUN);
  const route = RUN ? "gene" : "drawer";
  const r = await fetch(`/api/run/${encodeURIComponent(runId)}/${route}/${encodeURIComponent(gene)}`);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    $("#drawer-body").innerHTML = `<h3>${esc(gene)}</h3>
      <div class="note bad"><b>${esc(d.error || `HTTP ${r.status}`)}</b><br>
      ${esc(errText(d, "the drawer could not be opened"))}</div>`;
    $("#drawer").hidden = false;
    $("#scrim").hidden = false;
    return;
  }
  $("#drawer-body").innerHTML = sectionise(drawerHtml(d));
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer").scrollTop = 0;
}

// The paint half: hide the drawer, touch no history. `applyHash` uses this, so
// that arriving at a gene-less entry closes the drawer without pushing.
function closeDrawerPaint() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  // v4.2. The row highlight is the drawer's: it says which row the panel on
  // screen is about, so it goes when the panel does.
  clearRowHighlight();
}

// v4.2, Puck 2026-10-19: "when you click on a row, it should highlight the
// whole row and then bring up the table thing". One row at a time, cleared
// when the drawer closes — a highlight left behind after the drawer is a row
// claiming to be the subject of a panel that is no longer there.
function clearRowHighlight() {
  document.querySelectorAll("#table-grid tr.row-selected")
    .forEach((tr) => tr.classList.remove("row-selected"));
}

function highlightRow(tr) {
  clearRowHighlight();
  if (tr) tr.classList.add("row-selected");
}

function highlightRowFor(gene) {
  let found = null;
  document.querySelectorAll("#table-grid tbody tr").forEach((tr) => {
    const label = tr.querySelector(".gene");
    if (!label) return;
    if ((label.dataset.gene || label.textContent.trim()) === gene) found = tr;
  });
  highlightRow(found);
}

// The user's half, behind the ×, the scrim and Escape. Closing the drawer is
// going back from it, so it IS `history.back()`: that pops the entry the
// drawer owns — leaving Back pointing at whatever came before the gene instead
// of re-opening it — and the browser restores the scroll position it saved,
// which is more faithful than anything this file could reapply.
//
// The guard matters. If the drawer somehow owns no entry (an old deep link, a
// hand-edited hash) `history.back()` would walk off the site, so in that case
// it just closes.
function closeDrawer() {
  if (currentView().gene) history.back();
  else closeDrawerPaint();
}

// ---------- the evidence summary box (2026-10-18) ----------
//
// Puck: "a text box on highlight that has an LLM interpretation of the values
// ... basically a summary of evidence, kind of dry." The text is NOT generated
// here and NOT fetched from a model: it is the `summary` block that
// `scripts/bake_summaries.py` wrote into the gene's baked drawer, read through
// the same file route the drawer uses (`/api/run/{id}/drawer/{gene}`), cached per
// gene for the page's life. A gene with no baked summary -- no drawer file, or a
// drawer baked before summaries existed -- shows nothing at all: no box, no
// error, no "generating…". The box names what it is on its face: model, date,
// prompt version, and the drawer's own provenance line.
const SUMMARY_CACHE = new Map();   // `${run}|${gene}` -> drawer JSON | null
let SUMMARY_SHOWING = "";          // the gene the box is currently about
let SUMMARY_DISMISSED = "";        // a gene the reader closed; stays closed until another is highlighted
let SUMMARY_TIMER = null;

async function bakedDrawerFor(runId, gene) {
  const k = `${runId}|${gene}`;
  if (SUMMARY_CACHE.has(k)) return SUMMARY_CACHE.get(k);
  let d = null;
  try {
    const r = await fetch(`/api/run/${encodeURIComponent(runId)}/drawer/${encodeURIComponent(gene)}`);
    d = r.ok ? await r.json() : null;
  } catch (e) {
    d = null;                       // a network failure is "no summary", never a message
  }
  SUMMARY_CACHE.set(k, d);
  return d;
}

// ---------- the GO / NO-GO verdict (2026-10-18) ----------
//
// Puck: "make a recommendation, have go/no go: we need the gene to be
// expressed, and also misregulated in the study, and have a strong phenotype in
// a study." The verdict is the `recommendation` block `scripts/bake_drawers.py`
// wrote into the gene's baked drawer -- `targetring/recommend.py`, a
// deterministic rule over numbers the drawer already shows, with its thresholds
// and a hash of the rule stored beside it. Nothing is computed here and nothing
// is fetched for it. It is the owner's explicit rule, not the rings (PLAN v2.5:
// the rings are evidence, not a score) -- and it never orders, filters or
// ledgers anything on the figure; the box says so under the rule text.
// 2026-10-18 18:42 PDT, Puck: "can remove druggability caveat. and make it
// much less wordy. or remove the word expression altogether as the information
// is in the graph. i would like an explanation on the phenotype though,
// somewhere, like a 0/1 for phenotype that is claude coded." So the box is now:
// the verdict pill; three criteria as met / not met / no data plus the bare
// value (`criterion.bare`, computed in recommend.py beside the sentence, which
// stays in the JSON and in the summary's facts); a three-word region tag; the
// phenotype flag; the modality line when the gene is down-regulated; and one
// rule line whose thresholds are in its tooltip. The expression sentence and
// the two TPMs are gone from the box on purpose -- the specificity ring on the
// figure carries them, and the drawer's own numbers are one click away.
const VERDICT_CLASS = { "GO": "go", "NO-GO": "nogo", "INSUFFICIENT DATA": "nodata" };
const CRIT_MARK = { met: "✓", not_met: "✗", no_data: "~" };
const CRIT_WORD = { met: "met", not_met: "not met", no_data: "no data" };

// `phenotype (Claude): 1 — <sentence>`. A display aid printed beside the
// owner's deterministic phenotype criterion and never that criterion: the
// baked block says so itself (`is_criterion: false`) and the verdict was
// computed without it.
// `terms` is the drawer's `phenotype_terms`: the flag sentence is unchanged and
// the model count sits beside it, so the box says how much mouse evidence is
// under the terms the flag was given without the flag claiming anything about it.
function phenotypeFlagHtml(pf, terms) {
  if (!pf || (pf.flag !== 0 && pf.flag !== 1)) return "";
  const n = phenotypeModelCount(terms);
  const models = (terms || []).length
    ? ` <span class="flag-models" title="Distinct MGI biological models across this
      gene's ${(terms || []).length} phenotype term(s). Not an input to the flag or to
      the verdict; the terms are.">models: ${n}</span>`
    : "";
  return `<li class="crit-flag ${pf.flag ? "flag-1" : "flag-0"}"
    title="${esc(pf.note || "")} Model ${esc(pf.model || "")}, prompt ${esc(pf.prompt_version || "")}.">
    <b>phenotype (Claude)</b>: <span class="flag-num">${pf.flag}</span>
    <span class="flag-why">— ${esc(pf.reason || "")}</span>${models}</li>`;
}

// ---------- which model each phenotype term came from (2026-10-18) ----------
//
// Puck: "can you state which model each term came from". The drawer's
// `phenotype_terms` is one entry per TERM with the MGI biological models under
// it (`phenotype.terms_with_models`): allelic composition, genetic background
// and that MODEL's PMIDs. The flat `mouse_phenotypes` label list is still
// there and still what the rule and the Claude flag read, so none of this can
// move a verdict or a flag -- it is display only. A drawer baked before this
// existed has no `phenotype_terms`, and then the bare labels are shown, as
// before, with no claim about models either way.
const PUBMED = "https://pubmed.ncbi.nlm.nih.gov/";
const PHENOTYPE_TERMS_SHOWN = 12;

function phenotypeModelCount(terms) {
  const ids = new Set();
  (terms || []).forEach((t) => (t.models || []).forEach((m) => { if (m.id) ids.add(m.id); }));
  return ids.size;
}

// "term — n model(s)", one per line, for the verdict box's phenotype tooltip.
function phenotypeTermsTooltip(terms) {
  if (!terms || !terms.length) return "";
  return terms.map((t) => `${t.label} — ${(t.models || []).length} model(s)`).join("\n");
}

function phenotypeModelHtml(m) {
  const bits = [];
  if (m.allelic_composition) bits.push(esc(m.allelic_composition));
  if (m.genetic_background) bits.push(esc(m.genetic_background));
  const pmids = (m.pmids || []).map((p) =>
    `<a href="${esc(PUBMED + p)}" target="_blank" rel="noopener">${esc(p)}</a>`).join(", ");
  return `<li class="pheno-model"><code>${esc(m.id || "—")}</code>${
    bits.length ? " " + bits.join(" · ") : ""}${
    pmids ? ` · <span class="pheno-pmids">papers: ${pmids}</span>`
          : ` · <span class="hint">no paper on this model in MGI</span>`}</li>`;
}

function phenotypeTermListHtml(terms, labels) {
  if (!terms || !terms.length) {
    const mp = labels || [];
    return `<ul class="tight">${mp.slice(0, PHENOTYPE_TERMS_SHOWN)
      .map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      ${mp.length > PHENOTYPE_TERMS_SHOWN
        ? `<p class="hint">…and ${mp.length - PHENOTYPE_TERMS_SHOWN} more</p>` : ""}
      <p class="hint">This drawer was baked before the models were recorded — which
        mouse each term came from is in the phenotype table below.</p>`;
  }
  const shown = terms.slice(0, PHENOTYPE_TERMS_SHOWN);
  const h = shown.map((t) => {
    const models = t.models || [];
    return `<li class="pheno-term">${esc(t.label)}${t.mp_id
      ? ` <span class="hint">${esc(t.mp_id)}</span>` : ""}
      <span class="hint">· ${models.length} model(s)</span>
      ${models.length
        ? `<ul class="pheno-models">${models.map(phenotypeModelHtml).join("")}</ul>`
        : `<p class="hint">MGI records this phenotype and names no biological model
           for it — not the same as no model existing.</p>`}</li>`;
  }).join("");
  return `<ul class="tight pheno-terms">${h}</ul>
    ${terms.length > PHENOTYPE_TERMS_SHOWN
      ? `<p class="hint">…and ${terms.length - PHENOTYPE_TERMS_SHOWN} more term(s)</p>` : ""}
    <p class="hint">Each model is MGI's allelic composition and genetic background;
      the papers under it are that MODEL's literature — MGI does not attribute a paper
      to a particular phenotype, and neither does this list.</p>`;
}

function recommendationHtml(rec, compact, phenotypeFlag, phenotypeTerms) {
  if (!rec || !rec.verdict) return "";
  const cls = VERDICT_CLASS[rec.verdict] || "nodata";
  const crit = (rec.criteria || []).map((c) => {
    const st = c.status || "no_data";
    // The disease-area aid, reduced to a count with the terms in the tooltip.
    // The leading space is inside the string on purpose: `margin-left` spaces
    // it on screen, but copied text would otherwise read "6 abnormal
    // termsdisease-area terms: 3".
    const area = c.disease_area && c.disease_area.short
      ? ` <span class="crit-aid" title="${esc(c.disease_area.tooltip || "")}">${
          esc(c.disease_area.short)}</span>`
      : "";
    // The phenotype line's tooltip also names each term and how many models it
    // came from -- the terms are the criterion's own input, the models are not.
    const termsTip = c.key === "phenotype" ? phenotypeTermsTooltip(phenotypeTerms) : "";
    const tip = termsTip ? `${c.shown || ""}\n\n${termsTip}` : (c.shown || "");
    return `<li class="crit ${esc(st)}"><span class="mark" aria-hidden="true">${CRIT_MARK[st] || "~"}</span>
      <b>${esc(c.label || c.key)}</b> <span class="word">${CRIT_WORD[st] || esc(st)}</span>
      <span class="shown" title="${esc(tip)}">${esc(c.bare || c.shown || "")}</span>${area}</li>`;
  }).join("");
  // The region as a three-word tag, with the long sentence in its tooltip.
  const region = rec.region_tag && rec.region_tag.region
    ? `<span class="verdict-region" title="${esc(rec.region_tag.tooltip || "")}">${
        esc(rec.region_tag.shown)}</span>` : "";
  const caveats = (rec.caveats || []).map((c) => `<p class="verdict-caveat">${esc(c)}</p>`).join("");
  const modality = rec.modality ? `<p class="verdict-modality">${esc(rec.modality)}</p>` : "";
  const flag = phenotypeFlagHtml(phenotypeFlag, phenotypeTerms);
  return `<div class="verdict ${cls}${compact ? " compact" : ""}">
    <p class="verdict-line"><span class="verdict-word">${esc(rec.verdict)}</span>${region}</p>
    <ul class="crit-list">${crit}${flag}</ul>
    ${modality}${caveats}
    <p class="verdict-rule" title="${esc(rec.rule_tooltip || rec.rule_text || "")}">${
      esc(rec.rule_short || rec.rule_text || "")}</p>
  </div>`;
}

function summaryHtml(d) {
  const s = d.summary || {};
  const baked = d.baked || {};
  // Verdict line first, then the three criteria, then the LLM text (whose
  // first sentence restates that verdict), then what the text is.
  return `<h3>${esc(d.gene)} <span class="hint">${d.recommendation ? "GO / NO-GO and " : ""}evidence summary</span></h3>
    ${recommendationHtml(d.recommendation, true, d.phenotype_llm, d.phenotype_terms)}
    ${s.text ? `<p class="summary-text">${esc(s.text)}</p>
    <p class="summary-label"><b>LLM-generated from the values below</b>, <code>${esc(s.model)}</code>,
      ${esc((s.generated_at || "").slice(0, 10))}, prompt <code>${esc(s.prompt_version)}</code>.
      It reads this gene's own drawer and nothing else; the exact facts it was given are stored
      beside it. The verdict above was given to it, not decided by it. Open the drawer for the numbers.</p>`
    : `<p class="summary-label">No evidence summary is baked for this gene.</p>`}
    ${baked.note ? `<p class="summary-prov">${esc(baked.note)}${
      baked.at_utc ? ` Baked ${esc(baked.at_utc)}` : ""}${
      baked.git_sha ? ` at <code>${esc(baked.git_sha)}</code>` : ""}.</p>` : ""}`;
}

async function showSummary(runId, gene) {
  if (!runId || !gene || gene === SUMMARY_DISMISSED) return;
  const d = await bakedDrawerFor(runId, gene);
  // Nothing baked -- no summary text and no verdict -- shows nothing at all.
  if (!d || !((d.summary && d.summary.text) || (d.recommendation && d.recommendation.verdict))) return;
  SUMMARY_SHOWING = gene;
  $("#summary-body").innerHTML = summaryHtml(d);
  $("#summary-box").hidden = false;
}

function hideSummary(dismiss) {
  if (dismiss) SUMMARY_DISMISSED = SUMMARY_SHOWING;
  SUMMARY_SHOWING = "";
  $("#summary-box").hidden = true;
}

// Hover or keyboard focus on a gene's label, or on any cell of its row, is the
// highlight. Debounced so a pointer crossing the grid does not fetch every row;
// leaving the grid hides the box unless the pointer is on the box itself (so the
// text can be read and selected).
function wireSummaryHighlight(d) {
  const runId = d.run_id;
  const baked = new Set(d.baked_drawers || []);
  $("#table-grid").querySelectorAll("tbody tr").forEach((tr) => {
    const label = tr.querySelector(".gene");
    const gene = label ? (label.dataset.gene || label.textContent.trim()) : "";
    if (!gene || !baked.has(gene)) return;       // no drawer file -> no summary; nothing wired
    const arm = () => {
      clearTimeout(SUMMARY_TIMER);
      if (gene !== SUMMARY_DISMISSED) SUMMARY_DISMISSED = "";
      SUMMARY_TIMER = setTimeout(() => showSummary(runId, gene), 120);
    };
    tr.addEventListener("mouseenter", arm);
    tr.addEventListener("focusin", arm);
  });
  $("#table-grid").addEventListener("mouseleave", () => {
    clearTimeout(SUMMARY_TIMER);
    SUMMARY_TIMER = setTimeout(() => {
      if (!$("#summary-box").matches(":hover")) hideSummary(false);
    }, 250);
  });
}

// v4.0. The chromosome, first in the drawer's identifier line, spelled out as
// "chr 16" because there is room here and the rim only has room for the number.
// The three non-value states say so IN WORDS rather than going blank the way
// the rim does: the drawer is where a reader comes to find out why a suffix was
// missing, so this is the surface that has to name the state. `detail` carries
// the adapter's own sentence and is the hover.
const CHROMOSOME_MISSING = {
  no_data: "no chromosome on record",
  not_applicable: "not on a numbered chromosome",
  failed: "chromosome lookup FAILED",
};

function chromosomeHint(c) {
  if (!c) return "";                       // a drawer baked before v4.0
  const title = c.detail ? ` title="${esc(c.detail)}"` : "";
  if (c.state === "value" && c.text) {
    return `<span class="chr"${title}>chr ${esc(c.text)}</span>`;
  }
  const said = CHROMOSOME_MISSING[c.state] || "chromosome unknown";
  return `<span class="chr chr-missing"${title}>${esc(said)}</span>`;
}

function drawerHtml(d) {
  const drugs = d.drugs || [];
  const lit = d.literature || {};
  const ch = d.chembl || {};
  // v2.2 item 3: ChEMBL and Europe PMC are fetched when this drawer opens, not
  // on the render path. `null` means not fetched yet, never "nothing there".

  let h = `<h3>${esc(d.gene)}</h3>
    <p class="hint">${chromosomeHint(d.chromosome)}${d.ensembl_id
      ? (d.chromosome ? " · " : "") + "Ensembl " + esc(d.ensembl_id) : ""}${d.uniprot
      ? " · UniProt " + esc(d.uniprot) : ""}${d.gencode_id
      ? " · GTEx " + esc(d.gencode_id) : ""}</p>`;

  // A baked drawer states its date before anything else in it: the pipeline
  // and the literature below are as they were on that day, not as they are.
  if (d.baked) {
    h += `<div class="note"><b>Read from disk.</b> ${esc(d.baked.note || "")}
          ${d.baked.at_utc ? `Baked ${esc(d.baked.at_utc)}` : ""}${
          d.baked.git_sha ? ` at <code>${esc(d.baked.git_sha)}</code>` : ""}.</div>`;
  }

  if (d.input_symbol !== d.gene) {
    h += `<div class="note warn">Submitted as <code>${esc(d.input_symbol)}</code> —
          ${esc(d.resolution_note)}</div>`;
  }

  // The GO / NO-GO verdict, same block as the highlight box, above the
  // numbers it was computed from -- before the first <h4>, so `sectionise`
  // leaves it in the always-visible header rather than behind a collapsed
  // section. Absent for a drawer baked before the rule.
  if (d.recommendation) h += recommendationHtml(d.recommendation, false, d.phenotype_llm,
                                               d.phenotype_terms);

  // v4.2. "your measurement" prints the adjusted p as `fmtP` — `<.0001`, not
  // `2.19538e-22`. `d.padj` itself is untouched in the payload and on disk;
  // this is the one line that showed the raw float to a reader.
  h += `<h4>your measurement</h4><dl class="kv">
    <dt>logFC</dt><dd>${d.logfc}</dd>
    <dt>adjusted p</dt><dd>${fmtP(d.padj)}</dd>
    <dt>rings with a real value (k)</dt><dd>${d.k} of ${d.k_max}
      <span class="hint">completeness only — k orders nothing and selects nothing</span></dd>
    <dt>Pharos TDL</dt><dd>${d.tdl && d.tdl.state === "value"
      ? `<b>${esc((d.tdl.raw || {}).tdl || "")}</b> <span class="hint">development level, not a pocket</span>`
      : `<span class='pill grey'>${esc((d.tdl || {}).state || "—")}</span>`}</dd></dl>`;
  if (d.k_note) h += `<div class="note">${esc(d.k_note)}</div>`;
  // v2.5 item 2, rule 1: this gene is on the figure because a threshold could
  // not be evaluated against a cell it has no data for, not because it passed.
  if ((d.unfiltered_rings || []).length) {
    h += `<div class="note warn">~ Kept, not filtered: a threshold is active on
      <b>${d.unfiltered_rings.map(esc).join(", ")}</b> and this gene has no value there,
      so the filter could not be evaluated. Dropping it would delete genes for being
      under-studied rather than for missing the threshold.</div>`;
  }

  // --- drug pipeline (COUNCIL D6) ---
  h += `<h4>drug pipeline</h4>
        <p class="summary-line">${esc(d.pipeline_summary)}</p>`;
  if ((d.severe_liabilities || []).length) {
    h += `<div class="note bad"><b>⚠ Open Targets records a cardiac or mortality safety
      liability for this target:</b> ${d.severe_liabilities
        .map((s) => `${esc(s.event)} <span class="hint">(matched “${esc(s.matched)}”${
          s.datasource ? ", " + esc(s.datasource) : ""})</span>`).join("; ")}.
      The clinical rings are a pipeline statement, never a safety one.</div>`;
  }
  if (d.attribution_error) {
    h += `<div class="note warn">Indication attribution could not be evaluated for this
      gene, so the furthest-phase ring is <code>failed</code> rather than a number:
      ${esc(d.attribution_error)}</div>`;
  }
  if (drugs.length) {
    h += `<div class="note">An approved drug that hits this target is not the same as
      this target being clinically validated — polypharmacology defeats a mechanism
      filter alone. The indication is shown beside the phase for exactly that reason.</div>
      <div class="scroll-x"><table><thead><tr><th>Drug</th><th>Modality</th><th>Furthest phase</th>
      <th>Current status</th><th>Indication</th><th>Trials</th><th>Stopped</th>
      <th>Link</th></tr></thead><tbody>` +
      drugs.map((r) => `<tr>
        <td><b>${esc(r.drug)}</b></td>
        <td>${esc(r.modality || "—")}</td>
        <td>${esc(r.max_stage || "—")}</td>
        <td>${esc(r.status || "—")}${r.n_with_status === 0
          ? " <span class='hint'>(no trial row carries a status)</span>" : ""}</td>
        <td>${esc((r.indications || []).join("; ") || "—")}</td>
        <td>${r.n_trials}</td>
        <td class="reason">${esc(Object.entries(r.stop_reasons || {})
          .map(([k, v]) => `${k} ${v}`).join(", ") || "—")}</td>
        <td>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">open</a>` : "—"}</td>
      </tr>`).join("") + `</tbody></table></div>`;
  }

  // --- trials: sponsor, live status, categorised stops (v2.2 item 2, re-sourced
  // in v2.4 to Open Targets' own clinicalReports rows) ---
  // "sponsor plus live status must reach the drawer, since that is what tells
  // the user a target is crowded."
  const tr = d.trials || {};
  const trDrugs = (tr.drugs || []).filter((r) => (r.n_trials || 0) > 0 || r.error);
  if (tr.source) {
    h += `<h4>trials — ${esc(tr.source)}</h4>
      <p class="summary-line">${tr.n_lead_sponsors || 0} distinct lead sponsor(s)
        across this target's drugs${(tr.lead_sponsors || []).length
          ? ": " + tr.lead_sponsors.slice(0, 6).map(esc).join(", ") : ""}.</p>`;
    // v2.4 requirement 1: the source labels actually seen, so the next
    // relabelling is visible in the drawer instead of emptying a ring.
    if (tr.sources_seen && Object.keys(tr.sources_seen).length) {
      h += `<p class="summary-line hint">report source labels on this target:
        ${Object.entries(tr.sources_seen).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</p>`;
    }
    // v2.3 item 2. The target-level totals are DISTINCT NCT ids; the sum of the
    // rows below is drug-trial pairs. Both are shown and the note says which is
    // which, because this panel was read once with only the pair count on it.
    if (tr.n_distinct_trials !== undefined) {
      h += `<p class="summary-line"><b>${tr.n_distinct_trials}</b> distinct trial(s)
        <span class="hint">(${tr.n_pairs} drug-trial pairs)</span> ·
        <b>${tr.n_distinct_live}</b> live <span class="hint">(${tr.n_pairs_live} pairs)</span> ·
        <b>${tr.n_distinct_stopped}</b> stopped early
        <span class="hint">(${tr.n_pairs_stopped} pairs)</span>${
          tr.distinct_is_a_floor ? " — <b>a floor</b>" : ""}</p>
        <div class="note">${esc(tr.dedup_note || "")}</div>`;
    }
    h += `<div class="note">${esc(tr.note || "")}</div>`;
    if (trDrugs.length) {
      h += `<div class="scroll-x"><table><thead><tr><th>Drug</th><th>Trials</th>
        <th>Live</th><th>Stopped early</th><th>Lead sponsors (live)</th>
        <th>Live example</th></tr></thead><tbody>` +
        trDrugs.map((r) => `<tr>
          <td><b>${esc(r.drug)}</b></td>
          <td>${r.error ? "—" : r.n_trials}${
            !r.error && r.n_distinct !== undefined && r.n_distinct !== r.n_trials
              ? ` <span class="hint">(${r.n_distinct} distinct NCT)</span>` : ""}</td>
          <td>${r.error ? "—" : r.n_live}</td>
          <td>${r.error ? "—" : r.n_stopped}${
            r.n_for_cause ? ` <span class="hint">(${r.n_for_cause} for cause)</span>` : ""}</td>
          <td>${esc((r.sponsors || []).slice(0, 3).join("; ") || "—")}</td>
          <td>${(r.live || []).length
            ? `<a href="${esc(r.live[0].url || "#")}" target="_blank" rel="noopener">${
                esc(r.live[0].nct_id)}</a> ${esc(r.live[0].status)}
               <span class="hint">${esc((r.live[0].conditions || []).join(", "))}</span>`
            : r.error ? `<span class="hint">${esc(r.error)}</span>` : "—"}</td>
        </tr>`).join("") + `</tbody></table></div>`;
      const stopped = trDrugs.flatMap((r) => (r.stopped || []).map((s) => ({...s, drug: r.drug})))
        .filter((s) => s.why_stopped || (s.categories || []).length).slice(0, 6);
      if (stopped.length) {
        // v2.4: Open Targets categorises the stop, so the category leads and the
        // sponsor's free text follows it verbatim for a human to read — rather
        // than the free text standing alone with nothing to anchor it.
        h += `<p class="summary-line">Why trials stopped — <b>Open Targets' category</b>
          first, then the sponsor's own words verbatim:</p><ul class="reason">` +
          stopped.map((s) => `<li><code>${esc(s.nct_id || "—")}</code> ${esc(s.drug)}:
            <b>${esc((s.categories || []).join(", ") || "no category")}</b>${
              s.for_cause ? ` <span class="hint">(for cause)</span>` : ""}
            — “${esc(s.why_stopped)}”</li>`).join("") + `</ul>`;
      }
    }
  }

  // --- literature ---
  // UX B5: "the drug table immediately above links out to DailyMed,
  // ClinicalTrials.gov and TTD… 'get to a gene's papers' dead-ends at a number."
  const ptUrl = "https://www.ncbi.nlm.nih.gov/research/pubtator3/?query=" +
    encodeURIComponent(lit.pubtator_query || "");
  const epmcUrl = "https://europepmc.org/search?query=" +
    encodeURIComponent(lit.europepmc_query || "");
  h += `<h4>literature</h4><dl class="kv">
    <dt>PubTator3 records</dt><dd>
      <a href="${esc(ptUrl)}" target="_blank" rel="noopener">${lit.pubtator_count ?? "—"}</a>
      <span class="hint">gene-id resolved — opens the result list</span></dd>
    <dt>query</dt><dd><code>${esc(lit.pubtator_query || "")}</code></dd>
    <dt>Europe PMC</dt><dd>
      <a href="${esc(epmcUrl)}" target="_blank" rel="noopener">${lit.europepmc_count ?? "—"}</a>
      ${lit.europepmc_error ? `<span class="hint">— ${esc(lit.europepmc_error)}</span>` : ""}
      ${lit.europepmc_echo_live === false
        ? `<span class="hint">— replayed from cache, not checked this run</span>` : ""}
      <br><code>${esc(lit.europepmc_query || "")}</code>
      <br><span class="hint">A count, not evidence: COUNCIL M6 measured it swinging 44%
      on phrasing alone. It sets no ring value.</span></dd></dl>`;
  const years = Object.entries(lit.years || {}).sort((a, b) => b[0].localeCompare(a[0]));
  if (years.length) {
    h += `<p class="hint">by year: ${years.slice(0, 8)
      .map(([y, n]) => `${esc(y)}: ${n}`).join(" · ")}</p>`;
  }

  // --- rodent lead (COUNCIL M4) ---
  h += `<h4>mouse phenotype lead</h4>`;
  const mp = d.mouse_phenotypes || [];
  h += mp.length
    ? `<div class="note warn">A lead, not a verdict, and never folded into any score.
       The ring is three states — not phenotyped / phenotyped-and-normal /
       phenotyped-with-abnormal. The count below is context, not a score
       (COUNCIL V9), and the source is mouse only, not rat (COUNCIL V15).</div>
       ${phenotypeTermListHtml(d.phenotype_terms, mp)}`
    : `<p class="hint">No mouse phenotype records — not phenotyped, which is not the
       same as “no phenotype”. Embryonic-lethal lines drop out of adult phenotyping,
       which biases exactly the essential genes the safety ring flags as risky
       (COUNCIL M5).</p>`;

  if (d.triage) h += triageHtml(d.triage);
  else h += `<div class="note">LLM triage of the rodent lead was not run for this gene
             — see the run manifest.</div>`;

  // --- phenotype table (stage 7). Beside the rodent lead, not a new page or a
  // step in the stepped flow (PLAN §9) — a second, lazy-loaded panel so a
  // gene whose drawer is never opened for this never fetches anything, let
  // alone spends anything. ---
  h += `<h4>mouse phenotype table (MGI, via Open Targets × Europe PMC)</h4>
    <div id="phenotype-panel" data-gene="${esc(d.gene)}">
      <p class="hint">One row per (phenotype, model, paper) MGI curates for this gene —
        the ring's raw evidence, not its reduction — plus an LLM paraphrase of exactly
        those rows, with its own $5 cap, separate from triage's.</p>
      <button type="button" class="btn" onclick="loadPhenotypes('${esc(d.gene)}')">Load
        phenotype table &amp; summary</button>
    </div>`;

  // --- safety ---
  const sl = d.safety_liabilities || [];
  h += `<h4>safety liabilities</h4>` + (sl.length
    ? `<ul class="tight">${sl.map((s) =>
        `<li>${esc(s.event)} <span class="hint">${esc(s.datasource || "")}</span></li>`)
        .join("")}</ul>`
    : `<p class="hint">No rows. Open Targets' safety records are sparse — absence here is
       weak evidence of safety.</p>`);

  // --- ChEMBL, explicitly untrusted ---
  h += `<h4>ChEMBL mechanisms</h4>
        <div class="note warn">${esc(ch.untrusted_reason || "")}</div>`;
  if (ch.note) h += `<p>${esc(ch.note)}</p>`;
  if ((ch.rows || []).length) {
    h += `<p class="hint">${ch.n_direct_efficacy} of ${ch.n_mechanisms} rows are
      <code>direct_interaction=1</code> and <code>disease_efficacy=1</code>
      (target ${esc(ch.target_chembl_id)}).</p>
      <table><thead><tr><th>Molecule</th><th>Action</th><th>Direct</th><th>Efficacy</th>
      <th>Max phase</th><th>Mechanism</th></tr></thead><tbody>` +
      ch.rows.map((m) => `<tr>
        <td>${m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.molecule)}</a>`
          : esc(m.molecule)}</td>
        <td>${esc(m.action_type || "—")}</td><td>${m.direct_interaction}</td>
        <td>${m.disease_efficacy}</td><td>${esc(m.max_phase ?? "—")}</td>
        <td class="reason">${esc(m.mechanism_of_action || "")}</td></tr>`).join("") +
      `</tbody></table>`;
  }

  // --- every cell, with its state and why ---
  h += `<h4>every ring, with its state</h4><table><thead><tr><th>Ring</th><th>State</th>
        <th>Value</th><th>Why</th></tr></thead><tbody>` +
    Object.entries(d.cells || {}).map(([k, c]) => `<tr>
      <td>${esc(k)}</td>
      <td><span class="pill ${c.state === "value" ? "ok"
        : c.state === "failed" ? "bad" : "grey"}">${esc(c.state)}</span></td>
      <td>${c.value == null ? "—" : (+c.value).toPrecision(4)}</td>
      <td class="reason">${esc(fmtPInText(c.detail))}</td></tr>`).join("") +
    `</tbody></table>`;
  return h;
}

// ---------- phenotype page (stage 7) ----------
//
// Lazy-loaded from `/api/run/<id>/gene/<gene>/phenotypes`, separate from the
// main drawer fetch, so a drawer that is opened but whose phenotype panel is
// never clicked makes neither the Open Targets/Europe PMC calls nor — the
// part that actually costs money — the LLM call. Four spend accounts exist
// (rule 8, amended twice on 2026-10-09) and this panel only ever shows the
// `phenotype` one; the others live in the "Paid calls" section above and this
// code never touches them.

async function loadPhenotypes(gene) {
  const panel = document.querySelector('#phenotype-panel[data-gene="' +
    CSS.escape(gene) + '"]') || $("#phenotype-panel");
  panel.innerHTML = `<p class="hint">Loading…</p>`;
  let data;
  try {
    data = await fetch(`/api/run/${RUN.run_id}/gene/${encodeURIComponent(gene)}/phenotypes`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
  } catch (e) {
    panel.innerHTML = `<div class="note bad">Could not load the phenotype table: ${esc(String(e))}</div>`;
    return;
  }
  panel.innerHTML = phenotypeSectionHtml(data);
}

function phenotypeSectionHtml(d) {
  if (d.state === "none found") {
    return `<p class="hint">No mouse phenotype records for this gene — not phenotyped,
      which is not the same as "no phenotype". No LLM call was made
      (PLAN §3 stage 7a: a gene with zero rows stops here).</p>`;
  }

  const rows = d.rows || [];
  const counts = d.level_counts || {};
  let h = `<p class="summary-line">${rows.length} row(s)
      <span class="hint">(${Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ")})</span>
      · ${d.n_distinct_pmids} distinct PMID(s), ${d.n_articles_fetched} of up to
      ${d.max_articles_cap} fetched from Europe PMC.</p>`;

  h += `<div class="scroll-x" style="max-height:340px;overflow-y:auto">
      <table><thead><tr><th>MP term</th><th>level</th><th>model</th><th>PMID</th>
      <th>title / year</th><th>sentence naming the gene</th></tr></thead><tbody>` +
    rows.map((r) => `<tr>
      <td>${esc(r.mp_label)}${r.mp_id ? ` <span class="hint">${esc(r.mp_id)}</span>` : ""}</td>
      <td><span class="pill ${r.level === "asserted" ? "ok" : "grey"}">${esc(r.level)}</span>
        <div class="hint">${esc(r.level_label)}</div></td>
      <td>${esc(r.model_id || "no model on record")}</td>
      <td>${r.pmid
        ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(r.pmid)}/" target="_blank"
             rel="noopener">${esc(r.pmid)}</a>`
        : "no PMID on record"}</td>
      <td>${esc(r.title || "—")}${r.year ? ` <span class="hint">(${esc(r.year)})</span>` : ""}
        <div class="hint">${esc(r.abstract_status || "")}</div></td>
      <td>${esc(r.sentence || "—")}</td>
    </tr>`).join("") + `</tbody></table></div>`;

  h += `<p class="hint">Source: ${(d.source || []).map((s) => esc(s.legend_text)).join(" · ")}</p>`;

  h += phenotypeSummaryHtml(d.summary, d.summary_max_rows_cap, rows.length);
  return h;
}

function phenotypeSummaryHtml(s, capRows, totalRows) {
  if (!s || !s.attempted) {
    return `<div class="note">${esc((s && s.reason) || "summary not attempted")}</div>`;
  }
  const acct = s.account || {};
  const acctLine = acct.cap !== undefined
    ? `<p class="hint">phenotype account: $${(+acct.spent).toFixed(2)} of
        $${(+acct.cap).toFixed(2)} spent over ${acct.calls} call(s)
        (${acct.remaining !== undefined ? "$" + (+acct.remaining).toFixed(2) + " remaining" : ""})</p>`
    : "";

  if (!s.ok) {
    return `<div class="note bad"><b>summary refused:</b> ${esc(s.reason || "")}</div>${acctLine}`;
  }

  const rowCap = totalRows > capRows
    ? `<p class="hint">summary computed over the first ${capRows} of ${totalRows} table rows
        (<code>PHENOTYPE_SUMMARY_MAX_ROWS</code>) — the table above shows every row
        regardless.</p>`
    : "";

  return `<h4 style="margin-top:.75em">summary</h4>
    <p class="hint">model ${esc(s.model)} · prompt ${esc(s.prompt_hash)} ·
      ${s.input_tokens} in / ${s.output_tokens} out
      ${s.cached ? ` · <b>cached ${esc(s.cached_at)}</b> (no charge)`
        : ` · $${(+s.cost_usd).toFixed(4)} this call`}
      · ${esc(s.note)}</p>
    <p class="hint">sends only: ${esc(s.sends_only)}</p>
    ${rowCap}${acctLine}
    <ul class="tight">${(s.claims || []).map((c) => `<li>${esc(c.text)}
      <span class="hint">(rows ${c.row_ids.join(", ")} — “${esc(c.quote)}”)</span></li>`)
      .join("")}</ul>`;
}

// ---------- table view (v3.6) ----------
//
// The same run the circle was drawn from, as a grid. Three rules shape every
// line below and none of them is presentational:
//
//   1. It is a SECOND RENDERING, not a second computation. Every state, number,
//      colour and detail string arrives from `/api/run/<id>/values`, which
//      pivots `values.csv` -- the artefact invariant 1 asserts is cell-for-cell
//      what was drawn. Nothing here derives a cell.
//   2. SORTING IS THE SERVER'S. `order.order_by_keys` decides the row order, and
//      its single-key case is asserted equal to `order_for_figure`, the function
//      that places the spokes. Sorting the rows in JS would be a second
//      definition of "sorted by logFC" and the two would eventually disagree.
//   3. MISSING IS NOT LOW. A gene with no value on the sort key is drawn in a
//      named block below a divider, in both directions, and its cell is hatched
//      and labelled -- never blank, never 0, never sorted as the smallest.

let TABLE = { run_id: null, sort: [], dim: [], data: null };

// `#e8e8e8` and `#385b5c` both come back as cell fills, so the label on top of a
// swatch has to pick its own side. Rec. 601 luma, the usual mid-point.
// v4.0. WCAG relative luminance, and the ink that actually wins against this
// fill — not a perceived-brightness approximation with a hand-picked cut.
//
// The cell's background is not negotiable: it is the colour `values.csv`
// recorded, which is the colour the arc was painted, and the table is not
// choosing a palette (see `.grid td.st-value` in app.css). So the only thing
// that can move is the ink, and it has to move by the right measure.
//
// The old version used 0.299/0.587/0.114 over a 0.6 threshold, which is neither
// of WCAG's two formulas. The UI audit measured what that shipped: white on
// rgb(117,146,230) at 3.00:1, on rgb(143,155,100) at 2.98:1, on rgb(186,132,165)
// at 3.03:1 — six value cells under AA's 4.5:1 at 12.5 px, every one of which
// black clears comfortably (7.0:1, 7.0:1, 5.1:1). The threshold was wrong, not
// the idea.
//
// Both candidates are computed and the better one is returned, so a mid-tone
// fill where neither reaches 4.5:1 still gets the most legible of the two rather
// than whichever side of a constant it fell on.
function wcagLuminance(r, g, b) {
  const f = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function inkOn(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return "var(--ink)";
  const n = parseInt(m[1], 16);
  const bg = wcagLuminance((n >> 16) & 255, (n >> 8) & 255, n & 255);
  // #16181d is `--tr-ink`; its luminance is computed rather than written down so
  // the two cannot drift apart.
  const inkL = wcagLuminance(0x16, 0x18, 0x1d);
  const withDark = (Math.max(bg, inkL) + 0.05) / (Math.min(bg, inkL) + 0.05);
  const withWhite = (1.0 + 0.05) / (bg + 0.05);
  return withDark >= withWhite ? "#16181d" : "#ffffff";
}

// Four significant figures, trailing zeros dropped: `2.81`, `5`, `0.02312`. The
// drawer prints `toPrecision(4)` and this is the same number without the `5.000`.
const num = (v) => String(Number((+v).toPrecision(4)));

// ---------------------------------------------------------------------------
// An adjusted p, as a reader reads one (v4.2).
//
// Puck, 2026-10-19 18:03 PDT: "the small ps should just show less than the cut
// off: <0.05 (or whatever is standard for this kind of analysis)". 0.05 is the
// standard FDR cutoff for differential expression and it is already this
// build's own rule, so under it the print is the inequality: `2.19538e-22` is
// an exponent nobody acts on in the place he reads the number. At or above it,
// the real decimal at two significant digits — never an exponent.
//
// This is the same format and the same cutoff as `targetring/pvalue.py`, which
// writes the p into the verdict sentences server-side. Two implementations of
// one format is a thing that drifts, so the pair is asserted equal in
// `tests/test_pvalue.py` — the test reads THIS file and runs the cases through
// both. Display only: nothing sorts on it, no rule is evaluated against it,
// and no stored number is replaced by it.
//
// `P_CUTOFF` MIRRORS `config.RECOMMEND_MAX_Q` (= `pvalue.P_CUTOFF`), which is
// also the bound `/api/criteria` publishes as `thresholds.max_q`. It is a
// constant rather than a read of that payload because `fmtP` runs inside the
// first synchronous render, before the fetch resolves — a cell must not print
// one cutoff and then another. `tests/test_pvalue.py` asserts the mirror.
const P_CUTOFF = 0.05;
const P_SIG_DIGITS = 2;
const P_MISSING = "—";

// The cutoff as it appears inside `<0.05`: the bound with the rule's own
// digits, not padded out to two significant ones.
function pCutoffText(cutoff) {
  return String(Number(cutoff === undefined ? P_CUTOFF : cutoff));
}

// `v` to `sig` significant digits as a plain decimal, never an exponent.
// `toPrecision` would give `5.0e-2` for 0.05, which is the one thing the spec
// rules out, so the decimal count is computed and `toFixed` does the printing.
function pDecimal(v, sig) {
  const n = sig === undefined ? P_SIG_DIGITS : sig;
  const exp = Math.floor(Math.log10(v));
  let s = v.toFixed(Math.max(0, n - 1 - exp));
  // Rounding can cross a power of ten (0.0999 -> "0.100", three digits shown
  // for two digits of information). Re-measure and print at its own magnitude.
  const rounded = Number(s);
  if (rounded > 0) {
    const exp2 = Math.floor(Math.log10(rounded));
    if (exp2 !== exp) s = rounded.toFixed(Math.max(0, n - 1 - exp2));
  }
  return s;
}

// Declared with `function` rather than `const`: it is called from
// `geneRowHtml` and `drawerHtml`, both above this line.
function fmtP(p, missing) {
  const miss = missing === undefined ? P_MISSING : missing;
  if (p === null || p === undefined || p === "" || typeof p === "boolean") return miss;
  const v = Number(p);
  if (!Number.isFinite(v)) return miss;
  // Not a p-value. `de_input` rejects these on the way in, so one cannot get
  // here; if one does it prints as itself rather than as a plausible `<0.05`.
  if (v < 0 || v > 1) return String(v);
  if (v >= 1) return "1";
  if (v < P_CUTOFF) return "<" + pCutoffText();
  const s = pDecimal(v);
  // 0.999 rounds to "1.0", the one output with a non-zero digit before the
  // point. It is a 1.
  return s.startsWith("1") ? "1" : s;
}

// The same p, inside a sentence somebody else wrote. `run.py` records the
// `neg_log10_padj` cell as "adjusted p 2.19538e-22 (submitted)" and that string
// is the CARRIER — `drawer_bake._padj` and `summary_llm.build_facts` read the
// exact number back out of it, so it is never reformatted on disk. It is
// reformatted here, on the way to the screen, in the one place it is read.
const P_IN_TEXT = /\badjusted p ([0-9][0-9.eE+-]*)/g;

//: The same pattern, matched ONCE and off its own lastIndex — `P_IN_TEXT` is
//: `/g` and stateful, so `exec`ing it from a second caller would start partway
//: through a string. Built from `.source` rather than written twice: one
//: pattern, read two ways.
const P_IN_TEXT_ONCE = new RegExp(P_IN_TEXT.source);

function fmtPInText(s) {
  return String(s == null ? "" : s).replace(P_IN_TEXT, (whole, n) => {
    const v = Number(n);
    return Number.isFinite(v) && v >= 0 && v <= 1 ? "adjusted p " + fmtP(v) : whole;
  });
}

// The submitted adjusted p behind a `neg_log10_padj` cell. THE SAME TWO STEPS
// AS `drawer_bake._padj`, which is the Python half of this: the run writes the
// exact number into the cell's own detail ("adjusted p 2.19538e-22 (submitted)")
// and that string is the carrier, so it is read back out of there and only
// back-transformed from the drawn value where a run is too old to carry it.
//
// `10 ** -x` rather than a stored second number: the cell holds one measurement
// and inventing a second field for it is how two numbers come to disagree. Null
// where the cell has neither, and `fmtP` prints that as the em dash — never as
// a small p.
function cellPadj(c) {
  if (!c) return null;
  const m = P_IN_TEXT_ONCE.exec(c.detail || "");
  if (m) {
    const v = Number(m[1]);
    if (Number.isFinite(v)) return v;
  }
  if (c.state === "value" && c.value !== null && c.value !== undefined) {
    const x = Number(c.value);
    if (Number.isFinite(x)) return Math.pow(10, -x);
  }
  return null;
}

// Never blank, never zero (AGENTS.md rule 1). Every non-value state gets a word.
const STATE_WORD = { no_data: "no data", not_applicable: "n/a", failed: "FAILED" };

// ---------------------------------------------------------------------------
// ONE CELL TOOLTIP, for both renderings of the same cell (v4.2).
//
// Puck, 2026-10-19: "the value should show on hover" and the cells "should also
// have tool tips that show the value and an explanation of the cell". A ring
// cell on the circle and the same cell in the grid are the same measurement, so
// there is one function that writes what a cell says and two ways of showing it
// — `title` on the table's `<td>`, a positioned box over the SVG, which has no
// `title` a browser will render.
//
// Every word in it is somebody else's: the ring's own label, units and source
// name come from the payload (`view.table_payload`'s rings, or the scales route
// that feeds the legend beside the circle), and the last line is the adapter's
// own `detail` for that cell. Nothing here describes a ring in words this file
// invented — a second description of a measurement is the drift this build
// refuses everywhere else.
// ---------------------------------------------------------------------------

// ring key -> what the legend and the column head say about that ring.
let RING_INFO = {};
// "<ring>|<gene>" -> the cell as `values.csv` recorded it.
let CELL_INFO = {};

function rememberRings(rings) {
  (rings || []).forEach((r) => {
    const was = RING_INFO[r.key] || {};
    RING_INFO[r.key] = {
      n: r.n ?? was.n,
      label: r.label || was.label || r.key,
      column_label: r.column_label || was.column_label || r.label || r.key,
      units: r.units || was.units || "",
      transform: r.transform || was.transform || "",
      // The grid calls it `source_name`; the scales route calls it
      // `source_text` and that is the longer, linked form. Either is the
      // source, and whichever arrived last is the one with more in it.
      source: r.source_text || r.source_name || was.source || "",
    };
  });
}

function rememberCells(genes) {
  (genes || []).forEach((g) => {
    Object.entries(g.cells || {}).forEach(([key, c]) => {
      if (c) CELL_INFO[`${key}|${g.gene}`] = c;
    });
  });
}

// The value with its unit, or the state in words. Never a bare number: a cell
// whose unit is not on the hover is a number a reader has to guess at.
function cellValueWords(c, ring, ringKey) {
  if (!c) return "no cell";
  if (c.state !== "value") return STATE_WORD[c.state] || c.state;
  const u = (ring && ring.units) || "";
  const raw = num(c.value) + (u ? " " + u : "");
  // v4.2. Column 2's cell prints the p, so the hover leads with the same string
  // the cell does — and keeps the drawn -log10 value after it, unchanged and
  // with its unit. The colour on this band is on that scale and `<0.05` is a
  // bound rather than a magnitude, so the exact number stays one hover away.
  if (ringKey === PADJ_RING) {
    const p = cellPadj(c);
    if (p !== null) return fmtP(p) + " adjusted p · " + raw;
  }
  return raw;
}

function cellTooltip(ringKey, gene) {
  const r = RING_INFO[ringKey] || { column_label: ringKey, label: ringKey,
                                    units: "", source: "" };
  const c = CELL_INFO[`${ringKey}|${gene}`];
  const head = `${r.n ? r.n + ". " : ""}${r.column_label} · ${gene}`;
  const value = cellValueWords(c, r, ringKey);
  // The one sentence: what this ring measures, in the words the legend uses,
  // and where the number came from.
  const measures = `${r.label}${r.units ? ", in " + r.units : ""}${
    r.transform ? " (" + r.transform + ")" : ""}${
    r.source ? " — source: " + r.source : ""}.`;
  // `fmtPInText`: the `neg_log10_padj` cell's detail is "adjusted p 2.19538e-22
  // (submitted)", written by `run.py` and read back as data by
  // `drawer_bake._padj`, so it is reformatted HERE, on the way to the screen,
  // and never on disk. The value line above now leads with that same `<0.05`
  // and keeps the drawn −log10 number after it (Puck, 2026-10-19: "i didn't
  // see it in the table"), so this line still names the source of the p —
  // "(submitted)" — and the two cannot print different numbers.
  const detail = c && c.detail ? fmtPInText(c.detail) : "";
  return [head, value, measures, detail].filter(Boolean).join("\n");
}

// The SVG half. A `<title>` child inside an inlined SVG does render as a
// tooltip, but only after the browser's own delay and with no control over
// where it lands on a 900 px circle, so the box is positioned at the pointer.
function cellTipBox() {
  let el = $("#cell-tip");
  if (!el) {
    el = document.createElement("div");
    el.id = "cell-tip";
    el.hidden = true;
    el.setAttribute("role", "tooltip");
    document.body.appendChild(el);
  }
  return el;
}

function showCellTip(text, x, y) {
  const el = cellTipBox();
  el.textContent = text;
  el.hidden = false;
  // Clamped, so a cell at the right or bottom edge of the circle does not put
  // its own explanation off the screen.
  const box = el.getBoundingClientRect();
  const left = Math.min(x + 14, window.innerWidth - box.width - 8);
  const top = Math.min(y + 16, window.innerHeight - box.height - 8);
  el.style.left = Math.max(8, left) + "px";
  el.style.top = Math.max(8, top) + "px";
}

function hideCellTip() {
  const el = $("#cell-tip");
  if (el) el.hidden = true;
}

// Wired once on `#figure`, which survives every `innerHTML` the figure pane
// gets, so a redraw does not need re-wiring and cannot double-wire.
function wireFigureTooltips() {
  const fig = $("#figure");
  if (!fig || fig.dataset.tipWired) return;
  fig.dataset.tipWired = "1";
  fig.addEventListener("mousemove", (e) => {
    const el = e.target && e.target.closest ? e.target.closest('[id^="cell|"]') : null;
    if (!el) { hideCellTip(); return; }
    const bits = el.id.split("|");
    showCellTip(cellTooltip(bits[1], bits[3]), e.clientX, e.clientY);
  });
  fig.addEventListener("mouseleave", hideCellTip);
  addEventListener("scroll", hideCellTip, { passive: true });
}

function tableSortSpec() {
  return TABLE.sort.map((k) => k.ordering + (k.reverse ? ":asc" : ":desc")).join(",");
}

// v3.8. The dim filters, as the server's own `ring >= n` lines. The COMPARISON is
// not done here for the same reason the sort is not: a threshold evaluated in the
// browser would be a second definition of `regional_specificity >= 5`, and it
// would drift from the one PLAN §3.2 uses. The page collects the bound and draws
// the verdict; `dim.classify` decides it.
function tableDimSpec() {
  return TABLE.dim.map((f) => `${f.ring} ${f.op === "le" ? "<=" : ">="} ${f.threshold}`)
    .join("\n");
}

async function loadTable(runId) {
  const id = (runId || "").trim();
  if (!id) {
    $("#table-status").textContent =
      "No run loaded. Draw a figure, or type a run id above — every run under " +
      "runs/ can be opened by id.";
    return;
  }
  $("#table-status").textContent = `loading ${id}…`;
  let res, d;
  try {
    res = await fetch(`/api/run/${encodeURIComponent(id)}/values`
      + `?sort=${encodeURIComponent(tableSortSpec())}`
      + `&dim=${encodeURIComponent(tableDimSpec())}`);
    d = await res.json();
  } catch (e) {
    $("#table-status").className = "err";
    $("#table-status").textContent = "Request failed: " + e;
    return;
  }
  if (!res.ok) {
    // A sort this run cannot honour is refused, not swapped: the previous table
    // stays on screen rather than being redrawn in an order the strip does not
    // name. Same rule as `normalise_ordering` on the figure.
    $("#table-status").className = "err";
    $("#table-status").textContent = errText(d, "the table could not be loaded");
    if (d.error === "sort_rejected") TABLE.sort = [];
    // Same rule for a dim filter this run cannot evaluate: dropped rather than
    // approximated, so the count line can never name a bound the grid did not use.
    if (d.error === "dim_rejected") TABLE.dim = [];
    return;
  }
  $("#table-status").className = "";
  $("#table-status").textContent = "";
  TABLE.run_id = d.run_id;
  TABLE.data = d;
  $("#table-run").value = d.run_id;
  renderTable(d);
  // A run opened from disk still gets its ledger and its invariants, from its
  // own manifest — otherwise those two tabs would describe a different run than
  // the grid on screen, which is worse than showing nothing.
  if (!RUN) {
    renderLedger(d);
    renderInvariants(d);
    // And its caveats. This call was missing, so `What this is not` was an
    // empty pane for every `?run=` and every demo run (found 2026-10-18); the
    // payload carries `not_attempted`, `ordering_note` and `llm` from the
    // run's own manifest for exactly this.
    renderCaveats(d);
    // v4.0: and its downloads. `render()` is what normally fills the three
    // download menus, and it never runs on this path — so every item in them
    // had `href=null` and the "every download" the demo claims was ten dead
    // links (UI audit, 2026-10-19). The URLs are the artefact routes for THIS
    // run id, which is the same shape `view.py` builds them in.
    diskRunDownloads(d.run_id, d.artefacts);
    // The figure pane's "click a gene" sentence. v4.0 inlines the run's own
    // `figure-web.svg`, so on the tool path the circle IS clickable and the old
    // sentence ("No figure is drawn in this session") was false on the one
    // surface that matters most. `showToolFigure` is what knows whether the
    // inline succeeded, so it sets this; here we only handle the case where it
    // did not run or had nothing to draw.
    const hint = $("#figure-click-hint");
    if (hint && !$("#figure").querySelector('[id^="cell|"]')) {
      hint.classList.add("unavailable");
      hint.textContent = (d.baked_drawers || []).length
        ? "No figure is drawn in this session. Open a gene from the Table tab — its drawer is read from disk."
        : "No figure is drawn in this session, and the per-gene drawer needs the run in memory, so nothing here opens one.";
    }
  }
}

// v4.0. The three download menus, for a run read off disk.
//
// `render()` fills them from the analyse response's own `*_url` fields and never
// runs on this path, so every item in them carried `href=null`: ten links that
// looked like links and did nothing, on a page whose list of what works says
// "every download". Found by the UI audit, 2026-10-19.
//
// Which files exist is read from the payload's own `artefacts` list
// (`table.artefacts_on_disk`), not probed from here: ten HEAD requests to find
// out put two 404s in the console of a page whose claim is that it fetches
// nothing. An item whose file the run never wrote is REMOVED rather than left
// pointing at a 404 — the slide pair is an optional export, and a menu item
// that 404s is the dead link this function exists to fix, one step on.
function diskRunDownloads(runId, artefacts) {
  if (!runId) return;
  const base = `/api/run/${encodeURIComponent(runId)}`;
  const files = {
    "dl-png": "figure.png", "dl-svg": "figure.svg",
    "dl-slide": "figure-slide.svg", "dl-slide-png": "figure-slide.png",
    "dl-caption": "caption.txt",
    "dl-legend-png": "legend.png", "dl-legend": "legend.svg",
    "dl-csv": "values.csv", "dl-ledger": "ledger.csv",
    "dl-manifest": "manifest.json",
  };
  // No list at all (a payload from before v4.0) is not a reason to delete the
  // whole menu: the hrefs are set and whatever is missing 404s on click, which
  // is what it did before this function existed.
  const have = artefacts === undefined || artefacts === null
    ? null : new Set(artefacts);
  for (const [id, name] of Object.entries(files)) {
    const el = $("#" + id);
    if (!el) continue;
    if (have && !have.has(name)) { el.remove(); continue; }
    el.href = `${base}/${name}`;
  }
  // A menu left with nothing in it is an empty box, which is the other half of
  // this audit. The whole `<details>` goes.
  document.querySelectorAll("#pane-figure details.dl").forEach((d) => {
    if (!d.querySelector(".dl-menu a")) d.remove();
  });
  const run = $("#dl-run");
  if (run) run.textContent = `All downloads are run ${runId}.`;
}

function renderTable(d) {
  const ring = (k) => d.rings.find((r) => r.key === k);
  // v4.2. What every cell says on hover, here and on the circle, is written
  // from these two — see `cellTooltip`.
  rememberRings(d.rings);
  rememberCells(d.genes);
  // v3.7 item 4. `column_label`, so a sort chip names the column the way the
  // column head does and the way the scale column names the same ring.
  const ringLabel = (o) => o === "alphabetical" ? "gene symbol"
    : o === "submitted" ? "your submitted order"
    : ((ring(o.slice(5)) || {}).column_label || o.slice(5));

  // --- what this table is, before any of it ---
  const bad = d.any_invariant_failed;
  const cnt = d.counts;
  $("#table-meta").innerHTML =
    (bad ? `<div id="table-banner" class="banner-bad">DO NOT TRUST THIS TABLE —
       an invariant failed on run ${esc(d.run_id)}. See the Invariants tab.</div>` : "") +
    `<div class="counts">
       <b>${esc(cnt.input)}</b> genes in · <b>${esc(cnt.rendered)}</b> in this table ·
       <button class="drop linkish" id="table-show-ledger"><b>${esc(cnt.dropped)}</b>
         not shown — why?</button> ·
       region <b>${esc(d.region)}</b> ·
       <span class="pill ok">input = rendered + ledgered</span>
       <span class="hint">${esc(cnt.input)} = ${esc(cnt.rendered)} + ${esc(cnt.dropped)},
         checked against this run's own artefacts before the table was built</span>
     </div>` +
    ((d.filters || []).length
      ? `<div class="note warn"><b>Filtered — this is not the whole submitted set.</b>
         ${d.filters.map((f) => `<code>${esc(f.expression)}</code>`).join(" · ")}</div>`
      : "") +
    `<p class="hint">The circle for this run was drawn with —
       <b>${esc(d.ordering_note || d.ordering)}</b>. Columns are the rings in the
       figure's own radial order: <b>column 1 is the outermost band</b>.
       ${/* Said, not resolved silently (`citations._drift`'s rule). The
             sentence above is re-resolved from the run's own contrast block,
             so a reader with this run's manifest.json open must be told why
             the two disagree rather than finding it. */
         d.ordering_note_drift
          ? `<br><span class="src">${esc(d.ordering_note_drift)}</span>` : ""}
       ${d.submitted_order_source === "drawn"
        ? `This run predates <code>submitted_order</code> in the manifest, so the
           tie-break and the no-value block use the order it was drawn in.` : ""}
       ${/* v4.0. The sentence and the links are the payload's, not this file's.
             They used to be three hardcoded `/api/…` hrefs, which are correct in
             the app and dead in the offline export -- a file opened from `file://`
             has nothing serving them, and a dead link is the thing the citation
             strip refuses one block further down. `view.table_payload` authors
             what this surface can and cannot do; `export.py` authors the other
             answer. See `view.DRAWER_ON_DISK_NOTE`. */
         esc(d.drawer_note || "")}
       ${(d.artefact_links || []).map((a) =>
         `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.label)}</a>`)
         .join(" · ")}
     </p>`;

  const sl = $("#table-show-ledger");
  if (sl) sl.onclick = () => {
    const led = $("#table-ledger-details");
    if (led) { led.open = true; led.scrollIntoView({ behavior: "smooth", block: "center" }); }
  };

  // --- the sort keys, each one visible and individually reversible ---
  // PLAN §3 deleted ranking. This is lexicographic and nothing else: the keys
  // are applied in turn, they are never combined into a number, and there is no
  // column here that means "how good is this gene".
  $("#table-sort").innerHTML =
    `<div class="sortbar">
       <span class="lbl">Sort</span>` +
    (d.sort.length
      ? d.sort.map((k, i) => `<span class="key">
          <b>${i + 1}.</b> ${esc(ringLabel(k.ordering))}
          <button class="dir" data-i="${i}" title="reverse this key">${
            k.reverse ? "worst first ↑" : "best first ↓"}</button>
          <button class="rm" data-i="${i}" title="remove this key">✕</button>
        </span>`).join("")
      : `<span class="hint">unsorted — the order this run drew the spokes in</span>`) +
      `<button id="table-clear" class="ghost">reset to the circle's order</button>
     </div>
     <p class="hint">${esc(d.sort_note)}
       Click a column to sort by it; <b>shift-click</b> to add it as a further key.</p>`;

  $("#table-sort").querySelectorAll("button.dir").forEach((b) => {
    b.onclick = () => {
      // Reversing flips the values only. The no-value block stays last — a gene
      // nobody measured is not the best one either.
      const i = +b.dataset.i;
      TABLE.sort[i] = { ...TABLE.sort[i], reverse: !TABLE.sort[i].reverse };
      loadTable(TABLE.run_id);
    };
  });
  $("#table-sort").querySelectorAll("button.rm").forEach((b) => {
    b.onclick = () => { TABLE.sort.splice(+b.dataset.i, 1); loadTable(TABLE.run_id); };
  });
  $("#table-clear").onclick = () => { TABLE.sort = []; loadTable(TABLE.run_id); };

  // --- the dim filter, and what it did ---
  renderDimBar(d);
  renderDimCounts(d);

  // --- the grid ---
  const primary = d.sort.length ? d.sort[0].ordering : "";
  const dirOf = (o) => {
    const k = d.sort.find((x) => x.ordering === o);
    return !k ? "" : k.reverse ? " ↑" : " ↓";
  };
  const rank = (o) => {
    const i = d.sort.findIndex((x) => x.ordering === o);
    return i < 0 ? "" : `<span class="krank">${i + 1}</span>`;
  };

  // v4.2, Puck 2026-10-19: "that gene expression expansion should be organised
  // with the gene expression data not arbitrary". Columns 1 and 2 are the
  // submitted differential expression and nothing else is, so they get a
  // header of their own spanning both, and the run's contrast facts sit
  // directly above the grid under the same words (`#table-de`).
  //
  // The span is COMPUTED from the ring keys rather than written as `colspan=2`:
  // `DE_RING_KEYS` mirrors `figure.DE_INPUT_RINGS`, and a run whose payload
  // carries one of them, or neither, gets a header that matches its own grid
  // instead of a group drawn over somebody else's column.
  const deStart = d.rings.findIndex((r) => DE_RING_KEYS.includes(r.key));
  let deAfter = deStart < 0 ? -1 : deStart;
  while (deAfter >= 0 && deAfter < d.rings.length
         && DE_RING_KEYS.includes(d.rings[deAfter].key)) deAfter++;
  const deSpan = deStart < 0 ? 0 : deAfter - deStart;
  const tail = deSpan ? d.rings.length - deStart - deSpan : 0;
  // Concatenated rather than written across a template literal: every
  // continuation line here is indented on purpose, because
  // `test_the_export_neutralises_only_the_bootstrap` reads this file for
  // top-level statements by looking for a letter in column 0.
  const groupTitle = `Columns ${deStart + 1}–${deStart + deSpan}: the `
    + "differential expression submitted to this run. The comparison, its "
    + "Atlas provenance and which way log2FC points are stated directly "
    + "above the grid.";
  // `+ 2`: the Gene and k columns, which are not rings and are in no group.
  const groupRow = !deSpan ? "" : `<tr class="colgroups">
    <th colspan="${2 + deStart}" class="nogroup"></th>
    <th colspan="${deSpan}" class="group group-de"
        title="${esc(groupTitle)}">Gene expression</th>
    ${tail > 0 ? `<th colspan="${tail}" class="nogroup"></th>` : ""}
  </tr>`;

  // `groupRow` is prepended at the `<thead>` below rather than here. Three
  // tests in `test_citations.py` read the column-head markup by slicing this
  // file between two literals, the first of which is the next line; that slice
  // is about what ONE column head contains, and the group spans two of them.
  let head = `<tr>
    <th class="sortable gene-col" data-ordering="alphabetical">Gene${
      esc(dirOf("alphabetical"))}${rank("alphabetical")}</th>
    <th title="k — how many of the ${d.k_max} external rings have a real measurement.
      A completeness gauge: it orders nothing and selects nothing (PLAN §2).">k</th>`
    // v3.7 items 2 and 4. Three lines per head and only one of them is new
    // pixels: the ring number and `column_label` (the scale column's own name for
    // this ring, not the wedge's — item 4), then the source as a LABEL at 9 px.
    //
    // The source name is deliberately NOT a link. Two reasons, both measured in
    // the usability review: it would put a click target inside a click-to-sort
    // target, and `platform-docs.opentargets.org` renders 261 px at 12 px inside
    // columns that are 86-110 px wide. The name is a label; the link is in
    // `#table-sources` under the grid, once per column, where the row number is
    // the join. It costs ~16 px of header height and 0 px per row, and it is
    // appended to the `title` as well, which costs nothing at all.
    //
    // It is drawn at 12 px, not the 9 px item 2 asked for: `UX-v3-rounds.md` B12
    // is a floor over the whole stylesheet and letting this line under it would
    // have meant editing B12's assertion. See `.grid thead th .src` in app.css.
    // v3.8. `dim-key` underscores the column a dim filter is set on, so a
    // screenshot shows the filter's target and not only its effect on the rows.
    + d.rings.map((r) => `<th class="${r.sortable ? "sortable" : "unsortable"}${
        (d.dim || []).some((f) => f.ring === r.key) ? " dim-key" : ""}"
        data-ordering="${r.sortable ? "ring:" + esc(r.key) : ""}"
        title="${esc(r.n + ". " + r.label + (r.units ? " — " + r.units : "")
          + (r.source_name ? " — source: " + r.source_name : "")
          + (r.sortable ? "" : " — not sortable: ordering by k would order by how much "
            + "anybody has studied a gene (PLAN §2)"))}">
        <span class="rn">${r.n}</span> ${esc(r.column_label)}${
          esc(dirOf("ring:" + r.key))}${rank("ring:" + r.key)}${r.source_name
          ? `<span class="src">${esc(r.source_name)}</span>` : ""}</th>`).join("")
    + `</tr>`;

  const blockStart = d.genes.findIndex((g) => g.no_value_on_sort_key);
  const body = d.genes.map((g, i) => {
    const divider = (primary && i === blockStart && blockStart >= 0)
      ? `<tr class="missing-divider"><td colspan="${d.rings.length + 2}">
           ${d.missing_block.length} gene(s) have <b>no value</b> on
           ${esc(ringLabel(primary))}. They are <b>last, not lowest</b> — this key
           cannot place them, so it does not pretend to.</td></tr>`
      : "";
    // v3.8. A dim filter is view state, so it adds a CLASS and a WORD to a row
    // that is otherwise exactly the row it was — no reorder, no removal, no
    // recomputed cell. `match` adds nothing at all, which is what makes clearing
    // the filter restore the grid rather than rebuild it.
    const dimCls = g.dim_state === "dimmed" ? " dim-out"
      : g.dim_state === "unevaluable" ? " dim-unevaluable" : "";
    // The word, because opacity and a rail are two channels and a word is the one
    // that survives a greyscale print and a phone screenshot. `cannot be
    // evaluated` is spelled out: it is the state a reader is most likely to
    // misread as "excluded", and they are different facts about the gene.
    const dimTag = !dimCls ? "" : `<span class="dimtag" title="${esc(g.dim_reason)}">${
      g.dim_state === "dimmed" ? "dimmed" : "cannot be evaluated"}</span>`;
    return divider + `<tr class="${g.no_value_on_sort_key ? "unplaced" : ""}${dimCls}"${
      g.dim_reason ? ` title="${esc(g.dim_reason)}"` : ""}>
      <th scope="row" class="gene-col">
        ${(d.in_memory || (d.baked_drawers || []).includes(g.gene))
          ? `<button class="gene linkish" data-gene="${esc(g.gene)}">${esc(g.gene)}</button>`
          // Not a disabled button: a control that looks like a control and does
          // nothing is worse than plain text. A run read off disk has no
          // adapters behind it and the note above the grid says so -- unless
          // `scripts/bake_drawers.py` left this gene's drawer beside the run,
          // in which case the label opens that file (`openDrawer`).
          : `<span class="gene">${esc(g.gene)}</span>`}
        <div class="ens">${esc(g.ensembl_id)}</div>${dimTag}</th>
      <td class="kcell">${esc(g.k)}<span class="of">/${esc(d.k_max)}</span></td>`
      + d.rings.map((r) => cellHtml(g.cells[r.key], r.key, g.gene)).join("")
      + `</tr>`;
  }).join("");

  // The run's contrast facts, directly above the grid and under the same
  // heading the grouped column header carries. Built from the payload's own
  // `contrast` block (`view.table_payload`), which is the manifest's, so the
  // block and the numbers in columns 1-2 are the same run's or neither is.
  const de = $("#table-de");
  if (de) de.innerHTML = geneExpressionNoteHtml(d.contrast, "de-facts");

  $("#table-grid").innerHTML = `<table class="grid">
    <thead>${groupRow}${head}</thead><tbody>${body}</tbody></table>`;

  // v4.2. Re-sorting and dimming are `order.py`'s and `dim.classify`'s, both
  // server-side on purpose — a second definition of either in this file is the
  // drift the app's own comments refuse. With no server the column heads stop
  // being click targets and say why in one clause, rather than offering a sort
  // that answers with a refusal.
  const sortless = noServerHere();
  if (sortless) {
    $("#table-grid").querySelectorAll("th.sortable").forEach((th) => {
      th.classList.remove("sortable");
      th.classList.add("unsortable");
      th.title = (th.title ? th.title + " — " : "")
        + "sorting needs the analysis server; this is the order the run drew.";
    });
    const clear = $("#table-clear");
    if (clear) serverOnly(clear, "This is already the order the run drew.");
  }
  $("#table-grid").querySelectorAll("th.sortable").forEach((th) => {
    th.onclick = (ev) => {
      const o = th.dataset.ordering;
      if (!o) return;
      const at = TABLE.sort.findIndex((k) => k.ordering === o);
      if (ev.shiftKey) {
        // A further key, appended. Lexicographic: it only ever breaks ties the
        // keys above it left.
        if (at < 0) TABLE.sort.push({ ordering: o, reverse: false });
        else TABLE.sort[at] = { ...TABLE.sort[at], reverse: !TABLE.sort[at].reverse };
      } else if (at === 0) {
        TABLE.sort = [{ ordering: o, reverse: !TABLE.sort[0].reverse }];
      } else {
        TABLE.sort = [{ ordering: o, reverse: false }];
      }
      loadTable(TABLE.run_id);
    };
  });
  $("#table-grid").querySelectorAll("button.gene").forEach((b) => {
    if (!b.disabled) b.onclick = () => openDrawer(b.dataset.gene);
  });

  // v4.2, Puck: "when you click on a row, it should highlight the whole row and
  // then bring up the table thing". The whole row is the click target, so a
  // reader who lands on a number gets the same drawer as a reader who lands on
  // the gene name — which is already how a cell on the circle behaves, and the
  // grid was the one surface where only the first column was live.
  //
  // The gene BUTTON keeps its own handler: it is the keyboard route into the
  // drawer and must not depend on a row click. The row handler skips a click
  // on a link or on a control, so the dim tag's tooltip, the `<a>` in a cell
  // and the gene button are not intercepted by it.
  $("#table-grid").querySelectorAll("tbody tr").forEach((tr) => {
    const label = tr.querySelector(".gene");
    if (!label) return;                        // the "no value" divider row
    const gene = label.dataset.gene || label.textContent.trim();
    tr.classList.add("rowlink");
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a, button.gene")) return;
      highlightRow(tr);
      // A gene with no drawer on this run still highlights: the row is the
      // reader's place in the grid either way, and a click that did nothing at
      // all would read as a broken table.
      if (d.in_memory || (d.baked_drawers || []).includes(gene)) openDrawer(gene);
    });
  });
  wireSummaryHighlight(d);

  renderTableSources(d);

  // --- the ledger, reachable from the table itself ---
  // `input == rendered + ledgered` is printed above; this is the other half of
  // it. A grid of 14 genes with no way to see the 15th is a plausible wrong
  // answer to "what did I submit?".
  const led = d.ledger || [];
  $("#table-ledger").innerHTML =
    `<details id="table-ledger-details">
       <summary><span class="chev" aria-hidden="true">▾</span>
         <b>Drop ledger</b> — ${led.length} row(s): every input gene that is not in
         this grid, and why</summary>
       <div class="reveal" id="table-ledger-body"></div>
     </details>`;
  const body2 = $("#table-ledger-body");
  if (!led.length) {
    body2.innerHTML = "<p class='hint'>No gene was dropped, renamed or flagged.</p>";
  } else {
    body2.innerHTML =
      `<table><thead><tr><th>Input</th><th>Status</th><th>Resolved</th>
       <th>Row</th><th>Reason</th></tr></thead><tbody>` +
      led.map((e) => `<tr>
        <td><code>${esc(e.input)}</code></td>
        <td><span class="pill bad">${esc(e.status)}</span></td>
        <td>${esc(e.resolved_symbol || "")}${e.ensembl_id
          ? ` <code>${esc(e.ensembl_id)}</code>` : ""}</td>
        <td>${esc(e.row ?? "")}</td>
        <td class="reason">${esc(e.reason)}</td></tr>`).join("") +
      `</tbody></table>`;
  }
}

// v3.8 — the per-column dim filter's control.
//
// Puck asked for "a filter, per column, that greys out genes that don't match
// instead of removing them". Three things about this control are decisions rather
// than styling, and all three are about not being mistaken for something else:
//
//   1. IT IS NOT §3.2's CONTROL. PLAN §3.2's threshold filters live on the
//      analyse form, run before the figure, REMOVE genes from the plot and name
//      every one of them in the drop ledger. This runs after the fact over a grid
//      already on screen and removes nothing. Two mechanisms that look alike and
//      do different things is its own hazard, so this one is in a different place,
//      is dropdowns rather than a `ring >= n` textarea, says "is at least" rather
//      than ">=", and states what it does not do. §3.2 is not rerouted through it.
//   2. IT IS NOT A SCORE. There is no ranking in this project (PLAN §3). The one
//      way a filter smuggles one back in is a "matches N of M filters" column, so
//      there is no such number here, nothing to sort it by, and the server does
//      not compute it either.
//   3. THE COLUMN LIST IS THE SERVER'S. `dimmable` excludes `completeness`:
//      dimming by k would de-emphasise by how much anybody has studied a gene
//      (PLAN §2, COUNCIL M3), and doing it with opacity rather than with removal
//      does not make it a different act.
function renderDimBar(d) {
  const dimmable = d.rings.filter((r) => r.dimmable);
  const active = d.dim || [];
  // The control keeps the column, comparison and number it was last used with:
  // the grid reloads on every change, and a select that snapped back to column 1
  // each time would make setting a second bound on the same column a hunt.
  const draft = TABLE.dimDraft || { ring: "", op: "ge", threshold: "" };
  if (!dimmable.length) {
    $("#table-dim").innerHTML =
      `<p class="hint">No column in this run can carry a dim filter.</p>`;
    return;
  }
  $("#table-dim").innerHTML =
    `<div class="dimbar">
       <span class="lbl">Dim rows where</span>
       ${/* v4.2. `dim_label`, not `column_label`: the bound below is compared
             against the column's own raw `values.csv` number "in the units its
             label states", and on column 2 the HEAD no longer states those
             units — it prints the adjusted p while the file and the band hold
             −log10(padj). So this entry keeps the ring's own name, the bound
             stays in −log10, and the column number is the bridge to the grid.
             `citations.bound_label` is where that choice is made; the page does
             not decide it, and the server's own count line uses the same
             string. */""}
       <select id="dim-col">${dimmable.map((r) =>
        `<option value="${esc(r.key)}"${r.key === draft.ring ? " selected" : ""}>${
          esc(r.n)}. ${esc(r.dim_label || r.column_label)}${
          r.units ? " (" + esc(r.units) + ")" : ""}</option>`).join("")}</select>
       <select id="dim-op">
         <option value="ge"${draft.op === "ge" ? " selected" : ""}>is at least</option>
         <option value="le"${draft.op === "le" ? " selected" : ""}>is at most</option>
       </select>
       <input id="dim-thr" class="thr" inputmode="decimal" placeholder="number"
              value="${esc(draft.threshold)}">
       <button id="dim-add" class="ghost">dim the rest</button>` +
    active.map((f, i) => `<span class="chip">${esc(f.phrase)}
         <button class="dimrm" data-i="${i}" title="remove this dim filter">✕</button>
       </span>`).join("") +
    (active.length
      ? `<button id="dim-clear" class="ghost">show all rows at full strength</button>`
      : "") +
    `</div>
     <p class="hint"><b>This dims; it does not filter.</b> Every gene stays on the
       grid, in the same order, with the same cells — nothing is recomputed, no run,
       figure, artefact, <code>values.csv</code> or manifest is touched, and
       <b>nothing is added to the drop ledger</b>: the ledger is for genes that are
       genuinely not on the figure. The bound is compared against the column's own
       raw value in <code>values.csv</code>, in the units its label states, never a
       rank or a colour position. A gene with <b>no value</b> on the column you dim
       on is neither a match nor an exclusion and gets its own treatment — no value
       is not a low value. To actually remove genes, use
       <b>threshold filters</b> on the analyse form, which ledger what they drop.</p>`;

  // v4.2. `dim.classify` decides a dim filter and it is server-side on purpose,
  // so with no server the control is disabled with one clause rather than left
  // to answer with a refusal.
  if (noServerHere()) {
    const why = "Dim filters are decided by the analysis server.";
    ["#dim-col", "#dim-op", "#dim-thr", "#dim-add"].forEach((s) => serverOnly(s, why));
  }

  $("#dim-add").onclick = () => {
    const thr = $("#dim-thr").value.trim();
    if (thr === "") {
      $("#table-status").className = "err";
      $("#table-status").textContent =
        "A dim filter needs a number to compare against.";
      return;
    }
    const ring = $("#dim-col").value, op = $("#dim-op").value;
    // One filter per column per comparison, the same rule §3.2 has, and for the
    // same reason: two bounds on one column and the count line cannot say what it
    // did. Re-adding a column replaces its bound rather than stacking one.
    TABLE.dimDraft = { ring, op, threshold: thr };
    const at = TABLE.dim.findIndex((f) => f.ring === ring && f.op === op);
    if (at >= 0) TABLE.dim[at] = { ring, op, threshold: thr };
    else TABLE.dim.push({ ring, op, threshold: thr });
    loadTable(TABLE.run_id);
  };
  $("#table-dim").querySelectorAll("button.dimrm").forEach((b) => {
    b.onclick = () => { TABLE.dim.splice(+b.dataset.i, 1); loadTable(TABLE.run_id); };
  });
  const clear = $("#dim-clear");
  if (clear) clear.onclick = () => { TABLE.dim = []; loadTable(TABLE.run_id); };
}

// v3.8 item 4. The count line, directly above the grid.
//
// A filtered grid that does not say it is filtered is a misleading artefact — the
// same principle as the manifest making a screenshot of the figure
// self-describing. So a screenshot of this table states the three counts, names
// the bound, and says that all of the genes are still on it.
//
// The sentence is `dim.count_line`'s, not this function's: the numbers and the
// words that describe them come from the same place, so they cannot disagree. The
// swatches beside it are built out of the same CSS the rows use.
function renderDimCounts(d) {
  const active = d.dim || [];
  if (!active.length) {
    // No filter, no line. A count line reading "14 shown · 0 dimmed" over an
    // unfiltered grid would tell a reader it had been filtered.
    $("#table-dim-counts").innerHTML = "";
    return;
  }
  const c = d.dim_counts || {};
  // The counts, then the server's sentence WITHOUT the counts in it. `title` is
  // the whole thing as one string — the form somebody pastes under a screenshot.
  $("#table-dim-counts").innerHTML =
    `<div class="tally" title="${esc(d.dim_count_line)}">
       <b>${esc(c.match ?? 0)}</b> shown ·
       <b>${esc(c.dimmed ?? 0)}</b> dimmed ·
       <b>${esc(c.unevaluable ?? 0)}</b> cannot be evaluated</div>
     <p>${esc(d.dim_count_note)}</p>
     <div class="three">
       <span class="swat is-match"><i><em></em></i> matches the bound</span>
       <span class="swat is-dimmed"><i><em></em></i> has a value and misses it —
         faded, still here</span>
       <span class="swat is-unevaluable"><i><em></em></i> no value on this column,
         so the bound cannot be evaluated — full strength</span>
     </div>
     <p class="note">Dimming is a view state: the
       <b>${esc((d.counts || {}).rendered)}</b> genes rendered and the
       <b>${esc((d.counts || {}).dropped)}</b> in the drop ledger are the same
       numbers as with no dim filter set. A dimmed gene is a rendered gene.</p>`;
}

// v3.7 item 1. "Where these N columns come from" — the citation strip under the
// grid.
//
// One row per column, numbered to match the column numbers, each reading
// `n. <full ring label> — <units> — <name · domain>` with `name · domain` as the
// anchor text. Never a bare underline: `SourceDoc.domain` exists because an
// underline dies in an exported PNG, and this strip is screenshotted.
//
// Everything drawn here is authored by `citations.py` and arrives on the
// response. The page does not decide which URL a column gets, whether a column
// has one, or what to say when it does not — a second opinion about provenance is
// the thing this whole item exists to remove. In particular the FAILED rows point
// at `failed_target`, which `citations.cell_provenance` returns for exactly that
// state and which is deliberately not the source link (item 3).
function renderTableSources(d) {
  const rows = d.citations || [];
  const withFailures = rows.filter((r) => r.failed_target);
  // Open, not closed. The strip IS the fix for "all ~250 historical runs are
  // viewable with no citation anywhere in the session", and a citation behind a
  // triangle is most of that finding again. It is a `<details>` so a reader who
  // has read it once can put it away, which is the other half of what was asked.
  $("#table-sources").innerHTML =
    `<details id="table-sources-details" class="sources" open>
       <summary><span class="chev" aria-hidden="true">▾</span>
         <b>Where these ${rows.length} columns come from</b> —
         ${rows.filter((r) => r.linked).length} with a source link,
         ${rows.filter((r) => !r.linked).length} without and why${withFailures.length
           ? `, ${withFailures.length} with a FAILED cell` : ""}</summary>
       <div class="reveal">
         <p class="hint">${esc(d.citation_note || "")}</p>
         <ol class="srclist">` +
    // `n. <full ring label> — <units> — <name · domain>`, and nothing else on the
    // line. A linked row's `reason` — why THAT page and not the source's
    // homepage — rides on the anchor's title instead of under it: twelve extra
    // sentences put the strip at 548 px against a 240 px budget, and the one
    // reader who wants them is hovering the link anyway. An UNLINKED row keeps
    // its reason on screen, because there nothing else says why the row is short.
    rows.map((r) => `<li value="${r.n}">
        <span class="lbl">${esc(r.label)}</span>
        ${/* A retired ring has no units in this build, and the separator these
              spans carry would print them as "— —". Omitted, not dashed. */
          r.units ? `<span class="un">${esc(r.units)}</span>` : ""}
        ${r.linked
          ? `<a href="${esc(r.url)}" target="_blank" rel="noopener"
               title="${esc(r.reason)}">${esc(r.anchor_text)}</a>`
          : `<span class="nolink">no source link — ${esc(r.reason)}</span>`}
        ${r.recorded_drift ? `<span class="why warnish">${esc(r.recorded_drift)}</span>` : ""}
        ${r.failed_target
          ? `<span class="failnote"><b>${r.failed_genes.length} FAILED cell(s)</b>
              on this column — ${esc(r.failed_genes.join(", "))} —
              <b>not answered by the link above.</b> ${esc(r.failed_target.why)}
              <a href="${esc(r.failed_target.url)}" target="_blank" rel="noopener">${
                esc(r.failed_target.text)}</a></span>`
          : ""}
      </li>`).join("") +
        `</ol>
       </div>
     </details>`;
}

// One cell. The four states are four different things on screen and the three
// that are not measurements carry a word as well as a pattern, so the table is
// readable in greyscale, in a screenshot, and by a reader who cannot tell the
// hatches apart. A `no_data` cell is never blank and never 0.
// v4.2. `ringKey` and `gene` are passed so the hover is the SAME sentence the
// circle's cells show — ring name, gene, the value with its unit or the state
// word, what the ring measures and its source, then the cell's own record.
// `title` is enough on a `<td>`; the SVG needs the positioned box because it
// has no `title` a browser renders.
function cellHtml(c, ringKey, gene) {
  if (!c) {
    // `table.load` refuses a non-rectangular pivot, so this is unreachable —
    // and if it ever is reached it says so rather than drawing an empty box.
    return `<td class="st-missing">NO CELL</td>`;
  }
  const tip = ringKey && gene ? cellTooltip(ringKey, gene) : (c.detail || "");
  if (c.state === "value") {
    // v4.2, Puck 2026-10-19: "format the p value in the table". Column 2 prints
    // the adjusted p through the same `fmtP` the drawer and the GO box use —
    // `<0.05` under the rule's own cutoff, two significant digits above it. The
    // FILL is untouched: it is `c.fill`, computed by the renderer on the ring's
    // −log10 scale, so the grid and the band are still the same colour for the
    // same gene and only the printed number differs. `cellPadj` reads the p out
    // of the cell's own detail, so nothing here recomputes a measurement.
    const shown = ringKey === PADJ_RING ? fmtP(cellPadj(c)) : num(c.value);
    return `<td class="st-value" style="background:${esc(c.fill)};color:${
      inkOn(c.fill)}" title="${esc(tip)}">${esc(shown)}</td>`;
  }
  // v3.7 item 3. A `failed` cell's hover says where to look, and where not to.
  // The column's source link answers `no_data` — "is this gene really absent
  // upstream?" is checked at the source — and must not be read as answering this:
  // the call did not come back, so a working doc page would leave a reader
  // concluding the gene has no value. The cell's own `detail` is the record, and
  // the citation strip's FAILED row names the gene and links manifest.json.
  const lead = c.state === "failed"
    ? "call FAILED — not absence, and NOT answered by this column's source link. "
      + "The record is this run's own: "
    : "";
  return `<td class="st-${esc(c.state)}" title="${esc(lead + c.detail)}">${
    esc(STATE_WORD[c.state] || c.state)}</td>`;
}

function triageHtml(t) {
  if (t.error) return `<div class="note warn">Triage failed: ${esc(t.error)}</div>`;
  return `<div class="note warn">
      <b>${esc(t.verdict)}</b> — abstract asserts target manipulation altered the phenotype
      (unverified). Never folded into the score.</div>
    <p>“${esc(t.sentence)}”</p>
    <p class="hint">PMID
      <a href="https://pubmed.ncbi.nlm.nih.gov/${esc(t.pmid)}/" target="_blank"
         rel="noopener">${esc(t.pmid)}</a>
      · model <code>${esc(t.model)}</code> · prompt <code>${esc(t.prompt_hash)}</code>
      · quote verified in abstract: ${t.quote_verified ? "yes" : "NO — discarded"}</p>`;
}

// v4.0. The app boots itself; the offline export does not.
//
// `init()` fetches `/api/regions` and `/api/orderings` and wires the analyse form.
// The export inlines THIS FILE VERBATIM so that its grid is drawn by the app's own
// `renderTable` rather than by a copy of it -- that is the whole point of the
// export being trustworthy -- and it must make zero network requests when Puck
// opens it from `file://` on a machine with no server. So the one app-only
// statement in this file is the one that is guarded, and the export sets the flag
// before the script runs. Nothing else here has a top-level side effect;
// `test_export.py::test_the_export_neutralises_only_the_bootstrap` holds that.
if (!(typeof window !== "undefined" && window.TARGETRING_OFFLINE_EXPORT)) init();
