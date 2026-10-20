"""One count format, in two languages, and the two columns that restate.

The review of 2026-10-20 found MUC1's Literature cell printing `4.609` under a
column head whose units read "PubTator3 records", with its own cell detail
saying `40606 PubTator3 records`. That is the same defect 554359a fixed in
column 2 — the grid printing the number the BAND is drawn on, in a column
labelled with the units of a different number — and it is fixed the same way.

Three claims:

1. **A count is grouped in threes.** `app.js`'s `fmtCount` produces the same
   string as Python's `f"{n:,}"`, which is the house idiom server-side
   (`contrast.py`, `app.py`). The JS half is extracted from `app.js` and RUN
   here under node over the same cases, so the pair cannot drift. It is
   locale-independent by construction: `toLocaleString()` would print `40.606`
   in Berlin.
2. **A restating column prints its own units.** `printed_units` is what the
   cell shows and `bound_units` is what `values.csv` holds, so a dim bound
   cannot be offered in the units of a number it is not compared against.
3. **Nothing stored is changed.** The count is READ out of the cell's own
   detail, exactly as `cellPadj` reads the p, and the ring's value, fill, scale
   and ticks are untouched.

No network, no model call, no run on disk.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from targetring import config
from targetring.figure import RING_SPECS, scale_rows
from targetring.states import value

STATIC = config.REPO_ROOT / "targetring" / "static"

#: Counts a reader of this page actually meets, plus the boundaries of the
#: grouping rule. `65217` is the Atlas contrast's gene total (the flow page),
#: `40606` is MUC1's PubTator3 records, `1000` and `999` are the rule's edge.
CASES = [0, 1, 9, 99, 100, 999, 1000, 1001, 12345, 40606, 65217,
         999999, 1000000, 1234567, -40606]


def _js_block(js: str, name: str) -> str:
    """One top-level `function name(...) {...}` lifted out of `app.js`.

    Brace-matched rather than regexed to a closing line, so a nested block
    inside the function cannot end the slice early. Same helper as
    `tests/test_pvalue.py`, kept local so neither test file imports the other.
    """
    start = js.index(f"\nfunction {name}(") + 1
    depth, i = 0, js.index("{", start)
    while True:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
        i += 1


def _js_counts(call: str, inputs: list) -> list[str]:
    """Run `app.js`'s own count formatter over `inputs` under node."""
    js = (STATIC / "app.js").read_text()
    consts = re.search(r"^const COUNT_IN_TEXT = .*?;$", js, re.M).group(0)
    harness = (
        consts + "\n"
        + "\n".join(_js_block(js, n) for n in ("fmtCount", "fmtCountInText"))
        + "\nconsole.log(JSON.stringify(JSON.parse(process.env.TR_CASES)"
          f".map((v) => {call})));\n"
    )
    out = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=60,
        env={**os.environ, "TR_CASES": json.dumps(inputs)},
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this box")
def test_js_grouping_matches_pythons():
    """`fmtCount` and `f"{n:,}"`, over the same list, to the character."""
    assert _js_counts("fmtCount(v)", CASES) == [f"{n:,}" for n in CASES]


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this box")
def test_a_missing_count_is_a_dash_and_never_a_zero():
    """AGENTS.md rule 1 at the formatter: missing is not low. A gene with no
    PubTator3 count must not print `0` records."""
    assert _js_counts("fmtCount(v)", [None, "", True, "abc"]) == ["—"] * 4


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this box")
def test_the_cell_detail_is_regrouped_on_the_way_to_the_screen():
    """The adapter's own string, reformatted for display and not on disk."""
    detail = ("40606 PubTator3 records for '@GENE_MUC1' (log10(1+n) = 4.61); "
              "gene id-resolved, region is free text")
    out = _js_counts("fmtCountInText(v)", [detail])[0]
    assert out.startswith("40,606 PubTator3 records for '@GENE_MUC1'")
    # The log in the same sentence is a measurement, not a count, and keeps its
    # own format.
    assert "log10(1+n) = 4.61" in out
    # Three digits are not grouped, and a year is not a count.
    assert _js_counts("fmtCountInText(v)", ["999 records"])[0] == "999 records"


def test_node_is_here():
    """The two tests above are skipped without node, so the skip is visible
    rather than silent: this one fails on a box that cannot check the pair."""
    assert shutil.which("node"), "no node — the JS half of fmtCount is unchecked"


class TestRestatingColumns:
    """The flag, and the two units each restating ring needs."""

    def test_both_restating_rings_are_declared(self) -> None:
        keys = sorted(s.key for s in RING_SPECS if s.table_restates)
        assert keys == ["literature", "neg_log10_padj"], keys

    def test_each_restating_column_states_the_units_it_prints(self) -> None:
        """Written out rather than derived: the whole claim is that the printed
        and the stored units DIFFER, and a derived expectation would agree with
        whatever the code happened to do.

        head, the unit of the printed number, the unit of the stored number.
        """
        expected = {
            "neg_log10_padj": ("adjusted p (yours)", "adjusted p",
                               "-log10 adjusted p"),
            "literature": ("Literature records", "PubTator3 records",
                           "log10(1 + PubTator3 records)"),
        }
        for spec in RING_SPECS:
            if not spec.table_restates:
                continue
            head, printed, bound = expected[spec.key]
            assert spec.column_label == head, spec.column_label
            assert spec.printed_units == printed, spec.printed_units
            assert spec.bound_units == bound, spec.bound_units
            # The head names the restatement, never the transform the band is
            # drawn on: a column of counts headed "log10" names the colour
            # scale over the numbers.
            assert "log10" not in spec.column_label.lower(), spec.key
            # The two units are the point.
            assert spec.printed_units != spec.bound_units, spec.key
            # The stored unit is the one a dim bound goes on in, and it names
            # the transform.
            assert "log10" in spec.bound_units, spec.key

    def test_every_other_ring_has_one_unit_for_one_number(self) -> None:
        """A ring that prints what it stores must not have acquired a second
        unit by accident — that is how a hover and a dropdown come to disagree
        about what a reader is looking at."""
        for spec in RING_SPECS:
            if spec.table_restates:
                continue
            assert spec.printed_units == spec.units == spec.bound_units, spec.key

    def test_the_literature_ring_still_draws_the_log(self) -> None:
        """The colour scale, the ticks and the stated transform are what
        554359a left alone in column 2, and this leaves them alone in column
        11: only the printed number changed."""
        lit = next(s for s in RING_SPECS if s.key == "literature")
        assert lit.scale == "prelog1p"
        assert lit.units == "PubTator3 records"
        assert "log10(1 + records)" in lit.transform
        assert lit.label == "Literature volume"
        # The ring's full name is still the name on the figure and in the
        # citation strip, so the restated head is not a second name for it.
        assert lit.bound_label == lit.label

    def test_the_scales_payload_carries_both_units(self) -> None:
        """The circle's cell hover is the same sentence the grid's is, so the
        legend route publishes the pair too."""
        genes = ["MUC1", "EGFR", "GAPDH"]
        cells = {"logfc": {g: value(float(i) - 1.0) for i, g in enumerate(genes)},
                 "neg_log10_padj": {g: value(20.0 + i) for i, g in enumerate(genes)},
                 "literature": {g: value(2.0 + i) for i, g in enumerate(genes)}}
        rows = {r["key"]: r for r in scale_rows(cells)}
        assert rows["literature"]["printed_units"] == "PubTator3 records"
        assert rows["literature"]["bound_units"] == "log10(1 + PubTator3 records)"
        assert rows["neg_log10_padj"]["printed_units"] == "adjusted p"
        assert rows["neg_log10_padj"]["bound_units"] == "-log10 adjusted p"
        # And every other row has the one unit it always had.
        for key, row in rows.items():
            if key in ("literature", "neg_log10_padj"):
                continue
            assert row["printed_units"] == row["units"] == row["bound_units"], key


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this box")
def test_the_app_js_parses():
    """`node --check` over the real file, and over the exported copy.

    v4.3. `${/* … */""}` is this file's own comment idiom and it is only valid
    INSIDE a template literal; written one line above the backtick it is a
    `SyntaxError` that stops `app.js` dead. It happened twice in one afternoon,
    and both times the page came up with ten behaviour FAILs and a clean "no
    console errors" above them — an uncaught exception is not a console
    message. `scripts/ui_checks.py` now listens for `pageerror` too; this is
    the cheap half, and it runs without a browser.
    """
    for path in (STATIC / "app.js",
                 config.REPO_ROOT / "docs" / "demo" / "app.js"):
        if not path.is_file():
            continue
        out = subprocess.run(["node", "--check", str(path)],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"{path.name}: {out.stderr[:400]}"
