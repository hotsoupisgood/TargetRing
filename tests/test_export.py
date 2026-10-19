"""The offline HTML export — v4.0.

Puck reads a run's table on another computer, on another network. Nothing may be
published (`AGENTS.md` rule 10), so the export is a file he carries: one `.html`,
opened from `file://`, no server. Two properties decide whether that file is a
deliverable or a liability, and everything below is one of them:

* **It must agree with the app.** An export that looks like the app and quietly
  differs from it is `AGENTS.md:17`'s plausible wrong output, with the extra
  property that the disagreement is discovered on a machine the app is not on. So
  `TestOnePayloadOneRenderer` asserts the export and `/api/run/{id}/values` are the
  same dict from the same builder, and that the grid is drawn by `app.js` inlined
  verbatim rather than by a copy of it. `TestTheFrozenSortIsTheBuildsSort` asserts
  the one thing the export has to precompute — the row order — against
  `order.order_by_keys` on real runs, and `TestTheExportCarriesItsOwnProof` asserts
  the file ships Python's answers for the one decision it has to evaluate in the
  browser.

* **It must reach him whole.** A `file://` document cannot fetch its siblings, so
  a single external reference is an empty grid with nothing on screen to explain
  it. `TestNothingIsFetchedAndNothingIsExternal` reads the bytes.

The live half of the acceptance test — the real browser, the real `file://` URL,
the network panel, the clicks — is `scripts/v40_export_shots.py`. These tests
cannot see a request that is never made; that script can.

Nothing here contacts a source or triggers an analysis (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import dim as dim_mod
from targetring import export as export_mod
from targetring import table as table_mod
from targetring import view as view_mod
from targetring.app import app
from targetring.order import SortKey, order_by_keys
from targetring.run import FILTERABLE_RINGS

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
STATIC = REPO / "targetring" / "static"

# The run the brief named as the other schema era: ring 3 is `druggability`,
# retired in v2.3, so the export has to head and cite a column this build no
# longer draws.
RETIRED_RING_RUN = "20260924T061611Z-e8d4a4"

# How the one data block is delimited in the document, so the `blob` fixture can
# read back exactly what the file carries.
_EXPORT_OPEN = "const EXPORT = "
_EXPORT_CLOSE = ";\n</script>"


def a_real_run() -> str:
    for d in sorted((p for p in RUNS.iterdir() if p.name.startswith("20")),
                    reverse=True):
        if (d / "values.csv").exists() and (d / "manifest.json").exists():
            return d.name
    pytest.skip("no run artefacts under runs/")


def _runs_newest_first() -> list[str]:
    return [d.name for d in sorted((p for p in RUNS.iterdir()
                                    if p.name.startswith("20")), reverse=True)
            if (d / "values.csv").exists() and (d / "manifest.json").exists()]


def a_run_whose_sort_can_be_broken() -> tuple[str, str, str]:
    """The newest run on which corrupting one grade actually reorders the table.

    **Why this is not just `a_real_run()`.** Two tests in this file assert that a
    deliberately broken grade is CAUGHT. On a one-gene run nothing can break: a
    one-element list has one order, so the grades reproduce the sort no matter
    what is written into them, `assert_grades_reproduce_the_sort` is right not to
    raise, and the test fails. Three single-gene runs landed in `runs/` on
    2026-09-30 and became "newest", which is exactly how that happened.

    Rather than pinning a convenient run, this searches for a corruption that
    provably changes the order and returns it — so the test cannot pass against a
    run where the thing it claims to detect is undetectable. Returns
    `(run_id, grade_id, gene)`; fails the test if no run on disk qualifies.
    """
    for rid in _runs_newest_first():
        t = table_mod.load(rid)
        if len(t.genes) < 2:
            continue
        g = export_mod.sort_grades(t)
        for spec in export_mod.orderable_keys(t.rings):
            gid = export_mod.grade_id(spec)
            base = export_mod.order_from_grades(t.submitted_order, g, [spec])
            for gene in t.submitted_order:
                trial = copy.deepcopy(g)
                trial.grades[gid] = dict(trial.grades[gid])
                trial.grades[gid][gene] = -99
                if export_mod.order_from_grades(
                        t.submitted_order, trial, [spec]) != base:
                    return rid, gid, gene
    pytest.fail(
        "no run under runs/ has a sort that a corrupted grade can change, so "
        "the refusal below cannot be exercised. This is a FAILURE and not a "
        "skip: the assertion would pass vacuously, and a refusal test that "
        "passes because it stopped discriminating converts a known gap into an "
        "invisible one.")


def states_this_run_can_produce(t: table_mod.RunTable) -> set[str]:
    """The dim treatments this run's own data is capable of showing.

    `match` and `dimmed` need only a threshold with genes on both sides of it,
    which `dim_probes` chooses per ring. `unevaluable` needs a gene with no value
    on a dimmable ring — a run where every cell is a number cannot produce one,
    and no amount of probing will invent it.

    This is what makes "the probes exercise all three" checkable without either
    weakening it or asserting something a run cannot satisfy.
    """
    cells = t.cells_by_ring()
    has_gap = any(not cells[r].get(gene) or cells[r][gene].state != "value"
                  for r in view_mod.dimmable_rings(t.rings) for gene in t.genes)
    return {"match", "dimmed"} | ({"unevaluable"} if has_gap else set())


@pytest.fixture(scope="module")
def run_id() -> str:
    return a_real_run()


@pytest.fixture(scope="module")
def loaded(run_id: str) -> table_mod.RunTable:
    return table_mod.load(run_id)


@pytest.fixture(scope="module")
def html(run_id: str) -> str:
    return export_mod.build_html(run_id)


@pytest.fixture(scope="module")
def blob(html: str) -> dict:
    """The data the FILE ships, read back out of the file — not a second export.

    `build_html` calls `export_blob` itself, so a `blob` fixture that called
    `export_blob` again produced a SECOND export of the same run, with its own
    `datetime.now()` stamp. Every `(html, blob)` pair in this file was then two
    artefacts compared as if they were one, and
    `test_the_run_is_stamped_on_the_face_of_it` lost a ~37 ms race against the
    `%H:%M:%S` boundary roughly 3% of the time — a flake with no bug under it,
    and worse, a stamp assertion that was never actually about this document.

    Reading the blob back out is also the module's own thesis applied to its
    fixtures: there is one payload, and the export is a RENDERING of it. The
    escaping `_script_safe` applies is pure JSON `\\uXXXX`, so `json.loads`
    recovers the dict with no un-escaping step to get wrong.
    """
    start = html.index(_EXPORT_OPEN) + len(_EXPORT_OPEN)
    return json.loads(html[start:html.index(_EXPORT_CLOSE, start)])


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _code(path: Path) -> str:
    """A JavaScript file with its comments removed.

    These tests ask what the code *says on screen*, and this build's comments are
    long and quote the sentences they are about. Scanning them would make
    explaining a rule a violation of it.
    """
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("//"))


def _static_html(html: str) -> str:
    """The document without its inlined script and style bodies.

    `app.js` is inlined verbatim and contains `/api/…` template literals for the
    routes the SERVED app calls — correct there, unreachable here, and not links
    in this document. The manifest is inlined as escaped text in a `<pre>` and its
    recorded URLs are likewise text. What a static scan can honestly ask is what
    the MARKUP loads and points at; what the runtime points at is a property of
    the data, and `test_no_url_in_the_data_points_at_a_route` asks that.
    """
    out = re.sub(r"<script\b[^>]*>.*?</script>", "<script></script>", html, flags=re.S)
    return re.sub(r"<style\b[^>]*>.*?</style>", "<style></style>", out, flags=re.S)


def _some_runs(n: int = 4) -> list[str]:
    """A few real runs of different vintages, not one pinned id. The export's
    claim is about `runs/`, not about the newest thing in it."""
    ids = [d.name for d in sorted((p for p in RUNS.iterdir()
                                   if p.name.startswith("20")), reverse=True)
           if (d / "values.csv").exists() and (d / "manifest.json").exists()]
    if not ids:
        pytest.skip("no run artefacts under runs/")
    picked = ids[:2] + ids[len(ids) // 2:len(ids) // 2 + 1] + ids[-1:]
    if RETIRED_RING_RUN in ids and RETIRED_RING_RUN not in picked:
        picked.append(RETIRED_RING_RUN)
    return list(dict.fromkeys(picked))[:n + 1]


class TestOnePayloadOneRenderer:
    """The export is a second RENDERING of a run, never a second computation of
    it — the rule the table view was built under, applied to a detached file."""

    def test_the_export_and_the_endpoint_are_the_same_payload(
            self, client, run_id, blob) -> None:
        """Not "equivalent": the same dict, from `view.table_payload`, minus the
        three fields that are facts about the SURFACE rather than about the run."""
        served = client.get(f"/api/run/{run_id}/values").json()
        # `baked_drawers` is a surface fact too: the app reads the run's
        # `drawer/` directory, the file has no server to read one for it.
        surface = {"in_memory", "drawer_note", "artefact_links", "citations",
                   "baked_drawers"}
        assert set(served) == set(blob["payload"]), (
            "the export and the endpoint no longer carry the same fields")
        for key in set(served) - surface:
            assert served[key] == blob["payload"][key], (
                f"the export and the app disagree about {key!r}")

    def test_the_cells_are_identical_down_to_the_colour(
            self, client, run_id, blob) -> None:
        served = client.get(f"/api/run/{run_id}/values").json()
        assert [g["cells"] for g in served["genes"]] == \
               [g["cells"] for g in blob["payload"]["genes"]]
        assert [g["gene"] for g in served["genes"]] == \
               [g["gene"] for g in blob["payload"]["genes"]]

    def test_only_the_three_surface_fields_differ(self, blob) -> None:
        p = blob["payload"]
        assert p["in_memory"] is False
        assert p["artefact_links"] == [], (
            "the export offered a link to a route nothing is serving")
        assert p["drawer_note"] == export_mod.DRAWER_OFFLINE_NOTE

    def test_the_renderer_is_app_js_verbatim(self, html) -> None:
        """Inlined byte-for-byte. The moment this is a copy instead of the file,
        the app and the export can be edited apart."""
        assert (STATIC / "app.js").read_text() in html

    def test_the_export_defines_no_renderer_of_its_own(self) -> None:
        boot = (STATIC / "export_boot.js").read_text()
        for fn in ("renderTable", "cellHtml", "renderDimBar", "renderDimCounts",
                   "renderTableSources", "renderInvariants"):
            assert f"function {fn}" not in boot, (
                f"`export_boot.js` defines its own {fn}; there must be one "
                f"renderer and it is `app.js`'s")

    def test_the_only_function_replaced_is_the_fetch(self) -> None:
        boot = (STATIC / "export_boot.js").read_text()
        assert "loadTable = function" in boot
        app_js = (STATIC / "app.js").read_text()
        assert "async function loadTable" in app_js
        # And the app's one is still a fetch, so the two halves of this design
        # have not quietly converged on the same thing.
        assert 'fetch(`/api/run/${encodeURIComponent(id)}/values`' in app_js

    def test_the_export_neutralises_only_the_bootstrap(self) -> None:
        """`init()` is the one top-level statement in `app.js`, and it is the one
        thing the export suppresses. A second guarded statement would be a second
        way for the app and the export to behave differently."""
        js = (STATIC / "app.js").read_text()
        top = [ln for ln in js.splitlines()
               if re.match(r"^[A-Za-z_$(]", ln)
               and not re.match(r"^(function|async function|const|let|var|\"use)", ln)]
        assert top == ['if (!(typeof window !== "undefined" && '
                       'window.TARGETRING_OFFLINE_EXPORT)) init();'], top

    def test_the_flag_is_set_before_app_js_runs(self, html) -> None:
        assert (html.index("window.TARGETRING_OFFLINE_EXPORT = true")
                < html.index("async function loadTable")), (
            "app.js would boot itself and fetch before the flag was set")

    def test_the_wording_of_the_dim_sentences_is_not_duplicated(self) -> None:
        """The comparison is transliterated; the WORDS are not. Every sentence the
        export prints about dimming is a `dim.py` template it fills in."""
        boot = _code(STATIC / "export_boot.js")
        for phrase in ("is at least", "cannot be evaluated", "still on the grid",
                       "shown ·", "which is not"):
            assert phrase not in boot, (
                f"`export_boot.js` writes {phrase!r} itself instead of filling "
                f"`dim.py`'s template")
        for name in ("PHRASE_T", "MISSED_T", "SKIPPED_T", "NOTE_T",
                     "COUNT_LINE_T", "SKIPPED_TAIL"):
            assert f"T.{name}" in boot

    def test_the_row_order_sentence_is_the_builds_own_words(self) -> None:
        boot = _code(STATIC / "export_boot.js")
        for name in ("SORT_LABEL_UNSORTED", "SORT_LABEL_PREFIX",
                     "SORT_LABEL_JOIN", "SORT_LABEL_TAIL"):
            assert f"T.{name}" in boot
        assert "Lexicographic" not in boot


class TestTheFrozenSortIsTheBuildsSort:
    """The one thing the export precomputes. Held to `order.order_by_keys` rather
    than described as equal to it."""

    @pytest.mark.parametrize("rid", _some_runs())
    def test_the_grades_reproduce_the_sort_on_every_run_tried(self, rid) -> None:
        t = table_mod.load(rid)
        g = export_mod.sort_grades(t)
        checked = export_mod.assert_grades_reproduce_the_sort(t, g)
        assert checked > 20, "the export is checking too few sort states to mean much"

    def test_the_export_refuses_to_be_written_if_they_do_not(self) -> None:
        """The assertion is load-bearing, not decorative: break a grade and no
        file is produced. A table that sorts differently from the app is worse
        than no table.

        Takes its run from `a_run_whose_sort_can_be_broken` rather than from the
        `loaded` fixture, which is whatever happens to be newest in `runs/`. This
        test spent some time failing because that was a one-gene run, where no
        corruption can change the order of a one-element list and the refusal
        correctly never fired. The code was right and so was the test; the
        fixture could not exercise either.
        """
        rid, gid, gene = a_run_whose_sort_can_be_broken()
        t = table_mod.load(rid)
        g = export_mod.sort_grades(t)
        # Sanity: unbroken, it reproduces the sort and checks enough to mean
        # something. Without this the raise below could be about anything.
        assert export_mod.assert_grades_reproduce_the_sort(t, g) > 20
        g.grades[gid] = dict(g.grades[gid])
        g.grades[gid][gene] = -99
        with pytest.raises(AssertionError, match="do not reproduce"):
            export_mod.assert_grades_reproduce_the_sort(t, g)

    def test_build_html_refuses_too_and_writes_nothing(self) -> None:
        """"No file is produced" is the actual claim, and it is about
        `build_html`, not only about the assertion helper it calls."""
        rid, gid, gene = a_run_whose_sort_can_be_broken()
        real = export_mod.sort_grades

        def broken(t: table_mod.RunTable):
            g = real(t)
            if t.run_id == rid:
                g.grades[gid] = dict(g.grades[gid])
                g.grades[gid][gene] = -99
            return g

        export_mod.sort_grades = broken
        try:
            with pytest.raises(AssertionError, match="do not reproduce"):
                export_mod.build_html(rid)
        finally:
            export_mod.sort_grades = real

    def test_ties_survive_so_a_second_key_can_still_fire(self, loaded) -> None:
        """A permutation index would give every gene a distinct grade and key 2
        would never break anything — a multi-key sort that silently became a
        single-key one. Genes with equal values must share a grade."""
        g = export_mod.sort_grades(loaded)
        cells = loaded.cells_by_ring()
        found = False
        for spec in export_mod.orderable_keys(loaded.rings):
            ring = spec.ordering.removeprefix("ring:")
            per = cells.get(ring, {})
            pairs = [(a, b) for i, a in enumerate(loaded.genes)
                     for b in loaded.genes[i + 1:]
                     if per.get(a) and per.get(b)
                     and per[a].state == per[b].state == "value"
                     and per[a].value == per[b].value]
            for a, b in pairs:
                found = True
                assert g.grades[export_mod.grade_id(spec)][a] == \
                       g.grades[export_mod.grade_id(spec)][b], (
                    f"{a} and {b} have the same {ring} value and different grades")
        if not found:
            pytest.skip("this run has no tied values on any orderable ring")

    def test_missing_is_last_not_lowest_in_both_directions(self, loaded) -> None:
        """AGENTS.md rule 1, frozen into the grades. A gene with no value on a key
        must carry the LARGEST grade whichever way the values run — never 0, never
        interleaved."""
        g = export_mod.sort_grades(loaded)
        cells = loaded.cells_by_ring()
        for ring in (r["key"] for r in loaded.rings if r["key"] in FILTERABLE_RINGS):
            unplaced = set(g.unplaceable[f"ring:{ring}"])
            assert unplaced == {x for x in loaded.submitted_order
                                if not (cells[ring].get(x)
                                        and cells[ring][x].state == "value")}
            if not unplaced:
                continue
            for rev in (False, True):
                gr = g.grades[export_mod.grade_id(SortKey(f"ring:{ring}", rev))]
                top = max(gr.values())
                assert {x for x in gr if gr[x] == top} == unplaced, (
                    f"ring:{ring} reverse={rev}: the unmeasured genes are not the "
                    f"last block")

    def test_the_grades_offer_exactly_the_orders_the_figure_could_draw(
            self, loaded, blob) -> None:
        """`completeness` is absent: ordering by k would order by how much anybody
        has studied a gene (PLAN §2, COUNCIL M3)."""
        offered = {k.split("|")[0] for k in blob["grades"]}
        rings = {o.removeprefix("ring:") for o in offered if o.startswith("ring:")}
        assert rings == {r["key"] for r in loaded.rings
                         if r["key"] in FILTERABLE_RINGS}
        assert "ring:completeness" not in offered

    def test_the_python_twin_of_the_browsers_loop_is_the_thing_asserted(
            self, loaded) -> None:
        """`order_from_grades` is line-for-line what `export_boot.js` does, and it
        is what the grades are checked through — so the chain is
        `order_by_keys` == `order_from_grades` == the browser, with the last link
        held by the export's own probes."""
        g = export_mod.sort_grades(loaded)
        cells = loaded.cells_by_ring()
        keys = [k for k in export_mod.orderable_keys(loaded.rings) if not k.reverse]
        pair = keys[:2]
        assert export_mod.order_from_grades(loaded.submitted_order, g, pair) == \
               order_by_keys(loaded.submitted_order, cells, pair)


class TestTheExportCarriesItsOwnProof:
    """The dim comparison is the one decision that exists twice in this build. It
    is not left on trust — the file ships Python's answers and checks itself
    against them in the reader's browser before it draws."""

    def test_every_dimmable_column_is_probed_in_both_directions(
            self, loaded, blob) -> None:
        probed = {(p["dims"][0]["ring"], p["dims"][0]["op"])
                  for p in blob["probes"]["dim"] if len(p["dims"]) == 1}
        want = {(r, op) for r in view_mod.dimmable_rings(loaded.rings)
                for op in ("ge", "le")}
        assert probed == want

    def test_the_probes_carry_all_four_things_that_can_be_wrong(self, blob) -> None:
        for p in blob["probes"]["dim"]:
            assert set(p) >= {"state", "reason", "counts", "count_line"}
            assert sum(p["counts"].values()) == len(p["state"])

    def test_the_probes_exercise_every_treatment_this_run_can_show(
            self, loaded, blob) -> None:
        """The probes must exercise every treatment the run's data can produce —
        no fewer, and no claim to produce one it cannot.

        This asserted a flat `== {match, dimmed, unevaluable}` and failed, because
        the newest run in `runs/` is one gene with a value on every dimmable ring:
        there is no missing cell for a probe to call `unevaluable`, so no probe
        set over that run can contain one. The export was right, the requirement
        was right, and the fixture could not satisfy it.

        Relaxing it to `>=` or dropping `unevaluable` would have been the trap —
        a self-check that stops checking. So the expectation is computed from the
        run instead, and `test_some_run_on_disk_exercises_all_three` keeps the
        original claim alive where it can actually be met.
        """
        seen = {s for p in blob["probes"]["dim"] for s in p["state"].values()}
        want = states_this_run_can_produce(loaded)
        assert seen == want, (
            f"run {loaded.run_id} can show {sorted(want)}; its probes show "
            f"{sorted(seen)}. Missing: {sorted(want - seen)}. "
            f"Unexpected: {sorted(seen - want)}.")

    def test_some_run_on_disk_exercises_all_three(self) -> None:
        """The original claim, kept: the export's browser-side dim comparison is
        proven against all three treatments somewhere in `runs/`, not merely
        against whichever two the newest run happens to contain.

        Fails rather than skips if no run qualifies — that would mean the one
        decision this export evaluates in the reader's browser has never been
        checked in its `unevaluable` branch, which is a real gap and not a
        reason to go quiet.
        """
        for rid in _runs_newest_first():
            b = export_mod.export_blob(rid)
            seen = {s for p in b["probes"]["dim"] for s in p["state"].values()}
            if seen == {"match", "dimmed", "unevaluable"}:
                return
        pytest.fail(
            "no run under runs/ exercises all three dim treatments, so the "
            "export's self-check has never been held to its `unevaluable` "
            "branch on real data.")

    def test_a_multi_filter_probe_exercises_the_precedence_rule(self, blob) -> None:
        """One filter cannot show that a missed bound beats an unevaluable one."""
        assert any(len(p["dims"]) > 1 for p in blob["probes"]["dim"])

    def test_the_sort_probes_include_multi_key_states(self, blob) -> None:
        assert any(len(p["keys"]) >= 2 for p in blob["probes"]["sort"])
        assert any(len(p["keys"]) >= 3 for p in blob["probes"]["sort"])
        assert any(k["reverse"] for p in blob["probes"]["sort"] for k in p["keys"])

    def test_the_probe_answers_are_pythons_and_are_recomputed_not_copied(
            self, loaded, blob) -> None:
        """Spot-check the frozen answers against `dim.classify` itself, so a probe
        set that was generated from the same bug it is meant to catch would fail
        here."""
        cells = loaded.cells_by_ring()
        for p in blob["probes"]["dim"][:12]:
            dims = [dim_mod.DimFilter(**d) for d in p["dims"]]
            out = dim_mod.classify(loaded.genes, cells, dims)
            assert out.state == p["state"]
            assert out.reason == p["reason"]
            assert out.counts == p["counts"]

    def test_the_self_check_runs_before_the_grid_is_drawn(self) -> None:
        boot = (STATIC / "export_boot.js").read_text()
        assert boot.index("renderSelfCheck(selfCheck())") < boot.index(
            "loadTable(EXPORT.payload.run_id)")

    def test_a_failure_says_do_not_trust_rather_than_drawing_a_clean_table(
            self) -> None:
        """The same rule as the figure's invariant banner: when a check fails, say
        so on the artefact — never produce a clean-looking one."""
        boot = (STATIC / "export_boot.js").read_text()
        assert "DO NOT TRUST THIS EXPORT" in boot
        assert "banner-bad" in boot


class TestNothingIsFetchedAndNothingIsExternal:
    """A `file://` document cannot fetch its siblings. One external reference is
    an empty grid on Puck's machine with nothing on screen to explain it."""

    def test_no_element_loads_anything(self, html) -> None:
        # Scanned on the MARKUP, with script and style bodies blanked
        # (`_static_html`). `app.js` is inlined verbatim and, since the demo
        # gallery, contains `<img src="/api/run/…">` inside a template literal
        # -- text in a string, which the browser never parses as an element.
        # The contract this test holds (`export.py`: "no <link>, no <script
        # src>, no <img>") is about what the document loads, and a string is
        # not a load; `test_no_url_in_the_data_points_at_a_route` and
        # `test_the_markup_neither_loads_nor_links_to_anything_remote` already
        # ask the questions that are honestly askable of the script and the data.
        static = _static_html(html)
        offenders = re.findall(r"<(?:script|link|img|iframe|source|video|audio|"
                               r"object|embed)\b[^>]*\b(?:src|data)=", static)
        assert not offenders, offenders
        # The `<link>` check stays on the whole document, as it always was:
        # app.js has no `<link` in it, and the one favicon `<link>` is markup.
        links = re.findall(r'<link\b[^>]*href="([^"]*)"', html)
        assert links == ["data:,"], (
            f"the export references a stylesheet or an icon: {links}")

    def test_the_stylesheet_is_inlined_and_imports_nothing(self, html) -> None:
        assert (STATIC / "app.css").read_text() in html
        style = html[html.index("<style>"):html.index("</style>")]
        assert "@import" not in style
        assert "url(" not in style, "a CSS url() is a request"

    def test_the_markup_neither_loads_nor_links_to_anything_remote(
            self, html) -> None:
        """The source links are the point of the citation strip, and they are
        drawn at runtime from the data — so the MARKUP has none, and nothing in it
        is fetched on open."""
        static = _static_html(html)
        assert not re.findall(r'\bsrc="[^"]+"', static)
        assert not re.findall(r'href="https?://', static)
        hrefs = re.findall(r'href="([^"]*)"', static)
        assert not [h for h in hrefs if h.startswith("/")], hrefs

    def test_no_url_in_the_data_points_at_a_route(self, blob) -> None:
        """Where the runtime links come from. Every one is either a source
        document in this build's own registry, or the manifest inlined into this
        file — never `/api/…`, which nothing here is serving."""
        found: list[str] = []

        def walk(x, path="") -> None:
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(x, list):
                for i, v in enumerate(x):
                    walk(v, f"{path}[{i}]")
            elif isinstance(x, str) and x.startswith("/api/"):
                found.append(f"{path} = {x}")

        walk(blob)
        assert not found, f"the export carries dead server routes: {found}"

    def test_every_in_document_anchor_resolves(self, html) -> None:
        for a in {h for h in re.findall(r'href="(#[^"]*)"', html)}:
            assert f'id="{a[1:]}"' in html, f"{a} points at nothing in this file"
        # And the one the citation strip generates at runtime.
        assert f'id="{export_mod.MANIFEST_ANCHOR[1:]}"' in html

    def test_the_failed_rows_record_is_the_inlined_manifest(self) -> None:
        from targetring import citations
        got = citations.cell_provenance("failed", "tractable", "R1",
                                        record_url=export_mod.MANIFEST_ANCHOR)
        assert got["url"] == export_mod.MANIFEST_ANCHOR
        # And the app is unchanged by the parameter existing.
        assert citations.cell_provenance("failed", "tractable", "R1")["url"] == \
            "/api/run/R1/manifest.json"

    def test_the_manifest_the_link_points_at_is_really_in_the_file(
            self, html, run_id) -> None:
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        assert f'id="{export_mod.MANIFEST_ANCHOR[1:]}"' in html
        assert str(man["git_sha"]) in html
        assert str(man["timestamp_utc"]) in html

    def test_the_embedded_data_cannot_close_its_own_script_tag(self, html) -> None:
        blob = html[html.index("const EXPORT = "):html.index("</script>")]
        assert "</" not in blob and "<" not in blob, (
            "the JSON payload is not escaped; a `</script>` in a cell detail "
            "would end the script and blank the page")


class TestItSaysWhatItIsAndWhatItIsNot:
    """House style, and this artefact needs it more than the figure does: it is
    detached, it is read away from the app, and two things it cannot do are things
    the app can."""

    def test_the_drawer_is_declined_in_print_and_not_left_as_a_dead_click(
            self, html, blob) -> None:
        assert export_mod.DRAWER_OFFLINE_NOTE in html
        assert export_mod.DRAWER_OFFLINE_REASON in html
        assert "live ChEMBL and Europe PMC" in export_mod.DRAWER_OFFLINE_REASON
        # The renderer only draws a gene as a button when the run is in memory
        # or the payload names a baked drawer for it -- and the export's payload
        # names none (`baked_drawers=[]` in `export_blob`).
        assert blob["payload"]["in_memory"] is False
        assert blob["payload"]["baked_drawers"] == []
        js = (STATIC / "app.js").read_text()
        assert ('(d.in_memory || (d.baked_drawers || []).includes(g.gene))\n'
                '          ? `<button class="gene linkish"') in js

    def test_the_run_is_stamped_on_the_face_of_it(self, html, blob) -> None:
        p = blob["payload"]
        for claim in (p["run_id"], p["timestamp_utc"], p["git_sha"], p["region"]):
            assert str(claim) in html, f"{claim!r} is not printed on the export"
        assert blob["exported_at_utc"] in html
        assert blob["exported_by_git_sha"] in html

    def test_the_invariant_results_are_on_it_and_not_evaluated_is_not_a_pass(
            self, html, blob) -> None:
        assert blob["payload"]["invariants"], "this run recorded no invariants"
        assert 'id="invariants"' in html
        assert "NOT_EVALUATED is not a pass" in html

    def test_the_drop_ledger_travels_with_the_grid(self, html, blob) -> None:
        assert 'id="table-ledger"' in html
        # `renderTable` draws it from the payload; the payload has to carry it.
        assert "ledger" in blob["payload"]
        assert blob["payload"]["counts"]["input"] == (
            blob["payload"]["counts"]["rendered"]
            + blob["payload"]["counts"]["dropped"])

    def test_the_citation_strip_covers_every_column(self, blob) -> None:
        p = blob["payload"]
        assert len(p["citations"]) == len(p["rings"])
        assert [c["key"] for c in p["citations"]] == [r["key"] for r in p["rings"]]

    def test_the_reason_the_drawer_is_absent_is_stated_once(self, html) -> None:
        """`UX-v3-rounds.md` B14: one claim at two weights is the defect. The
        grid-level line answers "why is this gene not a link?"; the reason is
        argued once, at the top, where a reader finds out what the file is."""
        assert html.count(export_mod.DRAWER_OFFLINE_REASON) == 1
        assert "ChEMBL" not in export_mod.DRAWER_OFFLINE_NOTE

    def test_nothing_the_export_authors_promises_a_retired_mechanism(
            self, blob) -> None:
        """PLAN §3 deleted ranking. The export authors text too, so it is scanned
        with everything else this build writes."""
        from targetring.figure import RETIRED_CLAIM_WORDS

        ours = json.dumps({
            "what": export_mod.DRAWER_OFFLINE_NOTE + export_mod.DRAWER_OFFLINE_REASON,
            "css": export_mod._EXPORT_CSS,
            "boot": (STATIC / "export_boot.js").read_text(),
            "templates": blob["templates"],
        }).lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in ours, f"the export says {word!r}"

    def test_no_rule_the_export_adds_sets_type_under_the_floor(self) -> None:
        """`UX-v3-rounds.md` B12 is a floor over the whole stylesheet, and the
        export ships a stylesheet. The app's own rules are held by
        `test_presentation.py`; these are the ones this file adds."""
        css = re.sub(r"/\*.*?\*/", "", export_mod._EXPORT_CSS, flags=re.S)
        small = [m.group(0) for m in re.finditer(r"font-size:\s*([\d.]+)px", css)
                 if float(m.group(1)) < 12.0]
        assert not small, (
            f"{len(small)} rule(s) the export adds set type under the 12.0 px "
            f"floor: {small}")


class TestBothSchemaEras:
    """~250 runs on disk, written by every version of this build. An export that
    only handles the newest one is an export for one run."""

    def test_the_retired_ring_run_exports(self) -> None:
        if not (RUNS / RETIRED_RING_RUN).is_dir():
            pytest.skip(f"{RETIRED_RING_RUN} is not on disk")
        b = export_mod.export_blob(RETIRED_RING_RUN)
        keys = [r["key"] for r in b["payload"]["rings"]]
        assert "druggability" in keys, "this run is not the retired-ring era"
        assert "tractable" not in keys

    def test_the_retired_column_is_cited_as_its_own_and_not_its_successors(
            self) -> None:
        if not (RUNS / RETIRED_RING_RUN).is_dir():
            pytest.skip(f"{RETIRED_RING_RUN} is not on disk")
        b = export_mod.export_blob(RETIRED_RING_RUN)
        row = next(c for c in b["payload"]["citations"] if c["key"] == "druggability")
        assert row["linked"], "the retired ring lost its citation in the export"
        assert "retired 4-key" in row["anchor_text"] or "retired" in row["reason"]

    def test_a_run_that_does_not_add_up_gets_no_export(self, tmp_path,
                                                       monkeypatch) -> None:
        """The table's own refusal, carried into the export. Better not to exist
        than to be carried to another machine wrong."""
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        (tmp_path / "nope").mkdir()
        with pytest.raises(table_mod.TableUnavailable):
            export_mod.build_html("nope")

    @pytest.mark.parametrize("rid", _some_runs(3))
    def test_the_file_builds_and_is_one_document(self, rid) -> None:
        h = export_mod.build_html(rid)
        assert h.startswith("<!doctype html>")
        assert h.count("<html") == 1 and h.rstrip().endswith("</html>")
        assert 100_000 < len(h) < 4_000_000, len(h)
