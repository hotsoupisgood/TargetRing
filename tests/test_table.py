"""The table view — v3.6.

The table is a second rendering of a run the circle was already drawn from, so
the thing these tests exist to prevent is the two renderings disagreeing. A wrong
table is `AGENTS.md:17`'s plausible wrong figure with a header row on it.

Four groups:

* `TestTheTableAndTheCircleCannotDisagree` — the single-key sort IS
  `order_for_figure`, asserted by equality rather than by reading both.
* `TestMissingIsNotLow` — AGENTS.md rule 1, on the sort instead of on the cell.
  A gene with no value on the key is last in both directions and on every key of
  a multi-key sort.
* `TestThePivotPreservesEveryGene` — rule 3. Every gene, every ring, every state,
  and the run refuses to render as a table if any of that is untrue.
* `TestNothingHereRanksAnything` — PLAN §3. Lexicographic and nothing more.

Everything runs against artefacts under `runs/` and synthetic cells. No source is
contacted and no analysis is triggered (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import table as table_mod
from targetring.app import app
from targetring.order import (ALPHABETICAL, RING_PREFIX, SUBMITTED, RING_SIGN,
                              SortKey, missing_on, order_by_keys,
                              order_for_figure, parse_sort_keys, sort_label)
from targetring.states import Cell, failed, no_data, not_applicable, value

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"


def a_real_run() -> str:
    """The newest timestamped run on disk the table can actually load.

    Deliberately not a pinned id: the table's whole claim is that it reads any
    run's artefacts, and a test pinned to one run would stop exercising that the
    day somebody tidied `runs/`. Run ids sort chronologically by construction,
    and the `20`-prefix filter keeps hand-made directories like `smoke1` out of
    "newest" — they are still loadable, they are just not the newest thing.
    """
    for d in sorted((p for p in RUNS.iterdir() if p.name.startswith("20")),
                    reverse=True):
        if (d / "values.csv").exists() and (d / "manifest.json").exists():
            return d.name
    pytest.skip("no run artefacts under runs/")


def a_multi_gene_run(min_genes: int = 2) -> str:
    """The newest run with enough spokes to tell two defects apart.

    On a ONE-gene run, losing a cell and losing a whole ring are the same event:
    the ring has no other gene to leave a hole in. Both are refused, but they are
    refused by different checks with different messages, and a test that wants to
    pin one of them specifically needs a run where they are distinguishable.

    Same rule as `a_real_run` -- newest that qualifies, never a pinned id.
    """
    for d in sorted((p for p in RUNS.iterdir() if p.name.startswith("20")),
                    reverse=True):
        if not ((d / "values.csv").exists() and (d / "manifest.json").exists()):
            continue
        try:
            man = json.loads((d / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if len(man.get("gene_order") or []) >= min_genes:
            return d.name
    pytest.fail(
        f"no run under runs/ draws {min_genes}+ genes, so the rectangularity "
        f"check cannot be exercised as written. This is a FAILURE and not a "
        f"skip: the assertion below would pass vacuously on a one-gene run, and "
        f"a refusal test that passes because it stopped discriminating is worse "
        f"than one that fails.")


@pytest.fixture(scope="module")
def run_id() -> str:
    return a_real_run()


@pytest.fixture(scope="module")
def loaded(run_id: str) -> table_mod.RunTable:
    return table_mod.load(run_id)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# A hand-built ring where the missing genes are NOT at the ends, so a rule that
# sorted them as the lowest value, or dropped them, or left them interleaved,
# all give a different answer from the correct one.
def a_ring(**kw: float | None) -> dict[str, Cell]:
    return {g: (value(v) if v is not None else no_data("nobody measured it"))
            for g, v in kw.items()}


class TestTheTableAndTheCircleCannotDisagree:
    """`order_by_keys` with one key is `order_for_figure`. Not "behaves like" —
    equal, on this run's own cells, for every ring the app offers as an order.

    This is the assertion the whole design is for. If the table sorted in the
    browser there would be nothing to write here.
    """

    def test_one_key_is_exactly_the_spoke_ordering(self, loaded) -> None:
        from targetring.run import FILTERABLE_RINGS

        cbr = loaded.cells_by_ring()
        checked = 0
        for ring in FILTERABLE_RINGS:
            if ring not in cbr:
                continue
            ordering = f"{RING_PREFIX}{ring}"
            assert (order_by_keys(loaded.submitted_order, cbr, [SortKey(ordering)])
                    == order_for_figure(loaded.submitted_order, cbr, ordering)), ring
            checked += 1
        assert checked >= 9, f"only {checked} rings were compared"

    @pytest.mark.parametrize("ordering", [SUBMITTED, ALPHABETICAL])
    def test_the_two_non_ring_orders_agree_too(self, loaded, ordering: str) -> None:
        cbr = loaded.cells_by_ring()
        assert (order_by_keys(loaded.submitted_order, cbr, [SortKey(ordering)])
                == order_for_figure(loaded.submitted_order, cbr, ordering))

    def test_the_table_opens_in_the_order_the_circle_drew(self, client, run_id) -> None:
        """Unsorted is not "some order": it is this run's spoke order, so the two
        renderings agree before anybody touches a column."""
        d = client.get(f"/api/run/{run_id}/values").json()
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        assert d["order"] == man["gene_order"]
        assert [g["gene"] for g in d["genes"]] == man["gene_order"]

    def test_a_sort_the_run_cannot_honour_is_refused_not_swapped(
            self, client, run_id) -> None:
        """The figure's rule (`normalise_ordering`): falling back to one order
        while the screen names another is COUNCIL V5 in a different costume."""
        r = client.get(f"/api/run/{run_id}/values", params={"sort": "ring:nonexistent"})
        assert r.status_code == 422
        assert "no such ring" in r.json()["message"]
        r = client.get(f"/api/run/{run_id}/values", params={"sort": "by_merit"})
        assert r.status_code == 422

    def test_a_column_is_sortable_only_where_the_figure_offers_that_order(
            self, client, run_id) -> None:
        from targetring.run import FILTERABLE_RINGS

        d = client.get(f"/api/run/{run_id}/values").json()
        offered = {o["value"] for o in client.get("/api/orderings").json()["orderings"]}
        for r in d["rings"]:
            assert r["sortable"] == (r["key"] in FILTERABLE_RINGS), r["key"]
            if r["sortable"]:
                assert f"{RING_PREFIX}{r['key']}" in offered

    def test_k_is_shown_and_is_not_sortable(self, client, run_id) -> None:
        """PLAN §2: `completeness` is deliberately not filterable, because
        filtering on k filters on how much anybody has studied a gene. Ordering
        by it is the same bias with a click instead of a slider — so k is a
        visible column and not a sort key."""
        d = client.get(f"/api/run/{run_id}/values").json()
        comp = [r for r in d["rings"] if r["key"] == "completeness"]
        assert comp and comp[0]["sortable"] is False
        assert all(g["k"] <= d["k_max"] for g in d["genes"])
        r = client.get(f"/api/run/{run_id}/values",
                       params={"sort": "ring:completeness"})
        assert r.status_code == 200 or r.status_code == 422
        # It is not offered; if a future build offers it, that is a decision to
        # take deliberately and this test is where it gets argued.
        assert "ring:completeness" not in {
            o["value"] for o in client.get("/api/orderings").json()["orderings"]}


class TestMissingIsNotLow:
    """AGENTS.md rule 1, applied to the sort. `no_data` is not a small number."""

    RING = {"literature": a_ring(A=5.0, B=None, C=1.0, D=None, E=3.0)}
    GENES = ["A", "B", "C", "D", "E"]
    KEY = f"{RING_PREFIX}literature"

    def test_no_data_sorts_last_not_lowest(self) -> None:
        out = order_by_keys(self.GENES, self.RING, [SortKey(self.KEY)])
        assert out == ["A", "E", "C", "B", "D"]
        # The point, stated as the thing that must not happen: C, the genuine
        # smallest, is above the unmeasured genes and not among them.
        assert out.index("C") < out.index("B")
        assert out.index("C") < out.index("D")

    def test_reversing_the_key_does_not_float_the_missing_block_to_the_top(self) -> None:
        """The other door rule 1 can be walked through. "Worst first" must not
        mean "unmeasured first": a gene nobody measured is not the worst one
        either, and a reader who reversed a column would read it as one."""
        out = order_by_keys(self.GENES, self.RING, [SortKey(self.KEY, reverse=True)])
        assert out == ["C", "E", "A", "B", "D"]
        assert out[-2:] == ["B", "D"]

    def test_the_missing_block_keeps_the_submitted_order(self) -> None:
        """PLAN §3.1: "last as a visible block, in submitted order"."""
        out = order_by_keys(["E", "D", "C", "B", "A"], self.RING, [SortKey(self.KEY)])
        assert out[-2:] == ["D", "B"]

    def test_every_non_value_state_is_unplaceable_not_low(self) -> None:
        """All three non-measurements, not just `no_data`. A `failed` call is
        even less of a low value than an absence is."""
        ring = {"safety": {"A": value(-1.0), "B": no_data("x"),
                           "C": not_applicable("y"), "D": failed("boom"),
                           "E": value(1.0)}}
        out = order_by_keys(list("ABCDE"), ring, [SortKey(f"{RING_PREFIX}safety")])
        assert out[:2] == ["E", "A"]
        assert set(out[2:]) == {"B", "C", "D"}

    def test_the_block_is_reported_so_the_table_can_draw_it(self) -> None:
        out = order_by_keys(self.GENES, self.RING, [SortKey(self.KEY)])
        assert missing_on(out, self.RING, self.KEY) == ["B", "D"]
        # `submitted` and `alphabetical` can place everybody, so there is no
        # block to draw and none is claimed.
        assert missing_on(out, self.RING, SUBMITTED) == []
        assert missing_on(out, self.RING, ALPHABETICAL) == []

    def test_missing_is_last_at_every_level_of_a_multi_key_sort(self) -> None:
        """The rule is per key, not just for the primary one. Genes tied on key 1
        and unmeasured on key 2 go to the bottom of their own tie group — they
        are not given key 2's lowest value to settle it with."""
        cells = {
            "tractable": a_ring(A=1.0, B=1.0, C=1.0, D=0.0),
            "literature": a_ring(A=2.0, B=None, C=9.0, D=5.0),
        }
        out = order_by_keys(list("ABCD"), cells,
                            [SortKey(f"{RING_PREFIX}tractable"),
                             SortKey(f"{RING_PREFIX}literature")])
        assert out == ["C", "A", "B", "D"]

    def test_the_api_names_the_block_on_the_rows_and_beside_them(
            self, client, run_id, loaded) -> None:
        """A block nobody can see is not a block. It is marked per row and listed
        once, so the page can draw the divider without recognising a state."""
        cbr = loaded.cells_by_ring()
        ring = next((r["key"] for r in loaded.rings
                     if any(c.state != "value" for c in cbr[r["key"]].values())
                     and r["key"] != "completeness"), None)
        if ring is None:
            pytest.skip("this run has no ring with a missing cell")
        d = client.get(f"/api/run/{run_id}/values",
                       params={"sort": f"{RING_PREFIX}{ring}"}).json()
        flagged = [g["gene"] for g in d["genes"] if g["no_value_on_sort_key"]]
        assert flagged == d["missing_block"]
        assert flagged, f"{ring} has a missing cell but no gene was flagged"
        # And they are at the end, in both directions.
        n = len(flagged)
        assert [g["gene"] for g in d["genes"]][-n:] == flagged
        rev = client.get(f"/api/run/{run_id}/values",
                         params={"sort": f"{RING_PREFIX}{ring}:asc"}).json()
        assert [g["gene"] for g in rev["genes"]][-n:] == rev["missing_block"]
        assert set(rev["missing_block"]) == set(flagged)

    def test_the_sentence_says_it(self) -> None:
        note = sort_label([SortKey(f"{RING_PREFIX}literature")], {})
        assert "never lowest" in note
        assert "never combined" in note


class TestThePivotPreservesEveryGene:
    """AGENTS.md rule 3 and rule 1, on the pivot. `input == rendered + ledgered`
    still holds, every gene has a cell on every ring, and a hole is a refusal
    rather than a blank."""

    def test_every_gene_in_the_run_is_in_the_table(self, loaded, run_id) -> None:
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        assert loaded.genes == man["gene_order"]
        assert len(loaded.genes) == man["rendered_count"]

    def test_the_pivot_is_rectangular(self, loaded) -> None:
        for ring in loaded.rings:
            per = loaded.cells[ring["key"]]
            assert set(per) == set(loaded.genes), ring["key"]

    def test_the_cell_count_is_the_rendered_cell_count(self, loaded, run_id) -> None:
        """Invariant 1's identity, restated for the wide grid: the table has one
        cell per row of the CSV's cell section and not one more."""
        with (RUNS / run_id / "values.csv").open(newline="") as fh:
            rows = [r for r in csv.DictReader(fh)
                    if (r.get("cell_id") or "").startswith("cell|")]
        assert sum(len(p) for p in loaded.cells.values()) == len(rows)
        assert len(rows) == len(loaded.genes) * len(loaded.rings)

    def test_the_identity_holds_and_is_asserted_not_described(
            self, client, run_id) -> None:
        d = client.get(f"/api/run/{run_id}/values").json()
        c = d["counts"]
        assert c["input"] == c["rendered"] + c["dropped"]
        assert c["identity_holds"] is True

    def test_the_drop_ledger_reaches_the_table(self, client, run_id) -> None:
        d = client.get(f"/api/run/{run_id}/values").json()
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        assert d["ledger"] == man["ledger"]

    def test_a_run_whose_counts_do_not_add_up_gets_no_table(self, tmp_path,
                                                            run_id, monkeypatch) -> None:
        """It refuses rather than drawing a grid that has quietly lost a gene."""
        src = RUNS / run_id
        d = tmp_path / "20990101T000000Z-broken"
        d.mkdir()
        (d / "values.csv").write_text((src / "values.csv").read_text())
        man = json.loads((src / "manifest.json").read_text())
        man["input_row_count"] = man["rendered_count"] + man["ledger_count"] + 1
        (d / "manifest.json").write_text(json.dumps(man))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        with pytest.raises(table_mod.TableUnavailable, match="gene accounting is broken"):
            table_mod.load(d.name)

    @staticmethod
    def _run_with_a_dropped_cell(src_id: str, tmp_path, monkeypatch) -> str:
        """A copy of a real run with one cell row deleted and nothing else
        touched — the manifest, the counts and the ledger still agree, which is
        exactly how this would arrive."""
        src = RUNS / src_id
        d = tmp_path / "20990101T000000Z-holed"
        d.mkdir()
        lines = (src / "values.csv").read_text().splitlines(keepends=True)
        del lines[2]
        (d / "values.csv").write_text("".join(lines))
        (d / "manifest.json").write_text((src / "manifest.json").read_text())
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        return d.name

    def test_a_hole_in_the_pivot_is_a_refusal_not_a_blank_cell(
            self, tmp_path, run_id, monkeypatch) -> None:
        """A blank cell is indistinguishable from "nothing to say", which is the
        one rendering of `no_data` rule 1 forbids. So the table does not draw
        one — it declines to draw at all.

        Which of the two refusals fires depends on the run: on a multi-gene run
        the lost cell is a hole in a ring that other genes still populate; on a
        one-gene run it takes the whole ring with it and there is no hole left to
        find. Both are refusals of the same malformed file and this test asserts
        the refusal, not the wording. The two messages are pinned individually by
        the two tests below, each on a run that can actually produce it.
        """
        name = self._run_with_a_dropped_cell(run_id, tmp_path, monkeypatch)
        with pytest.raises(table_mod.TableUnavailable,
                           match="not rectangular|disagree about which rings"):
            table_mod.load(name)

    def test_on_a_multi_gene_run_the_hole_is_named_as_a_non_rectangular_pivot(
            self, tmp_path, monkeypatch) -> None:
        """The original wording, pinned on a run that can produce it.

        `a_multi_gene_run` FAILS rather than skips if `runs/` has no such run:
        this assertion is vacuous on one gene, and silently passing it there is
        how a refusal test stops checking without anyone noticing.
        """
        name = self._run_with_a_dropped_cell(
            a_multi_gene_run(), tmp_path, monkeypatch)
        with pytest.raises(table_mod.TableUnavailable, match="not rectangular"):
            table_mod.load(name)

    def test_a_ring_that_loses_every_cell_is_refused_though_it_leaves_no_hole(
            self, tmp_path, run_id, monkeypatch) -> None:
        """The gap the rectangularity check cannot see, on any run.

        `rings` is built from the rows being validated, so a ring whose cells are
        all gone is not a hole in anything — it is simply absent, and the figure
        draws one ring short and looks complete. That is a plausible wrong figure
        from malformed input, which `AGENTS.md:17` ranks below a crash, so it is
        a refusal. It is caught by cross-checking the ring list against the
        manifest, which the same run wrote and which is not derived from
        values.csv.
        """
        src = RUNS / run_id
        d = tmp_path / "20990101T000000Z-ringless"
        d.mkdir()
        rows = (src / "values.csv").read_text().splitlines(keepends=True)
        header, body = rows[0], rows[1:]
        doomed = body[1].split(",")[4]            # the second row's ring key
        kept = [r for r in body if not r.startswith(f"cell|{doomed}|")]
        assert len(kept) < len(body), "no row was actually dropped"
        (d / "values.csv").write_text(header + "".join(kept))
        (d / "manifest.json").write_text((src / "manifest.json").read_text())
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        with pytest.raises(table_mod.TableUnavailable) as e:
            table_mod.load(d.name)
        # Named, so the reader knows WHICH ring vanished rather than being told
        # the file is bad.
        assert doomed in str(e.value)

    def test_a_run_that_cannot_be_cross_checked_is_refused_not_assumed_intact(
            self, tmp_path, run_id, monkeypatch) -> None:
        """A manifest with neither `ring_sources` nor a readable I1 leaves
        nothing to check the ring list against. The table says so instead of
        drawing on the assumption that it is fine — the same stance
        `ensembl_release_from` takes about a release the source never stated."""
        src = RUNS / run_id
        d = tmp_path / "20990101T000000Z-uncheckable"
        d.mkdir()
        (d / "values.csv").write_text((src / "values.csv").read_text())
        man = json.loads((src / "manifest.json").read_text())
        man.pop("ring_sources", None)
        man["invariants"] = [x for x in man.get("invariants", [])
                             if x.get("id") != "I1"]
        (d / "manifest.json").write_text(json.dumps(man))
        monkeypatch.setattr(table_mod, "RUNS_DIR", tmp_path)
        with pytest.raises(table_mod.TableUnavailable,
                           match="nothing to check values.csv's ring list"):
            table_mod.load(d.name)

    def test_every_run_on_disk_still_passes_the_cross_check(self) -> None:
        """The check is new and runs on every load, so it is held to the ~155
        real runs rather than only to the broken fixtures above. A refusal that
        also refuses good artefacts is not a safety feature."""
        ids = [d.name for d in RUNS.iterdir() if d.name.startswith("20")
               and (d / "values.csv").exists() and (d / "manifest.json").exists()]
        assert len(ids) > 50, "runs/ is too thin for this to mean anything"
        for rid in ids:
            table_mod.load(rid)

    def test_the_four_states_survive_the_round_trip(self, loaded, client,
                                                    run_id) -> None:
        """The table must show WHICH of the four states each cell is, so all four
        have to reach it intact — as states, not as an absence."""
        seen = {c.state for per in loaded.cells.values() for c in per.values()}
        assert "value" in seen
        assert seen <= {"value", "no_data", "not_applicable", "failed"}
        d = client.get(f"/api/run/{run_id}/values").json()
        for g in d["genes"]:
            for key, c in g["cells"].items():
                assert c["state"] in ("value", "no_data", "not_applicable", "failed")
                # Never blank, never zero: a non-measurement carries no number at
                # all, and a measurement always carries one.
                assert (c["value"] is None) == (c["state"] != "value"), (g["gene"], key)
                assert c["detail"] != "" or c["state"] == "value"

    def test_a_value_row_with_no_number_is_rejected_at_the_door(self) -> None:
        with pytest.raises(table_mod.TableUnavailable, match="unreadable number"):
            table_mod._cell_from_row({"cell_id": "cell|x|0|A", "state": "value",
                                      "value": "", "detail": ""})

    def test_a_no_data_row_carrying_a_number_is_rejected_too(self) -> None:
        """The failure that would put an imputed value on a hatched cell."""
        with pytest.raises(table_mod.TableUnavailable, match="Only a measurement"):
            table_mod._cell_from_row({"cell_id": "cell|x|0|A", "state": "no_data",
                                      "value": "0.0", "detail": ""})

    def test_the_ledger_section_of_the_csv_is_not_read_as_cells(self, loaded) -> None:
        """`values.csv` carries four further sections after the cells, each with
        its own header. Read to the end, a drop-ledger row becomes a table row —
        a dropped gene appearing in the grid it was dropped from."""
        led = [e["input"] for e in loaded.manifest.get("ledger", [])
               if e.get("status") not in ("renamed", "possible_mouse")]
        for dropped in led:
            assert dropped not in loaded.genes

    def test_k_is_the_number_stamped_on_the_figure(self, loaded) -> None:
        comp = loaded.cells.get("completeness", {})
        if not comp:
            pytest.skip("this run has no completeness ring")
        for g in loaded.genes:
            assert loaded.k[g] == int(comp[g].value)

    def test_the_cell_colour_is_the_figures_colour(self, loaded, run_id) -> None:
        """The swatch comes off `values.csv`'s `fill` column — the colour the arc
        was painted — so the table is not choosing a palette beside the figure's."""
        with (RUNS / run_id / "values.csv").open(newline="") as fh:
            for row in csv.DictReader(fh):
                if not (row.get("cell_id") or "").startswith("cell|"):
                    break
                assert loaded.cells[row["ring"]][row["gene"]].fill == row["fill"]


class TestNothingHereRanksAnything:
    """PLAN §3, v2.5. Item 3 is a lexicographic multi-key sort and nothing more.

    The mechanism deleted in v2.5 kept reproducing the defect it was prescribed
    to fix (COUNCIL V2, V10). A multi-key sort is the obvious place for it to
    come back as a weighted sum with the weights called "key order", so the
    absence is asserted rather than assumed.
    """

    def test_the_keys_are_applied_in_turn_and_never_combined(self) -> None:
        """A composite would let a big win on key 2 beat a loss on key 1.
        Lexicographic never does: key 1 decides, and key 2 only breaks its ties."""
        cells = {
            "tractable": a_ring(A=1.0, B=0.9),
            "literature": a_ring(A=0.0, B=1000.0),
        }
        out = order_by_keys(["A", "B"], cells,
                            [SortKey(f"{RING_PREFIX}tractable"),
                             SortKey(f"{RING_PREFIX}literature")])
        assert out == ["A", "B"]

    def test_the_response_carries_no_score_column_and_no_composite(
            self, client, run_id) -> None:
        """The fields the TABLE authors — its columns, its sort sentence, its row
        shape. The verbatim manifest text it passes through is excluded on
        purpose and in both directions: a cell `detail` was written by whichever
        build made the run, and invariant 7's own PASS line legitimately reads
        "no headline marker survives the ranking" because asserting the absence
        is its job. What the LIVE build writes is
        `test_no_live_cell_detail_promises_a_retired_mechanism`.
        """
        from targetring.figure import RETIRED_CLAIM_WORDS

        d = client.get(f"/api/run/{run_id}/values",
                       params={"sort": "ring:tractable,ring:logfc"}).json()
        authored = ("rings", "sort", "sort_note", "order", "missing_block",
                    "counts", "k_max", "states", "submitted_order_source",
                    # v3.8. The dim filter authors text too, so it is scanned here
                    # with everything else this endpoint writes.
                    "dim", "dim_counts", "dim_count_line", "dim_count_note")
        ours = json.dumps({k: d[k] for k in authored}).lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in ours, f"the table response says {word!r}"
        # v3.8 adds two per-gene fields and this list is the guard that stops a
        # third arriving quietly. Both are STRINGS: the one shape a dim filter
        # could smuggle a ranking back in is a per-gene "matches N of M filters"
        # number, which is a composite score with a different name, so there is no
        # number here for anything to sort on (`tests/test_dim.py`).
        assert set(d["genes"][0]) == {"gene", "ensembl_id", "k",
                                      "no_value_on_sort_key", "cells",
                                      "dim_state", "dim_reason"}
        assert isinstance(d["genes"][0]["dim_state"], str)
        assert isinstance(d["genes"][0]["dim_reason"], str)
        assert set(d["genes"][0]["cells"]) == {r["key"] for r in d["rings"]}
        # No extra column that means "how good is this gene": the columns are
        # the run's own rings, one each, and nothing else.
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        drawn_rings = [r["ring"] for r in man.get("ring_sources", [])]
        if drawn_rings:
            assert [r["key"] for r in d["rings"]] == drawn_rings
        assert len(d["rings"]) == len({r["key"] for r in d["rings"]})

    def test_no_live_cell_detail_promises_a_retired_mechanism(self) -> None:
        """v3.6. Found by the table, because a cell detail is the one string in
        this build that reaches a reader without being drawn.

        `assert_footer_is_true` reads the rendered SVG, so it cannot see a
        `detail`; the detail goes to `values.csv`, to the drawer, and now to the
        table's cell tooltips. `chemical_probe_cell` was still telling a reader
        that "the tractability ring is the one that carries the Pareto axis" on
        a build where v2.5 deleted the front, the score and the axes.
        """
        from targetring.adapters import opentargets as ot
        from targetring.figure import RETIRED_CLAIM_WORDS

        prio = {"hasLigand": 1.0, "hasPocket": 1.0, "hasSmallMoleculeBinder": 1.0,
                "hasSafetyEvent": -1.0, "geneEssentiality": 0.0,
                "geneticConstraint": -0.93, "isCancerDriverGene": -1.0}
        # Both branches of the sparse key: present, and absent-and-hatched. The
        # offending sentence was on the first one only.
        recs = [
            ot.OTRecord(ensembl_id="ENSG00000157764", found=True,
                        approved_symbol="BRAF",
                        prioritisation={**prio, "hasHighQualityChemicalProbes": 1.0}),
            ot.OTRecord(ensembl_id="ENSG00000169174", found=True,
                        approved_symbol="PCSK9", prioritisation=dict(prio)),
            ot.OTRecord(ensembl_id="ENSG00000999999", found=False),
        ]
        makers = [ot.tractable_cell, ot.chemical_probe_cell, ot.safety_cell,
                  ot.clinical_phase_cell, ot.clinical_activity_cell, ot.rodent_cell]
        checked = 0
        for rec in recs:
            for make in makers:
                low = make(rec).detail.lower()
                checked += 1
                for word in RETIRED_CLAIM_WORDS:
                    assert word not in low, (
                        f"{make.__name__} still says {word!r}: {make(rec).detail}")
        assert checked == len(recs) * len(makers)

    def test_the_page_offers_no_column_that_means_how_good_a_gene_is(self) -> None:
        from targetring.figure import RETIRED_CLAIM_WORDS

        js = (REPO / "targetring" / "static" / "app.js").read_text()
        html = (REPO / "targetring" / "static" / "index.html").read_text()
        # Only the table's own code — the rest of `app.js` legitimately prints
        # the caveats that say these things are gone.
        section = js.split("// ---------- table view (v3.6) ----------", 1)[1]
        section = section.split("function triageHtml", 1)[0]
        for word in RETIRED_CLAIM_WORDS:
            assert word not in section.lower(), f"the table draws {word!r}"
        assert "pane-table" in html

    def test_the_same_key_cannot_be_used_twice(self, loaded) -> None:
        """Not pedantry: a key that has already decided the order deciding it
        again is the shape a reader reads as a weighting."""
        with pytest.raises(ValueError, match="used twice"):
            parse_sort_keys("ring:logfc,ring:logfc:asc", loaded.cells_by_ring())

    def test_every_ring_sign_is_an_assertion_about_desirability_only(self) -> None:
        """`RING_SIGN` is the only orientation left in the build (order.py). A
        multi-key sort must not grow a second one."""
        assert set(RING_SIGN.values()) == {+1}


class TestReadingRunsOffDisk:
    """The endpoint that made the 250 existing runs viewable. Before v3.6 `RUN`
    was only ever set in-page after an analyse, so a run you had already made was
    reachable only as files."""

    def test_the_run_list_is_newest_first_and_names_each_run(self, client) -> None:
        """Newest by mtime, not by name. `runs/` holds hand-named fixtures
        (`smoke1`, `V31-…`) that sort above every timestamped run, so a
        name-ordered picker opened on a four-day-old fixture."""
        runs = client.get("/api/runs", params={"limit": 8}).json()["runs"]
        assert runs
        stamps = [r.get("timestamp_utc", "") for r in runs if r.get("timestamp_utc")]
        assert stamps == sorted(stamps, reverse=True), stamps
        assert all("region" in r or "note" in r for r in runs)
        assert all(table_mod.valid_run_id(r["run_id"]) for r in runs)

    def test_an_old_run_without_submitted_order_says_which_list_it_used(
            self, loaded) -> None:
        """PLAN §3.1's tie-break is "in submitted order", and runs made before
        v3.6 did not record it. The table falls back to the drawn order and
        names the fallback rather than presenting it as the submitted one."""
        assert loaded.submitted_order_source in ("manifest", "drawn")
        assert sorted(loaded.submitted_order) == sorted(loaded.genes)

    def test_the_manifest_records_the_submitted_order_now(self) -> None:
        from targetring import manifest as manifest_mod

        m = manifest_mod.build(
            run_id="x", region="Liver", input_row_count=2, rendered_count=2,
            ledger_count=0, gene_order=["B", "A"], submitted_order=["A", "B"],
            invariants=_empty_report(), contracts=[], ledger=[], llm={}, timings={})
        assert m.as_dict()["submitted_order"] == ["A", "B"]

    def test_a_run_id_that_is_a_path_is_not_a_run_id(self, client) -> None:
        assert not table_mod.valid_run_id("../../etc")
        assert not table_mod.valid_run_id("a/b")
        assert not table_mod.valid_run_id("")
        assert table_mod.valid_run_id("20260928T044236Z-893dec")
        r = client.get("/api/run/..%2F..%2Fetc/values")
        assert r.status_code == 404

    def test_a_run_that_is_not_on_disk_says_so(self, client) -> None:
        r = client.get("/api/run/20990101T000000Z-nope/values")
        assert r.status_code == 404
        assert "no run" in r.json()["message"]

    def test_the_response_says_whether_the_drawer_will_work(self, client,
                                                            run_id) -> None:
        """The drawer needs adapters in memory. Saying so beats a reader clicking
        a gene and getting a 404 they have to interpret."""
        d = client.get(f"/api/run/{run_id}/values").json()
        assert d["in_memory"] is False
        from targetring import app as app_mod

        app_mod._runs[run_id] = object()
        try:
            assert client.get(f"/api/run/{run_id}/values").json()["in_memory"] is True
        finally:
            app_mod._runs.pop(run_id, None)

    def test_the_run_id_is_still_the_only_thing_the_page_needs(self, client,
                                                               run_id) -> None:
        d = client.get(f"/api/run/{run_id}/values").json()
        assert d["run_id"] == run_id
        assert d["ordering_note"]
        assert d["timestamp_utc"]


def _empty_report():
    from targetring.invariants import InvariantReport

    return InvariantReport()
