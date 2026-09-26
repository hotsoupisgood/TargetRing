"""UX v3 review instrument: does any text in an artefact overlap any other text?

Puck asked specifically about overlap in the legend now that it is its own
image. The build already has `assert_legend_panels_do_not_overlap`, but that
checks exactly one thing -- text against the cell-state *box* -- because that is
the collision v3.0 hit. It does not compare a text block with another text
block, so two scale rows growing into each other, or the caveat wall growing
down into a column, would not be caught.

This measures the artefact that was written rather than the model that wrote it:
it reads the SVG, recovers each run's position from its `transform`, and
computes its advance width with the same DejaVu metrics matplotlib laid it out
with. Two runs overlap when their boxes intersect by more than `SLOP` points in
both axes -- the slop is there because a tick label sitting on the edge of its
own colour bar is a design, not a defect, and touching by a fraction of a point
is not what a reader sees.

Usage: ux_v3_overlap.py <svg> [<svg> ...]
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

from matplotlib.font_manager import FontProperties, findfont
from matplotlib.ft2font import FT2Font, LOAD_NO_HINTING

SLOP = 1.0  # points, in both axes, before an intersection counts

TEXT = re.compile(r'<text ([^>]*)>(.*?)</text>', re.S)
FS = re.compile(r'font-size:\s*([0-9.]+)px')
WEIGHT = re.compile(r'font-weight:\s*(\d+|bold)')
XLATE = re.compile(r'translate\(([-0-9.]+)[ ,]+([-0-9.]+)\)')
ROTATE = re.compile(r'rotate\(([-0-9.]+)')
STRIP = re.compile(r'<[^>]+>')
ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#8722;": "-"}

_FONTS: dict[str, FT2Font] = {}


def _font(weight: str) -> FT2Font:
    if weight not in _FONTS:
        p = findfont(FontProperties(family="DejaVu Sans", weight=weight))
        _FONTS[weight] = FT2Font(p)
    return _FONTS[weight]


def advance(s: str, size: float, weight: str) -> float:
    """Width of `s` at `size` points, in points."""
    f = _font(weight)
    f.set_size(size, 72)
    try:
        f.set_text(s, 0.0, flags=LOAD_NO_HINTING)
        return f.get_width_height()[0] / 64.0
    except Exception:
        return 0.55 * size * len(s)


SVG_NS = "{http://www.w3.org/2000/svg}"


def runs(path: Path) -> list[dict]:
    """Every drawn string, with the box it occupies in canvas points.

    matplotlib puts the `transform` on the `<text>` for some runs and on an
    enclosing `<g>` for others -- a tick label is typically a translated group
    around a bare text. Reading only the element's own attribute finds about
    half of them and silently reports the rest as absent rather than as
    non-overlapping, so the transform is accumulated down the tree.
    """
    import xml.etree.ElementTree as ET

    src = path.read_text(errors="replace")
    vb = re.search(r'viewBox="([0-9.\- ]+)"', src)
    _, _, W, H = (float(x) for x in vb.group(1).split())
    root = ET.fromstring(src)
    out: list[dict] = []

    def walk(node, dx: float, dy: float, rot: float, style: str):
        tr = node.get("transform", "")
        m = XLATE.search(tr)
        if m:
            dx, dy = dx + float(m.group(1)), dy + float(m.group(2))
        r = ROTATE.search(tr)
        if r:
            rot = rot + float(r.group(1))
        style = (style + ";" + (node.get("style") or "")).strip(";")
        if node.tag == SVG_NS + "text":
            fs = FS.search(style)
            if fs:
                size = float(fs.group(1))
                txt = "".join(node.itertext()).strip()
                for k, v in ENT.items():
                    txt = txt.replace(k, v)
                if txt:
                    # matplotlib writes the anchor either as a transform on the
                    # element or its group, or as x/y attributes with a
                    # `rotate(a x y)` beside them. Both forms appear in the same
                    # file -- reading only the transform puts a third of the
                    # runs at the origin, where they all "overlap" each other
                    # and the real collisions are buried.
                    px = dx + float(node.get("x", 0.0))
                    py = dy + float(node.get("y", 0.0))
                    w = advance(txt, size, "bold" if WEIGHT.search(style) else "normal")
                    asc, desc = 0.78 * size, 0.22 * size
                    anchor = "start"
                    if "text-anchor: middle" in style:
                        anchor = "middle"
                    elif "text-anchor: end" in style:
                        anchor = "end"
                    x0 = px - (w / 2 if anchor == "middle" else w if anchor == "end" else 0)
                    corners = [(x0 - px, -asc), (x0 + w - px, -asc),
                               (x0 + w - px, desc), (x0 - px, desc)]
                    a = math.radians(rot)
                    ca, sa = math.cos(a), math.sin(a)
                    xs = [px + cx * ca - cy * sa for cx, cy in corners]
                    ys = [py + cx * sa + cy * ca for cx, cy in corners]
                    box = (min(xs), min(ys), max(xs), max(ys))
                    out.append({"text": txt, "size": size, "rot": rot,
                                "box": box, "W": W, "H": H})
        for child in node:
            walk(child, dx, dy, rot, style)

    walk(root, 0.0, 0.0, 0.0, "")
    return out


def overlaps(rs: list[dict]) -> list[tuple]:
    """Intersecting pairs, exact ones first.

    A pair is EXACT when neither run is rotated: the box is then the ink and an
    intersection is a collision a reader sees. A pair involving a rotated run is
    AABB-ONLY -- a spoke label set at 37 degrees has an axis-aligned box far
    larger than its ink, so the test over-reports and the pair needs an eye or
    `assert_no_text_over_the_rings`, which does the real geometry. Saying which
    kind a hit is costs one field and stops the tool being quietly wrong about
    the circle while being exactly right about the legend, which has no rotated
    text on it at all.
    """
    hits = []
    for i in range(len(rs)):
        for j in range(i + 1, len(rs)):
            a, b = rs[i]["box"], rs[j]["box"]
            dx = min(a[2], b[2]) - max(a[0], b[0])
            dy = min(a[3], b[3]) - max(a[1], b[1])
            if dx > SLOP and dy > SLOP:
                rot = abs(rs[i]["rot"]) > 1 or abs(rs[j]["rot"]) > 1
                hits.append((round(dx, 1), round(dy, 1), rs[i], rs[j],
                             "aabb-only" if rot else "EXACT"))
    hits.sort(key=lambda h: (h[4] != "EXACT", -(h[0] * h[1])))
    return hits


def main() -> int:
    bad = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        rs = runs(p)
        hits = overlaps(rs)
        exact = [h for h in hits if h[4] == "EXACT"]
        bad += len(exact)
        print(f"== {p.name}: {len(rs)} text runs, {len(exact)} EXACT overlapping "
              f"pairs, {len(hits) - len(exact)} aabb-only (> {SLOP} pt both axes)")
        for dx, dy, a, b, kind in hits[:10]:
            print(f"   [{kind}] {dx}x{dy} pt  {a['size']}pt {a['text'][:40]!r}")
            print(f"                  vs  {b['size']}pt {b['text'][:40]!r}")
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
