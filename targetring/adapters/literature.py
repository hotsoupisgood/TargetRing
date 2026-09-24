"""Literature counts: PubTator3 for the ring, Europe PMC for the audit trail.

PubTator3 (COUNCIL S7) resolves gene mentions to NCBI Gene ids rather than
matching text, so common-word symbols do not inflate the count, and the `year`
facet arrives in the same response at zero extra cost.

Europe PMC is kept for one reason: it echoes the query back, which is the
auditable surface QA B3 is about. Its count is displayed as context, never as
the ring value (COUNCIL M6: the count swings 44% on phrasing alone, so a ratio
of two such counts measures symbol ambiguity, not neglect).

**A correction to QA B3, measured 2026-09-24.** The review says the echo is
"the only reliable malformed-query detector the API offers". It is not
sufficient: sending the unbalanced `"BRAF` returns `hitCount: 101223` (12x
inflation) *and* echoes `request.queryString: '"BRAF'` byte-identically. The
echo catches silent *reinterpretation*; it does not catch a malformed query the
server is happy to run. So the query is validated before it is sent, and the
echo is asserted after. Both, not either.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import EUROPEPMC_SEARCH, PUBTATOR_SEARCH
from ..http import Http, HardFailure, check_no_ncbi_error, json_or_raise
from ..states import Cell, failed, no_data, value


class QueryRejected(HardFailure):
    """The query is malformed and was never sent."""


_BALANCED = {'"': '"', "(": ")", "[": "]"}


def validate_query(q: str) -> str:
    """Refuse a query that the server would run and answer wrongly.

    Verified: `"BRAF` -> HTTP 200, hitCount 101223 against 8418 for the balanced
    form, with a byte-identical echo. Nothing downstream can detect that, so it
    must not be sent.
    """
    if not q or not q.strip():
        raise QueryRejected("empty literature query")
    if q.count('"') % 2:
        raise QueryRejected(
            f"unbalanced double quote in literature query {q!r}. Europe PMC answers this with "
            f"HTTP 200 and a 12x inflated hitCount, echoed back byte-identically, so it is "
            f"rejected before sending."
        )
    depth = 0
    for ch in q:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise QueryRejected(f"unbalanced parenthesis in literature query {q!r}")
    if depth:
        raise QueryRejected(f"unbalanced parenthesis in literature query {q!r}")
    if re.search(r"[\x00-\x1f]", q):
        raise QueryRejected(f"control character in literature query {q!r}")
    return q


def pubtator_gene_query(symbol: str, region: str | None = None) -> str:
    """`@GENE_<SYMBOL>` is PubTator's gene-id-resolved form (COUNCIL S7).

    Honest limit, stated on the figure: PubTator annotates genes, diseases,
    chemicals, species, variants and cell lines -- not anatomy. The region half
    stays free text, so this is 'disambiguated gene, fuzzy region'.
    """
    q = f"@GENE_{symbol.upper()}"
    if region:
        q += f" {region}"
    return q


@dataclass
class LiteratureRecord:
    symbol: str
    query: str
    count: int | None = None
    years: dict[str, int] = field(default_factory=dict)
    epmc_query: str | None = None
    epmc_count: int | None = None
    epmc_echo_ok: bool | None = None
    error: str | None = None


class PubTatorAdapter:
    source = "pubtator.count"

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def fetch_one(self, symbol: str, region: str | None = None) -> LiteratureRecord:
        q = validate_query(pubtator_gene_query(symbol, region))
        rec = LiteratureRecord(symbol, q)
        cached = self.cache.get(self.source, q)
        if cached is None:
            try:
                r = await self.http.get(PUBTATOR_SEARCH, {"text": q})
                body = json_or_raise(r)
                # QA B3 rule 2: an ERROR key anywhere is a hard failure whatever
                # the status code. Verified: db=notadb gives 200 + ERROR body.
                check_no_ncbi_error(body, "PubTator3")
            except Exception as e:  # noqa: BLE001
                rec.error = str(e)
                return rec
            if not isinstance(body.get("count"), int):
                rec.error = f"PubTator3 returned no integer `count` (keys: {sorted(body)})"
                return rec
            years = {
                str(k): int(v)
                for k, v in (body.get("facets", {}).get("facet_fields", {}).get("year") or {}).items()
            } if isinstance(
                body.get("facets", {}).get("facet_fields", {}).get("year"), dict
            ) else _year_list_to_dict(
                body.get("facets", {}).get("facet_fields", {}).get("year")
            )
            cached = {"count": body["count"], "years": years}
            self.cache.put(self.source, q, cached)
        rec.count = cached["count"]
        rec.years = cached.get("years") or {}
        return rec


def _year_list_to_dict(node: Any) -> dict[str, int]:
    """PubTator's year facet has been seen as a list of {name,value} pairs and
    as a mapping. Accept either, and nothing else."""
    if isinstance(node, dict):
        return {str(k): int(v) for k, v in node.items() if isinstance(v, int)}
    if isinstance(node, list):
        out: dict[str, int] = {}
        for item in node:
            if isinstance(item, dict) and "name" in item and "value" in item:
                try:
                    out[str(item["name"])] = int(item["value"])
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    out[str(item[0])] = int(item[1])
                except (TypeError, ValueError):
                    continue
        return out
    return {}


class EuropePMCAdapter:
    """Context count only, with the echo assertion of QA B3 rule 1."""

    source = "europepmc.search"

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def fetch_one(self, symbol: str, region: str | None = None) -> tuple[str, int | None, bool | None, str | None]:
        q = validate_query(f'"{symbol}" AND "{region}"' if region else f'"{symbol}"')
        cached = self.cache.get(self.source, q)
        if cached is None:
            try:
                r = await self.http.get(
                    EUROPEPMC_SEARCH, {"query": q, "format": "json", "pageSize": 1}
                )
                body = json_or_raise(r)
            except Exception as e:  # noqa: BLE001
                return q, None, None, str(e)
            echo = (body.get("request") or {}).get("queryString")
            echo_ok = echo == q
            if not echo_ok:
                # Silent reinterpretation. Hard failure per QA B3.
                return q, None, False, (
                    f"Europe PMC echoed {echo!r} for the query {q!r}; the term was silently "
                    f"reinterpreted, so the count is not the count for this gene."
                )
            hit = body.get("hitCount")
            if not isinstance(hit, int):
                return q, None, True, f"no integer hitCount (keys: {sorted(body)})"
            cached = {"count": hit, "echo_ok": True}
            self.cache.put(self.source, q, cached)
        return q, cached["count"], cached["echo_ok"], None


def literature_cell(rec: LiteratureRecord) -> Cell:
    """log10(1 + count). Raw counts span five orders of magnitude, so the ring
    is drawn on a log scale and the scale is stated in the CSV detail."""
    if rec.error:
        return failed(f"PubTator3: {rec.error}")
    if rec.count is None:
        return no_data("no PubTator3 count")
    return value(
        math.log10(1 + rec.count),
        f"{rec.count} PubTator3 records for {rec.query!r} (log10(1+n) = "
        f"{math.log10(1 + rec.count):.2f}); gene id-resolved, region is free text",
        raw={"count": rec.count, "query": rec.query, "years": rec.years},
    )


__all__ = [
    "PubTatorAdapter", "EuropePMCAdapter", "LiteratureRecord", "literature_cell",
    "validate_query", "pubtator_gene_query", "QueryRejected",
]
