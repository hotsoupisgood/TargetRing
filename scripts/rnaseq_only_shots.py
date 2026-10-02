#!/usr/bin/env python
"""The one thing task 2 has to show: absent-from-Atlas vs absent-from-this-app.

    .venv/bin/python scripts/rnaseq_only_shots.py [--port 8811]

`tests/test_atlas.py::TestTheCountLineSeparatesAbsentFromUnsupported` asserts the
strings; this photographs them in the real stylesheet at a size Puck can read on
a phone, which is the half a test cannot do.

**Why "atopic eczema" is the query.** It resolves to `MONDO_0004980`, which has
**14 contrasts in Atlas and 0 this app will draw** — all microarray. And the
three hits under it are diseases Atlas has genuinely never studied. So one
screenshot carries both lines, adjacent, in the same list:

    atopic eczema        0 drawable of 14 contrasts … 14 microarray — in Atlas,
                         not read here (this app reads RNA-seq only)
    dermatitis, atopic, 2    no contrasts in Expression Atlas

A reader who cannot tell those apart is the reader this whole task exists for,
so they are photographed together rather than in two tidy separate shots.

    rnaseq-only-autocomplete.png   the two kinds of zero, side by side
    rnaseq-only-picker.png         clicking through: greyed rows, and the
                                   statement above them saying who excluded them
    rnaseq-only-probe.json         what the DOM actually says

Crops follow `v39_shots.py` and `stage12_shots.py`: a narrow viewport at
`deviceScaleFactor` 3, because a 1,390 px panel downscaled to a phone puts 12 px
type at 3.4 px. The server is expected to be up already. This script starts
nothing, fetches no source and makes no paid call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
PHONE_READABLE = {"width": 760, "height": 1500}

#: Resolves to `MONDO_0004980` — 14 array contrasts, 0 drawable — with genuine
#: zeroes immediately below it in the same hit list.
QUERY = "atopic eczema"

PROBE = """() => {
  const hits = [...document.querySelectorAll('button.hit')].map((b) => ({
    text: b.textContent.replace(/\\s+/g, ' ').trim(),
    availability: [...b.querySelectorAll('.hit-count')].map(
      (c) => c.className.replace('hit-count', '').trim())[0] || null,
    count_text: (b.querySelector('.hit-count') || {}).textContent,
  }));
  const rows = [...document.querySelectorAll('table.picker tbody tr')];
  return {
    hits,
    n_rows: rows.length,
    n_greyed: rows.filter((r) => r.classList.contains('greyed')).length,
    picker_head: (document.querySelector('.picker-head') || {}).textContent,
    excludes: [...document.querySelectorAll('#contrast-panel .note')].map(
      (n) => n.textContent.replace(/\\s+/g, ' ').trim()).find(
      (t) => t.includes('excludes')),
    first_flag: (document.querySelector('.flag.microarray') || {}).textContent,
    not_drawable_reason: (document.querySelector(
      '#contrast-detail .hint') || {}).textContent,
  };
}"""


def shoot(page, selector: str, name: str, max_h: float | None = None) -> None:
    el = page.query_selector(selector)
    if el is None:
        raise SystemExit(f"{selector} is not on the page; nothing was captured")
    if max_h is None:
        el.screenshot(path=str(OUT / name))
    else:
        box = el.bounding_box()
        page.screenshot(path=str(OUT / name), clip={
            "x": box["x"], "y": box["y"], "width": box["width"],
            "height": min(box["height"], max_h)})
    print(f"  {OUT / name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8811)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=PHONE_READABLE, device_scale_factor=3)
        page.goto(base + "/", wait_until="networkidle")
        # `init()` wires the disease box only after two awaited fetches, so
        # `domcontentloaded` is too early (stage12_shots.py's note).
        page.wait_for_function("() => document.querySelector('#region').options.length")
        page.evaluate("() => { const c = document.querySelector('#controls');"
                      " if (c) c.open = false; }")

        page.fill("#disease", QUERY)
        page.wait_for_selector("button.hit", timeout=15000)
        page.wait_for_function(
            "() => [...document.querySelectorAll('.hit-count')].some("
            "(c) => c.classList.contains('none_supported'))")
        # The hit list only -- the input and the help text above it are not what
        # is being shown, and including them shrinks the type that is.
        shoot(page, "#disease-hits", "rnaseq-only-autocomplete.png")

        # Clicking through must not produce a silent table of greyed rows.
        page.click("button.hit")
        page.wait_for_selector("table.picker tbody tr", timeout=20000)
        page.evaluate("() => document.querySelector('#contrast-panel')"
                      ".scrollIntoView({block: 'start'})")
        shoot(page, "#contrast-panel", "rnaseq-only-picker.png", max_h=1100)

        probe = page.evaluate(PROBE)
        (OUT / "rnaseq-only-probe.json").write_text(json.dumps(probe, indent=2))
        print(f"  {OUT / 'rnaseq-only-probe.json'}")

        # Read back rather than trusting the shot: a screenshot of the wrong
        # state is still a PNG, and this is the one claim the task turns on.
        kinds = [h["availability"] for h in probe["hits"]]
        if "none_supported" not in kinds:
            raise SystemExit("no 'exists but unsupported' hit was on screen")
        if "none_in_atlas" not in kinds:
            raise SystemExit("no genuine-zero hit was on screen to contrast with")
        print(f"\n  availability states on screen: {kinds}")
        print(f"  picker: {probe['n_greyed']} of {probe['n_rows']} rows greyed")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
