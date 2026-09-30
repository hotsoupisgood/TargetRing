"""Stage 5 — selection to circos, internal handoff. Offline.

`from_contrast()` builds a `ParsedInput` server-side from already-fetched
Atlas rows; this file proves it feeds the *existing* pipeline unchanged
(resolve, dedupe, filter, order, render, invariants, manifest) rather than
duplicating any of it. The full end-to-end test at the bottom fakes every
external adapter -- Open Targets, GTEx, PubTator, Pharos, Europe PMC -- by
monkeypatching the classes `run.py` imports, never an HTTP transport, so it
exercises the real orchestration logic with zero network and zero new mocking
infrastructure for adapters this stage does not touch.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from targetring import config
from targetring import run as run_mod
from targetring.adapters.atlas import AtlasAdapter
from targetring.atlas_index import AtlasIndex
from targetring.cache import Cache
from targetring.contrast import (
    Accounting,
    AnalyticsRow,
    ContrastRef,
    hydrate,
    load_analytics,
    top_n_by_padj,
)
from targetring.de_input import ParsedInput
from targetring.invariants import check_contrast_identity, check_three_stage_accounting

FIXTURES = config.REPO_ROOT / "fixtures" / "atlas"
CROHNS_INDEX = FIXTURES / "index" / "crohns-index.json"
REAL_ACC, REAL_CID = "E-GEOD-101794", "g2_g1"


class NoNetwork:
    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError(f"this test made a network request: GET {a!r} {k!r}")


def _cached_atlas(acc: str, root: Path) -> AtlasAdapter:
    shutil.rmtree(root, ignore_errors=True)
    cache = Cache(root=root)
    for kind, suffix in (("atlas.analysis_methods", "-analysis-methods.tsv"),
                        ("atlas.configuration", "-configuration.xml")):
        cache.put(kind, acc, (FIXTURES / f"{acc}{suffix}").read_text())
    cache.put("atlas.analytics", f"{acc}|rnaseq",
             (FIXTURES / f"{acc}-analytics.tsv").read_text())
    return AtlasAdapter(NoNetwork(), cache)


def _index_ref(acc: str, cid: str) -> ContrastRef:
    index = AtlasIndex.load(CROHNS_INDEX)
    row = index.find(acc, cid)
    assert row is not None
    return row.as_ref(index_built_utc=index.built_utc)


# ===========================================================================
# from_contrast()
# ===========================================================================


class TestFromContrast:
    def _ref(self) -> ContrastRef:
        return ContrastRef(accession="E-FAKE", contrast_id="g2_g1", ref_group="g2",
                          test_group="g1", ref_label="control", test_label="disease",
                          n_ref=3, n_test=3, name="fake vs fake")

    def test_builds_one_derow_per_selected_row(self) -> None:
        rows = [AnalyticsRow("ENSG00000000001", "AAA", 0.01, 1.5),
               AnalyticsRow("ENSG00000000002", "BBB", 0.02, -2.0)]
        parsed = run_mod.from_contrast(self._ref(), rows)
        assert parsed.input_row_count == 2
        assert [r.symbol for r in parsed.rows] == ["ENSG00000000001", "ENSG00000000002"]
        assert [r.logfc for r in parsed.rows] == [1.5, -2.0]
        assert [r.padj for r in parsed.rows] == [0.01, 0.02]

    def test_symbol_column_carries_the_ensembl_id_not_a_resolved_symbol(self) -> None:
        """So `run_analysis`'s own resolve step sends it through the SAME
        Resolver path a pasted Ensembl id would take (rule 5) -- this is what
        makes from_contrast a handoff and not a second resolution pipeline."""
        rows = [AnalyticsRow("ENSG00000157764", "BRAF", 1e-9, 3.0)]
        parsed = run_mod.from_contrast(self._ref(), rows)
        assert parsed.rows[0].symbol == "ENSG00000157764"

    def test_column_names_state_the_contrast_own_fields(self) -> None:
        parsed = run_mod.from_contrast(
            self._ref(), [AnalyticsRow("ENSG00000000001", "AAA", 0.01, 1.5)])
        assert parsed.columns["logfc"] == "g2_g1.log2foldchange"
        assert parsed.columns["padj"] == "g2_g1.p-value"

    def test_empty_selection_refuses_rather_than_building_an_empty_input(self) -> None:
        with pytest.raises(ValueError, match="refusal"):
            run_mod.from_contrast(self._ref(), [])


# ===========================================================================
# figure.py's contrast_notes -- I8 read back from the circos SVG
# ===========================================================================


class TestContrastNotesOnTheFigure:
    def _render(self, tmp_path: Path, contrast_notes):
        from targetring.figure import render
        from targetring.order import gene_stats
        from targetring.states import value

        genes = ["BRAF", "CDKN2A", "INS", "KCNQ1", "EGFR"]
        spec = {
            "logfc": {g: value(float(i) - 2.0) for i, g in enumerate(genes)},
            "tractable": {g: value(0.2 * i) for i, g in enumerate(genes)},
            "safety": {g: value(-0.1 * i) for i, g in enumerate(genes)},
            "regional_specificity": {g: value(1.0 + i) for i, g in enumerate(genes)},
            "clinical_phase": {g: value(float(i % 5)) for i, g in enumerate(genes)},
            "literature": {g: value(2.0 + i * 0.4) for i, g in enumerate(genes)},
        }
        out = tmp_path / "figure.svg"
        render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec), region="Ileum",
              out_svg=out, input_count=5, rendered_count=5, ledger_counts={},
              ordering_note="the order of your submitted DE table (unchanged)",
              provenance="run TEST · abc123", contrast_notes=contrast_notes)
        return out

    def test_no_contrast_notes_on_a_paste_path_run(self, tmp_path) -> None:
        """The default -- `contrast_notes=()` -- must change nothing on an
        existing paste-path render."""
        out = self._render(tmp_path, ())
        result = check_contrast_identity(None, out)
        assert result.outcome == "NOT_EVALUATED"

    def test_prose_name_and_sign_sentence_are_both_on_the_rendered_svg(self, tmp_path) -> None:
        ref = ContrastRef(accession="E-GEOD-101794", contrast_id="g2_g1",
                         ref_group="g2", test_group="g1", ref_label="control (n=13)",
                         test_label="Crohn's (n=61)", n_ref=13, n_test=61,
                         name="'Crohn's disease' vs 'non IBD control'")
        notes = [f"{ref.name} · {ref.accession} · {ref.contrast_id}", ref.sign_sentence]
        out = self._render(tmp_path, notes)
        result = check_contrast_identity(ref, out)
        assert result.outcome == "PASS", result.detail

    def test_i8_fails_if_the_sign_sentence_is_missing(self, tmp_path) -> None:
        ref = ContrastRef(accession="E-GEOD-101794", contrast_id="g2_g1",
                         ref_group="g2", test_group="g1", ref_label="control",
                         test_label="disease", n_ref=13, n_test=61, name="fake name")
        # Only the title, never the sign sentence -- the exact defect I8 exists
        # to catch (a figure that names its contrast but not its direction).
        out = self._render(tmp_path, [f"{ref.name} · {ref.accession} · {ref.contrast_id}"])
        result = check_contrast_identity(ref, out)
        assert result.outcome == "FAIL"
        assert "sign sentence" in result.detail


# ===========================================================================
# The full offline pipeline: fixture contrast -> top 60 -> select N -> circos
# ===========================================================================


class _FakeOTRecord:
    associations_error = None
    safety_liabilities: list = []
    mouse_phenotypes: list = []

    def drug_names(self) -> list:
        return []


class _FakeOpenTargets:
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    async def fetch(self, ids):
        return {i: _FakeOTRecord() for i in ids}

    async def fetch_attribution(self, records):
        return None


class _FakeGTEx:
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    async def fetch_one(self, gencode_id):
        return None


class _FakePubTator:
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    async def fetch_one(self, display, region):
        from targetring.adapters.literature import LiteratureRecord
        return LiteratureRecord(symbol=display, query=f"@GENE_{display}",
                                count=10, years={"2020": 10})


class _FakeEuropePMC:
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    async def integrity_probe(self, symbol):
        from targetring.adapters.literature import EPMCResult
        return EPMCResult(query=f'"{symbol}"', count=1, echo_ok=True,
                          error=None, live=True)


class _FakePharos:
    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    async def fetch_many(self, genes, uniprot_map):
        return {g: None for g in genes}


async def _fake_check_contracts(http):
    return []


def _no_data_cell(*a, **k):
    from targetring.states import no_data
    return no_data("adapter mocked for an offline pipeline test")


def _patch_adapters(monkeypatch) -> None:
    """Every adapter class and per-ring cell function `run.py` imports by
    name, replaced with a fake or a constant `no_data` -- never an HTTP mock,
    since this stage does not touch any of those adapters' own correctness
    (each has its own suite)."""
    monkeypatch.setattr(run_mod, "check_contracts", _fake_check_contracts)
    monkeypatch.setattr(run_mod, "OpenTargetsAdapter", _FakeOpenTargets)
    monkeypatch.setattr(run_mod, "GTExAdapter", _FakeGTEx)
    monkeypatch.setattr(run_mod, "PubTatorAdapter", _FakePubTator)
    monkeypatch.setattr(run_mod, "EuropePMCAdapter", _FakeEuropePMC)
    monkeypatch.setattr(run_mod, "PharosAdapter", _FakePharos)
    for name in ("tractable_cell", "chemical_probe_cell", "safety_cell",
                "clinical_phase_cell", "clinical_activity_cell", "rodent_cell",
                "tdl_cell", "literature_cell", "specificity_cell"):
        monkeypatch.setattr(run_mod, name, _no_data_cell)
    monkeypatch.setattr(run_mod, "drug_pipeline_rows", lambda rec: [])
    monkeypatch.setattr(run_mod, "pipeline_summary", lambda rec: {})
    monkeypatch.setattr(run_mod, "severe_liabilities", lambda rec: [])
    monkeypatch.setattr(run_mod, "trials_panel", lambda rec: [])


class TestEndToEndAtlasRun:
    @pytest.mark.asyncio
    async def test_fixture_contrast_top_60_select_14_to_circos(
        self, monkeypatch, tmp_path
    ) -> None:
        _patch_adapters(monkeypatch)
        atlas = _cached_atlas(REAL_ACC, tmp_path / "cache")
        ref = _index_ref(REAL_ACC, REAL_CID)
        hydrated = await hydrate(atlas, ref)
        rows = await load_analytics(atlas, hydrated)
        top, accounting = top_n_by_padj(rows, 60)

        # Select 14, exactly the plan's "done when" number.
        selection = top[:14]
        selection_accounting = accounting.with_selection(len(selection))
        parsed = run_mod.from_contrast(hydrated, selection)

        result = await run_mod.run_analysis(
            parsed_input=parsed, region="Ileum", run_regression=False,
            runs_dir=tmp_path / "runs", cache=Cache(root=tmp_path / "run_cache"),
            contrast=hydrated, selection_accounting=selection_accounting)

        # I1-I7 all evaluated (PASS or the expected NOT_EVALUATED for I5/I6,
        # since literature/regression are faked/skipped here); none FAILED.
        assert not result.invariants.failures, [
            (r.id, r.detail) for r in result.invariants.failures]
        ids = {r.id for r in result.invariants.results}
        assert {"I1", "I2", "I3", "I4", "I7", "I8", "I9"} <= ids

        i8 = next(r for r in result.invariants.results if r.id == "I8")
        i9 = next(r for r in result.invariants.results if r.id == "I9")
        assert i8.outcome == "PASS", i8.detail
        assert i9.outcome == "PASS", i9.detail

        # input_row_count == len(selection) (plan's "done when").
        assert result.manifest.input_row_count == 14
        assert result.manifest.rendered_count == 14

        # The manifest carries the contrast and the selection accounting.
        assert result.manifest.contrast["accession"] == REAL_ACC
        assert result.manifest.contrast["contrast_id"] == REAL_CID
        assert result.manifest.selection_accounting["n_selected"] == 14
        assert result.manifest.selection_accounting["n_top"] == 60

        # The CSV's logfc/padj equal the analytics file's values for those
        # genes -- no re-derivation (plan's "done when").
        import csv as csv_mod
        with open(result.csv_path) as f:
            csv_rows = list(csv_mod.DictReader(f))
        by_gene_ring = {(r["gene"], r["ring"]): r for r in csv_rows}
        by_id = {r.ensembl_id: r for r in selection}
        # `logfc`'s cell is keyed by the resolved approved symbol on the
        # figure, so join back through the manifest's gene order instead of
        # the raw Ensembl id.
        assert len(result.gene_order) == 14

    @pytest.mark.asyncio
    async def test_a_paste_path_run_is_unaffected_by_the_new_params(
        self, monkeypatch, tmp_path
    ) -> None:
        """contrast=None, selection_accounting=None (the defaults) must leave
        the existing seven-invariant shape exactly as it was."""
        _patch_adapters(monkeypatch)
        table = "gene,logfc,padj\nBRAF,1.5,0.001\nEGFR,-2.0,0.01\n"
        result = await run_mod.run_analysis(
            table=table, region="Ileum", run_regression=False,
            runs_dir=tmp_path / "runs", cache=Cache(root=tmp_path / "run_cache"))
        ids = [r.id for r in result.invariants.results]
        assert ids == ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]
        assert result.manifest.contrast is None
        assert result.manifest.selection_accounting is None


# ===========================================================================
# POST /api/analyse, source=atlas
# ===========================================================================


@pytest.fixture()
def route_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from targetring import app as app_mod

    _patch_adapters(monkeypatch)

    cache = Cache(root=tmp_path / "cache")
    for kind, suffix in (("atlas.analysis_methods", "-analysis-methods.tsv"),
                        ("atlas.configuration", "-configuration.xml")):
        cache.put(kind, REAL_ACC, (FIXTURES / f"{REAL_ACC}{suffix}").read_text())
    cache.put("atlas.analytics", f"{REAL_ACC}|rnaseq",
             (FIXTURES / f"{REAL_ACC}-analytics.tsv").read_text())

    class OfflineHttp:
        async def __aenter__(self):  # noqa: ANN204
            return NoNetwork()

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    monkeypatch.setattr(app_mod, "Http", OfflineHttp)
    monkeypatch.setattr(app_mod, "Cache", lambda *a, **k: cache)
    monkeypatch.setattr(app_mod.atlas_index, "index_path", lambda *a, **k: CROHNS_INDEX)
    monkeypatch.setattr(app_mod, "_index_cache", None)
    monkeypatch.setattr(app_mod, "RUNS_DIR", tmp_path / "runs")
    # `run_analysis` resolves its own `RUNS_DIR` from `run.py`'s own import of
    # `config.RUNS_DIR`, a separate name binding from `app.py`'s -- patching
    # only `app_mod.RUNS_DIR` (used for reading artefacts back, e.g.
    # `/api/run/{id}/{filename}`) leaves `run_analysis` writing to the real
    # repo `runs/` directory. This test is the first one in the whole suite to
    # call the live `/api/analyse` route end-to-end, which is exactly why nothing
    # caught this until now: found by a stray `runs/2026...` directory
    # appearing after this test ran and `test_table.py`'s `in_memory` check
    # failing on the *same* run id, still warm in `app_mod._runs`.
    monkeypatch.setattr(run_mod, "RUNS_DIR", tmp_path / "runs")

    before = set(app_mod._runs)
    yield TestClient(app_mod.app)
    # Never leave this test's run(s) warm in the module-level `_runs` dict for
    # a later test file to trip over (test_table.py's `in_memory` check did,
    # before this fixture became a generator).
    for run_id in set(app_mod._runs) - before:
        app_mod._runs.pop(run_id, None)


def _top_ids(n: int = 5) -> list[str]:
    """Deterministic ids from the real fixture, computed independently of the
    route, so the test can name a real selection."""
    import asyncio

    async def go():
        atlas = _cached_atlas(REAL_ACC, Path("/tmp/_stage5_route_helper_cache"))
        ref = _index_ref(REAL_ACC, REAL_CID)
        hydrated = await hydrate(atlas, ref)
        rows = await load_analytics(atlas, hydrated)
        top, _ = top_n_by_padj(rows, 60)
        return [r.ensembl_id for r in top[:n]]

    return asyncio.run(go())


class TestAnalyseRouteSourceAtlas:
    def test_a_valid_selection_produces_a_run(self, route_client) -> None:
        import json as json_mod
        ids = _top_ids(5)
        r = route_client.post("/api/analyse", data={
            "source": "atlas", "accession": REAL_ACC, "contrast_id": REAL_CID,
            "n": 60, "direction": "both", "ensembl_ids": json_mod.dumps(ids),
            "region": "Ileum",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counts"]["rendered"] == 5
        assert body["contrast"]["accession"] == REAL_ACC
        assert body["selection_accounting"]["n_selected"] == 5

    def test_empty_selection_is_a_422_refusal(self, route_client) -> None:
        r = route_client.post("/api/analyse", data={
            "source": "atlas", "accession": REAL_ACC, "contrast_id": REAL_CID,
            "ensembl_ids": "[]",
        })
        assert r.status_code == 422
        assert r.json()["error"] == "no_selection"

    def test_a_selection_outside_the_shown_top_n_is_rejected(self, route_client) -> None:
        import json as json_mod
        r = route_client.post("/api/analyse", data={
            "source": "atlas", "accession": REAL_ACC, "contrast_id": REAL_CID,
            "n": 5, "ensembl_ids": json_mod.dumps(["ENSG00000000000"]),
        })
        assert r.status_code == 422
        assert r.json()["error"] == "selection_outside_top_n"

    def test_malformed_ensembl_ids_is_a_422(self, route_client) -> None:
        r = route_client.post("/api/analyse", data={
            "source": "atlas", "accession": REAL_ACC, "contrast_id": REAL_CID,
            "ensembl_ids": "not json",
        })
        assert r.status_code == 422
        assert r.json()["error"] == "bad_ensembl_ids"

    def test_missing_accession_is_a_400(self, route_client) -> None:
        r = route_client.post("/api/analyse", data={"source": "atlas"})
        assert r.status_code == 400
