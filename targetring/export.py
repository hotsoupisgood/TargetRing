"""One run's table view as a single self-contained `.html` file. v4.0.

Puck wants to read a run's table on a different computer, on a different network.
Hosting it is forbidden (`AGENTS.md` rule 10), so the answer is an export he
carries: one file, opened from `file://`, **zero network requests and zero
external assets**. That is not a style preference — a browser reading a `file://`
document blocks `fetch()` of its own siblings, so any external reference produces
an empty grid on his machine with nothing on screen to explain it.

**The export is not a second renderer.** `app.js` is inlined verbatim and the grid
is drawn by the same `renderTable` the app calls, over a payload built by the same
`view.table_payload` the app's endpoint returns. Two renderers of one dataset is
how the app and the file he carries come to disagree about a number, and a
plausible wrong output is the worst possible output (`AGENTS.md:17`). The one
function the export replaces is `loadTable`, which in the app is an HTTP fetch and
here is a local recomputation — `static/export_boot.js`.

**What cannot be shared, and what is done about it.** A `file://` document has no
Python. Two decisions therefore have to be *evaluated* in the reader's browser:

  * The ROW ORDER. Not decided there: `sort_grades` asks
    `order.sort_key_and_reverse` — the function `order.order_by_keys` itself uses —
    for every gene's position on every key this export offers, and freezes it as a
    dense rank with ties intact. The browser does a stable sort on those integers,
    right-to-left, which is `order_by_keys`' own algorithm. `order_from_grades`
    below is the Python twin of that loop and `assert_grades_reproduce_the_sort`
    holds it to `order_by_keys` over every key sequence the export ships, at
    export time, for this run's real cells.

  * The DIM CLASSIFICATION. A threshold is a number Puck types, so it cannot be
    precomputed, and `dim.classify`'s comparison and three-way precedence are
    transliterated into `export_boot.js`. **This is the one piece of logic that
    exists twice in this build.** Its wording is not duplicated — `dim.py` exports
    every sentence as a template and both sides fill the same blanks — and its
    behaviour is not left on trust: `dim_probes` computes Python's answer for every
    dimmable column, both comparisons, five thresholds each and some combinations,
    the export checks itself against all of them in the reader's browser before it
    draws anything, and a disagreement replaces the grid's heading with a
    DO-NOT-TRUST banner. An export that is wrong says so; it does not look right.

**What it does not pretend to have.** The per-gene drawer. Its drug pipeline and
its literature are live ChEMBL and Europe PMC calls (`app.fill_drawer_only_sources`),
so there is nothing to carry and nothing to fake. There is no dead click: the gene
is plain text, exactly as it is in the app for a run read off disk, and the file
says on its face that the drawer is unavailable and why.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import citations as citations_mod
from . import dim as dim_mod
from . import table as table_mod
from . import view as view_mod
from . import sources as SOURCES
from .figure import FIGURE_TITLE, RING_SPECS, ring_specs_for
from .manifest import git_sha
from .order import (ALPHABETICAL, SORT_LABEL_JOIN, SORT_LABEL_PREFIX,
                    SORT_LABEL_TAIL, SORT_LABEL_UNSORTED, SUBMITTED, SortKey,
                    missing_on, order_by_keys, sort_label, sort_phrase)
from .run import FILTERABLE_RINGS
from .filters import OPS

STATIC = Path(__file__).parent / "static"

# Where the FAILED rows of the citation strip send a reader. In the app it is
# `/api/run/<id>/manifest.json`; here the manifest is inlined into the file, so the
# pointer resolves inside the document rather than at a route nothing is serving.
# `citations.cell_provenance` takes it as a parameter for exactly this reason.
MANIFEST_ANCHOR = "#export-manifest"

# What this file says about the drawer, in place of the app's three artefact links.
# Authored here, handed to `view.table_payload`, printed by `app.js` — the page
# draws claims and does not write them.
#
# TWO LENGTHS, ONE STATEMENT OF THE REASON. `UX-v3-rounds.md` B14 is one claim
# carried at two weights, and a paragraph above the grid that argued the same case
# as the bullet at the top of the page would be exactly that. So the REASON is
# stated once, in `What it cannot do`, which is where a reader arriving at the file
# finds out what it is; the line above the grid is the short answer to the question
# that is actually asked there — "why is this gene not a link?" — and adds nothing.
DRAWER_OFFLINE_NOTE = (
    "No per-gene drawer in this file: the gene names are plain text rather than "
    "dead buttons. See “What it cannot do” above.")
DRAWER_OFFLINE_REASON = (
    "A gene's drug pipeline and its literature are live ChEMBL and Europe PMC "
    "calls, made when you click it, so there is nothing here to carry and nothing "
    "worth faking. Everything else on this page is this run's own artefacts, "
    "frozen at export time.")


def _script_safe(blob: str) -> str:
    """JSON that cannot end the `<script>` element it is embedded in.

    `</script>` inside a string literal closes the tag in an HTML parser, and
    U+2028/9 are line terminators in JavaScript source but not in JSON. Both would
    produce a file that opens to a blank page on Puck's machine.
    """
    return (blob.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def grade_id(spec: SortKey) -> str:
    return f"{spec.ordering}|{'asc' if spec.reverse else 'desc'}"


def orderable_keys(rings: Sequence[dict[str, str]]) -> list[SortKey]:
    """Every sort key this export can be put into, both directions.

    Exactly the orderings the app offers for this run — the Gene column, and each
    ring the figure could also be ordered by — so the export can never sort by
    something the circle could not. `completeness` is absent because ordering by k
    would order by how much anybody has studied a gene (PLAN §2, COUNCIL M3).
    """
    orderings = [SUBMITTED, ALPHABETICAL] + [
        f"ring:{r['key']}" for r in rings if r["key"] in FILTERABLE_RINGS]
    return [SortKey(o, rev) for o in orderings for rev in (False, True)]


@dataclass
class Grades:
    """What the browser is allowed to know about the sort: integers, per key."""

    grades: dict[str, dict[str, int]]
    phrase: dict[str, str]
    unplaceable: dict[str, list[str]]


def sort_grades(t: table_mod.RunTable) -> Grades:
    """Every gene's position on every key, as a dense rank with ties intact.

    The rank comes from `order.sort_key_and_reverse`, which is the function
    `order._sorted_by_one` uses, so the desirability sign, the direction and the
    rule that an unmeasured cell sorts LAST rather than LOWEST are all this build's
    single answer rather than a copy of it. Ties have to survive — two genes with
    the same `tractable` value must come out of key 1 tied so that key 2 can break
    it, which is what makes a multi-key sort lexicographic instead of a permutation
    with a second key that never fires.
    """
    from .order import sort_key_and_reverse

    cells = t.cells_by_ring()
    position = {g: i for i, g in enumerate(t.submitted_order)}
    ring_labels = {s.key: s.label
                   for s in ring_specs_for(SOURCES.DESource.from_manifest(t.manifest))}
    grades: dict[str, dict[str, int]] = {}
    phrase: dict[str, str] = {}
    for spec in orderable_keys(t.rings):
        keyfn, reverse = sort_key_and_reverse(spec, cells, position)
        vals = {g: keyfn(g) for g in t.submitted_order}
        # `sorted(..., reverse=True)` is descending AND stable: equal elements keep
        # the order they arrived in. Ranking the distinct values in the same
        # direction reproduces that exactly, and equal values share a rank.
        rank = {v: i for i, v in enumerate(sorted(set(vals.values()),
                                                 reverse=reverse))}
        grades[grade_id(spec)] = {g: rank[vals[g]] for g in t.submitted_order}
        phrase[grade_id(spec)] = sort_phrase(spec, ring_labels)
    unplaceable = {
        f"ring:{r['key']}": missing_on(t.submitted_order, cells, f"ring:{r['key']}")
        for r in t.rings}
    return Grades(grades=grades, phrase=phrase, unplaceable=unplaceable)


def order_from_grades(submitted: Sequence[str], g: Grades,
                      keys: Sequence[SortKey]) -> list[str]:
    """The Python twin of the export's sort loop, line for line.

    It exists to be asserted against `order.order_by_keys`. The JavaScript in
    `export_boot.js` is this function and nothing more, so holding this one to the
    real sort at export time, and the JavaScript to this one in the browser via
    `EXPORT.probes`, covers the whole path with no step taken on faith.
    """
    genes = list(submitted)
    for spec in reversed(list(keys)):
        grade = g.grades[grade_id(spec)]
        genes = [x for _, _, x in sorted((grade[x], i, x)
                                         for i, x in enumerate(genes))]
    return genes


def _key_sequences(keys: Sequence[SortKey]) -> list[list[SortKey]]:
    """The sort states the export is checked over: every single key in both
    directions, every adjacent pair, and a few triples. Not exhaustive — the
    reader can build a sequence nobody enumerated — which is why the *algorithm* is
    asserted equal to `order_by_keys` rather than only its answers."""
    out: list[list[SortKey]] = [[]]
    out += [[k] for k in keys]
    singles = [k for k in keys if not k.reverse]
    out += [[a, b] for a, b in zip(singles, singles[1:])]
    out += [[a, SortKey(b.ordering, True)] for a, b in zip(singles, singles[1:])][:4]
    out += [list(singles[i:i + 3]) for i in range(0, max(0, len(singles) - 2), 3)]
    return out


def assert_grades_reproduce_the_sort(t: table_mod.RunTable, g: Grades) -> int:
    """The grades are a faithful representation of the sort, or no file is written.

    Checked against `order.order_by_keys` on this run's real cells, for every key
    sequence the export ships an answer for. A refusal here is the right outcome: a
    detached artefact that sorts differently from the app is the thing this whole
    module is arranged to prevent, and it is better not to exist than to be carried
    to another machine.
    """
    cells = t.cells_by_ring()
    checked = 0
    for keys in _key_sequences(orderable_keys(t.rings)):
        want = order_by_keys(t.submitted_order, cells, keys)
        got = order_from_grades(t.submitted_order, g, keys)
        if got != want:
            named = ", ".join(grade_id(k) for k in keys) or "(no keys)"
            raise AssertionError(
                f"run {t.run_id}: the frozen sort grades do not reproduce "
                f"`order.order_by_keys` for [{named}]: grades give {got}, the "
                f"build gives {want}. No export is written: a table that sorts "
                f"differently from the app is the wrong output this export exists "
                f"to not be.")
        checked += 1
    return checked


def sort_probes(t: table_mod.RunTable, g: Grades) -> list[dict[str, Any]]:
    """Python's row order, sentence and no-value block for a set of sort states,
    for the export to reproduce in the reader's browser before it draws."""
    cells = t.cells_by_ring()
    ring_labels = {s.key: s.label
                   for s in ring_specs_for(SOURCES.DESource.from_manifest(t.manifest))}
    out = []
    for keys in _key_sequences(orderable_keys(t.rings)):
        order = order_by_keys(t.submitted_order, cells, keys)
        primary = keys[0].ordering if keys else ""
        out.append({
            "name": ", ".join(grade_id(k) for k in keys) or "unsorted",
            "keys": [{"ordering": k.ordering, "reverse": k.reverse} for k in keys],
            "order": order if keys else list(t.genes),
            "sort_note": sort_label(keys, ring_labels),
            "missing_block": (missing_on(order, cells, primary) if primary else []),
        })
    return out


def _thresholds_for(values: list[float]) -> list[float]:
    """Bounds worth checking on one column: the edges, the middle, and one either
    side of the range so that "nothing matches" and "everything matches" are both
    exercised. The odd decimal is there to exercise the number formatting on both
    sides — a threshold that reads `2.5` in the app and `2.50000` in the export is
    one claim in two forms, which is the defect one step short of a wrong one.
    """
    if not values:
        return [0.0]
    lo, hi = min(values), max(values)
    mid = statistics.median(values)
    return [lo, mid, hi, lo - 1.0, hi + 1.0, round(mid + 0.123456789, 9)]


def dim_probes(t: table_mod.RunTable) -> list[dict[str, Any]]:
    """Python's three-state verdict for every dimmable column, both comparisons.

    This is what stands behind the one transliterated decision in the export. Each
    probe carries the per-gene state, the per-gene reason, the three counts and the
    count line, because those are four different ways for a browser-side comparison
    to be subtly wrong and only the first of them changes what a row looks like.
    """
    cells = t.cells_by_ring()
    specs = {s.key: s
             for s in ring_specs_for(SOURCES.DESource.from_manifest(t.manifest))}
    # `bound_label`, the same name `view.table_payload` hands `dim.count_line`:
    # these probes are the baked answer the export's own grid is checked
    # against, so a second label here would be a mismatch inside the artefact.
    labels = {r["key"]: citations_mod.bound_label(specs.get(r["key"]), r["label"])
              for r in t.rings}
    genes = list(t.genes)
    out: list[dict[str, Any]] = []

    def probe(dims: list[dim_mod.DimFilter], name: str) -> dict[str, Any]:
        o = dim_mod.classify(genes, cells, dims)
        dim_mod.assert_nothing_left(genes, o)
        return {
            "name": name,
            "dims": [{"ring": d.ring, "op": d.op, "threshold": d.threshold}
                     for d in dims],
            "state": o.state, "reason": o.reason, "counts": o.counts,
            "count_line": dim_mod.count_line(o, dims, labels),
            "count_note": dim_mod.count_note(o, dims, labels),
        }

    dimmable = view_mod.dimmable_rings(t.rings)
    for ring in dimmable:
        values = [c.value for c in cells.get(ring, {}).values()
                  if c.state == "value" and c.value is not None]
        for op in ("ge", "le"):
            for thr in _thresholds_for([float(v) for v in values]):
                f = dim_mod.DimFilter(ring=ring, op=op, threshold=thr)
                out.append(probe([f], f"{ring} {OPS[op][0]} {thr:g}"))
    # Combinations, because the precedence between a missed bound and an
    # unevaluable one is its own rule and one filter cannot exercise it.
    for a, b in zip(dimmable, dimmable[1:]):
        va = [float(c.value) for c in cells.get(a, {}).values()
              if c.state == "value" and c.value is not None]
        vb = [float(c.value) for c in cells.get(b, {}).values()
              if c.state == "value" and c.value is not None]
        fa = dim_mod.DimFilter(a, "ge", statistics.median(va) if va else 0.0)
        fb = dim_mod.DimFilter(b, "le", statistics.median(vb) if vb else 0.0)
        out.append(probe([fa, fb], f"{a} + {b}"))
    return out


def _templates() -> dict[str, Any]:
    """Every string the export's own JavaScript prints, authored in Python.

    The browser fills blanks; it does not write sentences. A count line phrased one
    way in the app and another in a file Puck reads on a train is one claim at two
    weights, which is `UX-v3-rounds.md` B14 with a machine boundary in the middle.
    """
    return {
        "OP_PHRASE": dim_mod.OP_PHRASE,
        "OP_SYMBOL": {op: OPS[op][0] for op in dim_mod.OP_PHRASE},
        "PHRASE_T": dim_mod.PHRASE_T,
        "MISSED_T": dim_mod.MISSED_T,
        "SKIPPED_T": dim_mod.SKIPPED_T,
        "SKIPPED_TAIL": dim_mod.SKIPPED_TAIL,
        "REASON_JOIN": dim_mod.REASON_JOIN,
        "ABSENT_STATE": dim_mod.ABSENT_STATE,
        "NOTE_T": dim_mod.NOTE_T,
        "NOTE_JOIN": dim_mod.NOTE_JOIN,
        "COUNT_LINE_T": dim_mod.COUNT_LINE_T,
        "NOT_A_NUMBER_T": dim_mod.NOT_A_NUMBER_T,
        "SORT_LABEL_UNSORTED": SORT_LABEL_UNSORTED,
        "SORT_LABEL_PREFIX": SORT_LABEL_PREFIX,
        "SORT_LABEL_JOIN": SORT_LABEL_JOIN,
        "SORT_LABEL_TAIL": SORT_LABEL_TAIL,
    }


def export_blob(run_id: str) -> dict[str, Any]:
    """Everything the file carries as data. No URL, no host, no route."""
    t = table_mod.load(run_id)
    g = sort_grades(t)
    checked = assert_grades_reproduce_the_sort(t, g)
    payload = view_mod.table_payload(
        run_id,
        in_memory=False,
        record_url=MANIFEST_ANCHOR,
        drawer_note=DRAWER_OFFLINE_NOTE,
        artefact_links=[],
        # A detached file has no server to read a baked drawer for it, so no
        # gene is drawn as a button however many drawers the run has on disk
        # (`view.table_payload`: "the export passes `[]`").
        baked_drawers=[],
    )
    return {
        "payload": payload,
        "submitted_order": t.submitted_order,
        "grades": g.grades,
        "sort_phrase": g.phrase,
        "unplaceable": g.unplaceable,
        "templates": _templates(),
        "probes": {"sort": sort_probes(t, g), "dim": dim_probes(t)},
        "exported_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"),
        "exported_by_git_sha": git_sha(),
        "sort_algorithm_checks": checked,
    }


# --- the page ---------------------------------------------------------------
#
# The markup is `static/index.html`'s `#pane-table`, minus the run picker: the file
# is one run and there is nothing to pick. Every container `renderTable` writes
# into is here, under the same ids, because the renderer is the app's.

_PANE = """
      <div id="table-status" class="hint"></div>
      <div id="table-meta"></div>
      <div id="table-sort"></div>
      <div id="table-dim"></div>
      <div id="table-dim-counts"></div>
      <div id="table-grid" class="scroll-x"></div>
      <div id="table-sources"></div>
      <div id="table-ledger"></div>
"""

_EXPORT_CSS = """
/* The export's own rules. Everything else on this page is `app.css`, inlined
   verbatim, so the grid is the app's grid and not a re-style of it.
   `UX-v3-rounds.md` B12's 12.0 px floor is a floor over this file too --
   `test_export.py::test_no_rule_the_export_adds_sets_type_under_the_floor`. */
#export-what, #export-provenance, #export-selfcheck { margin: 14px 0; }
#export-provenance dl { display: grid; grid-template-columns: max-content 1fr;
  gap: 2px 14px; margin: 6px 0 0; font-size: 13px; }
#export-provenance dt { color: var(--muted); }
#export-provenance dd { margin: 0; }
#export-manifest-body { max-height: 24em; overflow: auto; font-size: 12px;
  background: #f4f6f8; padding: 10px; border-radius: 6px; }
"""


def _provenance_html(blob: dict[str, Any], t: table_mod.RunTable) -> str:
    p = blob["payload"]
    bad = p["any_invariant_failed"]
    passes = sum(1 for i in p["invariants"] if i.get("outcome") == "PASS")
    return f"""
    <div id="export-provenance">
      <h2>What this file is</h2>
      <dl>
        <dt>run id</dt><dd><code>{_esc(p['run_id'])}</code></dd>
        <dt>run timestamp (UTC)</dt><dd>{_esc(p['timestamp_utc'])}</dd>
        <dt>region</dt><dd>{_esc(p['region'])}</dd>
        <dt>git SHA the run was drawn at</dt>
          <dd><code>{_esc(p['git_sha'])}</code></dd>
        <dt>invariants</dt>
          <dd>{'<span class="pill bad">A CHECK FAILED</span>' if bad
              else '<span class="pill ok">no check failed</span>'}
            · {passes} of {len(p['invariants'])} PASS, listed in full below.
            <b>NOT_EVALUATED is not a pass.</b></dd>
        <dt>spoke order the circle used</dt>
          <dd>{_esc(p['ordering_note'] or p['ordering'])}{
            f"<br><small>{_esc(p['ordering_note_drift'])}</small>"
            if p.get('ordering_note_drift') else ''}</dd>
        <dt>rings drawn</dt><dd>{len(p['rings'])}, in the figure's radial order.
          Column 1 is the outermost band</dd>
        <dt>exported (UTC)</dt><dd>{_esc(blob['exported_at_utc'])}</dd>
        <dt>git SHA this file was exported at</dt>
          <dd><code>{_esc(blob['exported_by_git_sha'])}</code></dd>
      </dl>
    </div>
    """


def _what_this_is_not_html(blob: dict[str, Any]) -> str:
    """Saying what a thing is not is house style here, and this artefact needs it
    more than the figure does: it is detached, it will be read away from the app,
    and the two things it cannot do are things the app can."""
    return f"""
    <div id="export-what">
      <div class="note warn"><b>This is a frozen copy of one run's table. Nothing
        on it is ranked or scored</b> (PLAN §3). Sorting by a column is
        lexicographic and combines nothing, and there is no column here that means
        "how good is this gene". Genes with no value on the column you sort by are
        <b>last, not lowest</b>.</div>
      <h2>What it cannot do</h2>
      <ul class="tight">
        <li><b>No per-gene drawer.</b> {_esc(DRAWER_OFFLINE_REASON)}</li>
        <li><b>No figure.</b> This is the grid only. The circle, its legend and its
          caveats are in the run's own artefacts beside
          <code>values.csv</code>.</li>
        <li><b>No live anything.</b> The file makes no network request and
          references no external asset: it is read from
          <code>file://</code> with no server, which is why nothing in it is
          fetched. The source links in the citation strip are the one exception a
          reader can choose to take, and they only do anything if you click one.
          </li>
        <li><b>It does not re-read the sources.</b> Every number is the cell this
          run's renderer wrote, carried across unchanged; a number here that is
          wrong was wrong on the figure too.</li>
      </ul>
      <div id="export-selfcheck"></div>
    </div>
    """


def _esc(s: Any) -> str:
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_html(run_id: str) -> str:
    """The whole file, as a string. Nothing outside this process is touched."""
    t = table_mod.load(run_id)
    blob = export_blob(run_id)
    css = (STATIC / "app.css").read_text() + _EXPORT_CSS
    app_js = (STATIC / "app.js").read_text()
    boot_js = (STATIC / "export_boot.js").read_text()
    manifest_json = json.dumps(t.manifest, indent=2, sort_keys=True)
    data = _script_safe(json.dumps(blob, ensure_ascii=False, sort_keys=True))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- targetring: offline table export. ONE FILE, no server, no network.
     Everything below is inlined: the stylesheet is `static/app.css`, the renderer
     is `static/app.js` verbatim, and the data is this run's own artefacts. There
     is deliberately no <link>, no <script src>, no <img>, no font and no fetch:
     opened from `file://` a browser blocks requests for sibling files, so an
     external reference would render an empty grid with nothing to explain it. -->
<title>{_esc(FIGURE_TITLE)} · run {_esc(run_id)} table (offline export)</title>
<!-- An empty data: icon, so not even a favicon is requested. -->
<link rel="icon" href="data:,">
<style>
{css}
</style>
</head>
<body>
<header>
  <h1>targetring <span class="ver">table export</span></h1>
  <p class="sub">Run <code>{_esc(run_id)}</code> · region
    <b>{_esc(blob['payload']['region'])}</b> · offline, single file, no server.
    <strong>Nothing here is ranked or scored.</strong></p>
</header>
<main>
<section id="result">
{_what_this_is_not_html(blob)}
{_provenance_html(blob, t)}
  <div class="tabpane" id="pane-table">
{_PANE}
  </div>
  <details id="export-invariants" open>
    <summary><span class="chev" aria-hidden="true">▾</span>
      <b>Invariants and source contracts</b>: what this run checked about itself,
      read back out of its manifest</summary>
    <div class="reveal" id="invariants"></div>
  </details>
  <details id="export-manifest">
    <summary><span class="chev" aria-hidden="true">▾</span>
      <b>manifest.json</b>: this run's own record, inlined verbatim: sources,
      schema versions, timings and every invariant result</summary>
    <div class="reveal"><pre id="export-manifest-body">{_esc(manifest_json)}</pre></div>
  </details>
</section>
</main>
<script>
window.TARGETRING_OFFLINE_EXPORT = true;
const EXPORT = {data};
</script>
<script>
{app_js}
</script>
<script>
{boot_js}
</script>
</body>
</html>
"""


def write_export(run_id: str, out_dir: Path | None = None,
                 filename: str | None = None) -> Path:
    """Write the file, and return where it landed. The only side effect."""
    html = build_html(run_id)
    d = out_dir or (table_mod.run_dir(run_id))
    d.mkdir(parents=True, exist_ok=True)
    path = d / (filename or f"targetring-{run_id}-table.html")
    path.write_text(html, encoding="utf-8")
    return path


__all__ = ["build_html", "write_export", "export_blob", "sort_grades", "Grades",
           "order_from_grades", "orderable_keys", "grade_id",
           "assert_grades_reproduce_the_sort", "sort_probes", "dim_probes",
           "MANIFEST_ANCHOR", "DRAWER_OFFLINE_NOTE", "DRAWER_OFFLINE_REASON",
           "STATIC"]
