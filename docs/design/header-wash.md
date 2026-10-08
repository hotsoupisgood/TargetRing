# The masthead watercolour

The soft wash behind `targetring v2` in the page header. It is an accent and
nothing else: it carries no data, states nothing, and is safe to lose.

## Regenerating it

```
.venv/bin/python scripts/make_header_wash.py --seed 11
```

**Seed 11.** That is the default, so the bare command reproduces the files on
disk. The seed and this command are also written into each PNG's own `tEXt`
metadata, so a file found on its own says how to rebuild itself:

```
.venv/bin/python -c "from PIL import Image; print(dict(Image.open('targetring/static/header-wash.png').text))"
```

To check the files on disk match what the generator produces now:

```
.venv/bin/python scripts/make_header_wash.py --check
```

`--check` compares **pixels, not file bytes**. PNG encoders differ between
versions, and a byte comparison would fail on a zlib upgrade while the image is
identical.

## What it is made of

| | |
|---|---|
| Output | `targetring/static/header-wash.png`, `header-wash-dark.png` |
| Size | 1600 × 256 px, RGBA, transparent except the wash |
| Pigments | `--tr-cat-1`, `--tr-cat-2`, `--tr-cat-3`, **read from `app.css`** |
| Peak alpha | 0.26 light, 0.19 dark |
| Mounted by | `targetring/static/header-wash.css` |
| Served by | `/header-wash.png`, `/header-wash-dark.png` (`app.py`) |

The pigments are read out of `app.css` at generation time rather than copied
into the script. A hex copied into the generator is a second palette, and it
drifts the first time the real one is retuned — the token block exists so there
is one system.

## Three properties it has to have

**It is reproducible.** Everything else in this repo is reproducible from the
manifest. A decorative asset that was not would be the one thing in the build
nobody could rebuild, and "regenerate the header" would mean "find whoever drew
it". One seed in, the same pixels out.

**The dark render is its own render, not the light one dimmed.** A pale paper
wash on a near-black surface is a bright blob, and no blend mode fixes that
honestly. Same generator, same seed, different token scope — the dark steps in
`app.css` are derived against `#15171b` rather than inverted, so the script
reads that block rather than transforming the light one. The peak alpha is lower
in dark because the eye measures the contrast step from the surface, not the
opacity.

**It never touches the type.** The `h1` and the sub-heading sit on the left, so
a horizontal `smoothstep` ramp carries alpha to zero across the left third. The
pigment collects on the right, where there is nothing to read. It is a ramp and
not a crop so the accent has no visible edge.

## Why the image is mounted from its own stylesheet

`app.css` **cannot** name an image.
`test_export.py::test_the_stylesheet_is_inlined_and_imports_nothing` inlines
`app.css` verbatim into the standalone export and asserts the literal text
`url(` appears nowhere in the result. Its message is *"a CSS url() is a
request"*, and that is AGENTS.md rule 10 in presentational form: a `file://`
artefact that quietly fetches a sibling is not local-only. Because the check is
on the literal string it also covers a `data:` URI, which is correct — a
literal-text check cannot tell a fetch from an embed.

So `app.css` keeps the masthead's geometry and `--tr-wash-height` (which
reserves the band, so mounting the image moves no layout), and the one rule that
mounts the image lives in `header-wash.css`. That file is linked from
`index.html` and is never inlined into the export: `export.py` builds its own
`<head>` and inlines only `app.css` plus its own export rules, so the offline
artefact stays `url()`-free and the served app still gets the accent.

## How it degrades

`header-wash.css` only ever sets `background-image`, never the `background`
shorthand — which would reset the colour. `app.css` paints
`background-color: var(--tr-surface)` underneath. So if the PNG is missing, or
the stylesheet is not served, the header degrades to plain tinted paper rather
than to a hole.

That is also the trap: **a missing wash is invisible.** The stylesheet shipped
once with no route behind it and 404ed silently, looking exactly like a correctly
absent accent. If the header looks plain, check `/header-wash.css` and
`/header-wash.png` both return 200 before touching the CSS.

Print and forced-colours drop the accent entirely: in a high-contrast mode the
wash is noise competing with the only thing that matters, and on paper it is ink
spent on nothing.

## Changing it

Pick a different seed, look at it, and if you keep it update **both** the
default in `scripts/make_header_wash.py` and the seed named at the top of this
file — the PNG metadata updates itself. `tests/test_header_wash.py` asserts
those three agree, so they cannot drift silently.
