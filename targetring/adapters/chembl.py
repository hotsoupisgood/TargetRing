"""ChEMBL mechanisms: `direct_interaction` + `disease_efficacy`.

**This adapter drives no ring and feeds no rank.** COUNCIL D2 makes ChEMBL the
only clean mechanism filter, but QA B6/§5 rules the UniProt/ChEMBL resolution
unprobed, so any ChEMBL-backed ring is untrusted on day one. It is drawer
evidence in v1, and the run manifest records it as such.

What I probed 2026-09-24 (the reviews did not):

* `proteinIds` -> `uniprot_swissprot` -> `/target.json?target_components__accession=`
  -> filter `SINGLE PROTEIN` + `Homo sapiens` -> one id for BRAF (CHEMBL5145),
  whose `/mechanism.json` returns the 14 rows COUNCIL D2 measured.
* The trembl accessions on the same gene return `total_count: 0`, so only
  swissprot is usable.
* **ChEMBL silently ignores unknown query parameters.** `/target.json?notaparam=x`
  returns HTTP 200 with `total_count: 18552` -- the entire unfiltered target
  table. This is exactly the REST hazard QA §4 predicted and it converts a
  typo'd filter into a confident wrong answer. Every response here is therefore
  checked against the filter that was supposed to apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..config import CHEMBL_BASE
from ..http import HardFailure, Http, describe, json_or_raise


@dataclass
class ChEMBLRecord:
    uniprot: str | None
    # COUNCIL D5: "no target record at all" and "target record with 0
    # mechanisms" are different answers and need different words.
    state: str = "no_uniprot"  # no_uniprot | no_target_record | undrugged | mechanisms | failed
    target_chembl_id: str | None = None
    mechanisms: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def direct_mechanisms(self) -> list[dict[str, Any]]:
        return [m for m in self.mechanisms if m.get("direct_interaction") == 1]

    @property
    def efficacy_mechanisms(self) -> list[dict[str, Any]]:
        return [
            m for m in self.mechanisms
            if m.get("direct_interaction") == 1 and m.get("disease_efficacy") == 1
        ]


class ChEMBLAdapter:
    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def fetch_one(self, uniprot: str | None) -> ChEMBLRecord:
        if not uniprot:
            # Non-coding genes have no protein. `not_applicable`, not `no_data`.
            return ChEMBLRecord(None, state="no_uniprot")
        rec = ChEMBLRecord(uniprot)
        try:
            tid = await self._target_id(uniprot)
        except Exception as e:  # noqa: BLE001
            rec.state, rec.error = "failed", describe(e)
            return rec
        if tid is None:
            rec.state = "no_target_record"
            return rec
        rec.target_chembl_id = tid
        try:
            rec.mechanisms = await self._mechanisms(tid)
        except Exception as e:  # noqa: BLE001
            rec.state, rec.error = "failed", describe(e)
            return rec
        rec.state = "mechanisms" if rec.mechanisms else "undrugged"
        return rec

    async def _target_id(self, uniprot: str) -> str | None:
        cached = self.cache.get("chembl.target", uniprot)
        if cached is None:
            r = await self.http.get(
                f"{CHEMBL_BASE}/target.json",
                {"target_components__accession": uniprot, "limit": 50},
            )
            body = json_or_raise(r)
            targets = body.get("targets")
            if targets is None:
                raise HardFailure(f"ChEMBL target.json had no `targets` key for {uniprot}")
            total = (body.get("page_meta") or {}).get("total_count")
            # ChEMBL ignores unknown params and returns the whole table with a
            # 200 (probed: total_count 18552). If the filter silently failed to
            # apply, the row count is absurd -- refuse rather than pick from it.
            if isinstance(total, int) and total > 200:
                raise HardFailure(
                    f"ChEMBL returned {total} target records for accession {uniprot}. The "
                    f"accession filter did not apply (ChEMBL ignores unknown parameters and "
                    f"answers 200 with the unfiltered table); refusing to use this response."
                )
            # COUNCIL S5: filter to SINGLE PROTEIN or you double-count. One
            # accession returned six records for BRAF: the single protein plus
            # families and complexes.
            single = [
                t for t in targets
                if t.get("target_type") == "SINGLE PROTEIN" and t.get("organism") == "Homo sapiens"
                and any(
                    c.get("accession") == uniprot for c in (t.get("target_components") or [])
                )
            ]
            cached = {"ids": [t["target_chembl_id"] for t in single], "total": total}
            self.cache.put("chembl.target", uniprot, cached)
        ids = cached["ids"]
        if len(ids) != 1:
            # Zero = no record (COUNCIL D5). More than one = ambiguous, and
            # picking one is how the drawer ends up about another protein.
            return None if not ids else None
        return ids[0]

    async def _mechanisms(self, target_chembl_id: str) -> list[dict[str, Any]]:
        cached = self.cache.get("chembl.mechanism", target_chembl_id)
        if cached is None:
            fields = ("molecule_chembl_id,action_type,direct_interaction,disease_efficacy,"
                      "max_phase,mechanism_of_action,target_chembl_id")
            r = await self.http.get(
                f"{CHEMBL_BASE}/mechanism.json",
                {"target_chembl_id": target_chembl_id, "only": fields, "limit": 200},
            )
            body = json_or_raise(r)
            rows = body.get("mechanisms")
            if rows is None:
                raise HardFailure(f"ChEMBL mechanism.json had no `mechanisms` key")
            # Same silent-filter check, applied to the rows themselves.
            wrong = [
                m.get("target_chembl_id") for m in rows
                if m.get("target_chembl_id") not in (None, target_chembl_id)
            ]
            if wrong:
                raise HardFailure(
                    f"ChEMBL mechanism.json returned rows for other targets "
                    f"({sorted(set(wrong))[:5]}); the target filter did not apply."
                )
            cached = rows
            self.cache.put("chembl.mechanism", target_chembl_id, cached)
        return cached


def mechanism_summary(rec: ChEMBLRecord) -> dict[str, Any]:
    """For the drawer. Deliberately not a number for a ring."""
    words = {
        "no_uniprot": "no UniProt accession — non-coding, or no reviewed protein entry",
        "no_target_record": "no ChEMBL target record for this accession (no data, not undrugged)",
        "undrugged": "ChEMBL target record exists with 0 mechanisms — genuinely undrugged",
        "mechanisms": None,
        "failed": f"ChEMBL call failed: {rec.error}",
    }
    note = words.get(rec.state)
    return {
        "state": rec.state,
        "note": note,
        "target_chembl_id": rec.target_chembl_id,
        "n_mechanisms": len(rec.mechanisms),
        "n_direct": len(rec.direct_mechanisms),
        "n_direct_efficacy": len(rec.efficacy_mechanisms),
        "rows": [
            {
                "molecule": m.get("molecule_chembl_id"),
                "action_type": m.get("action_type"),
                "direct_interaction": m.get("direct_interaction"),
                "disease_efficacy": m.get("disease_efficacy"),
                "max_phase": m.get("max_phase"),
                "mechanism_of_action": m.get("mechanism_of_action"),
                "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{m['molecule_chembl_id']}/"
                if m.get("molecule_chembl_id") else None,
            }
            for m in rec.efficacy_mechanisms or rec.mechanisms
        ][:40],
        "untrusted": True,
        "untrusted_reason": (
            "UniProt/ChEMBL resolution is unprobed at scale (QA B6). This panel is evidence, "
            "not a ring, and does not affect any rank."
        ),
    }


__all__ = ["ChEMBLAdapter", "ChEMBLRecord", "mechanism_summary"]
