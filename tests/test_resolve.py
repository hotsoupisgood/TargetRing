"""Resolver tests. The offline ones are the contract; the `live` ones hit the
network and are the re-verification QA B6 asked for on the unprobed hops.

Run offline only:  pytest -m "not live"
"""

from __future__ import annotations

import pytest

from targetring.cache import Cache
from targetring.http import Http
from targetring.resolve import Resolver, classify_input, looks_mouse


class TestExcelCorruption:
    """QA B7: unrecoverable, and the message must tell the user to re-export."""

    @pytest.mark.parametrize("bad", ["9-Sep", "1-Mar", "2-Mar", "1-Dec", "Sep-09", "Mar-01"])
    def test_detected_before_resolution(self, bad: str) -> None:
        _, status, note = classify_input(bad)
        assert status == "excel_corrupt"
        assert "re-export" in note.lower()
        assert "not found" not in note.lower()

    @pytest.mark.parametrize("good", ["SEPT9", "MARCH1", "BRAF", "TP53", "MARCHF1"])
    def test_real_symbols_not_flagged(self, good: str) -> None:
        _, status, _ = classify_input(good)
        assert status is None


class TestEnsemblVersionSuffix:
    """QA N6: strip the version; the versioned form is unverified against OT."""

    def test_stripped(self) -> None:
        cleaned, status, _ = classify_input("ENSG00000157764.14")
        assert cleaned == "ENSG00000157764"
        assert status is None

    def test_bare_passes_through(self) -> None:
        assert classify_input("ENSG00000157764")[0] == "ENSG00000157764"

    def test_whitespace_and_quotes_stripped(self) -> None:
        assert classify_input('  "BRAF" ')[0] == "BRAF"


class TestMouseHeuristic:
    """QA N5: `braf` resolves to human BRAF, so case cannot reject — only flag."""

    @pytest.mark.parametrize("sym", ["Braf", "Trp53", "Sept9"])
    def test_mouse_styled(self, sym: str) -> None:
        assert looks_mouse(sym)

    @pytest.mark.parametrize("sym", ["BRAF", "TP53", "SEPT9", "MARCHF1"])
    def test_human_styled(self, sym: str) -> None:
        assert not looks_mouse(sym)


@pytest.mark.live
class TestLiveResolution:
    @pytest.mark.asyncio
    async def test_renamed_genes_survive(self) -> None:
        """QA B5: `SEPT9` and `MARCH1` must both render, flagged `renamed`."""
        async with Http() as http:
            out = await Resolver(http, Cache()).resolve_all(["SEPT9", "MARCH1"])
        assert out["SEPT9"].status == "renamed"
        assert out["SEPT9"].approved_symbol == "SEPTIN9"
        assert out["SEPT9"].ensembl_id == "ENSG00000184640"
        assert out["MARCH1"].approved_symbol == "MARCHF1"
        assert out["MARCH1"].ensembl_id == "ENSG00000145416"
        for r in out.values():
            assert r.usable

    @pytest.mark.asyncio
    async def test_gencode_version_is_resolved_not_constructed(self) -> None:
        """COUNCIL S2: a wrong version returns an empty 200 that reads as absence."""
        async with Http() as http:
            out = await Resolver(http, Cache()).resolve_all(["BRAF"])
        r = out["BRAF"]
        assert r.gencode_id == "ENSG00000157764.12"
        assert r.gencode_version == "v26"

    @pytest.mark.asyncio
    async def test_uniprot_hop_is_swissprot_only(self) -> None:
        """QA B6 left this UNVERIFIED. Probed 2026-09-24: BRAF's trembl
        accessions return zero ChEMBL target records, so only swissprot is used.
        """
        async with Http() as http:
            out = await Resolver(http, Cache()).resolve_all(["BRAF", "MIR29B2CHG"])
        assert out["BRAF"].uniprot == "P15056"
        # Non-coding: no protein, and that is `not_applicable`, not `no_data`.
        assert out["MIR29B2CHG"].uniprot is None

    @pytest.mark.asyncio
    async def test_no_match_is_explicit(self) -> None:
        async with Http() as http:
            out = await Resolver(http, Cache()).resolve_all(["NOTAREALGENE123"])
        assert out["NOTAREALGENE123"].status == "no_match"
        assert not out["NOTAREALGENE123"].usable

    @pytest.mark.asyncio
    async def test_mouse_symbol_flagged_not_converted(self) -> None:
        async with Http() as http:
            out = await Resolver(http, Cache()).resolve_all(["Trp53"])
        r = out["Trp53"]
        assert r.status == "possible_mouse"
        assert r.approved_symbol == "TP53"  # resolved, but the user is warned
        assert "species" in r.note.lower()
