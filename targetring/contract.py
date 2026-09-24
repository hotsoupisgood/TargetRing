"""Startup contract checks (QA B1).

Run before a figure is produced. A schema drift must abort the run, because the
alternative is a ring that is empty for every gene and looks like legitimate
absence.

The field snapshot is checked in deliberately: QA B1 asks for introspection to
be re-run and diffed against it, and COUNCIL §7 warns that the 18
`prioritisation` keys are version-dependent.
"""

from __future__ import annotations

from typing import Any

from .adapters.opentargets import DRUGGABILITY_KEYS, SAFETY_KEYS, TARGET_QUERY
from .config import GTEX_BASE, GTEX_DATASET, OPENTARGETS_URL
from .http import Http, describe, graphql, json_or_raise

# Introspected 2026-09-24. Only the fields v1 actually reads.
FIELD_SNAPSHOT: frozenset[str] = frozenset({
    "approvedSymbol", "id", "biotype", "prioritisation", "tractability",
    "safetyLiabilities", "isEssential", "depMapEssentiality", "geneticConstraint",
    "obsoleteSymbols", "mousePhenotypes", "drugAndClinicalCandidates", "proteinIds",
    "homologues", "chemicalProbes",
})

PRIORITISATION_KEYS_SNAPSHOT: frozenset[str] = frozenset({
    "geneticConstraint", "hasHighQualityChemicalProbes", "hasLigand", "hasPocket",
    "hasSafetyEvent", "hasSmallMoleculeBinder", "isCancerDriverGene", "isInMembrane",
    "isSecreted", "maxClinicalStage", "mouseKOScore", "mouseOrthologMaxIdentityPercentage",
    "paralogMaxIdentityPercentage", "tissueDistribution", "tissueSpecificity",
    "celltypeSpecificity", "celltypeDistribution", "geneEssentiality",
})

BRAF = "ENSG00000157764"


async def introspect_target_fields(http: Http) -> set[str]:
    data = await graphql(http, OPENTARGETS_URL, '{__type(name:"Target"){fields{name}}}')
    return {f["name"] for f in data["__type"]["fields"]}


async def check_contracts(http: Http) -> list[dict[str, Any]]:
    """Returns one record per check. Any `ok: False` must abort the run."""
    out: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        out.append({"check": name, "ok": ok, "detail": detail})

    # 1. The exact production query, verbatim, for a known gene.
    try:
        data = await graphql(http, OPENTARGETS_URL, TARGET_QUERY, {"ids": [BRAF]})
        t = data["targets"][0]
        items = {i["key"]: i["value"] for i in t["prioritisation"]["items"]}
        record("opentargets.production_query", t["approvedSymbol"] == "BRAF",
               f"{len(items)} prioritisation keys, "
               f"{t['drugAndClinicalCandidates']['count']} drug candidates")
        missing_keys = PRIORITISATION_KEYS_SNAPSHOT - set(items)
        record("opentargets.prioritisation_keys", not missing_keys,
               "all 18 keys present" if not missing_keys
               else f"missing since the snapshot: {sorted(missing_keys)}")
        ring_keys = set(DRUGGABILITY_KEYS) | set(SAFETY_KEYS)
        absent = ring_keys - set(items)
        record("opentargets.ring_keys", not absent,
               "every key a ring reads is present" if not absent
               else f"rings would be empty for: {sorted(absent)}")
    except Exception as e:  # noqa: BLE001
        record("opentargets.production_query", False, describe(e))

    # 2. Field snapshot diff.
    try:
        live = await introspect_target_fields(http)
        gone = FIELD_SNAPSHOT - live
        record("opentargets.field_snapshot", not gone,
               "schema matches the checked-in snapshot" if not gone
               else f"fields removed from Target: {sorted(gone)}")
    except Exception as e:  # noqa: BLE001
        record("opentargets.field_snapshot", False, describe(e))

    # 3. The batch-drop premise behind the B2 regression test.
    try:
        data = await graphql(
            http, OPENTARGETS_URL,
            "query Q($ids:[String!]!){targets(ensemblIds:$ids){id}}",
            {"ids": [BRAF, "ENSG00000999999"]},
        )
        returned = [t["id"] for t in data["targets"]]
        record("opentargets.batch_drop", returned == [BRAF],
               f"nonexistent id still dropped, not nulled (returned {returned})")
    except Exception as e:  # noqa: BLE001
        record("opentargets.batch_drop", False, describe(e))

    # 4. GTEx version coupling.
    try:
        r = await http.get(f"{GTEX_BASE}/reference/gene", {"geneId": "BRAF"})
        rows = [g for g in json_or_raise(r)["data"] if g["geneSymbol"] == "BRAF"]
        gid = rows[0]["gencodeId"]
        r2 = await http.get(f"{GTEX_BASE}/expression/medianGeneExpression",
                            {"gencodeId": gid, "datasetId": GTEX_DATASET, "itemsPerPage": 200})
        n = len(json_or_raise(r2)["data"])
        record("gtex.version_coupling", n > 40,
               f"{gid} ({GTEX_DATASET}) returns {n} tissues")
    except Exception as e:  # noqa: BLE001
        record("gtex.version_coupling", False, describe(e))

    return out


def contract_summary(report: list[dict[str, Any]]) -> str:
    bad = [c for c in report if not c["ok"]]
    if not bad:
        return f"{len(report)}/{len(report)} source contracts pass"
    return f"{len(bad)} of {len(report)} source contracts FAILED: " + "; ".join(
        f"{c['check']}: {c['detail'][:120]}" for c in bad
    )
