"""Colour, chosen from what a ring's number *is*.

v2.5 drew all twelve rings on one truncated viridis ramp, each min-max scaled
inside its own submitted range. UX A5's finding was that a reader cannot tell
that ring 3's yellow and ring 7's yellow mean unrelated things, and COUNCIL V17
adds that a sequential ramp on a signed quantity is not a presentation choice —
it is the wrong encoding, because it puts "strongly down-regulated" at the end
of the ramp where every other ring puts "worst".

So colour here is derived, not picked, and the derivation is the point:

* **One luminance envelope for every ramp.** Every value colour this module can
  produce has WCAG relative luminance inside `RAMP_LUM`. That is what keeps
  `figure.assert_greyscale_disjoint` provable across twelve colormaps instead of
  one: the three reserved state fills sit at 0.0, 0.807 and 1.0, and no ramp can
  wander into their bands however many ramps there are. It is also the reason
  the ramps are *built* rather than taken from matplotlib's collection —
  `plasma`, `Blues` and `YlOrBr` have three different luminance spans, and
  stacking twelve of those reopens the collision the v2.2 truncation closed.
* **Hue carries ring identity; lightness and chroma carry magnitude.** Within a
  ring, pale and washed out is always less and dark and saturated is always more
  (v3.4, Puck's call -- it ran the other way until then). On a diverging ring
  the same sentence already held: the midpoint is the palest colour and each arm
  darkens away from zero in the direction its hue names, so diverging rings did
  not move. Between rings, hue says "this is a different measurement with a
  different scale".
* **A ring with few levels gets few colours.** An ordinal with four steps drawn
  on a 256-step ramp invites a reader to interpolate between steps that do not
  exist. `clinical_phase`, `tdl`, `chemical_probe` and `rodent` are drawn as
  discrete swatches sampled from their family's ramp.

Built in OKLCh and converted here rather than pulling in `colorspacious`: the
only operations needed are a forward conversion and two bisections, and this
repo's rule is that a colour claim is asserted, not trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from matplotlib.colors import LinearSegmentedColormap, to_hex, to_rgb

# The band every value colour lives in, as WCAG relative luminance.
#
# Reserved, and deliberately outside it: `failed` black at 0.0, `no_data`
# #e8e8e8 at 0.8069, `not_applicable` white at 1.0. The gaps are 0.060 below,
# 0.147 above and 0.193 between the top two — all clear of
# `figure.GREYSCALE_MIN_DELTA` (0.05). v2.5 achieved the same thing by
# truncating viridis to [0.16, 0.92], which put one ramp in [0.056, 0.663];
# this is that interval, generalised to every ramp and enforced by construction.
RAMP_LUM: tuple[float, float] = (0.075, 0.740)

# A diverging ramp's midpoint is its lightest colour, and the midpoint of a
# *signed* ring means "zero", so it must not be mistaken for the `no_data` grey
# at 0.8069. Held below the sequential ceiling to widen that gap to 0.207.
DIVERGING_MID_LUM = 0.700

# OKLCh hue angles, degrees, chosen by `scripts/hue_search.py` rather than by
# eye. Ten chromatic identities for twelve rings: `tractable`/`chemical_probe`
# share one on purpose (v2.3 split them out of a single ring and they answer the
# same question), as do `tdl`/`literature` (both measure how much anybody has
# studied the gene, which is COUNCIL M3's bias and worth seeing as one block).
#
# The search maximises the worst pair of *touching* rings under the worst of
# normal, protanopic and deuteranopic vision, subject to three hard constraints:
# every pair clears a normal-vision floor, each diverging ring's arms are far
# apart, and the two signed rings keep their direction -- logFC down is cool and
# up is warm, safety's bad arm is the warm one. It reached 8.6 for the worst
# touching pair and 6.4 for the worst pair anywhere on the figure;
# `scripts/check_ring_colours.py` re-measures both and the V2 report carries
# them, because the second number is at a geometric ceiling rather than at a
# comfortable margin.
# OKLCh hue angles, degrees. Ten chromatic identities for twelve rings:
# `tractable`/`chemical_probe` share one on purpose (v2.3 split them out of a
# single ring and they answer the same question), as do `tdl`/`literature`
# (both measure how much anybody has studied the gene, which is COUNCIL M3's
# bias and worth being able to see as one block).
#
# The angles are a 36-degree wheel walked in steps of 4, so that rings which
# *touch* are 108-180 degrees apart while the whole set stays evenly spread.
# Only logFC's two poles are chosen rather than derived: blue-down / red-up is
# the convention every reader of an expression heatmap already has, and
# `scripts/hue_search.py` confirmed that spending it buys nothing elsewhere.
# `scripts/check_ring_colours.py` measures what the assignment achieves.
HUE = {
    "blue": 268.4,      # logFC, down-regulated
    "ember": 36.5,      # logFC, up-regulated
    "rose": 341.8,      # neg_log10_padj
    "olive": 63.6,      # tractability + chemical probe
    "crimson": 8.6,     # safety, negative (liability) arm
    "azure": 221.4,     # safety, positive arm
    "green": 118.7,     # regional_specificity
    "teal": 191.6,      # clinical_phase
    "chartreuse": 90.0,  # tdl + literature
    "jade": 157.7,      # rodent
    # Used only by the `completeness` gauge, at GAUGE_CHROMA -- a third of the
    # chroma every other ring gets, so it reads as a deliberately muted
    # instrument rather than as a twelfth piece of evidence.
    "slate": 198.7,
}


# Chroma at the middle of a ramp. Clipped per step to whatever sRGB can hold at
# that lightness, so a hue that cannot carry it simply gets less rather than
# silently landing outside the gamut and being clamped into a different colour.
RAMP_CHROMA = 0.138

# `completeness` is a gauge and the figure says so in three places. Drawing it
# at full chroma would make k look like another piece of evidence, so it is
# drawn at a chroma that reads as deliberately muted. It is the one ramp that
# fails the categorical chroma floor, and that is the intended statement.
GAUGE_CHROMA = 0.042

# The chroma the pale end of a sequential ramp keeps. Zero would put the low
# end on the neutral axis, where a washed-out value sits beside the hatched grey
# `no_data` fill and gets read as missing -- the same failure `diverging_hexes`
# guards its midpoint against, for the same reason. This is the floor that keeps
# "low" legible as a measurement.
LOW_END_CHROMA = 0.022

RAMP_STEPS = 256


# --------------------------------------------------------------------------
# OKLab / OKLCh -> sRGB. Björn Ottosson's matrices.
# --------------------------------------------------------------------------

def _linear_to_srgb(u: float) -> float:
    return 12.92 * u if u <= 0.0031308 else 1.055 * (u ** (1 / 2.4)) - 0.055


def _srgb_to_linear(u: float) -> float:
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


def oklab_to_linear_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def _in_gamut(rgb: tuple[float, float, float], eps: float = 1e-4) -> bool:
    return all(-eps <= c <= 1 + eps for c in rgb)


def oklch_to_rgb(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    """sRGB in [0, 1], with chroma reduced until the colour is representable.

    Clamping the channels instead would move the hue: a blue that overflows
    green comes back as a different blue, which is exactly the kind of quiet
    substitution this repo forbids elsewhere. Reducing chroma keeps the hue and
    the lightness and gives up only saturation.
    """
    h = math.radians(h_deg)
    lo, hi = 0.0, max(C, 0.0)
    if _in_gamut(oklab_to_linear_rgb(L, hi * math.cos(h), hi * math.sin(h))):
        c = hi
    else:
        for _ in range(28):
            mid = (lo + hi) / 2
            if _in_gamut(oklab_to_linear_rgb(L, mid * math.cos(h), mid * math.sin(h))):
                lo = mid
            else:
                hi = mid
        c = lo
    lin = oklab_to_linear_rgb(L, c * math.cos(h), c * math.sin(h))
    return tuple(min(1.0, max(0.0, _linear_to_srgb(min(1.0, max(0.0, v))))) for v in lin)


def linear_rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Ottosson's inverse, so a colour claim can be measured off the hex.

    v3.4 needs this: the assertion that the low end of a ramp is desaturated has
    to read the chroma of the colour that was actually produced, not the chroma
    that was asked for. `oklch_to_rgb` reduces chroma until the colour fits in
    sRGB, so the two are not the same number and the difference is the whole
    point of checking.
    """
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_c = l ** (1 / 3), m ** (1 / 3), s_ ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_c,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_c,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_c,
    )


def oklch_from_hex(hex_colour: str) -> tuple[float, float, float]:
    """(L, C, hue degrees) of a hex colour."""
    from matplotlib.colors import to_rgb
    r, g, b = (_srgb_to_linear(c) for c in to_rgb(hex_colour))
    L, a, bb = linear_rgb_to_oklab(r, g, b)
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360.0


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG relative luminance — what a colour becomes in greyscale."""
    r, g, b = rgb
    return (0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))


def _lightness_for_luminance(target: float, C: float, h_deg: float) -> float:
    """OKLab L whose gamut-clipped colour has the requested WCAG luminance.

    Luminance is monotone increasing in L at fixed hue and requested chroma, so
    a bisection converges and — this is the part that matters — every step of a
    ramp built this way hits its luminance target exactly. Monotonicity of the
    finished ramp is then true by construction rather than by inspection, which
    is what `figure.assert_ramp_luminance_is_monotone` re-checks.
    """
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if relative_luminance(oklch_to_rgb(mid, C, h_deg)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _grey_luminance(L: float) -> float:
    return relative_luminance(oklch_to_rgb(L, 0.0, 0.0))


def _luminance_targets(n: int, lum: tuple[float, float]) -> list[float]:
    """n luminances, evenly spaced *perceptually* rather than in luminance.

    Even spacing in luminance would crowd the whole dark half of the ramp into
    the first few steps: luminance is roughly L^3. Spacing in OKLab L and then
    converting gives steps a reader sees as even.
    """
    lo_L = _lightness_for_luminance(lum[0], 0.0, 0.0)
    hi_L = _lightness_for_luminance(lum[1], 0.0, 0.0)
    if n == 1:
        return [_grey_luminance((lo_L + hi_L) / 2)]
    return [_grey_luminance(lo_L + (hi_L - lo_L) * i / (n - 1)) for i in range(n)]


# --------------------------------------------------------------------------
# Ramps
# --------------------------------------------------------------------------

def _hex_inside_envelope(target: float, C: float, h_deg: float,
                        lum: tuple[float, float]) -> str:
    """The hex for (target, C, hue), nudged until the *quantised* colour is
    inside the envelope.

    `_lightness_for_luminance` bisects to 1e-12, so the colour it picks hits its
    target. `to_hex` then rounds to 8 bits, which moves the luminance by up to
    about 0.002 -- and a step at the very top of the band can round outward past
    the ceiling. v3.4 raised that ceiling from 0.660 to 0.740 and
    `test_one_luminance_envelope_is_what_makes_room` caught exactly that: a
    family reaching 0.7424 against a 0.742 tolerance.

    Relaxing the tolerance would have hidden it. Instead the emitted colour is
    checked and walked back in L until it is inside, so the envelope is a
    property of what is drawn rather than of what was requested.
    """
    L = _lightness_for_luminance(target, C, h_deg)
    for _ in range(64):
        hex_colour = to_hex(oklch_to_rgb(L, C, h_deg))
        lum_seen = relative_luminance(to_rgb(hex_colour))
        if lum_seen > lum[1]:
            L -= 1e-3
        elif lum_seen < lum[0]:
            L += 1e-3
        else:
            return hex_colour
    return hex_colour


def sequential_hexes(hue: str, n: int = RAMP_STEPS,
                     chroma: float = RAMP_CHROMA,
                     lum: tuple[float, float] = RAMP_LUM) -> list[str]:
    """Pale -> dark in one hue, spanning exactly the shared luminance band.

    v3.4, Puck's call: low is washed out and high is saturated and dark, which
    is the polarity every sequential colormap a reader has already seen uses.
    Before this the ramp ran the other way.

    Two things carry the magnitude together rather than one:

    * **lightness**, running from the light end of `RAMP_LUM` down to the dark
      end as the value rises;
    * **chroma**, running from `LOW_END_CHROMA` up to `chroma` over the same
      span, so the low end is genuinely desaturated rather than merely pale.

    The light end stops at `RAMP_LUM[1]` and does not reach white, and that is
    not a stylistic compromise. White is `not_applicable` and #e8e8e8 is
    `no_data`; a value drawn at either is a value a reader can mistake for a
    missing one, which is rule 1. `LOW_END_CHROMA` is the same guard in the
    other channel -- it keeps the pale end visibly *on the scale* rather than
    letting it drift to a neutral that reads as the grey no-data fill.
    """
    h = HUE[hue]
    targets = list(reversed(_luminance_targets(n, lum)))
    if n == 1:
        return [to_hex(oklch_to_rgb(
            _lightness_for_luminance(targets[0], chroma, h), chroma, h))]
    out = []
    for i, t in enumerate(targets):
        c = LOW_END_CHROMA + (chroma - LOW_END_CHROMA) * i / (n - 1)
        out.append(_hex_inside_envelope(t, c, h, lum))
    return out


def diverging_hexes(low_hue: str, high_hue: str, n: int = RAMP_STEPS,
                    chroma: float = RAMP_CHROMA) -> list[str]:
    """Two hues meeting at a near-neutral midpoint that means zero.

    The midpoint is not a pure grey: a flat grey cell beside the hatched grey
    `no_data` cells is a reading people get wrong even with the hatch, so the
    centre keeps just enough chroma to read as "on the scale" and is held at
    `DIVERGING_MID_LUM`, 0.207 clear of the `no_data` fill in greyscale.

    Note what this costs and does not hide: on a diverging ring, greyscale is
    genuinely ambiguous — equal and opposite values have the same luminance by
    design. The sign lives in the hue, which survives projection and both common
    colour-vision deficiencies, but not a photocopier. That is a property of
    encoding polarity at all, and it is stated on the scale.
    """
    half = n // 2 + 1
    lum = (RAMP_LUM[0], DIVERGING_MID_LUM)
    targets = _luminance_targets(half, lum)
    lo_arm, hi_arm = [], []
    for i, t in enumerate(targets):
        # Chroma falls to near zero as the arm approaches zero.
        f = 1.0 - i / (half - 1)
        c = max(chroma * f, 0.010)
        lo_arm.append(to_hex(oklch_to_rgb(
            _lightness_for_luminance(t, c, HUE[low_hue]), c, HUE[low_hue])))
        hi_arm.append(to_hex(oklch_to_rgb(
            _lightness_for_luminance(t, c, HUE[high_hue]), c, HUE[high_hue])))
    return lo_arm + list(reversed(hi_arm))[1:]


def discrete_hexes(hue: str, levels: int, chroma: float = RAMP_CHROMA) -> list[str]:
    """`levels` swatches of one hue, spread across the whole luminance band.

    Two levels get the two ends, so a binary ring is two colours a long way
    apart rather than two neighbouring steps of a ramp nobody can read.

    v3.4: same polarity as `sequential_hexes` -- the first level is the pale,
    desaturated one and the last is dark and saturated.
    """
    h = HUE[hue]
    targets = list(reversed(_luminance_targets(levels, RAMP_LUM)))
    if levels == 1:
        return [to_hex(oklch_to_rgb(
            _lightness_for_luminance(targets[0], chroma, h), chroma, h))]
    out = []
    for i, t in enumerate(targets):
        c = LOW_END_CHROMA + (chroma - LOW_END_CHROMA) * i / (levels - 1)
        out.append(_hex_inside_envelope(t, c, h, RAMP_LUM))
    return out


def cmap_from(hexes: list[str], name: str) -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list(name, hexes, N=len(hexes))


# --------------------------------------------------------------------------
# The families the rings draw from
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Family:
    """How one ring is coloured, and the sentence that says so on its scale."""

    name: str
    kind: str                      # sequential | diverging | discrete | categorical
    hexes: tuple[str, ...]
    blurb: str

    def cmap(self) -> LinearSegmentedColormap:
        return cmap_from(list(self.hexes), self.name)


def _seq(name: str, hue: str, blurb: str, chroma: float = RAMP_CHROMA) -> Family:
    return Family(name, "sequential", tuple(sequential_hexes(hue, chroma=chroma)), blurb)


FAMILIES: dict[str, Family] = {
    # Diverging. Cool for down-regulated and warm for up-regulated is the
    # convention the reader already has from every expression heatmap, and the
    # hue search keeps it as a hard constraint. COUNCIL V17 asks for two things
    # and gets both: zero is anchored at the pale middle, and neither arm is
    # drawn as the good one, because whether up-regulation is the therapeutic
    # direction is a question this figure cannot answer.
    "logfc": Family(
        "logfc_div", "diverging", tuple(diverging_hexes("blue", "ember")),
        "blue = down · pale = no change · orange = up. Neither direction is "
        "drawn as the good one. The figure cannot say which is therapeutic.",
    ),
    # Safety is signed with a *stated* bad direction, so it gets a diverging
    # pair whose warm arm is the bad one -- also a hard constraint in the
    # search, because a signed ring whose bad half looks calm is a plausible
    # wrong figure in one colour.
    "safety": Family(
        "safety_div", "diverging", tuple(diverging_hexes("crimson", "azure")),
        "red = liability / constraint · blue = clean on these four keys. "
        "NOT evidence of safety.",
    ),
    "padj": _seq("padj_seq", "rose", "darker = weaker, lighter = stronger evidence"),
    "tractability": _seq("tract_seq", "olive", "darker = fewer tractability keys"),
    "specificity": _seq("spec_seq", "green", "darker = less region-restricted"),
    "knowledge": _seq("know_seq", "chartreuse", "darker = less studied"),
    "pipeline": _seq("pipe_seq", "teal", "darker = earlier / no pipeline"),
    "rodent": _seq("rodent_seq", "jade", "darker = phenotyped, nothing abnormal"),
    # The gauge. Muted on purpose — see GAUGE_CHROMA.
    "gauge": _seq("gauge_seq", "slate", "deliberately desaturated: k is coverage, "
                                       "not evidence", chroma=GAUGE_CHROMA),
}

# COUNCIL V12. Three discrete states that are not points on any ramp, so they
# are drawn off every ramp: three hues at three luminances inside the shared
# band, which keeps the greyscale guarantee without pretending the ring is
# continuous. v2.4's palette (#8c2d04 / #fdbe6f / #08519c) is re-stepped here
# rather than kept, because #fdbe6f sits at luminance 0.640 — inside the band
# but close enough to the sequential ceiling that a reader could read it as the
# top of a ramp.
ACTIVITY_HEXES: dict[float, str] = {
    # Hue AND lightness both do work here, so the three states stay apart in
    # greyscale and under both common colour-vision deficiencies. The hues are
    # the ones its ring-neighbours (7 clinical_phase, 9 tdl) do not use.
    -1.0: to_hex(oklch_to_rgb(_lightness_for_luminance(0.100, 0.13, HUE["ember"]),
                              0.13, HUE["ember"])),
    0.0: to_hex(oklch_to_rgb(_lightness_for_luminance(0.600, 0.09, HUE["rose"]),
                             0.09, HUE["rose"])),
    1.0: to_hex(oklch_to_rgb(_lightness_for_luminance(0.300, 0.13, HUE["blue"]),
                             0.13, HUE["blue"])),
}


def all_value_hexes() -> set[str]:
    """Every colour any ring can paint a *value* cell.

    This is what `assert_fills_disjoint` and invariant 2 check the reserved
    state fills against. It is the union over all twelve rings rather than one
    ramp, so adding a family cannot quietly reopen the collision.
    """
    out: set[str] = set(ACTIVITY_HEXES.values())
    for fam in FAMILIES.values():
        out |= set(fam.hexes)
    # The discrete swatch sets are samples of a family's ramp taken at level
    # positions, which need not land on one of the 256 stored steps.
    for hue, levels in _DISCRETE_SAMPLES:
        out |= set(discrete_hexes(hue, levels))
    return out


# (hue, levels) for every ring drawn as discrete swatches. Kept here so
# `all_value_hexes` covers them without figure.py having to hand them over.
_DISCRETE_SAMPLES: tuple[tuple[str, int], ...] = (
    ("olive", 2),       # chemical_probe, off the tractability family
    ("teal", 6),        # clinical_phase
    ("chartreuse", 4),  # tdl, off the literature family
    ("jade", 2),        # rodent
)


__all__ = [
    "RAMP_LUM", "DIVERGING_MID_LUM", "HUE", "RAMP_CHROMA", "GAUGE_CHROMA",
    "FAMILIES", "Family", "ACTIVITY_HEXES", "all_value_hexes",
    "sequential_hexes", "diverging_hexes", "discrete_hexes", "cmap_from",
    "oklch_to_rgb", "relative_luminance", "oklab_to_linear_rgb",
]
