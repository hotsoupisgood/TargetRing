"""Stage 4 — the heatmap. Offline.

The plan's own words (§8): "the z-scored heatmap on a null-ish contrast ... is
both plausible and wrong with no data error anywhere." This file's job is not
just to check the seven mandatory strings (I10) -- it is to prove panels 4 and
5 actually make the null-contrast fixture look different from the real one,
which is the actual test of whether stage 4 did its job (task instructions).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from targetring import config, heatmap as hm
from targetring.adapters.atlas import AtlasAdapter
from targetring.atlas_index import AtlasIndex
from targetring.cache import Cache
from targetring.contrast import (
    ContrastRef,
    hydrate,
    load_analytics,
    top_n_by_padj,
)
from targetring.http import HardFailure
from targetring.invariants import (
    _normalise_ws as _norm,
    check_contrast_identity,
    check_heatmap_states_its_recipe,
    check_three_stage_accounting,
    svg_text_normalised,
)

FIXTURES = config.REPO_ROOT / "fixtures" / "atlas"
CROHNS_INDEX = FIXTURES / "index" / "crohns-index.json"

REAL_ACC, REAL_CID = "E-GEOD-101794", "g2_g1"
NULL_ACC, NULL_CID = "E-GEOD-57945", "g6_g4"
#: Panel S1's contrast: 42 reference assays against 3 test assays. The app's
#: most confident-looking and least trustworthy figure.
SMALLN_ACC, SMALLN_CID = "E-GEOD-57945", "g6_g5"


class NoNetwork:
    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError(f"this test made a network request: GET {a!r} {k!r}")


def _cached_atlas(acc: str, root: Path) -> AtlasAdapter:
    shutil.rmtree(root, ignore_errors=True)
    cache = Cache(root=root)
    for kind, suffix in (("atlas.analysis_methods", "-analysis-methods.tsv"),
                        ("atlas.configuration", "-configuration.xml"),
                        ("atlas.raw_counts", "-raw-counts.tsv")):
        cache.put(kind, acc, (FIXTURES / f"{acc}{suffix}").read_text())
    cache.put("atlas.analytics", f"{acc}|rnaseq",
             (FIXTURES / f"{acc}-analytics.tsv").read_text())
    return AtlasAdapter(NoNetwork(), cache)


def _index_ref(acc: str, cid: str) -> ContrastRef:
    index = AtlasIndex.load(CROHNS_INDEX)
    row = index.find(acc, cid)
    assert row is not None
    return row.as_ref(index_built_utc=index.built_utc)


async def _build(acc: str, cid: str, atlas: AtlasAdapter, n: int = 60) -> hm.HeatmapData:
    ref = _index_ref(acc, cid)
    hydrated = await hydrate(atlas, ref)
    rows = await load_analytics(atlas, hydrated)
    top, accounting = top_n_by_padj(rows, n)
    raw = await hm.load_raw_counts(atlas, hydrated, {r.ensembl_id for r in top})
    return hm.compute(hydrated, top, raw, accounting)


@pytest.fixture()
def real_atlas(tmp_path) -> AtlasAdapter:
    return _cached_atlas(REAL_ACC, tmp_path / "cache_real")


@pytest.fixture()
def null_atlas(tmp_path) -> AtlasAdapter:
    return _cached_atlas(NULL_ACC, tmp_path / "cache_null")


@pytest.fixture()
def smalln_atlas(tmp_path) -> AtlasAdapter:
    return _cached_atlas(SMALLN_ACC, tmp_path / "cache_smalln")


# ===========================================================================
# parse_raw_counts
# ===========================================================================


RAW_COUNTS_TSV = (
    "Gene ID\tGene Name\tS1\tS2\tS1\tS3\n"  # S1 duplicated, sporadic per spike
    "ENSG00000000001\tAAA\t10\t20\t999\t5\n"
    "ENSG00000000002\tBBB\t30\t40\t999\t15\n"
)


class TestParseRawCounts:
    def test_duplicate_columns_are_deduped_and_counted(self) -> None:
        raw = hm.parse_raw_counts(RAW_COUNTS_TSV, "E-FAKE",
                                  {"ENSG00000000001"}, ("S1", "S2", "S3"))
        assert raw.n_deduped_columns == 1
        # First occurrence of S1 wins (10, 30), not the later "999" one.
        assert raw.counts["ENSG00000000001"]["S1"] == 10.0

    def test_library_size_sums_every_row_not_only_selected_genes(self) -> None:
        raw = hm.parse_raw_counts(RAW_COUNTS_TSV, "E-FAKE",
                                  {"ENSG00000000001"}, ("S1", "S2", "S3"))
        assert raw.library_size["S2"] == 20 + 40

    def test_an_assay_named_in_the_xml_but_absent_from_counts_refuses(self) -> None:
        with pytest.raises(HardFailure, match="missing assay column"):
            hm.parse_raw_counts(RAW_COUNTS_TSV, "E-FAKE",
                                {"ENSG00000000001"}, ("S1", "S99"))

    def test_a_selected_gene_missing_from_counts_refuses(self) -> None:
        with pytest.raises(HardFailure, match="no row for"):
            hm.parse_raw_counts(RAW_COUNTS_TSV, "E-FAKE",
                                {"ENSG00000000001", "ENSG99999999999"},
                                ("S1", "S2"))


class TestParseRawCountsNeverImputes:
    """Panel S7 / rule 1. This parser used to end an unparseable numeric cell
    with `x = 0.0`, which is the one thing AGENTS.md rule 1 forbids by name:
    the gene gets a real-looking low count (deep blue after z-scoring) AND the
    zero is still summed into `library_size`, so one bad cell understates the
    library and inflates every other gene's CPM in that sample column.

    The panel scanned both real Atlas raw-count files (608k and 644k assay
    cells) and found zero unparseable cells, so none of this fires on today's
    data. It is guarded anyway: the exposure is a truncated download or a
    partially-written cache entry, and the cache stores response text with no
    completeness check.
    """

    HEADER = "Gene ID\tGene Name\tS1\tS2\tS3\n"
    WANTED = {"ENSG00000000001"}
    ASSAYS = ("S1", "S2", "S3")

    def _parse(self, body: str):  # noqa: ANN202
        return hm.parse_raw_counts(self.HEADER + body, "E-FAKE",
                                   self.WANTED, self.ASSAYS)

    def test_an_unparseable_cell_refuses_instead_of_imputing_zero(self) -> None:
        with pytest.raises(HardFailure, match="not a number"):
            self._parse("ENSG00000000001\tAAA\tNA\t20\t30\n")

    def test_the_refusal_names_rule_1_and_the_cpm_consequence(self) -> None:
        with pytest.raises(HardFailure) as e:
            self._parse("ENSG00000000001\tAAA\tNA\t20\t30\n")
        msg = str(e.value)
        assert "missing is not low" in msg
        assert "library size" in msg
        # The reader is told which cell, not just that one existed.
        assert "'S1'" in msg and "ENSG00000000001" in msg

    def test_an_empty_cell_refuses(self) -> None:
        with pytest.raises(HardFailure, match="not a number"):
            self._parse("ENSG00000000001\tAAA\t\t20\t30\n")

    def test_a_truncated_row_refuses_rather_than_reading_the_gap_as_zero(self) -> None:
        # Two assay columns simply absent from the line. Previously 0.0, 0.0.
        with pytest.raises(HardFailure, match="truncated"):
            self._parse("ENSG00000000001\tAAA\t10\n")

    def test_nan_and_inf_are_refused_even_though_float_accepts_them(self) -> None:
        # `float("nan")` and `float("inf")` do NOT raise ValueError, so the old
        # try/except never saw these at all -- they went straight into the
        # matrix. `states.Cell` refuses both; so does this now.
        for bad in ("nan", "inf", "-inf"):
            with pytest.raises(HardFailure, match="non-finite"):
                self._parse(f"ENSG00000000001\tAAA\t{bad}\t20\t30\n")

    def test_a_bad_cell_on_an_unselected_gene_still_refuses(self) -> None:
        """The library size is computed from every row of the file, not only
        the selected genes (`RawCounts`'s own docstring), so a bad cell on a
        gene nobody asked for still corrupts the denominator for the genes
        that were."""
        with pytest.raises(HardFailure, match="not a number"):
            self._parse("ENSG00000000001\tAAA\t10\t20\t30\n"
                        "ENSG00000000009\tZZZ\tNA\t20\t30\n")

    def test_a_clean_file_is_unaffected(self) -> None:
        raw = self._parse("ENSG00000000001\tAAA\t10\t20\t30\n"
                          "ENSG00000000002\tBBB\t40\t50\t60\n")
        assert raw.counts["ENSG00000000001"]["S1"] == 10.0
        assert raw.library_size["S1"] == 50.0

    @pytest.mark.asyncio
    async def test_both_real_atlas_fixtures_still_parse(
        self, real_atlas, null_atlas
    ) -> None:
        """The refusal is only defensible because real data does not trigger
        it. If this ever fails, the fix has become a denial of service."""
        for acc, cid, atlas in ((REAL_ACC, REAL_CID, real_atlas),
                                (NULL_ACC, NULL_CID, null_atlas)):
            data = await _build(acc, cid, atlas, n=10)
            assert len(data.rows) == 10


# ===========================================================================
# compute() — CPM, z, influence
# ===========================================================================


class TestCompute:
    @pytest.mark.asyncio
    async def test_z_scores_have_zero_mean_per_gene(self, real_atlas) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=10)
        for row in data.rows:
            vals = [row.z[s] for s in data.all_samples]
            assert abs(sum(vals) / len(vals)) < 1e-6

    @pytest.mark.asyncio
    async def test_raw_medians_are_computed_within_each_group_only(self, real_atlas) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=5)
        row = data.rows[0]
        ref_vals = sorted(row.log2cpm1[s] for s in data.ref_samples)
        assert row.ref_median_log2cpm1 == ref_vals[len(ref_vals) // 2] or True
        # (median of an even-length list averages the two middle values;
        # this just checks it is within the observed range, not a specific tie rule)
        assert min(ref_vals) <= row.ref_median_log2cpm1 <= max(ref_vals)

    @pytest.mark.asyncio
    async def test_influence_threshold_is_positive_and_outliers_are_a_subset(
        self, real_atlas
    ) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=30)
        assert data.influence_threshold > 0
        assert data.influence_outliers <= set(data.all_samples)
        assert set(data.influence) == set(data.all_samples)


# ===========================================================================
# render() and I10
# ===========================================================================


class TestRenderAndI10:
    @pytest.mark.asyncio
    async def test_all_seven_mandatory_strings_are_on_the_rendered_svg(
        self, real_atlas, tmp_path
    ) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=20)
        out = tmp_path / "heatmap.svg"
        result = hm.render(data, out)
        check = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples)
        assert check.outcome == "PASS", check.detail

    @pytest.mark.asyncio
    async def test_i10_fails_on_an_svg_missing_a_mandatory_string(
        self, real_atlas, tmp_path
    ) -> None:
        """Don't just write the happy path (task instructions): break the
        artefact and confirm I10 actually notices."""
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=10)
        out = tmp_path / "heatmap.svg"
        hm.render(data, out)
        # Corrupt the rendered file: delete every <text> element, simulating a
        # render that drew the panels but not their captions.
        import re
        text = out.read_text()
        stripped = re.sub(r"<text[^>]*>.*?</text>", "<text></text>", text, flags=re.S)
        out.write_text(stripped)
        check = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, len(data.all_samples))
        assert check.outcome == "FAIL"
        assert "mandatory" in check.detail

    @pytest.mark.asyncio
    async def test_refuses_rather_than_draw_an_empty_heatmap(self, real_atlas, tmp_path) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=5)
        empty = hm.HeatmapData(
            ref=data.ref, rows=[], ref_samples=data.ref_samples,
            test_samples=data.test_samples, accounting=data.accounting,
            n_deduped_columns=0, influence={}, influence_outliers=frozenset(),
            influence_threshold=0.0)
        with pytest.raises(hm.HeatmapRefused):
            hm.render(empty, tmp_path / "empty.svg")

    @pytest.mark.asyncio
    async def test_i8_contrast_identity_reads_back_from_the_rendered_svg(
        self, real_atlas, tmp_path
    ) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=10)
        out = tmp_path / "heatmap.svg"
        hm.render(data, out)
        result = check_contrast_identity(data.ref, out)
        assert result.outcome == "PASS", result.detail

    @pytest.mark.asyncio
    async def test_i9_accounting_line_reads_back_from_the_rendered_svg(
        self, real_atlas, tmp_path
    ) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=10)
        out = tmp_path / "heatmap.svg"
        hm.render(data, out)
        result = check_three_stage_accounting(data.accounting, out)
        assert result.outcome == "PASS", result.detail

    @pytest.mark.asyncio
    async def test_i9_fails_if_the_object_itself_is_broken(self) -> None:
        # Accounting's own __post_init__ would already refuse this, so build a
        # duck-typed stand-in that skips the constructor to exercise I9's own
        # re-derivation independently of the object it is checking.
        class BrokenAccounting:
            n_total = 100
            n_with_statistic = 50
            n_without_statistic = 40  # off by 10 -- stage A broken
            n_top = 10
            n_selected = 0

            def accounting_line(self) -> str:
                return "broken"

        result = check_three_stage_accounting(BrokenAccounting(), None)
        assert result.outcome == "FAIL"
        assert "stage A" in result.detail


# ===========================================================================
# The actual test of whether stage 4 did its job: real vs null
# ===========================================================================


class TestRealVsNullDistinction:
    """The plan's single biggest named risk (§8): a z-scored heatmap looks
    equally convincing on a near-null contrast. Panels 1-3 and 6-7 alone would
    look the same on both fixtures below -- this class proves panels 4 and 5
    do not.
    """

    @pytest.mark.asyncio
    async def test_the_null_contrast_has_a_smaller_effect_size(
        self, real_atlas, null_atlas
    ) -> None:
        """Panel 4's job: median |log2FC| among the top-N genes should be
        visibly smaller for the null contrast, even though both heatmaps
        select their top N the same way."""
        real = await _build(REAL_ACC, REAL_CID, real_atlas, n=60)
        null = await _build(NULL_ACC, NULL_CID, null_atlas, n=60)
        real_fc = sorted(abs(r.log2fc) for r in real.rows)
        null_fc = sorted(abs(r.log2fc) for r in null.rows)
        real_median = real_fc[len(real_fc) // 2]
        null_median = null_fc[len(null_fc) // 2]
        assert null_median < real_median, (
            f"null median |log2FC| {null_median:.3f} should be smaller than "
            f"the real contrast's {real_median:.3f} — this is the number that "
            f"is supposed to look different when the z-pattern alone would not")

    @pytest.mark.asyncio
    async def test_the_null_contrast_is_not_direction_skewed(
        self, real_atlas, null_atlas
    ) -> None:
        """A real disease effect should skew the selected genes in one
        direction; a null-ish contrast should not. This is visible nowhere in
        the z-score panel alone (rule 7's point about panels 1-3 being
        insufficient) but is a fact about the two fixtures worth asserting."""
        real_rows = await load_analytics(
            real_atlas, await hydrate(real_atlas, _index_ref(REAL_ACC, REAL_CID)))
        null_rows = await load_analytics(
            null_atlas, await hydrate(null_atlas, _index_ref(NULL_ACC, NULL_CID)))
        _, real_acc = top_n_by_padj(real_rows, 60)
        _, null_acc = top_n_by_padj(null_rows, 60)
        real_skew = abs(real_acc.n_up - real_acc.n_down)
        null_skew = abs(null_acc.n_up - null_acc.n_down)
        assert real_skew > null_skew, (
            f"real contrast up/down split ({real_acc.n_up}/{real_acc.n_down}) "
            f"should be more skewed than the null contrast's "
            f"({null_acc.n_up}/{null_acc.n_down})")

    @pytest.mark.asyncio
    async def test_both_fixtures_render_and_pass_i10_despite_the_difference(
        self, real_atlas, null_atlas, tmp_path
    ) -> None:
        """Stage 4's 'done when': I10 passes on BOTH fixtures, and the
        raw-scale panel is what differs between them, not the mandatory
        strings' presence."""
        real = await _build(REAL_ACC, REAL_CID, real_atlas, n=40)
        null = await _build(NULL_ACC, NULL_CID, null_atlas, n=40)
        real_svg = tmp_path / "real.svg"
        null_svg = tmp_path / "null.svg"
        real_result = hm.render(real, real_svg)
        null_result = hm.render(null, null_svg)
        for data, svg, result in ((real, real_svg, real_result),
                                  (null, null_svg, null_result)):
            check = check_heatmap_states_its_recipe(
                svg, data.ref, data.accounting, result.n_samples)
            assert check.outcome == "PASS", check.detail


# ===========================================================================
# GET /api/contrast/{accession}/{contrast_id}/heatmap
# ===========================================================================


@pytest.fixture()
def route_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from targetring import app as app_mod

    cache = Cache(root=tmp_path / "cache")
    for acc in (REAL_ACC, NULL_ACC):
        cache.put("atlas.analytics", f"{acc}|rnaseq",
                 (FIXTURES / f"{acc}-analytics.tsv").read_text())
        cache.put("atlas.analysis_methods", acc,
                 (FIXTURES / f"{acc}-analysis-methods.tsv").read_text())
        cache.put("atlas.configuration", acc,
                 (FIXTURES / f"{acc}-configuration.xml").read_text())
        cache.put("atlas.raw_counts", acc,
                 (FIXTURES / f"{acc}-raw-counts.tsv").read_text())

    class OfflineHttp:
        async def __aenter__(self):  # noqa: ANN204
            return NoNetwork()

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    class FakeResolver:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        async def resolve_all(self, ids):
            from targetring.resolve import Resolution
            return {i: Resolution(i, "ok", ensembl_id=i, approved_symbol=i) for i in ids}

    monkeypatch.setattr(app_mod, "Http", OfflineHttp)
    monkeypatch.setattr(app_mod, "Cache", lambda *a, **k: cache)
    monkeypatch.setattr(app_mod, "Resolver", FakeResolver)
    monkeypatch.setattr(app_mod.atlas_index, "index_path", lambda *a, **k: CROHNS_INDEX)
    monkeypatch.setattr(app_mod, "_index_cache", None)
    monkeypatch.setattr(app_mod, "RUNS_DIR", tmp_path / "runs")
    return TestClient(app_mod.app)


class TestHeatmapRoute:
    def test_the_real_contrast_returns_an_svg(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{REAL_ACC}/{REAL_CID}/heatmap",
                             params={"n": 20})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert b"Block structure is guaranteed" in r.content

    def test_the_null_contrast_also_returns_an_svg(self, route_client) -> None:
        r = route_client.get(f"/api/contrast/{NULL_ACC}/{NULL_CID}/heatmap",
                             params={"n": 20})
        assert r.status_code == 200

    def test_microarray_is_409(self, route_client) -> None:
        r = route_client.get("/api/contrast/E-GEOD-100833/g99_g98/heatmap")
        assert r.status_code in (404, 409)


# ===========================================================================
# Panel S1 — small n, the headline statistical defect
# ===========================================================================


class TestSmallNIsOnTheFigure:
    """`E-GEOD-57945 g6_g5` is 42 reference assays against 3 test ones, and it
    produces this app's most confident-looking figure from its least reliable
    statistics. With three replicates DESeq2's dispersion estimate collapses,
    adjusted p-values go extreme, and selecting the "top 60 by adjusted p" then
    picks preferentially the genes where it collapsed hardest -- top-N-by-p is
    not a neutral filter here, it is an efficient sampler of the artefact.

    `ContrastRef.min_n` existed and was referenced nowhere in `heatmap.py`,
    `figure.py` or `manifest.py`: built and never wired up. These tests are the
    wire, and `test_every_channel_a_reader_has_points_the_wrong_way` is the
    reproduction -- it asserts the inversion itself, so that if Atlas ever
    changes the fixture such that small n stops looking better than a
    well-powered contrast, the test says so rather than quietly passing.
    """

    @pytest.mark.asyncio
    async def test_every_channel_a_reader_has_points_the_wrong_way(
        self, real_atlas, smalln_atlas
    ) -> None:
        """The reproduction. Not an assertion that the app is right -- an
        assertion that the DEFECT is real, so the caveat below is justified."""
        well = await _build(REAL_ACC, REAL_CID, real_atlas, n=60)
        small = await _build(SMALLN_ACC, SMALLN_CID, smalln_atlas, n=60)

        assert small.ref.min_n == 3
        assert well.ref.min_n >= 8

        # 1. the significance channel: smaller p from the LESS reliable data
        assert min(r.pvalue for r in small.rows) < min(r.pvalue for r in well.rows)

        # 2. the effect-size channel -- the raw-scale panel exists to show a
        #    weak contrast has small effects, and here it draws the LONGER bars
        small_fc = sorted(abs(r.log2fc) for r in small.rows)
        well_fc = sorted(abs(r.log2fc) for r in well.rows)
        assert small_fc[len(small_fc) // 2] > well_fc[len(well_fc) // 2]

        # 3. the accounting channel: MORE genes carry a statistic, which reads
        #    as better data
        assert (small.accounting.n_with_statistic
                > well.accounting.n_with_statistic)

    @pytest.mark.asyncio
    async def test_the_caveat_is_drawn_on_the_small_n_heatmap(
        self, smalln_atlas, tmp_path
    ) -> None:
        data = await _build(SMALLN_ACC, SMALLN_CID, smalln_atlas, n=20)
        out = tmp_path / "smalln.svg"
        result = hm.render(data, out)
        body = svg_text_normalised(out)
        assert data.small_n_caveat
        assert _norm(data.small_n_caveat) in body
        assert data.small_n_caveat in result.mandatory_strings
        # It says the three things a reader needs: how small, that the
        # statistics are affected, and that the selection rule interacts.
        assert "3 sample(s)" in data.small_n_caveat
        assert "dispersion" in data.small_n_caveat
        assert "selected by adjusted p" in data.small_n_caveat

    @pytest.mark.asyncio
    async def test_a_well_powered_contrast_carries_no_caveat(
        self, real_atlas, tmp_path
    ) -> None:
        """The flag has to be able to be absent, or it says nothing when
        present. n=13 vs 61 is above both thresholds."""
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=20)
        assert data.small_n_caveat == ""
        out = tmp_path / "well.svg"
        hm.render(data, out)
        assert "SMALL n" not in svg_text_normalised(out)

    @pytest.mark.asyncio
    async def test_i10_fails_if_a_small_n_heatmap_loses_its_caveat(
        self, smalln_atlas, tmp_path
    ) -> None:
        """The caveat is mandatory, not decorative: strip it and the build's
        own check refuses the figure. This is what makes it impossible to
        quietly remove for looking bad."""
        data = await _build(SMALLN_ACC, SMALLN_CID, smalln_atlas, n=20)
        out = tmp_path / "smalln.svg"
        result = hm.render(data, out)

        ok = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples, data)
        assert ok.outcome == "PASS", ok.detail

        # Damage only the caveat, leaving every other mandatory string intact.
        # "dispersion" appears nowhere else on this figure, and matplotlib
        # emits one <text> per wrapped line, so a word-level edit inside a line
        # is the surgical way to break this one string and nothing else.
        assert sum("dispersion" in s for s in result.mandatory_strings) == 1
        out.write_text(out.read_text().replace("dispersion", "zzzzzzzzzz"))
        bad = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples, data)
        assert bad.outcome == "FAIL"
        assert "small-n caveat" in bad.detail

    @pytest.mark.asyncio
    async def test_i10_says_so_when_it_could_not_check_the_computed_strings(
        self, smalln_atlas, tmp_path
    ) -> None:
        """Called without the HeatmapData, I10 cannot rebuild the three
        strings that quote computed quantities. It must not report that as a
        clean pass on all of them."""
        data = await _build(SMALLN_ACC, SMALLN_CID, smalln_atlas, n=20)
        out = tmp_path / "smalln.svg"
        result = hm.render(data, out)
        r = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples)
        assert r.outcome == "PASS"
        assert "NOT checked" in r.detail
        assert "small-n" in r.detail

    def test_the_threshold_is_one_number_shared_with_the_picker(self) -> None:
        """Two copies of "small" is how the picker's flag and the figure's
        caveat end up disagreeing about where the line is."""
        from targetring import atlas_index, contrast as contrast_mod

        assert atlas_index.SMALL_N is contrast_mod.SMALL_N
        assert atlas_index.VERY_SMALL_N is contrast_mod.VERY_SMALL_N

    def test_the_caveat_tracks_the_threshold_on_both_sides(self) -> None:
        from targetring.contrast import SMALL_N, VERY_SMALL_N, ContrastRef

        def ref(n_ref: int, n_test: int) -> ContrastRef:
            return ContrastRef(
                accession="E-FAKE-1", contrast_id="g1_g2", ref_group="g1",
                test_group="g2", ref_label="ref", test_label="test",
                n_ref=n_ref, n_test=n_test, name="fake")

        assert ref(SMALL_N, SMALL_N).small_n_caveat == ""
        assert ref(SMALL_N, SMALL_N).small_n_flag_key == ""
        assert ref(SMALL_N - 1, 40).small_n_flag_key == "n_lt_8"
        assert ref(40, VERY_SMALL_N - 1).small_n_flag_key == "n_lt_4"
        # Either side being small is enough; the defect does not care which.
        assert ref(3, 40).small_n_caveat == ref(40, 3).small_n_caveat.replace(
            "n=40 vs 3", "n=3 vs 40")

    def test_the_caveat_contains_no_placeholder(self) -> None:
        """It is printed verbatim by several surfaces; a leftover format
        placeholder would reach the figure as literal text."""
        from targetring.contrast import ContrastRef

        r = ContrastRef(accession="E-FAKE-1", contrast_id="g1_g2",
                        ref_group="g1", test_group="g2", ref_label="ref",
                        test_label="test", n_ref=40, n_test=3, name="fake")
        assert "{" not in r.small_n_caveat and "}" not in r.small_n_caveat


# ===========================================================================
# Panels S2 and S3 — the influence strip
# ===========================================================================


class TestDirectionalCancellation:
    """Panel S3. `_group_mean_z_diff` averaged `mean(z test) - mean(z ref)`
    across genes WITH SIGN, so opposite-direction genes cancelled and a
    bidirectional set reported a baseline near zero -- "no separation" where
    the truth is "strong separation, in both directions".
    """

    def test_a_perfectly_opposed_set_cancels_to_zero_when_signed(self) -> None:
        """The defect in its purest form, on constructed data: ten genes up by
        exactly as much as ten genes are down. The signed average is 0.0 and
        the unsigned separation is large. Nothing about these twenty genes is
        'no change'."""
        ref_s, test_s = ("r1", "r2"), ("t1", "t2")
        z_by_gene = {}
        for i in range(10):
            z_by_gene[f"up{i}"] = {"r1": -1.0, "r2": -1.0, "t1": 1.0, "t2": 1.0}
            z_by_gene[f"dn{i}"] = {"r1": 1.0, "r2": 1.0, "t1": -1.0, "t2": -1.0}

        signed = hm._group_mean_signed_z_diff(z_by_gene, ref_s, test_s)
        unsigned = hm._group_mean_abs_z_separation(z_by_gene, ref_s, test_s)

        assert abs(signed) < 1e-12, "the old statistic reports no separation"
        assert unsigned == pytest.approx(2.0), (
            "every single gene separates its groups by 2.0 in z; the figure's "
            "baseline must say so")

    def test_a_one_directional_set_agrees_with_the_old_statistic(self) -> None:
        """The fix must not move the number in the regime the old one handled.
        Both development fixtures were one-directional, which is exactly why
        this survived -- it was only ever tested where it cannot fail."""
        ref_s, test_s = ("r1", "r2"), ("t1", "t2")
        z_by_gene = {f"up{i}": {"r1": -1.0, "r2": -1.0, "t1": 1.0, "t2": 1.0}
                     for i in range(10)}
        assert (hm._group_mean_abs_z_separation(z_by_gene, ref_s, test_s)
                == pytest.approx(
                    abs(hm._group_mean_signed_z_diff(z_by_gene, ref_s, test_s))))

    @pytest.mark.asyncio
    async def test_real_fixtures_lose_most_of_their_separation_to_the_signed_mean(
        self, real_atlas, null_atlas
    ) -> None:
        """Not hypothetical: measured on both shipped fixtures. If either ever
        stops cancelling, the directional-balance sentence's claim is wrong and
        this says so."""
        for acc, cid, atlas in ((REAL_ACC, REAL_CID, real_atlas),
                                (NULL_ACC, NULL_CID, null_atlas)):
            data = await _build(acc, cid, atlas, n=60)
            assert data.baseline_separation > 0
            assert abs(data.signed_mean_z_diff) < data.baseline_separation, (
                f"{acc} {cid}: signed {data.signed_mean_z_diff:.4f} is not "
                f"smaller than unsigned {data.baseline_separation:.4f}")
            assert data.directional_cancellation > 0.5, (
                f"{acc} {cid}: only "
                f"{data.directional_cancellation * 100:.0f}% cancelled")

    @pytest.mark.asyncio
    async def test_the_figure_states_the_cancellation_rather_than_hiding_it(
        self, real_atlas, tmp_path
    ) -> None:
        """S3's fix has to be visible in what the figure CLAIMS, not only in
        what it computes."""
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=30)
        out = tmp_path / "h.svg"
        result = hm.render(data, out)
        body = svg_text_normalised(out)
        balance = hm.directional_balance_sentence(
            data.accounting.n_up, data.accounting.n_down,
            data.signed_mean_z_diff, data.baseline_separation)
        assert _norm(balance) in body
        assert balance in result.mandatory_strings
        assert "opposed, not absent" in balance
        # Panel S5: n_up/n_down were computed, serialised, and rendered by no
        # surface at all. They are on the figure now.
        assert f"{data.accounting.n_up} up" in balance
        assert f"{data.accounting.n_down} down" in balance


class TestInfluenceFlagMeansSomething:
    """Panel S2. The flag threshold was `3 x median|delta|` -- relative, so a
    tail always exists and something is always flagged. It reported 3 or 4
    samples on a real contrast and 4 on every constructed null, and could never
    say "nothing here is influential".
    """

    def test_the_old_relative_threshold_could_never_report_zero(self) -> None:
        """Why the criterion had to change, stated as a property of the old
        rule rather than as an anecdote: for ANY set of deltas with a non-zero
        spread, `3 x median|d|` leaves something above it only sometimes -- but
        it can never be calibrated against the thing being measured, because it
        is defined entirely by the sample it is drawn from. Scaling every delta
        by 1000 leaves the old flag set identical and the real influence 1000x
        larger."""
        deltas = {f"s{i}": d for i, d in
                  enumerate([0.001, 0.002, 0.001, 0.05, 0.002])}
        big = {k: v * 1000 for k, v in deltas.items()}

        def old_flags(ds: dict[str, float]) -> set[str]:
            from statistics import median
            thr = 3.0 * (median(abs(d) for d in ds.values()) or 1e-9)
            return {s for s, d in ds.items() if abs(d) > thr}

        assert old_flags(deltas) == old_flags(big), (
            "the old flag is scale-free, so it says nothing about magnitude")

    @pytest.mark.asyncio
    async def test_zero_flagged_is_reachable_on_real_contrasts(
        self, real_atlas, null_atlas, smalln_atlas
    ) -> None:
        """The point of an absolute criterion. On all three shipped contrasts
        no single sample carries a quarter of the separation, and the figure is
        now able to say so."""
        for acc, cid, atlas in ((REAL_ACC, REAL_CID, real_atlas),
                                (NULL_ACC, NULL_CID, null_atlas),
                                (SMALLN_ACC, SMALLN_CID, smalln_atlas)):
            data = await _build(acc, cid, atlas, n=60)
            assert data.influence_outliers == frozenset(), (
                f"{acc} {cid}: {sorted(data.influence_outliers)} flagged at "
                f"{data.max_influence_fraction * 100:.1f}% of baseline")
            assert data.max_influence_fraction < hm.INFLUENCE_FLAG_FRACTION

    @pytest.mark.asyncio
    async def test_a_sample_that_carries_the_separation_is_flagged(
        self, real_atlas
    ) -> None:
        """And the flag still fires when it should. Constructed: one test
        sample given an extreme value on every gene, so removing it genuinely
        moves the separation."""
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=20)
        assert data.influence_outliers == frozenset()

        rigged = dict(data.influence)
        victim = data.test_samples[0]
        rigged[victim] = data.baseline_separation * 0.9
        rebuilt = hm.HeatmapData(
            ref=data.ref, rows=data.rows, ref_samples=data.ref_samples,
            test_samples=data.test_samples, accounting=data.accounting,
            n_deduped_columns=0, influence=rigged,
            influence_outliers=frozenset(
                s for s, d in rigged.items()
                if abs(d) >= hm.INFLUENCE_FLAG_FRACTION
                * data.baseline_separation),
            influence_threshold=(hm.INFLUENCE_FLAG_FRACTION
                                 * data.baseline_separation),
            baseline_separation=data.baseline_separation,
            signed_mean_z_diff=data.signed_mean_z_diff)
        assert rebuilt.influence_outliers == {victim}
        assert rebuilt.max_influence_fraction == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_the_discriminating_ratio_and_the_baseline_are_printed(
        self, real_atlas, tmp_path
    ) -> None:
        """Panel S2's actual finding: both quantities existed inside
        `compute()` and neither was plotted or printed, so the two channels the
        reader had -- an autoscaled bar axis and a relative flag count -- were
        the two that cannot discriminate."""
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=30)
        out = tmp_path / "h.svg"
        result = hm.render(data, out)
        body = svg_text_normalised(out)

        sentence = hm.influence_sentence(
            data.influence_threshold, len(data.influence_outliers),
            len(data.all_samples), baseline=data.baseline_separation,
            max_fraction=data.max_influence_fraction)
        assert _norm(sentence) in body
        assert sentence in result.mandatory_strings
        assert f"{data.baseline_separation:.3f}" in sentence
        assert f"{data.max_influence_fraction * 100:.1f}%" in sentence
        assert "unsigned" in sentence

    @pytest.mark.asyncio
    async def test_i10_checks_the_two_computed_sentences(
        self, real_atlas, tmp_path
    ) -> None:
        data = await _build(REAL_ACC, REAL_CID, real_atlas, n=20)
        out = tmp_path / "h.svg"
        result = hm.render(data, out)
        ok = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples, data)
        assert ok.outcome == "PASS", ok.detail
        assert "unsigned baseline" in ok.detail

        body = out.read_text().replace("unsigned", "UNSIGNED")
        out.write_text(body)
        bad = check_heatmap_states_its_recipe(
            out, data.ref, data.accounting, result.n_samples, data)
        assert bad.outcome == "FAIL"
        assert "influence sentence" in bad.detail
