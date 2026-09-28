"""v3.5, live: the same run drawn in two windows, and what the page does with it.

The sweep (`scripts/ux_v3_web_fit.py --windows`, `--bisect`) measures the canvas
and the assertions. This measures the thing the sweep cannot: a real browser, the
real stylesheet, the panel width the flex row actually produces, and the page's
own resize path. Four frames and a JSON probe:

    v35-1-laptop.png    1440 x 900  -- the 26 deg gap, empty, as round 2 left it
    v35-2-big.png       the big window, same run, ring names in the gap
    v35-3-big-wedge.png the gap, cropped, so the names can be read in the shot
    v35-4-laptop-again.png  back to the laptop: the names go away again

Run: .venv/bin/python scripts/v35_shots.py [--big WxH]

Nothing here is a source of truth about whether the names FIT -- the render's own
assertions decide that and refuse to draw them otherwise. This is the check that
the page asks for the right canvas and shows what came back.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.screenshots import APP, CDP, COUNCIL_LIST  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
LAPTOP = {"width": 1440, "height": 900}
# 1440p with the browser full screen: the smallest window in the sweep that earns
# the names. The threshold is a circle of 1,006 CSS px, which needs both a panel
# of at least 1,324 px and 1,413 px of window height.
BIG = {"width": 2560, "height": 1440}

PROBE = """() => {
  const fig = document.querySelector('#figure');
  const svg = fig && fig.querySelector('svg');
  // The size a reader gets is the size in the file times the factor the browser
  // scales the whole SVG by (`#figure svg { width: 100% }`), which is the panel
  // over the viewBox width -- the same arithmetic as `scripts/ux_v3_measure.py`.
  // `getComputedStyle` alone reports the user-unit size and would flatter the app
  // by a third, which is the one direction this instrument must never be wrong in.
  const vb = svg ? svg.getAttribute('viewBox').split(/\\s+/).map(Number) : null;
  const shown = vb ? fig.getBoundingClientRect().width / vb[2] : 1;
  const names = [...fig.querySelectorAll('[id^="ringname|"]')].map((g) => {
    const t = g.querySelector('text');
    const size = t ? parseFloat(getComputedStyle(t).fontSize) : null;
    return {id: g.id, text: (t ? t.textContent : '').trim(),
            pt_in_the_file: size, css_px: size === null ? null : size * shown};
  });
  const caveats = [...document.querySelectorAll('#figure-caveats span')]
    .map((s) => s.className + ': ' + s.textContent.trim());
  const aside = document.querySelector('#wedge-note');
  return {
    window: [window.innerWidth, window.innerHeight],
    panel_px: Math.round(fig.getBoundingClientRect().width * 10) / 10,
    figure_px: Math.round(fig.getBoundingClientRect().height * 10) / 10,
    svg_viewbox: svg ? svg.getAttribute('viewBox') : null,
    ring_names: names,
    n_ring_names: names.length,
    // `let RUN` is a global lexical binding, not a property of `window`.
    web_render: (typeof RUN !== 'undefined' && RUN) ? RUN.web_render : null,
    caveats,
    wedge_note_beside_the_figure: aside ? aside.textContent.trim() : null,
    caveats_top_px: (() => {
      const el = document.querySelector('#figure-caveats');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + window.scrollY);
    })(),
  };
}"""


def run_once(page, table: str, region: str = "Liver") -> None:
    page.goto(APP, wait_until="networkidle")
    page.fill("#table", table)
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=300_000)
    page.wait_for_selector("#figure svg", timeout=300_000)
    page.wait_for_timeout(1500)


def settle_after_resize(page) -> None:
    """The page redraws when the resize settles (450 ms), and the redraw is a
    render on the server. Waited for by the status line going back, rather than by
    a fixed sleep that would pass whether anything happened or not."""
    page.wait_for_timeout(1200)
    page.wait_for_function(
        "() => !document.querySelector('#status').textContent.includes('redrawing')",
        timeout=120_000)
    page.wait_for_timeout(800)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--big", default=f"{BIG['width']}x{BIG['height']}")
    a = p.parse_args()
    bw, bh = (int(x) for x in a.big.lower().split("x"))
    big = {"width": bw, "height": bh}

    OUT.mkdir(parents=True, exist_ok=True)
    probes = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        print(f"1/4 the laptop: {LAPTOP['width']} x {LAPTOP['height']}")
        page.set_viewport_size(LAPTOP)
        run_once(page, COUNCIL_LIST)
        page.evaluate("document.querySelector('#figure').scrollIntoView({block:'start'})")
        page.wait_for_timeout(500)
        probes["laptop"] = page.evaluate(PROBE)
        page.screenshot(path=str(OUT / "v35-1-laptop.png"))

        print(f"2/4 the same run, resized to {bw} x {bh}")
        page.set_viewport_size(big)
        settle_after_resize(page)
        page.evaluate("document.querySelector('#figure').scrollIntoView({block:'start'})")
        page.wait_for_timeout(500)
        probes["big"] = page.evaluate(PROBE)
        page.screenshot(path=str(OUT / "v35-2-big.png"))

        print("3/4 the gap, cropped, so the twelve names are legible in the shot")
        box = page.evaluate("""() => {
          const f = document.querySelector('#figure').getBoundingClientRect();
          return {x: f.x, y: f.y, w: f.width, h: f.height};
        }""")
        page.screenshot(path=str(OUT / "v35-3-big-wedge.png"), clip={
            "x": box["x"] + box["w"] * 0.30, "y": max(box["y"], 0),
            "width": box["w"] * 0.40, "height": min(box["h"] * 0.45, 700)})

        print("4/4 back to the laptop: the names have to go away again")
        page.set_viewport_size(LAPTOP)
        settle_after_resize(page)
        page.evaluate("document.querySelector('#figure').scrollIntoView({block:'start'})")
        page.wait_for_timeout(500)
        probes["laptop_again"] = page.evaluate(PROBE)
        page.screenshot(path=str(OUT / "v35-4-laptop-again.png"))
        page.close()

    (ROOT / "logs" / "v35-live-probe.json").write_text(json.dumps(probes, indent=2))
    for where, pr in probes.items():
        wr = pr.get("web_render") or {}
        print(f"\n{where}: window {pr['window']}  panel {pr['panel_px']} px  "
              f"figure {pr['figure_px']} px  viewBox {pr['svg_viewbox']}")
        print(f"  canvas {wr.get('canvas_in')}  circle {wr.get('circle_px')} px  "
              f"type {wr.get('type_px')} px  ring names in the figure: "
              f"{wr.get('ring_names_in_figure')} ({pr['n_ring_names']} drawn)")
        if pr["ring_names"]:
            sizes = [n["css_px"] for n in pr["ring_names"]]
            print(f"  smallest name on screen: {min(sizes):.2f} CSS px")
        for c in pr["caveats"]:
            print(f"  caveat  {c[:110]}")
        if pr["wedge_note_beside_the_figure"]:
            print(f"  beside the figure: {pr['wedge_note_beside_the_figure'][:110]}")
    print(f"\nwrote {OUT}/v35-*.png and logs/v35-live-probe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
