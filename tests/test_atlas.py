"""Stage 1 — disease resolution and the Atlas contrast index. Offline.

Nothing here touches the network. Two devices make that true and provable rather
than merely intended:

* `NoNetwork`, an `Http` stand-in whose every method raises. Any test that hands
  it over is asserting that the code path did not need a request.
* `fixtures/atlas/ols-cache/`, the live OLS4 payloads stored as a real
  `cache.Cache` tree. Pointing the production `Cache` at it exercises the real
  cache key — source, schema version, query — so a schema bump invalidates the
  fixtures exactly as it invalidates the live cache, rather than leaving
  hand-shaped payloads answering for a parse that no longer exists.

**Where the plan turned out to be wrong, and these tests record the measurement
rather than the plan's number.** The plan's "done when" expects the fixture index
to yield *123 Crohn's contrasts across 9 experiments with 101 microarray*, which
are the spike's numbers. The spike found its experiments by regex-matching
"crohn" in the catalogue JSON and then filtering on the assay-group label prose.
The plan itself then requires the opposite method — filter by **ontology id on
the test group**, never by regex on the prose — and over all 369 human
differential experiments that method finds **138 contrasts across 17
experiments, 121 of them microarray**. Six experiments tag samples `EFO_0000384`
without the word "Crohn" appearing anywhere in their catalogue row, so the
spike's population was short. `101 of 138 from one experiment` is the dominance
figure and is unchanged; the spike's "101 microarray" and its "101 from
E-GEOD-100833" were the same number by coincidence, not the same fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from targetring import atlas_index as idx
from targetring import config, sources
from targetring.adapters import atlas as atlas_mod
from targetring.adapters.ols import DiseaseRef, OLSAdapter
from targetring.atlas_index import AtlasIndex, IndexMissing
from targetring.cache import Cache
from targetring.contrast import RNASEQ, ContrastIdentityError, ContrastRef
from targetring.http import HardFailure

FIXTURES = config.REPO_ROOT / "fixtures" / "atlas"
CROHNS_INDEX = FIXTURES / "index" / "crohns-index.json"
OLS_CACHE = FIXTURES / "ols-cache"

CROHNS_IDS = ("MONDO_0005011", "EFO_0000384")


class NoNetwork:
    """An `Http` that refuses to be used. Handing it over is the assertion."""

    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError(f"this test made a network request: GET {a!r} {k!r}")

    async def post_json(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError(f"this test made a network request: POST {a!r} {k!r}")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture()
def crohns_index() -> AtlasIndex:
    return AtlasIndex.load(CROHNS_INDEX)


@pytest.fixture()
def ols() -> OLSAdapter:
    """The real adapter over the recorded payloads. No network."""
    return OLSAdapter(NoNetwork(), Cache(root=OLS_CACHE))  # type: ignore[arg-type]


# ===========================================================================
# ContrastRef and I8
# ===========================================================================

class TestContrastRefAssertsItsIdentity:
    def _ref(self, **kw):
        base = dict(accession="E-GEOD-101794", contrast_id="g2_g1",
                    ref_group="g2", test_group="g1", ref_label="normal",
                    test_label="Crohn's disease", n_ref=12, n_test=40,
                    name="'Crohn's disease' vs 'normal'")
        return ContrastRef(**{**base, **kw})

    def test_the_id_is_reference_underscore_test(self) -> None:
        r = self._ref()
        assert r.contrast_id == f"{r.ref_group}_{r.test_group}"

    def test_an_id_that_reads_the_other_way_round_is_refused(self) -> None:
        """`g2_g1` means ref=g2, test=g1. Reading it left to right inverts every
        log2FC, and nothing on the wire says which token is the reference."""
        with pytest.raises(ContrastIdentityError) as e:
            self._ref(contrast_id="g1_g2")
        assert "g2_g1" in str(e.value)
        assert "inverts" in str(e.value)

    def test_it_is_an_assertion_error_so_the_route_aborts(self) -> None:
        assert issubclass(ContrastIdentityError, AssertionError)

    def test_a_side_with_no_assays_is_not_a_contrast(self) -> None:
        with pytest.raises(ContrastIdentityError):
            self._ref(n_test=0)

    def test_the_sign_sentence_names_the_test_group_as_up(self) -> None:
        r = self._ref()
        assert r.sign_sentence == (
            "positive log2FC = up in Crohn's disease (n=40) vs normal (n=12)")

    def test_the_sign_sentence_is_a_property_not_a_caller_s_string(self) -> None:
        """So the figure, the picker and the manifest cannot word it differently
        -- or, worse, identically with the labels swapped."""
        assert isinstance(type(self._ref()).sign_sentence, property)

    def test_it_is_frozen_so_nothing_downstream_can_swap_the_labels(self) -> None:
        r = self._ref()
        with pytest.raises(Exception):
            r.test_label = "normal"  # type: ignore[misc]

    def test_the_prose_name_is_carried_not_rebuilt(self) -> None:
        r = self._ref(name="'Crohn's disease, ileum' vs 'normal, ileum'")
        assert r.name == "'Crohn's disease, ileum' vs 'normal, ileum'"
        assert r.name not in r.sign_sentence


# ===========================================================================
# -configuration.xml
# ===========================================================================

class TestParseConfiguration:
    def test_it_reads_the_rnaseq_experiment_s_contrasts(self) -> None:
        cs = atlas_mod.parse_configuration(
            fixture_text("E-GEOD-101794-configuration.xml"), "E-GEOD-101794")
        assert [c.contrast_id for c in cs] == ["g2_g1", "g4_g3"]
        c = cs[0]
        assert c.ref_group == "g2" and c.test_group == "g1"
        assert c.platform == RNASEQ
        assert c.n_test == 61 and c.n_ref == 13
        assert c.name == ("'Crohn's disease' vs 'non inflammatory bowel disease "
                          "control' in 'A1a Paris classification'")

    def test_absent_array_design_means_rnaseq(self) -> None:
        cs = atlas_mod.parse_configuration(
            fixture_text("E-GEOD-101794-configuration.xml"), "E-GEOD-101794")
        assert all(c.platform == RNASEQ for c in cs)

    def test_a_microarray_experiment_carries_its_array_design(self) -> None:
        cs = atlas_mod.parse_configuration(
            fixture_text("E-GEOD-6731-configuration.xml"), "E-GEOD-6731")
        assert {c.platform for c in cs} == {"A-AFFY-1"}

    def test_groups_are_scoped_to_their_analytics_block(self) -> None:
        """A document-wide `iter("assay_group")` would let one block's `g1` supply
        the n for another block's contrast -- a wrong n per side on a picker whose
        whole job is showing n per side."""
        xml = """<configuration>
          <analytics><array_design>A-1</array_design><assay_groups>
            <assay_group id="g1" label="one"><assay>a1</assay></assay_group>
            <assay_group id="g2" label="two"><assay>b1</assay><assay>b2</assay></assay_group>
          </assay_groups><contrasts><contrast id="g2_g1"><name>n1</name>
            <reference_assay_group>g2</reference_assay_group>
            <test_assay_group>g1</test_assay_group></contrast></contrasts></analytics>
          <analytics><array_design>A-2</array_design><assay_groups>
            <assay_group id="g1" label="ONE"><assay>c1</assay><assay>c2</assay>
              <assay>c3</assay></assay_group>
            <assay_group id="g2" label="TWO"><assay>d1</assay></assay_group>
          </assay_groups><contrasts><contrast id="g2_g1"><name>n2</name>
            <reference_assay_group>g2</reference_assay_group>
            <test_assay_group>g1</test_assay_group></contrast></contrasts></analytics>
        </configuration>"""
        cs = atlas_mod.parse_configuration(xml, "E-TEST-1")
        assert [(c.platform, c.n_test, c.n_ref, c.test_label) for c in cs] == [
            ("A-1", 1, 2, "one"), ("A-2", 3, 1, "ONE")]

    def test_a_contrast_naming_a_group_the_block_lacks_is_a_hard_failure(self) -> None:
        xml = """<configuration><analytics><assay_groups>
            <assay_group id="g1" label="one"><assay>a1</assay></assay_group>
          </assay_groups><contrasts><contrast id="g2_g1"><name>n</name>
            <reference_assay_group>g2</reference_assay_group>
            <test_assay_group>g1</test_assay_group></contrast></contrasts>
        </analytics></configuration>"""
        with pytest.raises(HardFailure) as e:
            atlas_mod.parse_configuration(xml, "E-TEST-2")
        assert "wrong n per side" in str(e.value)

    def test_a_file_with_no_analytics_block_is_refused(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.parse_configuration("<configuration/>", "E-TEST-3")

    def test_unparseable_xml_is_a_hard_failure_not_an_empty_list(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.parse_configuration("<configuration><analytics>", "E-TEST-4")

    def test_an_invalid_id_is_reported_on_the_row_not_raised_by_the_parse(self) -> None:
        """The picker must SHOW such a contrast as unsafe. A parse that could only
        produce valid refs would have to throw the row away to report it."""
        cs = atlas_mod.parse_configuration(
            fixture_text("E-FAKE-1-configuration.xml"), "E-FAKE-1")
        assert [c.contrast_id for c in cs] == ["g2_g1", "g1_g2"]
        assert cs[0].invalid_reason == ""
        assert "sign of every log2FC is unestablished" in cs[1].invalid_reason
        # ...and it is only at `as_ref()` -- the point a figure would be drawn --
        # that it becomes a refusal.
        assert cs[0].as_ref().contrast_id == "g2_g1"
        with pytest.raises(ContrastIdentityError):
            cs[1].as_ref()


# ===========================================================================
# .condensed-sdrf.tsv
# ===========================================================================

class TestParseCondensedSdrf:
    def test_the_filename_uses_a_dot_not_a_dash(self) -> None:
        """One character. The dash form answers 404 with an Apache HTML body."""
        assert config.ATLAS_CONDENSED_SDRF_SUFFIX == ".condensed-sdrf.tsv"
        url = atlas_mod.condensed_sdrf_url("E-GEOD-101794")
        assert url.endswith("E-GEOD-101794.condensed-sdrf.tsv")
        assert "-condensed-sdrf" not in url

    def test_it_reads_per_assay_disease_ids(self) -> None:
        ann = atlas_mod.parse_condensed_sdrf(
            fixture_text("E-GEOD-101794.condensed-sdrf.tsv"), "E-GEOD-101794")
        a = ann["SRR5861974"]
        assert a.factors["disease"] == (
            "Crohn's disease", "http://www.ebi.ac.uk/efo/EFO_0000384")
        assert "EFO_0000384" in a.factor_uris_short()

    def test_six_and_seven_column_rows_are_both_valid(self) -> None:
        """A six-column row has no ontology URI, which is a fact about that
        annotation and not a missing value to fill in."""
        ann = atlas_mod.parse_condensed_sdrf(
            "E-T-1\t\ta1\tcharacteristic\tage\t31 year\n"
            "E-T-1\t\ta1\tfactor\tdisease\tx\thttp://x/EFO_1\n", "E-T-1")
        assert ann["a1"].characteristics["age"] == ("31 year", "")
        assert ann["a1"].factor_uris == {"http://x/EFO_1"}

    def test_a_wrong_column_count_is_refused(self) -> None:
        with pytest.raises(HardFailure) as e:
            atlas_mod.parse_condensed_sdrf("E-T-1\ta1\tfactor\tdisease\tx\n", "E-T-1")
        assert "columns" in str(e.value)

    def test_a_file_labelled_with_another_accession_is_refused(self) -> None:
        with pytest.raises(HardFailure) as e:
            atlas_mod.parse_condensed_sdrf(
                "E-OTHER\t\ta1\tfactor\tdisease\tx\thttp://x/EFO_1\n", "E-T-1")
        assert "wrong disease to every assay" in str(e.value)

    def test_an_unknown_annotation_kind_is_refused(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.parse_condensed_sdrf(
                "E-T-1\t\ta1\tsomething\tdisease\tx\thttp://x/EFO_1\n", "E-T-1")

    def test_factors_and_characteristics_are_kept_apart(self) -> None:
        """They are a different fact and are stored separately.

        Measured over the whole 2026-09-29 index: the test group's characteristic
        disease ids exceed its factor ids on **6 of 1,126** contrasts, four of them
        `E-GEOD-67196` (ALS patients' tissue, where the varied factor is something
        else). None of the six is a Crohn's contrast, so for Crohn's the two
        methods agree at 138 — which is why the distinction is worth *keeping* and
        not worth claiming credit for on this disease.
        """
        ann = atlas_mod.parse_condensed_sdrf(
            "E-T-1\t\ta1\tcharacteristic\tdisease\tCD\thttp://x/EFO_0000384\n"
            "E-T-1\t\ta1\tfactor\tdisease\tnormal\thttp://x/PATO_0000461\n", "E-T-1")
        a = ann["a1"]
        assert a.characteristic_uris_short() == {"EFO_0000384"}
        assert a.factor_uris_short() == {"PATO_0000461"}
        uris = atlas_mod.group_uris(ann, ["a1"], "disease")
        assert uris["factor_ids"] == ["PATO_0000461"]
        assert uris["characteristic_ids"] == ["EFO_0000384"]

    def test_an_assay_the_sdrf_does_not_annotate_is_reported(self) -> None:
        """A contrast filed under no disease disappears from every picker; the
        count of unannotated assays is how anybody finds out that it did."""
        ann = atlas_mod.parse_condensed_sdrf(
            "E-T-1\t\ta1\tfactor\tdisease\tCD\thttp://x/EFO_0000384\n", "E-T-1")
        uris = atlas_mod.group_uris(ann, ["a1", "a2", "a3"], "disease")
        assert uris["unannotated_assays"] == ["a2", "a3"]

    def test_short_form_strips_either_iri_prefix(self) -> None:
        assert atlas_mod.short_form(
            "http://www.ebi.ac.uk/efo/EFO_0000384") == "EFO_0000384"
        assert atlas_mod.short_form(
            "http://purl.obolibrary.org/obo/MONDO_0005011") == "MONDO_0005011"

    def test_a_file_annotating_nothing_is_refused(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.parse_condensed_sdrf("\n\n", "E-T-1")


# ===========================================================================
# URL building and catalogue selection
# ===========================================================================

class TestUrlsAndPopulation:
    def test_an_accession_shaped_thing_is_required_before_a_url_is_built(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.experiment_base("../../etc/passwd")
        with pytest.raises(HardFailure):
            atlas_mod.experiment_base("E-GEOD-101794/..")

    def test_the_two_analytics_layouts(self) -> None:
        assert atlas_mod.analytics_url("E-GEOD-101794").endswith(
            "E-GEOD-101794-analytics.tsv")
        assert atlas_mod.analytics_url("E-GEOD-6731", "A-AFFY-1").endswith(
            "E-GEOD-6731_A-AFFY-1-analytics.tsv")

    def test_the_population_is_human_differential_with_a_disease_factor(self) -> None:
        # `experimentType` is exactly `Differential` or `Baseline` in the live
        # catalogue (4,187 and 375 of the 4,562 rows, 2026-09-29). The platform is
        # in `technologyType`, not here.
        rows = [
            {"experimentAccession": "E-A-1", "species": "Homo sapiens",
             "experimentType": "Differential",
             "experimentalFactors": ["disease", "organism part"]},
            {"experimentAccession": "E-A-2", "species": "Mus musculus",
             "experimentType": "Differential",
             "experimentalFactors": ["disease"]},
            {"experimentAccession": "E-A-3", "species": "Homo sapiens",
             "experimentType": "Baseline",
             "experimentalFactors": ["disease"]},
            {"experimentAccession": "E-A-4", "species": "Homo sapiens",
             "experimentType": "Differential",
             "experimentalFactors": ["compound"]},
        ]
        assert atlas_mod.select_index_population(rows) == ["E-A-1"]

    def test_the_factor_test_is_on_the_name_because_that_is_all_there_is(self) -> None:
        """The catalogue carries factor NAMES. No ontology id appears in it at
        all, which is the whole reason the index exists."""
        row = {"experimentAccession": "E-A-1", "species": "Homo sapiens",
               "experimentType": "Differential",
               "experimentalFactors": ["Disease"]}
        assert atlas_mod.is_human_differential_disease(row)

    def test_a_catalogue_accession_this_build_will_not_use_stops_the_build(self) -> None:
        with pytest.raises(HardFailure):
            atlas_mod.select_index_population([
                {"experimentAccession": "E-GEOD-1/../x", "species": "Homo sapiens",
                 "experimentType": "Differential",
                 "experimentalFactors": ["disease"]}])


# ===========================================================================
# the index, built offline from the raw fixtures
# ===========================================================================

class TestIndexBuiltFromFixtures:
    """`build_index` end to end with a fake Http that serves the fixture files."""

    class FixtureHttp:
        """Serves `fixtures/atlas/` at the real URLs, with real content types.

        A response stand-in rather than an adapter stand-in, so the production
        `AtlasAdapter` -- including its content-type check, which is the thing that
        catches a 200-with-HTML -- is the code under test.
        """

        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str, params=None):  # noqa: ANN001, ANN201
            self.urls.append(url)
            name = url.rsplit("/", 1)[1]
            path = FIXTURES / name
            if not path.exists():
                raise HardFailure(
                    f"HTTP 404 from {url} (content-type='text/html', 250 B)")
            ctype = ("application/xml" if name.endswith(".xml")
                     else "text/tab-separated-values")
            body = path.read_text()

            class R:
                headers = {"content-type": ctype}
                text = body
                content = body.encode()
                request = type("Q", (), {"url": url})()
            return R()

    @pytest.fixture()
    def built(self, tmp_path):
        import asyncio

        http = self.FixtureHttp()
        index = asyncio.run(idx.build_index(
            http, Cache(root=tmp_path, enabled=False),
            accessions=["E-GEOD-101794", "E-GEOD-6731", "E-FAKE-1", "E-MISSING-1"]))
        return index, http

    def test_it_issues_two_gets_per_experiment(self, built) -> None:
        _index, http = built
        assert len(http.urls) == 8, "configuration.xml + .condensed-sdrf.tsv, each"
        assert sum(1 for u in http.urls if u.endswith("-configuration.xml")) == 4

    def test_an_experiment_that_fails_is_named_never_silently_skipped(self, built) -> None:
        index, _ = built
        assert [f["accession"] for f in index.failed] == ["E-MISSING-1"]
        assert "404" in index.failed[0]["reason"]
        assert index.n_experiments == 3

    def test_an_invalid_contrast_id_is_indexed_with_its_reason(self, built) -> None:
        index, _ = built
        bad = [c for c in index.contrasts if c.invalid_reason]
        assert [(c.accession, c.contrast_id) for c in bad] == [("E-FAKE-1", "g1_g2")]
        assert index.n_contrasts_invalid_id == 1

    def test_contrasts_are_filed_by_the_test_side_s_factor_ids(self, built) -> None:
        index, _ = built
        rows = index.contrasts_for(["EFO_0000384"])
        assert ("E-FAKE-1", "g2_g1") in [(c.accession, c.contrast_id) for c in rows]
        assert all("EFO_0000384" in c.test_disease_ids for c in rows)

    def test_the_index_round_trips_through_disk(self, built, tmp_path) -> None:
        index, _ = built
        p = index.save(tmp_path / "i.json")
        again = AtlasIndex.load(p)
        assert again.as_dict() == index.as_dict()

    def test_an_index_from_another_schema_is_refused_not_reinterpreted(
            self, built, tmp_path) -> None:
        index, _ = built
        p = index.save(tmp_path / "i.json")
        body = json.loads(p.read_text())
        body["schema_version"] = "0"
        p.write_text(json.dumps(body))
        with pytest.raises(IndexMissing) as e:
            AtlasIndex.load(p)
        assert "Rebuild it" in str(e.value)

    def test_a_missing_index_names_the_script_to_run(self, tmp_path) -> None:
        with pytest.raises(IndexMissing) as e:
            AtlasIndex.load(tmp_path / "nope.json")
        assert "scripts/build_atlas_index.py" in str(e.value)


# ===========================================================================
# the real Crohn's numbers
# ===========================================================================

class TestTheRealCrohnsNumbers:
    """The measurement, from the real index sliced into `fixtures/atlas/index/`.

    These are NOT the plan's expected 123 / 9 / 101. See the module docstring:
    the plan quoted the spike's regex-found population, and the plan's own
    mandated method -- ontology id on the test group, over all 369 human
    differential experiments -- finds more.
    """

    def test_the_counts(self, crohns_index) -> None:
        s = crohns_index.summary_for(CROHNS_IDS)
        assert (s.n_contrasts, s.n_experiments) == (138, 17)
        assert (s.n_rnaseq, s.n_microarray) == (17, 121)
        assert s.n_rnaseq + s.n_microarray == s.n_contrasts

    def test_the_count_string_is_built_from_the_index(self, crohns_index) -> None:
        s = crohns_index.summary_for(CROHNS_IDS)
        assert s.count_text == ("138 contrasts (17 RNA-seq, 121 microarray) in "
                                "17 experiments")

    def test_one_experiment_supplies_101_of_the_138(self, crohns_index) -> None:
        rows = crohns_index.contrasts_for(CROHNS_IDS)
        from collections import Counter
        acc, n = Counter(c.accession for c in rows).most_common(1)[0]
        assert (acc, n) == ("E-GEOD-100833", 101)

    def test_the_small_n_flags(self, crohns_index) -> None:
        rows = crohns_index.contrasts_for(CROHNS_IDS)
        assert sum(1 for c in rows if c.min_n < 8) == 75
        assert sum(1 for c in rows if c.min_n < 4) == 18
        assert min(c.min_n for c in rows) == 3

    def test_the_ulcerative_colitis_contrasts_are_excluded_by_ontology_id(
            self, crohns_index) -> None:
        """No UC and no generic-IBD id survives the filter.

        `E-GEOD-6731` is the clean demonstration: two contrasts against the same
        reference group, one test group tagged `EFO_0000384` and one tagged
        `EFO_0000729` (ulcerative colitis), and the labels on both are the bare
        strings `g2` and `g3` — so there is nothing in the prose to filter on even
        if a regex were wanted. The id is the only thing that separates them.
        """
        rows = crohns_index.contrasts_for(CROHNS_IDS)
        ids = {i for c in rows for i in c.test_disease_ids}
        assert "EFO_0000729" not in ids, "EFO_0000729 is ulcerative colitis"
        assert "EFO_0003767" not in ids, "EFO_0003767 is generic IBD"
        pair = [c for c in crohns_index.contrasts if c.accession == "E-GEOD-6731"]
        assert {c.contrast_id for c in pair} == {"g1_g2", "g1_g3"}
        kept = [c for c in rows if c.accession == "E-GEOD-6731"]
        assert [c.contrast_id for c in kept] == ["g1_g2"]
        assert [c.test_label for c in pair] == ["g2", "g3"], (
            "the prose is unusable here; the ontology id is not")

    def test_the_null_contrast_is_in_the_picker_because_it_belongs_there(
            self, crohns_index) -> None:
        """A correction to my own first reading of the spike.

        `E-GEOD-57945 g6_g4` is the spike's "null-ish" contrast and its label is
        `Crohn's disease; normal` — the `normal` is the *ileal inflammation* factor,
        not the disease. Its disease factor really is `EFO_0000384`, so it is a
        Crohn's contrast and the picker is right to list it. Filtering it out would
        be the index deciding which of a disease's contrasts a reader may see.

        What makes it dangerous is what its heatmap looks like, which is stage 4's
        problem and is why the plan's §8 calls it the single biggest risk. What the
        picker owes a reader is the label, the n per side (24 vs 42) and the flags —
        all of which it has.
        """
        rows = {(c.accession, c.contrast_id)
                for c in crohns_index.contrasts_for(CROHNS_IDS)}
        assert ("E-GEOD-57945", "g6_g4") in rows
        null = next(c for c in crohns_index.contrasts
                    if (c.accession, c.contrast_id) == ("E-GEOD-57945", "g6_g4"))
        assert null.test_label == "Crohn's disease; normal"
        assert (null.n_test, null.n_ref) == (24, 42)
        assert null.test_disease_ids == ("EFO_0000384",)
        # Its sibling `g6_g7` is ulcerative colitis against the same reference and
        # is excluded, so this is not "everything in the file passes".
        assert ("E-GEOD-57945", "g6_g7") not in rows

    def test_ordering_is_accession_then_contrast_id(self, crohns_index) -> None:
        rows = crohns_index.contrasts_for(CROHNS_IDS)
        assert [(c.accession, c.contrast_id) for c in rows] == sorted(
            (c.accession, c.contrast_id) for c in rows)

    def test_a_disease_atlas_has_never_studied_is_zero_and_still_selectable(
            self, crohns_index) -> None:
        s = crohns_index.summary_for(["MONDO_9999999"])
        assert s.n_contrasts == 0
        assert s.count_text == "0 contrasts"


class TestIndexStaleness:
    def test_an_index_older_than_thirty_days_is_flagged(self) -> None:
        old = AtlasIndex(built_utc="2026-01-01T00:00:00+00:00")
        assert old.is_stale()
        assert "STALE" in old.summary_for(["X"]).built_text

    def test_a_fresh_index_is_not(self, crohns_index) -> None:
        assert not crohns_index.is_stale()
        assert "STALE" not in crohns_index.summary_for(CROHNS_IDS).built_text

    def test_an_index_with_no_build_date_is_stale(self) -> None:
        assert AtlasIndex().is_stale()

    def test_the_build_date_is_in_every_count_string(self, crohns_index) -> None:
        s = crohns_index.summary_for(CROHNS_IDS)
        assert s.built_text.startswith("index built 2026-")

    def test_staleness_is_a_flag_and_never_a_refusal(self) -> None:
        """A month-old count is still the count that was measured; refusing would
        leave the picker with no numbers rather than with dated ones."""
        old = AtlasIndex(built_utc="2020-01-01T00:00:00+00:00",
                         contrasts=[idx.IndexedContrast(
                             accession="E-A-1", contrast_id="g2_g1", ref_group="g2",
                             test_group="g1", ref_label="n", test_label="d",
                             n_ref=3, n_test=3, name="x", platform=RNASEQ,
                             test_disease_ids=("EFO_1",))])
        assert old.summary_for(["EFO_1"]).n_contrasts == 1


# ===========================================================================
# OLS4 — two id spaces
# ===========================================================================

class TestDiseaseResolution:
    async def test_crohns_resolves_to_mondo_with_the_obsolete_efo_beside_it(
            self, ols) -> None:
        refs = await ols.resolve("Crohn's disease")
        top = refs[0]
        assert top.current_id == "MONDO_0005011"
        assert top.label == "Crohn disease"
        assert "EFO_0000384" in top.atlas_ids

    async def test_regional_enteritis_resolves_to_the_same_disease(self, ols) -> None:
        """A synonym of MONDO_0005011 and of the obsolete EFO term."""
        refs = await ols.resolve("regional enteritis")
        assert refs[0].current_id == "MONDO_0005011"
        assert "EFO_0000384" in refs[0].atlas_ids
        assert "regional enteritis" in refs[0].synonyms

    async def test_ibd_resolves_to_ibd_and_NOT_to_crohns(self, ols) -> None:
        """A plan correction. The plan's "done when" expects `IBD` to produce a ref
        whose `current_id` is `MONDO_0005011`; it does not, and it must not. IBD is
        `MONDO_0005265` (obsolete EFO `EFO_0003767`), a different disease with its
        own Atlas contrasts. Resolving a broader term to a narrower one would put
        generic-IBD samples in the Crohn's picker -- which is the 16 contrasts the
        spike excluded, arriving through the ontology layer instead."""
        refs = await ols.resolve("IBD")
        ids = {r.current_id for r in refs}
        assert "MONDO_0005265" in ids
        assert "MONDO_0005011" not in ids
        ibd = next(r for r in refs if r.current_id == "MONDO_0005265")
        assert "EFO_0003767" in ibd.atlas_ids

    async def test_the_obsolete_mapping_is_asserted_from_the_efo_side(self, ols) -> None:
        """The MONDO xref is a claim; `EFO_0000384`'s own obsoletion record is the
        evidence, and it is fetched and checked."""
        refs = await ols.resolve("Crohn's disease")
        note = " ".join(refs[0].mapping_notes)
        assert "EFO_0000384 is obsolete in EFO 3.88.0" in note
        assert "names MONDO_0005011 as its replacement" in note
        assert "NEVER sent to Open Targets" in note

    async def test_atlas_ids_always_contains_the_current_id(self, ols) -> None:
        """A superset, not an alternative: newer Atlas experiments tag with the
        current id and older ones with the obsolete one."""
        for r in await ols.resolve("Crohn's disease"):
            assert r.current_id in r.atlas_ids

    def test_a_ref_that_drops_its_own_current_id_is_refused(self) -> None:
        with pytest.raises(HardFailure) as e:
            DiseaseRef(label="x", current_id="MONDO_1", atlas_ids=("EFO_1",))
        assert "superset" in str(e.value)

    async def test_obsolete_atlas_ids_names_only_the_bridged_ones(self, ols) -> None:
        top = (await ols.resolve("Crohn's disease"))[0]
        assert top.obsolete_atlas_ids == ("EFO_0000384",)

    async def test_an_obsolete_id_cannot_be_used_as_a_current_id(self, ols) -> None:
        """`resolve_id("EFO_0000384")` is the mistake this whole module exists to
        stop: it is the Atlas-side id, and Open Targets returns null for it."""
        with pytest.raises(HardFailure) as e:
            await ols.resolve_id("EFO_0000384")
        assert "itself obsolete" in str(e.value)

    async def test_resolve_id_rebuilds_the_bridge_rather_than_trusting_the_browser(
            self, ols) -> None:
        r = await ols.resolve_id("MONDO_0005011")
        assert r.atlas_ids == ("MONDO_0005011", "EFO_0000384")
        assert r.mapping_notes

    async def test_an_id_outside_efo_and_mondo_is_refused(self, ols) -> None:
        with pytest.raises(HardFailure) as e:
            await ols.resolve_id("DOID_8778")
        assert "EFO or MONDO" in str(e.value)

    async def test_the_query_echo_is_asserted(self, ols, monkeypatch) -> None:
        """AGENTS.md rule 4. A Solr `q` with an unbalanced quote comes back 200
        with another query's results, and the echo is the only thing that tells
        those apart. This is also the plan's one correction: `/api/search` does
        NOT echo `q` and `/api/select` does, so this build uses `select`."""
        assert config.OLS4_SELECT.endswith("/select")

        class EchoLies:
            async def get(self, url, params=None):  # noqa: ANN001, ANN201
                class R:
                    headers = {"content-type": "application/json"}
                    request = type("Q", (), {"url": url})()

                    @staticmethod
                    def json():
                        return {"responseHeader": {"params": {"q": "something else"}},
                                "response": {"docs": []}}
                return R()

        bad = OLSAdapter(EchoLies(), Cache(enabled=False))  # type: ignore[arg-type]
        with pytest.raises(HardFailure) as e:
            await bad.select("Crohn's disease")
        assert "did not send" in str(e.value)

    async def test_the_tests_are_offline(self, ols) -> None:
        """`NoNetwork` raises on any request, so a cache miss would fail here
        rather than quietly reaching EBI from the test suite."""
        await ols.resolve("Crohn's disease")
        with pytest.raises(AssertionError, match="network request"):
            await ols.resolve("a query nobody recorded")


# ===========================================================================
# the autocomplete entry
# ===========================================================================

class TestAutocompleteEntry:
    async def test_the_line_carries_label_id_counts_and_build_date(
            self, ols, crohns_index) -> None:
        ref = (await ols.resolve("Crohn's disease"))[0]
        e = crohns_index.autocomplete_entry(ref)
        assert e["text"] == (
            "Crohn disease · MONDO_0005011 — 138 contrasts (17 RNA-seq, "
            "121 microarray) in 17 experiments · index built 2026-09-29")

    async def test_it_carries_both_id_spaces_separately(self, ols, crohns_index) -> None:
        ref = (await ols.resolve("Crohn's disease"))[0]
        e = crohns_index.autocomplete_entry(ref)
        assert e["current_id"] == "MONDO_0005011"
        assert e["obsolete_atlas_ids"] == ["EFO_0000384"]
        assert set(e["atlas_ids"]) == {"MONDO_0005011", "EFO_0000384"}

    async def test_zero_contrasts_is_shown_as_zero_and_stays_selectable(
            self, ols, crohns_index) -> None:
        ref = next(r for r in await ols.resolve("Crohn")
                   if r.current_id != "MONDO_0005011")
        e = crohns_index.autocomplete_entry(ref)
        assert "0 contrasts" in e["text"]
        assert e["current_id"]


# ===========================================================================
# the source registry
# ===========================================================================

@pytest.fixture()
def client(monkeypatch):
    """`TestClient` with the route's two collaborators pointed offline.

    `Http` is replaced by one that raises, and `Cache` by the recorded OLS4 tree,
    so a route that needed a request would fail here rather than calling EBI from
    the suite.
    """
    from fastapi.testclient import TestClient

    from targetring import app as app_mod

    class OfflineHttp:
        async def __aenter__(self):  # noqa: ANN204
            return NoNetwork()

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    monkeypatch.setattr(app_mod, "Http", OfflineHttp)
    monkeypatch.setattr(app_mod, "Cache", lambda *a, **k: Cache(root=OLS_CACHE))
    monkeypatch.setattr(app_mod.atlas_index, "index_path", lambda *a, **k: CROHNS_INDEX)
    monkeypatch.setattr(app_mod, "_index_cache", None)
    return TestClient(app_mod.app)


class TestDiseasesRoute:
    def test_it_returns_the_line_the_autocomplete_shows(self, client) -> None:
        r = client.get("/api/diseases", params={"q": "Crohn's disease", "limit": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["diseases"][0]["text"] == (
            "Crohn disease · MONDO_0005011 — 138 contrasts (17 RNA-seq, "
            "121 microarray) in 17 experiments · index built 2026-09-29")

    def test_it_carries_the_index_s_own_provenance(self, client) -> None:
        body = client.get("/api/diseases", params={"q": "Crohn"}).json()
        p = body["index"]
        assert p["n_contrasts"] == 166 and p["n_experiments"] == 17
        assert p["stale"] is False and p["n_failed_experiments"] == 0
        assert p["stale_after_days"] == 30

    def test_it_cites_both_new_sources(self, client) -> None:
        body = client.get("/api/diseases", params={"q": "Crohn"}).json()
        assert [s["source"] for s in body["sources"]] == ["ols.disease",
                                                          "atlas.contrast"]

    def test_an_empty_query_returns_nothing_and_says_so(self, client) -> None:
        body = client.get("/api/diseases", params={"q": "  "}).json()
        assert body["diseases"] == [] and body["note"]

    def test_a_missing_index_is_a_503_not_a_list_of_zeros(self, client,
                                                          monkeypatch, tmp_path) -> None:
        """"0 contrasts" from a missing index and "0 contrasts" from a disease
        Atlas has never studied are the same string and different facts."""
        from targetring import app as app_mod
        monkeypatch.setattr(app_mod.atlas_index, "index_path",
                            lambda *a, **k: tmp_path / "nope.json")
        monkeypatch.setattr(app_mod, "_index_cache", None)
        r = client.get("/api/diseases", params={"q": "Crohn"})
        assert r.status_code == 503
        assert r.json()["error"] == "index_missing"
        assert "build_atlas_index" in r.json()["message"]

    def test_an_ols_failure_is_a_502_and_not_a_partial_list(self, client,
                                                            monkeypatch) -> None:
        from targetring import app as app_mod

        class Failing:
            async def __aenter__(self):  # noqa: ANN204
                class H:
                    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                        raise HardFailure("OLS4 answered HTTP 503")
                return H()

            async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
                return False

        monkeypatch.setattr(app_mod, "Http", Failing)
        monkeypatch.setattr(app_mod, "Cache", lambda *a, **k: Cache(enabled=False))
        r = client.get("/api/diseases", params={"q": "Crohn's disease"})
        assert r.status_code == 502
        assert r.json()["error"] == "source_failure"
        assert "partial" in r.json()["note"]


class TestTheNewSourcesAreRegistered:
    def test_both_new_sources_have_an_entry(self) -> None:
        assert set(sources.DATA_SOURCE_DOCS) == {"atlas.contrast", "ols.disease"}

    def test_an_unregistered_source_is_refused(self) -> None:
        with pytest.raises(AssertionError) as e:
            sources.data_source_rows(["atlas.heatmap"])
        assert "do not borrow" in str(e.value)

    def test_the_structural_rules_cover_them(self) -> None:
        """https, a real path, a printed domain that matches the link's host, and
        complete manifest fields -- the same four rules as the rings."""
        sources.assert_registry_is_coherent()
        for key, doc in sources.DATA_SOURCE_DOCS.items():
            assert doc.url.startswith("https://"), key
            assert doc.url.split("/", 3)[3].strip("/"), key
            assert doc.endpoint and doc.version and doc.reads and doc.why, key

    def test_the_rows_carry_what_was_read(self) -> None:
        rows = sources.data_source_rows(["ols.disease", "atlas.contrast"])
        assert [r["source"] for r in rows] == ["ols.disease", "atlas.contrast"]
        assert "responseHeader.params.q" in rows[0]["reads"]
        assert "condensed-sdrf" in rows[1]["reads"]

    def test_the_manifest_names_both_endpoints(self) -> None:
        from targetring import manifest as manifest_mod
        assert "ols4_select" in manifest_mod.SOURCE_URLS
        assert "atlas_experiment" in manifest_mod.SOURCE_URLS

    def test_every_new_cache_source_has_a_schema_version(self) -> None:
        """`cache.Cache` raises on a source with no registered version, so this
        catches a new fetcher whose key would not invalidate."""
        for s in ("ols.select", "ols.term", "atlas.catalogue",
                  "atlas.configuration", "atlas.condensed_sdrf", "atlas.index"):
            assert s in config.SCHEMA_VERSIONS
            Cache(enabled=False)._path(s, "k")
