"""The masthead is one object shared by two pages, and nothing in the build
makes that true by construction.

`docs/flow/index.html` deliberately shares no JS and almost no CSS with the app
(rule 2 at the top of that file): it is a standalone page so it can be iframed
without dragging the app's stylesheet, state and refusals along. The cost of
that choice is a second copy of the masthead's geometry, and a second copy is a
thing that drifts — Puck's "make sure the header is maintained on the flow page"
is what drift looks like from the outside.

So the copy is held to the original here. These tests do not check that the
header looks nice; they check that the two files still agree, which is the only
claim the restated CSS makes and the only one a reader cannot verify by eye
across two files.

What is NOT tested: pixels. The two headers are compared at 390 px and 1280 px
by `scripts/` Playwright runs and in `docs/showcase/13-flow-header.png`; a DOM
test that re-measured type would only re-implement the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_CSS = (REPO / "targetring" / "static" / "app.css").read_text()
APP_HTML = (REPO / "targetring" / "static" / "index.html").read_text()
FLOW = (REPO / "docs" / "flow" / "index.html").read_text()
EXPORTER = (REPO / "scripts" / "export_static_demo.py").read_text()


def token(name: str) -> str:
    """The value `app.css` gives a `--tr-*` token, from its light block.

    First match wins and the light `:root` is first in the file, which is what
    is wanted: the flow page is light-only, so it restates the light values.
    """
    m = re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", APP_CSS, re.M)
    assert m, f"{name} is gone from app.css; the flow page restates it"
    return m.group(1).strip()


# Every masthead value the flow page copies, and the app token it copies from.
# A token that moves in `app.css` fails here with both values printed, which is
# the error message this file exists to produce.
MASTHEAD = [
    ("--tr-t-h1", "26px"),
    ("--tr-t-lede", "17px"),
    ("--tr-lh-tight", "1.2"),
    ("--tr-lh-snug", "1.35"),
    ("--tr-tracking-tight", "-0.012em"),
    ("--tr-tracking-caps", ".07em"),
    ("--tr-wash-height", "128px"),
    ("--tr-rule-strong", "#c7cad1"),
    ("--tr-surface", "#ffffff"),
    ("--tr-ink", "#16181d"),
    ("--tr-ink-2", "#3f434b"),
    ("--tr-ink-3", "#5d636e"),
    ("--tr-s-2", "8px"),
    ("--tr-s-3", "12px"),
    ("--tr-s-4", "16px"),
    ("--tr-s-5", "24px"),
    ("--tr-t-small", "12.5px"),
]


class TestTheFlowPageMastheadMatchesTheApp:

    @pytest.mark.parametrize("name,restated", MASTHEAD)
    def test_the_flow_page_masthead_matches_the_app(self, name, restated
                                                    ) -> None:
        """The literal in `docs/flow/` is still the app's token value."""
        assert token(name) == restated, (
            f"app.css {name} is now {token(name)!r} but docs/flow/index.html "
            f"restates it as {restated!r}. Update the masthead block in "
            "docs/flow/index.html and this table together."
        )
        assert restated in FLOW, (
            f"docs/flow/index.html no longer contains {restated!r} for {name}; "
            "the masthead has drifted from app.css."
        )

    def test_both_pages_print_the_same_title(self) -> None:
        h1 = '<h1>targetring <span class="ver">v2</span></h1>'
        assert h1 in APP_HTML, "the app's masthead h1 changed shape"
        assert h1 in FLOW, (
            "docs/flow/index.html no longer prints the app's h1 verbatim; "
            "two titles is two sites"
        )

    def test_the_flow_page_links_the_wash_and_does_not_copy_the_image_path(
            self) -> None:
        """The wash is the one stylesheet shared, by link, never by copy."""
        assert '<link rel="stylesheet" href="../demo/header-wash.css">' in FLOW
        # Comments stripped first: both names are discussed at length in this
        # page's own header comment, and a prose mention is not a reference.
        live = re.sub(r"/\*.*?\*/", "",
                      re.sub(r"<!--.*?-->", "", FLOW, flags=re.S), flags=re.S)
        # If the png were named here it would be a second copy of the path and
        # it would break the day the asset moves. `header-wash.css` owns it.
        assert "header-wash.png" not in live
        assert "app.css" not in live, (
            "docs/flow/index.html now links app.css; rule 2 says the flow page "
            "is standalone — restate the few masthead values instead"
        )


class TestTheNavIsOnBothPagesAndPointsAtRealPages:

    @pytest.mark.parametrize("src,name", [(APP_HTML, "static/index.html"),
                                          (FLOW, "docs/flow/index.html")])
    def test_the_nav_row_is_in_the_masthead(self, src, name) -> None:
        assert 'nav class="sitenav"' in src, f"{name} lost the site nav"
        assert "Tool" in src and "How it works" in src
        assert 'aria-current="page"' in src, (
            f"{name}'s nav does not mark the current page"
        )

    def test_every_nav_link_is_relative(self) -> None:
        """Absolute paths break under the `/TargetRing/` prefix and locally."""
        for src in (APP_HTML, FLOW):
            nav = re.search(r'<nav class="sitenav".*?</nav>', src, re.S)
            assert nav, "the nav block is gone"
            for href in re.findall(r'href="([^"]+)"', nav.group(0)):
                assert not href.startswith(("/", "http")), (
                    f"nav link {href!r} is not relative; it would 404 under "
                    "the /TargetRing/ prefix or from a file:// copy"
                )

    def test_the_flow_page_never_links_the_redirect_stub(self) -> None:
        """`../` from the flow page is `docs/index.html`, a meta-refresh.

        Going through it reloads the tool from scratch and drops whatever run
        the reader had open, so every link to the tool names `../demo/`.
        """
        assert 'href="../"' not in FLOW, (
            'docs/flow/index.html links href="../", which lands on the '
            "redirect stub and loses the run; use ../demo/"
        )

    def test_the_app_hides_the_nav_until_the_exporter_says_otherwise(self
                                                                    ) -> None:
        """The live app has no `../flow/`, so it must not paint a link to one."""
        assert "header nav.sitenav { display: none; }" in APP_CSS
        assert 'body[data-site="pages"] header nav.sitenav' in APP_CSS
        assert '<body class="landing" data-site="pages">' in EXPORTER, (
            "the exporter no longer marks the exported body, so the published "
            "tool page would hide its own nav"
        )
