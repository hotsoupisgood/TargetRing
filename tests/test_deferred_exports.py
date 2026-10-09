"""The five download-only artefacts, rendered after the response.

`run_analysis(defer_exports=True)` returns the slide, the legend and the
caption as a callable instead of rendering them inline — measured at ~4.5s of a
~19s run. What these tests defend:

* the DEFERRED SET is download-only. The figure the page shows and everything
  the manifest attests are rendered and checked before the response, so nothing
  a reader is shown depends on the task having finished.
* `assert_artefacts_are_their_own_layout` moves WITH the artefacts. It exists
  because one render wrote over another's PNG with both SVGs fine and nothing
  noticed; a version that ran over a half-written directory would miss exactly
  that.
* a missing file is explained, never 404ed as though the run never had one.
* "died" is a fact (a `running` marker whose pid is gone), not a timeout guess.

No network and no paid call.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from targetring.cache import Cache
from targetring.run import DEFERRED_ARTEFACTS, run_analysis

TABLE = "gene,logFC,padj\nBRAF,2.41,0.0003\nEGFR,-1.8,0.002\n"


async def _run(tmp_path, **kw):
    return await run_analysis(
        table=TABLE, region="Lung", run_regression=False,
        runs_dir=tmp_path / "runs", cache=Cache(root=tmp_path / "c"), **kw)


class TestTheDeferredSetIsDownloadOnly:

    @pytest.mark.asyncio
    async def test_the_page_figure_and_manifest_exist_at_response(
            self, tmp_path) -> None:
        """Nothing a reader is shown waits on the task."""
        r = await _run(tmp_path, defer_exports=True)
        rd = tmp_path / "runs" / r.run_id
        for essential in ("figure.svg", "figure-web.svg", "manifest.json",
                          "values.csv", "ledger.csv"):
            assert (rd / essential).exists(), essential

    @pytest.mark.asyncio
    async def test_the_deferred_five_are_absent_until_the_task_runs(
            self, tmp_path) -> None:
        r = await _run(tmp_path, defer_exports=True)
        rd = tmp_path / "runs" / r.run_id
        assert all(not (rd / a).exists() for a in DEFERRED_ARTEFACTS)
        r.finish_exports()
        assert all((rd / a).exists() for a in DEFERRED_ARTEFACTS)

    @pytest.mark.asyncio
    async def test_the_eager_path_is_unchanged(self, tmp_path) -> None:
        """Default off: every artefact inline, no marker, no callable."""
        r = await _run(tmp_path)
        rd = tmp_path / "runs" / r.run_id
        assert all((rd / a).exists() for a in DEFERRED_ARTEFACTS)
        assert r.finish_exports is None
        assert not (rd / "exports.json").exists()

    @pytest.mark.asyncio
    async def test_the_invariants_are_decided_before_the_response(
            self, tmp_path) -> None:
        """The manifest attests what it attests whether or not the task ran."""
        r = await _run(tmp_path, defer_exports=True)
        assert r.manifest.invariants
        assert isinstance(r.manifest.all_invariants_pass, bool)


class TestTheStateIsOnDisk:

    @pytest.mark.asyncio
    async def test_a_finished_task_records_done_with_its_pid(self, tmp_path
                                                              ) -> None:
        import os
        r = await _run(tmp_path, defer_exports=True)
        r.finish_exports()
        rec = json.loads((tmp_path / "runs" / r.run_id / "exports.json").read_text())
        assert rec["state"] == "done"
        assert rec["pid"] == os.getpid()
        assert rec["artefacts"] == list(DEFERRED_ARTEFACTS)

    @pytest.mark.asyncio
    async def test_a_failing_task_records_the_error_and_re_raises(
            self, tmp_path, monkeypatch) -> None:
        """The marker is the download route's only way to say why a file is
        absent; the raise is how the caller learns at all."""
        from targetring import figure
        r = await _run(tmp_path, defer_exports=True)
        monkeypatch.setattr(figure, "render_legend",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            r.finish_exports()
        rec = json.loads((tmp_path / "runs" / r.run_id / "exports.json").read_text())
        assert rec["state"] == "failed"
        assert "boom" in rec["error"]

    @pytest.mark.asyncio
    async def test_running_is_written_before_the_work_starts(self, tmp_path,
                                                              monkeypatch) -> None:
        """A process killed mid-render cannot write its own epitaph, so the
        marker has to exist before there is anything to be killed during."""
        from targetring import figure
        r = await _run(tmp_path, defer_exports=True)
        path = tmp_path / "runs" / r.run_id / "exports.json"
        seen = {}

        def spy(**kw):
            seen["state"] = json.loads(path.read_text())["state"]
            raise RuntimeError("stop here")

        monkeypatch.setattr(figure, "render_legend", spy)
        with pytest.raises(RuntimeError):
            r.finish_exports()
        assert seen["state"] == "running"


class TestTheLayoutCheckTravelsWithTheArtefacts:

    def test_it_runs_inside_finish_exports(self) -> None:
        """It cannot pass judgement on a directory still missing half its
        files, and the bug it earned is one a partial check would miss."""
        src = (pathlib.Path(__file__).resolve().parent.parent / "targetring"
               / "run.py").read_text()
        body = src[src.index("def finish_exports() -> None:"):]
        body = body[:body.index("def finish_exports_tracked")]
        assert "assert_artefacts_are_their_own_layout" in body

    def test_the_caption_travels_with_the_slide_that_makes_it(self) -> None:
        """`caption.txt` is the slide render's own output, so deferring one
        defers the other."""
        assert "caption.txt" in DEFERRED_ARTEFACTS
        assert "figure-slide.svg" in DEFERRED_ARTEFACTS
