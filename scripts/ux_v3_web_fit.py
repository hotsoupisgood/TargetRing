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

v3.5 adds `--windows`, which is the measurement for the other half of the same
question: A4 pinned this canvas to the smallest supported window, which pinned
the ARRANGEMENT to it, and the twelve ring names have been off since. That mode
sweeps window sizes through `figure.web_layout_for_viewport`, renders each one
with the names in the gap, and reports the window and the circle at which
`assert_wedge_labels_fit` and `assert_wedge_names_clear_the_floor` stop refusing
them -- plus `--bisect`, which finds that circle to the pixel instead of to the
step of a grid. Nothing here relaxes either assertion: it asks them.
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

PANEL_PX = F.WEB_REFERENCE_PANEL_PX

# The app's own arithmetic from a window to the panel `#figure` gets, used by
# `--windows` only: the live app MEASURES this and hands it to `/api/analyse`, so
# nothing in the product depends on these three numbers. They are here so a sweep
# can be quoted in window sizes, which is the unit Puck's ask is in ("when the
# viewport allows"), and they are anchored on the one live measurement this review
# already has: 1440 px window, 1040 px panel (`logs/ux-v3-r1fix-live.json`).
PAGE_PADDING_PX = 52.0                  # `main` 26 px either side
DECODE_PANEL_PX = 330.0 + 18.0          # `#decode` and the flex gap


def panel_for_window(window_w: float) -> float:
    return window_w - PAGE_PADDING_PX - DECODE_PANEL_PX


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
                 input_count=n + 1, rendered_count=n, layout=lay,
                 ordering_note="spoke order: the order of your submitted DE table",
                 provenance="run V32-COLD · 9305baf1", ledger_counts={"ambiguous": 1},
                 dropped_names=["HBB"], **kw)
        return "ok"
    except AssertionError as e:
        F.plt.close("all")
        return str(e).split(": ", 1)[-1][:150]


# --- v3.5: the window, the canvas, and whether the names are earned -----------

WORST_CORNERS = [(n, f) for n in (3, 14, 50) for f in (False, True)]


def names_verdict(tmp: Path, lay: F.Layout, corners=WORST_CORNERS) -> str:
    """"ok" if every corner of this canvas draws the ring names, else the first
    refusal. The corners are the ones round 2 swept: 3, 14 and 50 genes, with and
    without a filter, because the centre block and the FILTERED clause are both
    obstacles the names have to clear."""
    for n, filters in corners:
        r = one(tmp, n, filters, lay)
        if r != "ok":
            return f"n={n} f={int(filters)}: {r}"
    return "ok"


def describe(lay: F.Layout, window: tuple[float, float] | None = None) -> str:
    """Every number as the browser will draw it, which means at the real panel
    width rather than at the nominal one: below a 1440 px window the canvas is
    clamped to `WEB`'s and scaled DOWN, so its strings are smaller than 12.04 px
    there -- as they have been since round 2, and worth printing rather than
    assuming away."""
    w_in, h_in = lay.figsize
    panel = panel_for_window(window[0]) if window else F.WEB_REFERENCE_PANEL_PX
    scale = F.web_css_scale(lay, panel)
    fig_px = h_in * 72.0 * scale
    out = (f"canvas {w_in:5.2f} x {h_in:5.2f} in  circle "
           f"{F.web_circle_px(lay, panel):4.0f} px  figure {fig_px:4.0f} px  "
           f"type {lay.label_size * scale:5.2f} px  "
           f"names {lay.wedge_label_size * scale:5.2f} px")
    if window:
        out = (f"window {window[0]:.0f} x {window[1]:.0f}  panel {panel:4.0f} px  "
               + out + f"  fits {'yes' if fig_px <= window[1] else 'NO'}")
    return out


def windows_sweep(tmp: Path, windows) -> None:
    print("Does the window earn the twelve ring names? `web_layout_for_viewport` "
          "sizes the canvas; the render's own assertions answer.\n")
    for vw, vh in windows:
        lay = F.web_layout_for_viewport(panel_for_window(vw), vh, name_the_rings=True)
        print(f"{describe(lay, (vw, vh))}\n    names: {names_verdict(tmp, lay)}")


def bisect_on_height(tmp: Path, window_w: float, lo: float, hi: float,
                     tol: float = 1.0) -> None:
    """The window height at which the names turn on, at a fixed window width.

    Height, because it is the side a reader can actually give the figure (the
    width is spent on the decode panel and the page margins) and because it is the
    side round 2 took away. Reported in both currencies: the window, and the
    circle diameter in CSS px, which is the number `assert_wedge_labels_fit` is
    really answering about.
    """
    panel = panel_for_window(window_w)
    def ok(vh: float) -> bool:
        lay = F.web_layout_for_viewport(panel, vh, name_the_rings=True)
        return names_verdict(tmp, lay) == "ok"

    if ok(lo):
        print(f"the names already fit at {window_w:.0f} x {lo:.0f}")
        return
    if not ok(hi):
        print(f"the names do not fit at {window_w:.0f} x {hi:.0f} either")
        return
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    off = F.web_layout_for_viewport(panel, lo, name_the_rings=True)
    on = F.web_layout_for_viewport(panel, hi, name_the_rings=True)
    print(f"\nat a {window_w:.0f} px window the names turn on between "
          f"{lo:.0f} and {hi:.0f} px of window height:")
    print(f"  off  {describe(off, (window_w, lo))}")
    print(f"  on   {describe(on, (window_w, hi))}")
    print(f"  the threshold is a circle of {F.web_circle_px(on, panel):.0f} CSS px "
          f"({on.axes_box[3] * on.figsize[1]:.2f} in of axes box); "
          f"{F.web_circle_px(off, panel):.0f} px is refused")


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
    # v3.5.
    p.add_argument("--windows", action="store_true",
                   help="sweep window sizes through `web_layout_for_viewport` and "
                        "report whether each one earns the twelve ring names")
    p.add_argument("--bisect", nargs="?", const="1440", default=None,
                   help="window WIDTH at which to find the window height, to the "
                        "pixel, where the ring names turn on")
    a = p.parse_args()

    if a.windows:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            windows_sweep(Path(d), [
                (1280, 800), (1440, 900), (1440, 1200), (1512, 982),
                (1680, 1050), (1680, 1250), (1792, 1120), (1920, 1080),
                (1920, 1200), (2056, 1329), (2560, 1440), (3008, 1692),
            ])
        return 0
    if a.bisect is not None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bisect_on_height(Path(d), float(a.bisect), 900.0, 2400.0)
        return 0

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
