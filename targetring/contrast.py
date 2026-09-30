"""`ContrastRef` — one Expression Atlas contrast, carried as a unit.

Stage 1 puts only this dataclass here. Stage 3 adds `load_analytics`,
`top_n_by_padj` and `Accounting` beside it.

**Stage 3's own governing trap (spike §Awkward 3).** `-analytics.tsv` carries a
p-value for only 25,232 of 65,217 genes in the reference contrast (DESeq2's
independent filtering leaves the rest `NA`); log2FC is present for all of them.
Ranking by log2FC to dodge the gap would surface exactly the genes AGENTS.md
rule 1 says must never be read as low for having no measurement. So selection
here is **always** restricted to rows carrying both numbers, the excluded count
is a first-class field of `Accounting`, and nothing in this module offers a sort
by log2FC alone.

**Why this is a frozen dataclass and not a pair of strings.** The spike's first
finding was that the contrast id `g2_g1` means *reference* `g2`, *test* `g1`, so
reading the id left to right inverts every log2 fold change — and nothing on the
wire says which token is which. A figure drawn from an inverted sign is right in
every number and wrong about the direction of the disease, which is
`AGENTS.md:17`'s worst output arriving with no data error anywhere. So:

* the two group ids and the two group *labels* travel together with the id, and
* the constructor asserts `id == f"{ref}_{test}"` every single time (I8), and
* the prose `<name>` from `-configuration.xml` is carried verbatim, because a
  name built from the tokens is a name that can be built the wrong way round.

`sign_sentence` is the one sentence every artefact that uses this contrast must
print. It is a property rather than a string the caller writes, so the figure,
the picker and the manifest cannot word it differently — or, worse, the same way
with the labels swapped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from .http import HardFailure

if TYPE_CHECKING:
    # Only for type hints: `adapters/atlas.py` imports `RNASEQ` and
    # `ContrastRef` from this module at load time, so importing it back here
    # at module level would be a cycle. Every runtime use below imports it
    # locally instead.
    from .adapters import atlas as atlas_mod

#: The shape every `Gene ID` in `-analytics.tsv` must have (spike: 65,217/65,217
#: are `ENSG`). A row that is not this shape is not a gene id this build can
#: resolve, and reading it as one anyway is how a join goes silently wrong.
GENE_ID_RE = re.compile(r"^ENSG\d{11}$")

_BAD_NUMERIC = {"", "na", "n/a", "nan", "null", "none"}

#: Platform value for a contrast with no `<array_design>` in its `<analytics>`
#: block. Not the string "RNA-seq" from the spike: the rest of the codebase says
#: `rnaseq`, and the picker greys on `platform != RNASEQ`.
RNASEQ = "rnaseq"


class ContrastIdentityError(AssertionError):
    """I8 failed: the id is not `{reference}_{test}`.

    An `AssertionError` subclass so `app.py`'s existing handler returns the
    invariant-abort shape, and a named class so the picker can tell this apart
    from any other assertion and render the row as `INVALID — sign unsafe`
    instead of dropping it. A dropped row is a contrast a reader never learns
    about; a greyed one with a reason is a fact.
    """


@dataclass(frozen=True)
class ContrastRef:
    """Everything needed to fetch, draw and caption one contrast.

    Constructed only by `adapters/atlas.py`. Written to the manifest verbatim by
    `asdict()`, so a run's artefacts carry the whole reference and not a
    reconstruction of it.

    `ensembl_release` and `pipeline` come from `-analysis-methods.tsv`, which the
    index build deliberately does NOT fetch: the index is ~740 GETs at two per
    experiment, and a third file per experiment would be a third of that budget
    again for two strings that only matter once a contrast is actually drawn.
    They are empty on an index-built ref and filled by stage 3.
    """

    accession: str
    contrast_id: str
    ref_group: str
    test_group: str
    ref_label: str
    test_label: str
    n_ref: int
    n_test: int
    #: The `<contrast><name>` prose from the XML, byte-for-byte. I8 reads this
    #: back off the rendered figure, so it may not be normalised here.
    name: str
    #: `rnaseq`, or the array design accession (e.g. `A-AFFY-44`) for microarray.
    platform: str = RNASEQ
    ensembl_release: str = ""
    pipeline: str = ""
    analytics_url: str = ""
    configuration_url: str = ""
    #: When the index that produced this ref was built. Printed wherever its
    #: counts are shown, because a count from a month-old crawl is a month-old
    #: count and a reader is owed the date rather than a fresh-looking number.
    index_built_utc: str = ""
    #: The actual assay/run ids (`SRR.../ERR...`) on each side, filled by
    #: `hydrate()` from `-configuration.xml`. Empty on an index-built ref --
    #: `IndexedContrast` stores counts and disease ids, never the assay list --
    #: and stage 4 needs the real ids to pick `-raw-counts.tsv` columns by name,
    #: never by position or by count (rule 2).
    ref_assays: tuple[str, ...] = ()
    test_assays: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected = f"{self.ref_group}_{self.test_group}"
        if self.contrast_id != expected:
            raise ContrastIdentityError(
                f"I8: {self.accession} contrast id {self.contrast_id!r} is not "
                f"{expected!r} = f'{{reference}}_{{test}}'. The id is the only "
                f"thing on the wire that says which group is the reference, and "
                f"reading it the other way round inverts every log2 fold change. "
                f"No figure is drawn from a contrast whose sign cannot be "
                f"established.")
        if self.n_ref < 1 or self.n_test < 1:
            raise ContrastIdentityError(
                f"I8: {self.accession} {self.contrast_id} has n_ref={self.n_ref}, "
                f"n_test={self.n_test}; a side with no assays is not a contrast.")

    @property
    def is_rnaseq(self) -> bool:
        return self.platform == RNASEQ

    @property
    def sign_sentence(self) -> str:
        """The one sentence every artefact using this contrast prints.

        A property and not a caller-supplied string: the figure, the picker and
        the manifest must not be able to word this differently, and above all
        must not be able to word it identically with the labels swapped.
        """
        return (f"positive log2FC = up in {self.test_label} (n={self.n_test}) "
                f"vs {self.ref_label} (n={self.n_ref})")

    @property
    def min_n(self) -> int:
        return min(self.n_ref, self.n_test)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "sign_sentence": self.sign_sentence,
                "is_rnaseq": self.is_rnaseq}


class MicroarrayNotDrawable(Exception):
    """Stage 3 is RNA-seq only. 101 of 123 Crohn's contrasts are microarray
    (spike §Awkward 4, two file layouts) and stage 6 is where the second layout
    is handled. Never a partial table: refuse the whole request rather than
    reading the wrong file and calling it empty."""

    def __init__(self, ref: "ContrastRef") -> None:
        self.ref = ref
        super().__init__(
            f"{ref.accession} {ref.contrast_id} is microarray (platform "
            f"{ref.platform!r}); the gene table and heatmap are RNA-seq only "
            f"until stage 6 reads {ref.accession}_{ref.platform}-analytics.tsv.")


async def hydrate(atlas: "atlas_mod.AtlasAdapter", ref: ContrastRef) -> ContrastRef:
    """Fill `ensembl_release`, `pipeline` and the two sides' assay ids.

    An index-built ref carries all three empty on purpose: the index build
    does not fetch `-analysis-methods.tsv` for all ~369 experiments, and
    `IndexedContrast` stores counts and disease ids, never the assay list
    (module docstring). This is the one place they get filled, and it happens
    once a contrast is actually drawn -- never earlier, never guessed.
    """
    from .adapters import atlas as atlas_mod

    if not ref.is_rnaseq:
        raise MicroarrayNotDrawable(ref)
    methods_text = await atlas.analysis_methods(ref.accession)
    methods = atlas_mod.parse_analysis_methods(methods_text, ref.accession)

    xml = await atlas.configuration(ref.accession)
    parsed = atlas_mod.parse_configuration(xml, ref.accession)
    match = next((c for c in parsed if c.contrast_id == ref.contrast_id), None)
    if match is None:
        raise HardFailure(
            f"{ref.accession}-configuration.xml no longer names contrast "
            f"{ref.contrast_id!r}; the index this ref came from may be stale")

    return replace(
        ref,
        ensembl_release=atlas_mod.ensembl_release_from(methods, ref.accession),
        pipeline=atlas_mod.pipeline_from(methods, ref.accession),
        ref_assays=match.ref_assays, test_assays=match.test_assays,
    )


def padj_column_label(ref: ContrastRef) -> str:
    """What `-analysis-methods.tsv` says the p-value column is -- read, not
    assumed. Nothing here hardcodes 'DESeq2': stage 6's microarray pipeline is
    limma, read from the same row, and this label follows it."""
    pipeline = ref.pipeline or "pipeline not read"
    return f"adjusted p-value ({pipeline}, from -analysis-methods.tsv)"


def _num(raw: str) -> float | None:
    v = raw.strip()
    if v.lower() in _BAD_NUMERIC:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f


@dataclass(frozen=True)
class AnalyticsRow:
    """One gene's numbers for one contrast, out of `-analytics.tsv`."""

    ensembl_id: str
    atlas_symbol: str
    pvalue: float | None
    log2fc: float | None

    @property
    def has_statistic(self) -> bool:
        return self.pvalue is not None and self.log2fc is not None


def parse_analytics(text: str, ref: ContrastRef) -> list[AnalyticsRow]:
    """Read exactly this contrast's two columns out of the whole file.

    An experiment can carry several contrasts in one `-analytics.tsv`
    (E-GEOD-101794: `g2_g1` and `g4_g3` side by side, 6 columns). Selecting the
    two columns by this contrast's own name is what stops another contrast's
    numbers from being read as this one's -- there is no positional reading
    anywhere in this function.
    """
    lines = text.splitlines()
    if not lines:
        raise HardFailure(f"{ref.accession}-analytics.tsv is empty")
    header = lines[0].split("\t")
    p_col = f"{ref.contrast_id}.p-value"
    fc_col = f"{ref.contrast_id}.log2foldchange"
    if p_col not in header or fc_col not in header:
        raise HardFailure(
            f"{ref.accession}-analytics.tsv has no columns for contrast "
            f"{ref.contrast_id!r} (looked for {p_col!r} and {fc_col!r} among "
            f"{header!r}). Refusing to read a neighbouring contrast's numbers "
            f"as this one's.")
    p_i, fc_i = header.index(p_col), header.index(fc_col)
    name_i = 1

    seen: set[str] = set()
    rows: list[AnalyticsRow] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split("\t")
        gid = parts[0].strip()
        if not GENE_ID_RE.match(gid):
            raise HardFailure(
                f"{ref.accession}-analytics.tsv:{lineno} Gene ID {gid!r} is not "
                f"ENSG-shaped; refusing to treat it as a gene id")
        if gid in seen:
            raise HardFailure(
                f"{ref.accession}-analytics.tsv:{lineno} duplicate Gene ID "
                f"{gid!r}; every id must be unique or a top-N selection could "
                f"silently count one gene twice")
        seen.add(gid)
        name = parts[name_i].strip() if len(parts) > name_i else ""
        pval = _num(parts[p_i]) if len(parts) > p_i else None
        fc = _num(parts[fc_i]) if len(parts) > fc_i else None
        rows.append(AnalyticsRow(gid, name, pval, fc))
    if not rows:
        raise HardFailure(
            f"{ref.accession}-analytics.tsv had a header but no data rows")
    return rows


async def load_analytics(atlas: "atlas_mod.AtlasAdapter",
                         ref: ContrastRef) -> list[AnalyticsRow]:
    if not ref.is_rnaseq:
        raise MicroarrayNotDrawable(ref)
    text = await atlas.analytics(ref.accession, ref.platform)
    return parse_analytics(text, ref)


SelectionDirection = Literal["both", "split"]


@dataclass(frozen=True)
class Accounting:
    """I9's three-stage accounting, as counts rather than a per-gene ledger
    (65,217 genes cannot go in one -- PLAN §1 item 5). Each stage is a subset
    of the one before it, asserted here so a broken selection cannot reach the
    figure quietly:

    A. `n_total == n_with_statistic + n_without_statistic`
    B. `n_top <= n_with_statistic`
    C. `n_selected <= n_top`
    """

    n_total: int
    n_with_statistic: int
    n_without_statistic: int
    n_top: int = 0
    n_up: int = 0
    n_down: int = 0
    n_selected: int = 0

    def __post_init__(self) -> None:
        if self.n_total != self.n_with_statistic + self.n_without_statistic:
            raise AssertionError(
                f"I9 stage A: {self.n_total} total != {self.n_with_statistic} "
                f"with a statistic + {self.n_without_statistic} without")
        if self.n_top > self.n_with_statistic:
            raise AssertionError(
                f"I9 stage B: top {self.n_top} exceeds the "
                f"{self.n_with_statistic} genes that have a statistic to rank")
        if self.n_selected > self.n_top:
            raise AssertionError(
                f"I9 stage C: {self.n_selected} selected exceeds the "
                f"{self.n_top} shown; a selection must be a subset of the top N")

    def with_selection(self, n_selected: int) -> "Accounting":
        return replace(self, n_selected=n_selected)

    def accounting_line(self) -> str:
        """The seventh mandatory heatmap string (stage 4 / I10), verbatim.

        Printed with the literal word 'yet' regardless of `n_selected`: the
        heatmap is drawn once, before any client-side checkbox is touched, so
        at render time nothing has been selected. Stage 5's manifest reports
        the eventual selection under its own field, not by re-wording this
        line.
        """
        return (f"{self.n_total:,} genes in contrast · "
                f"{self.n_with_statistic:,} with a statistic · "
                f"top {self.n_top} shown · {self.n_selected} selected yet")

    def selection_rule_line(self) -> str:
        """§10 terse mode: 'top N of D by adjusted p', denominator included."""
        return f"top {self.n_top} of {self.n_with_statistic:,} by adjusted p"


def top_n_by_padj(rows: list[AnalyticsRow], n: int,
                  direction: SelectionDirection = "both",
                  ) -> tuple[list[AnalyticsRow], Accounting]:
    """Rule 1 as a sort, rule 7's one permitted rank-based selection.

    Selection runs **only** over rows carrying both a p-value and a log2FC
    (module docstring) -- never a fallback to log2FC when p is missing, which
    is exactly the dodge that would surface genes DESeq2's independent
    filtering already excluded.

    `direction="split"` takes N/2 up and N/2 down, each by its own p, so a
    one-directional top-N (spike: 59 up, 1 down at N=60) cannot masquerade as a
    two-sided list; `"both"` is the plain top-N by p regardless of sign, with
    the resulting up/down split still counted and returned either way.
    """
    n_total = len(rows)
    with_stat = [r for r in rows if r.has_statistic]
    n_without = n_total - len(with_stat)

    if direction == "both":
        top = sorted(with_stat, key=lambda r: r.pvalue)[:n]
    elif direction == "split":
        half = n // 2
        up = sorted((r for r in with_stat if r.log2fc > 0), key=lambda r: r.pvalue)
        down = sorted((r for r in with_stat if r.log2fc < 0), key=lambda r: r.pvalue)
        top = up[:half] + down[:n - half]
        top.sort(key=lambda r: r.pvalue)
    else:
        raise ValueError(f"direction must be 'both' or 'split', got {direction!r}")

    n_up = sum(1 for r in top if r.log2fc > 0)
    n_down = sum(1 for r in top if r.log2fc < 0)
    accounting = Accounting(
        n_total=n_total, n_with_statistic=len(with_stat),
        n_without_statistic=n_without, n_top=len(top), n_up=n_up, n_down=n_down)
    return top, accounting


@dataclass(frozen=True)
class GeneTableRow:
    """One row of the stage 3 gene table: the contrast's own numbers, plus
    resolution through the *same* `Resolver` a pasted id would go through
    (AGENTS.md rule 5)."""

    ensembl_id: str
    atlas_symbol: str
    pvalue: float
    log2fc: float
    resolution_status: str
    resolved_symbol: str | None
    resolution_note: str

    @property
    def display_symbol(self) -> str:
        """Never `nan`: an empty Atlas symbol (spike: `ENSG00000279487`) shows
        as its id instead of the literal string pandas/Atlas would print."""
        return self.resolved_symbol or self.atlas_symbol or self.ensembl_id


@dataclass(frozen=True)
class GeneTable:
    ref: ContrastRef
    rows: list[GeneTableRow]
    accounting: Accounting
    n_excluded_no_resolution: int
    padj_label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "contrast": self.ref.as_dict(),
            "padj_label": self.padj_label,
            "accounting": asdict(self.accounting),
            "accounting_line": self.accounting.accounting_line(),
            "selection_rule_line": self.accounting.selection_rule_line(),
            "sign_sentence": self.ref.sign_sentence,
            "n_excluded_no_resolution": self.n_excluded_no_resolution,
            "rows": [
                {
                    "ensembl_id": r.ensembl_id,
                    "atlas_symbol": r.atlas_symbol,
                    "display_symbol": r.display_symbol,
                    "pvalue": r.pvalue,
                    "log2fc": r.log2fc,
                    "resolution_status": r.resolution_status,
                    "resolved_symbol": r.resolved_symbol,
                    "resolution_note": r.resolution_note,
                }
                for r in self.rows
            ],
        }


async def build_gene_table(atlas: "atlas_mod.AtlasAdapter", resolver: Any,
                           ref: ContrastRef, n: int = 60,
                           direction: SelectionDirection = "both") -> GeneTable:
    """The whole of stage 3: fetch, select, resolve. `resolver` is a
    `resolve.Resolver` -- typed `Any` to avoid a cycle, since `resolve.py`
    never needs to import this module."""
    hydrated = await hydrate(atlas, ref)
    rows = await load_analytics(atlas, hydrated)
    top, accounting = top_n_by_padj(rows, n, direction)

    # Rule 5: every id goes through the resolver exactly as a pasted id would.
    resolutions = await resolver.resolve_all([r.ensembl_id for r in top])
    n_no_resolution = 0
    out_rows: list[GeneTableRow] = []
    for r in top:
        res = resolutions[r.ensembl_id]
        if not res.usable:
            n_no_resolution += 1
        out_rows.append(GeneTableRow(
            ensembl_id=r.ensembl_id, atlas_symbol=r.atlas_symbol,
            pvalue=r.pvalue, log2fc=r.log2fc, resolution_status=res.status,
            resolved_symbol=res.approved_symbol, resolution_note=res.note))

    return GeneTable(
        ref=hydrated, rows=out_rows, accounting=accounting,
        n_excluded_no_resolution=n_no_resolution,
        padj_label=padj_column_label(hydrated))


__all__ = [
    "Accounting", "AnalyticsRow", "ContrastIdentityError", "ContrastRef",
    "GeneTable", "GeneTableRow", "MicroarrayNotDrawable", "RNASEQ",
    "SelectionDirection", "build_gene_table", "hydrate", "load_analytics",
    "padj_column_label", "parse_analytics", "top_n_by_padj",
]
