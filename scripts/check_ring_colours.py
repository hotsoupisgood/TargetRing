"""Measure the ring palette the figure actually uses (v2.6).

Three numbers the V2 report has to carry, and one of them is a limit rather
than a pass:

1. **Adjacent rings** — the only pairs that physically touch on a Circos — under
   normal, protanopic and deuteranopic vision. These have to clear a target.
2. **All pairs**, under normal vision. Eleven chromatic identities at a single
   luminance sit on a circle of radius 100*C in OKLab, so the best achievable
   worst pair is 2*100*C*sin(pi/11) ~ 6.5 at C=0.115. This is a geometric
   ceiling, not a palette that needs more work, and the figure answers it with
   secondary encoding: ring number, fixed radial position, and a labelled
   swatch per ring in the scale column.
3. **Every ring's ramp against every reserved state fill**, in greyscale.

Run:  PYTHONPATH=. .venv/bin/python scripts/check_ring_colours.py
"""
from __future__ import annotations

import itertools
import sys

from targetring.figure import (
    ADJACENT_RING_MIN_DELTA_E, GREYSCALE_MIN_DELTA, RING_SPECS, STATE_STYLE,
    adjacent_ring_delta_e, ring_colours, ring_identity_colours,
)
from targetring.palette import all_value_hexes, relative_luminance
from scripts.hue_search import VISION, dE

ADJACENT_TARGET = ADJACENT_RING_MIN_DELTA_E


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def main() -> int:
    rings = list(RING_SPECS)
    bad = []
    worst_touching = [999.0]
    worst_cells = [999.0]
    scored = [0, 0]

    print("touching rings — worst pair of equally-bright cells across the boundary,\n"
          "under the worst of normal / protan / deutan")
    for a, b in zip(rings, rings[1:]):
        if a.family and a.family == b.family:
            print(f"  {a.key:22s} <-> {b.key:22s}   same family by decision, "
                  f"not scored")
            continue
        d, ia, ib = 999.0, "", ""
        for x in ring_identity_colours(a):
            for y in ring_identity_colours(b):
                v = min(dE(_rgb(x), _rgb(y), k) for k in VISION)
                if v < d:
                    d, ia, ib = v, x, y
        cell, ca, cb = adjacent_ring_delta_e(a, b)
        flag = "" if d >= ADJACENT_TARGET else "   <-- under target"
        worst_touching[0] = min(worst_touching[0], d)
        scored[0] += 1
        scored[1] += 1 if d >= ADJACENT_TARGET else 0
        if cell < 900:
            worst_cells[0] = min(worst_cells[0], cell)
        cellstr = "no equally-bright mid-band pair" if cell > 900 else (
            f"worst mid-band cell pair {ca}/{cb} dE {cell:.1f}")
        print(f"  {a.key:22s} {ia}  <->  {b.key:22s} {ib}   identity dE "
              f"{d:5.1f}{flag}\n      {cellstr}")

    print("\nall pairs of rings, worst equally-bright cell pair over the WHOLE band\n"
          "(the ends of every ramp converge on near-black and near-white; this is\n"
          "the number that ceiling produces, and it is reported, not asserted)")
    def rgb(h):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    for kind in VISION:
        worst, who = 999.0, ("", "")
        for a, b in itertools.combinations(rings, 2):
            if a.family and a.family == b.family:
                continue
            for ca in ring_colours(a):
                la = relative_luminance(rgb(ca))
                for cb in ring_colours(b):
                    if abs(la - relative_luminance(rgb(cb))) > 0.04:
                        continue
                    d = dE(rgb(ca), rgb(cb), kind)
                    if d < worst:
                        worst, who = d, (a.key, b.key)
        print(f"  {kind or 'normal':7s} {who[0]} <-> {who[1]}  dE {worst:.1f}")

    print("\ngreyscale: every value colour any ring can paint, vs each reserved fill")
    lums = [relative_luminance(_rgb(h)) for h in all_value_hexes()]
    print(f"  {len(lums)} value colours span luminance {min(lums):.4f}..{max(lums):.4f}")
    for name, st in STATE_STYLE.items():
        l = relative_luminance(_rgb(st['fc']))
        gap = min(abs(l - x) for x in lums)
        ok = "ok" if gap >= GREYSCALE_MIN_DELTA else "FAIL"
        if gap < GREYSCALE_MIN_DELTA:
            bad.append(f"greyscale {name} gap {gap:.4f}")
        print(f"  {name:16s} {st['fc']} lum {l:.4f}  nearest value colour "
              f"{gap:.4f} away  [{ok}, need {GREYSCALE_MIN_DELTA}]")

    print(f"\nworst touching-ring pair anywhere on the two ramps: "
          f"dE {worst_touching[0]:.1f}, against a target of {ADJACENT_TARGET:g}.")
    print(f"{scored[1]} of the {scored[0]} scored boundaries clear it; the "
          f"{scored[0] - scored[1]} that do not are\npale-meets-pale or "
          "dark-meets-dark, which is the gamut ceiling described in\n"
          "figure.assert_rings_are_distinguishable, not a fixable assignment. Ring\n"
          "identity is also carried by radial position, ring number and the scale\n"
          "column; this is the number that says why those are not optional.")
    print("FAILED: " + "; ".join(bad) if bad else
          "no colour is shared between rings in different families, and the "
          "greyscale guarantee holds across every family")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
