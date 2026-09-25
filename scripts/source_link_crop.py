"""The scale column's source lines, cropped out of a real run at 1:1 (v2.8).

The claim this makes checkable by eye is the one that matters for the exported
PNG: each ring's source is printed with a *domain a reader can type*, in a
colour that reads as a link, and the link text did not cost the source name its
place. The column is 6.6 pt type on a 19.5 in canvas, so a full-figure
screenshot cannot show whether any of that is legible -- this crops the
uncompressed pixels.

The crop is located from the `ringsource|<key>` groups' own positions in the
SVG, not from pixel coordinates, so it follows the column if the layout moves
again -- the same reason `wedge_crop.py` locates its crop from cell ids.

Run: PYTHONPATH=. .venv/bin/python scripts/source_link_crop.py \\
         runs/V28-COUNCIL-COLD docs/screenshots/11-source-links-in-the-legend.png
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring.figure import AUDIT  # noqa: E402

NS = "{http://www.w3.org/2000/svg}"


def _source_line_extent(svg: Path) -> tuple[float, float, float]:
    """Left x, first baseline y and last baseline y of the source lines.

    `TestExportRemainsEditable` pins the font type so that text stays text, so
    each source line is one `<text x= y=>` and these three numbers come straight
    off it. The right edge is not in the markup -- a `<text>` declares no width
    -- so the caller takes it from the layout's own column, which is exact and
    moves when the layout does.
    """
    root = ET.parse(svg).getroot()
    xs: list[float] = []
    ys: list[float] = []
    for el in root.iter():
        if not (el.get("id") or "").startswith("ringsource|"):
            continue
        for t in el.iter(f"{NS}text"):
            xs.append(float(t.get("x")))
            ys.append(float(t.get("y")))
    if not xs:
        raise SystemExit(f"no ringsource groups in {svg}")
    return min(xs), min(ys), max(ys)


def main() -> None:
    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    svg, png = run / "figure.svg", run / "figure.png"
    root = ET.parse(svg).getroot()
    _, _, vw, vh = (float(v) for v in root.get("viewBox").split())
    img = Image.open(png)
    sx, sy = img.width / vw, img.height / vh
    x0, y_first, y_last = _source_line_extent(svg)
    # Right edge from the layout's own scale column, since a <text> declares no
    # width. Up from the first source line far enough to catch the ring title
    # and ticks it belongs to, because a column of source lines on its own would
    # not show that each is attached to the right ring.
    x1 = (AUDIT.scale_box[0] + AUDIT.scale_box[2]) * vw
    pad_x, pad_top, pad_bottom = 6.0, 34.0, 8.0
    box = (max(0, int((x0 - pad_x) * sx)), max(0, int((y_first - pad_top) * sy)),
           min(img.width, int((x1 + pad_x) * sx)),
           min(img.height, int((y_last + pad_bottom) * sy)))
    img.crop(box).save(out)
    print(f"wrote {out} ({box[2] - box[0]}x{box[3] - box[1]} px from {png})")


main()
