"""The one comparison a reader can check without trusting the report (v2.6).

On the council liver run, v2.5 painted 16 (gene, colour) pairs identically on
two to seven *different* rings -- the literal UX A5 defect, and the reason the
report cannot just say "the rings are more distinguishable now". This crops the
same spoke out of the v2.5 and v2.6 audit renders so the same cells can be put
side by side.

The crop is located by the cells' own SVG ids rather than by geometry, because
the two canvases are different sizes and the axes box moved to make room for
the scale column -- picking pixel coordinates by hand would be a measurement of
nothing.

Run: PYTHONPATH=. .venv/bin/python scripts/before_after_crop.py \\
         runs/V25-COUNCIL-COLD runs/V26-COUNCIL-COLD TTR \\
         docs/screenshots/9-two-rings-that-looked-alike.png
"""
from __future__ import annotations

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NS = "{http://www.w3.org/2000/svg}"
RINGS = ("clinical_phase", "regional_specificity")


def _cell_boxes(svg: Path, gene: str, rings=RINGS) -> dict[str, tuple[float, ...]]:
    """Bounding boxes, in SVG user units, of this gene's cells on these rings."""
    root = ET.parse(svg).getroot()
    want = {r: f"cell|{r}|" for r in rings}
    out: dict[str, tuple[float, ...]] = {}
    for el in root.iter():
        gid = el.get("id") or ""
        for ring, prefix in want.items():
            if not gid.startswith(prefix) or not gid.endswith(f"|{gene}"):
                continue
            xs, ys = [], []
            for node in [el] + list(el.iter()):
                d = node.get("d")
                if not d:
                    continue
                nums = [float(v) for v in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", d)]
                xs += nums[0::2]
                ys += nums[1::2]
            if xs:
                out[ring] = (min(xs), min(ys), max(xs), max(ys))
    return out


def _fills(run: Path, gene: str) -> dict[str, str]:
    return {r["ring"]: r["fill"] for r in csv.DictReader(open(run / "values.csv"))
            if r["gene"] == gene and r["ring"] in RINGS}


def _crop(run: Path, gene: str, pad: float = 1.55) -> Image.Image:
    """The spoke, cropped from the PNG the run already wrote."""
    svg, png = run / "figure.svg", run / "figure.png"
    root = ET.parse(svg).getroot()
    vb_w, vb_h = (float(v) for v in root.get("viewBox").split()[2:])
    im = Image.open(png).convert("RGB")
    sx, sy = im.width / vb_w, im.height / vb_h

    boxes = _cell_boxes(svg, gene)
    if len(boxes) < len(RINGS):
        raise SystemExit(f"{run}: found {sorted(boxes)} for {gene}, wanted {RINGS}")
    x0 = min(b[0] for b in boxes.values()) * sx
    y0 = min(b[1] for b in boxes.values()) * sy
    x1 = max(b[2] for b in boxes.values()) * sx
    y1 = max(b[3] for b in boxes.values()) * sy
    # SVG y grows downward in matplotlib's output, so no flip is needed.
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = max(x1 - x0, y1 - y0) * pad / 2
    return im.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))


def main() -> int:
    before, after, gene, out = (Path(sys.argv[1]), Path(sys.argv[2]),
                                sys.argv[3], Path(sys.argv[4]))
    crops = [_crop(before, gene), _crop(after, gene)]
    size = min(c.width for c in crops)
    crops = [c.resize((size, size), Image.LANCZOS) for c in crops]

    try:
        big = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 21)
        small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
    except OSError:                                    # pragma: no cover
        big = small = ImageFont.load_default()

    fb, fa = _fills(before, gene), _fills(after, gene)
    lines = {
        "clinical_phase": "phase = 5 (approved)",
        "regional_specificity": "specificity = 5,204x",
    }
    pad, header, footer = 24, 104, 132
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    # The swatch column is placed past the longest caption rather than at a
    # guessed offset -- the first version put it on top of the text.
    text_w = max(probe.textlength(f"{t}   {f[k]}", font=small)
                 for f in (fb, fa) for k, t in lines.items())
    col_w = max(size, int(text_w) + 64)
    W = col_w * 2 + pad * 3
    canvas = Image.new("RGB", (W, size + header + footer), "white")
    for i, c in enumerate(crops):
        canvas.paste(c, (pad + i * (col_w + pad), header))
    d = ImageDraw.Draw(canvas)
    d.text((pad, 12), f"{gene}: clinical_phase and regional_specificity —",
           font=big, fill="#111111")
    d.text((pad, 38), "two rings that measure nothing in common",
           font=big, fill="#111111")
    d.text((pad, 74), "v2.5 — one viridis ramp", font=small, fill="#8a4b00")
    d.text((pad + col_w + pad, 74), "v2.6 — one family per ring",
           font=small, fill="#8a4b00")

    y = header + size + 12
    for i, f in enumerate((fb, fa)):
        x = pad + i * (col_w + pad)
        same = f[RINGS[0]] == f[RINGS[1]]
        for j, ring in enumerate(("clinical_phase", "regional_specificity")):
            d.text((x, y + j * 22), f"{lines[ring]}   {f[ring]}",
                   font=small, fill="#111111")
            d.rectangle((x + int(text_w) + 14, y + j * 22 + 2,
                         x + int(text_w) + 54, y + j * 22 + 16),
                        fill=f[ring], outline="#666666")
        d.text((x, y + 52),
               "the identical pixel" if same else "two colours, two scales",
               font=small, fill="#b00020" if same else "#1a7f37")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"wrote {out}  ({fb} -> {fa})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
