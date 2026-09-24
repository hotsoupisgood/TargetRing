"""The six invariants of PLAN.md §6, checked against the rendered artefact.

These run at the end of every render, including the unattended one, and their
PASS/FAIL goes into the manifest and onto the figure. The point of checking the
SVG rather than the in-memory model is that it survives a regression in the join
logic: QA S1 check 4 catches B2 in the artefact even if the code that built it
has gone wrong.

Each check returns a Result. `NOT_EVALUATED` is a real outcome and is reported
as loudly as a failure -- a check that could not run is not a check that passed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from .figure import STATE_STYLE, colormap_hexes

Outcome = Literal["PASS", "FAIL", "NOT_EVALUATED"]

SVG_NS = "{http://www.w3.org/2000/svg}"
_CELL_ID = re.compile(r"^cell\|(?P<ring>[^|]+)\|(?P<index>\d+)\|(?P<gene>.+)$")


@dataclass
class Result:
    id: str
    name: str
    outcome: Outcome
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.outcome != "PASS"


@dataclass
class InvariantReport:
    results: list[Result] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.failed]

    @property
    def ok(self) -> bool:
        return not self.failures

    def banner_lines(self) -> list[str]:
        return [f"{r.id} {r.name}: {r.outcome} — {r.detail[:150]}" for r in self.failures]

    def as_json(self) -> list[dict[str, str]]:
        return [{"id": r.id, "name": r.name, "outcome": r.outcome, "detail": r.detail}
                for r in self.results]


_URL_REF = re.compile(r"url\(#([^)]+)\)")


def _pattern_colours(root: ET.Element) -> dict[str, set[str]]:
    """Every literal colour used inside each `<pattern>` definition.

    A hatched cell's fill is a `url(#…)` reference. Comparing that string to a
    colormap hex would be a vacuous proof of distinctness -- the pattern could
    be painted in a colormap colour and the check would still pass. So the
    pattern is resolved and its actual fills and strokes are what invariant 2
    compares.
    """
    out: dict[str, set[str]] = {}
    for pat in root.iter(f"{SVG_NS}pattern"):
        pid = pat.get("id")
        if not pid:
            continue
        colours: set[str] = set()
        for node in pat.iter():
            for attr in ("fill", "stroke"):
                v = (node.get(attr) or "").strip().lower()
                if v and v != "none":
                    colours.add(v)
            style = node.get("style") or ""
            for m in re.finditer(r"(?:fill|stroke):\s*([^;]+)", style):
                v = m.group(1).strip().lower()
                if v and v != "none":
                    colours.add(v)
        out[pid] = colours
    return out


def _parse_cells(svg_path: Path) -> list[dict[str, Any]]:
    """Every element the renderer tagged with a `cell|...` id, in document order."""
    root = ET.parse(svg_path).getroot()
    patterns = _pattern_colours(root)
    out: list[dict[str, Any]] = []
    for el in root.iter():
        gid = el.get("id") or ""
        m = _CELL_ID.match(gid)
        if not m:
            continue
        fill = _fill_of(el)
        ref = _URL_REF.match(fill)
        out.append({
            "id": gid,
            "ring": m.group("ring"),
            "index": m.group("index"),
            "gene": m.group("gene"),
            "fill": fill,
            # For a plain fill this is the colour itself; for a hatch it is
            # every colour the pattern actually paints with.
            "colours": patterns.get(ref.group(1), set()) if ref else {fill},
        })
    return out


def _fill_of(el: ET.Element) -> str:
    """The gid lands on a wrapping `<g>`; the fill is on the `<path>` inside it.

    A hatched cell's fill is a `url(#pattern)` reference, which is exactly what
    we want for invariant 2: a pattern reference can never equal a colormap hex.
    """
    for node in [el, *el.iter()]:
        style = node.get("style") or ""
        m = re.search(r"fill:\s*([^;]+)", style)
        if m:
            return m.group(1).strip().lower()
        if node.get("fill"):
            return node.get("fill", "").strip().lower()
    return ""


def check_all(
    *,
    svg_path: Path,
    csv_rows: Sequence[dict[str, Any]],
    gene_order: Sequence[str],
    input_count: int,
    rendered_count: int,
    ledger_count: int,
    regression: dict[str, Any] | None = None,
    literature_audit: dict[str, Any] | None = None,
) -> InvariantReport:
    rep = InvariantReport()
    try:
        svg_cells = _parse_cells(svg_path)
    except Exception as e:  # noqa: BLE001
        for i, name in enumerate(
            ["cell-count identity", "missing-fill disjointness", "label correspondence"], 1
        ):
            rep.results.append(Result(f"I{i}", name, "NOT_EVALUATED", f"SVG unreadable: {e}"))
        svg_cells = []
    else:
        rep.results.append(_i1_cell_count(svg_cells, csv_rows))
        rep.results.append(_i2_disjoint_fills(svg_cells, csv_rows))
        rep.results.append(_i3_labels(svg_cells, csv_rows, gene_order))

    rep.results.append(_i4_accounting(input_count, rendered_count, ledger_count))
    rep.results.append(_i5_literature(literature_audit))
    rep.results.append(_i6_regression(regression))
    return rep


def _i1_cell_count(svg_cells, csv_rows) -> Result:
    """PLAN §6.1 — cell-count identity between the CSV and the rendered rings."""
    svg_by_ring: dict[str, int] = {}
    for c in svg_cells:
        svg_by_ring[c["ring"]] = svg_by_ring.get(c["ring"], 0) + 1
    csv_by_ring: dict[str, int] = {}
    for r in csv_rows:
        csv_by_ring[r["ring"]] = csv_by_ring.get(r["ring"], 0) + 1
    if svg_by_ring == csv_by_ring:
        return Result("I1", "cell-count identity", "PASS",
                      f"{len(svg_cells)} cells across {len(csv_by_ring)} rings match the CSV")
    diff = {
        r: (csv_by_ring.get(r, 0), svg_by_ring.get(r, 0))
        for r in set(csv_by_ring) | set(svg_by_ring)
        if csv_by_ring.get(r) != svg_by_ring.get(r)
    }
    return Result("I1", "cell-count identity", "FAIL",
                  f"csv vs svg cell counts differ (ring: csv, svg): {diff}")


def _i2_disjoint_fills(svg_cells, csv_rows) -> Result:
    """PLAN §6.2 — the missing fill must be provably distinct from every value
    colour. Checked in the artefact, so it survives a palette change."""
    state_by_id = {r["cell_id"]: r["state"] for r in csv_rows}
    missing_colours: set[str] = set()
    value_colours: set[str] = set()
    n_missing = n_value = 0
    for c in svg_cells:
        st = state_by_id.get(c["id"])
        if st is None:
            continue
        if st == "value":
            n_value += 1
            value_colours |= set(c["colours"])
        else:
            n_missing += 1
            missing_colours |= set(c["colours"])

    overlap = missing_colours & value_colours
    if overlap:
        return Result("I2", "missing-fill disjointness", "FAIL",
                      f"these colours paint both a value and a non-value cell: "
                      f"{sorted(overlap)}")
    # Against the whole colormap, not only the values this run happened to draw,
    # so a future dataset cannot collide.
    cmap_overlap = missing_colours & colormap_hexes()
    if cmap_overlap:
        return Result("I2", "missing-fill disjointness", "FAIL",
                      f"non-value colours {sorted(cmap_overlap)} are inside the "
                      f"{len(colormap_hexes())}-step value colormap")
    if n_missing == 0:
        return Result("I2", "missing-fill disjointness", "PASS",
                      f"no non-value cells in this run; the {len(STATE_STYLE)} reserved "
                      f"fills remain outside the colormap")
    return Result("I2", "missing-fill disjointness", "PASS",
                  f"{n_missing} non-value cells painted only in {sorted(missing_colours)}; "
                  f"disjoint from the {n_value} value cells' {len(value_colours)} colours and "
                  f"from the full {len(colormap_hexes())}-step colormap "
                  f"(hatch patterns resolved, not compared as url refs)")


def _i3_labels(svg_cells, csv_rows, gene_order) -> Result:
    """PLAN §6.3 — spoke n's label matches spoke n's data.

    This is the check that catches the QA B2 off-by-one in the rendered
    artefact. A count check alone passes while the labels are wrong.
    """
    expected = {
        (r["ring"], str(r["spoke_index"])): r["gene"] for r in csv_rows
    }
    mismatches = []
    for c in svg_cells:
        want = expected.get((c["ring"], c["index"]))
        if want is None:
            mismatches.append(f"{c['id']} has no CSV row")
        elif want != c["gene"]:
            mismatches.append(f"{c['ring']} spoke {c['index']}: svg={c['gene']} csv={want}")
    order_ok = True
    for ring in {c["ring"] for c in svg_cells}:
        got = [c["gene"] for c in sorted(
            (c for c in svg_cells if c["ring"] == ring), key=lambda c: int(c["index"])
        )]
        if got != list(gene_order):
            order_ok = False
            mismatches.append(f"{ring} spoke order != gene order: {got[:6]} vs {list(gene_order)[:6]}")
    if mismatches:
        return Result("I3", "label correspondence", "FAIL", "; ".join(mismatches[:6]))
    return Result("I3", "label correspondence", "PASS",
                  f"all {len(svg_cells)} cells carry the gene their spoke index claims, "
                  f"and every ring follows the same order ({'ok' if order_ok else 'x'})")


def _i4_accounting(input_count, rendered_count, ledger_count) -> Result:
    """PLAN §6.4 — input == rendered + ledgered."""
    if input_count == rendered_count + ledger_count:
        return Result("I4", "input == rendered + ledgered", "PASS",
                      f"{input_count} = {rendered_count} rendered + {ledger_count} dropped")
    return Result("I4", "input == rendered + ledgered", "FAIL",
                  f"{input_count} input != {rendered_count} rendered + {ledger_count} ledgered "
                  f"({input_count - rendered_count - ledger_count:+d} unaccounted)")


def _i5_literature(audit: dict[str, Any] | None) -> Result:
    """PLAN §6.5 — Europe PMC echo byte-identical, no NCBI ERROR key.

    Note the correction recorded in adapters/literature.py: the echo is
    necessary but not sufficient. It is checked here; the pre-send validator is
    what actually stops the 12x-inflation case.
    """
    if not audit:
        return Result("I5", "literature query integrity", "NOT_EVALUATED",
                      "no literature calls were made in this run")
    bad_echo = audit.get("echo_mismatches") or []
    ncbi_errors = audit.get("ncbi_errors") or []
    unreachable = audit.get("unreachable") or []
    if bad_echo or ncbi_errors:
        return Result("I5", "literature query integrity", "FAIL",
                      f"echo mismatches: {bad_echo[:3]}; NCBI ERROR keys: {ncbi_errors[:3]}")
    checked = audit.get("echo_checked", 0)
    if checked == 0:
        return Result(
            "I5", "literature query integrity", "NOT_EVALUATED",
            f"no Europe PMC echo could be checked "
            f"({len(unreachable)} call(s) unreachable: {unreachable[:2]}). "
            f"NCBI ERROR-key detection ran on {audit.get('ncbi_checked', 0)} response(s).",
        )
    return Result("I5", "literature query integrity", "PASS",
                  f"{checked} Europe PMC echo(es) byte-identical; no ERROR key in "
                  f"{audit.get('ncbi_checked', 0)} NCBI response(s)")


def _i6_regression(reg: dict[str, Any] | None) -> Result:
    """PLAN §6.6 — the five-ID regression test."""
    if not reg:
        return Result("I6", "five-ID regression", "NOT_EVALUATED",
                      "the regression test was not run for this render")
    if reg.get("ok"):
        return Result("I6", "five-ID regression", "PASS", reg.get("detail", ""))
    return Result("I6", "five-ID regression", "FAIL", reg.get("detail", "failed"))


__all__ = ["check_all", "InvariantReport", "Result", "Outcome"]
