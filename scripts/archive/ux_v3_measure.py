"""UX v3 review instrument: measure type size at the size a reader actually sees.

A font size in a file is not a finding. What a reader can read is a function of
the file's type size, the canvas it sits on, and the width that canvas is fitted
to -- a laptop panel or a slide. This script does that arithmetic once, for every
artefact and every size that matters, so the three rounds are measured the same
way rather than re-argued.

The floors it compares against, and why each one:

* `SCREEN_FLOOR_PX = 12.0` CSS px. The app's own stylesheet does not go below
  12.5 px for a hint and 11.5 px for a table header, so 12 px is this build's own
  smallest-thing-a-human-reads. Below ~9 px glyph strokes land on one device
  pixel at 1x and most readers stop reading rather than lean in.
* `SLIDE_FLOOR_PT = 10.0` pt at final rendered size, on the half-slide test
  UX-v1-mvp.md A4 used. 10 pt is the bottom of what survives a projector at the
  back of a room; it is not a comfortable size and it is deliberately not
  generous, so a pass here is a floor rather than a target.

Usage: ux_v3_measure.py <run_dir> [--json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCREEN_FLOOR_PX = 12.0
SLIDE_FLOOR_PT = 10.0

# A 16:9 slide is 13.333 x 7.5 in. "Half a slide" is the test in UX A4.
SLIDE_W_IN = 13.333
HALF_SLIDE_W_IN = SLIDE_W_IN / 2

# The laptop case: a 1440 px viewport, the app's 26 px page padding either side
# and its 1500 px max-width, leaves the result column this wide in CSS px.
LAPTOP_PANEL_PX = 1388.0

# v3.1 / round 1's fix. `figure-web.svg` no longer gets the whole column: A2's
# fix puts the twelve scales and the four cell states beside it as HTML, in a
# 330 px `#decode` panel with an 18 px gap, so the figure itself is 1040 px.
#
# The constant is here rather than folded into `LAPTOP_PANEL_PX` because the two
# are different measurements and only one of them moved: the export opened full
# width still gets the whole 1388. Measured in the live DOM at 1440 x 900
# (`logs/ux-v3-r1fix-live.json`: `#figure` is 1040.0 px, scale 1.5046) rather
# than derived from the stylesheet -- a flex row with `min-width: 0` does not
# give you its width by arithmetic, and reading 1388 here instead of 1040
# reports the app 33% more legible than it is, which is the one direction this
# instrument must never be wrong in.
DECODE_PANEL_PX = 330.0
FIGURE_ROW_GAP_PX = 18.0
WEB_FIGURE_PX = LAPTOP_PANEL_PX - DECODE_PANEL_PX - FIGURE_ROW_GAP_PX

# v3.5. CSS px per canvas inch on the app render, which is the constant the
# responsive canvas is built on: the canvas grows with the window at a fixed
# 1.5046 px per point, so a `figure-web.svg` states the panel it was drawn for in
# its own viewBox. Imported rather than copied — 1040 / 9.6 in two files is two
# numbers that can disagree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from targetring.figure import WEB_PX_PER_IN  # noqa: E402

FS = re.compile(r'font-size:\s*([0-9.]+)px')
VIEWBOX = re.compile(r'viewBox="([0-9.\- ]+)"')
# The size is on the <text> element itself, not on a wrapping <g>: matplotlib
# writes one inline style per run. Anchoring on <text ...>...</text> is therefore
# what pairs a size with the string a reader is being asked to read.
TEXT = re.compile(r'<text ([^>]*)>(.*?)</text>', re.S)
STRIP = re.compile(r'<[^>]+>')


def svg_type_sizes(path: Path) -> tuple[float, float, dict[float, dict]]:
    """Return (viewBox width, viewBox height, {size_pt: {n, samples}}).

    matplotlib writes SVG user units as points, so a `font-size: 8px` in the
    file is 8 pt of the canvas -- which is why the numbers below are labelled pt
    and not px.
    """
    src = path.read_text(errors="replace")
    vb = VIEWBOX.search(src)
    w, h = (float(x) for x in vb.group(1).split()[2:4])
    sizes: dict[float, dict] = {}
    for attrs, body in TEXT.findall(src):
        fs = FS.search(attrs)
        if not fs:
            continue
        pt = round(float(fs.group(1)), 2)
        e = sizes.setdefault(pt, {"n": 0, "samples": []})
        e["n"] += 1
        txt = STRIP.sub("", body).strip()
        if txt and len(e["samples"]) < 3:
            e["samples"].append(txt[:52])
    return w, h, sizes


def report(run: Path) -> dict:
    out: dict = {"run": run.name, "floors": {"screen_px": SCREEN_FLOOR_PX,
                                             "slide_pt": SLIDE_FLOOR_PT},
                 "artefacts": {}}
    cases = [
        # file, how it is read, the width it is fitted to, the floor
        ("figure-web.svg", "app, 1440 px laptop viewport", WEB_FIGURE_PX, "screen"),
        ("figure.svg", "the export, opened full width on the same laptop", LAPTOP_PANEL_PX, "screen"),
        ("figure-slide.svg", "half a slide", HALF_SLIDE_W_IN * 72, "slide"),
        ("figure-slide.svg", "a full slide", SLIDE_W_IN * 72, "slide"),
        ("legend.svg", "half a slide", HALF_SLIDE_W_IN * 72, "slide"),
        ("legend.svg", "a full slide", SLIDE_W_IN * 72, "slide"),
    ]
    for name, how, target_units, floor_kind in cases:
        p = run / name
        if not p.exists():
            continue
        w, h, sizes = svg_type_sizes(p)
        if name == "figure-web.svg":
            # v3.5. The app canvas is a function of the window the run was drawn
            # for, so 1040 px is the panel of ONE of them. Read the panel back off
            # the file instead: `web_layout_for_viewport` sizes the canvas at a
            # fixed 108.33 CSS px per inch, so the canvas width IS the panel it
            # was drawn for. A fixed 1040 here would report a render drawn for a
            # 2160 px panel at 0.72 of its real size and call every string on it
            # under the floor -- an instrument wrong in the one direction this
            # file says it must never be wrong in.
            target_units = w / 72.0 * WEB_PX_PER_IN
        scale = target_units / w
        floor = SCREEN_FLOOR_PX if floor_kind == "screen" else SLIDE_FLOOR_PT
        rows = []
        for pt in sorted(sizes):
            seen = round(pt * scale, 2)
            rows.append({"in_file_pt": pt, "as_seen": seen, "n": sizes[pt]["n"],
                         "passes_floor": seen >= floor,
                         "samples": sizes[pt]["samples"]})
        n_under = sum(r["n"] for r in rows if not r["passes_floor"])
        out["artefacts"][f"{name} @ {how}"] = {
            "canvas_units": [w, h], "scale": round(scale, 4),
            "floor": floor, "floor_kind": floor_kind,
            "n_text_runs": sum(r["n"] for r in rows),
            "n_under_floor": n_under,
            "smallest_as_seen": rows[0]["as_seen"] if rows else None,
            "rows": rows,
        }
    return out


def main() -> int:
    run = Path(sys.argv[1])
    r = report(run)
    if "--json" in sys.argv:
        print(json.dumps(r, indent=1))
        return 0
    print(f"run {r['run']}   floors: screen {SCREEN_FLOOR_PX} px · slide {SLIDE_FLOOR_PT} pt\n")
    for k, v in r["artefacts"].items():
        print(f"== {k}")
        print(f"   canvas {v['canvas_units'][0]:.0f}x{v['canvas_units'][1]:.0f} units"
              f" · scale {v['scale']}"
              f" · {v['n_under_floor']}/{v['n_text_runs']} text runs under the floor")
        for row in v["rows"]:
            flag = "ok " if row["passes_floor"] else "UNDER"
            print(f"   {flag} {row['in_file_pt']:>6.2f} pt in file"
                  f" -> {row['as_seen']:>6.2f}  x{row['n']:<4}"
                  f" {' | '.join(row['samples'])[:78]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
