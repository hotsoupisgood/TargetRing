"use strict";

let RUN = null;

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

// ---------- setup ----------

async function init() {
  const r = await fetch("/api/regions").then((x) => x.json());
  const sel = $("#region");
  sel.innerHTML = r.regions.map((t) =>
    `<option ${t === r.default ? "selected" : ""}>${esc(t)}</option>`).join("");
  if (r.error) $("#status").textContent = "GTEx region list unavailable: " + r.error;

  // v2.5. The spoke orders and the rings a threshold can be set on both come
  // from the build, so a control can never offer a ring the figure does not have.
  const o = await fetch("/api/orderings").then((x) => x.json());
  $("#ordering").innerHTML = o.orderings.map((x) =>
    `<option value="${esc(x.value)}" ${x.value === o.default ? "selected" : ""}>
      ${esc(x.label)}</option>`).join("");
  $("#filterable").innerHTML = "Filterable rings: " +
    o.filterable_rings.map((f) => `<code>${esc(f.ring)}</code>`).join(", ");

  $("#load-example").onclick = async () => {
    $("#table").value = (await fetch("/api/example").then((x) => x.json())).realistic;
  };
  $("#load-broken").onclick = async () => {
    $("#table").value = (await fetch("/api/example").then((x) => x.json())).broken;
  };
  $("#go").onclick = analyse;
  $("#drawer-close").onclick = closeDrawer;
  $("#scrim").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => showTab(t.dataset.tab);
  });

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
  const wanted = new URLSearchParams(location.search).get("run");
  if (wanted) {
    $("#result").hidden = false;
    $("#banner").hidden = true;
    $("#counts").innerHTML = "";
    $("#figure").innerHTML =
      `<p class="hint">No figure is drawn in this session. You are looking at run
       <code>${esc(wanted)}</code> as a table, read from its artefacts on disk.
       Its rendered figure is
       <a href="/api/run/${esc(wanted)}/figure.svg" target="_blank" rel="noopener">
       figure.svg</a>.</p>`;
    showTab("table");
    picker.value = wanted;
    loadTable(wanted);
  }

  // v3.5. The figure is sized for the window it is read in, so the window
  // changing size is an input. Debounced, and a no-op when the change is smaller
  // than the canvas quantum or when there is no run in memory to redraw.
  window.addEventListener("resize", () => {
    clearTimeout(RESIZE_TIMER);
    RESIZE_TIMER = setTimeout(redrawForThisWindow, RESIZE_SETTLE_MS);
  });
}

function showTab(name) {
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
}

// ---------- run ----------

let TICKER = null;

async function analyse() {
  const btn = $("#go");
  btn.disabled = true;
  $("#status").className = "";

  // UX A2. v1 set one status string and left #result on screen, so for the
  // whole of a cold run the page showed a complete, plausible, finished-looking
  // figure of the PREVIOUS gene set, with working Download buttons. At ~4 s per
  // gene that window is four minutes. Clearing it removes the whole risk.
  $("#result").hidden = true;
  RUN = null;

  // UX B2. One static grey sentence for four minutes reads as "it has hung".
  const t0 = Date.now();
  const stage = "Checking source contracts, resolving identifiers, fetching rings…";
  const tick = () => {
    const s = Math.round((Date.now() - t0) / 1000);
    $("#status").textContent = `${stage}  ·  ${s}s elapsed`;
  };
  tick();
  clearInterval(TICKER);
  TICKER = setInterval(tick, 1000);

  const fd = new FormData();
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
    fail("Request failed: " + e);
    btn.disabled = false;
    return;
  }
  clearInterval(TICKER);
  btn.disabled = false;

  if (!res.ok) {
    fail((body.message || "failed") + (body.note ? "\n\n" + body.note : ""));
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
  if (sl) sl.onclick = () => showTab("ledger");

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
    $("#status").textContent = (body.message || "the figure could not be redrawn")
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

function wireFigure() {
  // Cells carry id="cell|<ring>|<index>|<gene>" — the same handle the invariant
  // checks use, so what is clickable is exactly what was audited.
  $("#figure").querySelectorAll('[id^="cell|"]').forEach((el) => {
    const gene = el.id.split("|")[3];
    el.style.cursor = "pointer";
    el.addEventListener("click", () => openDrawer(gene));
  });
  $("#figure").querySelectorAll("text").forEach((t) => {
    const m = (t.textContent || "").match(/^[⚠\s]*([A-Za-z0-9._-]+)\s*\(k=/);
    if (!m) return;
    if (!RUN.gene_order.includes(m[1])) return;
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
  $("#caveats").innerHTML =
    `<div class="note warn"><b>This figure is internally consistent and auditable.
     Nothing on it is ranked or scored.</b> It is your gene set, the evidence rings,
     and a drawer per gene.</div>
     <h4>Not attempted — and deliberately so</h4>
     <ul class="tight">${b.not_attempted.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
     <h4>Spoke order</h4>
     <p>${esc(b.ordering_note || "")}</p>
     <p class="hint">The order carries meaning, so it is printed on the figure too.
     The default is your own table's order — it is your measurement, not a composite
     this app invented.</p>
     ${llmAccountsHtml(b.llm)}`;
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
  return `<h4>Paid calls — two caps, two counters</h4>
    <table><thead><tr><th>account</th><th>spent</th><th>calls</th>
      <th>what it pays for</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">Neither account borrows from the other and both are separate
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

async function openDrawer(gene) {
  const d = await fetch(`/api/run/${RUN.run_id}/gene/${encodeURIComponent(gene)}`)
    .then((r) => r.json());
  $("#drawer-body").innerHTML = sectionise(drawerHtml(d));
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer").scrollTop = 0;
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
}

function drawerHtml(d) {
  const drugs = d.drugs || [];
  const lit = d.literature || {};
  const ch = d.chembl || {};
  // v2.2 item 3: ChEMBL and Europe PMC are fetched when this drawer opens, not
  // on the render path. `null` means not fetched yet, never "nothing there".

  let h = `<h3>${esc(d.gene)}</h3>
    <p class="hint">${d.ensembl_id ? "Ensembl " + esc(d.ensembl_id) : ""}${d.uniprot
      ? " · UniProt " + esc(d.uniprot) : ""}${d.gencode_id
      ? " · GTEx " + esc(d.gencode_id) : ""}</p>`;

  if (d.input_symbol !== d.gene) {
    h += `<div class="note warn">Submitted as <code>${esc(d.input_symbol)}</code> —
          ${esc(d.resolution_note)}</div>`;
  }

  h += `<h4>your measurement</h4><dl class="kv">
    <dt>logFC</dt><dd>${d.logfc}</dd>
    <dt>adjusted p</dt><dd>${d.padj}</dd>
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
       <ul class="tight">${mp.slice(0, 12).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
       ${mp.length > 12 ? `<p class="hint">…and ${mp.length - 12} more</p>` : ""}`
    : `<p class="hint">No mouse phenotype records — not phenotyped, which is not the
       same as “no phenotype”. Embryonic-lethal lines drop out of adult phenotyping,
       which biases exactly the essential genes the safety ring flags as risky
       (COUNCIL M5).</p>`;

  if (d.triage) h += triageHtml(d.triage);
  else h += `<div class="note">LLM triage of the rodent lead was not run for this gene
             — see the run manifest.</div>`;

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
      <td class="reason">${esc(c.detail)}</td></tr>`).join("") +
    `</tbody></table>`;
  return h;
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
function inkOn(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return "var(--ink)";
  const n = parseInt(m[1], 16);
  const l = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return l > 0.6 ? "#16181d" : "#ffffff";
}

// Four significant figures, trailing zeros dropped: `2.81`, `5`, `0.02312`. The
// drawer prints `toPrecision(4)` and this is the same number without the `5.000`.
const num = (v) => String(Number((+v).toPrecision(4)));

// Never blank, never zero (AGENTS.md rule 1). Every non-value state gets a word.
const STATE_WORD = { no_data: "no data", not_applicable: "n/a", failed: "FAILED" };

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
    $("#table-status").textContent = d.message || "the table could not be loaded";
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
  }
}

function renderTable(d) {
  const ring = (k) => d.rings.find((r) => r.key === k);
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
        ${d.in_memory
          ? `<button class="gene linkish" data-gene="${esc(g.gene)}">${esc(g.gene)}</button>`
          // Not a disabled button: a control that looks like a control and does
          // nothing is worse than plain text. A run read off disk has no
          // adapters behind it and the note above the grid says so.
          : `<span class="gene">${esc(g.gene)}</span>`}
        <div class="ens">${esc(g.ensembl_id)}</div>${dimTag}</th>
      <td class="kcell">${esc(g.k)}<span class="of">/${esc(d.k_max)}</span></td>`
      + d.rings.map((r) => cellHtml(g.cells[r.key])).join("")
      + `</tr>`;
  }).join("");

  $("#table-grid").innerHTML = `<table class="grid">
    <thead>${head}</thead><tbody>${body}</tbody></table>`;

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
       <select id="dim-col">${dimmable.map((r) =>
        `<option value="${esc(r.key)}"${r.key === draft.ring ? " selected" : ""}>${
          esc(r.n)}. ${esc(r.column_label)}${
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
function cellHtml(c) {
  if (!c) {
    // `table.load` refuses a non-rectangular pivot, so this is unreachable —
    // and if it ever is reached it says so rather than drawing an empty box.
    return `<td class="st-missing">NO CELL</td>`;
  }
  if (c.state === "value") {
    return `<td class="st-value" style="background:${esc(c.fill)};color:${
      inkOn(c.fill)}" title="${esc(c.detail)}">${esc(num(c.value))}</td>`;
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
