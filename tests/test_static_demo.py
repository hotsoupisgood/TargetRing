"""The exported static demo under `docs/demo/`.

Cheap on purpose: it reads the tree `scripts/export_static_demo.py` already
wrote and asserts what a reader would notice if it were wrong — a missing
drawer, a page that loads `/app.css` off the host's root, a home directory
published by accident. Re-crawling is the export script's job, not a test's.

Skipped, not failed, when `docs/demo/` is absent: the export is a build
artefact of a repo that has `runs/` on disk, and `runs/` is not in git.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from targetring import config

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
DEMO = DOCS / "demo"

pytestmark = pytest.mark.skipif(
    not (DEMO / "index.html").exists(),
    reason="docs/demo not exported; run scripts/export_static_demo.py")

# v4.0: ONE run, not the four. Puck, 2026-10-19: "just have one example".
# `docs/DEMO-RUN-ALLOWLIST.md` still documents four on disk -- that doc is about
# what may be served, and this is about what IS -- and the one exported is the
# flagship, which is what `config.DEMO_FEATURED_RUN` names.
RUN_IDS = (config.DEMO_FEATURED_RUN,)

PER_RUN = ("values.json", "evidence.json", "figure.png", "figure.svg",
           "manifest.json", "values.csv", "ledger.csv", "caption.txt",
           "legend.png", "legend.svg")


def test_the_exported_runs_are_the_allowlist():
    listed = [r["run_id"] for r in
              json.loads((DEMO / "api" / "runs.json").read_text())["runs"]]
    assert sorted(listed) == sorted(RUN_IDS)
    assert sorted(p.name for p in (DEMO / "api" / "run").iterdir()) == sorted(RUN_IDS)


def test_the_published_site_offers_exactly_one_run():
    """The honest version of "no run-card list": there is no second run to
    reach. A page with the card index hidden by CSS over a four-run export would
    still be four runs one URL apart."""
    assert len(RUN_IDS) == 1
    assert len(list((DEMO / "api" / "run").iterdir())) == 1


def test_the_figure_the_page_inlines_was_exported():
    """`showToolFigure` inlines `figure-web.svg` to make the circle clickable.
    Without that file the tool view falls back to the grid and the page the
    owner asked for is not there."""
    assert (DEMO / "api" / "run" / RUN_IDS[0] / "figure-web.svg").exists()


def test_every_published_dollar_carries_its_measured_date():
    """Puck: "just date it because the costs all change". Both surfaces: the
    manifest a reader downloads AND `values.json`, which is where the spend
    table the page actually draws comes from."""
    base = DEMO / "api" / "run" / RUN_IDS[0]
    for name in ("manifest.json", "values.json"):
        llm = json.loads((base / name).read_text()).get("llm") or {}
        if not llm.get("accounts"):
            continue
        when = llm.get("accounts_measured_utc") or ""
        assert when.startswith("20"), f"{name} publishes undated dollars"
        # The date must be the measurement's, never the export's: re-exporting
        # tomorrow must not relabel these balances as current.
        assert when <= json.loads((base / name).read_text())["timestamp_utc"] \
            or when == json.loads((base / name).read_text())["timestamp_utc"]


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_every_run_has_its_payloads_and_artefacts(run_id):
    base = DEMO / "api" / "run" / run_id
    missing = [n for n in PER_RUN if not (base / n).exists()]
    assert not missing, f"{run_id} is missing {missing}"


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_every_gene_the_grid_links_has_a_drawer_file(run_id):
    """The grid makes a gene clickable iff `baked_drawers` names it, so the
    exported drawer files and that list have to be the same set — one short and
    a click opens a 404 instead of a verdict."""
    base = DEMO / "api" / "run" / run_id
    named = set(json.loads((base / "values.json").read_text())["baked_drawers"])
    on_disk = {p.stem for p in (base / "drawer").glob("*.json")}
    assert named == on_disk and named


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_a_drawer_carries_the_verdict_and_the_summary(run_id):
    """What the demo is for. A drawer without these is a drawer that opens
    empty, which is the one thing the four runs were picked to show."""
    base = DEMO / "api" / "run" / run_id / "drawer"
    one = sorted(base.glob("*.json"))[0]
    d = json.loads(one.read_text())
    assert d["baked"]["run_id"] == run_id
    assert d["recommendation"]["verdict"] in {"GO", "NO-GO", "INSUFFICIENT"}
    assert d["summary"]["text"].strip()


def test_the_page_loads_the_shim_before_app_js_and_its_assets_relatively():
    html = (DEMO / "index.html").read_text()
    assert html.index('src="static-demo.js"') < html.index('src="app.js"')
    for name in ("app.js", "app.css", "header-wash.css"):
        assert f'"/{name}"' not in html, f"{name} is still referenced off the host root"
        assert (DEMO / name).exists()


def test_the_tool_page_names_its_input_gene_expression():
    """Puck, 2026-10-19, on whether "contrast" is the right first word for a
    biologist: "Maybe just title it gene expression and then say
    Differential... or Contrast underneath".

    So the published tool must carry all three layers, and the third one must
    still name the Atlas identifier: a heading a reader understands is only an
    improvement if the thing it names stays auditable. A page that said "Gene
    expression" and dropped `g2_g1` would be a friendlier label over a figure
    nobody can trace back to a study -- this build's own worst case.
    """
    html = (DEMO / "index.html").read_text()
    js = (DEMO / "app.js").read_text()
    assert "Gene expression" in html, "the exported page has no gene expression heading"
    assert "Differential expression:" in html + js, (
        "nothing on the exported page says what was compared")
    # Layer three. The id itself comes from the run's own manifest, so assert
    # both that the page labels one and that the manifest the page reads still
    # carries it -- either half alone would pass over an empty id.
    assert "contrast <code>" in js, (
        "the provenance line no longer labels the Atlas contrast id")
    manifest = json.loads((DEMO / "api" / "run" / config.DEMO_FEATURED_RUN
                           / "manifest.json").read_text())
    assert manifest["contrast"]["contrast_id"], "the exported manifest has no contrast id"


def test_github_pages_needs_these_two():
    assert (DOCS / ".nojekyll").exists()
    assert "./demo/" in (DOCS / "index.html").read_text()


def test_the_shim_repeats_the_servers_refusal_verbatim():
    from targetring.app import DEMO_REFUSAL
    shim = (REPO / "scripts" / "static_demo_shim.js").read_text()
    assert DEMO_REFUSAL in re.sub(r'"\s*\+\s*\n?\s*"', "", shim)


def test_nothing_published_names_this_machine_or_a_key():
    """Rule 10's whole point, as a grep. `ledger.csv` is the DROP ledger (which
    genes left the figure and why) and belongs here; the spend ledger is a
    different file and is not exported by any route."""
    bad = re.compile(r"/Users/|sk-ant-|ANTHROPIC_API_KEY")
    offenders = []
    for p in DEMO.rglob("*"):
        if not p.is_file() or p.suffix in {".png", ".svg"}:
            continue
        if p.name == "app.js":
            continue        # the app's own source, published already
        if bad.search(p.read_text(errors="ignore")):
            offenders.append(str(p.relative_to(DEMO)))
    assert not offenders, offenders
    assert not list(DEMO.rglob("spend-ledger*"))


# ---------------------------------------------------------------------------
# The 2026-10-19 button-flow audit found two ways the published download menus
# were dead, and neither was visible locally — the demo is served at `/` here,
# where the wrong URL happens to be the right one. Both regressions are cheap
# to re-introduce and expensive to notice, so both get a test.
# ---------------------------------------------------------------------------

def test_the_shim_rebases_a_url_set_as_a_property():
    """`app.js` fills the download menus with `el.href = "/api/run/..."`.

    That is not a fetch, not an `innerHTML` write and not an inserted node, so
    the shim's other three rebases never saw it: under `/TargetRing/` all ten
    items pointed at the host's root and every download was a 404.
    """
    shim = (REPO / "scripts" / "static_demo_shim.js").read_text()
    assert "patchUrlProperty" in shim, (
        "the shim no longer patches the href/src property setters; every "
        "download on the published site goes back to 404"
    )
    for ctor, prop in (("HTMLAnchorElement", "href"),
                       ("HTMLImageElement", "src")):
        assert f'patchUrlProperty(window.{ctor}, "{prop}")' in shim, (
            f"{ctor}.{prop} is no longer rebased"
        )
    assert (DEMO / "static-demo.js").read_text() == shim, (
        "docs/demo/static-demo.js is stale; re-run export_static_demo.py"
    )


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_every_artefact_the_payload_advertises_was_exported(run_id):
    """A menu item is built for every name in `artefacts`, so a name that is
    not in the tree is a dead link on a published page.

    `figure-slide.svg` and `figure-slide.png` are deliberately not exported,
    and the payload — which reads the RUN's directory, not the export's — used
    to list them anyway. `export_static_demo.only_exported_artefacts` trims it.
    """
    body = json.loads((DEMO / "api" / "run" / run_id / "values.json").read_text())
    arts = body.get("artefacts")
    assert isinstance(arts, list) and arts, "values.json lost its artefacts list"
    missing = [a for a in arts if not (DEMO / "api" / "run" / run_id / a).exists()]
    assert not missing, (
        f"values.json advertises {missing}, which the export did not write; "
        "every one is a live menu item that 404s"
    )
