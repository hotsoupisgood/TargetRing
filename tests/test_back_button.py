"""Both pages were a single history entry until 2026-10-19.

Six tabs and a full-height drawer under one URL meant the browser's Back button
did the only thing it could and left the site mid-read — on the published build
onto `docs/index.html`, the meta-refresh stub, which reloads the tool from
scratch and drops whatever run was open. On a phone, where Back is the system
gesture, that is the most common way out of a gene drawer.

These are text assertions, not behaviour: the behaviour is covered by the
Playwright pass recorded in `docs/reviews/UI-AUDIT-2026-10-19.md`. What is held
here is the one invariant that is easy to break by editing and hard to spot by
reading — that the PAINTERS never push. A painter that pushes answers
`popstate` with a new entry, which is a Back button that goes forward, and it
looks completely reasonable at the call site.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_JS = (REPO / "targetring" / "static" / "app.js").read_text()
FLOW = (REPO / "docs" / "flow" / "index.html").read_text()


class TestTheBackButtonExistsOnBothPages:
    def test_the_tool_has_a_hash_router(self) -> None:
        for fn in ("function showTab(", "function goTab(", "function goGene(",
                   "function applyHash(", "function startHistory("):
            assert fn in APP_JS, f"app.js lost {fn}"
        assert 'addEventListener("popstate", applyHash)' in APP_JS

    def test_only_the_user_routes_push(self) -> None:
        """`pushState` may appear in `goTab` and `goGene`, and nowhere else."""
        n = APP_JS.count("history.pushState")
        assert n == 2, (
            f"app.js has {n} pushState calls, expected 2 (goTab, goGene). A "
            "painter that pushes breaks Back; a third route needs its own "
            "entry in this test and a reason in the comment above goTab."
        )
        assert APP_JS.count("history.replaceState") == 1, (
            "the opening paint must replace, not push, or one Back from a "
            "freshly opened tool lands on the tool again"
        )

    def test_the_tabs_go_rather_than_show(self) -> None:
        """The six tab buttons are a user route, so they push."""
        assert "t.onclick = () => goTab(t.dataset.tab);" in APP_JS

    def test_closing_the_drawer_is_going_back(self) -> None:
        """Which is also what restores the reader's scroll position."""
        assert "function closeDrawerPaint()" in APP_JS
        assert "if (currentView().gene) history.back();" in APP_JS

    def test_the_flow_page_addresses_its_boxes(self) -> None:
        for bit in ('"#box-"', "history.pushState", "history.replaceState",
                    'addEventListener("popstate"', 'addEventListener("hashchange"'):
            assert bit in FLOW, f"docs/flow/index.html lost {bit}"
        assert FLOW.count("history.pushState") == 1, (
            "only `go()` may push on the flow page"
        )

    def test_the_flow_page_does_not_scroll_itself_on_load(self) -> None:
        """It stacks below the chart at <=880 px, so an unconditional
        `scrollIntoView` on the opening paint put the masthead off screen."""
        assert "select(first, false);" in FLOW, (
            "the flow page's opening paint must pass scroll=false"
        )
        assert "if (!scroll) return;" in FLOW
