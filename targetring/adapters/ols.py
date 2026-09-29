"""OLS4 — resolve a disease name to a `DiseaseRef` that carries *two* id spaces.

**The trap this module exists for.** `EFO_0000384` is obsolete in current EFO:
its label is literally `obsolete_Crohn's disease`, it was obsoleted in EFO
3.88.0, and its `term replaced by` annotation points at `MONDO_0005011`. Open
Targets `disease(efoId:"EFO_0000384")` returns **null**. Expression Atlas, by
contrast, still tags its samples `http://www.ebi.ac.uk/efo/EFO_0000384` in
`{ACC}.condensed-sdrf.tsv` and will go on doing so, because those files are
archived experiment annotations and not a live ontology.

So the brief's requirement "must all land on `EFO_0000384`" is right for the
Atlas side and wrong for every other source in the stack. A single-id disease
record would look up the obsolete id in Open Targets, get null, and render that
as "no disease data" — absence read as a fact, which is `AGENTS.md` rule 5's
gencode-version trap in a different id space.

`DiseaseRef` therefore carries both, and which id goes to which source is not a
decision any caller gets to make:

* `atlas_ids` — the ids Atlas samples are tagged with, obsolete ones included.
* `current_id` — the id Open Targets and every current source use.

`atlas_ids` always includes `current_id` itself, because newer Atlas experiments
do tag with MONDO ids; it is a superset, not an alternative.

**Rule 4 (assert the query echo), and the one plan correction here.** The plan
said OLS4 returns `responseHeader.params.q`. `/api/search` does **not**: its
`responseHeader` is `{"QTime": 0, "status": 0}`, with or without
`echoParams=all`. `/api/select` **does**. Verified live 2026-09-29. So this
module uses `select` and asserts the echo, rather than using `search` and quietly
skipping the assertion — an unbalanced quote inflating a hit count 12x with HTTP
200 is the failure rule 4 was written for, and OLS4's `q` is a Solr query too.

**The mapping is asserted, not assumed.** `MONDO_0005011`'s own
`database_cross_reference` list contains `EFO:0000384`, which is the candidate.
The candidate is then confirmed from the EFO side: `EFO_0000384`'s term record
must say `is_obsolete` and `term_replaced_by` == this MONDO IRI. A cross-reference
that does not survive that check is dropped and named, because an unverified
id-space bridge is exactly how the wrong disease's samples end up on a figure.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..cache import Cache
from ..http import HardFailure, Http, json_or_raise

#: `EFO:0000384` in a cross-reference list -> `EFO_0000384` as an id.
_EFO_XREF = re.compile(r"^EFO:(\d+)$")

#: Ontology prefixes whose ids Expression Atlas is known to tag samples with.
#: EFO (including obsolete terms) and MONDO. An id from any other prefix is not
#: put in `atlas_ids`: a DOID or an ICD10 code would never match an Atlas URI and
#: its presence would make `atlas_ids` look richer than it is.
ATLAS_ID_PREFIXES = ("EFO_", "MONDO_")


@dataclass(frozen=True)
class DiseaseRef:
    """One disease, in both id spaces, with the bridge between them recorded.

    `atlas_ids` is a tuple so the ref can be a cache key and so nothing downstream
    can append to it after the mapping has been asserted.
    """

    label: str
    current_id: str
    atlas_ids: tuple[str, ...]
    synonyms: tuple[str, ...] = ()
    ols_fetched_utc: str = ""
    #: `EFO_0000384 -> obsolete, replaced by MONDO_0005011 (EFO 3.88.0)`. One
    #: line per bridged id, so the manifest and the UI can say *why* an obsolete
    #: id is being sent to Atlas rather than leaving a reader to wonder.
    mapping_notes: tuple[str, ...] = ()
    #: Cross-references that were offered and did NOT survive verification.
    #: Reported, never silently dropped.
    rejected_ids: tuple[str, ...] = ()
    ontology: str = ""

    def __post_init__(self) -> None:
        if not self.current_id:
            raise HardFailure("a DiseaseRef with no current id resolves nothing")
        if self.current_id not in self.atlas_ids:
            raise HardFailure(
                f"{self.current_id} is missing from its own atlas_ids "
                f"{self.atlas_ids}. `atlas_ids` is a superset of the current id, "
                f"not an alternative to it: newer Atlas experiments tag with the "
                f"current id and older ones with the obsolete one, and a lookup "
                f"that dropped the current id would miss the new experiments.")

    @property
    def obsolete_atlas_ids(self) -> tuple[str, ...]:
        return tuple(i for i in self.atlas_ids if i != self.current_id)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _double_quote(iri: str) -> str:
    """OLS4's term route wants the IRI URL-encoded twice. Not a mistake in the
    docs: the path segment is decoded once by the router and once by the handler,
    and a singly-encoded IRI resolves to a 404 that reads like an absent term."""
    return urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")


class OLSAdapter:
    """Two cached calls: `select` for the query, `term` for one term's record."""

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def select(self, q: str, *, obsoletes: bool = False,
                     rows: int = 20) -> list[dict[str, Any]]:
        """`/api/select`, with the query echo asserted (rule 4).

        `obsoletes=True` returns ONLY obsolete terms — it is a filter, not an
        inclusion flag (verified live: the same query returns 8 obsolete EFO terms
        with it and 42 current terms without it). Both halves are needed, so this
        is called twice and the results are not mixed.
        """
        key = f"{q}|obsoletes={int(obsoletes)}|rows={rows}|{config.OLS4_ONTOLOGY_LIST}"
        cached = self.cache.get("ols.select", key)
        if cached is None:
            params: dict[str, Any] = {
                "q": q, "ontology": config.OLS4_ONTOLOGY_LIST, "rows": rows,
                "type": "class",
            }
            if obsoletes:
                params["obsoletes"] = "true"
            body = json_or_raise(await self.http.get(config.OLS4_SELECT, params))
            echo = (((body.get("responseHeader") or {}).get("params") or {})).get("q")
            if echo != q:
                # AGENTS.md rule 4. A Solr `q` with an unbalanced quote comes back
                # 200 with a different query's results; the echo is the only thing
                # that distinguishes that from an answer.
                raise HardFailure(
                    f"OLS4 /select echoed q={echo!r} for a query of {q!r}. The "
                    f"results are for a query this build did not send.")
            resp = body.get("response")
            if not isinstance(resp, dict) or "docs" not in resp:
                raise HardFailure(
                    f"OLS4 /select returned no `response.docs` for {q!r}: "
                    f"{sorted(body)}")
            cached = resp["docs"]
            self.cache.put("ols.select", key, cached)
        return cached

    async def term(self, ontology: str, iri: str) -> dict[str, Any]:
        """One term's full record — `is_obsolete`, `term_replaced_by`, synonyms
        and cross-references, none of which the `select` payload carries."""
        key = f"{ontology}|{iri}"
        cached = self.cache.get("ols.term", key)
        if cached is None:
            url = f"{config.OLS4_ONTOLOGIES}/{ontology}/terms/{_double_quote(iri)}"
            body = json_or_raise(await self.http.get(url))
            if not isinstance(body, dict) or body.get("iri") != iri:
                raise HardFailure(
                    f"OLS4 returned a record for {(body or {}).get('iri')!r} when "
                    f"asked for {iri!r}; joining these by position is rule 2's "
                    f"defect in an ontology")
            cached = body
            self.cache.put("ols.term", key, cached)
        return cached

    # -- the resolution itself ---------------------------------------------

    async def _atlas_ids_for(self, ontology: str, iri: str,
                            current_id: str) -> tuple[list[str], list[str], list[str]]:
        """`(atlas_ids, mapping_notes, rejected)` for one current term.

        Candidates come from the term's own `database_cross_reference` list. Each
        EFO candidate is then confirmed *from the EFO side*: that term must be
        obsolete and must name this IRI as its replacement. An xref is a claim;
        the obsoletion record is the evidence.
        """
        rec = await self.term(ontology, iri)
        if rec.get("is_obsolete"):
            raise HardFailure(
                f"{current_id} is itself obsolete; it cannot be the current id")
        xrefs = (rec.get("annotation") or {}).get("database_cross_reference") or []
        ids = [current_id]
        notes: list[str] = []
        rejected: list[str] = []
        for x in xrefs:
            m = _EFO_XREF.match(str(x).strip())
            if not m:
                continue
            candidate = f"EFO_{m.group(1)}"
            if candidate == current_id:
                continue
            efo_iri = f"http://www.ebi.ac.uk/efo/{candidate}"
            try:
                efo = await self.term("efo", efo_iri)
            except HardFailure as e:
                rejected.append(f"{candidate}: EFO has no such term ({e})")
                continue
            replaced_by = efo.get("term_replaced_by") or ""
            if efo.get("is_obsolete") and replaced_by == iri:
                version = ((efo.get("annotation") or {})
                           .get("obsoleted_in_version") or [""])[0]
                ids.append(candidate)
                notes.append(
                    f"{candidate} is obsolete in EFO"
                    + (f" {version}" if version else "")
                    + f" and names {current_id} as its replacement; Expression "
                      f"Atlas still tags samples with it, so it is an Atlas-side "
                      f"id and is NEVER sent to Open Targets")
            elif not efo.get("is_obsolete"):
                # A live EFO term that cross-references this one. It is a real id
                # Atlas could be using, and it is not a *replacement* claim, so it
                # goes in with the reason stated rather than being asserted away.
                ids.append(candidate)
                notes.append(
                    f"{candidate} is a live EFO term cross-referenced by "
                    f"{current_id}; included as an Atlas-side id")
            else:
                rejected.append(
                    f"{candidate}: obsolete in EFO but replaced by "
                    f"{replaced_by or 'nothing stated'}, not by {current_id}")
        return ids, notes, rejected

    async def resolve(self, q: str, *, limit: int = 8) -> list[DiseaseRef]:
        """`DiseaseRef`s for a typed query, best match first.

        The order is OLS4's own relevance order, untouched. There is no re-ranking
        and no "recommended" hit: rule 7 applies to a list of diseases as much as
        to a list of genes, and OLS4's order is one source's own number.
        """
        docs = await self.select(q, obsoletes=False)
        out: list[DiseaseRef] = []
        seen: set[str] = set()
        for doc in docs:
            if len(out) >= limit:
                break
            sf = str(doc.get("short_form") or "")
            iri = str(doc.get("iri") or "")
            ontology = str(doc.get("ontology_name") or "")
            if not sf or not iri or sf in seen:
                continue
            if not sf.startswith(ATLAS_ID_PREFIXES):
                continue
            seen.add(sf)
            ids, notes, rejected = await self._atlas_ids_for(ontology, iri, sf)
            rec = await self.term(ontology, iri)
            out.append(DiseaseRef(
                label=str(rec.get("label") or doc.get("label") or sf),
                current_id=sf, atlas_ids=tuple(dict.fromkeys(ids)),
                synonyms=tuple(rec.get("synonyms") or ()),
                ols_fetched_utc=_utc_now(), mapping_notes=tuple(notes),
                rejected_ids=tuple(rejected), ontology=ontology))
        return out

    async def resolve_id(self, current_id: str) -> DiseaseRef:
        """The same ref, from an id the UI already has.

        The picker is reached with a `current_id` in a query string, and it needs
        the `atlas_ids` to look anything up. Re-deriving them from the id rather
        than trusting a round trip through the browser means the bridge is asserted
        on every request, not once at autocomplete time.
        """
        ontology, iri = _id_to_ontology_and_iri(current_id)
        rec = await self.term(ontology, iri)
        ids, notes, rejected = await self._atlas_ids_for(ontology, iri, current_id)
        return DiseaseRef(
            label=str(rec.get("label") or current_id), current_id=current_id,
            atlas_ids=tuple(dict.fromkeys(ids)),
            synonyms=tuple(rec.get("synonyms") or ()), ols_fetched_utc=_utc_now(),
            mapping_notes=tuple(notes), rejected_ids=tuple(rejected),
            ontology=ontology)


def _id_to_ontology_and_iri(short: str) -> tuple[str, str]:
    """`MONDO_0005011` -> (`mondo`, the OBO IRI); `EFO_0000384` -> (`efo`, the
    EBI IRI). The two prefixes live at different hosts and there is no rule that
    derives one from the other, so this is a lookup and not a format string."""
    if short.startswith("MONDO_"):
        return "mondo", f"http://purl.obolibrary.org/obo/{short}"
    if short.startswith("EFO_"):
        return "efo", f"http://www.ebi.ac.uk/efo/{short}"
    raise HardFailure(
        f"{short!r} is not an EFO or MONDO id. This build resolves diseases in "
        f"those two ontologies only (config.OLS4_ONTOLOGY_LIST).")


__all__ = ["ATLAS_ID_PREFIXES", "DiseaseRef", "OLSAdapter"]
