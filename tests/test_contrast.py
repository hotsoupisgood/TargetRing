"""Stage 3 — contrast statistics and the gene table, no figure yet. Offline.

`FakeResolver` stands in for `resolve.Resolver`: `build_gene_table` takes
`resolver: Any` on purpose (contrast.py's own docstring) so this module's tests
can check contrast.py's own logic -- selection, accounting, table assembly --
without re-testing `resolve.py`'s network hops, which have their own suite.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from targetring import config
from targetring.adapters.atlas import AtlasAdapter
from targetring.atlas_index import AtlasIndex
from targetring.cache import Cache
from targetring.contrast import (
    Accounting,
    AnalyticsRow,
    ContrastRef,
    MicroarrayNotDrawable,
    build_gene_table,
    hydrate,
    load_analytics,
    padj_column_label,
    parse_analytics,
    top_n_by_padj,
)
from targetring.http import HardFailure
from targetring.resolve import Resolution

FIXTURES = config.REPO_ROOT / "fixtures" / "atlas"
CROHNS_INDEX = FIXTURES / "index" / "crohns-index.json"

REAL_ACC = "E-GEOD-101794"
REAL_CID = "g2_g1"
NULL_ACC = "E-GEOD-57945"
NULL_CID = "g6_g4"


class NoNetwork:
    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError(f"this test made a network request: GET {a!r} {k!r}")


class FakeResolver:
    """Duck-types `resolve.Resolver`. Records what it was asked to resolve so a
    test can assert build_gene_table sent every selected id through it."""

    def __init__(self, overrides: dict[str, Resolution] | None = None) -> None:
        self.overrides = overrides or {}
        self.requested: list[str] = []

    async def resolve_all(self, ids: list[str]) -> dict[str, Resolution]:
        self.requested = list(ids)
        out: dict[str, Resolution] = {}
        for i in ids:
            if i in self.overrides:
                out[i] = self.overrides[i]
            else:
                out[i] = Resolution(i, "ok", ensembl_id=i, approved_symbol=f"SYM_{i[-4:]}")
        return out


def _cached_atlas(acc: str, root: Path) -> AtlasAdapter:
    """A real `AtlasAdapter` whose cache is pre-warmed from the fixture files,
    so no network path is ever reached -- the same convention `test_atlas.py`
    uses for the OLS4 cache tree."""
    shutil.rmtree(root, ignore_errors=True)
    cache = Cache(root=root)
    cache.put("atlas.analytics", f"{acc}|rnaseq",
             (FIXTURES / f"{acc}-analytics.tsv").read_text())
    cache.put("atlas.analysis_methods", acc,
             (FIXTURES / f"{acc}-analysis-methods.tsv").read_text())
    cache.put("atlas.configuration", acc,
             (FIXTURES / f"{acc}-configuration.xml").read_text())
    return AtlasAdapter(NoNetwork(), cache)


def _index_ref(acc: str, cid: str) -> ContrastRef:
    index = AtlasIndex.load(CROHNS_INDEX)
    row = index.find(acc, cid)
    assert row is not None, f"{acc} {cid} missing from the fixture index"
    return row.as_ref(index_built_utc=index.built_utc)


@pytest.fixture()
def real_atlas(tmp_path) -> AtlasAdapter:
    return _cached_atlas(REAL_ACC, tmp_path / "cache")


@pytest.fixture()
def null_atlas(tmp_path) -> AtlasAdapter:
    return _cached_atlas(NULL_ACC, tmp_path / "cache")


# ===========================================================================
# parse_analytics
# ===========================================================================


def _ref(contrast_id: str = "g2_g1", **kw) -> ContrastRef:
    rg, tg = contrast_id.split("_", 1)
    base = dict(accession="E-FAKE", contrast_id=contrast_id, ref_group=rg,
               test_group=tg, ref_label="control", test_label="disease",
               n_ref=3, n_test=3, name="fake vs fake")
    base.update(kw)
    return ContrastRef(**base)


TWO_CONTRAST_TSV = (
    "Gene ID\tGene Name\tg2_g1.p-value\tg2_g1.log2foldchange\t"
    "g4_g3.p-value\tg4_g3.log2foldchange\n"
    "ENSG00000000001\tAAA\t0.01\t1.5\t0.5\t-9.0\n"
    "ENSG00000000002\tBBB\tNA\t2.5\t0.02\t3.0\n"
    "ENSG00000000003\tCCC\t0.03\t-1.0\tNA\t0.1\n"
)


class TestParseAnalytics:
    def test_reads_only_the_named_contrasts_columns(self) -> None:
        rows = parse_analytics(TWO_CONTRAST_TSV, _ref("g2_g1"))
        by_id = {r.ensembl_id: r for r in rows}
        # g4_g3's numbers must never leak into g2_g1's row.
        assert by_id["ENSG00000000001"].pvalue == 0.01
        assert by_id["ENSG00000000001"].log2fc == 1.5
        assert by_id["ENSG00000000002"].pvalue is None  # NA in g2_g1, not g4_g3's 0.02
        assert by_id["ENSG00000000002"].log2fc == 2.5

    def test_reading_the_other_contrast_gets_its_own_numbers(self) -> None:
        rows = parse_analytics(TWO_CONTRAST_TSV, _ref("g4_g3"))
        by_id = {r.ensembl_id: r for r in rows}
        assert by_id["ENSG00000000001"].pvalue == 0.5
        assert by_id["ENSG00000000001"].log2fc == -9.0

    def test_missing_contrast_columns_is_a_hard_failure(self) -> None:
        with pytest.raises(HardFailure, match="no columns for contrast"):
            parse_analytics(TWO_CONTRAST_TSV, _ref("g9_g8"))

    def test_non_ensg_gene_id_is_a_hard_failure(self) -> None:
        bad = TWO_CONTRAST_TSV.replace("ENSG00000000002", "NOT_A_GENE")
        with pytest.raises(HardFailure, match="not ENSG-shaped"):
            parse_analytics(bad, _ref("g2_g1"))

    def test_duplicate_gene_id_is_a_hard_failure(self) -> None:
        dup = TWO_CONTRAST_TSV + "ENSG00000000001\tAAA\t0.9\t0.1\t0.9\t0.1\n"
        with pytest.raises(HardFailure, match="duplicate Gene ID"):
            parse_analytics(dup, _ref("g2_g1"))

    def test_empty_atlas_symbol_is_kept_as_empty_not_nan(self) -> None:
        text = TWO_CONTRAST_TSV.replace("ENSG00000000003\tCCC", "ENSG00000000003\t")
        rows = parse_analytics(text, _ref("g2_g1"))
        row = next(r for r in rows if r.ensembl_id == "ENSG00000000003")
        assert row.atlas_symbol == ""


# ===========================================================================
# top_n_by_padj -- rule 1 as a sort
# ===========================================================================


class TestTopNByPadj:
    def _rows(self) -> list[AnalyticsRow]:
        return [
            AnalyticsRow("ENSG1", "A", 0.001, 5.0),   # huge logFC, no stat elsewhere
            AnalyticsRow("ENSG2", "B", None, 99.0),   # NO statistic: must never surface
            AnalyticsRow("ENSG3", "C", 0.01, -3.0),
            AnalyticsRow("ENSG4", "D", 0.02, 2.0),
            AnalyticsRow("ENSG5", "E", None, -50.0),  # NO statistic either
        ]

    def test_rows_without_a_statistic_are_never_selected(self) -> None:
        top, acc = top_n_by_padj(self._rows(), n=10)
        ids = {r.ensembl_id for r in top}
        assert "ENSG2" not in ids and "ENSG5" not in ids
        assert acc.n_without_statistic == 2
        assert acc.n_with_statistic == 3

    def test_top_n_is_sorted_by_p_not_by_logfc(self) -> None:
        top, _ = top_n_by_padj(self._rows(), n=2)
        assert [r.ensembl_id for r in top] == ["ENSG1", "ENSG3"]

    def test_up_and_down_counts_are_returned(self) -> None:
        _, acc = top_n_by_padj(self._rows(), n=10)
        assert acc.n_up == 2  # ENSG1, ENSG4
        assert acc.n_down == 1  # ENSG3

    def test_split_direction_balances_up_and_down(self) -> None:
        rows = [AnalyticsRow(f"ENSG{i}", "X", 0.001 * i, 1.0) for i in range(1, 6)]
        rows += [AnalyticsRow(f"ENSG{i}d", "X", 0.001 * i, -1.0) for i in range(1, 6)]
        top, acc = top_n_by_padj(rows, n=4, direction="split")
        assert acc.n_up == 2 and acc.n_down == 2

    def test_bad_direction_raises(self) -> None:
        with pytest.raises(ValueError):
            top_n_by_padj(self._rows(), n=2, direction="sideways")


# ===========================================================================
# Accounting -- I9
# ===========================================================================


class TestAccounting:
    def test_stage_a_is_asserted_at_construction(self) -> None:
        with pytest.raises(AssertionError, match="stage A"):
            Accounting(n_total=10, n_with_statistic=4, n_without_statistic=4)

    def test_stage_b_is_asserted_at_construction(self) -> None:
        with pytest.raises(AssertionError, match="stage B"):
            Accounting(n_total=10, n_with_statistic=4, n_without_statistic=6, n_top=5)

    def test_stage_c_is_asserted_at_construction(self) -> None:
        with pytest.raises(AssertionError, match="stage C"):
            Accounting(n_total=10, n_with_statistic=4, n_without_statistic=6,
                      n_top=4, n_selected=5)

    def test_accounting_line_matches_plan_wording(self) -> None:
        a = Accounting(n_total=65217, n_with_statistic=25232,
                       n_without_statistic=39985, n_top=60)
        assert a.accounting_line() == (
            "65,217 genes in contrast · 25,232 with a statistic · top 60 "
            "shown · 0 selected yet")

    def test_selection_rule_line(self) -> None:
        a = Accounting(n_total=100, n_with_statistic=80, n_without_statistic=20, n_top=20)
        assert a.selection_rule_line() == "top 20 of 80 by adjusted p"

    def test_with_selection_returns_a_new_object(self) -> None:
        a = Accounting(n_total=10, n_with_statistic=10, n_without_statistic=0, n_top=5)
        b = a.with_selection(3)
        assert b.n_selected == 3 and a.n_selected == 0


# ===========================================================================
# hydrate() -- fills ensembl_release, pipeline, assay ids
# ===========================================================================


class TestHydrate:
    @pytest.mark.asyncio
    async def test_fills_release_pipeline_and_assay_ids(self, real_atlas) -> None:
        ref = _index_ref(REAL_ACC, REAL_CID)
        hydrated = await hydrate(real_atlas, ref)
        assert hydrated.ensembl_release == "95"
        assert hydrated.pipeline.startswith("DESeq2")
        assert len(hydrated.ref_assays) == ref.n_ref == 13
        assert len(hydrated.test_assays) == ref.n_test == 61
        assert len(set(hydrated.ref_assays) & set(hydrated.test_assays)) == 0

    @pytest.mark.asyncio
    async def test_refuses_microarray(self, real_atlas) -> None:
        ref = _ref(platform="A-AFFY-44")
        with pytest.raises(MicroarrayNotDrawable):
            await hydrate(real_atlas, ref)

    @pytest.mark.asyncio
    async def test_stale_index_naming_a_gone_contrast_is_a_hard_failure(
        self, real_atlas
    ) -> None:
        ref = _ref(contrast_id="g99_g98", accession=REAL_ACC, ref_group="g99",
                  test_group="g98")
        with pytest.raises(HardFailure, match="no longer names contrast"):
            await hydrate(real_atlas, ref)


class TestPadjColumnLabel:
    def test_names_the_pipeline_it_actually_read(self) -> None:
        ref = _ref(pipeline="DESeq2 version: 1.10.1")
        assert "DESeq2" in padj_column_label(ref)
        assert "not assumed" not in padj_column_label(ref)  # no false claim of assumption
        assert "-analysis-methods.tsv" in padj_column_label(ref)

    def test_never_hardcodes_desep2_for_an_unhydrated_ref(self) -> None:
        ref = _ref(pipeline="")
        assert "DESeq2" not in padj_column_label(ref)


# ===========================================================================
# build_gene_table -- the whole of stage 3
# ===========================================================================


class TestBuildGeneTable:
    @pytest.mark.asyncio
    async def test_end_to_end_against_the_real_fixture(self, real_atlas) -> None:
        ref = _index_ref(REAL_ACC, REAL_CID)
        resolver = FakeResolver()
        table = await build_gene_table(real_atlas, resolver, ref, n=60)

        assert len(table.rows) == 60
        assert table.accounting.n_top == 60
        assert table.accounting.n_total == 2000  # the trimmed fixture's row count
        # Every selected id was sent through the resolver -- rule 5.
        assert set(resolver.requested) == {r.ensembl_id for r in table.rows}
        # Rule 1: nothing selected lacks a statistic.
        assert all(r.pvalue is not None for r in table.rows)

    @pytest.mark.asyncio
    async def test_a_gene_open_targets_does_not_resolve_is_counted_not_dropped(
        self, real_atlas
    ) -> None:
        ref = _index_ref(REAL_ACC, REAL_CID)
        # Force the first selected gene to fail resolution, the spike's own
        # case (ENSG00000279487 unresolved by Open Targets).
        rows = await load_analytics(real_atlas, await hydrate(real_atlas, ref))
        top, _ = top_n_by_padj(rows, 60)
        unresolved_id = top[0].ensembl_id
        resolver = FakeResolver({
            unresolved_id: Resolution(unresolved_id, "no_match",
                                      note="no match in current Open Targets")
        })
        table = await build_gene_table(real_atlas, resolver, ref, n=60)
        assert table.n_excluded_no_resolution == 1
        row = next(r for r in table.rows if r.ensembl_id == unresolved_id)
        assert row.resolution_status == "no_match"
        # Atlas's own symbol is shown when resolution fails but Atlas named the
        # gene (rule 5); the id is only the last-resort fallback, tested
        # separately below for a gene with no Atlas symbol at all (spike:
        # ENSG00000279487).
        assert row.display_symbol == row.atlas_symbol
        assert row.display_symbol != "nan"

    def test_a_gene_with_no_atlas_symbol_and_no_resolution_shows_its_id(self) -> None:
        """The spike's own case: `ENSG00000279487` has an empty Gene Name and
        was the one id Open Targets did not return either."""
        from targetring.contrast import GeneTableRow

        row = GeneTableRow(
            ensembl_id="ENSG00000279487", atlas_symbol="", pvalue=0.01,
            log2fc=1.0, resolution_status="no_match", resolved_symbol=None,
            resolution_note="no match")
        assert row.display_symbol == "ENSG00000279487"
        assert row.display_symbol != "nan"

    @pytest.mark.asyncio
    async def test_microarray_contrast_refuses_before_any_fetch(self, real_atlas) -> None:
        ref = _ref(platform="A-AFFY-44")
        with pytest.raises(MicroarrayNotDrawable):
            await build_gene_table(real_atlas, FakeResolver(), ref, n=60)

    @pytest.mark.asyncio
    async def test_direction_split_is_accepted(self, real_atlas) -> None:
        ref = _index_ref(REAL_ACC, REAL_CID)
        table = await build_gene_table(real_atlas, FakeResolver(), ref, n=20,
                                       direction="split")
        assert table.accounting.n_up + table.accounting.n_down == len(table.rows)

    @pytest.mark.asyncio
    async def test_the_null_fixture_also_builds_a_table(self, null_atlas) -> None:
        """The null-contrast fixture must be just as usable as the real one --
        this is not a fixture that only the heatmap risk test reaches."""
        ref = _index_ref(NULL_ACC, NULL_CID)
        table = await build_gene_table(null_atlas, FakeResolver(), ref, n=60)
        assert len(table.rows) == 60
        assert table.ref.sign_sentence.startswith("positive log2FC = up in")


# ===========================================================================
# GET /api/contrast/{accession}/{contrast_id}/genes
# ===========================================================================


@pytest.fixture()
def route_client(monkeypatch, tmp_path):
    """The route's real collaborators, pointed at fixture data: the shared
    Crohn's index, and a `Cache` pre-warmed with the real-contrast fixture
    files. `Resolver` is swapped for `FakeResolver` so the route never makes a
    live Open Targets call."""
    from fastapi.testclient import TestClient

    from targetring import app as app_mod

    cache_root = tmp_path / "cache"
    cache = Cache(root=cache_root)
    for acc in (REAL_ACC, NULL_ACC):
        cache.put("atlas.analytics", f"{acc}|rnaseq",
                 (FIXTURES / f"{acc}-analytics.tsv").read_text())
        cache.put("atlas.analysis_methods", acc,
                 (FIXTURES / f"{acc}-analysis-methods.tsv").read_text())
        cache.put("atlas.configuration", acc,
                 (FIXTURES / f"{acc}-configuration.xml").read_text())

    class OfflineHttp:
        async def __aenter__(self):  # noqa: ANN204
            return NoNetwork()

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    monkeypatch.setattr(app_mod, "Http", OfflineHttp)
    monkeypatch.setattr(app_mod, "Cache", lambda *a, **k: cache)
    monkeypatch.setattr(app_mod, "Resolver", lambda *a, **k: FakeResolver())
    monkeypatch.setattr(app_mod.atlas_index, "index_path", lambda *a, **k: CROHNS_INDEX)
    monkeypatch.setattr(app_mod, "_index_cache", None)
    monkeypatch.setattr(app_mod, "RUNS_DIR", tmp_path / "runs")
    return TestClient(app_mod.app)


class TestGenesRoute:
    def test_the_real_contrast_returns_a_60_row_table(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{REAL_ACC}/{REAL_CID}/genes")
        assert r.status_code == 200
        body = r.json()
        assert len(body["rows"]) == 60
        assert body["accounting"]["n_top"] == 60
        assert body["sign_sentence"].startswith("positive log2FC = up in")

    def test_n_is_capped_at_max_genes(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{REAL_ACC}/{REAL_CID}/genes",
                             params={"n": 5000})
        assert r.status_code == 200
        assert len(r.json()["rows"]) == config.MAX_GENES

    def test_a_bad_direction_is_a_422(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{REAL_ACC}/{REAL_CID}/genes",
                             params={"direction": "sideways"})
        assert r.status_code == 422
        assert r.json()["error"] == "bad_direction"

    def test_an_unknown_contrast_is_a_404(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{REAL_ACC}/g999_g998/genes")
        assert r.status_code == 404

    def test_a_missing_index_is_a_503(self, route_client, monkeypatch, tmp_path) -> None:
        """`read_index_path`, not `index_path`: the route reads the cache's
        index if a build wrote one and otherwise the checked-in copy under
        `fixtures/` (README: the disease picker works out of the box). Pointing
        only the cache path at nothing left the shipped copy to answer, which
        is correct behaviour, so the test must remove BOTH to earn its 503."""
        from targetring import app as app_mod
        monkeypatch.setattr(app_mod.atlas_index, "read_index_path",
                            lambda *a, **k: tmp_path / "nope.json")
        monkeypatch.setattr(app_mod, "_index_cache", None)
        r = route_client.get(f"/api/contrast/{REAL_ACC}/{REAL_CID}/genes")
        assert r.status_code == 503

    def test_microarray_is_409_never_a_partial_table(self, route_client) -> None:
        # E-GEOD-100833 is the microarray-dominated experiment from stage 2's
        # fixture index.
        r = route_client.get("/api/contrast/E-GEOD-100833/g2_g1/genes")
        assert r.status_code in (404, 409)  # 404 if that exact id isn't indexed
        if r.status_code == 409:
            assert r.json()["error"] == "microarray_not_drawable"

    def test_the_null_contrast_also_serves_a_table(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{NULL_ACC}/{NULL_CID}/genes")
        assert r.status_code == 200
        assert len(r.json()["rows"]) == 60
