"""Prove the legend's source links are followable in the running app (v2.8).

`tests/test_presentation.py` asserts the anchors are in `figure.svg`. That is not
the same claim as "a reader can click it": `app.js` inlines the SVG into the
page, and an `<a xlink:href>` inside inline SVG is only useful if the browser
treats it as a link and nothing is drawn over it. v2.7.1 made the same
distinction for the tooltips and checked the live DOM rather than the file only.

So this does three things a file test cannot:

  1. counts the anchors in the *inlined* DOM and checks each one is the child of
     a `ringsource|<key>` group -- i.e. it wraps a source line and not something
     else;
  2. hit-tests each one, because an anchor with a figure element painted over it
     is not clickable and would pass every check that only reads markup. SVG
     text hits only on *painted* pixels, so a single `elementFromPoint` at the
     box centre lands between two characters and reports a false negative -- it
     did, on all nine, while the click below worked. The check samples a grid
     across the anchor's box and requires a run of hits;
  3. actually clicks one and waits for the tab it opens, then asserts the URL
     and closes it. One is enough to prove the mechanism; nine would be nine
     page loads against other people's servers for no more information.

Connects over CDP to the Chrome on 127.0.0.1:18800, the same way
`scripts/screenshots.py` does and for the same reason (see its docstring).

Run: .venv/bin/python scripts/verify_live_links.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.screenshots import APP, CDP, COUNCIL_LIST, _go  # noqa: E402
from targetring import sources  # noqa: E402

XLINK = "http://www.w3.org/1999/xlink"


def main() -> int:
    expected = {k: d.url for k, d in sources.RING_SOURCE_DOCS.items()}
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1680, "height": 1250})
        page.goto(APP, wait_until="networkidle")
        page.fill("#table", COUNCIL_LIST)
        _go(page, "Liver")

        found = page.evaluate(
            """() => {
              const out = [];
              document.querySelectorAll('#figure a').forEach((a) => {
                const g = a.closest('g[id^="ringsource|"]');
                // `elementFromPoint` is viewport-relative and returns null for a
                // point off screen, and the figure is much taller than the
                // viewport -- without this every probe read as "not the link".
                a.scrollIntoView({block: 'center', inline: 'center'});
                const box = a.getBoundingClientRect();
                let hits = 0, probes = 0, blockedBy = null;
                for (let fx = 0.04; fx < 1; fx += 0.02) {
                  for (let fy = 0.25; fy <= 0.75; fy += 0.25) {
                    probes++;
                    const el = document.elementFromPoint(
                      box.left + box.width * fx, box.top + box.height * fy);
                    if (el && (el === a || a.contains(el))) hits++;
                    else if (!blockedBy)
                      blockedBy = el ? el.tagName + (el.id ? '#' + el.id : '')
                                     : 'null (off screen?)';
                  }
                }
                out.push({
                  ring: g ? g.id.split('|')[1] : null,
                  href: a.getAttribute('xlink:href') || a.getAttribute('href'),
                  target: a.getAttribute('target'),
                  width: Math.round(box.width), height: Math.round(box.height),
                  hits, probes, blocked_by: blockedBy,
                });
              });
              return out;
            }"""
        )
        print(f"anchors in the inlined DOM: {len(found)} (expected {len(expected)})")
        by_ring = {}
        for row in found:
            print(f"  {str(row['ring']):<22} clickable at {row['hits']}/{row['probes']} "
                  f"probes  {row['width']}x{row['height']}px  target={row['target']}  "
                  f"{row['href']}")
            if row["ring"] is None:
                failures.append(f"an anchor is not inside a ringsource group: {row['href']}")
                continue
            by_ring[row["ring"]] = row
            # A whole source line reachable at fewer than a fifth of the probes
            # is not a link a reader can hit; the glyphs themselves account for
            # roughly half of any run of probes across a line of type.
            if row["hits"] * 5 < row["probes"]:
                failures.append(
                    f"{row['ring']}: reachable at only {row['hits']}/{row['probes']} "
                    f"points; first obstruction {row['blocked_by']}")
            if row["target"] != "_blank":
                failures.append(f"{row['ring']}: target is {row['target']!r}")

        if set(by_ring) != set(expected):
            failures.append(f"rings with a link: {sorted(by_ring)} != {sorted(expected)}")
        for ring, url in expected.items():
            got = (by_ring.get(ring) or {}).get("href")
            if got != url:
                failures.append(f"{ring}: href is {got!r}, registry says {url!r}")

        # The real proof: follow one.
        ring = "tdl"
        print(f"\nclicking the {ring} source line...")
        loc = page.locator(f'#figure g[id="ringsource|{ring}"] a')
        with ctx.expect_page() as popup:
            loc.click()
        new = popup.value
        new.wait_for_load_state("domcontentloaded", timeout=60_000)
        print(f"  opened: {new.url}")
        if not new.url.startswith(expected[ring]):
            failures.append(f"click on {ring} opened {new.url}, expected {expected[ring]}")
        body = new.inner_text("body")[:400000]
        for level in ("Tclin", "Tchem", "Tbio", "Tdark"):
            if level not in body:
                failures.append(f"the {ring} page does not define {level}")
        print(f"  page defines Tclin/Tchem/Tbio/Tdark: "
              f"{all(l in body for l in ('Tclin', 'Tchem', 'Tbio', 'Tdark'))}")
        new.close()
        page.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall live link checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
