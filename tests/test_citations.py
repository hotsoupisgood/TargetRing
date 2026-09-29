"""Source-link provenance on the table surface — v3.7.

Four things were asked for and each group below holds one of them:

* `TestADiskLoadedRunGetsItsCitations` — the defect. `/api/run/{id}/scales` reads
  the in-process `_runs` dict, so it 404s for every run on disk, and the decode
  panel it feeds held the only on-screen source links in the app. These tests
  assert the citation strip is complete for a run that is **not** in memory, for
  runs whose manifests predate `ring_sources`, and for a ring this build retired
  — so the defect cannot come back by any of the three routes it could arrive by.
* `TestFailedAndNoDataDoNotResolveAlike` — item 3. The two states that look alike
  on a grid must not send a reader to the same place.
* `TestTheTableAndTheLegendAgreeOnEveryRingName` — item 4.
* `TestTheConstraintsThisWorkWasGivven` — no per-cell links, no new URLs, no
  thirteenth column, no Sources tab, and the source name in the head is a label
  rather than a link.

Everything runs against artefacts under `runs/` and the registry. No source is
contacted and no analysis is triggered (AGENTS.md rules 8 and 9): the only network
check on these URLs is `scripts/check_source_links.py`, which is run by hand.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import citations, sources
from targetring import table as table_mod
from targetring.app import app
from targetring.figure import RING_SPECS

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
STATIC = REPO / "targetring" / "static"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _loadable() -> list[str]:
    return sorted(p.name for p in RUNS.iterdir()
                  if p.is_dir() and table_mod.valid_run_id(p.name)
                  and (p / "values.csv").exists() and (p / "manifest.json").exists())


def _ring_keys(run_id: str) -> list[str]:
    """The rings a run drew, off its own `values.csv`."""
    keys: list[str] = []
    with (RUNS / run_id / "values.csv").open(newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("cell_id") or "").startswith("cell|"):
                break
            if row["ring"] not in keys:
                keys.append(row["ring"])
    return keys


def _a_run_where(predicate) -> str:
    for run_id in _loadable():
        try:
            if predicate(run_id):
                return run_id
        except (OSError, json.JSONDecodeError, csv.Error):
            continue
    pytest.skip("no run under runs/ matches")


def _has_ring_sources(run_id: str) -> bool:
    m = json.loads((RUNS / run_id / "manifest.json").read_text())
    return bool(m.get("ring_sources"))


def _states(run_id: str) -> set[str]:
    with (RUNS / run_id / "values.csv").open(newline="") as fh:
        out = set()
        for row in csv.DictReader(fh):
            if not (row.get("cell_id") or "").startswith("cell|"):
                break
            out.add(row["state"])
        return out


@pytest.fixture(scope="module")
def run_without_ring_sources() -> str:
    """A run whose manifest predates v2.8.

    150 of the 247 loadable runs are like this — `ring_sources` arrived in v2.8
    and most of `runs/` is older. A strip built from the manifest alone would have
    left the majority of the runs this work was commissioned for uncited, which is
    why the registry is the authority and the manifest is only a cross-check.
    """
    return _a_run_where(lambda r: not _has_ring_sources(r))


@pytest.fixture(scope="module")
def run_with_a_failed_cell() -> str:
    return _a_run_where(lambda r: {"failed", "no_data"} <= _states(r))


@pytest.fixture(scope="module")
def run_with_a_retired_ring() -> str:
    return _a_run_where(lambda r: "druggability" in _ring_keys(r))


class TestADiskLoadedRunGetsItsCitations:
    """The 404-scales defect, held shut from three directions."""

    def test_the_strip_is_complete_for_a_run_that_is_not_in_memory(
            self, client) -> None:
        run_id = _loadable()[-1]
        d = client.get(f"/api/run/{run_id}/values").json()
        assert d["in_memory"] is False, "this test is about the disk path"
        assert len(d["citations"]) == len(d["rings"]) >= 10
        # Numbered to match the column numbers, in the same radial order.
        assert [c["n"] for c in d["citations"]] == [r["n"] for r in d["rings"]]
        assert [c["key"] for c in d["citations"]] == [r["key"] for r in d["rings"]]
        # Every row says something. A ring with no link states its reason; a ring
        # with one carries both halves of `name · domain`.
        for c in d["citations"]:
            assert c["label"] and c["reason"], c
            if c["linked"]:
                assert c["url"].startswith("https://") and " · " in c["anchor_text"]
            else:
                assert not c["url"]
        assert d["citation_note"]

    def test_the_scales_endpoint_still_404s_and_the_strip_does_not_care(
            self, client) -> None:
        """The defect itself, pinned. `/scales` reads `_runs`, so it answers 404
        for a run on disk — that is unchanged by this work and is the reason the
        citations had to come from somewhere else. What must never be true again is
        a session in which a run is on screen and its sources are not."""
        run_id = _loadable()[-1]
        assert client.get(f"/api/run/{run_id}/scales").status_code == 404
        d = client.get(f"/api/run/{run_id}/values").json()
        assert [c for c in d["citations"] if c["linked"]], (
            "a disk-loaded run has no source link on screen — this is the defect")

    def test_every_run_on_disk_gets_a_citation_for_every_column(self) -> None:
        """All of them, not a sample. The claim is about `runs/`, so it is asserted
        over `runs/`: 250 directories, each pivoted and each column accounted for.
        """
        specs = {s.key: s for s in RING_SPECS}
        checked = 0
        for run_id in _loadable():
            try:
                t = table_mod.load(run_id)
            except table_mod.TableUnavailable:
                continue      # refused for its own reasons; not a citation question
            rows = citations.citation_rows(
                run_id=run_id, rings=t.rings, cells=t.cells, specs=specs,
                manifest_rows=t.manifest.get("ring_sources") or [])
            assert len(rows) == len(t.rings)
            for r in rows:
                assert r["reason"], f"{run_id}/{r['key']}: no citation and no reason"
                assert r["linked"] or not r["url"]
            checked += 1
        assert checked > 200, f"only {checked} runs were checked"

    def test_a_manifest_without_ring_sources_is_cited_from_the_registry(
            self, client, run_without_ring_sources) -> None:
        m = json.loads((RUNS / run_without_ring_sources / "manifest.json").read_text())
        assert not m.get("ring_sources"), "fixture picked the wrong run"
        d = client.get(f"/api/run/{run_without_ring_sources}/values").json()
        linked = [c for c in d["citations"] if c["linked"]]
        assert linked, "a pre-v2.8 run got no citations"
        for c in linked:
            assert c["url"] == sources.RING_SOURCE_DOCS[c["key"]].url

    def test_a_retired_ring_gets_its_own_citation_and_never_a_borrowed_one(
            self, client, run_with_a_retired_ring) -> None:
        """`druggability` was split in v2.3, and **v3.9 gave it its own entry**.

        This test used to assert the opposite — that ring 3 was left unlinked —
        and the reasoning behind that was sound as far as it went: `tractable`'s
        citation documents three of `druggability`'s four keys, so pointing at it
        would cite a different number. What v3.7 missed is that the choice was
        never "borrow `tractable`'s link or offer none". The four keys are
        documented, across two sections of one page, and that page is now this
        ring's own citation. So the assertions here are the same rule with the
        gap closed: a citation, and provably not the successor's.
        """
        d = client.get(f"/api/run/{run_with_a_retired_ring}/values").json()
        row = next(c for c in d["citations"] if c["key"] == "druggability")
        assert row["linked"] is True and row["url"].startswith("https://")
        for successor in ("tractable", "chemical_probe"):
            assert row["url"] != sources.RING_SOURCE_DOCS[successor].url
            assert row["anchor_text"] != (
                sources.RING_SOURCE_DOCS[successor].legend_text), (
                f"a reader cannot tell this row from {successor}'s")
        # The retirement is still stated, beside the citation rather than instead
        # of it, and the print distinguishes four keys from three.
        assert "v2.3" in row["reason"]
        assert "4-key" in row["anchor_text"] and "Tractability" in row["anchor_text"]
        # The key count is on the strip itself, not only in the manifest's `reads`:
        # the strip is what a reader of an old run is actually shown.
        assert "4-key mean" in row["reason"] and "3-key" in row["reason"]
        # And its column head still says this build has no scale for the column,
        # rather than going blank.
        head = next(r for r in d["rings"] if r["key"] == "druggability")
        assert head["source_name"] == citations.RETIRED_RING_NAME

    def test_the_strip_is_outside_the_horizontal_scroller_and_above_the_ledger(
            self) -> None:
        """Placement, as a structural fact rather than a screenshot. A strip inside
        `.scroll-x` scrolls away with column 12."""
        html = (STATIC / "index.html").read_text()
        grid = html.index('id="table-grid"')
        strip = html.index('id="table-sources"')
        ledger = html.index('id="table-ledger"')
        assert grid < strip < ledger
        # `#table-grid` is the scroller and it closes before the strip opens.
        assert html.index("</div>", grid) < strip

    def test_the_run_query_parameter_opens_the_table_that_carries_the_strip(
            self) -> None:
        """`?run=<id>` is the disk path a reader actually arrives by, and it opens
        the table tab. The strip is drawn by `renderTable`, so it comes with it."""
        js = (STATIC / "app.js").read_text()
        assert 'URLSearchParams(location.search).get("run")' in js
        assert re.search(r"renderTableSources\(d\);", js)
        assert js.index("renderTableSources(d);") < js.index(
            "function renderTableSources")


class TestFailedAndNoDataDoNotResolveAlike:
    """v3.7 item 3. `no_data` is a question for the source; `failed` is a question
    about this run."""

    RID = "20260928T000000Z-test"

    def test_the_two_states_resolve_to_different_targets(self) -> None:
        for key in sources.RING_SOURCE_DOCS:
            nd = citations.cell_provenance("no_data", key, self.RID)
            fl = citations.cell_provenance("failed", key, self.RID)
            assert nd["url"] != fl["url"], key
            assert nd["kind"] == "source_doc" and fl["kind"] == "run_record"

    def test_no_data_resolves_to_the_rings_source_exactly_like_a_value(self) -> None:
        for key, doc in sources.RING_SOURCE_DOCS.items():
            for state in ("value", "no_data", "not_applicable"):
                assert citations.cell_provenance(state, key, self.RID)["url"] == doc.url

    def test_failed_never_resolves_to_any_source_document(self) -> None:
        """The whole of item 3 in one assertion: no `failed` cell on any ring ever
        points at field documentation. A broken call sent to a healthy doc page
        leads the reader to a working page and the false conclusion that the gene
        has no value."""
        every_url = {d.url for d in sources.RING_SOURCE_DOCS.values()}
        for key in list(sources.RING_SOURCE_DOCS) + ["druggability", "logfc"]:
            p = citations.cell_provenance("failed", key, self.RID)
            assert p["url"] not in every_url
            assert p["url"] == f"/api/run/{self.RID}/manifest.json"
            assert "not absence" in p["why"]

    def test_a_failed_cell_makes_its_column_say_so_and_name_the_gene(
            self, client, run_with_a_failed_cell) -> None:
        d = client.get(f"/api/run/{run_with_a_failed_cell}/values").json()
        rows = [c for c in d["citations"] if c["failed_target"]]
        assert rows, "fixture picked a run with no failed cell"
        for r in rows:
            assert r["failed_genes"]
            assert r["failed_target"]["url"].endswith("/manifest.json")
            assert r["failed_target"]["url"] != r["url"]
        # And a column with no failed cell carries no such row: the note is a fact
        # about this run, not a standing caveat on every column.
        assert [c for c in d["citations"] if not c["failed_target"]]

    def test_the_failed_rows_target_is_the_one_the_resolver_returns(
            self, client, run_with_a_failed_cell) -> None:
        """One definition, read twice. The strip cannot say the source link
        answers a failed cell while `cell_provenance` says it does not."""
        d = client.get(f"/api/run/{run_with_a_failed_cell}/values").json()
        for r in d["citations"]:
            if not r["failed_target"]:
                continue
            assert r["failed_target"] == citations.cell_provenance(
                "failed", r["key"], run_with_a_failed_cell,
                r["source_name"])

    def test_the_page_sends_a_failed_hover_to_the_run_and_not_to_the_source(
            self) -> None:
        js = (STATIC / "app.js").read_text()
        cell = js[js.index("function cellHtml"):]
        cell = cell[:cell.index("\n}")]
        assert 'c.state === "failed"' in cell
        assert "NOT answered by this column's source link" in cell
        # And no cell of any state is wrapped in an anchor.
        assert "<a " not in cell and "href" not in cell


class TestTheTableAndTheLegendAgreeOnEveryRingName:
    """v3.7 item 4. Ten of twelve columns were labelled differently in the grid
    than in the scale column — "Region TPM ÷ median" against "Regional
    specificity" — leaving the ring number as the only bridge between two
    surfaces describing the same band."""

    @staticmethod
    def _words(s: str) -> list[str]:
        # Unicode minus and the en/em dashes normalise to nothing: they are
        # punctuation between words, and "−log10" and "-log10" are the same name.
        return [w for w in re.split(r"[^0-9a-z]+", s.lower().replace("−", "-"))
                if w]

    def test_every_column_label_is_the_legends_own_words_in_its_own_order(
            self) -> None:
        """The rule `table_label` is written against, asserted rather than trusted:
        the column head's words are a subsequence of the full label's words and
        start on the same word. So it is the same NAME, cut at word boundaries —
        never a truncation (QA-v2-retest §5) and never a second name for one ring.
        """
        for spec in RING_SPECS:
            full, col = self._words(spec.label), self._words(spec.column_label)
            assert col, spec.key
            assert col[0] == full[0], (
                f"{spec.key}: the grid heads this column {spec.column_label!r} and "
                f"the legend calls the ring {spec.label!r}; they do not even start "
                f"on the same word")
            it = iter(full)
            assert all(w in it for w in col), (
                f"{spec.key}: {spec.column_label!r} is not {spec.label!r}'s own "
                f"words in its own order")

    def test_the_full_label_is_on_the_table_surface_too(self, client) -> None:
        """"Recognisably the same name" is only half of it: the full label has to
        be *somewhere* on the surface. It is in the citation strip's row for the
        column, and in the column's `title`."""
        run_id = _loadable()[-1]
        d = client.get(f"/api/run/{run_id}/values").json()
        by_key = {c["key"]: c for c in d["citations"]}
        for r in d["rings"]:
            assert by_key[r["key"]]["label"] == r["label"]
        js = (STATIC / "app.js").read_text()
        head = js[js.index("let head = `<tr>"):js.index("const blockStart")]
        assert 'title="${esc(r.n + ". " + r.label' in head, (
            "the column head no longer carries the full label in its title")

    def test_the_column_label_is_not_the_wedge_label(self) -> None:
        """Three budgets, three names, and this is the one that says the wedge's is
        not reused: it is written to stand alone at ~44 px inside the 12 o'clock
        gap, which is why it produced "TDL — knowledge" for a ring the legend calls
        "Target development level"."""
        differ = [s.key for s in RING_SPECS if s.wedge_label != s.column_label]
        assert len(differ) >= 8, differ
        js = (STATIC / "app.js").read_text()
        section = js.split("// ---------- table view (v3.6) ----------", 1)[1]
        assert "short_label" not in section, (
            "the table still reads the wedge's name for a ring")

    def test_the_live_build_heads_a_column_with_the_written_short_name(
            self) -> None:
        """`citations.column_label` prints the run's own label where this build has
        renamed the ring since. For a run drawn by THIS build the two agree, so the
        written short name is what a reader gets."""
        for spec in RING_SPECS:
            assert citations.column_label(spec, spec.label) == spec.column_label
            # An older run's label is printed verbatim rather than replaced by
            # today's name, which would put the head and the strip in conflict.
            assert citations.column_label(spec, "Some older label") == "Some older label"
        assert citations.column_label(None, "druggability") == "druggability"


class TestTheConstraintsThisWorkWasGiven:
    """The four hard constraints, as tests rather than as intentions."""

    def test_no_cell_carries_a_link_of_its_own(self, client) -> None:
        """168 cells, 11 distinct source strings: a 15:1 affordance-to-fact ratio,
        and it implies a per-cell upstream record that does not exist. The column
        is the unit of provenance."""
        run_id = _loadable()[-1]
        d = client.get(f"/api/run/{run_id}/values").json()
        for g in d["genes"]:
            for cell in g["cells"].values():
                assert set(cell) == {"state", "value", "detail", "fill", "source"}
                assert "http" not in json.dumps(cell.get("detail") or "")

    def test_nothing_is_built_on_the_csvs_source_column(self) -> None:
        """`values.csv`'s `source` is `spec.source` (`figure.py`) — a ring constant
        denormalised down the column. Nothing about it varies by gene, so a reader
        who saw it per cell would read a per-cell fact that is not there."""
        js = (STATIC / "app.js").read_text()
        section = js.split("// ---------- table view (v3.6) ----------", 1)[1]
        # `\.source\b` is the cell's own field; `source_name` and `short_source`
        # are the ring's, which is a different fact and the one this work uses.
        assert not re.search(r"\.source\b", section), re.findall(
            r".{0,40}\.source\b.{0,20}", section)
        assert "source_name" in section and "citations" in section
        cit = (REPO / "targetring" / "citations.py").read_text()
        assert not re.search(r"\.source\b|\[.source.\]", cit)

    def test_every_url_on_the_strip_is_the_registrys_or_this_runs_own_artefact(
            self, client) -> None:
        """No new URLs. The registry's URLs are fetched by
        `scripts/check_source_links.py` and shape-checked by
        `sources.assert_registry_is_coherent()`; anything outside it escapes both
        guarantees, and a dead link in a legend is worse than no link."""
        registry = {d.url for d in sources.RING_SOURCE_DOCS.values()}
        for run_id in _loadable()[-6:]:
            d = client.get(f"/api/run/{run_id}/values").json()
            found = set(re.findall(r"https?://\S+?(?=[\s\"']|$)",
                                   json.dumps(d["citations"])))
            assert found <= registry, found - registry
            for c in d["citations"]:
                if c["failed_target"]:
                    assert c["failed_target"]["url"] == (
                        f"/api/run/{run_id}/manifest.json")

    def test_the_registry_is_still_the_only_place_a_url_is_written(self) -> None:
        """A second copy of a URL anywhere is what this module exists not to be."""
        cit = (REPO / "targetring" / "citations.py").read_text()
        # The only https:// strings in `citations.py` are in its prose, not in a
        # value it returns.
        for line in cit.splitlines():
            if "https://" in line:
                assert line.lstrip().startswith(("#", "*", '"', "'")) or '"""' in line, line

    def test_there_is_no_sources_tab_and_no_thirteenth_column(
            self, client) -> None:
        html = (STATIC / "index.html").read_text()
        tabs = re.findall(r'class="tab[^"]*" data-tab="([a-z]+)"', html)
        assert tabs == ["figure", "table", "ledger", "invariants", "caveats"]
        assert "sources" not in tabs
        run_id = _loadable()[-1]
        d = client.get(f"/api/run/{run_id}/values").json()
        # Gene, k, and one column per ring. Nothing else.
        assert set(d["genes"][0]["cells"]) == {r["key"] for r in d["rings"]}
        js = (STATIC / "app.js").read_text()
        head = js[js.index("let head = `<tr>"):js.index("const blockStart")]
        assert head.count("<th") == 3, "a column was added to the head"

    def test_the_source_name_in_the_head_is_a_label_and_not_a_link(self) -> None:
        """Measured and refused by the usability review: a hyperlink here puts a
        click target inside a click-to-sort target, and
        `platform-docs.opentargets.org` renders 261 px at 12 px in columns 86-110 px
        wide."""
        js = (STATIC / "app.js").read_text()
        head = js[js.index("let head = `<tr>"):js.index("const blockStart")]
        assert 'class="src"' in head and "r.source_name" in head
        assert "<a " not in head and "href" not in head
        css = (STATIC / "app.css").read_text()
        block = css[css.index(".grid thead th .src"):]
        block = block[:block.index("}")]
        assert "cursor: inherit" in block and "text-decoration: none" in block
        assert "font-weight: 400" in block, "muted is carried by weight, not by size"

    def test_the_source_line_sits_on_the_floor_and_not_under_it(self) -> None:
        """Item 2 asked for ~9 px. `UX-v3-rounds.md` B12 set a 12.0 px floor over
        this stylesheet and refused an exception for a gap of half a pixel; 9 px is
        six times that. Letting this line through would have meant editing B12's
        own assertion, so the line is at the floor and
        `test_no_stylesheet_rule_sets_type_under_the_floor` still reads every rule
        in the file. This test is the record of that, so the next person does not
        read 12 px as a typo for 9 and 'fix' it."""
        css = (STATIC / "app.css").read_text()
        block = css[css.index(".grid thead th .src"):]
        block = block[:block.index("}")]
        px = float(re.search(r"font-size:\s*([0-9.]+)px", block).group(1))
        assert px == 12.0, f"the source line is {px} px; B12's floor is 12.0"

    def test_the_names_the_head_can_print_are_the_ones_that_were_approved(
            self) -> None:
        assert {s.short_source for s in RING_SPECS} == {
            "Open Targets", "GTEx v8", "Pharos", "PubTator3", "yours", "derived"}

    def test_nothing_the_strip_authors_promises_a_retired_mechanism(
            self, client) -> None:
        """The same guarantee `test_the_response_carries_no_score_column_and_no_composite`
        holds over the table's own fields, extended to the strip's. Scoped to the
        strings *this build writes* — a run's own `label` is verbatim artefact text
        and is excluded for the same reason it is there."""
        from targetring.figure import RETIRED_CLAIM_WORDS

        run_id = _loadable()[-1]
        d = client.get(f"/api/run/{run_id}/values").json()
        ours = [d["citation_note"]] + [
            s for c in d["citations"]
            for s in (c["reason"], c["source_name"], c["anchor_text"],
                      c["recorded_drift"],
                      (c["failed_target"] or {}).get("why", ""))]
        low = " ".join(ours).lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in low, f"the citation strip says {word!r}"
        # And the module's own prose, which is the other thing a reader reaches.
        mod = (REPO / "targetring" / "citations.py").read_text().lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in mod, f"citations.py says {word!r}"

    def test_the_link_text_is_name_and_domain_never_a_bare_underline(self) -> None:
        """`SourceDoc.domain` exists because an underline dies in an exported PNG,
        and this strip is screenshotted."""
        for key, doc in sources.RING_SOURCE_DOCS.items():
            # The source name opens the claim and the typeable domain closes it.
            # v3.9 allows a qualifier between them, for a ring whose source name
            # does not identify the measure — `druggability` and `tractable` are
            # both "Open Targets" on one page, and that string alone would print
            # the same citation for a 4-key mean and a 3-key one.
            assert doc.legend_text.startswith(doc.name)
            assert doc.legend_text.endswith(f" · {doc.domain}")
            if doc.name_qualifier:
                assert doc.legend_text == (
                    f"{doc.name} ({doc.name_qualifier}) · {doc.domain}")
            else:
                assert doc.legend_text == f"{doc.name} · {doc.domain}"
            p = citations.cell_provenance("no_data", key, "R")
            assert p["text"] == doc.legend_text
        js = (STATIC / "app.js").read_text()
        fn = js[js.index("function renderTableSources"):]
        fn = fn[:fn.index("\n}")]
        assert "r.anchor_text" in fn
