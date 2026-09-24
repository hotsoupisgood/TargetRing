"""One identifier resolver for the whole app, run before any ring.

QA B6 / AGENTS.md rule 5. Resolution is the join key for every ring, not a
per-ring concern. Symbol -> Ensembl gene id -> GTEx gencode id (exact version)
-> UniProt accession, with explicit `ambiguous` and `no_match` states.

Why each hop matters:

* Ensembl id is the canonical internal key. Exact-symbol matching alone
  silently deletes renamed genes (QA B5: `SEPT9` is `SEPTIN9` now, `MARCH1` is
  `MARCHF1`), and those are exactly the symbols in an older DE table.
* GTEx serves one exact gencode version. `ENSG00000157764.14` returns
  `{"data":[]}` with HTTP 200 -- a wrong version is indistinguishable from real
  absence (COUNCIL S2), so the version is resolved, never constructed.
* UniProt comes from Open Targets `proteinIds`. The reviews left this hop
  UNVERIFIED (QA B6); probed 2026-09-24 -- see `docs/V1-REPORT.md`. Only
  `uniprot_swissprot` is usable: the trembl accessions on BRAF return zero
  ChEMBL target records.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Literal

from .cache import Cache
from .config import GTEX_BASE, OPENTARGETS_URL
from .http import Http, graphql, json_or_raise

ResolutionStatus = Literal["ok", "renamed", "ambiguous", "no_match", "excel_corrupt", "possible_mouse"]

# QA B7: Excel eats `SEPT9` into `9-Sep`. Unrecoverable -- `9-Sep` gets 0 hits
# from mapIds -- and the user's fix is to re-export, not to correct a symbol,
# so it needs its own status and its own message.
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
EXCEL_DATE_RE = re.compile(
    rf"^(\d{{1,2}}-({_MONTHS})|({_MONTHS})-\d{{1,2}}|({_MONTHS})-\d{{2,4}})$",
    re.IGNORECASE,
)
# Mouse symbols are Title-case with a lowercase tail (`Braf`, `Trp53`). mapIds
# resolves `braf` to human BRAF case-insensitively (QA N5), so casing cannot be
# used to *reject*; it can only raise a flag for the user (QA B7).
MOUSE_LIKE_RE = re.compile(r"^[A-Z][a-z0-9]*[a-z][a-z0-9]*\d*$")
ENSEMBL_RE = re.compile(r"^(ENSG\d{11})(\.\d+)?$", re.IGNORECASE)


@dataclass
class Resolution:
    """What every downstream call joins on."""

    input_symbol: str
    status: ResolutionStatus
    ensembl_id: str | None = None
    approved_symbol: str | None = None
    gencode_id: str | None = None
    gencode_version: str | None = None
    uniprot: str | None = None
    candidates: list[dict[str, str]] = field(default_factory=list)
    note: str = ""

    @property
    def usable(self) -> bool:
        """Can this gene carry rings? `ambiguous` deliberately cannot."""
        return self.status in ("ok", "renamed", "possible_mouse") and self.ensembl_id is not None


def classify_input(raw: str) -> tuple[str, ResolutionStatus | None, str]:
    """Pre-resolution triage of one raw cell. Returns (cleaned, forced_status, note)."""
    cleaned = raw.strip().strip("\"'").strip()
    if not cleaned:
        return cleaned, "no_match", "blank symbol"
    if EXCEL_DATE_RE.match(cleaned):
        return (
            cleaned,
            "excel_corrupt",
            "Excel converted this symbol to a date. Unrecoverable from the value alone — "
            "re-export the source file with the symbol column formatted as Text.",
        )
    m = ENSEMBL_RE.match(cleaned)
    if m:
        # QA N6: strip the version suffix. OT accepts the bare id; the versioned
        # form is unverified, so it is never sent.
        return m.group(1).upper(), None, ""
    return cleaned, None, ""


def looks_mouse(symbol: str) -> bool:
    return bool(MOUSE_LIKE_RE.match(symbol)) and symbol.upper() != symbol


class Resolver:
    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def resolve_all(self, symbols: list[str]) -> dict[str, Resolution]:
        """Resolve every input string once. Returns {input_symbol: Resolution}."""
        out: dict[str, Resolution] = {}
        to_map: list[tuple[str, str]] = []  # (input, cleaned)

        for raw in symbols:
            cleaned, forced, note = classify_input(raw)
            if forced is not None:
                out[raw] = Resolution(raw, forced, note=note)
                continue
            to_map.append((raw, cleaned))

        if to_map:
            mapping = await self._map_ids([c for _, c in to_map])
            for raw, cleaned in to_map:
                out[raw] = self._from_hits(raw, cleaned, mapping.get(cleaned, []))

        # Second hop, only for genes that resolved. Both are independent.
        usable = [r for r in out.values() if r.usable]
        await asyncio.gather(
            self._attach_gencode(usable),
            self._attach_uniprot(usable),
        )
        return out

    # -- hop 1: symbol/alias/obsolete/HGNC/Ensembl -> Ensembl gene id ---------

    async def _map_ids(self, terms: list[str]) -> dict[str, list[dict[str, str]]]:
        uniq = sorted(set(terms))
        result: dict[str, list[dict[str, str]]] = {}
        pending: list[str] = []
        for t in uniq:
            hit = self.cache.get("opentargets.resolve", t)
            if hit is None:
                pending.append(t)
            else:
                result[t] = hit

        if pending:
            # mapIds is one call for the whole batch and returns a `mappings`
            # array keyed by `term`, so there is nothing to zip (QA B2).
            query = (
                "query Resolve($terms:[String!]!){"
                "mapIds(queryTerms:$terms,entityNames:[\"target\"]){"
                "mappings{term hits{id name entity}}}}"
            )
            data = await graphql(self.http, OPENTARGETS_URL, query, {"terms": pending})
            by_term = {m["term"]: m for m in data["mapIds"]["mappings"]}
            missing = set(pending) - set(by_term)
            if missing:
                raise RuntimeError(
                    f"mapIds did not echo back {sorted(missing)!r}; refusing to guess "
                    f"(the batch-drop pattern of QA B2)"
                )
            for term in pending:
                hits = [
                    {"id": h["id"], "name": h["name"]}
                    for h in by_term[term]["hits"]
                    if h["entity"] == "target"
                ]
                self.cache.put("opentargets.resolve", term, hits)
                result[term] = hits
        return result

    def _from_hits(self, raw: str, cleaned: str, hits: list[dict[str, str]]) -> Resolution:
        if not hits:
            return Resolution(raw, "no_match", note=f"{cleaned!r} matched no Open Targets target")
        if len(hits) > 1:
            # QA B6: never pick the first. Silently choosing a winner here is how
            # a figure becomes about the wrong gene.
            return Resolution(
                raw,
                "ambiguous",
                candidates=hits,
                note=f"{len(hits)} targets match {cleaned!r}: "
                + ", ".join(f"{h['name']} ({h['id']})" for h in hits[:6]),
            )
        hit = hits[0]
        status: ResolutionStatus = "ok"
        note = ""
        renamed = hit["name"].upper() != cleaned.upper() and not ENSEMBL_RE.match(cleaned)
        # Mouse-styling outranks a rename flag: a mouse symbol pasted into a
        # human table changes the science, where a rename only changes the
        # label. `Trp53` is both (it is the mouse symbol for TP53) and must
        # surface as the species problem, not as a tidy rename (QA B7).
        if looks_mouse(cleaned):
            status = "possible_mouse"
            note = (
                f"{cleaned!r} is styled like a mouse symbol and resolved to human "
                f"{hit['name']}. Not auto-converted — silently orthologue-mapping would "
                f"change the science. Check the species of the source table."
            )
        elif renamed:
            status = "renamed"
            note = f"{cleaned} → {hit['name']} (current approved symbol)"
        return Resolution(raw, status, ensembl_id=hit["id"], approved_symbol=hit["name"], note=note)

    # -- hop 2a: Ensembl -> GTEx gencode id, exact version -------------------

    async def _attach_gencode(self, rs: list[Resolution]) -> None:
        await asyncio.gather(*(self._one_gencode(r) for r in rs))

    async def _one_gencode(self, r: Resolution) -> None:
        assert r.ensembl_id
        cached = self.cache.get("gtex.gene", r.ensembl_id)
        if cached is None:
            resp = await self.http.get(f"{GTEX_BASE}/reference/gene", {"geneId": r.ensembl_id})
            body = json_or_raise(resp)
            rows = body.get("data", [])
            # GTEx matches loosely; keep only rows whose unversioned id is ours.
            cached = [
                {
                    "gencodeId": row["gencodeId"],
                    "gencodeVersion": row.get("gencodeVersion"),
                    "geneSymbol": row.get("geneSymbol"),
                }
                for row in rows
                if str(row.get("gencodeId", "")).split(".")[0].upper() == r.ensembl_id.upper()
            ]
            self.cache.put("gtex.gene", r.ensembl_id, cached)
        if len(cached) == 1:
            r.gencode_id = cached[0]["gencodeId"]
            r.gencode_version = cached[0]["gencodeVersion"]
        elif len(cached) > 1:
            # Do not pick one: the wrong version returns an empty 200 that reads
            # as legitimate absence (COUNCIL S2).
            r.note = (r.note + " | " if r.note else "") + (
                f"GTEx returned {len(cached)} gencode ids for {r.ensembl_id}; "
                f"expression left as no_data rather than guessing a version"
            )

    # -- hop 2b: Ensembl -> UniProt (swissprot only) -------------------------

    async def _attach_uniprot(self, rs: list[Resolution]) -> None:
        need = [r for r in rs if r.ensembl_id]
        cached_for: dict[str, str | None] = {}
        pending = []
        for r in need:
            hit = self.cache.get("opentargets.target", f"proteinIds|{r.ensembl_id}")
            if hit is None:
                pending.append(r)
            else:
                cached_for[r.ensembl_id] = hit.get("uniprot")

        if pending:
            query = (
                "query Prot($ids:[String!]!){targets(ensemblIds:$ids){id proteinIds{id source}}}"
            )
            data = await graphql(
                self.http, OPENTARGETS_URL, query, {"ids": [r.ensembl_id for r in pending]}
            )
            # QA B2: build {id: record}. `targets()` drops what it cannot find.
            by_id = {t["id"]: t for t in data["targets"]}
            for r in pending:
                rec = by_id.get(r.ensembl_id)
                acc = None
                if rec:
                    swiss = [
                        p["id"]
                        for p in rec.get("proteinIds") or []
                        if str(p.get("source", "")).lower() == "uniprot_swissprot"
                    ]
                    acc = swiss[0] if swiss else None
                self.cache.put("opentargets.target", f"proteinIds|{r.ensembl_id}", {"uniprot": acc})
                cached_for[r.ensembl_id] = acc

        for r in need:
            r.uniprot = cached_for.get(r.ensembl_id)


def ledger_reason(r: Resolution) -> str:
    """The user-facing sentence for a gene that will not be rendered."""
    return {
        "excel_corrupt": r.note,
        "ambiguous": f"Ambiguous identifier — {r.note}. Disambiguate and re-run.",
        "no_match": r.note or "no match",
    }.get(r.status, r.note)


def as_json(r: Resolution) -> dict[str, object]:
    return {
        "input": r.input_symbol,
        "status": r.status,
        "ensembl_id": r.ensembl_id,
        "approved_symbol": r.approved_symbol,
        "gencode_id": r.gencode_id,
        "uniprot": r.uniprot,
        "note": r.note,
        "candidates": r.candidates,
    }


__all__ = [
    "Resolution",
    "Resolver",
    "ResolutionStatus",
    "classify_input",
    "looks_mouse",
    "ledger_reason",
    "as_json",
    "EXCEL_DATE_RE",
]
