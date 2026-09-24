"""Startup contract tests — QA B1.

"Pin the query text in source. Add a startup contract test that issues the exact
production query for one known gene and fails the run loudly on any non-200 or
any `errors` key. A 400 must abort, never render an empty ring."

These are `live` by definition. `check_contracts()` is also called by the app at
startup so a schema drift stops the run instead of painting empty rings.
"""

from __future__ import annotations

import pytest

from targetring.adapters.opentargets import TARGET_QUERY, OpenTargetsAdapter
from targetring.cache import Cache
from targetring.config import GTEX_DATASET, OPENTARGETS_URL
from targetring.contract import FIELD_SNAPSHOT, check_contracts, introspect_target_fields
from targetring.http import Http, graphql, json_or_raise

pytestmark = pytest.mark.live

BRAF = "ENSG00000157764"


class TestOpenTargetsContract:
    async def test_production_query_returns_200_with_no_errors(self) -> None:
        async with Http() as http:
            data = await graphql(http, OPENTARGETS_URL, TARGET_QUERY, {"ids": [BRAF]})
        t = data["targets"][0]
        assert t["id"] == BRAF and t["approvedSymbol"] == "BRAF"
        assert len(t["prioritisation"]["items"]) == 18
        assert t["drugAndClinicalCandidates"]["count"] > 0

    async def test_prioritisation_values_are_strings(self) -> None:
        """COUNCIL M2, measured: round() on one raises TypeError."""
        async with Http() as http:
            data = await graphql(http, OPENTARGETS_URL, TARGET_QUERY, {"ids": [BRAF]})
        vals = [i["value"] for i in data["targets"][0]["prioritisation"]["items"]]
        assert all(isinstance(v, str) for v in vals), "cast explicitly, do not round()"

    async def test_a_bad_field_aborts_rather_than_returning_empty(self) -> None:
        """The failure mode B1 exists to prevent: a 400 must raise, not render."""
        from targetring.http import HardFailure
        async with Http() as http:
            with pytest.raises(HardFailure, match="GraphQL errors"):
                await graphql(http, OPENTARGETS_URL,
                              '{target(ensemblId:"%s"){knownDrugs{count}}}' % BRAF)

    async def test_introspected_fields_match_the_checked_in_snapshot(self) -> None:
        """QA B1: re-run introspection and diff against a checked-in snapshot."""
        async with Http() as http:
            live = await introspect_target_fields(http)
        missing = set(FIELD_SNAPSHOT) - live
        assert not missing, (
            f"Open Targets no longer exposes {sorted(missing)!r} on Target. "
            f"Every ring that reads one of these is now silently empty."
        )

    async def test_knowndrugs_still_does_not_exist(self) -> None:
        async with Http() as http:
            live = await introspect_target_fields(http)
        assert "knownDrugs" not in live


class TestBatchDropIsStillReal:
    """QA B2. If Open Targets ever starts returning nulls instead of dropping,
    the join code is still correct — but the regression test's premise changed
    and we want to know."""

    async def test_batch_still_silently_drops(self) -> None:
        ids = [BRAF, "ENSG00000999999", "ENSG00000141510"]
        async with Http() as http:
            data = await graphql(
                http, OPENTARGETS_URL,
                "query Q($ids:[String!]!){targets(ensemblIds:$ids){id approvedSymbol}}",
                {"ids": ids},
            )
        returned = [t["id"] for t in data["targets"]]
        assert "ENSG00000999999" not in returned
        assert set(returned) <= set(ids), "response must be a subset of the request"

    async def test_adapter_renders_the_gap_as_explicit_missing(self) -> None:
        async with Http() as http:
            recs = await OpenTargetsAdapter(http, Cache(enabled=False)).fetch(
                [BRAF, "ENSG00000999999"]
            )
        assert len(recs) == 2, "every requested id must get a record"
        assert recs[BRAF].found is True
        assert recs["ENSG00000999999"].found is False


class TestGTExContract:
    async def test_reference_gene_serves_the_version_expression_needs(self) -> None:
        """COUNCIL S2's trap: a constructed version returns an empty 200."""
        async with Http() as http:
            r = await http.get("https://gtexportal.org/api/v2/reference/gene", {"geneId": "BRAF"})
            rows = [g for g in json_or_raise(r)["data"] if g["geneSymbol"] == "BRAF"]
            gid = rows[0]["gencodeId"]
            r2 = await http.get(
                "https://gtexportal.org/api/v2/expression/medianGeneExpression",
                {"gencodeId": gid, "datasetId": GTEX_DATASET, "itemsPerPage": 200},
            )
            good = json_or_raise(r2)["data"]
            r3 = await http.get(
                "https://gtexportal.org/api/v2/expression/medianGeneExpression",
                {"gencodeId": "ENSG00000157764.14", "datasetId": GTEX_DATASET},
            )
            bad = json_or_raise(r3)["data"]
        assert len(good) > 40, "the resolved version must return the tissue panel"
        assert bad == [], "a wrong version still fails silently — hence resolve, never construct"


class SourceUnreachable:
    """A source being down is not a contract violation — UX A1, in the suite.

    v1 applied this to Europe PMC only. The v2 screenshot pass was interrupted
    by EBI returning HTTP 500 with an HTML error page for every ChEMBL request,
    and two tests reported FAIL for a source that was simply down and that
    drives no ring and no rank (PLAN §8). Reporting a source outage as a test
    failure is the same mistake as stamping DO NOT TRUST THIS FIGURE for one:
    it trains the reader to ignore the signal.

    Use as a context manager around the call. A transport error, a timeout or a
    5xx skips with the status in the reason; anything else — a 200 whose shape
    is wrong — is a real contract failure and propagates.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "SourceUnreachable":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        import httpx

        from targetring.http import HardFailure
        if exc is None:
            return False
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            pytest.skip(f"{self.name} unreachable, contract NOT evaluated: "
                        f"{type(exc).__name__}: {str(exc)[:140]}")
        if isinstance(exc, HardFailure):
            text = str(exc)
            transient = any(f"HTTP {c}" in text for c in (500, 502, 503, 504))
            if transient or "content-type" in text:
                pytest.skip(f"{self.name} unreachable, contract NOT evaluated: "
                            f"{text[:160]}")
        return False


async def _epmc(query: str) -> dict:
    """Europe PMC was returning nginx 502/503 during the v1 build (COUNCIL M6
    saw a transient 503 too)."""
    with SourceUnreachable("Europe PMC"):
        async with Http() as http:
            r = await http.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                {"query": query, "format": "json", "pageSize": 1},
            )
            return json_or_raise(r)


class TestLiteratureContract:
    async def test_europepmc_echoes_the_query(self) -> None:
        body = await _epmc("BRAF AND knockout")
        assert body["request"]["queryString"] == "BRAF AND knockout"

    async def test_the_echo_alone_does_not_catch_a_malformed_query(self) -> None:
        """The correction to QA B3 rule 1, pinned as a test so it is not
        re-forgotten: the 12x-inflated query echoes back byte-identically."""
        body = await _epmc('"BRAF')
        assert body["request"]["queryString"] == '"BRAF'      # echo is clean
        assert body["hitCount"] > 50_000                       # answer is not

    def test_the_malformed_query_is_never_sent(self) -> None:
        """The half of the above that does not depend on Europe PMC being up."""
        from targetring.adapters.literature import QueryRejected, validate_query
        with pytest.raises(QueryRejected):
            validate_query('"BRAF')

    async def test_ncbi_error_key_in_a_200_is_a_hard_failure(self) -> None:
        from targetring.http import HardFailure, check_no_ncbi_error
        async with Http() as http:
            r = await http.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                {"db": "notadb", "term": "BRAF", "retmode": "json"},
            )
            body = json_or_raise(r)
        assert r.status_code == 200
        with pytest.raises(HardFailure, match="ERROR key"):
            check_no_ncbi_error(body, "test")


class TestChEMBLContract:
    async def test_unknown_parameter_is_silently_ignored(self) -> None:
        """Probed 2026-09-24, not in either review: ChEMBL answers a bogus
        filter with 200 and the entire unfiltered table. A typo'd filter becomes
        a confident wrong answer, so the adapter checks the row count."""
        with SourceUnreachable("ChEMBL"):
            async with Http() as http:
                r = await http.get("https://www.ebi.ac.uk/chembl/api/data/target.json",
                                   {"notaparam": "x", "limit": 1})
                body = json_or_raise(r)
            assert r.status_code == 200
            assert body["page_meta"]["total_count"] > 10_000

    async def test_the_adapter_refuses_an_unfiltered_response(self) -> None:
        from targetring.adapters.chembl import ChEMBLAdapter
        async with Http() as http:
            rec = await ChEMBLAdapter(http, Cache(enabled=False)).fetch_one("P15056")
        if rec.state == "failed" and (
            any(f"HTTP {c}" in (rec.error or "") for c in (500, 502, 503, 504))
            or "Error" in (rec.error or "") or "Timeout" in (rec.error or "")
        ):
            # The adapter swallows the exception by design — a source failure is
            # a `failed` cell, not a crash — so the skip is decided on the
            # recorded error rather than on an exception reaching the test.
            pytest.skip(f"ChEMBL unreachable, contract NOT evaluated: {rec.error[:160]}")
        assert rec.target_chembl_id == "CHEMBL5145"
        assert len(rec.mechanisms) >= 10


async def test_check_contracts_runs_clean() -> None:
    async with Http() as http:
        report = await check_contracts(http)
    failures = [c for c in report if not c["ok"]]
    assert not failures, f"contract failures: {failures}"
