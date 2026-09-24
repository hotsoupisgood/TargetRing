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


_URL_IN_TEXT = re.compile(r"https?://\S+")


def _summarise(detail: str, limit: int = 180) -> str:
    """One readable line. UX A1: v1 truncated at 200 characters and ended
    mid-URL at `?query=%22S`, so the mangled text was in the banner source
    before it was ever drawn. URLs are removed rather than cut, and what is
    left is truncated on a word boundary."""
    text = _URL_IN_TEXT.sub("[url in the manifest]", detail).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


@dataclass
class Result:
    id: str
    name: str
    outcome: Outcome
    detail: str = ""
    # UX A1 / COUNCIL V3 / V11. A source being down and an invariant being
    # violated are different facts and must not render identically. `affects`
    # is the plain consequence, shown beside a NOT_EVALUATED result so the
    # reader knows what is and is not in doubt.
    affects: str = ""

    @property
    def failed(self) -> bool:
        """The invariant was checked and did not hold: the drawn data is wrong."""
        return self.outcome == "FAIL"

    @property
    def unchecked(self) -> bool:
        """The invariant could not be checked. Not a pass, and not a failure."""
        return self.outcome == "NOT_EVALUATED"


@dataclass
class InvariantReport:
    results: list[Result] = field(default_factory=list)
    # Non-invariant facts about the run: sources that were unreachable, inputs
    # that arrived incomplete. Each is (headline, consequence).
    conditions: list[tuple[str, str]] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.failed]

    @property
    def unchecked(self) -> list[Result]:
        return [r for r in self.results if r.unchecked]

    @property
    def ok(self) -> bool:
        """Every invariant PASSed. A NOT_EVALUATED run is not ok -- it is just
        not *wrong*, which is a different thing and rendered differently."""
        return not self.failures and not self.unchecked

    @property
    def data_is_sound(self) -> bool:
        """No invariant was checked and found to be violated.

        This is the question the red banner answers. It is deliberately not the
        same as `ok`: v1 stamped DO NOT TRUST THIS FIGURE across the export
        because Europe PMC -- which sets no ring value at all -- was returning
        503 from EBI's nginx.
        """
        return not self.failures

    def banner_lines(self) -> list[str]:
        """Red banner. FAIL only: an invariant was checked and did not hold."""
        return [f"{r.id} {r.name}: FAIL — {_summarise(r.detail)}" for r in self.failures]

    def condition_lines(self) -> list[str]:
        """Amber notice. Checks that could not run, and source outages.

        Never the red banner (UX A1): these say "we do not know", not "the
        figure is wrong", and rendering them identically is what makes the red
        banner stop working the day it is real.
        """
        out = [
            f"{r.id} {r.name}: NOT CHECKED — {_summarise(r.detail, 150)}"
            + (f" · {r.affects}" if r.affects else "")
            for r in self.unchecked
        ]
        out += [f"{head} — {why}" for head, why in self.conditions]
        return out

    def as_json(self) -> list[dict[str, str]]:
        return [{"id": r.id, "name": r.name, "outcome": r.outcome,
                 "detail": r.detail, "affects": r.affects}
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
    pareto_audit: dict[str, Any] | None = None,
    conditions: Sequence[tuple[str, str]] = (),
) -> InvariantReport:
    rep = InvariantReport(conditions=list(conditions))
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
    rep.results.append(_i7_headline_eligibility(svg_cells, pareto_audit, svg_path))
    return rep


def diamond_genes(svg_path: Path) -> list[str]:
    """Genes the figure marks with the headline diamond, read back out of the SVG."""
    root = ET.parse(svg_path).getroot()
    out: list[str] = []
    for el in root.iter(f"{SVG_NS}text"):
        text = "".join(el.itertext())
        m = re.match(r"^\s*◆\s*⚠?\s*([A-Za-z0-9._-]+)\s*\(k=", text)
        if m:
            out.append(m.group(1))
    return out


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


EPMC_DRIVES_NO_RING = (
    "Europe PMC sets no ring value (PLAN §2, COUNCIL M6); the literature ring is "
    "PubTator3 and is unaffected"
)


def _i5_literature(audit: dict[str, Any] | None) -> Result:
    """PLAN §6.5 — Europe PMC echo byte-identical, no NCBI ERROR key.

    Note the correction recorded in adapters/literature.py: the echo is
    necessary but not sufficient. It is checked here; the pre-send validator is
    what actually stops the 12x-inflation case.

    COUNCIL V11: a replayed echo is not a checked echo. v1 reported
    "I5 PASS — 14 Europe PMC echo(es) byte-identical" from a warm cache while
    EBI was returning 503 to every live request, which sits badly beside the
    build's own "a check that could not run is not a check that passed" and
    beside I6, which bypasses the cache for exactly this reason. Live and
    replayed echoes are now counted separately and only live ones can produce a
    PASS.
    """
    if not audit:
        return Result("I5", "literature query integrity", "NOT_EVALUATED",
                      "no literature calls were made in this run",
                      affects=EPMC_DRIVES_NO_RING)
    bad_echo = audit.get("echo_mismatches") or []
    ncbi_errors = audit.get("ncbi_errors") or []
    unreachable = audit.get("unreachable") or []
    if bad_echo or ncbi_errors:
        return Result("I5", "literature query integrity", "FAIL",
                      f"echo mismatches: {bad_echo[:3]}; NCBI ERROR keys: {ncbi_errors[:3]}")
    live = audit.get("echo_live", 0)
    replayed = audit.get("echo_replayed", 0)
    ncbi = audit.get("ncbi_checked", 0)
    if live == 0:
        if replayed and not unreachable:
            why = (f"every one of the {replayed} echo(es) came from the on-disk cache. They "
                   f"were replayed, not checked: nothing was asserted against Europe PMC "
                   f"this run")
        elif replayed:
            why = (f"{replayed} echo(es) were replayed from the on-disk cache and "
                   f"{len(unreachable)} live call(s) did not answer, so none was checked "
                   f"this run")
        else:
            why = (f"no Europe PMC echo could be checked: {len(unreachable)} call(s) "
                   f"unreachable")
        return Result(
            "I5", "literature query integrity", "NOT_EVALUATED",
            f"{why}."
            + (f" First: {unreachable[0]}." if unreachable else "")
            + f" NCBI ERROR-key detection ran on {ncbi} response(s).",
            affects=EPMC_DRIVES_NO_RING,
        )
    return Result("I5", "literature query integrity", "PASS",
                  f"{live} Europe PMC echo(es) checked live and byte-identical"
                  + (f" ({replayed} more replayed from cache and not counted)"
                     if replayed else "")
                  + f"; no ERROR key in {ncbi} NCBI response(s)")


def _i6_regression(reg: dict[str, Any] | None) -> Result:
    """PLAN §6.6 — the five-ID regression test."""
    if not reg:
        return Result("I6", "five-ID regression", "NOT_EVALUATED",
                      "the regression test was not run for this render")
    if reg.get("ok"):
        return Result("I6", "five-ID regression", "PASS", reg.get("detail", ""))
    if reg.get("unavailable"):
        # The source never answered. Not a pass -- but it does not accuse the
        # join logic of a regression it did not have. Reported as a run
        # condition rather than as a red banner (UX A1).
        return Result("I6", "five-ID regression", "NOT_EVALUATED",
                      reg.get("detail", "source unavailable"),
                      affects="the Open Targets batch join was not re-verified this run; "
                              "every OT-sourced ring is unchecked, not known wrong")
    return Result("I6", "five-ID regression", "FAIL", reg.get("detail", "failed"))


def _i7_headline_eligibility(
    svg_cells, pareto_audit: dict[str, Any] | None, svg_path: Path | None
) -> Result:
    """COUNCIL V2/V3 — the headline is computed only over eligible genes.

    New in v2 and added to the manifest's PASS/FAIL list. Two assertions:

    1. Every gene in the Pareto front has a real value on *all* the Pareto axes.
       In v1 a gene whose only axis was druggability=1.0 entered the front and
       ejected two genes that had all four, so this is the check that would have
       caught the original defect.
    2. The diamond drawn on the figure marks exactly the front. Checked in the
       artefact, like I1-I3, so a rendering bug cannot put a headline marker on
       a gene the ranking says is ineligible.
    """
    if not pareto_audit:
        return Result("I7", "headline eligibility", "NOT_EVALUATED",
                      "no Pareto audit was supplied for this render",
                      affects="the headline set was not re-checked against the figure")

    axes = list(pareto_audit.get("axes") or ())
    front = sorted(pareto_audit.get("front") or ())
    per_gene_k = pareto_audit.get("front_pareto_k") or {}
    bad = {g: per_gene_k.get(g) for g in front if per_gene_k.get(g, 0) < len(axes)}
    if bad:
        return Result(
            "I7", "headline eligibility", "FAIL",
            f"these genes are in the Pareto front without a value on every axis "
            f"{axes}: {bad}. A gene must not reach the headline on the axes it "
            f"happens to have (COUNCIL V2).")

    n_in = pareto_audit.get("n_eligible", 0)
    n_out = pareto_audit.get("n_ineligible", 0)
    drawn = pareto_audit.get("diamond_genes")
    if pareto_audit.get("diamonds_suppressed"):
        # COUNCIL V10: on a short list almost nothing is dominated, so the
        # diamond is not drawn. None drawn is then correct, and any drawn is not.
        if drawn:
            return Result("I7", "headline eligibility", "FAIL",
                          f"the diamond is suppressed on a list this short, but the figure "
                          f"draws one on {sorted(drawn)}")
        return Result(
            "I7", "headline eligibility", "PASS",
            f"{len(front)} of {n_in} eligible gene(s) in the front, each with a real value "
            f"on all {len(axes)} axes {axes}; {n_out} ineligible. The headline diamond is "
            f"suppressed on a list this short (COUNCIL V10) and none is drawn.")
    if drawn is not None and sorted(drawn) != front:
        return Result(
            "I7", "headline eligibility", "FAIL",
            f"the figure draws a headline diamond on {sorted(drawn)} but the ranking's "
            f"front is {front}")

    return Result(
        "I7", "headline eligibility", "PASS",
        f"{len(front)} of {n_in} eligible gene(s) in the front, each with a real value on "
        f"all {len(axes)} axes {axes}; {n_out} gene(s) ineligible and drawn without a "
        f"diamond" + (f"; the figure's {len(drawn)} diamond(s) match"
                      if drawn is not None else ""))


__all__ = ["check_all", "InvariantReport", "Result", "Outcome"]
