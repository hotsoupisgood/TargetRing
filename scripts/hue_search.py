"""Pick the ring hues by search, not by taste (v2.6).

Twelve rings need ten chromatic identities, and the dataviz method's own note is
that no ordering of *eight* categorical slots clears its all-pairs CVD floor --
so ten cannot either. What is achievable is the best ten, and knowing the number
is the difference between a defensible figure and a hopeful one.

The objective, in order:

* **hard** -- every pair of ring identities clears `ALL_PAIRS_MIN` under normal
  vision. This is UX A5 stated as a number: "a reader cannot tell that ring 3's
  yellow and ring 7's yellow mean unrelated things".
* **hard** -- each diverging ring's two arms are far apart, and the two signed
  rings keep their direction: logFC down is cool and up is warm, and safety's
  bad arm is the warm one. A signed ring whose bad direction is the calm-looking
  half is a plausible wrong figure in one colour.
* **maximise** -- the worst *adjacent* pair, over normal / protanopic /
  deuteranopic vision (Machado 2009 at severity 1.0, the same model
  `dataviz/scripts/validate_palette.js` uses; matrices copied from it so the two
  agree). Adjacent rings are the only ones that physically touch.

Two ceilings worth knowing before reading the output. Identities at one
luminance sit on a circle of radius 100*RAMP_CHROMA in OKLab, so eleven of them
cannot be more than 2*100*C*sin(pi/11) apart -- about 7.9 at C=0.138. And under
protanopia or deuteranopia at a fixed luminance the hue circle collapses onto
one axis, where the widest separation any two hues can reach is about 23.

Run:  PYTHONPATH=. .venv/bin/python scripts/hue_search.py > logs/v26-hue-search.txt
"""
from __future__ import annotations

import itertools
import math
import random

from targetring.palette import (
    ACTIVITY_HEXES, GAUGE_CHROMA, RAMP_CHROMA, RAMP_LUM, _lightness_for_luminance,
    oklch_to_rgb,
)

MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868],
               [0.114503, 0.786281, 0.099216],
               [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968],
               [0.280085, 0.672501, 0.047413],
               [-0.011820, 0.042940, 0.968881]],
}
VISION = (None, "protan", "deutan")


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _oklab(rgb):
    r, g, b = (_lin(x) for x in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _sim(rgb, kind):
    if kind is None:
        return rgb
    r, g, b = (_lin(x) for x in rgb)
    out = []
    for row in MACHADO[kind]:
        v = max(0.0, min(1.0, row[0] * r + row[1] * g + row[2] * b))
        out.append(12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)
    return tuple(out)


def dE(a, b, kind=None) -> float:
    """Euclidean distance in OKLab x100 -- the method's Delta E."""
    return 100 * math.dist(_oklab(_sim(a, kind)), _oklab(_sim(b, kind)))


_DE: dict[tuple, float] = {}


def dE_cached(a, b, kind=None) -> float:
    key = (a, b, kind)
    v = _DE.get(key)
    if v is None:
        v = _DE[key] = dE(a, b, kind)
    return v


# A ring's identity colour is the step a reader sees most of: the middle of its
# ramp. Memoised on a quarter-degree grid, because each call is two nested
# bisections and the annealer asks for tens of millions of them.
_ID: dict[tuple[int, int], tuple[float, float, float]] = {}


def identity(hue: float, chroma: float = RAMP_CHROMA, lum: float | None = None):
    lum = lum if lum is not None else (RAMP_LUM[0] + RAMP_LUM[1]) / 2
    key = (round(hue * 4) % 1440, round(chroma * 10000))
    hit = _ID.get(key)
    if hit is None:
        h = key[0] / 4.0
        hit = _ID[key] = oklch_to_rgb(_lightness_for_luminance(lum, chroma, h), chroma, h)
    return hit


# The free hues, in the order the annealer holds them.
NAMES = ["logfc-down", "logfc-up", "padj", "tractability", "safety-neg",
         "safety-pos", "specificity", "phase", "knowledge", "rodent", "gauge"]
CHROMA = [RAMP_CHROMA] * 10 + [GAUGE_CHROMA]

# The twelve rings, outermost first, as indices into NAMES. `clinical_activity`
# is not searched -- its three colours are fixed and carry luminance as well as
# hue -- so it enters as a literal set. Rings 4 and 11 repeat rings 3 and 9 by
# decision, which is why they are the same index rather than an exception.
RING_SLOTS: list[tuple[str, object]] = [
    ("logfc", [0, 1]),
    ("neg_log10_padj", [2]),
    ("tractable", [3]),
    ("chemical_probe", [3]),
    ("safety", [4, 5]),
    ("regional_specificity", [6]),
    ("clinical_phase", [7]),
    ("clinical_activity", "fixed"),
    ("tdl", [8]),
    ("rodent", [9]),
    ("literature", [8]),
    ("completeness", [10]),
]

ALL_PAIRS_MIN = 6.3
ARM_MIN_DELTA_E = 20.0
COOL = (180.0, 330.0)


def _activity_cols():
    return [tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
            for h in ACTIVITY_HEXES.values()]


def _ring_cols(hues):
    out = []
    for _, slots in RING_SLOTS:
        if slots == "fixed":
            out.append(_activity_cols())
        else:
            out.append([identity(hues[i], CHROMA[i]) for i in slots])
    return out


def _cool(h: float) -> bool:
    return COOL[0] <= h <= COOL[1]


def legal(hues: list[float]) -> bool:
    if not _cool(hues[0]) or _cool(hues[1]):
        return False
    if _cool(hues[4]) or not _cool(hues[5]):
        return False
    for i, j in ((0, 1), (4, 5)):
        if dE_cached(identity(hues[i]), identity(hues[j])) < ARM_MIN_DELTA_E:
            return False
    cols = [identity(h, c) for h, c in zip(hues, CHROMA)]
    return all(dE_cached(cols[i], cols[j]) >= ALL_PAIRS_MIN
               for i, j in itertools.combinations(range(len(cols)), 2))


def worst_adjacent(hues) -> float:
    """The worst pair of *touching* rings, over the three vision models.

    Rings 3/4 and 9/11 share a family by decision, so their own boundary is not
    a separation failure and is not scored.
    """
    cols = _ring_cols(hues)
    worst = 99.0
    for i in range(len(RING_SLOTS) - 1):
        a_slots, b_slots = RING_SLOTS[i][1], RING_SLOTS[i + 1][1]
        if a_slots == b_slots and a_slots != "fixed":
            continue
        for a in cols[i]:
            for b in cols[i + 1]:
                worst = min(worst, min(dE_cached(a, b, k) for k in VISION))
    return worst


def search(seed: int = 7, iters: int = 240000):
    rng = random.Random(seed)
    best = None
    restarts = 24
    for _ in range(restarts):
        # Start from an evenly spaced wheel, randomly rotated and permuted.
        # Uniform random hues almost never satisfy the all-pairs floor -- 55
        # pairs have to clear it at once -- so the annealer never got started.
        # Even spacing satisfies it by construction; the permutation is what the
        # search is actually exploring.
        hues = None
        for _ in range(4000):
            rot = rng.uniform(0, 360)
            wheel = [(rot + 360.0 * i / len(NAMES)) % 360 for i in range(len(NAMES))]
            rng.shuffle(wheel)
            if legal(wheel):
                hues = wheel
                break
        if hues is None:
            continue
        cur = worst_adjacent(hues)
        n_iter = iters // restarts
        for n in range(n_iter):
            temp = 2.5 * (1 - n / n_iter) ** 2
            i = rng.randrange(len(hues))
            trial = list(hues)
            trial[i] = (trial[i] + rng.gauss(0, 40 * (1 - n / n_iter) + 4)) % 360
            if not legal(trial):
                continue
            s = worst_adjacent(trial)
            if s > cur or (temp > 0 and rng.random() < math.exp((s - cur) / temp)):
                hues, cur = trial, s
        if best is None or cur > best[1]:
            best = (hues, cur)
    return best


if __name__ == "__main__":
    hues, adj = search()
    print(f"constraints: all pairs >= {ALL_PAIRS_MIN} (normal), diverging arms "
          f">= {ARM_MIN_DELTA_E}, logFC down cool / up warm, safety bad arm warm")
    print(f"worst adjacent-ring dE {adj:.1f} over normal/protan/deutan\n")
    for name, h in zip(NAMES, hues):
        print(f"  {name:16s} {h:6.1f}")
    cols = [identity(h, c) for h, c in zip(hues, CHROMA)]
    print("\nworst all-pairs, per vision model:")
    for kind in VISION:
        i, j = min(itertools.combinations(range(len(cols)), 2),
                   key=lambda p: dE(cols[p[0]], cols[p[1]], kind))
        print(f"  {kind or 'normal':7s} {NAMES[i]} <-> {NAMES[j]}  "
              f"dE {dE(cols[i], cols[j], kind):.1f}")
