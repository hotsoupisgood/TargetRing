# Palette validation — 2026-10-07

Two passes: the twelve ring ramps (`scripts/check_ring_colours.py`, which
already existed and had not been run and recorded), and the three severity
colours the evidence surface added on the same day.

Nothing here was retuned. One pass confirmed a documented ceiling; the other
**disproved a claim that had been written into `app.css` as a comment**, and
that comment has been corrected.

Reproduce:

```
PYTHONPATH=. .venv/bin/python scripts/check_ring_colours.py
```

---

## 1. The ring ramps

### Greyscale against the reserved fills — the hard requirement, PASS

The one that is a pass rather than a target. Every value colour any ring can
paint, against each reserved state fill:

| fill | | luminance | nearest value colour | required |
|---|---|---|---|---|
| `no_data` | `#e8e8e8` | 0.8070 | **0.0672** away | 0.05 |
| `not_applicable` | `#ffffff` | 1.0000 | **0.2602** away | 0.05 |
| `failed` | `#000000` | 0.0000 | **0.0747** away | 0.05 |

2,200 value colours span luminance 0.0747–0.7398. All three clear it, and the
guarantee holds across every family. This is the one that must not regress: a
hatched cell that greys to the same value as a real measurement is rule 1
failing in print.

### Touching rings — 5 of 10 clear the target, by gamut ceiling

Worst pair of equally-bright cells across each boundary, under the worst of
normal / protan / deutan, against a target of dE 8:

| boundary | identity dE | |
|---|---|---|
| logfc ↔ neg_log10_padj | 6.3 | under |
| neg_log10_padj ↔ tractable | 9.5 | ok |
| tractable ↔ chemical_probe | — | same family by decision |
| chemical_probe ↔ safety | 16.0 | ok |
| safety ↔ regional_specificity | 7.5 | under |
| regional_specificity ↔ clinical_phase | 9.0 | ok |
| clinical_phase ↔ clinical_activity | 2.8 | under |
| clinical_activity ↔ tdl | 3.0 | under |
| tdl ↔ rodent | 2.1 | under |
| rodent ↔ literature | 17.7 | ok |
| literature ↔ completeness | 9.0 | ok |

Worst anywhere on the two ramps: **dE 2.1**. The five that miss are
pale-meets-pale or dark-meets-dark — the gamut ceiling
`figure.assert_rings_are_distinguishable` describes, not a fixable assignment.
Eleven chromatic identities at one luminance sit on a circle of radius 100·C in
OKLab, so the best achievable worst pair is 2·100·C·sin(π/11) ≈ 6.5 at C=0.115.

### All pairs over the whole band — the ceiling, reported not asserted

| vision | worst pair | dE |
|---|---|---|
| normal | logfc ↔ safety | 0.8 |
| protan | clinical_phase ↔ completeness | 0.1 |
| deutan | logfc ↔ rodent | 0.1 |

The ends of every ramp converge on near-black and near-white, so this number is
produced by the ceiling and is reported rather than asserted against.

**Conclusion: no change.** Ring identity is carried by radial position, ring
number and the labelled swatch in the scale column. These numbers are why those
three are not optional, and that is already stated on the figure.

---

## 2. The evidence surface's three severities — one claim disproved

`app.css` section 11 had been written with the comment: *"each with a BORDER as
well as a wash, because a wash alone is the one channel that survives neither
greyscale nor either common colour-vision deficiency."* Measured, that is only
true of one of the three pairs.

Border colours, measured in both schemes:

| pair | worst dE (normal/protan/deutan) | greyscale ΔLum |
|---|---|---|
| critical ↔ serious | **17.1** light, **11.9** dark | 0.140 / 0.109 |
| critical ↔ ink-4 (unevaluable) | **13.8** light, **12.1** dark | 0.123 / 0.124 |
| serious ↔ ink-4 (unevaluable) | **8.0** light, **8.0** dark | **0.017 / 0.015** |

**`critical` is safe.** The severity that matters most — only a source
disagreement says the figure's own numbers may be wrong — separates from both
others in colour across all three vision models and in lightness. Three
channels.

**`serious` vs `ink-4` is not.** dE 8.0 sits *at* the target rather than above
it, and ΔLum 0.017 is the same grey on a monochrome print. The border does not
tell a flag from a reading that could not be run.

What does, in every condition including monochrome, is the **pill text** —
the literal words `flag` and `cannot be checked` on every finding. That channel
was already there and is now the stated reason it is there.

**Changes made:** the `app.css` comment now records the measurement and names
the pill text as the distinguishing channel, instead of claiming the border
does it. No colour was changed: re-tuning `--tr-status-serious` away from
`--tr-ink-4` would move a token the whole app shares to fix a distinction that
is already carried by words.

### Not covered

The header wash (`--tr-cat-1/2/3` at ≤0.26 alpha) is decorative, carries no
state and is dropped entirely under `forced-colors` and in print, so it is out
of scope for a legibility pass. `th` and `.grid thead .gene-col` carry a
hard-coded `#f2f3f5` background with no dark override: measured, they keep dark
text on a light ground in dark mode, so they are a light-coloured island rather
than unreadable. Left alone — a decision, not a defect.
