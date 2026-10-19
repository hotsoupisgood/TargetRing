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

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
DEMO = DOCS / "demo"

pytestmark = pytest.mark.skipif(
    not (DEMO / "index.html").exists(),
    reason="docs/demo not exported; run scripts/export_static_demo.py")

# The four Crohn's runs, `docs/DEMO-RUN-ALLOWLIST.md`.
RUN_IDS = ("20261018T190526Z-94ef5f", "20261018T190549Z-81475c",
           "20261018T190619Z-e52d65", "20261018T190650Z-df1587")

PER_RUN = ("values.json", "evidence.json", "figure.png", "figure.svg",
           "manifest.json", "values.csv", "ledger.csv", "caption.txt",
           "legend.png", "legend.svg")


def test_the_exported_runs_are_the_allowlist():
    listed = [r["run_id"] for r in
              json.loads((DEMO / "api" / "runs.json").read_text())["runs"]]
    assert sorted(listed) == sorted(RUN_IDS)
    assert sorted(p.name for p in (DEMO / "api" / "run").iterdir()) == sorted(RUN_IDS)


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
