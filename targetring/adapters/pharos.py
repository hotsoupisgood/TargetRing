"""Pharos TDL — the Target Development Level, as a fifth Pareto axis.

COUNCIL S1 and V8, verified live 2026-09-24 against
`POST https://pharos-api.ncats.io/graphql` (no key):

    {target(q:{sym:"BRAF"}){name sym tdl fam novelty publicationCount}}
      -> tdl "Tclin", fam "Kinase", novelty 0.00253795, publicationCount 2631

    {targets{count facets(include:["Target Development Level"]){facet values{name value}}}}
      -> count 20412; Tbio 12303, Tdark 5501, Tchem 1904, Tclin 704

Those four numbers sum to exactly 20,412: **every target Pharos holds has a
TDL**, which is what S1 means by "the only axis with no missing values" and why
V2-REPORT §1.4 names it as the answer to a headline that keeps not appearing.

**What is actually complete, stated precisely.** TDL has no missing values
*within Pharos*. A submitted gene that Pharos does not hold at all returns
`target: null` (probed: `{target(q:{sym:"NOTAGENE"}){sym}}` -> `null`, HTTP 200,
no `errors` key) and is `no_data` here -- absence of a record, never Tdark.
Pharos holds 20,412 human targets against Open Targets' ~62,000 Ensembl genes,
so a non-coding gene or an unreviewed ORF will legitimately have no TDL.

**The ordinal is stated, never inferred** (Puck's condition). TDL is an ordered
knowledge level and the order is Pharos's own published one:

    Tdark  0  nothing known beyond the sequence
    Tbio   1  biology known; no chemical matter meeting the activity bar
    Tchem  2  small molecules meeting the activity bar exist
    Tclin  3  an approved drug acts through this target

Anything else Pharos might introduce is `no_data` naming the string -- an
unmapped level is a fact we do not have, not a low one. That is the same rule
`CLINICAL_STAGE_ORDINAL` follows, and the same defect it exists to prevent.

**What TDL is not.** S1 is explicit and it is repeated on the figure: TDL is a
*development and knowledge* level, not biophysical druggability. Tclin means
somebody already drugged it; Tdark means nobody has looked. It conflates
druggability with study depth more directly than Open Targets' tractability
does. It is the cleaner axis because it is explicit and complete, not because it
measures a pocket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..cache import Cache
from ..config import PHAROS_GRAPHQL
from ..http import Http, describe, graphql
from ..states import Cell, failed, no_data, value

# Stated, not inferred. See the module docstring.
TDL_ORDINAL: dict[str, int] = {
    "Tdark": 0,
    "Tbio": 1,
    "Tchem": 2,
    "Tclin": 3,
}
TDL_MEANING: dict[str, str] = {
    "Tdark": "nothing known beyond the sequence — nobody has looked",
    "Tbio": "biology known; no chemical matter meeting the activity bar",
    "Tchem": "small molecules meeting the activity bar exist",
    "Tclin": "an approved drug acts through this target",
}
TDL_MAX = max(TDL_ORDINAL.values())

SOURCE_LABEL = "Pharos TDL"

# One POST for the whole submitted set, using GraphQL aliases. Pharos has no
# batch `targets(syms: [...])` that returns one row per requested symbol, and
# aliases give the join-by-identifier discipline AGENTS.md rule 2 requires for
# free: the alias *is* the requested index, so a target Pharos does not hold
# comes back as `gN: null` in its own slot rather than shortening an array.
_ALIAS = "g{}"


@dataclass
class PharosRecord:
    symbol: str
    found: bool = False
    tdl: str | None = None
    family: str | None = None
    novelty: float | None = None
    error: str | None = None
    # Which key Pharos answered on: the current symbol, or the UniProt
    # accession when Pharos still holds the gene under a superseded symbol.
    matched_on: str = ""
    pharos_symbol: str | None = None


def build_query(keys: list[str], field: str = "sym") -> str:
    parts = []
    for i, key in enumerate(keys):
        safe = str(key).replace("\\", "").replace('"', "")
        parts.append(
            f'{_ALIAS.format(i)}:target(q:{{{field}:"{safe}"}}){{sym tdl fam novelty}}')
    return "{" + " ".join(parts) + "}"


class PharosAdapter:
    source = "pharos.target"

    def __init__(self, http: Http, cache: Cache, batch_size: int = 40) -> None:
        self.http = http
        self.cache = cache
        self.batch_size = batch_size
        self.failure: str | None = None

    async def fetch_many(
        self, symbols: list[str], uniprot_by_symbol: dict[str, str | None] | None = None
    ) -> dict[str, PharosRecord]:
        """TDL for each submitted symbol, with a UniProt second attempt.

        Measured 2026-09-24 on `fixtures/de_realistic.csv`: Pharos returned
        `null` for SEPTIN9 and MARCHF1, which are the **current** HGNC symbols
        for SEPT9 and MARCH1 -- the two genes PLAN §4 names as Excel's date
        corruption cases. Pharos still keys them under the old symbols, so a
        symbol lookup alone made a gene ineligible for the headline because of a
        naming convention rather than a fact about the target.

        AGENTS.md rule 5 already resolves symbol -> Ensembl -> UniProt once,
        before any ring, so the accession is in hand. `target(q:{uniprot:"..."})`
        is probed and works (`P15056` -> BRAF/Tclin). Symbol first, accession
        second, and the record says which key answered.
        """
        wanted = list(dict.fromkeys(symbols))
        uniprot_by_symbol = uniprot_by_symbol or {}
        out: dict[str, PharosRecord] = {}
        pending: list[str] = []
        for s in wanted:
            hit = self.cache.get(self.source, s)
            if hit is None:
                pending.append(s)
            else:
                out[s] = _from_payload(s, hit, matched_on="sym (cached)")

        batches = [pending[i:i + self.batch_size]
                   for i in range(0, len(pending), self.batch_size)]
        for res in await asyncio.gather(*(self._batch(b, "sym") for b in batches)):
            out.update(res)

        # Second pass, only for the genes Pharos did not hold under their
        # current symbol and for which resolution produced an accession.
        retry = {
            s: acc for s in wanted
            if (r := out.get(s)) is not None and not r.found and r.error is None
            and (acc := uniprot_by_symbol.get(s))
        }
        if retry:
            syms = list(retry)
            accs = [retry[s] for s in syms]
            batches = [(syms[i:i + self.batch_size], accs[i:i + self.batch_size])
                       for i in range(0, len(syms), self.batch_size)]
            for names, keys in batches:
                found = await self._batch(keys, "uniprot", label_as=names)
                for name in names:
                    rec = found.get(name)
                    if rec is not None and rec.found:
                        out[name] = rec

        return {s: out.get(s, PharosRecord(s, error="not returned")) for s in wanted}

    async def _batch(
        self, keys: list[str], field: str, label_as: list[str] | None = None
    ) -> dict[str, PharosRecord]:
        if not keys:
            return {}
        names = label_as or keys
        try:
            data = await graphql(self.http, PHAROS_GRAPHQL, build_query(keys, field))
        except Exception as e:  # noqa: BLE001 -- recorded, surfaced as `failed`
            self.failure = describe(e)
            return {n: PharosRecord(n, error=describe(e)) for n in names}
        out: dict[str, PharosRecord] = {}
        for i, name in enumerate(names):
            alias = _ALIAS.format(i)
            payload = data.get(alias)
            # A null slot is Pharos saying "no such target", which is a real
            # answer and is cached. A missing alias is a response shape we did
            # not ask for and is not.
            if alias not in data:
                out[name] = PharosRecord(name, error="alias missing from the Pharos response")
                continue
            # Only a symbol lookup is cached under the gene name; a UniProt hit
            # is cached too, because it is the answer for that gene either way.
            if payload or field == "sym":
                self.cache.put(self.source, name, payload)
            out[name] = _from_payload(name, payload, matched_on=f"{field}={keys[i]}")
        return out


def _from_payload(
    symbol: str, payload: dict[str, Any] | None, matched_on: str = ""
) -> PharosRecord:
    if not payload:
        return PharosRecord(symbol, found=False, matched_on=matched_on)
    return PharosRecord(
        symbol=symbol,
        found=True,
        tdl=payload.get("tdl"),
        family=payload.get("fam"),
        novelty=payload.get("novelty"),
        matched_on=matched_on,
        pharos_symbol=payload.get("sym"),
    )


def tdl_cell(rec: PharosRecord) -> Cell:
    """COUNCIL S1/V8 — the TDL axis, on an explicit ordinal."""
    if rec.error:
        return failed(f"{SOURCE_LABEL}: {rec.error}")
    if not rec.found:
        return no_data(
            f"Pharos holds no target record for {rec.symbol}. Pharos covers 20,412 human "
            f"targets; absence of a record is not Tdark, which is a measured level meaning "
            f"'nobody has looked at a target we do hold'."
        )
    raw_tdl = str(rec.tdl or "")
    ordinal = TDL_ORDINAL.get(raw_tdl)
    if ordinal is None:
        return no_data(
            f"Pharos returned Target Development Level {raw_tdl!r}, which this build does "
            f"not map. Mapped levels: {', '.join(f'{k}={v}' for k, v in TDL_ORDINAL.items())}. "
            f"An unmapped level is a fact we do not have, not a low one."
        )
    renamed = ""
    if rec.pharos_symbol and rec.pharos_symbol != rec.symbol:
        renamed = (f"; Pharos holds this target as {rec.pharos_symbol}, matched on "
                   f"{rec.matched_on}")
    return value(
        float(ordinal),
        f"{raw_tdl} ({ordinal}/{TDL_MAX}) — {TDL_MEANING[raw_tdl]}"
        + (f"; family {rec.family}" if rec.family else "")
        + renamed
        + ". TDL is a DEVELOPMENT/KNOWLEDGE level, not biophysical "
          "druggability: Tclin means somebody already drugged it.",
        raw={"tdl": raw_tdl, "ordinal": ordinal, "family": rec.family,
             "novelty": rec.novelty, "source": SOURCE_LABEL,
             "matched_on": rec.matched_on, "pharos_symbol": rec.pharos_symbol},
    )


__all__ = [
    "PharosAdapter", "PharosRecord", "tdl_cell", "build_query",
    "TDL_ORDINAL", "TDL_MEANING", "TDL_MAX", "SOURCE_LABEL",
]
