"""v4.0 — the published demo is the TOOL, on one worked example.

Puck, 2026-10-19: "i want the target ring to be interactive too also make it
the same as the final site will be so just have one example" and "its supposed
to be a tool, explanations will be on my blog post".

Three claims are under test here, and each of them is a thing a reader of the
published page would notice if it broke:

1. **The server names one run to open in**, and it is on the gate's own
   allowlist. A page that opened a run the gate refuses would land on a 404.
2. **A run read off disk can draw its decode panel.** `/api/run/{id}/scales`
   used to 404 for every run not in this process's memory, and the cost of that
   404 was two bordered boxes with nothing in them beside the figure — which is
   what Puck's phone screenshot of 2026-10-19 is a picture of.
3. **The page is pinned light and the controls stay.** The figure is drawn on
   white and no stylesheet recolours it, so OS-dark put a white slab in a dark
   frame; and the analyse controls are what the tool IS, so they stay visible
   and answer with the server's own refusal rather than being hidden.

No network: every route asserted here reads files.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from targetring import config
from targetring.app import app

from test_table import a_real_run

STATIC = config.REPO_ROOT / "targetring" / "static"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def demo_one(monkeypatch):
    """Demo on, one allowed run, and that run is the one the page opens in."""
    run_id = a_real_run()
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_RUNS", (run_id,))
    monkeypatch.setattr(config, "DEMO_FEATURED_RUN", run_id)
    return run_id


class TestTheServerNamesTheRunToOpenIn:
    def test_health_carries_the_featured_run(self, demo_one, client) -> None:
        h = client.get("/api/health").json()
        assert h["demo"] is True
        assert h["featured_run"] == demo_one

    def test_the_featured_run_is_on_the_gate_allowlist(self, demo_one, client) -> None:
        """The landing and the gate are two different names (`DEMO_FEATURED_RUN`
        and `DEMO_RUNS`) and this is the one relationship between them that has
        to hold: a page that opens a run the middleware refuses lands on a
        404 with no figure, which is worse than a card index."""
        h = client.get("/api/health").json()
        assert h["featured_run"] in h["demo_runs"]
        assert client.get(f"/api/run/{h['featured_run']}/manifest.json").status_code == 200

    def test_normal_mode_names_no_featured_run(self, client, monkeypatch) -> None:
        """Puck's own machine is not a one-example demo: the landing stays the
        disease search, so nothing here may add `featured_run` to it."""
        monkeypatch.setattr(config, "DEMO_MODE", False)
        assert "featured_run" not in client.get("/api/health").json()

    def test_the_default_featured_run_is_the_flagship(self) -> None:
        """`docs/DEMO-RUN-ALLOWLIST.md` calls `...94ef5f` "The flagship run" --
        the one whose region was inferred by the cached chooser answer and
        stamped on the figure. That is the example the site opens in."""
        assert config.DEMO_FEATURED_RUN == "20261018T190526Z-94ef5f"


class TestTheDecodePanelOfARunOnDisk:
    """`/api/run/{id}/scales` for a run this process never rendered."""

    def test_it_answers_from_values_csv(self, client) -> None:
        r = client.get(f"/api/run/{a_real_run()}/scales")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["in_memory"] is False
        assert b["rings"], "no ring scales: the panel beside the figure is empty"
        assert b["states"], "no cell states: the white box in Puck's screenshot"

    def test_a_scale_names_its_ring_and_its_source(self, client) -> None:
        """Enough of a row to draw: without a label and a colour ramp the panel
        is a list of empty bars, which is the finding restated rather than
        fixed."""
        rings = client.get(f"/api/run/{a_real_run()}/scales").json()["rings"]
        for ring in rings:
            assert ring["label"] and ring["colours"], ring
            assert ring["source_text"], ring

    def test_the_caveat_clauses_are_empty_rather_than_re_derived(
            self, client) -> None:
        """They are what a particular render said about its own geometry. For a
        figure drawn last week this process can only guess, and a guess about
        spoke angles is COUNCIL V5 — so the list is empty and the page hides the
        strip. Every one of those clauses is in the run's own `figure.png`
        footer."""
        assert client.get(f"/api/run/{a_real_run()}/scales").json()["figure_caveats"] == []

    def test_an_unknown_run_is_still_a_404(self, client) -> None:
        assert client.get("/api/run/20200101T000000Z-nope00/scales").status_code == 404

    def test_a_demo_box_will_not_serve_a_run_off_its_list(
            self, demo_one, client) -> None:
        """The disk fallback must not be a way around the allowlist: it reads
        `runs/`, where ~250 runs sit, and the gate is the only thing standing
        between those and a published URL."""
        other = [p.name for p in config.RUNS_DIR.iterdir()
                 if p.is_dir() and p.name != demo_one]
        assert other, "no second run on disk to test the gate with"
        r = client.get(f"/api/run/{other[0]}/scales")
        assert r.status_code == 404 and r.json()["error"] == "unknown_run"


class TestTheSpendIsDated:
    """Puck: "just date it because the costs all change"."""

    def test_the_manifest_block_carries_the_date_it_was_folded(self) -> None:
        from targetring import spend
        b = spend.manifest_block()
        when = b["accounts_measured_utc"]
        assert when.startswith("20") and when.endswith("+00:00"), when

    def test_the_page_prints_it_in_the_heading(self) -> None:
        """In the heading beside the word "spent", not in the small print: a
        reader who reads one line of that box reads the heading, and an undated
        dollar figure is the thing being fixed."""
        js = (STATIC / "app.js").read_text()
        i = js.index("function llmAccountsHtml")
        block = js[i:i + 3000]
        assert "accounts_measured_utc" in block
        assert "measured ${esc(when)}" in block

    def test_the_dollars_are_still_there(self) -> None:
        """Dated, not deleted: "Spend accounts/dollars stay visible"."""
        js = (STATIC / "app.js").read_text()
        assert "Number(a.spent).toFixed(2)" in js


class TestThePageIsLight:
    def test_the_document_pins_the_light_theme(self) -> None:
        html = (STATIC / "index.html").read_text()
        assert '<html lang="en" data-theme="light">' in html

    def test_the_dark_block_is_scoped_so_that_attribute_defeats_it(self) -> None:
        """Pinned by the mechanism `app.css` already had rather than by deleting
        the dark tokens. If that scope is ever loosened to a bare `:root`, the
        attribute above stops working and the page goes dark on a dark figure
        with nothing failing — so the scope is the thing asserted."""
        css = (STATIC / "app.css").read_text()
        i = css.index("@media (prefers-color-scheme: dark)")
        assert ':root:where(:not([data-theme="light"]))' in css[i:i + 200]


class TestThePageIsTheTool:
    def test_tool_mode_hides_the_card_index_and_keeps_the_controls(self) -> None:
        """The inverse of `body.demo`, deliberately: the controls ARE the tool,
        and their refusal is a sentence the server writes."""
        css = (STATIC / "app.css").read_text()
        assert "body.tool #demo-panel { display: none !important; }" in css
        assert ("body.tool #find-panel, body.tool #input-panel "
                "{ display: block !important; }") in css

    def test_the_circle_is_inlined_as_svg_not_shown_as_a_png(self) -> None:
        """The whole of the interactivity. The cells in `figure-web.svg` carry
        `id="cell|<ring>|<i>|<gene>"`; an `<img>` of a PNG carries nothing to
        click."""
        js = (STATIC / "app.js").read_text()
        i = js.index("async function showToolFigure")
        block = js[i:i + 1600]
        assert "figure-web.svg" in block
        assert '$("#figure").innerHTML = svg' in block
        assert "wireFigure(" in block

    def test_the_spoke_label_pattern_allows_the_chromosome_suffix(self) -> None:
        """v4.0 put ` · 16` between the symbol and the `(k=` that anchors the
        match. A pattern that did not know that made every labelled spoke on
        every figure unclickable."""
        import re as _re
        js = (STATIC / "app.js").read_text()
        src = _re.search(r"const SPOKE_LABEL_RE = (/.*/);", js).group(1)
        pat = _re.compile(src[1:-1])
        assert pat.match("NOD2 · 16 (k=9/9)").group(1) == "NOD2"
        assert pat.match("MUC1 (k=9/9)").group(1) == "MUC1"
        assert pat.match("⚠ FCGR3A · X (k=5/9)").group(1) == "FCGR3A"

    def test_an_empty_decode_box_is_not_drawn(self) -> None:
        """The two bordered boxes with nothing in them, from the screenshot."""
        js = (STATIC / "app.js").read_text()
        assert "function hideEmptyDecodeBoxes" in js
        css = (STATIC / "app.css").read_text()
        assert "#cell-states[hidden]" in css

    def test_the_controls_are_filled_from_the_manifest_not_from_a_literal(
            self) -> None:
        """"Disease field pre-filled with Crohn's disease" — and the word
        "Crohn" appears nowhere in the page that fills it in. A literal would
        stop being true the first time `DEMO_FEATURED_RUN` names another
        disease, which is a plausible wrong figure made out of HTML."""
        import re as _re
        js = (STATIC / "app.js").read_text()
        i = js.index("async function showToolInputs")
        block = js[i:i + 2600]
        assert "manifest.json" in block
        # Comments stripped first. The comment in that function names Crohn's as
        # the worked example it is documenting, and a test that could not tell a
        # worked example from a hard-coded value would be testing prose.
        code = "\n".join(_re.sub(r"//.*", "", l) for l in block.splitlines())
        assert "Crohn" not in code

    def test_the_paid_calls_tab_replaces_the_prose_in_tool_mode(self) -> None:
        """"No 'What this is not' prose; the page is the tool" — and the spend
        table, which is a measurement rather than an explanation, stays."""
        js = (STATIC / "app.js").read_text()
        i = js.index("function renderCaveats")
        block = js[i:i + 900]
        assert 'classList.contains("tool")' in block
        assert "llmAccountsHtml(b.llm)" in block
        assert 't.textContent = "Paid calls"' in js

    def test_the_local_app_keeps_its_own_caveats_pane(self) -> None:
        """Only the published tool drops it. The prose is this build's statement
        of what it does not claim (AGENTS.md, "What is not claimed"), and the
        figure's own footer names the tab it is in."""
        js = (STATIC / "app.js").read_text()
        assert "This figure is internally consistent and auditable" in js
        html = (STATIC / "index.html").read_text()
        assert 'data-tab="caveats"' in html


class TestTheDrawerStillOpensWithoutARunInMemory:
    def test_the_run_id_falls_back_to_the_featured_run(self) -> None:
        """The circle is clickable as soon as its SVG is inlined, and
        `loadTable` — which sets `TABLE.run_id` — is a separate request in
        flight beside it. A click in that window used to ask
        `/api/run/undefined/drawer/MUC1`."""
        js = (STATIC / "app.js").read_text()
        assert "(TABLE.run_id || TOOL_RUN)" in js

    def test_a_baked_drawer_is_a_file_read_and_is_allowed(
            self, demo_one, client) -> None:
        genes = client.get(f"/api/run/{demo_one}/values").json().get("baked_drawers")
        if not genes:
            pytest.skip(f"{demo_one} has no baked drawers")
        d = client.get(f"/api/run/{demo_one}/drawer/{genes[0]}")
        assert d.status_code == 200
        assert json.loads(d.text)["gene"] == genes[0]
