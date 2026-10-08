"""The masthead watercolour — the accent that must not be able to go missing
quietly, or to stop being reproducible.

It carries no data and states nothing, so it gets a small file. What it does get
is the three claims `header-wash.css` and `docs/design/header-wash.md` make to a
reader, each as a test, because an accent whose documentation is wrong is worse
than no accent:

* it is **reproducible** from a stated seed, and the seed in the script, in the
  PNG metadata and in the doc are the same number;
* it is **served** — the stylesheet shipped once with no route behind it and
  404ed silently, which looks exactly like a correctly absent accent;
* it **never names an image from `app.css`**, which is
  `test_export.py::test_the_stylesheet_is_inlined_and_imports_nothing`'s rule and
  the whole reason the wash has its own stylesheet.

Regenerating both images takes about two seconds, so `--check`'s pixel
comparison is done here rather than left to a script nobody runs (AGENTS.md
rules 9 and 10: no network, nothing leaves the machine).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "targetring" / "static"
DOC = REPO / "docs" / "design" / "header-wash.md"
sys.path.insert(0, str(REPO / "scripts"))

from targetring.app import app  # noqa: E402

import make_header_wash as gen  # noqa: E402

LIGHT = STATIC / "header-wash.png"
DARK = STATIC / "header-wash-dark.png"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class TestItIsReproducible:

    @pytest.mark.parametrize("scheme,path", [("light", LIGHT), ("dark", DARK)])
    def test_the_file_on_disk_is_what_the_generator_produces(self, scheme, path
                                                             ) -> None:
        """Pixels, not bytes: PNG encoders differ between versions and a byte
        comparison would fail on a zlib upgrade while the image is identical."""
        assert path.exists(), f"{path.name} missing — run make_header_wash.py"
        fresh = np.asarray(gen.render(scheme, gen.DEFAULT_SEED))
        on_disk = np.asarray(Image.open(path).convert("RGBA"))
        assert np.array_equal(fresh, on_disk), f"{path.name} is stale"

    def test_the_same_seed_twice_is_the_same_image(self) -> None:
        a = np.asarray(gen.render("light", 3))
        b = np.asarray(gen.render("light", 3))
        assert np.array_equal(a, b)

    def test_a_different_seed_is_a_different_image(self) -> None:
        """Or the seed is decoration and `--seed` does nothing."""
        a = np.asarray(gen.render("light", 3))
        b = np.asarray(gen.render("light", 4))
        assert not np.array_equal(a, b)

    @pytest.mark.parametrize("path", [LIGHT, DARK])
    def test_the_png_says_how_to_rebuild_itself(self, path) -> None:
        text = Image.open(path).text
        assert text["Seed"] == str(gen.DEFAULT_SEED)
        assert "make_header_wash.py" in text["Command"]
        assert text["Source"] == "targetring/static/app.css"

    def test_the_script_the_png_and_the_doc_name_one_seed(self) -> None:
        """Three places state the seed; they cannot be allowed to drift."""
        in_png = Image.open(LIGHT).text["Seed"]
        in_doc = re.search(r"--seed (\d+)", DOC.read_text())
        assert in_doc, "the doc states no regeneration command"
        assert in_doc.group(1) == in_png == str(gen.DEFAULT_SEED)


class TestItSamplesTheRealPalette:

    def test_the_pigments_are_read_from_app_css_not_copied(self) -> None:
        light, dark = gen.tokens("light"), gen.tokens("dark")
        for token in gen.PIGMENT_TOKENS:
            assert light[token].startswith("#"), token
            assert dark[token].startswith("#"), token
            # The dark steps are derived against #15171b, not inverted, so the
            # two scopes genuinely differ — which is why there are two renders.
            assert light[token] != dark[token], token

    def test_no_pigment_hex_is_hard_coded_in_the_generator(self) -> None:
        """A hex here would be a second palette that drifts from the real one
        the first time the token block is retuned."""
        src = Path(gen.__file__).read_text()
        for token in gen.PIGMENT_TOKENS:
            assert gen.tokens("light")[token] not in src, token
            assert gen.tokens("dark")[token] not in src, token


class TestItNeverTouchesTheType:

    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def test_the_left_third_is_effectively_clear(self, scheme) -> None:
        """The h1 and the sub-heading sit there. Measured, not asserted by
        reading the ramp constant back out of the code."""
        alpha = np.asarray(gen.render(scheme, gen.DEFAULT_SEED))[..., 3]
        left = alpha[:, :alpha.shape[1] // 3]
        assert left.max() <= 8, f"{scheme}: left third peaks at {left.max()}/255"

    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def test_the_pigment_is_on_the_right(self, scheme) -> None:
        alpha = np.asarray(gen.render(scheme, gen.DEFAULT_SEED))[..., 3]
        third = alpha.shape[1] // 3
        assert alpha[:, -third:].mean() > alpha[:, :third].mean() * 10

    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def test_it_is_an_accent_and_never_opaque(self, scheme) -> None:
        """It is translucent everywhere, so the surface colour beneath always
        shows through and a missing file degrades to paper, not to a hole."""
        alpha = np.asarray(gen.render(scheme, gen.DEFAULT_SEED))[..., 3]
        assert alpha.max() < 255

    def test_dark_is_quieter_than_light_in_absolute_alpha(self) -> None:
        """The eye measures the contrast step from the surface, not the opacity,
        so the same alpha over #15171b reads louder than over white."""
        light = np.asarray(gen.render("light", gen.DEFAULT_SEED))[..., 3]
        dark = np.asarray(gen.render("dark", gen.DEFAULT_SEED))[..., 3]
        assert dark.max() < light.max()


class TestItIsActuallyServed:
    """The defect this class exists for: `header-wash.css` was linked from
    `index.html` with no route behind it, 404ed, and looked exactly like a
    correctly absent accent."""

    @pytest.mark.parametrize("path,ctype", [
        ("/header-wash.css", "text/css"),
        ("/header-wash.png", "image/png"),
        ("/header-wash-dark.png", "image/png"),
    ])
    def test_each_asset_is_served(self, client, path, ctype) -> None:
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(ctype)
        assert len(r.content) > 0

    def test_the_page_links_the_stylesheet(self, client) -> None:
        assert 'href="/header-wash.css"' in client.get("/").text

    def test_no_other_image_is_reachable_through_the_wash_routes(self, client
                                                                  ) -> None:
        """Two literal routes, not a `{variant}` path parameter: a segment
        interpolated into `STATIC / ...` is how this starts serving more."""
        for path in ("/header-wash-evil.png", "/header-wash-.png",
                     "/header-wash../app.css.png"):
            assert client.get(path).status_code == 404, path


class TestAppCssStillNamesNoImage:
    """`app.css` is inlined verbatim into the offline export, which asserts the
    literal text `url(` appears nowhere in it. That is rule 10 in presentational
    form, and it is the reason the wash has its own stylesheet at all."""

    def test_app_css_contains_no_url(self) -> None:
        assert "url(" not in (STATIC / "app.css").read_text()

    def test_the_wash_stylesheet_is_where_the_image_is_named(self) -> None:
        css = (STATIC / "header-wash.css").read_text()
        assert 'url("header-wash.png")' in css
        assert 'url("header-wash-dark.png")' in css

    def test_the_wash_sets_only_background_image_so_it_degrades_to_paper(self
                                                                         ) -> None:
        """Never the `background` shorthand, which would reset the colour
        `app.css` paints underneath."""
        css = (STATIC / "header-wash.css").read_text()
        assert not re.search(r"^\s*background:\s", css, re.M), \
            "the shorthand would reset background-color and leave a hole"
