"""Demo mode: `config.DEMO_MODE`, enforced by `app._demo_gate`.

The claim under test is a closed one -- a demo box serves the artefacts of the
listed runs and does nothing else -- so the tests enumerate: every acting route
is refused, every run route for an unlisted id is "unknown run", and the two
things a demo must still do (list its runs, serve their files) work. The last
class asserts the gate is inert when the flag is off, because a gate that
leaked into normal mode would refuse Puck's own analyses.

No network: every refused route is refused before its handler runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from targetring import app as app_mod
from targetring import config
from targetring.app import app, demo_forbids, demo_run_allowed

from test_table import a_real_run


@pytest.fixture
def demo(monkeypatch):
    """Demo on, with exactly one allowed run: a real one on disk."""
    allowed = a_real_run()
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_RUNS", (allowed,))
    return allowed


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


ACTING_PATHS = [
    ("POST", "/api/analyse"),
    ("GET", "/api/autopick?disease=MONDO_0005011"),
    ("GET", "/api/region-for?disease=crohn"),
    ("GET", "/api/diseases?q=crohn"),
    ("GET", "/api/contrasts?disease=MONDO_0005011"),
    ("GET", "/api/contrast/E-GEOD-101794/g2_g1/genes"),
    ("GET", "/api/contrast/E-GEOD-101794/g2_g1/heatmap"),
    ("GET", "/api/analyse/progress/sometoken"),
]


class TestEveryActingRouteIsRefused:
    @pytest.mark.parametrize("method,path", ACTING_PATHS)
    def test_refused_with_a_sentence(self, demo, client, method, path) -> None:
        r = client.request(method, path)
        assert r.status_code == 403, (path, r.status_code, r.text[:200])
        body = r.json()
        assert body["error"] == "demo_mode"
        assert "read-only demo" in body["message"]

    def test_the_two_paid_drawer_routes_are_refused_even_for_an_allowed_run(
            self, demo, client) -> None:
        # Phenotype summary is a paid call; web-figure is an in-process render.
        # Both act, both refused, even on the run the demo serves.
        r = client.get(f"/api/run/{demo}/gene/BRAF/phenotypes")
        assert r.status_code == 403 and r.json()["error"] == "demo_mode"
        r = client.post(f"/api/run/{demo}/web-figure", data={"panel_px": 900})
        assert r.status_code == 403 and r.json()["error"] == "demo_mode"


class TestOnlyTheListedRunsExist:
    def test_the_list_is_the_allowlist(self, demo, client) -> None:
        ids = [r["run_id"] for r in client.get("/api/runs").json()["runs"]]
        assert ids == [demo]

    def test_an_old_allowed_run_is_listed_not_cut_by_newest_n(self, client,
                                                             monkeypatch) -> None:
        """Found live 2026-10-15: three allowed, one listed, because the other
        two were older than the 40th-newest run. The allowlist must be applied
        before the cut, so the OLDEST loadable run must appear when allowed."""
        loadable = sorted(p.name for p in config.RUNS_DIR.iterdir()
                          if p.is_dir() and p.name.startswith("20")
                          and (p / "values.csv").exists()
                          and (p / "manifest.json").exists())
        if len(loadable) < 2:
            pytest.skip("need two loadable runs to have an oldest and a newest")
        oldest = loadable[0]
        monkeypatch.setattr(config, "DEMO_MODE", True)
        monkeypatch.setattr(config, "DEMO_RUNS", (oldest,))
        ids = [r["run_id"] for r in client.get("/api/runs").json()["runs"]]
        assert ids == [oldest]

    def test_an_unlisted_real_run_is_unknown(self, demo, client, monkeypatch) -> None:
        # Pick a second real run that is NOT the allowed one. If the box has
        # only one run, the discrimination cannot be made and that is a skip,
        # not a vacuous pass.
        others = [p.name for p in config.RUNS_DIR.iterdir()
                  if p.is_dir() and p.name != demo and (p / "manifest.json").exists()]
        if not others:
            pytest.skip("only one run on disk; cannot test the allowlist boundary")
        other = others[0]
        for path in (f"/api/run/{other}/manifest.json", f"/api/run/{other}/values",
                     f"/api/run/{other}/evidence", f"/api/run/{other}/figure.png"):
            r = client.get(path)
            assert r.status_code == 404, path
            assert r.json()["message"] == "unknown run", path

    def test_a_never_existed_id_gets_the_same_answer(self, demo, client) -> None:
        """Enumeration defence: a real-but-unlisted run and a fictional id must be
        indistinguishable from outside."""
        r = client.get("/api/run/20200101T000000Z-000000/manifest.json")
        assert r.status_code == 404 and r.json()["message"] == "unknown run"

    def test_the_listed_run_is_served(self, demo, client) -> None:
        r = client.get(f"/api/run/{demo}/manifest.json")
        assert r.status_code == 200 and r.json()["run_id"] == demo
        r = client.get(f"/api/run/{demo}/values")
        assert r.status_code == 200
        r = client.get(f"/api/run/{demo}/evidence")
        assert r.status_code == 200

    def test_the_run_list_carries_where_the_genes_came_from(self, demo, client) -> None:
        row = client.get("/api/runs").json()["runs"][0]
        for k in ("contrast", "contrast_name", "filtered", "any_invariant_failed"):
            assert k in row, k


class TestHealthSaysSo:
    def test_demo_is_declared(self, demo, client) -> None:
        h = client.get("/api/health").json()
        assert h["demo"] is True and h["demo_runs"] == [demo]
        assert "read-only demo" in h["note"]

    def test_normal_mode_is_declared_too(self, client, monkeypatch) -> None:
        monkeypatch.setattr(config, "DEMO_MODE", False)
        h = client.get("/api/health").json()
        assert h["demo"] is False


class TestTheGateIsInertWhenOff:
    def test_nothing_is_refused_and_nothing_is_filtered(self, client, monkeypatch) -> None:
        monkeypatch.setattr(config, "DEMO_MODE", False)
        monkeypatch.setattr(config, "DEMO_RUNS", ())
        # Cheap, no-network routes that the gate would otherwise touch.
        assert client.get("/api/health").status_code == 200
        r = client.get("/api/runs")
        assert r.status_code == 200
        # A run route for an id outside the (empty) list is NOT a 404 from the
        # gate; whatever it returns, it is the handler's answer.
        r = client.get("/api/run/20200101T000000Z-000000/manifest.json")
        assert r.json().get("error") != "unknown_run"


class TestTheTwoPredicates:
    """The gate's two lists, as pure functions, so a future route can be
    checked against them without a client."""

    @pytest.mark.parametrize("path", [
        "/api/analyse", "/api/autopick", "/api/region-for", "/api/diseases",
        "/api/contrasts", "/api/contrast/E-1/g1_g2/genes",
        "/api/analyse/progress/t", "/api/run/x/gene/BRAF/phenotypes",
        "/api/run/x/web-figure",
    ])
    def test_forbidden(self, path) -> None:
        assert demo_forbids(path)

    @pytest.mark.parametrize("path", [
        "/", "/app.js", "/api/health", "/api/runs", "/api/regions",
        "/api/orderings", "/api/run/x/values", "/api/run/x/manifest.json",
    ])
    def test_allowed(self, path) -> None:
        assert not demo_forbids(path)

    def test_run_allowlist(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "DEMO_RUNS", ("ok-run",))
        assert demo_run_allowed("/api/run/ok-run/values")
        assert not demo_run_allowed("/api/run/other/values")
        assert demo_run_allowed("/api/runs")          # not a run path
        assert demo_run_allowed("/api/health")
