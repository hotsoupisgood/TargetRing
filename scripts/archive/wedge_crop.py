"""The 12 o'clock gap, cropped out of a real run, at 1:1 (v2.7).

A ring name sitting at the radius of the band it names is the whole argument for
the wedge over a numbered key beside the figure, and it is the one claim in the
report a reader can check by eye. This crops the gap out of an audit render so
they can, and it does it at 50 genes, which is the case where the spokes are
thinnest and the gap is most expensive.

The crop is located from the *cells*' own SVG ids -- the outermost ring's first
and last spoke -- rather than from pixel coordinates, so it follows the figure
if the canvas or the axes box moves again.

Run: PYTHONPATH=. .venv/bin/python scripts/wedge_crop.py \\
         runs/COLD50 docs/screenshots/10-ring-names-in-the-wedge.png
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

NS = "{http://www.w3.org/2000/svg}"


def _outer_ring_extent(svg: Path) -> tuple[float, float, float, float]:
    """Bounding box, in SVG user units, of every cell on the outermost ring.

    The outermost ring is the first `cell|` id in document order; its cells ring
    the gap on both sides, so their union bounds the circle's top and gives the
    y the gap's labels live above.
    """
    root = ET.parse(svg).getroot()
    first_ring, boxes = None, []
    for el in root.iter():
        gid = el.get("id") or ""
        if not gid.startswith("cell|"):
            continue
        ring = gid.split("|")[1]
        if first_ring is None:
            first_ring = ring
        if ring != first_ring:
            continue
        pts = _points(el)
        if pts:
            xs, ys = zip(*pts)
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    if not boxes:
        raise SystemExit(f"no cells found in {svg}")
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _points(el) -> list[tuple[float, float]]:
    """Every absolute coordinate pair in this element's path data."""
    d = el.get("d") or ""
    if not d:
        child = el.find(f"{NS}path")
        d = (child.get("d") if child is not None else "") or ""
    nums, out, cur = [], [], ""
    for ch in d:
        if ch in "0123456789.-eE" and not (ch == "-" and cur and cur[-1] not in "eE"):
            cur += ch
        else:
            if cur:
                nums.append(float(cur))
            cur = "-" if ch == "-" else ""
    if cur:
        nums.append(float(cur))
    for i in range(0, len(nums) - 1, 2):
        out.append((nums[i], nums[i + 1]))
    return out


def main() -> None:
    run = Path(sys.argv[1])
    out = Path(sys.argv[2])
    svg, png = run / "figure.svg", run / "figure.png"
    root = ET.parse(svg).getroot()
    _, _, vw, vh = (float(v) for v in root.get("viewBox").split())
    img = Image.open(png)
    # The PNG is written at a different dpi from the SVG's user units, so the
    # scale is measured off the two canvases rather than assumed.
    sx, sy = img.width / vw, img.height / vh
    x0, y0, x1, y1 = _outer_ring_extent(svg)
    cx = (x0 + x1) / 2
    # The gap is centred on 12 o'clock and the names run inward from the
    # outermost band, so: half the circle's width, from the top of the ring down
    # past the hole.
    half = (x1 - x0) * 0.34
    box = (int((cx - half) * sx), int((y0 - 14) * sy),
           int((cx + half) * sx), int((y0 + (y1 - y0) * 0.36) * sy))
    img.crop(box).save(out)
    print(f"wrote {out} ({box[2] - box[0]}x{box[3] - box[1]} px from {png})")


main()
