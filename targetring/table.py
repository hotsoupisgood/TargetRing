"""The table view: a run's cells as a wide grid, read back out of its artefacts.

v3.6. Puck asked for the circle's data as a table he can sort. The one thing this
module must never be is a second computation of that data -- a table and a circle
that disagree about a gene is `AGENTS.md:17`'s plausible wrong figure with two
witnesses instead of one. So it is a **pivot of `values.csv`**, the artefact
invariant 1 already asserts is cell-for-cell identical to the rendered rings,
and it recomputes nothing: state, value, fill colour, detail and Ensembl id all
come off the row the renderer wrote.

Reading from disk rather than from `_runs` is also what makes the 250 runs
already under `runs/` viewable. `RUN` is only ever set in-page after an analyse
completes, so before this there was no way to look at a run you had already
produced.

Three things are re-asserted here rather than assumed, because a file on disk can
be truncated, hand-edited or written by an older build:

  * `input == rendered + ledgered`, from the manifest's own three counts
    (AGENTS.md rule 3). The table refuses to render rather than show a gene set
    that does not add up.
  * the pivot is complete -- every gene has a cell on every ring. A hole in a
    wide table is a blank cell, and a blank cell is the one rendering of
    `no_data` that rule 1 forbids.
  * every cell round-trips through `states.Cell`, so a `value` row with no number
    and a `no_data` row carrying one are both rejected at the door instead of
    reaching the page.
  * **the cell section ends where the file says it ends** (v3.9). `values.csv`
    appends the drop ledger after the cells -- all 248 runs under `runs/` do,
    measured -- so the boundary is asserted against the `#` marker and against the
    file's own `rendered=` count rather than guessed at from row shape. See
    `read_cell_rows`.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from .config import RUNS_DIR
from .order import EXTERNAL_RING_COUNT, EXTERNAL_RINGS
from .states import Cell

# A run id names a directory. It arrives in a URL path, so it is checked against
# what `run.py` actually generates rather than trusted: `20260928T044236Z-893dec`.
RUN_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")

# The columns the pivot cannot be built without. `ensembl_id` (v2.9), `fill`,
# `ring_label`, `detail` and `source` are read where present and are deliberately
# NOT required: `runs/` holds files written by every version of this build since
# v1, and refusing to show an older run because it predates a column is refusing
# to show it for a reason a reader does not care about. What is missing is
# missing on screen too — an older run's Ensembl column is empty, not invented.
REQUIRED_CELL_COLUMNS = ("cell_id", "spoke_index", "gene", "ring", "state", "value")

# What `run._write_csv` puts in front of every section it appends after the cells.
# The cell section's end is asserted against this rather than inferred from a row
# that merely fails to look like a cell -- see `read_cell_rows`.
SECTION_MARKER = "#"


class TableUnavailable(Exception):
    """This run cannot be shown as a table, and the reason names the artefact."""


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id)) and ".." not in run_id


def run_dir(run_id: str) -> Path:
    if not valid_run_id(run_id):
        raise TableUnavailable(f"{run_id!r} is not a run id")
    d = RUNS_DIR / run_id
    if not d.is_dir():
        raise TableUnavailable(f"no run {run_id} under {RUNS_DIR}")
    return d


@dataclass(frozen=True)
class TableCell:
    """One gene x one ring, as the renderer wrote it.

    `fill` is the colour that cell was actually painted, so the table's swatch is
    the figure's swatch and not a second opinion about it. It is carried for
    every state, including the hatched ones -- `STATE_STYLE`'s greys are in the
    CSV too.
    """

    cell: Cell
    fill: str
    source: str

    @property
    def state(self) -> str:
        return self.cell.state

    @property
    def value(self) -> float | None:
        return self.cell.value

    @property
    def detail(self) -> str:
        return self.cell.detail

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "value": self.value,
                "detail": self.detail, "fill": self.fill, "source": self.source}


@dataclass
class RunTable:
    run_id: str
    region: str
    rings: list[dict[str, str]]           # {key, label} in the figure's radial order
    genes: list[str]                      # the order this run DREW the spokes in
    submitted_order: list[str]
    submitted_order_source: str           # "manifest" | "drawn"
    cells: dict[str, dict[str, TableCell]] = field(default_factory=dict)  # [ring][gene]
    ensembl: dict[str, str] = field(default_factory=dict)
    k: dict[str, int] = field(default_factory=dict)
    k_max: int = EXTERNAL_RING_COUNT
    manifest: dict[str, Any] = field(default_factory=dict)

    def cells_by_ring(self) -> dict[str, dict[str, Cell]]:
        """The shape `order.py` sorts -- the same one `run.py` hands the figure."""
        return {ring: {g: tc.cell for g, tc in per.items()}
                for ring, per in self.cells.items()}


def read_cell_rows(csv_path: Path) -> list[dict[str, str]]:
    """The cell section of `values.csv`, and only it — with the boundary asserted.

    `run._write_csv` writes the cells first and then appends up to four further
    sections, each introduced by a blank line and a `#` comment: spoke order,
    active filters, kept-not-filtered, and the **drop ledger**, whose own header
    is `input_symbol,status,resolved_symbol,ensembl_id,row,reason`. A separate
    `ledger.csv` arrived later and is an *addition*: measured 2026-09-28 over
    `runs/`, all **248** runs with a `values.csv` carry the appended section and
    227 of them also carry the separate file. There is no such thing here as a
    `values.csv` whose ledger lives only elsewhere.

    So a plain `DictReader` run to the end yields ledger rows wearing cell column
    names -- `input_symbol` landing in `cell_id`, a drop reason landing in
    `value` -- which is how a gene that was dropped from the figure would arrive
    back in the grid it was dropped from. That is a plausible wrong table.

    Three things are asserted rather than inferred from row shape, because
    "stop at the first row that does not look like a cell" is a guess about
    the file and these are facts about it:

      * **the boundary is a section marker.** The row that ends the cells must
        begin with `#`, which is the delimiter `_write_csv` writes. A row that is
        neither a cell nor a marker means the file was truncated or hand-edited
        mid-section, and stopping there silently would drop real cells.
      * **nothing after the boundary is a cell.** Checked to end of file, so a
        cell section split by an interleaved marker is a refusal rather than a
        half-read table.
      * **the file's own count agrees.** `_write_csv`'s trailing
        `# input_rows=.. rendered=.. ledgered=..` line states how many genes the
        cell section holds. Compared against the genes actually read, it catches
        both halves of the failure at once: stopping too early and reading too
        far. `table.load` separately checks the manifest's copy of the same
        identity; this one is the CSV checking itself.
    """
    rows: list[dict[str, str]] = []
    boundary = ""
    trailing: list[str] = []
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        have = set(reader.fieldnames or ())
        if not set(REQUIRED_CELL_COLUMNS) <= have:
            raise TableUnavailable(
                f"{csv_path.name} is missing the column(s) "
                f"{', '.join(sorted(set(REQUIRED_CELL_COLUMNS) - have))}, so its cells "
                f"cannot be pivoted (header: {reader.fieldnames!r})."
            )
        for row in reader:
            cell_id = (row.get("cell_id") or "").strip()
            if cell_id.startswith("cell|"):
                if boundary:
                    raise TableUnavailable(
                        f"{csv_path.name}: {cell_id} is a cell row appearing AFTER the "
                        f"section boundary {boundary!r}. The cell section is not "
                        f"contiguous, so reading up to the boundary would silently "
                        f"drop cells; no table is drawn."
                    )
                rows.append(row)
                continue
            if not boundary:
                if not cell_id.startswith(SECTION_MARKER):
                    raise TableUnavailable(
                        f"{csv_path.name}: the cell section ends at {cell_id!r}, which is "
                        f"neither a cell row nor a {SECTION_MARKER!r} section marker. "
                        f"`run._write_csv` separates every appended section with one, so "
                        f"this file is truncated or was edited mid-section."
                    )
                boundary = cell_id
            if cell_id.startswith(SECTION_MARKER):
                trailing.append(cell_id)
    if not rows:
        raise TableUnavailable(f"{csv_path.name} has no cell rows")
    _assert_the_files_own_count_agrees(csv_path, rows, trailing)
    return rows


def _assert_the_files_own_count_agrees(csv_path: Path, rows: list[dict[str, str]],
                                       markers: list[str]) -> None:
    """`# input_rows=15 rendered=14 ledgered=3`, checked against the cells read.

    Written by `_write_csv` on every run and present in all 248 under `runs/`
    (measured). It is the only statement in the file about where the cell section
    *should* end, so it is the one thing worth comparing a read against: a reader
    that stopped early reports fewer genes than the writer counted, and one that
    ran on into the ledger reports more. Absent from a file written by some older
    build, this check is skipped and says nothing -- an unverifiable claim is not
    a failed one.
    """
    stated: int | None = None
    for m in markers:
        found = re.search(r"\brendered=(\d+)", m)
        if found:
            stated = int(found.group(1))
    if stated is None:
        return
    genes = len({r["gene"] for r in rows})
    if genes != stated:
        raise TableUnavailable(
            f"{csv_path.name} says `rendered={stated}` and its cell section holds "
            f"{genes} gene(s). The file disagrees with itself about where the cells "
            f"end -- the likeliest cause is an appended section being read as data, "
            f"or cells lost from the middle. No table is drawn."
        )


def ring_keys_drawn(run_id: str) -> list[str]:
    """The ring keys one run's `values.csv` actually drew, in the figure's order.

    v3.9. Exists so that nothing outside this module re-implements the section
    boundary. `sources`' coverage check is asserted over the keys of every run
    under `runs/`, and a helper that read to end of file would hand it
    `input_symbol` as a ring key -- the same defect as the table's, in the
    provenance audit that was written to catch that class of defect.
    """
    keys: list[str] = []
    for row in read_cell_rows(run_dir(run_id) / "values.csv"):
        if row["ring"] not in keys:
            keys.append(row["ring"])
    return keys


def _cell_from_row(row: dict[str, str]) -> Cell:
    """`states.Cell` is the gate, not a convenience. A row claiming `value` with
    an empty number, or `no_data` with one, raises here instead of being drawn."""
    state = (row.get("state") or "").strip()
    raw = (row.get("value") or "").strip()
    detail = row.get("detail") or ""
    if state == "value":
        try:
            return Cell("value", float(raw), detail)
        except (TypeError, ValueError) as e:
            raise TableUnavailable(
                f"{row.get('cell_id')}: state=value with an unreadable number {raw!r}"
            ) from e
    if raw:
        raise TableUnavailable(
            f"{row.get('cell_id')}: state={state!r} carries the value {raw!r}. "
            f"Only a measurement has a number."
        )
    if state not in ("no_data", "not_applicable", "failed"):
        raise TableUnavailable(f"{row.get('cell_id')}: unknown cell state {state!r}")
    return Cell(state, None, detail)  # type: ignore[arg-type]


def _assert_accounted_from_manifest(m: dict[str, Any], run_id: str) -> None:
    """AGENTS.md rule 3, re-asserted against the artefact rather than trusted.

    The run asserted this before it rendered. The table asserts it again because
    the table is a separate reading of the same run, and a reader who is shown a
    grid of 14 genes must be shown the same accounting the figure was stamped
    with -- or nothing.
    """
    try:
        i, r, d = (int(m["input_row_count"]), int(m["rendered_count"]),
                   int(m["ledger_count"]))
    except (KeyError, TypeError, ValueError) as e:
        raise TableUnavailable(
            f"run {run_id}: manifest.json has no gene counts, so "
            f"`input == rendered + ledgered` cannot be checked"
        ) from e
    if i != r + d:
        raise TableUnavailable(
            f"run {run_id}: gene accounting is broken in the artefact — {i} input rows "
            f"!= {r} rendered + {d} ledgered. No table is drawn for it."
        )


#: I1's `detail`, which is the only independent record of the ring count that
#: runs written before `ring_sources` carry. Prose, so it is matched for the two
#: integers and nothing else -- a refusal that depended on reading a sentence
#: would be a worse problem than the one it closes.
_I1_SHAPE = re.compile(r"^(\d+) cells? across (\d+) rings?\b")


def _assert_ring_list_agrees(run_id: str, manifest: dict[str, Any],
                             ring_keys: list[str], n_cells: int) -> None:
    """The rings in `values.csv` are the rings the run says it drew.

    **Why this is not redundant with the rectangularity check.** That check looks
    for a gene with no cell on a ring it can see, and it can only see rings that
    some surviving row names. A ring whose cells are all gone is not a hole in
    anything -- it is simply absent, and the table renders an 11-ring figure of a
    12-ring run with nothing on it saying so. For a one-gene run the two cases
    coincide: every lost cell is a lost ring.

    That is a plausible wrong figure built from malformed input, which
    `AGENTS.md:17` ranks below a crash. So it is a refusal.

    The cross-check needs a record of the rings that is NOT derived from
    `values.csv`. Two exist, and which is available depends on when the run was
    written (all 155 runs on disk carry one or the other):

    * `ring_sources` -- the citation list, one entry per ring, by name. Checked
      both ways: a ring in the file that the manifest never cited is as wrong as
      a cited ring with no cells.
    * for older runs, invariant **I1**'s `detail`, which states the cell and ring
      counts the run asserted at write time. Counts only, no names.

    A run carrying neither cannot be cross-checked, and says so rather than
    being drawn on the assumption that it is fine -- the same stance
    `ensembl_release_from` takes about a release the source never stated.
    """
    sources = manifest.get("ring_sources")
    if sources is not None:
        cited = [str(s.get("ring") or "") for s in sources if s.get("ring")]
        drawn = set(ring_keys)
        lost = [r for r in cited if r not in drawn]
        uncited = [r for r in ring_keys if r not in set(cited)]
        if lost or uncited:
            raise TableUnavailable(
                f"run {run_id}: values.csv and manifest.json disagree about which "
                f"rings this run has. "
                + (f"{len(lost)} ring(s) the manifest cites have no cell in the "
                   f"CSV at all ({', '.join(lost[:6])}) — the figure would be "
                   f"drawn without them and would look complete. " if lost else "")
                + (f"{len(uncited)} ring(s) in the CSV are cited nowhere "
                   f"({', '.join(uncited[:6])}). " if uncited else "")
                + "The artefacts disagree; no table is drawn.")
        return

    i1 = next((x for x in (manifest.get("invariants") or [])
               if x.get("id") == "I1"), None)
    m = _I1_SHAPE.match(str((i1 or {}).get("detail") or ""))
    if m is None:
        raise TableUnavailable(
            f"run {run_id}: the manifest records neither `ring_sources` nor a "
            f"readable I1 cell count, so there is nothing to check values.csv's "
            f"ring list against. A ring that lost all its cells would be "
            f"invisible here and the figure would still draw, so it is refused "
            f"rather than assumed intact.")
    want_cells, want_rings = int(m.group(1)), int(m.group(2))
    if (n_cells, len(ring_keys)) != (want_cells, want_rings):
        raise TableUnavailable(
            f"run {run_id}: values.csv has {n_cells} cell(s) across "
            f"{len(ring_keys)} ring(s); the run's own I1 recorded {want_cells} "
            f"across {want_rings}. The artefacts disagree; no table is drawn.")


def load(run_id: str) -> RunTable:
    """Pivot one run's `values.csv` to wide, with its manifest beside it."""
    d = run_dir(run_id)
    csv_path, man_path = d / "values.csv", d / "manifest.json"
    if not csv_path.exists():
        raise TableUnavailable(f"run {run_id} has no values.csv")
    if not man_path.exists():
        raise TableUnavailable(f"run {run_id} has no manifest.json")
    try:
        manifest: dict[str, Any] = json.loads(man_path.read_text())
    except json.JSONDecodeError as e:
        raise TableUnavailable(f"run {run_id}: manifest.json is not readable: {e}") from e
    _assert_accounted_from_manifest(manifest, run_id)

    rows = read_cell_rows(csv_path)

    # Ring order is the order the rings appear in the file, which is the figure's
    # own radial order -- ring 1 outermost. Taken from the artefact rather than
    # from today's `RING_SPECS`, so an old run is shown with the rings it was
    # drawn with instead of with this build's list (PANEL P1: a reader must be
    # able to say which band is ring 1, and a table that renamed the columns
    # against an older file would be the same misreading with a header on it).
    rings: list[dict[str, str]] = []
    seen_rings: set[str] = set()
    # Spoke order likewise: `spoke_index` is what the circle drew.
    by_index: dict[int, str] = {}
    cells: dict[str, dict[str, TableCell]] = {}
    ensembl: dict[str, str] = {}

    for row in rows:
        ring, gene = row["ring"], row["gene"]
        if ring not in seen_rings:
            seen_rings.add(ring)
            rings.append({"key": ring, "label": row.get("ring_label") or ring})
        try:
            by_index[int(row["spoke_index"])] = gene
        except (TypeError, ValueError) as e:
            raise TableUnavailable(
                f"{row.get('cell_id')}: unreadable spoke_index {row.get('spoke_index')!r}"
            ) from e
        if gene in cells.setdefault(ring, {}):
            raise TableUnavailable(
                f"run {run_id}: {gene} appears twice on ring {ring}. A wide table "
                f"would silently keep one of them."
            )
        cells[ring][gene] = TableCell(cell=_cell_from_row(row),
                                      fill=row.get("fill") or "",
                                      source=row.get("source") or "")
        ensembl.setdefault(gene, row.get("ensembl_id") or "")

    genes = [by_index[i] for i in sorted(by_index)]

    # Every gene on every ring. A missing pair would render as an empty cell, and
    # an empty cell is indistinguishable from "nothing to say" -- exactly the
    # reading rule 1 exists to prevent.
    holes = [f"{g}/{ring['key']}" for ring in rings for g in genes
             if g not in cells.get(ring["key"], {})]
    if holes:
        raise TableUnavailable(
            f"run {run_id}: the pivot is not rectangular — {len(holes)} gene/ring "
            f"pair(s) have no cell ({', '.join(holes[:6])}"
            f"{'…' if len(holes) > 6 else ''}). Every gene must have a cell on every "
            f"ring, even if that cell is hatched."
        )

    # ...and the hole check alone is not enough, because `rings` is built FROM
    # `rows`: a ring can never be missing from the list the holes are looked for
    # in. Lose every cell on one ring and there is no hole, just a narrower
    # figure that looks complete. On a one-gene run that is ANY single lost cell.
    # So the ring list is cross-checked against the run's other artefact, exactly
    # as `gene_order` is immediately below -- the manifest was written by the
    # same run and is not derived from values.csv, which is the whole point.
    _assert_ring_list_agrees(run_id, manifest, [r["key"] for r in rings],
                             n_cells=len(rows))

    drawn = list(manifest.get("gene_order") or [])
    if drawn and drawn != genes:
        raise TableUnavailable(
            f"run {run_id}: values.csv draws the spokes in a different order from the "
            f"one manifest.json records. The artefacts disagree; no table is drawn."
        )

    # The tie-break and the missing block are defined "in submitted order"
    # (PLAN §3.1). Runs written before v3.6 do not record it, and it is not
    # recoverable from a ring-ordered figure -- so it is stated which one is in
    # use rather than guessed at. For a `submitted` run the two are the same list.
    submitted = list(manifest.get("submitted_order") or [])
    source = "manifest"
    if not submitted or sorted(submitted) != sorted(genes):
        submitted, source = list(genes), "drawn"

    # k off the `completeness` ring where the run drew one, so the column is the
    # number stamped on the figure rather than a recount. Recounted only for a
    # run that has no such ring, and then from the same EXTERNAL_RINGS list.
    k: dict[str, int] = {}
    comp = cells.get("completeness", {})
    for g in genes:
        c = comp.get(g)
        if c is not None and c.state == "value" and c.value is not None:
            k[g] = int(c.value)
        else:
            k[g] = sum(1 for ring in EXTERNAL_RINGS
                       if cells.get(ring, {}).get(g) is not None
                       and cells[ring][g].state == "value")

    return RunTable(
        run_id=run_id,
        region=str(manifest.get("region") or ""),
        rings=rings, genes=genes,
        submitted_order=submitted, submitted_order_source=source,
        cells=cells, ensembl=ensembl, k=k, manifest=manifest,
    )


#: Where `scripts/bake_drawers.py` writes a run's per-gene drawers, one JSON per
#: gene, beside the run's other artefacts. A file, not a cache entry: it is part
#: of the run's record and is served exactly as written.
DRAWER_DIR = "drawer"
DRAWER_GENE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def baked_drawer_path(run_id: str, gene: str) -> Path | None:
    """The baked drawer file for one gene of one run, or None if there is none.

    `gene` is whitelisted against `DRAWER_GENE_RE` before it touches a path,
    for the reason `valid_run_id` exists: this segment arrives from a URL.
    """
    if not DRAWER_GENE_RE.match(gene):
        return None
    p = run_dir(run_id) / DRAWER_DIR / f"{gene}.json"
    return p if p.is_file() else None


def baked_drawer_genes(run_id: str) -> list[str]:
    """The genes a run has a baked drawer for, sorted. Empty when the run was
    never baked -- which is every run `scripts/bake_drawers.py` was not run on."""
    try:
        d = run_dir(run_id) / DRAWER_DIR
    except TableUnavailable:
        return []
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json") if DRAWER_GENE_RE.match(p.stem))


#: The downloadable artefacts a run may have written, in the order the three
#: download menus list them. Not every run has every one: the slide pair is an
#: optional export and a run cut off partway has fewer.
DOWNLOADABLE = (
    "figure.png", "figure.svg", "figure-slide.svg", "figure-slide.png",
    "caption.txt", "legend.png", "legend.svg",
    "values.csv", "ledger.csv", "manifest.json",
)


def artefacts_on_disk(run_id: str) -> list[str]:
    """Which of `DOWNLOADABLE` this run actually wrote.

    v4.0. The three download menus are built from this when there is no analyse
    response to build them from. They used to be filled only from that
    response's `*_url` fields, so a run opened from disk got ten `<a>` elements
    with no `href` — links that looked like links and did nothing, on a page
    whose own list of what works says "every download" (UI audit, 2026-10-19).

    Read rather than assumed, and that is the value of it: a menu item pointing
    at a file the run never wrote is the same defect one step further on, and
    HEAD-probing from the page to find out put 404s in the console of a box
    whose claim is that it fetches nothing.
    """
    try:
        d = run_dir(run_id)
    except TableUnavailable:
        return []
    return [n for n in DOWNLOADABLE if (d / n).exists()]


def recent_runs(limit: int = 40,
                only: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The runs on disk, newest first.

    `only`: restrict to these ids BEFORE the newest-`limit` cut. Demo mode
    found out why on 2026-10-15: filtering the newest 40 of 273 runs down to an
    allowlist of three returned one, because the other two were older than the
    40th. A list that is the allowlist must start from the allowlist.

    Ordered by directory mtime rather than by name. Most run ids are
    `YYYYMMDDTHHMMSSZ-<hex>` and sort chronologically, but `runs/` also holds
    hand-named directories (`smoke1`, `V31-de_realistic-Lung`), and by name
    those sort above every real run — so the picker would have opened on a
    fixture from four days ago. One `stat` per directory, and only the ids
    actually being offered have their manifest read.
    """
    if not RUNS_DIR.is_dir():
        return []
    keep = None if only is None else set(only)
    dirs = [p for p in RUNS_DIR.iterdir()
            if p.is_dir() and valid_run_id(p.name) and (p / "values.csv").exists()
            and (keep is None or p.name in keep)]
    ids = [p.name for p in sorted(dirs, key=lambda p: (p.stat().st_mtime, p.name),
                                  reverse=True)]
    out: list[dict[str, Any]] = []
    for run_id in ids[:limit]:
        entry: dict[str, Any] = {"run_id": run_id}
        try:
            m = json.loads((RUNS_DIR / run_id / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            # Listed anyway, and named as unreadable. A run that is on disk and
            # missing from the picker is a run somebody concludes was never made.
            entry["note"] = "manifest unreadable"
            out.append(entry)
            continue
        c = m.get("contrast") or {}
        entry.update({
            "timestamp_utc": m.get("timestamp_utc", ""),
            "region": m.get("region", ""),
            "n_genes": len(m.get("gene_order") or []),
            "ordering": m.get("ordering", ""),
            "any_invariant_failed": bool(m.get("any_invariant_failed")),
            # Where the genes came from, so a list of runs can say "Crohn's
            # disease, E-GEOD-101794 g2_g1" rather than only a region and a
            # count. Empty strings on a paste run; the page says "pasted table".
            "contrast": (f"{c.get('accession', '')} {c.get('contrast_id', '')}".strip()
                         if c else ""),
            "contrast_name": (c.get("name") or "") if c else "",
            "filtered": bool(m.get("filters")),
        })
        out.append(entry)
    return out


__all__ = ["RunTable", "TableCell", "TableUnavailable", "load", "recent_runs",
           "read_cell_rows", "ring_keys_drawn", "valid_run_id", "run_dir",
           "baked_drawer_path", "baked_drawer_genes", "DRAWER_DIR",
           "RUN_ID_RE", "REQUIRED_CELL_COLUMNS", "SECTION_MARKER"]
