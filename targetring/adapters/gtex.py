"""GTEx median expression: the regional-specificity ring.

COUNCIL S2 replaced HPA with GTEx for this ring: HPA's free-text search returns
other genes (BRAF -> HMG20B, KIAA1549, ...), legitimately nulls ubiquitous
genes, and cost 7.9 s for `INS` alone (QA B4).

The trap that matters here: GTEx serves one exact gencode version.
`ENSG00000157764.14` returns HTTP 200 with `{"data":[]}` -- a wrong version is
indistinguishable from real absence. The version therefore comes from the
resolver (`/reference/gene`), is never constructed, and an empty response for a
*resolved* gencode id is reported as `no_data` with that distinction spelled
out.

The ring value is region TPM / median across the 54 tissues (PLAN §2 ring 2).
The denominator tissue set is stated on the figure, per COUNCIL M8.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import GTEX_BASE, GTEX_DATASET
from ..http import Http, json_or_raise
from ..states import Cell, no_data, value


@dataclass
class GTExRecord:
    gencode_id: str | None
    tissues: dict[str, float] = field(default_factory=dict)
    ontology: dict[str, str] = field(default_factory=dict)
    error: str | None = None


class GTExAdapter:
    source = "gtex.median_expression"

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def fetch_one(self, gencode_id: str | None) -> GTExRecord:
        if not gencode_id:
            return GTExRecord(None)
        key = f"{GTEX_DATASET}|{gencode_id}"
        cached = self.cache.get(self.source, key)
        if cached is None:
            try:
                r = await self.http.get(
                    f"{GTEX_BASE}/expression/medianGeneExpression",
                    {"gencodeId": gencode_id, "datasetId": GTEX_DATASET, "itemsPerPage": 200},
                )
                body = json_or_raise(r)
            except Exception as e:  # noqa: BLE001
                return GTExRecord(gencode_id, error=str(e))
            rows = body.get("data") or []
            cached = [
                {
                    "tissue": row["tissueSiteDetailId"],
                    "median": row["median"],
                    "ontologyId": row.get("ontologyId"),
                }
                for row in rows
                # Only rows for the gencode id we asked for. GTEx has not been
                # seen to return others, but the batch-drop class of bug (QA B2)
                # is cheap to exclude by construction.
                if row.get("gencodeId") == gencode_id
            ]
            self.cache.put(self.source, key, cached)

        tissues, ontology = {}, {}
        for row in cached:
            m = row.get("median")
            if isinstance(m, (int, float)):
                tissues[row["tissue"]] = float(m)
                if row.get("ontologyId"):
                    ontology[row["tissue"]] = row["ontologyId"]
        return GTExRecord(gencode_id, tissues, ontology)


def list_regions(rec: GTExRecord) -> list[str]:
    return sorted(rec.tissues)


def specificity_cell(rec: GTExRecord, region: str) -> Cell:
    """region TPM / median across all tissues GTEx reports for this gene."""
    if rec.gencode_id is None:
        return no_data("no GTEx gencode id resolved for this gene")
    if rec.error:
        from ..states import failed
        return failed(f"GTEx call failed: {rec.error}")
    if not rec.tissues:
        return no_data(
            f"GTEx returned no expression rows for the resolved gencode id {rec.gencode_id} "
            f"in {GTEX_DATASET}. The id came from /reference/gene, so this is absence in "
            f"GTEx, not a version mismatch."
        )
    if region not in rec.tissues:
        return no_data(f"{region} is not among the {len(rec.tissues)} tissues GTEx reports here")
    denom = statistics.median(rec.tissues.values())
    if denom <= 0:
        # Ratio undefined. A zero denominator is not a large ratio.
        return no_data(
            f"median expression across {len(rec.tissues)} tissues is {denom:g} TPM; "
            f"the specificity ratio is undefined, not high"
        )
    ratio = rec.tissues[region] / denom
    return value(
        ratio,
        f"{rec.tissues[region]:.4g} TPM in {region} / {denom:.4g} TPM median across "
        f"{len(rec.tissues)} GTEx tissues",
        raw={"region_tpm": rec.tissues[region], "median_tpm": denom,
             "n_tissues": len(rec.tissues), "uberon": rec.ontology.get(region)},
    )


__all__ = ["GTExAdapter", "GTExRecord", "specificity_cell", "list_regions"]
