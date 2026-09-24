"""v2.2 item 3 — ChEMBL and Europe PMC are off the render path.

Both drive no ring and no rank (PLAN §8, COUNCIL M6) and on 2026-09-24 they were
99% and 64% of the time spent on sources. They are fetched on gene click now.
What must survive the move: their four-state semantics, the never-cache-a-failure
rule, and invariant I5 still being *evaluated* rather than quietly becoming
NOT_EVALUATED for ever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from targetring.cache import Cache
from targetring.http import Http


class TestTheRenderPathDoesNotTouchThem:
    def test_run_analysis_does_not_import_the_chembl_adapter(self) -> None:
        """A structural check, because the cheapest way for this to regress is
        somebody adding one line back into the adapter gather."""
        src = Path("targetring/run.py").read_text()
        assert "ChEMBLAdapter" not in src, (
            "ChEMBL is drawer-on-click; it must not be constructed on the render path")
        assert "chembl.fetch_one" not in src

    def test_the_only_europe_pmc_call_on_the_render_path_is_the_probe(self) -> None:
        src = Path("targetring/run.py").read_text()
        assert "epmc.fetch_one" not in src, (
            "the per-gene Europe PMC fetch belongs on the drawer")
        assert "integrity_probe" in src

    def test_the_drawer_only_cost_alarm_is_still_armed(self) -> None:
        """It fired correctly on v2 (ChEMBL at 99% of source time). Leaving it
        armed is the cheapest way to notice if either comes back."""
        from targetring.run import COST_SHARE_WARN, DRAWER_ONLY_SOURCES
        assert set(DRAWER_ONLY_SOURCES) == {"chembl", "europepmc"}
        assert 0 < COST_SHARE_WARN < 1


class TestI5IsStillEvaluated:
    """An invariant that stops being checked is an invariant that has been
    relaxed. PLAN §6.5 makes the Europe PMC echo an invariant, so one live query
    stays on the render path to be asserted."""

    def test_the_probe_bypasses_the_cache_so_the_echo_is_live(self, tmp_path: Path) -> None:
        import asyncio

        import httpx

        from targetring.adapters.literature import EuropePMCAdapter

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={
                "request": {"queryString": '"BRAF"'}, "hitCount": 42,
            }, headers={"content-type": "application/json"})

        async def go():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            cache = Cache(root=tmp_path)
            a = EuropePMCAdapter(Http(client), cache)
            first = await a.fetch_one("BRAF", None)       # warms the cache
            replay = await a.fetch_one("BRAF", None)      # served from disk
            probe = await a.integrity_probe("BRAF")       # must not be
            return first, replay, probe

        first, replay, probe = asyncio.run(go())
        assert first.live is True
        assert replay.live is False, "a replayed echo is not a checked echo (V11)"
        assert probe.live is True, "the probe must be live by construction"
        assert probe.echo_ok is True
        assert len(seen) == 2, "the probe issued its own request"

    def test_a_reinterpreted_echo_still_fails_i5(self) -> None:
        from targetring.invariants import _i5_literature
        r = _i5_literature({"echo_mismatches": ["BRAF: echoed 'BRAF*'"],
                            "echo_live": 0, "echo_replayed": 0, "ncbi_checked": 3})
        assert r.outcome == "FAIL"

    def test_one_live_echo_is_enough_to_evaluate_it(self) -> None:
        from targetring.invariants import _i5_literature
        r = _i5_literature({"echo_live": 1, "echo_replayed": 0, "ncbi_checked": 14,
                            "echo_mismatches": [], "ncbi_errors": []})
        assert r.outcome == "PASS"

    def test_an_unreachable_probe_is_not_evaluated_not_failed(self) -> None:
        """UX A1: a source being down and an invariant being violated are
        different facts and must not render identically."""
        from targetring.invariants import _i5_literature
        r = _i5_literature({"echo_live": 0, "echo_replayed": 0, "ncbi_checked": 14,
                            "echo_mismatches": [], "ncbi_errors": [],
                            "unreachable": ["BRAF: HTTP 503"]})
        assert r.outcome == "NOT_EVALUATED"
        assert r.affects


class TestTheDrawerFetchKeepsTheSemantics:
    @pytest.mark.asyncio
    async def test_a_gene_with_no_uniprot_is_not_applicable_not_undrugged(self) -> None:
        from targetring.app import fill_drawer_only_sources
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "request": {"queryString": '"XYZ"'}, "hitCount": 3,
            }, headers={"content-type": "application/json"})

        # No UniProt: ChEMBL is skipped entirely and says why, without a call.
        d = {"gene": "XYZ", "uniprot": None, "literature": {}, "chembl": None}
        import targetring.app as app_mod
        real = app_mod.Http
        app_mod.Http = lambda: _FakeHttp(httpx.MockTransport(handler))
        try:
            out = await fill_drawer_only_sources(d)
        finally:
            app_mod.Http = real
        assert out["chembl"]["state"] == "no_uniprot"
        assert "non-coding" in out["chembl"]["note"]
        assert out["literature"]["europepmc_count"] == 3

    @pytest.mark.asyncio
    async def test_a_failed_fetch_is_marked_for_retry_not_kept(self) -> None:
        """Failures are never cached — in the Cache, and not on the drawer
        either. A transient EBI 500 must not become a permanent answer."""
        from targetring.app import fill_drawer_only_sources
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if "europepmc" in str(request.url):
                return httpx.Response(200, json={
                    "request": {"queryString": '"XYZ"'}, "hitCount": 1,
                }, headers={"content-type": "application/json"})
            return httpx.Response(500, text="<html>EBI is unwell</html>")

        d = {"gene": "XYZ", "uniprot": "P00000", "literature": {}, "chembl": None}
        import targetring.app as app_mod
        real = app_mod.Http
        app_mod.Http = lambda: _FakeHttp(httpx.MockTransport(handler))
        try:
            out = await fill_drawer_only_sources(d)
        finally:
            app_mod.Http = real
        assert out["chembl"]["state"] == "failed"
        assert out["chembl"]["note"], "a failure must carry a reason"
        assert out["_retry_on_next_click"] is True


class _FakeHttp(Http):
    """An Http whose client is a MockTransport, usable as an async context
    manager exactly like the real one."""

    def __init__(self, transport) -> None:
        import httpx
        super().__init__(httpx.AsyncClient(transport=transport))


class TestThePerHostGateSurvivesMoreThanOneEventLoop:
    """Found running six fixtures in one process, 2026-09-24: an
    `asyncio.Semaphore` binds to the loop that first blocks on it, so a
    module-level one shared across `asyncio.run()` calls raised
    `RuntimeError: ... is bound to a different event loop` on the second run.
    Latent while every host was capped at 4; certain once clinicaltrials.gov was
    throttled to 2.
    """

    def test_two_sequential_runs_do_not_share_a_bound_semaphore(self) -> None:
        import asyncio

        from targetring.http import _semaphore

        async def contend() -> None:
            sem = _semaphore("https://clinicaltrials.gov/api/v2/studies")

            async def hold() -> None:
                async with sem:
                    await asyncio.sleep(0.01)

            await asyncio.gather(*(hold() for _ in range(6)))

        asyncio.run(contend())
        asyncio.run(contend())  # this is the call that used to raise

    def test_the_gate_still_caps_at_the_host_limit(self) -> None:
        import asyncio

        from targetring.http import HOST_CONCURRENCY, _semaphore

        peak = 0

        async def go() -> None:
            nonlocal peak
            sem = _semaphore("https://clinicaltrials.gov/api/v2/studies")
            live = 0

            async def one() -> None:
                nonlocal live, peak
                async with sem:
                    live += 1
                    peak = max(peak, live)
                    await asyncio.sleep(0.01)
                    live -= 1

            await asyncio.gather(*(one() for _ in range(10)))

        asyncio.run(go())
        assert peak <= HOST_CONCURRENCY["clinicaltrials.gov"]
