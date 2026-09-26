"use strict";

let RUN = null;

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
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((x) =>
    x.classList.toggle("active", x.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => { p.hidden = true; });
  $("#pane-" + name).hidden = false;
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

  // UX B3: "N not shown" looked like a link and did nothing. It is the one
  // place the user asks "why?".
  const sl = $("#show-ledger");
  if (sl) sl.onclick = () => showTab("ledger");

  // UX B4: after a run the figure was below the fold and the scroll did not move.
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });

  // v3.0. The page inlines the WEB render, not the export. Same cells --
  // `assert_renders_agree` fails the run if a single one differs -- but no
  // footer block and no dropped-gene list in the hole, because this page
  // already carries both in the `What this is not` and `Drop ledger` tabs. The
  // circle gets the space instead. Falls back to the export if an older run is
  // still in memory without a web render.
  fetch(b.web_url || b.svg_url).then((r) => r.text()).then((svg) => {
    $("#figure").innerHTML = svg;
    wireFigure();
  });
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
     this app invented.</p>`;
}

// ---------- drawer ----------

async function openDrawer(gene) {
  const d = await fetch(`/api/run/${RUN.run_id}/gene/${encodeURIComponent(gene)}`)
    .then((r) => r.json());
  $("#drawer-body").innerHTML = drawerHtml(d);
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

init();
