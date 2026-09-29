"""The local Atlas contrast index — built once, read on every autocomplete.

**Why this file exists.** "Show the contrast count in the disease autocomplete"
sounds like a catalogue lookup and is not one: the Atlas experiments catalogue
carries *no ontology ids at all* (`experimentalFactors: ["clinical information",
"disease"]` — factor names, free text descriptions, nothing more). The EFO ids
live per-assay in `{ACC}.condensed-sdrf.tsv` and the contrast prose names in
`{ACC}-configuration.xml`. There is no endpoint that answers "how many Crohn's
contrasts are there", so the answer is crawled once and kept.

**What the build costs, and how it behaves.** ~369 human differential
experiments carrying a `disease` factor, two files each: ≈740 GETs through the
shared `Http`, four concurrent per host. **A 429 aborts the build and says so.**
There is no retry loop and no partial index written after one — `AGENTS.md` rule
9, and a half-crawled index would under-count every disease in it while looking
complete.

**Three things the index records that a count alone would hide:**

* `built_utc`, printed *wherever* its counts are shown. A contrast count is a
  measurement with a date on it, and a month-old number rendered like a live one
  is a small plausible wrong figure.
* `failed`, the experiments whose files did not parse, with the reason. Counted
  and named on the picker. Silently skipping four experiments is how a picker
  comes to show 119 contrasts and call it 123.
* the disease ids **per side**, factors and characteristics kept apart, so a
  contrast is filed by what was *varied* rather than by what the donors happened
  to have. The spike's null contrast — `E-GEOD-57945 g6_g4`, Crohn's patients'
  *normal* tissue against controls — is the case that makes the distinction
  matter: its test group's donors have Crohn's as a characteristic and `normal`
  as the factor.

**The 16 excluded contrasts.** The spike found 139 contrasts in the Crohn's
experiments and 123 whose test side is actually Crohn's; the other 16 are
ulcerative colitis or generic IBD. They are excluded here by **ontology id on the
test group**, never by a regex on the prose name — `E-GEOD-100833`'s group labels
say "inflammatory bowel disease" while its assays are tagged `EFO_0000384`, so
prose and ids disagree on the very experiment that supplies most of the rows.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import config
from .adapters import atlas as atlas_mod
from .adapters.atlas import AtlasAdapter
from .adapters.ols import DiseaseRef
from .cache import Cache
from .contrast import RNASEQ, ContrastRef
from .http import HardFailure, Http, RateLimited

#: Bumped by hand when a row's fields change shape. Written into the file and
#: checked on load: an index written by an older parse is refused, not read with
#: today's field names against yesterday's values.
INDEX_SCHEMA_VERSION = config.SCHEMA_VERSIONS["atlas.index"]

#: The factor whose ontology ids file a contrast under a disease.
DISEASE_FACTOR = "disease"

#: The annotation the picker's tissue column shows.
ORGANISM_PART_FACTOR = "organism part"


def index_path(root: Path | None = None) -> Path:
    return (Path(root or config.CACHE_DIR) / "atlas.index"
            / f"v{INDEX_SCHEMA_VERSION}" / "index.json")


@dataclass(frozen=True)
class IndexedContrast:
    """One contrast as the index stores it.

    Deliberately NOT a `ContrastRef`. A `ContrastRef` cannot exist unless I8
    holds, and the picker's job includes showing a contrast whose id is not
    `{ref}_{test}` as `INVALID — sign unsafe` rather than dropping it. So the
    index stores the raw fact plus `invalid_reason`, and `as_ref()` is where I8
    bites — which is at the point a figure would be drawn, not at the point a row
    would be listed.
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
    #: Disease ids on the test side's assays, from the SDRF's `factor` rows.
    test_disease_ids: tuple[str, ...] = ()
    ref_disease_ids: tuple[str, ...] = ()
    #: The same from `characteristic` rows. Kept because they are a different
    #: fact, and never used to file a contrast under a disease.
    test_characteristic_disease_ids: tuple[str, ...] = ()
    ref_characteristic_disease_ids: tuple[str, ...] = ()
    #: The `organism part` values on each side — the picker's tissue column.
    #: Distinct values, factors preferred, characteristics as the fallback.
    test_organism_parts: tuple[str, ...] = ()
    ref_organism_parts: tuple[str, ...] = ()
    #: Assays the condensed SDRF does not annotate at all. Reported on the row.
    unannotated_assays: int = 0
    #: Empty when the contrast is safe to draw; a sentence when it is not.
    invalid_reason: str = ""

    @property
    def is_rnaseq(self) -> bool:
        return self.platform == RNASEQ

    @property
    def min_n(self) -> int:
        return min(self.n_ref, self.n_test)

    def as_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IndexedContrast":
        tuples = {"test_disease_ids", "ref_disease_ids",
                  "test_characteristic_disease_ids",
                  "ref_characteristic_disease_ids",
                  "test_organism_parts", "ref_organism_parts"}
        return cls(**{k: (tuple(v) if k in tuples else v) for k, v in d.items()})

    def as_ref(self, index_built_utc: str = "") -> ContrastRef:
        """Raises `ContrastIdentityError` when I8 fails."""
        return ContrastRef(
            accession=self.accession, contrast_id=self.contrast_id,
            ref_group=self.ref_group, test_group=self.test_group,
            ref_label=self.ref_label, test_label=self.test_label,
            n_ref=self.n_ref, n_test=self.n_test, name=self.name,
            platform=self.platform,
            configuration_url=atlas_mod.configuration_url(self.accession),
            analytics_url=atlas_mod.analytics_url(self.accession, self.platform),
            index_built_utc=index_built_utc)


@dataclass(frozen=True)
class DiseaseContrastSummary:
    """The numbers the autocomplete line is built from, with their build date."""

    disease_ids: tuple[str, ...]
    n_contrasts: int
    n_rnaseq: int
    n_microarray: int
    n_experiments: int
    built_utc: str
    stale: bool
    n_failed_experiments: int

    def as_dict(self) -> dict[str, Any]:
        # Both derived strings, because both are printed and neither is the
        # browser's to compose: `built_text` carries the STALE marker, and a page
        # that built its own version of that sentence could disagree with the
        # index about whether the count it is showing is current.
        return {**{k: (list(v) if isinstance(v, tuple) else v)
                   for k, v in self.__dict__.items()},
                "count_text": self.count_text,
                "built_text": self.built_text}

    @property
    def count_text(self) -> str:
        """`123 contrasts (22 RNA-seq, 101 microarray) in 9 experiments`.

        Zero is printed as "0 contrasts" and the entry stays selectable: a disease
        OLS4 resolves and Atlas has never studied is a real answer, and the picker
        page then says which of the two it is.
        """
        if not self.n_contrasts:
            return "0 contrasts"
        return (f"{self.n_contrasts} contrast"
                f"{'' if self.n_contrasts == 1 else 's'} "
                f"({self.n_rnaseq} RNA-seq, {self.n_microarray} microarray) in "
                f"{self.n_experiments} experiment"
                f"{'' if self.n_experiments == 1 else 's'}")

    @property
    def built_text(self) -> str:
        date = (self.built_utc or "unknown")[:10]
        return f"index built {date}" + (" — STALE" if self.stale else "")


@dataclass
class AtlasIndex:
    """The whole index, in memory. Small: ~740 experiments' worth of rows."""

    built_utc: str = ""
    schema_version: str = INDEX_SCHEMA_VERSION
    n_experiments: int = 0
    contrasts: list[IndexedContrast] = field(default_factory=list)
    #: `[{accession, reason}]`. Counted and named, never silently skipped.
    failed: list[dict[str, str]] = field(default_factory=list)
    #: The accession list the build walked, so a partial build is identifiable.
    population: list[str] = field(default_factory=list)

    # -- persistence -------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "built_utc": self.built_utc,
            "n_experiments": self.n_experiments,
            "n_contrasts": len(self.contrasts),
            "failed": self.failed,
            "population": self.population,
            "contrasts": [c.as_dict() for c in self.contrasts],
        }

    def save(self, path: Path | None = None) -> Path:
        p = Path(path or index_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=1))
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, path: Path | None = None) -> "AtlasIndex":
        p = Path(path or index_path())
        if not p.exists():
            raise IndexMissing(
                f"no Atlas contrast index at {p}. Build it once with "
                f"`.venv/bin/python scripts/build_atlas_index.py` (~740 GETs, a "
                f"few minutes). Until then the picker has no contrast counts, and "
                f"it says so rather than showing zeros.")
        body = json.loads(p.read_text())
        got = str(body.get("schema_version"))
        if got != INDEX_SCHEMA_VERSION:
            raise IndexMissing(
                f"the index at {p} was written by schema v{got} and this build "
                f"reads v{INDEX_SCHEMA_VERSION}. Rebuild it; a row parsed under "
                f"one schema and read under another is a count nobody measured.")
        return cls(
            built_utc=str(body.get("built_utc") or ""),
            schema_version=got,
            n_experiments=int(body.get("n_experiments") or 0),
            contrasts=[IndexedContrast.from_dict(d) for d in body.get("contrasts", [])],
            failed=list(body.get("failed") or []),
            population=list(body.get("population") or []),
        )

    # -- reading -----------------------------------------------------------

    @property
    def n_contrasts(self) -> int:
        return len(self.contrasts)

    @property
    def n_contrasts_without_disease_id(self) -> int:
        """Contrasts whose test group carries no `disease` factor id at all.

        147 of 1,126 on the 2026-09-29 build. They appear in **no** disease's
        picker, which is the right behaviour -- a contrast with no disease id is
        not evidence about a disease -- and it is also a silent 13% of the index,
        so the number is carried out to where a reader can see it rather than
        being a property of the filter nobody states.
        """
        return sum(1 for c in self.contrasts if not c.test_disease_ids)

    @property
    def n_contrasts_invalid_id(self) -> int:
        """Contrasts whose id is not `{ref}_{test}` (I8). 8 of 1,126."""
        return sum(1 for c in self.contrasts if c.invalid_reason)

    def provenance(self) -> dict[str, Any]:
        """What the index is, as the picker and the manifest print it."""
        return {
            "built_utc": self.built_utc,
            "schema_version": self.schema_version,
            "stale": self.is_stale(),
            "stale_after_days": config.ATLAS_INDEX_STALE_DAYS,
            "n_experiments": self.n_experiments,
            "n_experiments_in_population": len(self.population),
            "n_contrasts": self.n_contrasts,
            "n_contrasts_without_disease_id": self.n_contrasts_without_disease_id,
            "n_contrasts_invalid_id": self.n_contrasts_invalid_id,
            "n_failed_experiments": len(self.failed),
            "failed": self.failed,
        }

    def is_stale(self, days: int | None = None) -> bool:
        """Older than `config.ATLAS_INDEX_STALE_DAYS`. A flag, never a refusal:
        a month-old count is still the count that was measured, and refusing would
        leave the picker with no numbers at all rather than with dated ones."""
        if not self.built_utc:
            return True
        limit = config.ATLAS_INDEX_STALE_DAYS if days is None else days
        try:
            built = datetime.fromisoformat(self.built_utc)
        except ValueError:
            return True
        if built.tzinfo is None:
            built = built.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - built > timedelta(days=limit)

    def contrasts_for(self, atlas_ids: Iterable[str]) -> list[IndexedContrast]:
        """Contrasts whose **test** side carries one of these ids.

        On the test side and on the factor ids, both deliberately:

        * the test side, because the contrast is *about* its test group, and the
          sign sentence names the test group as the direction of "up";
        * the factor ids, because a control group of normal tissue taken from
          patients with the disease carries the disease as a characteristic of the
          donor. Filing that under the disease is how a null contrast ends up in a
          disease's picker.

        Ordered by accession then contrast id, which is the picker's stated
        default. No relevance, no recommendation, nothing composite (rule 7).
        """
        want = {i for i in atlas_ids if i}
        rows = [c for c in self.contrasts if want & set(c.test_disease_ids)]
        return sorted(rows, key=lambda c: (c.accession, c.contrast_id))

    def summary_for(self, atlas_ids: Iterable[str]) -> DiseaseContrastSummary:
        ids = tuple(sorted({i for i in atlas_ids if i}))
        rows = self.contrasts_for(ids)
        return DiseaseContrastSummary(
            disease_ids=ids,
            n_contrasts=len(rows),
            n_rnaseq=sum(1 for c in rows if c.is_rnaseq),
            n_microarray=sum(1 for c in rows if not c.is_rnaseq),
            n_experiments=len({c.accession for c in rows}),
            built_utc=self.built_utc,
            stale=self.is_stale(),
            n_failed_experiments=len(self.failed),
        )

    def autocomplete_entry(self, ref: DiseaseRef) -> dict[str, Any]:
        """One row of `/api/diseases`, count string and build date included.

        The count string is built from the index and never from the catalogue,
        because the catalogue cannot answer the question (module docstring).
        """
        s = self.summary_for(ref.atlas_ids)
        return {
            "label": ref.label,
            "current_id": ref.current_id,
            "atlas_ids": list(ref.atlas_ids),
            "obsolete_atlas_ids": list(ref.obsolete_atlas_ids),
            "synonyms": list(ref.synonyms)[:12],
            "mapping_notes": list(ref.mapping_notes),
            "rejected_ids": list(ref.rejected_ids),
            "ols_fetched_utc": ref.ols_fetched_utc,
            "counts": s.as_dict(),
            # `Crohn disease · MONDO_0005011 — 123 contrasts (22 RNA-seq,
            #  101 microarray) in 9 experiments · index built 2026-09-29`
            "text": (f"{ref.label} · {ref.current_id} — {s.count_text} · "
                     f"{s.built_text}"),
        }


# ---------------------------------------------------------------------------
# the picker
# ---------------------------------------------------------------------------

#: Below this on either side, the picker flags the contrast. 75 of the 138
#: Crohn's contrasts. Not a threshold that removes anything -- a label.
SMALL_N = 8
#: And below this, a second, louder flag. 18 of 138, minimum n = 3.
VERY_SMALL_N = 4


def _flags(c: IndexedContrast) -> list[dict[str, str]]:
    """Every reason a reader should look twice at this row, all of them visible.

    None of these removes a row. The picker's whole job is to make a bad contrast
    *legible*, and a filter that quietly dropped the 121 microarray rows would
    leave a reader believing Atlas has 17 Crohn's contrasts.
    """
    out: list[dict[str, str]] = []
    if c.invalid_reason:
        out.append({"key": "invalid_id", "label": "INVALID — sign unsafe",
                    "reason": c.invalid_reason})
    if not c.is_rnaseq:
        out.append({
            "key": "microarray",
            "label": f"microarray ({c.platform}) — not drawable until stage 6",
            "reason": ("microarray uses a second file layout "
                       "({ACC}_{PLATFORM}-analytics.tsv and "
                       "-normalized-expressions.tsv) and a different heatmap "
                       "recipe. The row is greyed and still counted: hiding it "
                       "would make this picker look like Atlas has only the "
                       "RNA-seq contrasts.")})
    if c.min_n < VERY_SMALL_N:
        out.append({"key": "n_lt_4",
                    "label": f"n<{VERY_SMALL_N} on a side (n={c.min_n})",
                    "reason": ("a group this small supports no useful dispersion "
                               "estimate; the statistic is Atlas's and is shown "
                               "as it is, but it is one of a handful of samples")})
    elif c.min_n < SMALL_N:
        out.append({"key": "n_lt_8",
                    "label": f"n<{SMALL_N} on a side (n={c.min_n})",
                    "reason": "one side has fewer than eight assays"})
    if c.unannotated_assays:
        out.append({"key": "unannotated",
                    "label": f"{c.unannotated_assays} assay(s) not in the SDRF",
                    "reason": ("the condensed SDRF annotates fewer assays than "
                               "the configuration names, so this contrast's "
                               "disease ids are incomplete")})
    return out


def picker_row(c: IndexedContrast, built_utc: str) -> dict[str, Any]:
    """One row of `/api/contrasts`, flags and all.

    `contrast_ref` is present only when I8 holds. A row that cannot produce a
    `ContrastRef` cannot produce a figure either, and carrying a half-built one to
    the browser would put the sign of every log2FC in a payload that no assertion
    has passed over.
    """
    flags = _flags(c)
    keys = {f["key"] for f in flags}
    row: dict[str, Any] = {
        "accession": c.accession,
        "contrast_id": c.contrast_id,
        "name": c.name,
        "test_label": c.test_label,
        "ref_label": c.ref_label,
        "n_test": c.n_test,
        "n_ref": c.n_ref,
        "min_n": c.min_n,
        "platform": c.platform,
        "is_rnaseq": c.is_rnaseq,
        "tissue": list(c.test_organism_parts),
        "ref_tissue": list(c.ref_organism_parts),
        "test_disease_ids": list(c.test_disease_ids),
        "flags": flags,
        "flag_keys": sorted(keys),
        # Drawable in stage 3 means: RNA-seq, and an id whose direction is
        # established. Microarray waits for stage 6; an invalid id waits for
        # nothing, because there is nothing to wait for.
        "drawable": c.is_rnaseq and not c.invalid_reason,
        "not_drawable_reason": ("the sign of this contrast is unestablished"
                                if c.invalid_reason else
                                "microarray is not drawable until stage 6"
                                if not c.is_rnaseq else ""),
        "configuration_url": atlas_mod.configuration_url(c.accession),
    }
    if not c.invalid_reason:
        ref = c.as_ref(index_built_utc=built_utc)
        row["contrast_ref"] = ref.as_dict()
        row["sign_sentence"] = ref.sign_sentence
    else:
        # Stated, and stated as the reason there is no sentence rather than as a
        # blank: "no sign sentence" and "the sign is unknown" are not the same.
        row["sign_sentence"] = ""
    return row


#: The single-column sorts the picker offers. One column at a time, never a
#: composite: a composite order over n and platform would be a ranking of
#: contrasts, which is rule 7 in a table.
PICKER_SORTS: dict[str, str] = {
    "": "accession, then contrast id",
    "n": "smallest side, ascending — then accession and contrast id",
    "platform": "platform — then accession and contrast id",
}


def sort_picker_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Sorted by ONE column, with the default order as the tie-break.

    A sort this picker cannot honour is refused rather than silently swapped for
    another while the panel names the first -- the same rule `order.py` holds for
    the spokes.
    """
    if sort not in PICKER_SORTS:
        raise ValueError(
            f"{sort!r} is not a contrast sort. Offered: "
            f"{[k or '(default)' for k in PICKER_SORTS]}. One column at a time; a "
            f"composite order over n and platform would be a ranking of "
            f"contrasts.")
    tie = lambda r: (r["accession"], r["contrast_id"])  # noqa: E731
    if sort == "n":
        return sorted(rows, key=lambda r: (r["min_n"], *tie(r)))
    if sort == "platform":
        return sorted(rows, key=lambda r: (r["platform"], *tie(r)))
    return sorted(rows, key=tie)


def picker_payload(index: "AtlasIndex", ref: DiseaseRef, sort: str = "") -> dict[str, Any]:
    """The whole picker: rows, flag counts, dominance, and no default pick.

    **There is no `selected` or `recommended` field and there will not be one.**
    The spike's verdict is that no sensible default exists — 101 of 138 Crohn's
    contrasts come from one ustekinumab trial — and a default pick in a payload is
    a recommendation whatever the UI calls it (rule 7, COUNCIL V10 by analogy).
    """
    rows = sort_picker_rows(
        [picker_row(c, index.built_utc) for c in index.contrasts_for(ref.atlas_ids)],
        sort)
    by_acc: dict[str, int] = {}
    for r in rows:
        by_acc[r["accession"]] = by_acc.get(r["accession"], 0) + 1
    dominant = max(by_acc.items(), key=lambda kv: (kv[1], kv[0])) if by_acc else None
    flag_counts = {k: sum(1 for r in rows if k in r["flag_keys"])
                   for k in ("microarray", "n_lt_8", "n_lt_4", "invalid_id",
                             "unannotated")}
    # `n<8` is the count of rows flagged AT that level, and `n_lt_4` rows are
    # flagged more loudly instead -- so the number a reader wants for "a side
    # under 8" is the sum. Both are given, and named, because a reader comparing
    # to the plan's 75 must not have to add them up and guess.
    flag_counts["n_under_8_total"] = flag_counts["n_lt_8"] + flag_counts["n_lt_4"]
    summary = index.summary_for(ref.atlas_ids)
    return {
        "disease": {
            "label": ref.label, "current_id": ref.current_id,
            "atlas_ids": list(ref.atlas_ids),
            "obsolete_atlas_ids": list(ref.obsolete_atlas_ids),
            "mapping_notes": list(ref.mapping_notes),
        },
        "counts": summary.as_dict(),
        "flag_counts": flag_counts,
        "n_drawable_now": sum(1 for r in rows if r["drawable"]),
        "dominance": ({"accession": dominant[0], "n": dominant[1],
                       "of": len(rows),
                       "text": (f"{dominant[1]} of {len(rows)} contrasts here are "
                                f"from one experiment, {dominant[0]}")}
                      if dominant else None),
        "order": sort or "",
        "order_text": PICKER_SORTS[sort or ""],
        "sorts": [{"value": k, "label": v} for k, v in PICKER_SORTS.items()],
        "index": index.provenance(),
        "rows": rows,
        # Said in the payload rather than only in the panel's HTML, so the
        # statement travels with the data a test reads.
        "no_default": ("No contrast is picked for you. 101 of the 138 Crohn's "
                       "contrasts come from one trial, and the spike's verdict is "
                       "that no sensible default exists."),
        "row_unit": ("Each row is one CONTRAST, not one experiment: an experiment "
                     "with 101 contrasts appears 101 times, because the sign, the "
                     "groups and the n per side differ per contrast."),
    }


class IndexMissing(HardFailure):
    """No index on disk, or one written by a different schema.

    A `HardFailure` so a route that needs counts refuses rather than serving
    zeros: "0 contrasts" from a missing index and "0 contrasts" from a disease
    Atlas has never studied are the same string and completely different facts.
    """


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def _index_one(acc: str, xml: str, sdrf: str) -> list[IndexedContrast]:
    """One experiment's contrasts, from its two files. Pure — no network."""
    annotations = atlas_mod.parse_condensed_sdrf(sdrf, acc)
    out: list[IndexedContrast] = []
    for pc in atlas_mod.parse_configuration(xml, acc):
        test = atlas_mod.group_uris(annotations, pc.test_assays, DISEASE_FACTOR)
        ref = atlas_mod.group_uris(annotations, pc.ref_assays, DISEASE_FACTOR)
        out.append(IndexedContrast(
            accession=pc.accession, contrast_id=pc.contrast_id,
            ref_group=pc.ref_group, test_group=pc.test_group,
            ref_label=pc.ref_label, test_label=pc.test_label,
            n_ref=pc.n_ref, n_test=pc.n_test, name=pc.name, platform=pc.platform,
            test_disease_ids=tuple(test["factor_ids"]),
            ref_disease_ids=tuple(ref["factor_ids"]),
            test_characteristic_disease_ids=tuple(test["characteristic_ids"]),
            ref_characteristic_disease_ids=tuple(ref["characteristic_ids"]),
            test_organism_parts=tuple(atlas_mod.group_values(
                annotations, pc.test_assays, ORGANISM_PART_FACTOR)),
            ref_organism_parts=tuple(atlas_mod.group_values(
                annotations, pc.ref_assays, ORGANISM_PART_FACTOR)),
            unannotated_assays=(len(test["unannotated_assays"])
                                + len(ref["unannotated_assays"])),
            invalid_reason=pc.invalid_reason))
    return out


async def build_index(http: Http, cache: Cache, *,
                      accessions: Sequence[str] | None = None,
                      progress: Callable[[str], None] | None = None) -> AtlasIndex:
    """Crawl the population and return the index. ~740 GETs; a 429 aborts.

    `accessions` is for the offline fixture build and for a rerun over one
    experiment; left out, the population is selected from the live catalogue.

    A 429 is allowed to propagate as `RateLimited`, deliberately: nothing is
    written, so there is no half-crawled index for a later read to under-count
    from. A per-experiment parse failure is a different thing — it is recorded in
    `failed` with its reason and the crawl continues, because one malformed
    configuration file should not cost the other 368.
    """
    adapter = AtlasAdapter(http, cache)
    say = progress or (lambda _m: None)

    if accessions is None:
        catalogue = await adapter.catalogue()
        accessions = atlas_mod.select_index_population(catalogue)
        say(f"catalogue: {len(catalogue)} experiments, "
            f"{len(accessions)} human differential with a disease factor")

    index = AtlasIndex(built_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       n_experiments=0, population=list(accessions))

    async def one(acc: str) -> tuple[str, list[IndexedContrast] | str]:
        try:
            xml, sdrf = await asyncio.gather(adapter.configuration(acc),
                                             adapter.condensed_sdrf(acc))
        except RateLimited:
            raise
        except HardFailure as e:
            return acc, f"{type(e).__name__}: {e}"
        try:
            return acc, _index_one(acc, xml, sdrf)
        except HardFailure as e:
            return acc, f"parse: {e}"

    # `Http` gates to 4 per host, so this gather is bounded by the semaphore
    # rather than by a chunk size here. One place enforces rule 9.
    results = await asyncio.gather(*(one(a) for a in accessions))

    for acc, res in results:
        if isinstance(res, str):
            index.failed.append({"accession": acc, "reason": res})
            say(f"  FAILED {acc}: {res[:160]}")
            continue
        index.contrasts.extend(res)
        index.n_experiments += 1
    index.contrasts.sort(key=lambda c: (c.accession, c.contrast_id))
    say(f"indexed {index.n_experiments} experiments, {index.n_contrasts} contrasts, "
        f"{len(index.failed)} failed")
    return index


__all__ = ["AtlasIndex", "DISEASE_FACTOR", "DiseaseContrastSummary",
           "INDEX_SCHEMA_VERSION", "IndexMissing", "IndexedContrast",
           "ORGANISM_PART_FACTOR", "PICKER_SORTS", "SMALL_N", "VERY_SMALL_N",
           "build_index", "index_path", "picker_payload", "picker_row",
           "sort_picker_rows"]
