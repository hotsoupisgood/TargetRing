"""One way to print an adjusted p-value, and nothing else.

Puck, 2026-10-19 18:03 PDT: "the small ps should just show less than the cut
off: <0.05 (or whatever is standard for this kind of analysis)". 0.05 is the
standard FDR-adjusted cutoff for differential expression, and it is already
this build's own rule: `config.RECOMMEND_MAX_Q` is the `adjusted p <= 0.05`
half of the GO / NO-GO "dysregulated" criterion. So the cutoff is not a second
choice made for the formatter -- it is read from there, and moving the rule
moves the format with it.

`2.19538e-22` is the exact submitted number and it stays exact everywhere it is
STORED -- the `neg_log10_padj` cell's own detail carries it verbatim
(`run.py`), `drawer_bake._padj` and `summary_llm.build_facts` read it back out
of there, and the criterion keeps it in `value.padj`. What this changes is only
what a reader sees: across a Crohn's contrast every gene is "e-15 or smaller"
and the exponents differ by an amount nobody acts on, so the exponent is noise
in the place Puck actually reads the number. Below the cutoff the honest print
is the inequality, because clearing the cutoff is the whole of what the number
is being used to say.

So: DISPLAY only. No rule is ever evaluated against this string
(`recommend._dysregulated` compares the float `f.padj` to `t.max_q`), nothing
sorts on it (the grid and the gene table sort on the number), and it never
replaces a stored value.

At or above the cutoff the real decimal is printed, never scientific notation:
two significant digits, with the leading zero kept. `0.05 -> "0.050"`,
`0.123 -> "0.12"`, `0.5 -> "0.50"`. Significant digits rather than a fixed
number of decimals because a fixed count prints either `0.05` as `0.1` or `0.5`
as `0.5000`; two significant digits give both the same two digits of
information. The leading zero stays -- Puck wrote the cutoff as `<0.05`, not
`<.05`, and that is the one difference from the APA style this module printed
before 18:03.

A value outside [0, 1] is not a p-value. `de_input` rejects those on the way in
(`dropped_no_padj`), so one cannot reach here; if one ever does it is printed as
itself rather than as `<0.05`, because a clean-looking wrong number is this
build's worst output (AGENTS.md:17).
"""

from __future__ import annotations

import math
from typing import Any

from . import config

#: Below this, print the inequality rather than a decimal. ONE source of truth:
#: the same constant the "dysregulated" criterion tests against, so the screen
#: cannot print `<0.05` while the rule reads `0.01`. `static/app.js` mirrors it
#: as `P_CUTOFF` and `tests/test_pvalue.py` asserts the two are equal.
P_CUTOFF = config.RECOMMEND_MAX_Q

#: Significant digits at or above the cutoff.
P_SIG_DIGITS = 2

#: What a p that is not there prints as. The same em dash the drawer already
#: uses for an absent value, so a missing p does not look like a small one.
P_MISSING = "—"


def cutoff_text(cutoff: float = P_CUTOFF) -> str:
    """The cutoff as it appears inside `<0.05`.

    `:g` rather than `_decimal`: this is the bound as the rule writes it
    ("adjusted p <= 0.05"), not a measurement, so it keeps the rule's own
    digits instead of being padded out to two significant ones.
    """
    return f"{cutoff:g}"


def _decimal(v: float, sig: int = P_SIG_DIGITS) -> str:
    """`v` to `sig` significant digits as a plain decimal, never an exponent."""
    exp = math.floor(math.log10(v))
    s = f"{v:.{max(0, sig - 1 - exp)}f}"
    # Rounding can cross a power of ten (0.0999 -> "0.100", which shows three
    # digits for two digits of information). Re-measure the rounded number and
    # print it again at its own magnitude.
    rounded = float(s)
    if rounded > 0:
        exp2 = math.floor(math.log10(rounded))
        if exp2 != exp:
            s = f"{rounded:.{max(0, sig - 1 - exp2)}f}"
    return s


def format_p(p: Any, missing: str = P_MISSING, cutoff: float = P_CUTOFF) -> str:
    """An adjusted p as it is printed on screen.

    >>> format_p(2.19538e-22)
    '<0.05'
    >>> format_p(0.0499999)
    '<0.05'
    >>> format_p(0.05)
    '0.050'
    >>> format_p(0.123)
    '0.12'
    >>> format_p(0.5)
    '0.50'
    >>> format_p(1)
    '1'
    >>> format_p(None)
    '—'
    """
    if p is None or isinstance(p, bool):
        return missing
    try:
        v = float(p)
    except (TypeError, ValueError):
        return missing
    if v != v:                                  # NaN is not a p-value either
        return missing
    if v < 0.0 or v > 1.0:
        return f"{v:g}"
    if v >= 1.0:
        return "1"
    if v < cutoff:
        return "<" + cutoff_text(cutoff)
    s = _decimal(v)
    # 0.999 rounds to "1.0" at two significant digits, which would be the only
    # output with a non-zero digit before the point. It is a 1.
    return "1" if s.startswith("1") else s


__all__ = ["P_CUTOFF", "P_SIG_DIGITS", "P_MISSING", "cutoff_text", "format_p"]
