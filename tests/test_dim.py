"""The per-column dim filter — v3.8.

Puck asked for "a filter, per column, that greys out genes that don't match
instead of removing them". The intent is the honest one: the evidence stays on
screen so a reader can see what the filter excluded and disagree with it. The
three ways this feature can become `AGENTS.md:17`'s plausible wrong output are
what these tests are for:

* `TestDimmingRemovesNothing` — it is view state. The rendered and ledgered counts
  are the same numbers with a dim filter as without, `input == rendered +
  ledgered` still holds, no gene leaves the grid, the order does not move, and
  **nothing lands in the drop ledger** — the ledger is for genes that are
  genuinely not on the figure, and view state in it would break what it means.
* `TestThreeStatesNotTwo` — PLAN §3.2 rule 1, applied to dimming. A gene with no
  value on the filtered column cannot be compared, so it is neither a match nor
  an exclusion. Folding it in with the excluded genes would make dimming
  de-emphasis-by-missingness, which fades out the under-studied genes instead of
  the ones that missed the bound (COUNCIL M3).
* `TestClearingRestoresTheGridExactly` — a cleared filter is the payload of a
  filter that was never set, field for field.

Plus the two hazards named when the work was approved: the encoding must **not**
be grey, because grey already means `no_data` and one grid cannot have grey mean
two incompatible things; and the filter must not grow into a score.

Everything runs against artefacts under `runs/` and synthetic cells. No source is
contacted and no analysis is triggered (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from targetring import de_input
from targetring import dim as dim_mod
from targetring import table as table_mod
from targetring.app import app
from targetring.dim import (DIMMED, MATCH, UNEVALUABLE, DimFilter, DimRejected,
                            classify, count_line, parse_dims)
from targetring.states import failed, no_data, not_applicable, value

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
STATIC = REPO / "targetring" / "static"


def a_real_run() -> str:
    """The newest timestamped run whose artefacts the table can load. Not pinned,
    for the reason `test_table.py` gives: the claim is that this works on any run."""
    for d in sorted((p for p in RUNS.iterdir() if p.name.startswith("20")),
                    reverse=True):
        if (d / "values.csv").exists() and (d / "manifest.json").exists():
            return d.name
    pytest.skip("no run artefacts under runs/")


@pytest.fixture(scope="module")
def run_id() -> str:
    return a_real_run()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def loaded(run_id: str) -> table_mod.RunTable:
    return table_mod.load(run_id)


def a_column_with_both(loaded: table_mod.RunTable) -> tuple[str, float]:
    """A real column of this run that has some values and some non-values, and a
    bound that splits the values. Chosen from the artefact rather than hard-coded:
    the three states have to be reachable on the run the reader actually opens.
    """
    from targetring.run import FILTERABLE_RINGS

    for r in loaded.rings:
        key = r["key"]
        if key not in FILTERABLE_RINGS:
            continue
        per = loaded.cells[key]
        vals = sorted(c.value for g, c in per.items()
                      if c.state == "value" and c.value is not None)
        missing = [g for g, c in per.items() if c.state != "value"]
        if len(vals) >= 2 and missing and vals[0] != vals[-1]:
            return key, vals[len(vals) // 2]
    pytest.skip("no column on this run has both measured and unmeasured genes")


def _js() -> str:
    return (STATIC / "app.js").read_text()


def _css() -> str:
    return (STATIC / "app.css").read_text()


def _css_rules() -> str:
    """The stylesheet with its comments stripped. These rules are argued for at
    length in the file, and the arguments name the very classes the assertions
    look for — so the comments have to go before a selector scan means anything."""
    return re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)


def _dimbar_js() -> str:
    """Just `renderDimBar`. The whole file also contains §3.2's control, so a
    file-wide assertion about this one would be answered by the other."""
    js = _js()
    return js[js.index("function renderDimBar"):js.index("function renderDimCounts")]


# ---------------------------------------------------------------------------
# 1. It is a view state.
# ---------------------------------------------------------------------------

class TestDimmingRemovesNothing:
    """The assertion the whole design is for. A dim filter that moved a count
    would be a §3.2 filter with a different name — and one that ledgered a dimmed
    gene would be worse than that, because the drop ledger is the record of what
    is *not* on the figure and a reader trusts it for exactly that."""

    def test_the_counts_are_the_same_numbers_with_a_dim_filter_as_without(
            self, client, run_id, loaded) -> None:
        key, thr = a_column_with_both(loaded)
        plain = client.get(f"/api/run/{run_id}/values").json()
        dimmed = client.get(f"/api/run/{run_id}/values",
                            params={"dim": f"{key} >= {thr}"}).json()
        assert dimmed["counts"] == plain["counts"]
        assert dimmed["counts"]["identity_holds"] is True
        i, r, dd = (dimmed["counts"]["input"], dimmed["counts"]["rendered"],
                    dimmed["counts"]["dropped"])
        assert i == r + dd, (i, r, dd)
        # and the dim filter actually did something, so this is not a pass by
        # having had no effect
        assert dimmed["dim_counts"]["dimmed"] + dimmed["dim_counts"]["unevaluable"] > 0

    def test_every_rendered_gene_is_still_rendered(self, client, run_id, loaded) -> None:
        key, thr = a_column_with_both(loaded)
        plain = client.get(f"/api/run/{run_id}/values").json()
        dimmed = client.get(f"/api/run/{run_id}/values",
                            params={"dim": f"{key} >= {thr}"}).json()
        assert [g["gene"] for g in dimmed["genes"]] == [g["gene"] for g in plain["genes"]]
        assert dimmed["order"] == plain["order"]
        assert len(dimmed["genes"]) == dimmed["counts"]["rendered"]
        # the three treatments partition the grid and nothing falls out of it
        assert sum(dimmed["dim_counts"].values()) == len(dimmed["genes"])

    def test_no_dimmed_gene_reaches_the_drop_ledger(self, client, run_id, loaded) -> None:
        """The ledger is for genes that are genuinely not on the figure. Putting
        view state in it would break what a reader reads it for.

        The DROP ledger, specifically. `de_input.dropped_entries`' docstring is
        the rule: "The ledger carries two kinds of entry, and conflating them
        breaks the accounting in both directions. `renamed` and
        `possible_mouse` genes are rendered *and* listed, because the user
        needs to see that SEPT9 became SEPTIN9 on a figure that shows SEPTIN9."

        This test used to union every entry's `input` and `resolved_symbol` and
        call the result the drop ledger, so on any run carrying a rename it
        asserted that a gene which is on the figure by design is not on the
        figure. It failed on `MARCHF1` (from `MARCH1 -> MARCHF1`) while dimming
        was working exactly as specified: counts unchanged, `identity_holds`
        true, 15 input = 14 rendered + 1 dropped, and the one real drop (`ALB`,
        ambiguous) never rendered at all.
        """
        key, thr = a_column_with_both(loaded)
        plain = client.get(f"/api/run/{run_id}/values").json()
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        assert d["ledger"] == plain["ledger"]
        # `RENDERED_STATUSES` rather than a literal pair, so this cannot drift
        # from the definition `run.py` and the ledger pane already share.
        dropped = [e for e in d["ledger"]
                   if e.get("status") not in de_input.RENDERED_STATUSES]
        ledgered = {e.get("input") for e in dropped} | {
            e.get("resolved_symbol") for e in dropped}
        for g in d["genes"]:
            if g["dim_state"] != MATCH:
                assert g["gene"] not in ledgered, g["gene"]
        assert not any("dim" in str(e.get("status", "")).lower() for e in d["ledger"])

    def test_a_renamed_gene_is_rendered_and_listed_and_is_not_a_drop(
            self, client, run_id) -> None:
        """The distinction the test above got wrong, asserted directly so it
        cannot be lost again: a `renamed` entry names a gene that IS on the
        figure, and it is not counted among the dropped."""
        d = client.get(f"/api/run/{run_id}/values").json()
        rendered = {g["gene"] for g in d["genes"]}
        renamed = [e for e in d["ledger"] if e.get("status") == "renamed"]
        if not renamed:
            pytest.skip("this run carries no rename")
        for e in renamed:
            assert e["resolved_symbol"] in rendered, e
            assert e["input"] not in rendered, e
        dropped = [e for e in d["ledger"]
                   if e.get("status") not in de_input.RENDERED_STATUSES]
        assert d["counts"]["dropped"] == len(dropped)
        assert d["counts"]["input"] == d["counts"]["rendered"] + len(dropped)

    def test_no_artefact_is_written_or_changed(self, client, run_id, loaded) -> None:
        """Nothing is recomputed, so nothing on disk moves. Checked by mtime and
        size on every artefact of the run, across a dimmed request."""
        key, thr = a_column_with_both(loaded)
        d = RUNS / run_id
        before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                  for p in sorted(d.iterdir()) if p.is_file()}
        client.get(f"/api/run/{run_id}/values", params={"dim": f"{key} >= {thr}"})
        after = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                 for p in sorted(d.iterdir()) if p.is_file()}
        assert before == after

    def test_the_sort_is_untouched_by_dimming(self, client, run_id, loaded) -> None:
        """Dimming is not an ordering. A sorted table dims the same rows in the
        same places, and the row order is the sort's alone."""
        key, thr = a_column_with_both(loaded)
        sort = "alphabetical"
        plain = client.get(f"/api/run/{run_id}/values", params={"sort": sort}).json()
        dimmed = client.get(f"/api/run/{run_id}/values",
                            params={"sort": sort, "dim": f"{key} >= {thr}"}).json()
        assert dimmed["order"] == plain["order"]
        assert dimmed["sort"] == plain["sort"]
        assert dimmed["missing_block"] == plain["missing_block"]

    def test_the_run_level_filters_are_a_different_field_from_the_view_state(
            self, client, run_id, loaded) -> None:
        """PLAN §3.2's filters removed genes from *this run* and are a property of
        it. A dim filter is a property of the view. One key each, never one key
        for both — otherwise a reader could not tell which had happened."""
        key, thr = a_column_with_both(loaded)
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        man = json.loads((RUNS / run_id / "manifest.json").read_text())
        assert d["filters"] == (man.get("filters") or [])
        assert d["dim"] and d["dim"] != d["filters"]

    def test_dimming_does_not_go_near_the_render_path(self) -> None:
        """§3.2 is not quietly rerouted through the new path. `run.py` — which is
        the whole render path — does not import `dim` at all."""
        src = (REPO / "targetring" / "run.py").read_text()
        assert "import dim" not in src and "from .dim" not in src
        fig = (REPO / "targetring" / "figure.py").read_text()
        assert "import dim" not in fig and "from .dim" not in fig
        # ...and `dim.py` does not reach into the ledger or the filter machinery.
        d = (REPO / "targetring" / "dim.py").read_text()
        assert "LedgerEntry" not in d and "FILTERED" not in d
        assert re.search(r"from \.filters import OPS, SYMBOL_TO_OP", d), (
            "the shared primitive is the comparison and nothing else")

    def test_the_guard_fires_if_dimming_ever_starts_removing(self) -> None:
        """`assert_nothing_left` is the tripwire, and it is tested by breaking it
        rather than by reading it."""
        out = dim_mod.DimOutcome(state={"A": MATCH})
        with pytest.raises(AssertionError, match="view state"):
            dim_mod.assert_nothing_left(["A", "B"], out)


# ---------------------------------------------------------------------------
# 2. Three states, not two.
# ---------------------------------------------------------------------------

class TestThreeStatesNotTwo:
    """PLAN §3.2 rule 1, on dimming instead of on removal. A gene with no value on
    the filtered column cannot be compared at all — so it is not an exclusion, and
    it must not be drawn as one."""

    RING = {"literature": {"HIT": value(9.0), "MISS": value(0.5),
                           "NONE": no_data("nobody measured it"),
                           "NA": not_applicable("not applicable here"),
                           "BOOM": failed("the call did not come back")}}
    GENES = ["HIT", "MISS", "NONE", "NA", "BOOM"]

    def test_the_three_states_are_three(self) -> None:
        f = DimFilter("literature", "ge", 1.0)
        out = classify(self.GENES, self.RING, [f])
        assert out.state == {"HIT": MATCH, "MISS": DIMMED, "NONE": UNEVALUABLE,
                             "NA": UNEVALUABLE, "BOOM": UNEVALUABLE}
        # the point, as the thing that must not happen
        assert out.state["NONE"] != out.state["MISS"]

    @pytest.mark.parametrize("gene", ["NONE", "NA", "BOOM"])
    def test_every_non_measurement_is_unevaluable_not_excluded(self, gene) -> None:
        """All three non-measurements, not just `no_data`. A `failed` call is even
        less of an exclusion than an absence is: nobody compared anything."""
        out = classify(self.GENES, self.RING, [DimFilter("literature", "ge", 1.0)])
        assert out.state[gene] == UNEVALUABLE
        assert "cannot be evaluated" in out.reason[gene]
        assert "no value is not a low value" in out.reason[gene]

    def test_a_bound_nothing_can_match_still_does_not_exclude_the_unmeasured(
            self) -> None:
        """The sharpest version: a bound every measured gene misses. If the third
        state were cosmetic, this would dim the whole grid."""
        out = classify(self.GENES, self.RING, [DimFilter("literature", "ge", 1e6)])
        assert out.counts == {MATCH: 0, DIMMED: 2, UNEVALUABLE: 3}

    def test_the_unmeasured_genes_are_not_dimmed_on_screen_either(self) -> None:
        """The state is only half of it — the row has to be drawn differently. Two
        distinct classes, and `dim-out`'s opacity rule must not also select the
        unevaluable rows."""
        js = _js()
        assert 'g.dim_state === "dimmed" ? " dim-out"' in js
        assert 'g.dim_state === "unevaluable" ? " dim-unevaluable"' in js
        css = _css()
        m = re.search(r"\.grid tr\.dim-out td:not\(\.kcell\)\s*\{([^}]*)\}", css)
        assert m and "opacity" in m.group(1), "the fade rule moved or changed name"
        # the fade is on `dim-out` alone. `dim-unevaluable` is full strength: it is
        # unresolved, not de-emphasised.
        for rule in re.finditer(r"([^{}]*dim-unevaluable[^{}]*)\{([^}]*)\}", css):
            assert "opacity" not in rule.group(2), rule.group(0)

    def test_each_state_carries_a_word_as_well_as_a_treatment(self) -> None:
        """The build's own rule for the cell states — pattern AND word — because a
        word is the channel that survives greyscale, a phone screenshot and a
        reader who cannot separate two shades."""
        js = _js()
        assert '"dimmed"' in js and '"cannot be evaluated"' in js
        assert 'class="dimtag"' in js
        assert dim_mod.DIM_STATE_WORD[UNEVALUABLE] == "cannot be evaluated"

    def test_no_data_on_the_filtered_column_is_reported_through_the_endpoint(
            self, client, run_id, loaded) -> None:
        """The same rule on a real run, end to end: every gene the artefact has no
        value for on the dimmed column comes back `unevaluable`, and no such gene
        comes back `dimmed`."""
        key, thr = a_column_with_both(loaded)
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        seen_unevaluable = 0
        for g in d["genes"]:
            cell = g["cells"][key]
            if cell["state"] != "value":
                assert g["dim_state"] == UNEVALUABLE, (g["gene"], cell["state"])
                seen_unevaluable += 1
            elif g["dim_state"] == DIMMED:
                assert cell["value"] < thr
            elif g["dim_state"] == MATCH:
                assert cell["value"] >= thr
        assert seen_unevaluable == d["dim_counts"]["unevaluable"] > 0

    def test_a_measured_gene_is_never_unevaluable(self) -> None:
        """The other direction. If a real number could come back `unevaluable`,
        the third treatment would be a place to hide a comparison that failed."""
        out = classify(self.GENES, self.RING, [DimFilter("literature", "le", 1.0)])
        assert out.state["MISS"] == MATCH and out.state["HIT"] == DIMMED

    def test_completeness_cannot_be_dimmed_on(self, client, run_id) -> None:
        """PLAN §2: `completeness` is deliberately not filterable, because
        filtering on k filters on how much anybody has studied a gene. Doing it
        with opacity instead of with removal does not make it a different act."""
        d = client.get(f"/api/run/{run_id}/values").json()
        comp = [r for r in d["rings"] if r["key"] == "completeness"]
        assert comp and comp[0]["dimmable"] is False
        assert "completeness" not in d["dim_dimmable"]
        r = client.get(f"/api/run/{run_id}/values",
                       params={"dim": "completeness >= 5"})
        assert r.status_code == 422
        assert "completeness" in r.json()["message"]

    def test_the_bound_is_compared_against_the_raw_value(self) -> None:
        """§3.2's rule and it must not diverge: the number in `values.csv`, in the
        column's own units — never a rank, never a scaled colour position. A ring
        whose values are all negative is the case a rank-based comparison passes
        and a raw one does not."""
        ring = {"safety": {"A": value(-0.9), "B": value(-0.1)}}
        out = classify(["A", "B"], ring, [DimFilter("safety", "ge", -0.5)])
        assert out.state == {"A": DIMMED, "B": MATCH}
        # a rank would have made A and B 1st and 2nd and dimmed neither, or one of
        # each whatever the bound was
        out2 = classify(["A", "B"], ring, [DimFilter("safety", "ge", 0.0)])
        assert out2.counts[DIMMED] == 2

    def test_the_comparison_is_the_one_section_3_2_uses(self) -> None:
        """Not "behaves like" — the same operator table. If `>= 3` ever came to
        mean two things in this app, one of the two surfaces would be lying."""
        from targetring.filters import OPS, Filter

        for op in OPS:
            for v, thr in ((3.0, 3.0), (2.999, 3.0), (-1.0, 0.0), (5.0, 3.0)):
                assert (DimFilter("literature", op, thr).matches(v)
                        is Filter("literature", op, thr).keeps(v))


# ---------------------------------------------------------------------------
# 3. Clearing restores the grid exactly.
# ---------------------------------------------------------------------------

class TestClearingRestoresTheGridExactly:

    def test_a_cleared_filter_is_the_payload_of_a_filter_never_set(
            self, client, run_id, loaded) -> None:
        key, thr = a_column_with_both(loaded)
        before = client.get(f"/api/run/{run_id}/values").text
        client.get(f"/api/run/{run_id}/values", params={"dim": f"{key} >= {thr}"})
        after = client.get(f"/api/run/{run_id}/values", params={"dim": ""}).text
        assert after == before
        # and no `dim` parameter at all is the same thing again
        assert client.get(f"/api/run/{run_id}/values").text == before

    def test_with_no_filter_every_gene_is_a_match_and_no_count_line_is_claimed(
            self, client, run_id) -> None:
        """An unfiltered grid must not carry a count line: "14 shown · 0 dimmed"
        over an untouched table tells a reader it was filtered."""
        d = client.get(f"/api/run/{run_id}/values").json()
        assert d["dim"] == []
        assert d["dim_count_line"] == ""
        assert all(g["dim_state"] == MATCH for g in d["genes"])
        assert d["dim_counts"] == {MATCH: len(d["genes"]), DIMMED: 0, UNEVALUABLE: 0}

    def test_the_only_fields_a_dim_filter_changes_are_the_dim_fields(
            self, client, run_id, loaded) -> None:
        """Stated as an allow-list, so a future field that starts moving under a
        dim filter fails here instead of on a reader's screen."""
        key, thr = a_column_with_both(loaded)
        plain = client.get(f"/api/run/{run_id}/values").json()
        dimmed = client.get(f"/api/run/{run_id}/values",
                            params={"dim": f"{key} >= {thr}"}).json()
        assert set(plain) == set(dimmed)
        moved = {k for k in plain if plain[k] != dimmed[k]}
        assert moved == {"dim", "dim_counts", "dim_count_line", "dim_count_note",
                         "genes"}, moved
        # and inside `genes`, only the two dim fields
        for a, b in zip(plain["genes"], dimmed["genes"]):
            assert {k: v for k, v in a.items() if not k.startswith("dim_")} == \
                   {k: v for k, v in b.items() if not k.startswith("dim_")}

    def test_matching_rows_get_no_treatment_at_all(self) -> None:
        """Why clearing restores rather than rebuilds: a match has no class, so
        there is nothing to undo. Nothing in the stylesheet styles a `match`."""
        js = _js()
        assert re.search(r'dim_state === "unevaluable" \? " dim-unevaluable" : ""', js)
        css = _css_rules()
        assert "dim-match" not in css and "tr.dim-" in css

    def test_the_page_clears_the_filters_and_refetches(self) -> None:
        js = _js()
        assert re.search(r'#dim-clear[\s\S]{0,200}TABLE\.dim = \[\]', js)
        # a new run clears them too: a bound is set against one run's raw values
        assert "TABLE = { run_id: null, sort: [], dim: [], data: null }" in js


# ---------------------------------------------------------------------------
# 4. The encoding must not be grey, and it must not become a score.
# ---------------------------------------------------------------------------

class TestTheEncodingIsNotGrey:
    """`no_data` renders hatched grey and `not_applicable` dotted grey, mirroring
    `figure.STATE_STYLE`. If "excluded by your filter" also greyed, grey would mean
    two incompatible things on one grid — *we have no value here* and *we have a
    value and you excluded it* — and on a pasted screenshot nobody could tell
    which. That is the plausible-wrong-output failure in a new place."""

    def test_the_dim_rules_do_not_set_a_grey_fill(self) -> None:
        css = _css_rules()
        for m in re.finditer(r"([^{}]*\bdim-(?:out|unevaluable)\b[^{}]*)\{([^}]*)\}",
                             css):
            body = m.group(2)
            assert "repeating-linear-gradient" not in body, m.group(0)
            assert "radial-gradient" not in body, m.group(0)
            for hexa in re.findall(r"#([0-9a-fA-F]{6})", body):
                r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
                # a neutral is a grey; the rails and the tint are not neutral
                assert max(r, g, b) - min(r, g, b) > 8, (m.group(0), hexa)

    def test_the_dim_treatment_does_not_touch_the_cell_state_classes(self) -> None:
        """Row-level, not cell-level. A hatched cell in a dimmed row still reads
        as hatched, and the four cell states keep meaning exactly what they did."""
        css = _css_rules()
        for m in re.finditer(r"([^{}]*\bdim-(?:out|unevaluable)\b[^{}]*)\{", css):
            assert "st-" not in m.group(1), m.group(0)

    def test_the_three_treatments_use_three_separable_channels(self) -> None:
        """Opacity, hue and a word. Opacity and the amber tint separate in
        luminance, so they survive greyscale; the word survives everything,
        including an exported PNG. The luminances are measured live by
        `scripts/v38_dim_probe.py`; this asserts the channels exist."""
        css = _css_rules()
        assert re.search(r"\.grid tr\.dim-out td:not\(\.kcell\)\s*\{[^}]*opacity", css)
        # An inset shadow rather than `border-left`: this table is
        # `border-collapse: collapse`, so half of a left border on the first cell
        # of a row is drawn outside the table box and clipped away — the probe
        # measured the visible rail at about half its declared width.
        assert re.search(r"\.grid tr\.dim-out \.gene-col\s*\{[^}]*"
                         r"box-shadow:\s*inset 5px 0 0 #16233a", css)
        assert re.search(r"\.grid tr\.dim-unevaluable \.gene-col\s*\{[^}]*"
                         r"box-shadow:\s*inset 5px 0 0 var\(--warn\)", css)
        assert re.search(r"\.grid \.dimtag\s*\{", css)

    def test_the_two_rails_are_separated_in_lightness_not_only_in_hue(self) -> None:
        """The defect the live probe caught on the first pass, kept caught. A slate
        `#4a5568` and the amber `#8a4b00` are obviously different colours whose
        Rec. 601 luminances are 84 and 85 — 5.5 units apart in the measured
        greyscale PNG, which is the encoding failing the exact test it exists to
        pass. Hue alone is not a channel this grid may rely on."""
        css = _css_rules()
        rails = {}
        for name, sel in (("dimmed", r"\.grid tr\.dim-out \.gene-col"),
                          ("unevaluable", r"\.grid tr\.dim-unevaluable \.gene-col")):
            m = re.search(sel + r"\s*\{([^}]*)\}", css)
            assert m, name
            hexes = re.findall(r"#([0-9a-fA-F]{6})", m.group(1))
            if hexes:
                rails[name] = hexes[0]
        # `--warn` is a variable, so its value comes from `:root`
        warn = re.search(r"--warn:\s*#([0-9a-fA-F]{6})", css).group(1)
        rails.setdefault("unevaluable", warn)
        luma = lambda h: (0.299 * int(h[0:2], 16) + 0.587 * int(h[2:4], 16)
                          + 0.114 * int(h[4:6], 16))
        gap = abs(luma(rails["dimmed"]) - luma(rails["unevaluable"]))
        assert gap >= 35, (rails, round(gap, 1))

    def test_the_stylesheet_has_no_stray_text_between_its_rules(self) -> None:
        """Not style policing — a guard on a real failure. An unterminated comment
        block in this file left prose sitting outside a rule, and the CSS parser's
        error recovery silently swallowed the rule after it, so the dimmed rows
        rendered with no rail at all and every text assertion still passed. A
        stylesheet that fails quietly is the presentational form of
        `AGENTS.md:17`."""
        raw = _css()
        assert raw.count("/*") == raw.count("*/"), (
            f"{raw.count('/*')} comment opens, {raw.count('*/')} closes")
        rules = _css_rules()
        stray = [ln for ln in rules.splitlines()
                 if ln.strip() and not re.match(
                     r"^[\s@#.:\[\w>*,+~()%/-]*[{},;]?\s*$", ln)
                 and not any(c in ln for c in "{};:")]
        assert not stray, stray[:5]

    def test_nothing_the_dim_filter_adds_is_under_the_type_floor(self) -> None:
        """`UX-v3-rounds.md` B12: a 12.0 px floor across this stylesheet that has
        already refused an exemption for half a pixel. The global assertion in
        `test_presentation.py` covers the file; this one names the new rules, so a
        failure points at this feature instead of at the sweep."""
        css = _css_rules()
        for block in re.finditer(r"([^{}]*(?:dim|\.dimbar|dimtag)[^{}]*)\{([^}]*)\}",
                                 css):
            for m in re.finditer(r"font-size:\s*([0-9.]+)px", block.group(2)):
                assert float(m.group(1)) >= 12.0, block.group(0)


class TestNothingHereIsAScore:
    """PLAN §3, v2.5: there is no ranking in this project — score, weights, Pareto
    front and the tests that served them were all deleted. A filter is not a
    ranking, but there is one specific way this feature smuggles one in: a
    "matches N of M filters" count, sortable. That is a composite score with a
    different name, so it does not exist."""

    def test_no_per_gene_count_of_matched_filters_is_computed(self, client, run_id,
                                                             loaded) -> None:
        key, thr = a_column_with_both(loaded)
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        for g in d["genes"]:
            assert set(g) == {"gene", "ensembl_id", "k", "no_value_on_sort_key",
                              "dim_state", "dim_reason", "cells"}, set(g)
            assert isinstance(g["dim_state"], str)

    def test_the_outcome_object_has_no_per_gene_number(self) -> None:
        out = classify(["A"], {"literature": {"A": value(1.0)}},
                       [DimFilter("literature", "ge", 0.0)])
        assert set(vars(out)) == {"state", "reason"}
        assert all(isinstance(v, str) for v in out.state.values())

    def test_the_dim_state_is_not_offered_as_a_sort_key(self, client, run_id) -> None:
        """An ordering by dim state is an ordering by a filter verdict, which on
        several filters is the composite by another route."""
        for spec in ("dim", "dim_state", "ring:dim_state", "matches"):
            r = client.get(f"/api/run/{run_id}/values", params={"sort": spec})
            assert r.status_code == 422, spec
        offered = {o["value"] for o in client.get("/api/orderings").json()["orderings"]}
        assert not any("dim" in o for o in offered)

    def test_the_grid_grows_no_column(self, client, run_id, loaded) -> None:
        key, thr = a_column_with_both(loaded)
        plain = client.get(f"/api/run/{run_id}/values").json()
        dimmed = client.get(f"/api/run/{run_id}/values",
                            params={"dim": f"{key} >= {thr}"}).json()
        assert dimmed["rings"] == plain["rings"]
        assert set(dimmed["genes"][0]["cells"]) == set(plain["genes"][0]["cells"])


# ---------------------------------------------------------------------------
# 5. The count line, and keeping the two controls apart.
# ---------------------------------------------------------------------------

class TestTheGridSaysItIsFiltered:
    """A filtered grid that does not say it is filtered is a misleading artefact —
    the same principle as the manifest making a screenshot of the figure
    self-describing (AGENTS.md)."""

    def test_the_count_line_states_all_three_counts_and_the_bound(self, client,
                                                                  run_id, loaded) -> None:
        key, thr = a_column_with_both(loaded)
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        line = d["dim_count_line"]
        c = d["dim_counts"]
        assert f"{c[MATCH]} shown" in line
        assert f"{c[DIMMED]} dimmed" in line
        assert f"{c[UNEVALUABLE]} cannot be evaluated" in line
        assert "is at least" in line and f"{thr:g}" in line
        # and it says the grid is whole, which is the thing a reader must not get
        # wrong about a dim filter
        assert f"All {len(d['genes'])} genes are still on the grid" in line
        assert "nothing was removed" in line and "drop ledger" in line

    def test_the_count_line_names_the_column_the_way_the_control_does(
            self, client, run_id, loaded) -> None:
        """The sentence names the column in the words the dim CONTROL uses, which
        is the column head's name on every ring whose head states the units of
        its own raw values — eleven of twelve. Column 2 is the exception and the
        reason this reads `dim_label`: its head prints the adjusted p while the
        bound in this sentence is compared against the −log10 in `values.csv`, so
        naming it "adjusted p ... is at least 10" would state the wrong units for
        the number the reader typed (`citations.bound_label`).
        """
        key, thr = a_column_with_both(loaded)
        d = client.get(f"/api/run/{run_id}/values",
                       params={"dim": f"{key} >= {thr}"}).json()
        ring = [r for r in d["rings"] if r["key"] == key][0]
        assert ring["dim_label"] in d["dim_count_line"]
        # And the two names are the same name wherever the head is in the raw
        # units, so this is not a licence for a second name per column.
        if key not in ("neg_log10_padj",):
            assert ring["dim_label"] == ring["column_label"]
            assert ring["column_label"] in d["dim_count_line"]

    def test_the_control_and_the_count_line_use_one_name_per_column(
            self, client, run_id) -> None:
        """`dim_label` is on the payload and the page reads it — the dropdown and
        the server's sentence cannot name one column two ways."""
        d = client.get(f"/api/run/{run_id}/values").json()
        assert all("dim_label" in r for r in d["rings"])
        js = _js()
        bar = js[js.index("function renderDimBar"):]
        bar = bar[:bar.index("\n}")]
        assert "r.dim_label" in bar, "the dim control reads the column head again"
        assert "esc(r.column_label)" not in bar

    def test_the_line_is_the_servers_sentence_not_the_pages(self) -> None:
        """One place authors the numbers and the words about them, so they cannot
        disagree. The page prints `dim_count_line`; it does not compose one."""
        js = _js()
        # The whole sentence as the block's `title` — one copyable string — and its
        # counts-free half as the visible line, because the counts are already the
        # headline above it and stating them twice is `UX-v3-rounds.md` B14.
        assert "esc(d.dim_count_line)" in js
        assert "esc(d.dim_count_note)" in js
        assert "#table-dim-counts" in js
        # The full sentence appears once, and only as a `title`: rendered as text
        # it would state the three counts a second time, four lines under the
        # headline that already states them.
        block = js[js.index("function renderDimCounts"):]
        block = block[:block.index("\n}")]
        assert block.count("esc(d.dim_count_line)") == 1
        assert re.search(r'title="\$\{esc\(d\.dim_count_line\)\}"', block)
        code = re.sub(r"^\s*//.*$", "", block, flags=re.M)  # the comments argue for it
        assert "shown ·" in code and code.count("shown ·") == 1

    def test_the_count_line_is_above_the_grid_and_not_collapsed(self) -> None:
        html = (STATIC / "index.html").read_text()
        i_counts = html.index('id="table-dim-counts"')
        i_grid = html.index('id="table-grid"')
        assert i_counts < i_grid
        assert "<details" not in html[i_counts:i_grid]

    def test_two_filters_are_reported_as_two_and_not_as_a_tally(self) -> None:
        f1 = DimFilter("literature", "ge", 2.0)
        f2 = DimFilter("safety", "ge", 0.0)
        ring = {"literature": {"A": value(5.0), "B": value(1.0), "C": no_data("x")},
                "safety": {"A": value(0.5), "B": value(0.5), "C": value(0.5)}}
        out = classify(["A", "B", "C"], ring, [f1, f2])
        line = count_line(out, [f1, f2])
        assert "and" in line
        assert "literature is at least 2" in line and "safety is at least 0" in line
        assert not re.search(r"\d+ of \d+", line), line
        # precedence: a real measurement that misses is the stronger fact
        assert out.state == {"A": MATCH, "B": DIMMED, "C": UNEVALUABLE}


class TestItIsNotTheThresholdFilterControl:
    """Puck was offered the choice of unifying the two and did not ask for it.
    §3.2 removes and ledgers; this only dims. Two mechanisms that look alike but do
    different things is its own hazard, so the difference is visible."""

    def test_the_two_controls_are_in_different_places(self) -> None:
        html = (STATIC / "index.html").read_text()
        # §3.2's is in the analyse form; this one is in the table pane
        assert html.index('id="filters"') < html.index('id="pane-table"')
        assert html.index('id="table-dim"') > html.index('id="pane-table"')

    def test_the_new_control_is_not_shaped_like_the_old_one(self) -> None:
        """§3.2's is a free-text textarea of `ring >= n` lines with a
        `drop_no_data` checkbox. This is dropdowns and a number, and it has no
        such checkbox — there is nothing to drop."""
        html = (STATIC / "index.html").read_text()
        js = _js()
        assert "<textarea id=\"filters\"" in html
        assert "textarea" not in js[js.index("function renderDimBar"):
                                    js.index("function renderDimCounts")]
        assert 'id="dim-col"' in js and 'id="dim-op"' in js and 'id="dim-thr"' in js
        # §3.2's own control still has its `drop_no_data` toggle, on the form. This
        # one has none: rule 1's opt-out authorises a removal, and nothing here is
        # removed.
        assert "drop_no_data" not in _dimbar_js()
        assert "drop-no-data" not in _dimbar_js()
        # and there is no such field to set, either
        import dataclasses
        assert [f.name for f in dataclasses.fields(DimFilter)] == [
            "ring", "op", "threshold"]

    def test_the_wording_is_words_not_the_threshold_symbols(self) -> None:
        assert dim_mod.OP_PHRASE == {"ge": "is at least", "le": "is at most"}
        js = _js()
        bar = _dimbar_js()
        assert "is at least" in bar and "is at most" in bar
        # Nothing a reader SEES says "threshold" or `>=`: that vocabulary belongs to
        # §3.2's control, which removes genes. `threshold` survives as a field name
        # on the request, which nobody reads off the screen.
        shown = re.findall(r">([^<>{}`$]{3,})<", bar)
        assert shown, bar[:200]
        for text in shown:
            assert "threshold" not in text.lower() or "threshold filters" in text.lower()
        assert ">=" not in " ".join(shown)

    def test_the_control_says_what_it_does_not_do(self) -> None:
        """v4.3, review of 2026-10-20 item 4. The 125-word paragraph this used
        to read became one line -- "Dims rows only; nothing is filtered or
        recomputed. No value is not a low value." -- and the rest of it is the
        `DIM_TITLE` hover, verbatim. Nothing is lost, so this asserts both
        halves: the ONE claim a reader can get wrong is on screen, and the rest
        is one hover away."""
        bar = _dimbar_js()
        for phrase in ("Dims rows only", "nothing is\n       filtered or recomputed",
                       "No value is not a low value", "DIM_TITLE"):
            assert phrase in bar, phrase
        js = (STATIC / "app.js").read_text()
        title = js[js.index("const DIM_TITLE ="):js.index("const PADJ_RING")]
        for phrase in ("drop ledger", "threshold filters", "values.csv",
                       "neither a match nor an exclusion"):
            assert phrase in title, phrase

    def test_the_dim_filter_refuses_the_drop_no_data_toggle(self) -> None:
        """§3.2's rule-1 opt-out has no meaning here — nothing is being removed,
        so there is nothing to authorise. Accepting the token would make this
        control read as §3.2's."""
        with pytest.raises(DimRejected):
            parse_dims("literature >= 2 drop_no_data", ["literature"])
        assert "drop_no_data" not in str(vars(DimFilter("literature", "ge", 1.0)))

    def test_a_dim_filter_on_a_column_this_run_does_not_have_is_refused(
            self, client, run_id) -> None:
        """Refused, not approximated: a bound printed as active over a grid it did
        not touch is the same defect as a figure naming an order it did not use."""
        r = client.get(f"/api/run/{run_id}/values", params={"dim": "nonexistent >= 1"})
        assert r.status_code == 422
        assert r.json()["error"] == "dim_rejected"
        r = client.get(f"/api/run/{run_id}/values", params={"dim": "literature >= abc"})
        assert r.status_code == 422

    def test_a_refused_dim_filter_leaves_the_grid_on_screen(self) -> None:
        js = _js()
        assert 'if (d.error === "dim_rejected") TABLE.dim = [];' in js

    def test_two_bounds_of_the_same_kind_on_one_column_are_refused(self) -> None:
        with pytest.raises(DimRejected, match="count line"):
            parse_dims("literature >= 2\nliterature >= 3", ["literature"])
        # ...but a `>=` and a `<=` on one column is a range, and is allowed
        assert len(parse_dims("literature >= 2\nliterature <= 9", ["literature"])) == 2

    def test_the_comparison_is_evaluated_on_the_server(self) -> None:
        """Same reason the sort is: a threshold evaluated in the browser would be
        a second definition of `regional_specificity >= 5` and would drift from
        §3.2's."""
        bar = _dimbar_js()
        assert not re.search(r"cells\[[^\]]*\]\.value\s*[<>]", bar)
        assert "tableDimSpec" in _js() and "&dim=" in _js()
