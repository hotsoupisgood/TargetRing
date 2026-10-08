#!/usr/bin/env python
"""Generate the masthead watercolour, light and dark.

    .venv/bin/python scripts/make_header_wash.py            # both, default seed
    .venv/bin/python scripts/make_header_wash.py --seed 7   # a different wash
    .venv/bin/python scripts/make_header_wash.py --check    # regenerate + compare

Writes `targetring/static/header-wash.png` and `header-wash-dark.png`, which
`static/header-wash.css` mounts. See that file for why the image is named from
its own stylesheet and never from `app.css`.

THREE PROPERTIES THIS HAS TO HAVE, and they are the reason it is a generator and
not a hand-drawn asset somebody dropped in `static/`:

1. **It is reproducible.** One seed in, the same bytes out, and the seed plus the
   exact command are written into the PNG's own `tEXt` metadata and into
   `docs/design/header-wash.md`. Everything else in this repo is reproducible
   from the manifest; a decorative asset that is not would be the one thing in
   the build nobody could regenerate. `--check` is the test of that claim.
2. **It samples the real palette.** The pigments are `--tr-cat-1/2/3` read out
   of `app.css` at generation time, not copies of them. A hex copied into this
   file is a second palette that drifts the first time the real one is retuned,
   and the whole point of the token block is that there is one system.
3. **The dark render is its own render, not the light one dimmed.** A pale paper
   wash on a near-black surface is a bright blob and no blend mode fixes that
   honestly. Same generator, same seed, different token scope -- which is
   exactly what `header-wash.css` says happens.

WHAT THE IMAGE IS. Pigment collects on the RIGHT: the h1 and the sub-heading sit
left, so the left third is carried to near-zero alpha by a horizontal ramp and
the wash cannot reduce contrast on type. It is transparent everywhere else,
never opaque -- `app.css` paints `background-color: var(--tr-surface)` underneath
and `header-wash.css` only ever sets `background-image`, so a missing asset
degrades to plain tinted paper rather than to a hole.

The watercolour look is three things stacked: irregular blooms whose edges are
fractal value noise rather than circles, a soft threshold that gives the damp
paper edge, and granulation -- pigment settling darker where a bloom's edge
stalls. No external asset, no font, no network (AGENTS.md rules 9 and 10).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "targetring" / "static"
APP_CSS = STATIC / "app.css"

#: The default. Chosen by eye from the first sixteen and then fixed: an accent
#: nobody can regenerate is the problem, not which of sixteen washes it is.
DEFAULT_SEED = 11

#: 2x the 128 px band `--tr-wash-height` reserves, and wide enough that
#: `background-size: cover` never upscales on a 27-inch monitor.
WIDTH, HEIGHT = 1600, 256

#: The pigments, as token NAMES. Resolved against `app.css` at generation time.
PIGMENT_TOKENS = ("--tr-cat-2", "--tr-cat-1", "--tr-cat-3")


def _scope_block(css: str, scheme: str) -> str:
    """The token block for one scheme.

    Light is the `:root{...}` at the top of the file. Dark is the
    `prefers-color-scheme: dark` block -- read rather than assumed, because the
    dark steps are derived against #15171b and are NOT an inversion of the light
    ones, which is the whole reason this script renders twice.
    """
    if scheme == "light":
        start = css.index(":root")
        return css[start:css.index("}", start)]
    m = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\s*\)\s*\{", css)
    if not m:
        raise SystemExit("app.css has no prefers-color-scheme: dark block")
    return css[m.end():css.index("\n  }", m.end())]


def tokens(scheme: str) -> dict[str, str]:
    block = _scope_block(APP_CSS.read_text(), scheme)
    # The group already carries the leading `--`; do not add it again.
    return {k: v.strip() for k, v in
            re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


def rgb(hex_colour: str) -> np.ndarray:
    h = hex_colour.strip().lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def value_noise(rng: np.random.Generator, shape: tuple[int, int],
                octaves: int = 5) -> np.ndarray:
    """Fractal value noise in [0, 1].

    Bilinear-upsampled random grids summed at halving amplitude. Cheap, and the
    only property that matters here is that a bloom's edge is irregular at more
    than one scale -- a single-octave edge reads as a blurred circle, which is
    the thing that makes a generated wash look generated.
    """
    h, w = shape
    out = np.zeros(shape, dtype=np.float64)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gh, gw = max(2, h >> (octaves - o)), max(2, w >> (octaves - o))
        grid = rng.random((gh, gw))
        # Bilinear upsample via PIL: exact, and avoids a hand-rolled resampler.
        up = np.asarray(Image.fromarray((grid * 255).astype(np.uint8))
                        .resize((w, h), Image.BILINEAR), dtype=np.float64) / 255.0
        out += up * amp
        total += amp
        amp *= 0.5
    return out / total


def smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def bloom(rng: np.random.Generator, cx: float, cy: float,
          rx: float, ry: float) -> np.ndarray:
    """One pigment bloom: an ellipse whose edge is eaten by noise, with the
    pigment settling darker where that edge stalls.

    `edge` is the granulation term. In a real wash the pigment is carried to the
    boundary and dries there, so the rim is denser than the middle -- without it
    this is an airbrush, which is the other way a generated wash gives itself
    away.
    """
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float64)
    d = np.sqrt(((xx - cx * WIDTH) / (rx * WIDTH)) ** 2
                + ((yy - cy * HEIGHT) / (ry * HEIGHT)) ** 2)
    # Noise displaces the boundary instead of modulating the fill, so the shape
    # is irregular rather than a circle with texture painted on it.
    d = d + (value_noise(rng, (HEIGHT, WIDTH)) - 0.5) * 0.85
    body = smoothstep((1.0 - d) * 1.6)
    edge = np.exp(-((d - 0.82) ** 2) / 0.012) * 0.55
    return np.clip(body + edge * body, 0.0, 1.0)


def render(scheme: str, seed: int) -> Image.Image:
    tok = tokens(scheme)
    missing = [t for t in PIGMENT_TOKENS if t not in tok]
    if missing:
        raise SystemExit(f"app.css {scheme} scope is missing {missing}")

    rng = np.random.default_rng(seed)
    rgba = np.zeros((HEIGHT, WIDTH, 4), dtype=np.float64)

    # Placed right of centre and drifting further right with each pigment, so
    # they overlap the way wet-in-wet washes do rather than sitting side by side.
    placements = ((0.74, 0.34, 0.40, 0.95),
                  (0.92, 0.62, 0.34, 0.85),
                  (0.62, 0.78, 0.30, 0.70))
    # Dark mode is quieter in absolute alpha: the same pigment over #15171b
    # reads louder than over white, because the contrast step from the surface is
    # what the eye measures, not the opacity.
    peak = 0.26 if scheme == "light" else 0.19

    for token, (cx, cy, rx, ry) in zip(PIGMENT_TOKENS, placements):
        mask = bloom(rng, cx, cy, rx, ry)
        colour = rgb(tok[token])
        a = mask * peak
        # Over, in premultiplied terms, so three translucent washes stack like
        # pigment rather than replacing one another.
        out_a = rgba[..., 3] + a * (1.0 - rgba[..., 3])
        with np.errstate(invalid="ignore", divide="ignore"):
            for c in range(3):
                rgba[..., c] = np.where(
                    out_a > 0,
                    (colour[c] * a + rgba[..., c] * rgba[..., 3] * (1.0 - a)) / np.maximum(out_a, 1e-9),
                    0.0)
        rgba[..., 3] = out_a

    # The h1 and the sub-heading live on the left. Carried to zero there, and it
    # is a ramp rather than a crop so there is no visible edge to the accent.
    ramp = smoothstep((np.linspace(0.0, 1.0, WIDTH) - 0.30) / 0.55)
    rgba[..., 3] *= ramp[None, :]
    # And faded at the very bottom so the band meets the header's 1px rule
    # cleanly instead of being cut off by it.
    vfade = smoothstep((1.0 - np.linspace(0.0, 1.0, HEIGHT)) / 0.22)
    rgba[..., 3] *= np.clip(vfade, 0.25, 1.0)[:, None]

    # Colour channels are already 0-255; alpha is a 0-1 coverage and is scaled
    # here, once, rather than being carried as bytes through the compositing.
    return Image.fromarray(np.dstack([
        np.clip(np.round(rgba[..., :3]), 0, 255).astype(np.uint8),
        np.clip(np.round(rgba[..., 3] * 255), 0, 255).astype(np.uint8),
    ]), "RGBA")


def metadata(scheme: str, seed: int) -> PngImagePlugin.PngInfo:
    """The seed and the exact command, in the file itself.

    `header-wash.css` promises a reader that this is here. An asset that says how
    to regenerate it cannot become the one thing in the build nobody can rebuild.
    """
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "targetring scripts/make_header_wash.py")
    info.add_text("Comment",
                  f"Masthead watercolour wash ({scheme}). Generated, never "
                  f"hand-drawn; pigments are --tr-cat-1/2/3 read from app.css.")
    info.add_text("Seed", str(seed))
    info.add_text("Command",
                  f".venv/bin/python scripts/make_header_wash.py --seed {seed}")
    info.add_text("Source", "targetring/static/app.css")
    return info


def out_path(scheme: str) -> Path:
    return STATIC / ("header-wash.png" if scheme == "light"
                     else "header-wash-dark.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--check", action="store_true",
                    help="regenerate and compare against what is on disk")
    args = ap.parse_args()

    rc = 0
    for scheme in ("light", "dark"):
        path = out_path(scheme)
        img = render(scheme, args.seed)
        if args.check:
            if not path.exists():
                print(f"MISSING  {path.name}")
                rc = 1
                continue
            # Compare pixels, not file bytes: PNG encoders differ between
            # versions and a byte comparison would fail on a zlib upgrade while
            # the image is identical.
            same = np.array_equal(np.asarray(img),
                                  np.asarray(Image.open(path).convert("RGBA")))
            print(f"{'OK      ' if same else 'DIFFERS '} {path.name}")
            rc = rc or (0 if same else 1)
            continue
        img.save(path, "PNG", pnginfo=metadata(scheme, args.seed), optimize=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        print(f"wrote {path.relative_to(ROOT)}  "
              f"{path.stat().st_size:,} bytes  sha256:{digest}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
