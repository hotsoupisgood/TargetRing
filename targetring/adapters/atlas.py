"""EBI Expression Atlas — the catalogue, the contrast configuration, the SDRF.

Four fetchers and three parsers. Everything here goes through the shared `Http`,
so `AGENTS.md` rule 9 holds for the index build as much as for a single request:
four concurrent per host, and a 429 aborts rather than retrying.

**What this module is careful about, and why each one is a real trap.**

1. **`{ACC}.condensed-sdrf.tsv` uses a DOT.** Every other file in the directory
   uses a dash — `{ACC}-configuration.xml`, `{ACC}-analytics.tsv` — and the
   `-condensed-sdrf.tsv` form returns HTTP 404 with an Apache HTML body (verified
   2026-09-29). One character, and the failure arrives as an HTML page rather
   than as an error, which is `http.json_or_raise`'s whole reason for existing.
   The suffix is a pinned constant in `config` so there is one place to be wrong.

2. **The catalogue carries no ontology ids at all.** `experimentalFactors` is
   `["clinical information", "disease"]` — factor *names*, not values, and
   certainly not EFO ids. The disease ids exist only per-assay in the condensed
   SDRF. So "how many Crohn's contrasts are there" is not a question the
   catalogue can answer, and `atlas_index.py` exists because of that.

3. **Assay groups are scoped to their `<analytics>` block, not to the file.** A
   microarray experiment with two array designs has two `<analytics>` blocks,
   each with its own `<assay_groups>` and its own `g1`. Collecting groups with a
   document-wide `iter("assay_group")` would let one block's `g1` supply the n
   and the label for another block's contrast — a wrong n per side on a picker
   whose entire job is to show n per side. Groups are parsed per block.

4. **`factor` rows and `characteristic` rows are kept apart.** Both carry
   ontology URIs and they mean different things: a normal-tissue sample taken
   from a Crohn's patient carries Crohn's as a *characteristic* of the donor and
   `normal` as the experimental *factor*. `E-GEOD-57945 g6_g4` — the spike's
   null contrast, "Crohn's, normal tissue" vs control — is exactly that case.
   Matching a disease on characteristics would file that contrast under Crohn's
   disease; matching on factors files it where it belongs. The index keeps both
   and the disease filter reads the factors.

5. **The platform comes from the XML's `<array_design>`, per block.** The
   condensed SDRF also carries an array design column, and it is not used for
   this: the contrast belongs to an `<analytics>` block and the block states its
   own platform. Two sources for one fact is how they come to disagree.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .. import config
from ..cache import Cache
from ..contrast import RNASEQ, ContrastRef
from ..http import HardFailure, Http

#: A well-formed Atlas accession. Used to keep a catalogue row's own accession
#: out of a URL unchecked -- the index build interpolates ~369 of them.
ACCESSION_RE = re.compile(r"^E-[A-Z]+-\d+$")


def experiment_base(acc: str) -> str:
    if not ACCESSION_RE.match(acc):
        raise HardFailure(
            f"{acc!r} is not an Expression Atlas accession (E-XXXX-nnn). Refusing "
            f"to build a URL from it.")
    return config.ATLAS_EXPERIMENT_BASE.format(acc=acc)


def configuration_url(acc: str) -> str:
    return f"{experiment_base(acc)}{acc}-configuration.xml"


def condensed_sdrf_url(acc: str) -> str:
    # The dot. See the module docstring, item 1.
    return f"{experiment_base(acc)}{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}"


def analytics_url(acc: str, platform: str = RNASEQ) -> str:
    """The differential statistics file.

    Two layouts (spike §Awkward 4): RNA-seq is `{ACC}-analytics.tsv`, microarray
    is `{ACC}_{PLATFORM}-analytics.tsv`. Stage 3 reads the first and stage 6 the
    second; the URL is built here so a ref carries the right one from the start
    rather than having it guessed at read time.
    """
    stem = acc if platform == RNASEQ else f"{acc}_{platform}"
    return f"{experiment_base(acc)}{stem}-analytics.tsv"


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

def _text_or_raise(r: httpx.Response, expect: tuple[str, ...], what: str) -> str:
    """QA N1 in this module's shape: status is necessary, never sufficient.

    `ftp.ebi.ac.uk` answers a missing file with HTTP 404 and an HTML body, and a
    404 is caught upstream by `_check_status`. What this catches is the other half
    -- a 200 whose body is a directory listing or an error page, which
    `ET.fromstring` would reject noisily and a TSV split would accept silently as
    one very strange row.
    """
    ctype = (r.headers.get("content-type") or "").lower()
    if not any(e in ctype for e in expect):
        raise HardFailure(
            f"expected {what} from {r.request.url}, got content-type {ctype!r} "
            f"({len(r.content)} B, starts {r.text[:120]!r})")
    return r.text


class AtlasAdapter:
    """Cached reads of the four Atlas files stage 1 needs."""

    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def catalogue(self) -> list[dict[str, Any]]:
        """Every experiment in the Atlas, as the catalogue lists them.

        4,562 rows today. The `Accept: application/json` trap the brief described
        did not reproduce (spike correction: a bare GET returns
        `application/json`), so the header is sent anyway and the content type is
        checked regardless -- the check is what matters, not the header.
        """
        cached = self.cache.get("atlas.catalogue", config.ATLAS_CATALOGUE_URL)
        if cached is None:
            r = await self.http.get(config.ATLAS_CATALOGUE_URL)
            body = json.loads(_text_or_raise(r, ("json",), "the experiments catalogue"))
            cached = body["experiments"] if isinstance(body, dict) else body
            if not isinstance(cached, list) or not cached:
                raise HardFailure(
                    f"the Atlas catalogue came back as "
                    f"{type(cached).__name__} of {len(cached or [])}; expected a "
                    f"non-empty list of experiments")
            self.cache.put("atlas.catalogue", config.ATLAS_CATALOGUE_URL, cached)
        return cached

    async def configuration(self, acc: str) -> str:
        cached = self.cache.get("atlas.configuration", acc)
        if cached is None:
            r = await self.http.get(configuration_url(acc))
            cached = _text_or_raise(r, ("xml",), f"{acc}-configuration.xml")
            self.cache.put("atlas.configuration", acc, cached)
        return cached

    async def condensed_sdrf(self, acc: str) -> str:
        cached = self.cache.get("atlas.condensed_sdrf", acc)
        if cached is None:
            r = await self.http.get(condensed_sdrf_url(acc))
            cached = _text_or_raise(
                r, ("tab-separated", "text/plain"),
                f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}")
            self.cache.put("atlas.condensed_sdrf", acc, cached)
        return cached


# ---------------------------------------------------------------------------
# catalogue selection
# ---------------------------------------------------------------------------

def is_human_differential_disease(row: dict[str, Any]) -> bool:
    """The index's population: human, `Differential`, with a `disease` factor.

    369 experiments today (spike correction: 369, not the brief's 381). The
    factor test is on the factor NAME, which is all the catalogue has -- the
    values and their ontology ids arrive with the condensed SDRF.
    """
    species = row.get("species") or ""
    kind = row.get("experimentType") or ""
    factors = [str(f).strip().lower() for f in (row.get("experimentalFactors") or [])]
    return ("Homo sapiens" in species and "Differential" in kind
            and "disease" in factors)


def select_index_population(catalogue: Iterable[dict[str, Any]]) -> list[str]:
    accs = sorted({str(r.get("experimentAccession") or "") for r in catalogue
                   if is_human_differential_disease(r)})
    bad = [a for a in accs if not ACCESSION_RE.match(a)]
    if bad:
        raise HardFailure(
            f"the catalogue offered {len(bad)} accession(s) this build will not put "
            f"in a URL: {bad[:5]}")
    return accs


# ---------------------------------------------------------------------------
# -configuration.xml
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssayGroup:
    group_id: str
    label: str
    assays: tuple[str, ...]

    @property
    def n(self) -> int:
        return len(self.assays)


@dataclass
class ParsedContrast:
    """One `<contrast>` as the XML states it, before I8 is enforced.

    A dict-like intermediate exists on purpose. `ContrastRef`'s constructor
    refuses an id that is not `{ref}_{test}`, and the picker's requirement is that
    such a contrast is *shown* as `INVALID — sign unsafe` rather than dropped
    (plan stage 2's "done when"). A parse that could only produce valid refs would
    have to throw the row away to report it, so the parse produces this and
    `as_ref()` is where I8 bites.
    """

    accession: str
    contrast_id: str
    ref_group: str
    test_group: str
    ref_label: str
    test_label: str
    n_ref: int
    n_test: int
    name: str
    platform: str
    ref_assays: tuple[str, ...] = ()
    test_assays: tuple[str, ...] = ()

    @property
    def id_is_ref_test(self) -> bool:
        return self.contrast_id == f"{self.ref_group}_{self.test_group}"

    @property
    def invalid_reason(self) -> str:
        """Empty when the contrast is safe to draw; a sentence when it is not."""
        if not self.id_is_ref_test:
            return (f"contrast id {self.contrast_id!r} is not "
                    f"{self.ref_group}_{self.test_group}; the sign of every log2FC "
                    f"is unestablished")
        if min(self.n_ref, self.n_test) < 1:
            return "a side has no assays"
        return ""

    def as_ref(self, **extra: Any) -> ContrastRef:
        """Raises `ContrastIdentityError` when I8 fails. That is the point."""
        return ContrastRef(
            accession=self.accession, contrast_id=self.contrast_id,
            ref_group=self.ref_group, test_group=self.test_group,
            ref_label=self.ref_label, test_label=self.test_label,
            n_ref=self.n_ref, n_test=self.n_test, name=self.name,
            platform=self.platform,
            configuration_url=configuration_url(self.accession),
            analytics_url=analytics_url(self.accession, self.platform),
            **extra)


def parse_configuration(xml: str, acc: str) -> list[ParsedContrast]:
    """Every contrast in the file, with its groups' labels, sizes and assays.

    Per `<analytics>` block, because the groups are scoped to the block (module
    docstring item 3). The block's `<array_design>` is the contrast's platform;
    its absence means RNA-seq.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise HardFailure(f"{acc}-configuration.xml did not parse: {e}") from e

    out: list[ParsedContrast] = []
    blocks = list(root.iter("analytics"))
    if not blocks:
        raise HardFailure(
            f"{acc}-configuration.xml has no <analytics> block; this is not a "
            f"differential experiment configuration")
    for block in blocks:
        designs = [(a.text or "").strip() for a in block.iter("array_design")]
        designs = [d for d in designs if d]
        platform = designs[0] if designs else RNASEQ
        groups: dict[str, AssayGroup] = {}
        for g in block.iter("assay_group"):
            gid = g.get("id") or ""
            assays = tuple((a.text or "").strip() for a in g.iter("assay"))
            groups[gid] = AssayGroup(group_id=gid, label=(g.get("label") or "").strip(),
                                     assays=assays)
        for c in block.iter("contrast"):
            ref = (c.findtext("reference_assay_group") or "").strip()
            test = (c.findtext("test_assay_group") or "").strip()
            rg, tg = groups.get(ref), groups.get(test)
            if rg is None or tg is None:
                raise HardFailure(
                    f"{acc} contrast {c.get('id')!r} names assay group(s) "
                    f"{[x for x in (ref, test) if x not in groups]} that this "
                    f"<analytics> block does not define. Filling in the n from "
                    f"another block's group of the same name is how a picker comes "
                    f"to print the wrong n per side.")
            out.append(ParsedContrast(
                accession=acc, contrast_id=(c.get("id") or "").strip(),
                ref_group=ref, test_group=test,
                ref_label=rg.label, test_label=tg.label,
                n_ref=rg.n, n_test=tg.n,
                # The prose name, verbatim. Never rebuilt from the labels: a name
                # built from two tokens can be built the wrong way round.
                name=(c.findtext("name") or "").strip(),
                platform=platform,
                ref_assays=rg.assays, test_assays=tg.assays))
    return out


# ---------------------------------------------------------------------------
# .condensed-sdrf.tsv
# ---------------------------------------------------------------------------

@dataclass
class AssayAnnotation:
    """One assay's ontology URIs, factors and characteristics kept apart."""

    factors: dict[str, tuple[str, str]] = field(default_factory=dict)
    characteristics: dict[str, tuple[str, str]] = field(default_factory=dict)

    def _uris(self, table: dict[str, tuple[str, str]]) -> set[str]:
        return {uri for _, uri in table.values() if uri}

    @property
    def factor_uris(self) -> set[str]:
        return self._uris(self.factors)

    @property
    def characteristic_uris(self) -> set[str]:
        return self._uris(self.characteristics)

    def factor_uris_short(self) -> set[str]:
        return {short_form(u) for u in self.factor_uris}

    def characteristic_uris_short(self) -> set[str]:
        return {short_form(u) for u in self.characteristic_uris}


#: `characteristic` / `factor`, column 4 of the condensed SDRF.
SDRF_KINDS = ("characteristic", "factor")


def parse_condensed_sdrf(text: str, acc: str) -> dict[str, AssayAnnotation]:
    """`assay id -> annotation`.

    Six or seven tab-separated columns: accession, array design (blank for
    RNA-seq), assay, `characteristic`|`factor`, name, value, and the ontology URI
    where there is one. A row with six columns has no URI, which is a fact about
    that annotation and not a missing value to fill in.
    """
    out: dict[str, AssayAnnotation] = {}
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) not in (6, 7):
            raise HardFailure(
                f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}:{n} has {len(parts)} "
                f"columns, expected 6 or 7: {raw[:160]!r}")
        row_acc, _design, assay, kind, name, value = parts[:6]
        uri = parts[6].strip() if len(parts) == 7 else ""
        if row_acc != acc:
            raise HardFailure(
                f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}:{n} is labelled "
                f"{row_acc!r}. A file serving another experiment's annotations "
                f"would attach the wrong disease to every assay in it.")
        if kind not in SDRF_KINDS:
            raise HardFailure(
                f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX}:{n} column 4 is "
                f"{kind!r}, expected one of {SDRF_KINDS}")
        ann = out.setdefault(assay, AssayAnnotation())
        table = ann.factors if kind == "factor" else ann.characteristics
        table[name.strip().lower()] = (value.strip(), uri)
    if not out:
        raise HardFailure(
            f"{acc}{config.ATLAS_CONDENSED_SDRF_SUFFIX} annotated no assays")
    return out


def short_form(uri: str) -> str:
    """`http://www.ebi.ac.uk/efo/EFO_0000384` -> `EFO_0000384`.

    The last path segment, and nothing cleverer: OBO and EFO IRIs differ in
    everything before it (`purl.obolibrary.org/obo/` vs `www.ebi.ac.uk/efo/`) and
    agree on it, which is the only reason ids from the two can be compared at all.
    """
    return uri.rstrip("/").rpartition("/")[2]


def group_values(annotations: dict[str, AssayAnnotation], assays: Iterable[str],
                 name: str) -> list[str]:
    """The distinct values of one annotation across a group's assays.

    Factors first, characteristics as the fallback, and the two are not merged:
    what an experiment *varied* is the factor, and a characteristic is used only
    where there is no factor of that name at all. Used for the picker's tissue
    column, where an empty cell would read as "this contrast has no tissue".
    """
    from_factors: set[str] = set()
    from_characteristics: set[str] = set()
    for a in assays:
        ann = annotations.get(a)
        if ann is None:
            continue
        if name in ann.factors:
            from_factors.add(ann.factors[name][0])
        elif name in ann.characteristics:
            from_characteristics.add(ann.characteristics[name][0])
    return sorted(from_factors or from_characteristics)


def group_uris(annotations: dict[str, AssayAnnotation], assays: Iterable[str],
               factor: str = "disease") -> dict[str, Any]:
    """The disease ids on one assay group's assays, factors and characteristics
    kept apart, plus the assays the SDRF does not annotate at all.

    The last of those three is reported rather than ignored. An assay group whose
    assays are absent from the condensed SDRF has no disease id, and a contrast
    that is silently filed under no disease disappears from every picker without
    anyone learning that it did.
    """
    f_ids: set[str] = set()
    c_ids: set[str] = set()
    missing: list[str] = []
    for a in assays:
        ann = annotations.get(a)
        if ann is None:
            missing.append(a)
            continue
        fv, furi = ann.factors.get(factor, ("", ""))
        cv, curi = ann.characteristics.get(factor, ("", ""))
        if furi:
            f_ids.add(short_form(furi))
        if curi:
            c_ids.add(short_form(curi))
        del fv, cv
    return {"factor_ids": sorted(f_ids), "characteristic_ids": sorted(c_ids),
            "unannotated_assays": missing}


__all__ = [
    "ACCESSION_RE", "AssayAnnotation", "AssayGroup", "AtlasAdapter",
    "ParsedContrast", "analytics_url", "condensed_sdrf_url", "configuration_url",
    "experiment_base", "group_uris", "group_values", "is_human_differential_disease",
    "parse_condensed_sdrf", "parse_configuration", "select_index_population",
    "short_form",
]
