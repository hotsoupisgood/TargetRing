"""v2.2 item 4 — Pharos TDL (COUNCIL S1 / V8).

It arrived as a fifth Pareto axis, on S1's case that TDL is the one axis with no
missing values inside Pharos (Tbio 12303 + Tdark 5501 + Tchem 1904 + Tclin 704 =
20,412 = the whole target count). v2.5 removed the headline it was chosen to
rescue; the ring stayed, because it is a real measurement and a legible one.
Puck's condition is unchanged and is what most of this file tests: map
Tclin/Tchem/Tbio/Tdark to an **explicit** ordinal, never an inferred one.
"""

from __future__ import annotations

import pytest

from targetring.adapters.pharos import (
    TDL_MAX,
    TDL_ORDINAL,
    PharosAdapter,
    PharosRecord,
    build_query,
    tdl_cell,
)
from targetring.cache import Cache
from targetring.http import Http


class TestTheOrdinalIsExplicit:
    def test_all_four_levels_are_stated_and_ordered(self) -> None:
        assert TDL_ORDINAL == {"Tdark": 0, "Tbio": 1, "Tchem": 2, "Tclin": 3}
        assert TDL_MAX == 3

    def test_each_level_maps_to_its_stated_ordinal(self) -> None:
        for level, ordinal in TDL_ORDINAL.items():
            c = tdl_cell(PharosRecord("G", found=True, tdl=level))
            assert c.state == "value"
            assert c.value == float(ordinal)
            assert level in c.detail

    def test_an_unmapped_level_is_no_data_not_a_low_one(self) -> None:
        """The same rule CLINICAL_STAGE_ORDINAL follows, and the same defect it
        exists to prevent: v1 absorbed an unknown enum as `value(1.0)`."""
        c = tdl_cell(PharosRecord("G", found=True, tdl="Tsomethingnew"))
        assert c.state == "no_data"
        assert "Tsomethingnew" in c.detail
        assert "not a low one" in c.detail

    def test_the_ring_domain_matches_the_ordinal(self) -> None:
        from targetring.figure import RING_SPECS
        spec = next(r for r in RING_SPECS if r.key == "tdl")
        assert spec.domain == (0.0, float(TDL_MAX))


class TestAbsenceIsNotTdark:
    """Tdark is a measured level meaning 'nobody has looked at a target we do
    hold'. A gene Pharos does not hold at all is a different fact."""

    def test_a_gene_pharos_does_not_hold_is_no_data(self) -> None:
        c = tdl_cell(PharosRecord("MIR29B2CHG", found=False))
        assert c.state == "no_data"
        assert "not Tdark" in c.detail

    def test_tdark_itself_is_a_real_value(self) -> None:
        c = tdl_cell(PharosRecord("TMEM229B", found=True, tdl="Tdark"))
        assert c.state == "value" and c.value == 0.0

    def test_a_failed_call_is_failed_not_absent(self) -> None:
        c = tdl_cell(PharosRecord("G", error="ConnectTimeout"))
        assert c.state == "failed"
        assert "ConnectTimeout" in c.detail


class TestItIsARingAndNothingWeighsIt:
    def test_it_is_an_external_ring_and_counts_toward_k(self) -> None:
        from targetring.order import EXTERNAL_RINGS
        from targetring.run import FILTERABLE_RINGS
        assert "tdl" in EXTERNAL_RINGS
        assert "tdl" in FILTERABLE_RINGS

    def test_more_developed_is_the_stated_better_direction(self) -> None:
        """The sign survives ranking's removal because ordering the spokes by
        this one ring still has to know which end is which. It orders one ring at
        a time and combines nothing, so it cannot reintroduce M3's
        incommensurability."""
        from targetring.order import RING_PREFIX, RING_SIGN, order_for_figure
        from targetring.states import value
        assert RING_SIGN["tdl"] == +1
        spec = {"tdl": {"clin": value(3.0), "dark": value(0.0)}}
        assert order_for_figure(["dark", "clin"], spec,
                                f"{RING_PREFIX}tdl") == ["clin", "dark"]

    def test_the_figure_makes_no_axis_claim_about_it_any_more(self) -> None:
        """COUNCIL V5: a figure must not make a false claim about its own
        method. Three footer lines once said 'four axes'; there are no axes."""
        from targetring.figure import RETIRED_CLAIM_WORDS, RING_SPECS
        tdl = next(s for s in RING_SPECS if s.key == "tdl")
        text = f"{tdl.label} {tdl.source}".lower()
        for word in RETIRED_CLAIM_WORDS:
            assert word not in text


class TestTheJoinIsByIdentifier:
    """AGENTS.md rule 2. GraphQL aliases give this for free: the alias IS the
    requested index, so a target Pharos does not hold comes back as `gN: null`
    in its own slot rather than shortening an array."""

    def test_one_alias_per_requested_symbol(self) -> None:
        q = build_query(["BRAF", "TP53", "NOTAGENE"])
        assert q.count("target(q:") == 3
        for i in range(3):
            assert f"g{i}:target" in q

    def test_quotes_in_a_symbol_cannot_break_out_of_the_query(self) -> None:
        q = build_query(['BR"AF'])
        assert q == '{g0:target(q:{sym:"BRAF"}){sym tdl fam novelty}}'

    def test_it_can_be_keyed_on_uniprot_too(self) -> None:
        assert 'uniprot:"P15056"' in build_query(["P15056"], "uniprot")

    @pytest.mark.asyncio
    async def test_a_null_slot_does_not_shift_its_neighbours(self) -> None:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {
                "g0": {"sym": "BRAF", "tdl": "Tclin"},
                "g1": None,
                "g2": {"sym": "TP53", "tdl": "Tchem"},
            }}, headers={"content-type": "application/json"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        out = await PharosAdapter(Http(client), Cache(enabled=False)).fetch_many(
            ["BRAF", "GHOST", "TP53"])
        assert out["BRAF"].tdl == "Tclin"
        assert out["GHOST"].found is False
        assert out["TP53"].tdl == "Tchem", "the null must not shift TP53's answer"


@pytest.mark.live
class TestPharosLive:
    @pytest.mark.asyncio
    async def test_the_council_s1_measurements_still_hold(self) -> None:
        """S1 measured BRAF Tclin and a facet count of 20,412 summing exactly."""
        from targetring.config import PHAROS_GRAPHQL
        from targetring.http import graphql
        async with Http() as http:
            d = await graphql(http, PHAROS_GRAPHQL,
                              '{target(q:{sym:"BRAF"}){sym tdl}}')
            f = await graphql(http, PHAROS_GRAPHQL,
                              '{targets{count facets(include:["Target Development Level"])'
                              '{facet values{name value}}}}')
        assert d["target"]["tdl"] == "Tclin"
        counts = {v["name"]: v["value"]
                  for v in f["targets"]["facets"][0]["values"]}
        assert set(counts) == set(TDL_ORDINAL), (
            f"Pharos now uses levels {sorted(counts)}; the ordinal is stated and would "
            f"have to be revisited")
        assert sum(counts.values()) == f["targets"]["count"], (
            "S1's whole case is that TDL has no missing values inside Pharos")

    @pytest.mark.asyncio
    async def test_the_uniprot_fallback_finds_a_superseded_symbol(self) -> None:
        """Measured: Pharos returns null for SEPTIN9 and MARCHF1 — the current
        HGNC symbols for the two genes PLAN §4 names as Excel's date-corruption
        cases — and holds them under SEPT9 and MARCH1. A symbol-only lookup
        emptied this ring for exactly those genes, for a naming reason."""
        async with Http() as http:
            a = PharosAdapter(http, Cache(enabled=False))
            by_sym = await a.fetch_many(["SEPTIN9"])
            with_fallback = await a.fetch_many(["SEPTIN9"], {"SEPTIN9": "Q9UHD8"})
        assert by_sym["SEPTIN9"].found is False
        assert with_fallback["SEPTIN9"].found is True
        assert with_fallback["SEPTIN9"].pharos_symbol == "SEPT9"
        assert "uniprot" in tdl_cell(with_fallback["SEPTIN9"]).detail
