"""One p-value format, in two languages, from one cutoff.

Puck, 2026-10-19 18:03 PDT: "the small ps should just show less than the cut
off: <0.05 (or whatever is standard for this kind of analysis)".

Three claims are under test, and each is a thing a reader of the page would
notice if it broke:

1. **The cutoff is the rule.** `pvalue.P_CUTOFF` IS
   `config.RECOMMEND_MAX_Q` — the same 0.05 the GO / NO-GO "dysregulated"
   criterion tests against — and `static/app.js` mirrors that number. A page
   that printed `<0.05` while the rule read `adjusted p <= 0.01` would be
   stating a bound it does not apply.
2. **Two implementations, one output.** The server writes the p into the
   verdict sentences (`recommend._dysregulated`) and the browser writes it into
   the table and the drawer. The JS half is extracted from `app.js` and RUN
   here under node over the same case list as the Python half, so the pair
   cannot drift into printing two different strings for one number.
3. **Never an exponent, and never a reformatted stored value.** Above the
   cutoff a p is a plain decimal; `2.19538e-22` survives verbatim in the places
   it is stored and read back as data.

No network, no model call, no run on disk.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from targetring import config, recommend
from targetring.pvalue import P_CUTOFF, P_MISSING, cutoff_text, format_p

STATIC = config.REPO_ROOT / "targetring" / "static"

#: Every case both halves must agree on, as `(input, expected)`. The two the
#: spec names explicitly are the boundary pair: the largest p that still prints
#: as the inequality, and the cutoff itself.
CASES: tuple[tuple[float | None, str], ...] = (
    (2.19538e-22, "<0.05"),          # MUC1 in the demo run
    (1e-300, "<0.05"),
    (0.0001, "<0.05"),
    (0.0499999, "<0.05"),            # boundary, below
    (0.049999999999, "<0.05"),
    (0.05, "0.050"),                 # boundary, at -- two significant digits
    (0.0500001, "0.050"),
    (0.0999, "0.10"),                # rounding crosses a power of ten
    (0.1, "0.10"),
    (0.123, "0.12"),
    (0.5, "0.50"),
    (0.95, "0.95"),
    (0.999, "1"),                    # rounds up to the only pre-point digit
    (1.0, "1"),
    (0, "<0.05"),
)


# ---------------------------------------------------------------------------
# 1. The cutoff is the rule
# ---------------------------------------------------------------------------

def test_cutoff_is_the_recommend_bound():
    """One source of truth: the formatter reads the criterion's own constant."""
    assert P_CUTOFF == config.RECOMMEND_MAX_Q
    assert P_CUTOFF == recommend.current_thresholds().max_q
    assert cutoff_text() == "0.05"


def test_js_mirrors_the_python_cutoff():
    """`app.js`'s mirrored constant is the same number, to the digit.

    The spec allows JS to read the bound from `/api/criteria` or to mirror it
    "with a test asserting they are equal". It mirrors, because `fmtP` runs in
    the first synchronous render before that fetch resolves -- so this is the
    assertion that keeps the mirror honest.
    """
    js = (STATIC / "app.js").read_text()
    m = re.search(r"^const P_CUTOFF = ([0-9.eE+-]+);", js, re.M)
    assert m, "app.js no longer declares a top-level `const P_CUTOFF`"
    assert float(m.group(1)) == config.RECOMMEND_MAX_Q


def test_criteria_payload_publishes_the_same_bound():
    """The bound the page is told is the bound the format uses."""
    assert recommend.criteria_block()["thresholds"]["max_q"] == P_CUTOFF


# ---------------------------------------------------------------------------
# 2. Two implementations, one output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", CASES)
def test_format_p_python(value, expected):
    assert format_p(value) == expected


def _js_block(js: str, name: str) -> str:
    """One top-level `function name(...) {...}` lifted out of `app.js`.

    Brace-matched rather than regexed to a closing line, so a nested block
    inside the function cannot end the slice early.
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


def _js_formatter(call: str, inputs: list) -> list[str]:
    """Run `app.js`'s own p formatter over `inputs` under node.

    `call` is the expression applied to each input (`fmtP(v)` /
    `fmtPInText(v)`). The inputs arrive through the environment rather than
    argv, because `node -e` does not forward trailing arguments to `argv[2]`.
    """
    js = (STATIC / "app.js").read_text()
    consts = "\n".join(
        re.search(rf"^const {n} = .*?;$", js, re.M).group(0)
        for n in ("P_CUTOFF", "P_SIG_DIGITS", "P_MISSING", "P_IN_TEXT")
    )
    harness = (
        consts + "\n"
        + "\n".join(_js_block(js, n)
                    for n in ("pCutoffText", "pDecimal", "fmtP", "fmtPInText"))
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
def test_js_half_matches_the_python_half():
    """The real `fmtP` from `app.js`, run, over the same cases.

    Not a regex on the source: the point is that the two formatters produce
    identical STRINGS, which only running one of them can show.
    """
    got = _js_formatter("fmtP(v)", [v for v, _ in CASES])
    assert got == [e for _, e in CASES]


# ---------------------------------------------------------------------------
# 3. Never an exponent, never a reformatted stored value
# ---------------------------------------------------------------------------

def test_no_p_is_ever_scientific():
    """Across the whole printable range, nothing comes out with an exponent."""
    vs = [P_CUTOFF * 1.0000001, 0.06, 0.09999, 0.1, 0.333333, 0.6, 0.9, 0.9994]
    vs += [10 ** -e for e in range(1, 320)]
    for v in vs:
        s = format_p(v)
        assert "e" not in s.lower(), f"{v!r} printed as {s!r}"


def test_above_the_cutoff_keeps_its_leading_zero():
    for v, expected in CASES:
        s = format_p(v)
        if s.startswith("0"):
            assert s.startswith("0."), s
        assert not s.startswith("."), f"{v!r} printed as {s!r} with no leading zero"


def test_missing_prints_the_state_it_is_in():
    """The marker names the state, and is not a dash.

    It was "—" until 2026-10-21. The dashboard language standard forbids an em
    dash in reader-visible text (rule 5), and a bare dash was the wrong marker
    anyway: a missing adjusted p has to read as missing, never as a small one
    or a blank (AGENTS.md rule 1, missing is not low). It now prints the name
    `states.py` gives the state.
    """
    assert format_p(None) == P_MISSING == "no data"
    assert "—" not in P_MISSING and "–" not in P_MISSING
    assert format_p(float("nan")) == P_MISSING
    assert format_p("") == P_MISSING
    assert format_p(True) == P_MISSING
    assert format_p(None, missing="n/a") == "n/a"


def test_a_non_p_prints_as_itself():
    """Outside [0, 1] it is not a p-value, so it is not dressed up as one.

    A clean-looking wrong number is this build's worst output (AGENTS.md).
    """
    assert format_p(-1) == "-1"
    assert format_p(7) == "7"


def test_a_moved_cutoff_moves_the_format():
    assert format_p(0.004, cutoff=0.001) == "0.0040"
    assert format_p(0.0004, cutoff=0.001) == "<0.001"


# ---------------------------------------------------------------------------
# The verdict sentences
# ---------------------------------------------------------------------------

def _dysregulated(padj: float, logfc: float = 3.1) -> dict:
    t = recommend.current_thresholds()
    f = recommend.Facts(logfc=logfc, padj=padj)
    return recommend._dysregulated(f, t)


def test_verdict_strings_print_the_inequality():
    """The GO box reads "met log2 fold change +3.1, adjusted p under 0.05".

    Puck's own example was "p <0.05" (2026-10-19 18:03). The statement is
    unchanged and the number has not moved; the operator is the word it means,
    because an ASCII "p <0.05" on a tool surface is a p-value FAIL against the
    dashboard language standard adopted 2026-10-21. `recommend._p_words` is
    the one place that conversion happens.
    """
    c = _dysregulated(2.19538e-22)
    assert c["bare"] == "log2 fold change +3.1, p under 0.05"
    assert c["shown"] == "log2 fold change 3.1, adjusted p under 0.05"
    assert c["status"] == recommend.MET


def test_a_verdict_p_above_the_cutoff_is_a_decimal():
    c = _dysregulated(0.2)
    assert c["shown"] == "log2 fold change 3.1, adjusted p 0.20"
    assert c["bare"] == "log2 fold change +3.1, p 0.20"


def test_the_rule_text_is_not_reformatted():
    """The RULE stays "adjusted p <= 0.05" -- it is a bound, not a measurement.

    Two things stay as they are (Puck, 2026-10-19), and this is one of them.
    """
    c = _dysregulated(2.19538e-22)
    # v4.3, review of 2026-10-20 item 5: the bound is printed with the glyph a
    # reader reads (≤, not <=). It is still the BOUND and still not reformatted
    # into the measurement's "<0.05" -- which is what this test is about.
    assert c["threshold"] == "|log2 fold change| ≥ 1 and adjusted p ≤ 0.05"
    assert "<0.05" not in c["threshold"]
    spec = {x["key"]: x for x in recommend.criteria_spec()}
    assert spec["dysregulated"]["met_when"] == (
        "|log2 fold change| ≥ 1 and adjusted p ≤ 0.05")


def test_the_stored_number_is_untouched():
    """`value.padj` keeps the submitted float, because rules read it."""
    c = _dysregulated(2.19538e-22)
    assert c["value"]["padj"] == 2.19538e-22
    assert c["value"]["logfc"] == 3.1


def test_a_missing_p_is_still_no_data():
    c = _dysregulated(None)
    assert c["status"] == recommend.NO_DATA
    assert "no adjusted p" in c["shown"]


# ---------------------------------------------------------------------------
# The carrier string
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this box")
def test_fmt_p_in_text_rewrites_the_cell_detail_but_not_the_rule():
    """`fmtPInText` is what the column-2 tooltip is formatted by.

    `run.py` writes the cell detail as "adjusted p 2.19538e-22 (submitted)" and
    `drawer_bake._padj` reads the exact number back out of that string, so it is
    reformatted on the way to the screen and never on disk. The rule sentence
    ("adjusted p <= 0.05") must survive the same pass unchanged.
    """
    given = [
        "adjusted p 2.19538e-22 (submitted)",
        "adjusted p 0.2 (submitted)",
        "|logFC| >= 1 and adjusted p <= 0.05",
        "adjusted p is exactly 0; -log10 is undefined, not infinite",
        "log2 fold change 3.1 (submitted)",
    ]
    assert _js_formatter("fmtPInText(v)", given) == [
        "adjusted p below 0.05 (submitted)",
        "adjusted p 0.20 (submitted)",
        "|logFC| >= 1 and adjusted p <= 0.05",
        "adjusted p is exactly 0; -log10 is undefined, not infinite",
        "log2 fold change 3.1 (submitted)",
    ]


def test_run_py_still_writes_the_carrier_verbatim():
    """The on-disk detail keeps the exact float. If this ever changes,
    `drawer_bake._padj` stops being able to read the number back."""
    src = (config.REPO_ROOT / "targetring" / "run.py").read_text()
    assert 'f"adjusted p {by_gene[g][0].padj:g} (submitted)"' in src


def test_drawer_bake_can_still_read_the_carrier():
    from targetring import drawer_bake
    from targetring.states import value
    cell = value(21.6586, "adjusted p 2.19538e-22 (submitted)")
    assert drawer_bake._padj(cell) == 2.19538e-22


def test_doctests():
    import doctest

    from targetring import pvalue
    assert doctest.testmod(pvalue, raise_on_error=False).failed == 0
