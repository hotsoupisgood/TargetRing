"""Stage 4 — the heatmap, and the single biggest named risk in the plan.

**The risk, stated once so the rest of this file can be read against it**
(spike §3, plan §8): a z-scored heatmap on a null-ish contrast is exactly as
visually convincing as one on a real contrast, with every number correct and
no data error anywhere. Nothing in a caption fixes that. What fixes it is two
panels that make the null case *look* different rather than merely say it is:

* the raw-scale panel (median log2(CPM+1) per group, plus each gene's own
  |log2FC|) puts effect *size* beside the z-pattern, so a null contrast's
  smaller effect size is visible even though its z-pattern looks the same;
* the per-sample influence strip makes a signal driven by a handful of
  outlier samples legible instead of narrated -- the spike's null figure was
  "driven by ~3 outlier samples" in prose; here it is a number per sample.

If either panel cannot be drawn, `render()` refuses rather than drawing panels
1-3 and 6-7 alone and calling it a heatmap (plan §3 stage 4, §8).

RNA-seq only. Recipe: library-size CPM on the two groups' own samples, then
log2(x+1), then a per-gene z-score across exactly the samples shown -- which is
also why "block structure is guaranteed by construction" is printed on every
render rather than only on the ones where it would be flattering to omit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median, mean
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
# matplotlib's own default is 'path': every string becomes vector paths with
# no text in the SVG at all, which is invisible to `invariants.svg_text()` and
# every reader like it (`figure.py`'s own comment on this same setting, needed
# again here because this module does not import `figure` or `pycirclize`).
matplotlib.rcParams["svg.fonttype"] = "none"

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402

from .contrast import Accounting, AnalyticsRow, ContrastRef  # noqa: E402
from .http import HardFailure  # noqa: E402
from .palette import cmap_from, diverging_hexes, sequential_hexes  # noqa: E402

if False:  # TYPE_CHECKING-only, avoids importing the adapter at module load
    from .adapters.atlas import AtlasAdapter  # noqa: F401


# ---------------------------------------------------------------------------
# -raw-counts.tsv
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawCounts:
    """Library size is computed from every row of the file, not only the
    selected genes -- CPM is a property of the whole library, and computing it
    from a pre-filtered subset would be a different, wrong, number."""

    library_size: dict[str, float]
    counts: dict[str, dict[str, float]]
    n_deduped_columns: int


def parse_raw_counts(text: str, acc: str, wanted_genes: set[str],
                     wanted_assays: Sequence[str]) -> RawCounts:
    lines = text.splitlines()
    if not lines:
        raise HardFailure(f"{acc}-raw-counts.tsv is empty")
    header = lines[0].split("\t")

    # Spike correction: duplicated sample columns are a sporadic per-experiment
    # property (real in E-GEOD-150316 and E-MTAB-9711, absent here) -- asserted
    # every time, never predicted. First occurrence wins; the count is carried
    # rather than silently dropped.
    seen_cols: dict[str, int] = {}
    n_deduped = 0
    for i, name in enumerate(header):
        if name in seen_cols:
            n_deduped += 1
            continue
        seen_cols[name] = i

    missing_assays = [a for a in wanted_assays if a not in seen_cols]
    if missing_assays:
        raise HardFailure(
            f"{acc}-raw-counts.tsv is missing assay column(s) the configuration "
            f"names for this contrast: {missing_assays[:10]}. An assay present "
            f"in the XML but absent from counts is refused, never drawn as if "
            f"it were not part of the contrast.")
    wanted_idx = {a: seen_cols[a] for a in wanted_assays}

    library_size: dict[str, float] = {a: 0.0 for a in wanted_assays}
    counts: dict[str, dict[str, float]] = {}
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split("\t")
        gid = parts[0].strip()
        row: dict[str, float] = {}
        for a, idx in wanted_idx.items():
            # Rule 1, at the one place in this function that used to break it.
            # This read `except ValueError: x = 0.0`, which gave an unparseable
            # cell, an empty cell and a SHORT ROW a real-looking count of zero.
            # Two rule-1 violations in one line: the gene got a value it does
            # not have (after CPM -> log2 -> z a strongly negative z, drawn deep
            # blue -- "missing read as low", exactly what rule 1 forbids), and
            # the zero was still added to `library_size`, understating the
            # library so that every OTHER gene's CPM in that column came out
            # inflated. One bad cell therefore rescaled a whole sample column.
            #
            # There is no fourth state available here: this layer feeds a
            # matplotlib `imshow`, which has no hatched `no_data` cell, and
            # omitting the cell would leave the library size wrong anyway. So
            # this refuses, which is what the rest of this function already does
            # for a missing assay column and a missing gene row.
            if idx >= len(parts):
                raise HardFailure(
                    f"{acc}-raw-counts.tsv:{lineno} has {len(parts)} field(s) "
                    f"but column {idx} is assay {a!r}; the row is truncated. A "
                    f"short row is refused, never read as a zero count — that "
                    f"would give this gene a value it does not have and "
                    f"understate {a}'s library size, inflating every other "
                    f"gene's CPM in that column.")
            raw = parts[idx].strip()
            try:
                x = float(raw)
            except ValueError:
                raise HardFailure(
                    f"{acc}-raw-counts.tsv:{lineno} assay {a!r} for gene "
                    f"{gid!r} is {raw!r}, which is not a number. Refused rather "
                    f"than imputed to 0.0 (rule 1: missing is not low) — a zero "
                    f"here would be drawn as a real low-expression cell and "
                    f"would also understate {a}'s library size, inflating every "
                    f"other gene's CPM in that column. The likeliest cause is a "
                    f"truncated download or a partially-written cache entry.") from None
            if math.isnan(x) or math.isinf(x):
                raise HardFailure(
                    f"{acc}-raw-counts.tsv:{lineno} assay {a!r} for gene "
                    f"{gid!r} parsed as {raw!r} -> {x}; a non-finite count is "
                    f"refused for the same reason `states.Cell` refuses one — "
                    f"it cannot be z-scored and would poison the whole row.")
            row[a] = x
            library_size[a] += x
        if gid in wanted_genes:
            counts[gid] = row

    missing_genes = wanted_genes - set(counts)
    if missing_genes:
        raise HardFailure(
            f"{acc}-raw-counts.tsv has no row for {len(missing_genes)} gene(s) "
            f"selected from -analytics.tsv: {sorted(missing_genes)[:10]}")
    return RawCounts(library_size=library_size, counts=counts,
                     n_deduped_columns=n_deduped)


async def load_raw_counts(atlas: "AtlasAdapter", ref: ContrastRef,
                          wanted_genes: set[str]) -> RawCounts:
    if not ref.is_rnaseq:
        from .contrast import MicroarrayNotDrawable
        raise MicroarrayNotDrawable(ref)
    if not ref.ref_assays or not ref.test_assays:
        raise HardFailure(
            f"{ref.accession} {ref.contrast_id} carries no assay ids; call "
            f"contrast.hydrate() before building a heatmap")
    text = await atlas.raw_counts(ref.accession)
    all_assays = tuple(ref.ref_assays) + tuple(ref.test_assays)
    return parse_raw_counts(text, ref.accession, wanted_genes, all_assays)


# ---------------------------------------------------------------------------
# CPM -> log2 -> z, and the influence strip
# ---------------------------------------------------------------------------


def _cpm_log2(counts_row: dict[str, float], library_size: dict[str, float],
             assays: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in assays:
        lib = library_size.get(a, 0.0)
        cpm = (counts_row.get(a, 0.0) / lib * 1e6) if lib > 0 else 0.0
        out[a] = math.log2(cpm + 1.0)
    return out


def _zscores(values: dict[str, float], samples: Sequence[str]) -> dict[str, float]:
    xs = [values[s] for s in samples]
    m = mean(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
    if sd == 0:
        return {s: 0.0 for s in samples}
    return {s: (values[s] - m) / sd for s in samples}


def _group_mean_z_diff(z_by_gene: dict[str, dict[str, float]],
                       ref_samples: Sequence[str],
                       test_samples: Sequence[str]) -> float:
    diffs = []
    for z in z_by_gene.values():
        rv = [z[s] for s in ref_samples if s in z]
        tv = [z[s] for s in test_samples if s in z]
        if rv and tv:
            diffs.append(mean(tv) - mean(rv))
    return mean(diffs) if diffs else 0.0


@dataclass(frozen=True)
class HeatmapGeneRow:
    ensembl_id: str
    display_symbol: str
    pvalue: float
    log2fc: float
    z: dict[str, float]
    log2cpm1: dict[str, float]
    ref_median_log2cpm1: float
    test_median_log2cpm1: float


@dataclass(frozen=True)
class HeatmapData:
    ref: ContrastRef
    rows: list[HeatmapGeneRow]
    ref_samples: tuple[str, ...]
    test_samples: tuple[str, ...]
    accounting: Accounting
    n_deduped_columns: int
    influence: dict[str, float]
    influence_outliers: frozenset[str]
    influence_threshold: float

    @property
    def all_samples(self) -> tuple[str, ...]:
        return self.ref_samples + self.test_samples


def compute(ref: ContrastRef, top_rows: list[AnalyticsRow], raw: RawCounts,
           accounting: Accounting) -> HeatmapData:
    ref_samples, test_samples = ref.ref_assays, ref.test_assays
    all_samples = ref_samples + test_samples

    log2cpm_by_gene: dict[str, dict[str, float]] = {}
    for r in top_rows:
        log2cpm_by_gene[r.ensembl_id] = _cpm_log2(
            raw.counts[r.ensembl_id], raw.library_size, all_samples)

    gene_z: dict[str, dict[str, float]] = {}
    rows: list[HeatmapGeneRow] = []
    for r in top_rows:
        vals = log2cpm_by_gene[r.ensembl_id]
        z = _zscores(vals, all_samples)
        gene_z[r.ensembl_id] = z
        rows.append(HeatmapGeneRow(
            ensembl_id=r.ensembl_id,
            display_symbol=r.atlas_symbol or r.ensembl_id,
            pvalue=r.pvalue, log2fc=r.log2fc, z=z, log2cpm1=vals,
            ref_median_log2cpm1=median(vals[s] for s in ref_samples),
            test_median_log2cpm1=median(vals[s] for s in test_samples)))

    # Rows are drawn most-significant first: the selection rule is "top by p",
    # so the figure's own row order follows the rule it names (I3's argument,
    # applied here rather than re-derived from a separate ordering module).
    rows.sort(key=lambda r: r.pvalue)

    baseline = _group_mean_z_diff(gene_z, ref_samples, test_samples)
    influence: dict[str, float] = {}
    for s in all_samples:
        remaining = tuple(x for x in all_samples if x != s)
        rem_ref = tuple(x for x in ref_samples if x != s)
        rem_test = tuple(x for x in test_samples if x != s)
        z_wo = {gid: _zscores(vals, remaining)
                for gid, vals in log2cpm_by_gene.items()}
        influence[s] = _group_mean_z_diff(z_wo, rem_ref, rem_test) - baseline

    med_abs = median(abs(d) for d in influence.values()) or 1e-9
    threshold = 3.0 * med_abs
    outliers = frozenset(s for s, d in influence.items() if abs(d) > threshold)

    return HeatmapData(
        ref=ref, rows=rows, ref_samples=ref_samples, test_samples=test_samples,
        accounting=accounting, n_deduped_columns=raw.n_deduped_columns,
        influence=influence, influence_outliers=outliers,
        influence_threshold=threshold)


# ---------------------------------------------------------------------------
# The seven mandatory strings (I10). Built here, checked in invariants.py
# against the same builder functions -- never a second hand-copied literal
# that could drift from what actually gets drawn (the mistake I7 exists to
# catch for the circos's ordering sentence).
# ---------------------------------------------------------------------------


def title_line(ref: ContrastRef) -> str:
    return f"{ref.name} · {ref.accession} · {ref.contrast_id}"


def group_label(label: str, n: int) -> str:
    return f"{label} (n={n})"


def zscore_sentence(n_samples: int, n_top: int) -> str:
    return (f"Rows are z-scored across the {n_samples} samples shown, after "
            f"selecting the top {n_top} by this contrast's adjusted p. Block "
            f"structure is guaranteed by construction and is not evidence.")


def raw_scale_sentence() -> str:
    return ("Raw scale, same rows: median log2(CPM+1) per group and each "
            "gene's own |log2FC| from the contrast, so effect size is visible "
            "beside the z-pattern above.")


def influence_sentence(threshold: float, n_outliers: int, n_samples: int) -> str:
    return (f"Per-sample influence: leave-one-out change in the between-group "
            f"mean-z difference. {n_outliers} of {n_samples} sample(s) flagged "
            f"above |Δ| > {threshold:.3f} (3× the median absolute shift).")


def colour_direction_sentence() -> str:
    return "Colour: red/ember = above this gene's row mean (z); blue = below it."


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


@dataclass
class HeatmapResult:
    svg_path: Path
    mandatory_strings: list[str]
    n_genes: int
    n_samples: int


class HeatmapRefused(Exception):
    """Plan §8: if the risk cannot be retired cleanly, refuse the whole
    heatmap rather than ship panels 1-3 and 6-7 alone and call it done."""


def render(data: HeatmapData, out_svg: Path, out_png: Path | None = None) -> HeatmapResult:
    """One SVG, every mandatory string drawn onto it directly -- no second file
    and no post-hoc XML surgery, so `svg_text()` (the same reader every other
    invariant in this repo uses) finds all seven strings on the one artefact a
    reader actually has open."""
    if not data.rows:
        raise HeatmapRefused("no genes to draw — selecting nothing is a "
                             "refusal, not an empty figure")

    n_genes = len(data.rows)
    ref_n, test_n = len(data.ref_samples), len(data.test_samples)
    n_samples = ref_n + test_n
    all_samples = data.all_samples

    z_matrix = [[r.z[s] for s in all_samples] for r in data.rows]
    z_cmap = cmap_from(diverging_hexes("blue", "ember"), "heatmap_z")
    z_vmax = max(1e-6, max(abs(v) for row in z_matrix for v in row))

    raw_vals = [[r.ref_median_log2cpm1, r.test_median_log2cpm1] for r in data.rows]
    raw_vmax = max(1e-6, max(v for row in raw_vals for v in row))
    seq_cmap = cmap_from(sequential_hexes("teal"), "heatmap_raw")
    fc_vals = [abs(r.log2fc) for r in data.rows]

    row_h = max(0.16, min(0.30, 6.0 / max(n_genes, 1)))
    fig_h = 2.6 + row_h * n_genes * 1.75
    fig = plt.figure(figsize=(13.0, fig_h), dpi=100)
    gs = fig.add_gridspec(
        7, 2,
        height_ratios=[0.55, row_h * n_genes, 0.46, row_h * n_genes * 0.85,
                      0.34, 0.60, 0.42],
        width_ratios=[1.0, 0.05], hspace=0.28, wspace=0.05,
        left=0.30, right=0.90, top=0.96, bottom=0.02)

    title = title_line(data.ref)
    fig.suptitle(title, fontsize=13, y=0.985)

    # --- 1. group labels, in place above the z panel (mandatory string 2) --
    # Wrapped to roughly the pixel width its own block will occupy: a narrow
    # reference block (spike: n=13 of 74) next to a long prose label is
    # exactly how two "in place" labels end up printed through each other,
    # which is the same class of defect P3 found on the circos slide.
    import textwrap

    ax_group = fig.add_subplot(gs[0, 0])
    ax_group.set_xlim(0, n_samples)
    ax_group.set_ylim(0, 1)
    ax_group.axis("off")
    ref_label_text = group_label(data.ref.ref_label, ref_n)
    test_label_text = group_label(data.ref.test_label, test_n)
    ref_wrap_w = max(14, int(ref_n * 1.7))
    test_wrap_w = max(14, int(test_n * 1.7))
    ax_group.text(ref_n / 2, 1.0, textwrap.fill(ref_label_text, ref_wrap_w),
                 ha="center", va="top", fontsize=8.2, linespacing=1.3)
    ax_group.text(ref_n + test_n / 2, 1.0,
                 textwrap.fill(test_label_text, test_wrap_w),
                 ha="center", va="top", fontsize=8.2, linespacing=1.3)
    ax_group.axvline(ref_n, color="#999999", lw=0.8)

    # --- 2. z-score panel ---------------------------------------------------
    ax_z = fig.add_subplot(gs[1, 0])
    im = ax_z.imshow(z_matrix, aspect="auto", cmap=z_cmap, vmin=-z_vmax,
                     vmax=z_vmax, interpolation="none")
    ax_z.axvline(ref_n - 0.5, color="#ffffff", lw=1.4)
    ax_z.axvline(ref_n - 0.5, color="#333333", lw=0.6, linestyle="--")
    ax_z.set_yticks(range(n_genes))
    ax_z.set_yticklabels([r.display_symbol for r in data.rows], fontsize=7.4)
    ax_z.set_xticks([])
    for side in ("top", "right"):
        ax_z.spines[side].set_visible(False)

    ax_cbar = fig.add_subplot(gs[1, 1])
    fig.colorbar(im, cax=ax_cbar)
    ax_cbar.set_ylabel("z", fontsize=8)
    ax_cbar.tick_params(labelsize=6.5)

    # --- 3. caption: z-score sentence + colour direction (mandatory 3, 6) --
    ax_zcap = fig.add_subplot(gs[2, 0])
    ax_zcap.axis("off")
    zs_sentence = zscore_sentence(n_samples, n_genes)
    colour_sentence = colour_direction_sentence()
    sign_sentence = data.ref.sign_sentence
    ax_zcap.text(0.0, 0.92, zs_sentence, fontsize=7.8, va="top", wrap=True,
                transform=ax_zcap.transAxes)
    ax_zcap.text(0.0, 0.42, colour_sentence, fontsize=7.8, va="top",
                color="#333333", transform=ax_zcap.transAxes)
    # I8: the sign sentence, so a reader cannot read this figure's direction
    # backwards -- the `{reference}_{test}` trap every artefact must guard
    # against, not only the circos.
    ax_zcap.text(0.0, 0.08, sign_sentence, fontsize=7.8, va="top",
                color="#333333", style="italic", transform=ax_zcap.transAxes)

    # --- 4. raw-scale panel: same rows, raw scale (mandatory string 4) -----
    gs_raw = gs[3, 0].subgridspec(1, 2, width_ratios=[1.0, 0.55], wspace=0.55)
    ax_raw = fig.add_subplot(gs_raw[0, 0])
    ax_raw.imshow(raw_vals, aspect="auto", cmap=seq_cmap, vmin=0, vmax=raw_vmax,
                 interpolation="none")
    ax_raw.set_yticks(range(n_genes))
    ax_raw.set_yticklabels([r.display_symbol for r in data.rows], fontsize=7.0)
    ax_raw.set_xticks([0, 1])
    # Short labels: the full prose names are already stated once, in place,
    # above the z panel (mandatory string 2) and in the sign sentence -- a
    # long label centred on a narrow column here is the same collision the
    # group-label row above had to be wrapped to avoid, and repeating the
    # full text a third time does not make the figure more honest.
    ax_raw.set_xticklabels([f"ref median\n(n={ref_n})", f"test median\n(n={test_n})"],
                          fontsize=6.6)

    ax_fc = fig.add_subplot(gs_raw[0, 1])
    ax_fc.barh(range(n_genes), fc_vals, color="#555555", height=0.7)
    # `imshow` puts row 0 at the top by default, matching `ax_z` above; `barh`
    # puts it at the bottom, so only the bar axis needs inverting to keep both
    # panels in the same row order as the z panel (P4/P1's lesson generalised:
    # a reader comparing two panels by eye must not have to check which one
    # flipped).
    ax_fc.invert_yaxis()
    ax_fc.set_yticks([])
    ax_fc.set_xlabel("|log2FC|", fontsize=7.2)
    ax_fc.tick_params(labelsize=6.5)

    ax_rawcap = fig.add_subplot(gs[4, 0])
    ax_rawcap.axis("off")
    raw_sentence = raw_scale_sentence()
    ax_rawcap.text(0.0, 0.8, raw_sentence, fontsize=7.8, va="top", wrap=True,
                  transform=ax_rawcap.transAxes)

    # --- 5. per-sample influence strip (mandatory string 5) -----------------
    ax_inf = fig.add_subplot(gs[5, 0])
    deltas = [data.influence[s] for s in all_samples]
    colours = ["#c0392b" if s in data.influence_outliers else "#7f8c8d"
              for s in all_samples]
    ax_inf.bar(range(n_samples), deltas, color=colours, width=0.85)
    ax_inf.axhline(0, color="#333333", lw=0.6)
    ax_inf.axvline(ref_n - 0.5, color="#999999", lw=0.8, linestyle="--")
    ax_inf.set_xlim(-0.5, n_samples - 0.5)
    ax_inf.set_xticks([])
    ax_inf.set_ylabel("Δ mean-z\ndiff.", fontsize=7)
    ax_inf.tick_params(labelsize=6.5)

    inf_sentence = influence_sentence(
        data.influence_threshold, len(data.influence_outliers), n_samples)
    ax_infcap = fig.add_subplot(gs[6, 0])
    ax_infcap.axis("off")
    ax_infcap.text(0.0, 0.85, inf_sentence, fontsize=7.8, va="top", wrap=True,
                  transform=ax_infcap.transAxes)

    # --- 7. the three-stage accounting line (mandatory string 7, I9) -------
    accounting_line = data.accounting.accounting_line()
    dedup_note = (f" · {data.n_deduped_columns} duplicate raw-count column(s) "
                  f"deduped" if data.n_deduped_columns else "")
    ax_infcap.text(0.0, 0.30, accounting_line + dedup_note, fontsize=7.8,
                  va="top", color="#111111", transform=ax_infcap.transAxes)

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_svg, format="svg")
    if out_png is not None:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, format="png", dpi=150)
    plt.close(fig)

    mandatory = [
        title, ref_label_text, test_label_text, zs_sentence, raw_sentence,
        inf_sentence, colour_sentence, accounting_line, sign_sentence,
    ]
    return HeatmapResult(svg_path=out_svg, mandatory_strings=mandatory,
                         n_genes=n_genes, n_samples=n_samples)


__all__ = [
    "HeatmapData", "HeatmapGeneRow", "HeatmapRefused", "HeatmapResult",
    "RawCounts", "colour_direction_sentence", "compute", "group_label",
    "influence_sentence", "load_raw_counts", "parse_raw_counts",
    "raw_scale_sentence", "render", "title_line", "zscore_sentence",
]
