"use strict";

// The offline export's driver. v4.0.
//
// Puck asked to read a run's table on another computer, on another network.
// Nothing here may be published (AGENTS.md rule 10), so the answer is a file he
// carries: one `.html`, opened from `file://`, zero network requests.
//
// WHAT THIS FILE IS NOT is the more useful description. It is **not a renderer**.
// The grid, the column heads, the four cell states, the sort chips, the dim
// control, the count line, the citation strip and the drop ledger are all drawn
// by `app.js`, inlined into the export verbatim, calling the same `renderTable`
// the app calls. Two renderers of one dataset is precisely how the app and the
// export come to disagree about a number, and a plausible wrong output is the
// worst possible output (`AGENTS.md:17`). So there is one renderer, and this file
// replaces exactly one function of the app: `loadTable`, which in the app is an
// HTTP fetch and here is a local recomputation.
//
// What it therefore DOES have to do is the part a served page asks Python for,
// because there is no Python behind a `file://` document:
//
//   * the ROW ORDER for a sort the reader just clicked, and
//   * the DIM CLASSIFICATION for a threshold the reader just typed.
//
// Neither decision is made here. Both are made by this build, in Python, and
// carried in `EXPORT`:
//
//   * `EXPORT.grades[key]` is `order.sort_key_and_reverse`'s own answer, dense-ranked
//     with ties intact, one integer per gene per key. So which way `tractable`
//     runs, and that a `no_data` cell is LAST rather than LOWEST, are Python's
//     answers. All that happens below is a stable sort on those integers, applied
//     right-to-left, which is what `order.order_by_keys` does and is equal to
//     sorting on the tuple of the keys.
//   * `EXPORT.templates` are `dim.py`'s own sentences. The comparison and the
//     three-way precedence are transliterated from `dim.classify` because a
//     threshold is a number Puck types and cannot be precomputed. That is the one
//     piece of logic that exists twice in this build, it is named in the report,
//     and it is not left on trust: `EXPORT.probes` carries Python's answer for
//     every column, both comparisons, several thresholds and some multi-key
//     sorts, `selfCheck()` runs all of them in the reader's browser before the
//     grid is drawn, and a disagreement puts a DO-NOT-TRUST banner at the top of
//     the file instead of a quietly wrong table.

const T = EXPORT.templates;

// ---------------------------------------------------------------- number words
// Python's `format(v, "g")`: 6 significant digits, trailing zeros dropped, and
// exponent notation outside [1e-4, 1e6). Every number the dim filter puts into a
// sentence goes through this, on both sides, and the probes hold the two to
// equality -- a threshold that reads `2.5` in the app and `2.50000` here would be
// the same claim in two forms, which is the defect one level down from a wrong one.
function gfmt(v) {
  const n = Number(v);
  if (!isFinite(n)) return String(n);
  if (n === 0) return "0";
  const e = Math.floor(Math.log10(Math.abs(n)));
  if (e < -4 || e >= 6) {
    const parts = n.toExponential(5).split("e");
    const mant = parts[0].replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
    const sign = parts[1][0] === "-" ? "-" : "+";
    const digits = parts[1].replace(/^[+-]/, "");
    return mant + "e" + sign + (digits.length < 2 ? "0" + digits : digits);
  }
  const s = n.toPrecision(6);
  return s.indexOf(".") < 0 ? s
    : s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

// `str.format`, for the templates `dim.py` exports. The words are Python's; only
// the blanks are filled here.
function fmt(t, vals) {
  return String(t).replace(/\{(\w+)\}/g, (_, k) => String(vals[k]));
}

const uncap = (s) => s ? s.charAt(0).toLowerCase() + s.slice(1) : s;

// -------------------------------------------------------------------- the sort
const gradeId = (k) => k.ordering + "|" + (k.reverse ? "asc" : "desc");

// `order.order_by_keys`, on the grades Python computed: applied right-to-left over
// a stable sort, which is the same answer as sorting on the tuple of the keys and
// keeps each key's own missing rule intact at its own level. The index tie-break
// makes the stability explicit rather than relying on the engine's.
function offlineOrder(keys) {
  if (!keys.length) return EXPORT.payload.order.slice();
  let genes = EXPORT.submitted_order.slice();
  for (let i = keys.length - 1; i >= 0; i--) {
    const grade = EXPORT.grades[gradeId(keys[i])];
    if (!grade) throw new Error("this export carries no grades for " + gradeId(keys[i]));
    genes = genes.map((g, j) => [g, j])
      .sort((a, b) => (grade[a[0]] - grade[b[0]]) || (a[1] - b[1]))
      .map((x) => x[0]);
  }
  return genes;
}

// `order.sort_label`, from its four pieces and one phrase per key -- all of them
// Python's strings, so the row order is stated here in the words the app uses.
function sortNote(keys) {
  if (!keys.length) return T.SORT_LABEL_UNSORTED;
  return T.SORT_LABEL_PREFIX
    + keys.map((k) => EXPORT.sort_phrase[gradeId(k)]).join(T.SORT_LABEL_JOIN)
    + T.SORT_LABEL_TAIL;
}

// `order.missing_on`: a filter over the order given, not a computation. Which
// genes a key cannot place is Python's answer, per key, in `EXPORT.unplaceable`.
function missingOn(order, primary) {
  const un = EXPORT.unplaceable[primary];
  if (!un) return [];
  const set = new Set(un);
  return order.filter((g) => set.has(g));
}

// --------------------------------------------------------------- the dim filter
// `dim.classify`, transliterated. The one duplicated decision in the build; see
// the header, and `EXPORT.probes.dim` for what holds it to Python's answer.
const opMatches = (f, v) => f.op === "ge" ? v >= f.threshold : v <= f.threshold;

const dimPhrase = (f, label) => fmt(T.PHRASE_T, {
  label: label || f.ring, op_phrase: T.OP_PHRASE[f.op],
  threshold: gfmt(f.threshold),
});

function classifyDim(genes, cellOf, dims) {
  const state = {}, reason = {};
  if (!dims.length) {
    genes.forEach((g) => { state[g] = "match"; });
    return { state: state, reason: reason };
  }
  for (const g of genes) {
    const missed = [], skipped = [];
    for (const f of dims) {
      const c = cellOf(g, f.ring);
      if (!c || c.state !== "value" || c.value === null || c.value === undefined) {
        skipped.push(fmt(T.SKIPPED_T, {
          ring: f.ring, state: c ? c.state : T.ABSENT_STATE,
          phrase: dimPhrase(f, null),
        }));
        continue;
      }
      if (!opMatches(f, Number(c.value))) {
        missed.push(fmt(T.MISSED_T, {
          ring: f.ring, value: gfmt(Number(c.value)),
          op_phrase: T.OP_PHRASE[f.op], threshold: gfmt(f.threshold),
        }));
      }
    }
    // A real measurement that misses a bound is the stronger fact, so a gene that
    // fails any evaluable filter is dimmed even if another could not be evaluated
    // for it. A gene that fails none and cannot be evaluated on at least one is
    // `unevaluable` -- never dimmed, because no value is not a low value.
    if (missed.length) {
      state[g] = "dimmed";
      reason[g] = missed.join(T.REASON_JOIN);
    } else if (skipped.length) {
      state[g] = "unevaluable";
      reason[g] = skipped.join(T.REASON_JOIN) + T.SKIPPED_TAIL;
    } else {
      state[g] = "match";
    }
  }
  return { state: state, reason: reason };
}

function dimCounts(out, genes) {
  const c = { match: 0, dimmed: 0, unevaluable: 0 };
  genes.forEach((g) => { c[out.state[g]] += 1; });
  return c;
}

function countNote(counts, dims, labels) {
  if (!dims.length) return "";
  const total = counts.match + counts.dimmed + counts.unevaluable;
  const named = dims.map((f) => dimPhrase(f, labels[f.ring])).join(T.NOTE_JOIN);
  return fmt(T.NOTE_T, { named: named, total: total });
}

function countLine(counts, dims, labels) {
  if (!dims.length) return "";
  return fmt(T.COUNT_LINE_T, {
    match: counts.match, dimmed: counts.dimmed, unevaluable: counts.unevaluable,
    note: uncap(countNote(counts, dims, labels)),
  });
}

// ------------------------------------------------------- the payload, offline
// Exactly the dict `view.table_payload` returns, for the sort and the dim state
// the reader has set. Everything that is a property of the RUN -- the cells, the
// colours, the details, k, the citations, the ledger, the invariants, the counts --
// is passed through untouched from the frozen base payload. Only the view state is
// recomputed, which is the only thing that can have changed since the export ran.
function offlinePayload() {
  const base = EXPORT.payload;
  const keys = TABLE.sort.slice();
  const dims = TABLE.dim.map((f) => ({
    ring: f.ring, op: f.op, threshold: Number(f.threshold),
  }));
  const order = offlineOrder(keys);
  const primary = keys.length ? keys[0].ordering : "";
  const block = primary ? missingOn(order, primary) : [];
  const blockSet = new Set(block);

  const byGene = {};
  base.genes.forEach((g) => { byGene[g.gene] = g; });
  const cellOf = (g, ring) => (byGene[g] && byGene[g].cells || {})[ring];

  const out = classifyDim(order, cellOf, dims);
  const counts = dimCounts(out, order);
  const labels = {};
  base.rings.forEach((r) => { labels[r.key] = r.column_label; });

  return Object.assign({}, base, {
    genes: order.map((g) => Object.assign({}, byGene[g], {
      no_value_on_sort_key: blockSet.has(g),
      dim_state: out.state[g],
      dim_reason: out.reason[g] || "",
    })),
    order: order,
    sort: keys.map((k) => ({ ordering: k.ordering, reverse: !!k.reverse })),
    sort_note: sortNote(keys),
    missing_block: block,
    dim: dims.map((f) => ({
      ring: f.ring, op: f.op, symbol: T.OP_SYMBOL[f.op], threshold: f.threshold,
      phrase: dimPhrase(f, null),
      expression: f.ring + " " + T.OP_SYMBOL[f.op] + " " + gfmt(f.threshold),
    })),
    dim_counts: counts,
    dim_count_line: countLine(counts, dims, labels),
    dim_count_note: countNote(counts, dims, labels),
  });
}

// The app's `loadTable` is an HTTP fetch. This is the same contract with the
// server taken out: set `TABLE`, hand `renderTable` a payload. Replacing this one
// function is the whole of the export's JavaScript surface on the render path.
loadTable = function offlineLoadTable() {
  const bad = TABLE.dim.filter((f) => !isFinite(Number(f.threshold)));
  if (bad.length) {
    // Refused, not approximated -- the same rule as the served page's, whose
    // `dim_rejected` drops the filters rather than printing a bound the grid did
    // not use. The message is `dim.parse_dims`' own.
    const st = $("#table-status");
    st.className = "err";
    st.textContent = fmt(T.NOT_A_NUMBER_T, {
      ring: bad[0].ring || "?", raw: "'" + String(bad[0].threshold) + "'",
    });
    TABLE.dim = [];
  }
  const d = offlinePayload();
  TABLE.run_id = EXPORT.payload.run_id;
  TABLE.data = d;
  if (!bad.length) {
    const st = $("#table-status");
    st.className = "";
    st.textContent = "";
  }
  renderTable(d);
  return Promise.resolve(d);
};

// ------------------------------------------------------------------ self-check
// The export asserts its own agreement with Python, on the reader's machine,
// before it draws anything. `EXPORT.probes` is what this build computed at export
// time: row orders and sentences for a set of sort states, and per-gene states,
// reasons, counts and count lines for a set of dim filters over every dimmable
// column in both directions. A file that cannot reproduce them says so at the top
// rather than showing a grid that looks right.
function selfCheck() {
  const fails = [];
  // Compared by value, never by `JSON.stringify`. A per-gene map is a set of
  // facts about genes, not an ordered thing: the embedded copy is written with
  // its keys sorted and this file builds its own in row order, so stringifying
  // both reports a disagreement between two identical answers. Row ORDER is a
  // list and is compared as one -- there the sequence is the whole claim.
  const sameList = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  const sameMap = (a, b) => {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length
      && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && a[k] === b[k]);
  };

  EXPORT.probes.sort.forEach((p, i) => {
    const order = offlineOrder(p.keys);
    if (!sameList(order, p.order)) {
      fails.push("sort probe " + i + " (" + p.name + "): row order is [" +
        order.join(", ") + "], Python's is [" + p.order.join(", ") + "]");
    }
    if (sortNote(p.keys) !== p.sort_note) {
      fails.push("sort probe " + i + " (" + p.name + "): the row-order sentence " +
        "differs from Python's");
    }
    const primary = p.keys.length ? p.keys[0].ordering : "";
    if (!sameList(primary ? missingOn(order, primary) : [], p.missing_block)) {
      fails.push("sort probe " + i + " (" + p.name + "): the no-value block differs");
    }
  });

  const base = EXPORT.payload;
  const byGene = {};
  base.genes.forEach((g) => { byGene[g.gene] = g; });
  const cellOf = (g, ring) => (byGene[g] && byGene[g].cells || {})[ring];
  const labels = {};
  base.rings.forEach((r) => { labels[r.key] = r.column_label; });

  EXPORT.probes.dim.forEach((p, i) => {
    const genes = base.order;
    const out = classifyDim(genes, cellOf, p.dims);
    const counts = dimCounts(out, genes);
    if (!sameMap(out.state, p.state)) {
      fails.push("dim probe " + i + " (" + p.name + "): the three-state " +
        "classification differs from Python's");
    }
    if (!sameMap(out.reason, p.reason)) {
      fails.push("dim probe " + i + " (" + p.name + "): a per-gene reason differs " +
        "from Python's");
    }
    if (!sameMap(counts, p.counts)) {
      fails.push("dim probe " + i + " (" + p.name + "): the counts are " +
        JSON.stringify(counts) + ", Python's are " + JSON.stringify(p.counts));
    }
    if (countLine(counts, p.dims, labels) !== p.count_line) {
      fails.push("dim probe " + i + " (" + p.name + "): the count line differs " +
        "from Python's");
    }
  });
  return fails;
}

function renderSelfCheck(fails) {
  const n = EXPORT.probes.sort.length + EXPORT.probes.dim.length;
  if (!fails.length) {
    $("#export-selfcheck").innerHTML =
      `<p><span class="pill ok">PASS</span> This file reproduced all
       <b>${n}</b> answers this build computed for it before drawing the grid:
       <b>${EXPORT.probes.sort.length}</b> row orders with their stated
       sentences and no-value blocks, and <b>${EXPORT.probes.dim.length}</b> dim
       classifications with their per-gene reasons, counts and count lines. The
       cells themselves are not checked here because they are not recomputed —
       they are this run's own <code>values.csv</code> rows, carried across.</p>`;
    return;
  }
  $("#export-selfcheck").innerHTML =
    `<div class="banner-bad">DO NOT TRUST THIS EXPORT — ${fails.length}
       disagreement(s) with the build that wrote it, across ${n} probes. Open the
       run in the app instead.</div>` +
    `<ul class="tight">${fails.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
}

// The citation strip's FAILED rows point at the manifest inlined into this file
// rather than at `/api/run/<id>/manifest.json`, which nothing here serves. A
// `<details>` that a fragment navigates into is only auto-expanded by some
// browsers, so the link opens it. Delegated, because `renderTableSources` redraws
// the strip on every sort and every dim change.
function openTheInlinedManifest() {
  const d = document.getElementById("export-manifest");
  if (d) d.open = true;
}
document.addEventListener("click", (ev) => {
  const a = ev.target && ev.target.closest
    && ev.target.closest('a[href^="#export-manifest"]');
  if (a) openTheInlinedManifest();
});
// The strip's anchors carry `target="_blank"`, so the link may instead arrive as a
// fresh load of this same file at that fragment. Whether a browser auto-expands a
// `<details>` it navigates into is a browser-version question, and "the record is
// here" has to be true on Puck's machine and not only on this one.
if (location.hash.indexOf("#export-manifest") === 0) openTheInlinedManifest();

renderSelfCheck(selfCheck());
// The invariant table is `app.js`'s `renderInvariants`, on this run's own manifest
// rows. A detached artefact that cannot be audited is not a deliverable, which is
// the same reason the manifest exists beside every figure.
renderInvariants(EXPORT.payload);
loadTable(EXPORT.payload.run_id);
