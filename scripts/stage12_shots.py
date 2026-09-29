#!/usr/bin/env python
"""Stages 1-2, live in the browser on 8811: the autocomplete and the picker.

    .venv/bin/python scripts/stage12_shots.py [--port 8811]

The tests assert the payloads; this is the other half — the real stylesheet, the
real wrapping, the numbers as printed. Crops follow `v39_shots.py`: a narrow
viewport at `deviceScaleFactor` 3 rather than a laptop width, because Puck reads
these on a phone and a 1,390 px panel downscaled to a phone puts 12 px type at
3.4 px.

    stage1-disease-autocomplete.png   the hits, both ids, the counts, the date
    stage2-contrast-picker.png        the head, the dominance banner, the table
    stage2-contrast-detail.png        one row's ContrastRef and sign sentence
    stage12-probe.json                what the DOM actually says

The server is expected to be up already. This script starts nothing, fetches no
source and makes no paid call.
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

QUERY = "Crohn's disease"

PROBE = """() => {
  const hits = [...document.querySelectorAll('button.hit')].map(
    (b) => b.textContent.replace(/\\s+/g, ' ').trim());
  const rows = [...document.querySelectorAll('table.picker tbody tr')];
  return {
    hits,
    index_note: ($('#index-note') || {}).textContent,
    picker_head: ($('.picker-head') || {}).textContent,
    dominance: ($('#contrast-panel .note.warn') || {}).textContent,
    n_rows: rows.length,
    n_greyed: rows.filter((r) => r.classList.contains('greyed')).length,
    first_row: rows.length ? rows[0].textContent.replace(/\\s+/g, ' ').trim() : null,
    flags: [...new Set([...document.querySelectorAll('.flag')].map(
      (f) => f.textContent.trim()))].slice(0, 8),
    order_text: [...document.querySelectorAll('#contrast-panel .hint')].map(
      (h) => h.textContent.replace(/\\s+/g, ' ').trim()).find(
      (t) => t.startsWith('shown:')),
    no_default: [...document.querySelectorAll('#contrast-panel .hint b')].map(
      (b) => b.textContent.trim()).find((t) => t.startsWith('No contrast')),
  };
  function $(s) { return document.querySelector(s); }
}"""


def shoot(page, selector: str, name: str, max_h: float | None = None) -> None:
    """Element screenshot, optionally clipped to its first `max_h` CSS pixels.

    The clip is not tidiness. The Crohn's picker is 138 rows and 36,504 px tall at
    3x; a whole-element shot of it is a grey smear on a phone, which is the
    failure `v37_shots.py` wrote the narrow-viewport rule for. So the table is
    photographed at its top, where the head, the dominance banner, the order
    statement and the first rows are — and the row count in the head says how many
    rows are below the crop, so the crop cannot be mistaken for the whole table.
    """
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
    print(f"  {name}")


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
        # `domcontentloaded` is too early: a `fill` before that lands types into an
        # input with no listener and the run fails 15 s later with an empty page.
        # The populated region select is the signal that `init()` finished.
        page.wait_for_function("() => document.querySelector('#region').options.length")
        # The paste panel is 1,053 px of help text between the disease box and the
        # picker; closed, the two things being photographed are adjacent.
        page.evaluate("() => { const c = document.querySelector('#controls');"
                      " if (c) c.open = false; }")

        page.fill("#disease", QUERY)
        page.wait_for_selector("button.hit", timeout=15000)
        page.wait_for_function(
            "() => document.querySelector('#index-note').textContent.length > 40")
        shoot(page, "#find-panel", "stage1-disease-autocomplete.png")

        page.click("button.hit")
        page.wait_for_selector("table.picker tbody tr", timeout=20000)
        page.evaluate("() => document.querySelector('#contrast-panel')"
                      ".scrollIntoView({block: 'start'})")
        shoot(page, "#contrast-panel", "stage2-contrast-picker.png", max_h=1180)

        # The table is eight columns and scrolls sideways at this width, so the
        # right-hand half -- platform, drawable, and the flags that are the whole
        # point of greying a row rather than hiding it -- is off the first crop.
        # It is photographed as it really is, by scrolling the container, rather
        # than by hiding the two label columns for the photograph.
        page.evaluate("() => { const s = document.querySelector("
                      "'#contrast-panel .scroll-x'); s.scrollLeft = s.scrollWidth; }")
        page.wait_for_timeout(300)
        shoot(page, "#contrast-panel .scroll-x",
              "stage2-contrast-picker-flags.png", max_h=1000)
        page.evaluate("() => { document.querySelector("
                      "'#contrast-panel .scroll-x').scrollLeft = 0; }")

        # The same table sorted by the smallest side, which is the one place the
        # n<4 rows collect. Also the proof that the sort is a single column: the
        # accession order inside one n is the default order.
        page.select_option("#picker-sort", "n")
        page.wait_for_function(
            "() => document.querySelector('#contrast-panel .hint')"
            ".textContent.includes('smallest side') === false || true")
        page.wait_for_timeout(700)
        page.evaluate("() => document.querySelector('#contrast-panel')"
                      ".scrollIntoView({block: 'start'})")
        shoot(page, "#contrast-panel", "stage2-contrast-picker-sorted-by-n.png",
              max_h=1180)
        page.select_option("#picker-sort", "")
        page.wait_for_timeout(700)

        # The one drawable RNA-seq row, so the detail shows a sign sentence rather
        # than a refusal. `E-GEOD-101794 g2_g1` is the spike's contrast.
        page.evaluate(
            "() => { const rows = [...document.querySelectorAll("
            "'table.picker tbody tr')];"
            " const r = rows.find((x) => !x.classList.contains('greyed'));"
            " r.click(); r.scrollIntoView(); }")
        page.wait_for_selector("#contrast-detail .prose-name", timeout=10000)
        shoot(page, "#contrast-detail", "stage2-contrast-detail.png")

        probe = page.evaluate(PROBE)
        (OUT / "stage12-probe.json").write_text(json.dumps(probe, indent=2))
        print(f"  stage12-probe.json")
        browser.close()

    print("\nwhat the DOM says:")
    print(f"  hits: {len(probe['hits'])}, first = {probe['hits'][0][:110]!r}")
    print(f"  {probe['picker_head'].strip()[:140]}")
    print(f"  {(probe['dominance'] or '').strip()[:140]}")
    print(f"  rows {probe['n_rows']}, greyed {probe['n_greyed']}")
    print(f"  {probe['order_text'][:140]}")
    print(f"  {probe['no_default']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
