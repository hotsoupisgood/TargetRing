"""The seven invariants of PLAN.md §6, checked against the rendered artefact.

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
_WHITESPACE = re.compile(r"\s+")


def _normalise_ws(s: str) -> str:
    """Matplotlib's word-wrap splits one long sentence into several `<text>`
    elements, one per visual line (verified empirically: no `<tspan>` is used).
    Joining them with a space and collapsing whitespace reconstructs the
    original sentence for a substring check regardless of where the wrap fell
    -- the same problem I7 does not have only because its notes are short
    enough to survive on one line."""
    return _WHITESPACE.sub(" ", s).strip()


def svg_text_normalised(svg_path: Path) -> str:
    return _normalise_ws(" ".join(svg_text(svg_path)))


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

    def summary_line(self) -> str:
        """v3.0. One line, for the EXPORT to stamp on itself.

        AGENTS.md: "a morning screenshot that cannot be audited is not a
        deliverable." A FAIL was always shouted in red across the figure and a
        PASS said nothing at all, so on a downloaded PNG a clean run and a run
        nobody checked were the same picture. The person the export exists for
        has no Invariants tab and no manifest open beside them.

        Stated per outcome rather than as "all good": NOT_EVALUATED is not a
        pass (UX A1), and a line that rounded it to one would be the exact
        conflation the two-channel design exists to prevent.
        """
        n = len(self.results)
        if not n:
            return ""
        passed = sum(1 for r in self.results if r.outcome == "PASS")
        bits = [f"INVARIANTS {passed}/{n} PASS"]
        if self.failures:
            bits.append(f"{len(self.failures)} FAILED "
                        f"({', '.join(r.id for r in self.failures)}) — "
                        f"do not trust the numbers on this figure")
        if self.unchecked:
            bits.append(f"{len(self.unchecked)} NOT CHECKED "
                        f"({', '.join(r.id for r in self.unchecked)}) — not "
                        f"a failure and not a pass; see manifest.json")
        if not self.failures and not self.unchecked:
            bits.append("every check this build can make, made and held; "
                        "per-check detail in manifest.json")
        return " · ".join(bits)

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
    figure_claims: dict[str, Any] | None = None,
    conditions: Sequence[tuple[str, str]] = (),
    # Stage 5: an Atlas-sourced run carries a `ContrastRef` and a
    # `contrast.Accounting`; both are `None` on a pasted-table run. I8/I9 are
    # appended ONLY when a contrast is present -- a pasted-table run does not
    # gain two new NOT_EVALUATED rows for a feature it never touched, and the
    # existing seven-invariant manifest shape for every paste-path run and test
    # is unchanged.
    contrast: Any = None,
    selection_accounting: Any = None,
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
    rep.results.append(
        _i7_figure_states_what_it_did(figure_claims, svg_path, csv_rows))
    if contrast is not None:
        rep.results.append(_i8_contrast_identity(contrast, svg_path))
        rep.results.append(
            _i9_three_stage_accounting(selection_accounting, svg_path))
    return rep


def svg_text(svg_path: Path) -> list[str]:
    """Every text run the figure drew, in document order."""
    root = ET.parse(svg_path).getroot()
    return ["".join(el.itertext()) for el in root.iter(f"{SVG_NS}text")]


def headline_markers(svg_path: Path) -> list[str]:
    """Text the figure draws carrying the retired headline diamond.

    v2.5 keeps this readback and inverts what it proves. It used to name the
    genes the Pareto front marked, so I7 could check the drawn diamonds against
    the ranking; nothing is ranked now, so the only correct answer is none, and
    a diamond surviving anywhere in the artefact means a ranking marker outlived
    the ranking.
    """
    return [t.strip() for t in svg_text(svg_path) if "◆" in t]


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


def _spokes_follow_the_stated_order(claims: dict[str, Any],
                                    csv_rows: Sequence[dict[str, Any]]) -> str:
    """Re-derive the spoke order from the drawn values and compare.

    The data comes out of the CSV of what was actually drawn, the way I1 and I3
    take theirs, so this survives a regression in the ordering code rather than
    asking that code whether it did its job.
    """
    from .order import ALPHABETICAL, SUBMITTED, is_ring_ordering, order_for_figure
    from .states import Cell

    ordering = claims.get("ordering") or SUBMITTED
    submitted = list(claims.get("submitted_order") or ())
    drawn: dict[int, str] = {}
    cells: dict[str, dict[str, Cell]] = {}
    for r in csv_rows:
        drawn.setdefault(int(r["spoke_index"]), r["gene"])
        ring = cells.setdefault(r["ring"], {})
        ring[r["gene"]] = Cell(r["state"],
                               float(r["value"]) if r["value"] != "" else None, "")
    got = [drawn[i] for i in sorted(drawn)]
    if not got:
        return ""

    if ordering == SUBMITTED:
        if not submitted:
            return ""
        want = [g for g in submitted if g in set(got)]
    elif ordering == ALPHABETICAL:
        want = sorted(got)
    elif is_ring_ordering(ordering):
        # The submitted order is the documented tie-break, so it has to be the
        # input here too or a legitimate tie reads as a mis-ordering.
        base = [g for g in submitted if g in set(got)] or got
        want = order_for_figure(base, cells, ordering)
    else:
        return ""
    if want != got:
        return (f"the figure says its spokes are in {ordering!r} order and they are not: "
                f"drawn {got[:6]}, expected {want[:6]} (re-derived from values.csv)")
    return ""


def _i7_figure_states_what_it_did(
    claims: dict[str, Any] | None, svg_path: Path | None,
    csv_rows: Sequence[dict[str, Any]] = (),
) -> Result:
    """COUNCIL V5, and v2.5 item 2 — the figure states what it actually did.

    **What this invariant used to be, and why it is not simply deleted.** I7 was
    "headline eligibility": every gene in the Pareto front had a real value on
    all the Pareto axes (COUNCIL V2), and the diamonds drawn on the figure were
    exactly that front, checked in the artefact so a rendering bug could not put
    a headline marker on a gene the ranking called ineligible. Its first half was
    a ranking check and went with ranking. Its second half was not: it was a
    figure-honesty check -- *the drawn artefact must not assert something the run
    does not support* -- and that claim outlives the feature it was written for.

    So I7 keeps its id and its place in the PASS/FAIL list, and now asserts three
    things about the rendered SVG:

    1. **No headline marker survives the ranking.** Nothing is ranked, so a ◆
       anywhere in the artefact is a claim with nothing behind it.
    2. **The spoke order is stated on the figure**, the sentence printed is the
       ordering the run used, and **the spokes are actually in that order** --
       re-derived from the CSV of drawn values, so a figure that says
       "alphabetical" and draws something else fails here rather than being
       believed. An order that carries meaning and is not stated is the same
       defect as an unlabelled colour scale; an order that is stated wrongly is
       worse.
    3. **Every active threshold filter is named on the figure.** A filtered plot
       that does not say so reads as the whole submitted set -- a plausible wrong
       figure, which AGENTS.md calls the worst possible output.
    """
    if not claims:
        return Result("I7", "the figure states what it did", "NOT_EVALUATED",
                      "no figure claims were supplied for this render",
                      affects="the ordering rule and any active filters were not "
                              "re-checked against the drawn artefact")
    if svg_path is None:
        return Result("I7", "the figure states what it did", "NOT_EVALUATED",
                      "no SVG to read the claims back out of")
    try:
        drawn = svg_text(svg_path)
    except Exception as e:  # noqa: BLE001
        return Result("I7", "the figure states what it did", "NOT_EVALUATED",
                      f"SVG unreadable: {e}")
    body = "\n".join(drawn)

    diamonds = [t.strip() for t in drawn if "◆" in t]
    if diamonds:
        return Result(
            "I7", "the figure states what it did", "FAIL",
            f"the figure draws the retired headline marker: {diamonds[:3]}. Nothing is "
            f"ranked in v2.5, so a ◆ marks membership of a set that is not computed.")

    ordering = claims.get("ordering_note") or ""
    if not ordering:
        return Result("I7", "the figure states what it did", "FAIL",
                      "this run states no spoke-ordering rule; the order of the circle "
                      "carries meaning and must be printed on it")
    if ordering not in body:
        return Result(
            "I7", "the figure states what it did", "FAIL",
            f"the run ordered the spokes by {ordering!r} and the figure does not say so "
            f"anywhere in its own text.")

    wrong = _spokes_follow_the_stated_order(claims, csv_rows)
    if wrong:
        return Result("I7", "the figure states what it did", "FAIL", wrong)

    missing = [f for f in (claims.get("filter_notes") or []) if f not in body]
    if missing:
        return Result(
            "I7", "the figure states what it did", "FAIL",
            f"{len(missing)} active filter(s) removed genes from this plot and are not "
            f"named on it: {missing}. A reader would take a filtered figure for the whole "
            f"submitted set.")

    n_filters = len(claims.get("filter_notes") or [])
    n_filtered = claims.get("n_filtered", 0)
    n_marked = claims.get("n_kept_unevaluated", 0)
    return Result(
        "I7", "the figure states what it did", "PASS",
        f"no headline marker survives the ranking; the spoke order is printed on the "
        f"figure ({ordering}) and the spokes follow it; "
        + (f"{n_filters} active filter(s) named on it, {n_filtered} gene(s) filtered into "
           f"the ledger and {n_marked} kept and marked for having no data on a filtered ring"
           if n_filters else "no threshold filter was active"))


def _i8_contrast_identity(ref: Any, svg_path: Path | None) -> Result:
    """Stage 3-5's I8 — contrast identity, read back from the rendered SVG.

    `ContrastRef.__post_init__` already refuses to exist unless
    `contrast_id == f'{ref}_{test}'` (PLAN §1 item 7), so this is not re-checking
    the sign; it is checking that the figure a reader is holding actually names
    the contrast it was drawn from -- the accession, the contrast id, the XML's
    own prose name byte-for-byte, and the sign sentence naming the test label as
    'up'. A figure that draws the right numbers under the wrong name is exactly
    as wrong as one that draws the wrong numbers.
    """
    if ref is None:
        return Result("I8", "contrast identity", "NOT_EVALUATED",
                      "no contrast was used in this render")
    if svg_path is None:
        return Result("I8", "contrast identity", "NOT_EVALUATED",
                      "no SVG to read the contrast claims back out of")
    try:
        body = svg_text_normalised(svg_path)
    except Exception as e:  # noqa: BLE001
        return Result("I8", "contrast identity", "NOT_EVALUATED",
                      f"SVG unreadable: {e}")
    missing = [
        label for label, needle in (
            ("accession", ref.accession),
            ("contrast id", ref.contrast_id),
            ("prose name", _normalise_ws(ref.name)),
            ("sign sentence", _normalise_ws(ref.sign_sentence)),
        )
        if needle not in body
    ]
    if missing:
        return Result(
            "I8", "contrast identity", "FAIL",
            f"the rendered figure does not print its {', '.join(missing)} "
            f"anywhere in its own text; a reader cannot verify which contrast "
            f"(or which direction) this figure came from")
    return Result("I8", "contrast identity", "PASS",
                  f"{ref.accession} {ref.contrast_id} — prose name and sign "
                  f"sentence both printed on the figure and match the "
                  f"`ContrastRef` this run used")


def _i9_three_stage_accounting(accounting: Any, svg_path: Path | None) -> Result:
    """Stage 3-5's I9 — three counts, each a subset of the one before it.

    `contrast.Accounting.__post_init__` already asserts stages A-C
    algebraically at construction (a broken accounting object cannot exist),
    so what is left to check here is the same thing I1-I3 check for cell
    counts: that the number the run computed is the number the artefact
    actually prints, re-derived from the rendered SVG rather than trusted to
    the object that built it.
    """
    if accounting is None:
        return Result("I9", "three-stage accounting", "NOT_EVALUATED",
                      "no selection accounting was produced for this render")
    try:
        if accounting.n_total != accounting.n_with_statistic + accounting.n_without_statistic:
            return Result("I9", "three-stage accounting", "FAIL",
                          f"stage A broken: {accounting.n_total} != "
                          f"{accounting.n_with_statistic} + "
                          f"{accounting.n_without_statistic}")
        if accounting.n_top > accounting.n_with_statistic:
            return Result("I9", "three-stage accounting", "FAIL",
                          f"stage B broken: top {accounting.n_top} exceeds "
                          f"{accounting.n_with_statistic} with a statistic")
        if accounting.n_selected > accounting.n_top:
            return Result("I9", "three-stage accounting", "FAIL",
                          f"stage C broken: {accounting.n_selected} selected "
                          f"exceeds the {accounting.n_top} shown")
    except AttributeError as e:
        return Result("I9", "three-stage accounting", "NOT_EVALUATED",
                      f"accounting object missing a field: {e}")
    if svg_path is not None:
        try:
            body = svg_text_normalised(svg_path)
        except Exception as e:  # noqa: BLE001
            return Result("I9", "three-stage accounting", "NOT_EVALUATED",
                          f"SVG unreadable: {e}")
        # Two legitimate phrasings, not one: the heatmap draws
        # `accounting_line()` ("... selected yet") because nothing has been
        # selected at render time, and the circos draws `selection_line()`
        # ("... selected") because it exists only once something has been.
        # Either one printed on the artefact is the three-stage count stated.
        candidates = [accounting.accounting_line()]
        if hasattr(accounting, "selection_line"):
            candidates.append(accounting.selection_line())
        line = _normalise_ws(candidates[0])
        if not any(_normalise_ws(c) in body for c in candidates):
            return Result(
                "I9", "three-stage accounting", "FAIL",
                f"the figure does not print its own accounting line "
                f"({line!r}); a reader cannot see the three-stage count this "
                f"run computed")
    return Result("I9", "three-stage accounting", "PASS",
                  f"{accounting.n_total} = {accounting.n_with_statistic} with "
                  f"a statistic + {accounting.n_without_statistic} without; "
                  f"top {accounting.n_top} <= with-statistic; "
                  f"{accounting.n_selected} selected <= top")


def _i10_heatmap_states_its_recipe(svg_path: Path | None, ref: Any,
                                   accounting: Any, n_samples: int) -> Result:
    """Stage 4's I10 — the seven mandatory strings, re-derived from the same
    builder functions `heatmap.render()` draws from and checked against the
    rendered SVG. An SVG missing any one of them fails here rather than being
    trusted to have drawn what the code intended."""
    from . import heatmap as heatmap_mod

    if svg_path is None or ref is None or accounting is None:
        return Result("I10", "heatmap states its recipe", "NOT_EVALUATED",
                      "no heatmap was rendered for this run")
    try:
        body = svg_text_normalised(svg_path)
    except Exception as e:  # noqa: BLE001
        return Result("I10", "heatmap states its recipe", "NOT_EVALUATED",
                      f"SVG unreadable: {e}")

    expected = {
        "1. title (prose name · accession · contrast id)":
            heatmap_mod.title_line(ref),
        "2. reference column-group label":
            heatmap_mod.group_label(ref.ref_label, ref.n_ref),
        "2. test column-group label":
            heatmap_mod.group_label(ref.test_label, ref.n_test),
        "3. z-score / block-structure sentence":
            heatmap_mod.zscore_sentence(n_samples, accounting.n_top),
        "4. raw-scale panel sentence":
            heatmap_mod.raw_scale_sentence(),
        "6. colour-direction sentence":
            heatmap_mod.colour_direction_sentence(),
        "7. three-stage accounting line":
            accounting.accounting_line(),
    }
    missing = [label for label, s in expected.items()
              if _normalise_ws(s) not in body]
    if missing:
        return Result(
            "I10", "heatmap states its recipe", "FAIL",
            f"{len(missing)} of the 7 mandatory string(s) are missing from the "
            f"rendered heatmap: {missing}. Per AGENTS.md, a heatmap missing "
            f"any of these is not shipped.")
    return Result("I10", "heatmap states its recipe", "PASS",
                  "all 7 mandatory strings (title, both column-group labels, "
                  "the z-score/block-structure sentence, the raw-scale "
                  "sentence, the colour-direction sentence, the three-stage "
                  "accounting line) are printed on the rendered heatmap")


__all__ = ["check_all", "InvariantReport", "Result", "Outcome", "svg_text",
           "svg_text_normalised", "headline_markers",
           "check_contrast_identity", "check_three_stage_accounting",
           "check_heatmap_states_its_recipe", "check_phenotype_claims_citable"]


def check_contrast_identity(ref: Any, svg_path: Path | None) -> Result:
    """Public entry point for I8, called from `run.py` (against the circos
    SVG) and from `tests/test_heatmap.py` (against the heatmap SVG)."""
    return _i8_contrast_identity(ref, svg_path)


def check_three_stage_accounting(accounting: Any,
                                 svg_path: Path | None = None) -> Result:
    return _i9_three_stage_accounting(accounting, svg_path)


def check_heatmap_states_its_recipe(svg_path: Path | None, ref: Any,
                                    accounting: Any, n_samples: int) -> Result:
    return _i10_heatmap_states_its_recipe(svg_path, ref, accounting, n_samples)


def _i11_phenotype_claims_citable(claims: Sequence[dict[str, Any]],
                                  rows: Sequence[dict[str, Any]]) -> Result:
    """Stage 7's I11 — every claim in a phenotype-page LLM summary must cite
    real rows and quote them verbatim, or the whole summary is refused.

    PLAN §3 stage 7b, "mechanical, not a vibe check":
      * zero claims -> refused (a summary that says nothing is not a summary);
      * every `row_id` in every claim must exist among the rows actually sent
        to the model;
      * every `quote` must be a case-sensitive substring of the concatenated
        `title + sentence + mp_label` of AT LEAST ONE row it cites — the
        model is allowed to pick which cited row the quote came from, it is
        not allowed to quote a sentence none of them contain;
      * ANY single failing claim refuses the ENTIRE summary. A partially
        validated paragraph is itself a plausible wrong figure — there is no
        partial-credit rendering.

    This does not read an SVG. The phenotype page is HTML in the drawer, not a
    figure (plan §9), and `rows`/`claims` are the exact JSON the model was
    given and returned — re-deriving anything from a rendered artefact here
    would be checking a different object from the one that was actually
    validated before the page was ever shown.
    """
    if not claims:
        return Result("I11", "phenotype summary claims are citable", "FAIL",
                      "the model returned zero claims; a summary with nothing "
                      "in it is refused rather than rendered as empty")
    by_id = {int(r["row_id"]): r for r in rows}
    for i, claim in enumerate(claims):
        row_ids = claim.get("row_ids") or []
        if not row_ids:
            return Result(
                "I11", "phenotype summary claims are citable", "FAIL",
                f"claim {i} ({claim.get('text', '')!r}) cites no rows at all")
        missing = [rid for rid in row_ids if rid not in by_id]
        if missing:
            return Result(
                "I11", "phenotype summary claims are citable", "FAIL",
                f"claim {i} cites row_id(s) {missing} which do not exist among "
                f"the {len(rows)} row(s) sent to the model; refusing the whole "
                f"summary rather than rendering a partially-validated one")
        quote = str(claim.get("quote") or "")
        if not quote:
            return Result(
                "I11", "phenotype summary claims are citable", "FAIL",
                f"claim {i} carries no quote to verify against its cited rows")
        cited_text = [
            f"{by_id[rid].get('title') or ''} {by_id[rid].get('sentence') or ''} "
            f"{by_id[rid].get('mp_label') or ''}"
            for rid in row_ids
        ]
        if not any(quote in text for text in cited_text):
            return Result(
                "I11", "phenotype summary claims are citable", "FAIL",
                f"claim {i}'s quote {quote!r} is not a substring of the "
                f"title/sentence/mp_label of any row it cites ({row_ids}); the "
                f"model paraphrased instead of quoting verbatim, which this "
                f"check cannot tell apart from a fabricated claim, so both are "
                f"refused the same way")
    return Result("I11", "phenotype summary claims are citable", "PASS",
                  f"{len(claims)} claim(s), every row_id exists among the "
                  f"{len(rows)} row(s) sent to the model, every quote is a "
                  f"verbatim substring of a row it cites")


def check_phenotype_claims_citable(claims: Sequence[dict[str, Any]],
                                   rows: Sequence[dict[str, Any]]) -> Result:
    """Public entry point for I11, called from `phenotype.py` on every
    phenotype-page summary before it is ever shown, and from
    `tests/test_phenotype.py` / `tests/test_llm.py` directly."""
    return _i11_phenotype_claims_citable(claims, rows)
