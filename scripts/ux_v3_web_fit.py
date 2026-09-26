"""UX v3 round 2 A4 — where the app figure's vertical budget actually goes.

A4 said "shorten the `web` canvas until the figure fits one 1440 x 900 viewport,
and let the existing geometry assertions arbitrate the margins". The arbitration
came back empty: `logs/v32-web-sweep.txt` swept 83 (height, bottom, axes-height)
triples and every one of them failed something — the comparability banner within
4 px of the top spoke label when the circle was large enough for
`fit_wedge_labels` to place twelve ring names in the 26 degree gap, and the
caveat line inside the bottom spoke labels when it was not.

So this script does not sweep margins. It prints the BUDGET: how many points of
the canvas height each block actually takes, measured off a drawn figure, so the
thing that has to give can be chosen rather than guessed. Usage:

    .venv/bin/python scripts/ux_v3_web_fit.py [--wedge N] [--h N] [--bot N]
                                              [--axh N] [--banner-pt N]

Everything is reported in canvas points (1 pt = 1.5046 CSS px at the 1040 px
panel the app gives the figure) so the numbers add up against the 598 pt that a
900 px viewport allows.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from targetring import figure as F  # noqa: E402
from targetring.order import gene_stats  # noqa: E402
from targetring.states import value  # noqa: E402

PANEL_PX = 1040.0


def full_spec(genes):
    out = {}
    for s in F.RING_SPECS:
        if s.categorical:
            levels = sorted(s.categorical)
            out[s.key] = {g: value(levels[i % len(levels)])
                          for i, g in enumerate(genes)}
        elif s.domain:
            lo, hi = s.domain
            out[s.key] = {g: value(lo + (hi - lo) * (i % 5) / 4.0)
                          for i, g in enumerate(genes)}
        else:
            out[s.key] = {g: value(1.0 + i * 0.3) for i, g in enumerate(genes)}
    return out


def one(tmp: Path, n: int, filters: bool, lay: F.Layout):
    genes = [f"GENE{i:03d}" for i in range(n)]
    spec = full_spec(genes)
    F.LAYOUTS["web"] = lay
    kw = {}
    if filters:
        kw["filter_notes"] = ["logfc >= 1.0 (kept genes with no value)"]
    try:
        F.render(genes=genes, cells_by_ring=spec, stats=gene_stats(spec),
                 region="Lung", out_svg=tmp / f"web-{n}-{int(filters)}.svg",
                 input_count=n + 1, rendered_count=n, layout="web",
                 ordering_note="spoke order: the order of your submitted DE table",
                 provenance="run V32-COLD · 9305baf1", ledger_counts={"ambiguous": 1},
                 dropped_names=["HBB"], **kw)
        return "ok"
    except AssertionError as e:
        return str(e).split(": ", 1)[-1][:150]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wedge", type=float, default=None)
    p.add_argument("--h", type=float, default=None)
    p.add_argument("--bot", type=float, default=None)
    p.add_argument("--axh", type=float, default=None)
    p.add_argument("--banner-pt", type=float, default=None)
    p.add_argument("--sweep", action="store_true",
                   help="grid over (axes height, bottom margin) at a fixed "
                        "canvas height, reporting the worst case over the four "
                        "(gene count, filters) corners rather than one of them")
    a = p.parse_args()

    lay = F.WEB
    if a.h is not None:
        lay = dataclasses.replace(lay, figsize=(lay.figsize[0], a.h))
    box = list(lay.axes_box)
    if a.bot is not None:
        box[1] = a.bot
    if a.axh is not None:
        box[3] = a.axh
    lay = dataclasses.replace(lay, axes_box=tuple(box))
    if a.wedge is not None:
        lay = dataclasses.replace(lay, wedge_deg=a.wedge)
    if a.banner_pt is not None:
        F.WEB_BANNER_PT = a.banner_pt

    h_in = lay.figsize[1]
    scale = PANEL_PX / (lay.figsize[0] * 72.0)
    print(f"canvas {lay.figsize[0]:g} x {h_in:g} in · axes_box {tuple(lay.axes_box)} "
          f"· wedge {lay.wedge_deg:g}° · scale {scale:.4f} "
          f"· figure {h_in * 72 * scale:.0f} px (900 px allows h <= "
          f"{900 / (72 * scale):.2f} in)")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        if not a.sweep:
            for n in (3, 14, 50):
                for filters in (False, True):
                    print(f"  n={n:<3} filters={int(filters)}  "
                          f"{one(tmp, n, filters, lay)}")
            return 0
        print(f"{'axh':>6} {'bot':>6} {'top':>6} {'circle':>7}  worst of "
              f"n=3/14/50 x filters")
        for axh in (0.62, 0.645, 0.67, 0.695, 0.72):
            for bot in (0.16, 0.185, 0.21, 0.235):
                if bot + axh > 0.90:
                    continue
                cand = dataclasses.replace(
                    lay, axes_box=(lay.axes_box[0], bot, lay.axes_box[2], axh))
                worst, circle = "ok", axh * h_in * 72 * scale
                for n in (3, 14, 50):
                    for filters in (False, True):
                        r = one(tmp, n, filters, cand)
                        if r != "ok":
                            worst = f"n={n} f={int(filters)}: {r[:96]}"
                            break
                    if worst != "ok":
                        break
                print(f"{axh:6.3f} {bot:6.3f} {1 - bot - axh:6.3f} "
                      f"{min(circle, lay.axes_box[2] * lay.figsize[0] * 72 * scale):7.0f}"
                      f"  {worst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
