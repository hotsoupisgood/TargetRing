"""v3.7, live: the citation strip and the three-line column heads, in a browser.

The tests assert the payload and the markup. This is the other half — the real
stylesheet, the real wrapping, the real header height — and it is taken on the
case that was broken: a run opened from disk by `?run=<id>`, with no figure in the
session and nothing in `_runs`.

    v37-0-page.png        1440 x 900, the table tab as a reader meets it
    v37-1-heads.png       the column heads, cropped: number, ring name, source
    v37-2-sources.png     "Where these N columns come from", the whole strip
    v37-3-failed.png      a run with a FAILED cell: the cell, and the strip row
                          that says the column's source link does not answer it
    v37-probe.json        computed sizes, header cost, and the two targets

Crops are taken at `deviceScaleFactor` 3 in a 760 px viewport rather than at
1440 px: Puck reads these on a phone, and a 1,390 px-wide strip downscaled to a
390 px screen puts 12 px type at 3.4 px. Narrow viewport, 3x pixels, cropped to
the element — so the text is ~7 px on the phone before he zooms, and crisp after.

Run:  .venv/bin/python scripts/v37_shots.py --run <run_id> [--port 8837]
The server is expected to be up already; this script starts nothing and fetches
no source (AGENTS.md rules 8 and 9).
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
LAPTOP = {"width": 1440, "height": 900}
# Narrow enough that a crop of it is still readable on a phone; wide enough that
# the strip's rows do not wrap into a column of single words.
PHONE_READABLE = {"width": 760, "height": 1400}

PROBE = """() => {
  const px = (el, prop) => el ? parseFloat(getComputedStyle(el)[prop]) : null;
  const th = [...document.querySelectorAll('#table-grid thead th')];
  const src = [...document.querySelectorAll('#table-grid thead th .src')];
  const strip = document.querySelector('#table-sources');
  const details = document.querySelector('#table-sources-details');
  const scroller = document.querySelector('#table-grid');
  const links = [...document.querySelectorAll('#table-sources a')];
  const failed = [...document.querySelectorAll('#table-grid td.st-failed')];
  const nodata = [...document.querySelectorAll('#table-grid td.st-no_data')];
  return {
    // The page got here without an analyse and without `_runs`.
    in_session_figure: typeof RUN !== 'undefined' && RUN !== null,
    run_in_url: new URLSearchParams(location.search).get('run'),
    table_tab_active: !document.querySelector('#pane-table').hidden,

    // Item 2: the third header line, its size, and what it costs.
    n_columns: th.length,
    n_source_labels: src.length,
    source_label_texts: src.map((s) => s.textContent.trim()),
    source_label_px: src.length ? px(src[0], 'fontSize') : null,
    source_label_is_an_anchor: src.some((s) => s.closest('a') !== null),
    source_label_colour: src.length ? getComputedStyle(src[0]).color : null,
    header_height_px: th.length
      ? Math.round(th[0].getBoundingClientRect().height * 10) / 10 : null,
    source_line_cost_px: src.length
      ? Math.round((src[0].getBoundingClientRect().height
                    + parseFloat(getComputedStyle(src[0]).marginTop)) * 10) / 10
      : null,
    column_widths_px: th.slice(2).map(
      (e) => Math.round(e.getBoundingClientRect().width)),
    // Item 4: what the head calls each ring, without the source line.
    head_names: th.slice(2).map((e) => {
      const c = e.cloneNode(true);
      [...c.querySelectorAll('.src, .krank')].forEach((x) => x.remove());
      return c.textContent.trim();
    }),

    // Item 1: the strip, where it is, and that it is not inside the scroller.
    strip_summary: details ? details.querySelector('summary').textContent
      .replace(/\\s+/g, ' ').trim() : null,
    strip_open: details ? details.open : null,
    strip_inside_the_scroller: !!(strip && scroller && scroller.contains(strip)),
    strip_above_the_ledger: !!(strip && document.querySelector('#table-ledger')
      && strip.compareDocumentPosition(document.querySelector('#table-ledger')) & 4),
    strip_height_px: strip
      ? Math.round(strip.getBoundingClientRect().height) : null,
    grid_height_px: scroller
      ? Math.round(scroller.getBoundingClientRect().height) : null,
    strip_rows: [...document.querySelectorAll('#table-sources .srclist > li')]
      .map((li) => {
        const a = li.querySelector('a:not(.failnote a)');
        return {n: li.value,
                label: (li.querySelector('.lbl') || {}).textContent,
                units: (li.querySelector('.un') || {}).textContent,
                link_text: a ? a.textContent.trim() : null,
                href: a ? a.getAttribute('href') : null,
                nolink: !!li.querySelector('.nolink'),
                failed_href: (li.querySelector('.failnote a') || {})
                  .getAttribute ? li.querySelector('.failnote a')
                    .getAttribute('href') : null};
      }),
    n_links_in_the_strip: links.length,
    // Item 3, as the reader meets it: two states, two hovers, two targets.
    n_failed_cells: failed.length,
    n_no_data_cells: nodata.length,
    failed_cell_title: failed.length ? failed[0].getAttribute('title') : null,
    no_data_cell_title: nodata.length ? nodata[0].getAttribute('title') : null,
    // The hard constraint: no cell is a link, on any state.
    cells_that_are_links: [...document.querySelectorAll('#table-grid td a')].length,
  };
}"""


def open_run(page, base: str, run_id: str) -> None:
    """The disk path, exactly as a reader arrives by it."""
    page.goto(f"{base}?run={run_id}", wait_until="networkidle")
    page.wait_for_selector("#pane-table:not([hidden])", timeout=60_000)
    page.wait_for_selector("#table-grid table.grid", timeout=60_000)
    page.wait_for_selector("#table-sources-details", timeout=60_000)
    page.evaluate("document.querySelector('#table-sources-details').open = true")
    page.wait_for_timeout(400)


def clip_of(page, selector: str, pad: int = 8, max_w: int | None = None) -> dict:
    box = page.evaluate(
        """(s) => { const r = document.querySelector(s).getBoundingClientRect();
                    return {x: r.x, y: r.y, width: r.width, height: r.height}; }""",
        selector)
    w = box["width"] + pad * 2
    return {"x": max(0.0, box["x"] - pad), "y": max(0.0, box["y"] - pad),
            "width": min(w, max_w) if max_w else w,
            "height": box["height"] + pad * 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="a run id under runs/")
    ap.add_argument("--failed-run", default="QA-V2-V3-failA",
                    help="a run with a FAILED cell")
    ap.add_argument("--port", type=int, default=8837)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}/"

    OUT.mkdir(parents=True, exist_ok=True)
    probes: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # 1. The page as a reader meets it, at the laptop this review measures.
            ctx = browser.new_context(viewport=LAPTOP, device_scale_factor=2)
            page = ctx.new_page()
            open_run(page, base, a.run)
            probes["laptop"] = page.evaluate(PROBE)
            page.screenshot(path=str(OUT / "v37-0-page.png"), full_page=True)
            ctx.close()

            # 2 and 3. The crops, narrow and at 3x, so they survive a phone.
            ctx = browser.new_context(viewport=PHONE_READABLE, device_scale_factor=3)
            page = ctx.new_page()
            open_run(page, base, a.run)
            probes["phone_readable"] = page.evaluate(PROBE)

            # Two shots of the same thing, because they answer different
            # questions on a phone. The tight one is six columns and is legible
            # without zooming; the wide one is all twelve, for the shape of the
            # header, and is 3x so it stays crisp when he pinches it.
            page.evaluate(
                "document.querySelector('#table-grid').scrollIntoView({block:'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v37-1-heads.png"),
                            clip=clip_of(page, "#table-grid thead", max_w=620))
            ctx.close()

            ctx = browser.new_context(viewport={"width": 1700, "height": 1100},
                                      device_scale_factor=3)
            page = ctx.new_page()
            open_run(page, base, a.run)
            page.evaluate(
                "document.querySelector('#table-grid').scrollIntoView({block:'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v37-1b-heads-all-12.png"),
                            clip=clip_of(page, "#table-grid thead"))
            ctx.close()

            ctx = browser.new_context(viewport=PHONE_READABLE, device_scale_factor=3)
            page = ctx.new_page()
            open_run(page, base, a.run)

            page.evaluate(
                "document.querySelector('#table-sources').scrollIntoView({block:'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v37-2-sources.png"),
                            clip=clip_of(page, "#table-sources"))

            # 4. The run with a FAILED cell, so item 3 is in a picture.
            open_run(page, base, a.failed_run)
            probes["failed_run"] = page.evaluate(PROBE)
            page.evaluate("""() => {
              const td = document.querySelector('#table-grid td.st-failed');
              if (td) td.scrollIntoView({block: 'center', inline: 'center'});
            }""")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v37-3-failed-grid.png"),
                            clip=clip_of(page, "#table-grid", max_w=740))
            page.evaluate(
                "document.querySelector('#table-sources').scrollIntoView({block:'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v37-3-failed.png"),
                            clip=clip_of(page, "#table-sources"))
            ctx.close()
        finally:
            browser.close()

    (ROOT / "logs").mkdir(exist_ok=True)
    (OUT / "v37-probe.json").write_text(json.dumps(probes, indent=2))
    lap = probes["laptop"]
    print(f"columns {lap['n_columns']}  source labels {lap['n_source_labels']}"
          f"  at {lap['source_label_px']} px  anchor={lap['source_label_is_an_anchor']}")
    print(f"header {lap['header_height_px']} px, of which the source line is "
          f"{lap['source_line_cost_px']} px")
    print(f"grid {lap['grid_height_px']} px + strip {lap['strip_height_px']} px")
    print(f"strip inside the scroller: {lap['strip_inside_the_scroller']}  "
          f"above the ledger: {lap['strip_above_the_ledger']}  "
          f"links: {lap['n_links_in_the_strip']}  "
          f"cell links: {lap['cells_that_are_links']}")
    print(f"in-session figure: {lap['in_session_figure']}  (this is the disk path)")
    fr = probes["failed_run"]
    print(f"failed run: {fr['n_failed_cells']} failed / {fr['n_no_data_cells']} "
          f"no-data cells")
    print(f"  failed hover:  {str(fr['failed_cell_title'])[:110]}")
    print(f"  no-data hover: {str(fr['no_data_cell_title'])[:110]}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
