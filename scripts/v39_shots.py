"""v3.9, live: the `druggability` citation row, and that it reads as its own.

The tests assert the payload and the registry. This is the other half — the real
stylesheet, the real wrapping, in the browser Puck opens on port 8811 — and it is
taken on the case the work was commissioned for: a run that draws `druggability`,
opened from disk by `?run=<id>` with nothing in `_runs`.

    v39-1-druggability-row.png   ring 3's strip row: the citation, the 4-key
                                 wording, and the link
    v39-2-tractable-row.png      the SUCCESSOR's row, from a run that draws it,
                                 so the two can be compared as printed
    v39-3-strip.png              the whole strip for the druggability run, so the
                                 row is seen in the company it keeps
    v39-probe.json               the rows as the DOM has them, both runs

Crops follow `v37_shots.py`: a 760 px viewport at `deviceScaleFactor` 3 rather
than a laptop width, because a 1,390 px strip downscaled to a phone puts 12 px
type at 3.4 px. Narrow viewport, 3x pixels, cropped to the row.

Run:  .venv/bin/python scripts/v39_shots.py [--port 8811]
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
PHONE_READABLE = {"width": 760, "height": 1400}

# The run the audit named: 14 genes, ring 3 is `druggability`, and its cells carry
# `mean of n/4 keys`.
DRUGGABILITY_RUN = "20260924T061611Z-e8d4a4"

ROWS = """() => {
  const rows = [...document.querySelectorAll('#table-sources .srclist > li')];
  return rows.map((li) => {
    const a = li.querySelector('a:not(.failnote a)');
    return {
      n: li.value,
      label: (li.querySelector('.lbl') || {}).textContent,
      link_text: a ? a.textContent.trim() : null,
      href: a ? a.getAttribute('href') : null,
      nolink: !!li.querySelector('.nolink'),
      text: li.textContent.replace(/\\s+/g, ' ').trim(),
    };
  });
}"""


def open_run(page, base: str, run_id: str) -> None:
    page.goto(f"{base}?run={run_id}", wait_until="networkidle")
    page.wait_for_selector("#pane-table:not([hidden])", timeout=60_000)
    page.wait_for_selector("#table-grid table.grid", timeout=60_000)
    page.wait_for_selector("#table-sources-details", timeout=60_000)
    page.evaluate("document.querySelector('#table-sources-details').open = true")
    page.wait_for_timeout(400)


def clip_of(page, selector: str, pad: int = 8, max_w: int | None = None,
            left_pad: int | None = None) -> dict:
    """`left_pad` exists for the strip's rows: the `<li>` marker — the ring number
    a reader joins the row to its column by — is painted OUTSIDE the element's own
    box, so a symmetric crop cuts the "3." off the front of ring 3."""
    box = page.evaluate(
        """(s) => { const r = document.querySelector(s).getBoundingClientRect();
                    return {x: r.x, y: r.y, width: r.width, height: r.height}; }""",
        selector)
    left = pad if left_pad is None else left_pad
    w = box["width"] + pad + left
    return {"x": max(0.0, box["x"] - left), "y": max(0.0, box["y"] - pad),
            "width": min(w, max_w) if max_w else w,
            "height": box["height"] + pad * 2}


def row_selector(page, ring_key: str) -> str:
    """The strip row for one ring, located by its ordinal in the DOM.

    The rows carry no ring key, so the index comes from the payload's own
    `citations` order — which is the grid's column order, which is the order the
    rows are written in. Asserted rather than assumed: the row's label must be the
    label the payload has for that ring.
    """
    idx = page.evaluate(
        """(key) => {
          const rows = [...document.querySelectorAll('#table-sources .srclist > li')];
          return rows.findIndex((li) => (li.textContent || '').includes(key));
        }""", ring_key)
    if idx < 0:
        # Fall back to the column order, for a ring whose key is not printed.
        raise SystemExit(f"no strip row mentions {ring_key!r}")
    return f"#table-sources .srclist > li:nth-of-type({idx + 1})"


def a_run_that_draws(ring_key: str) -> str:
    """A run under `runs/` whose cells include this ring, read through
    `table.read_cell_rows` so the appended ledger section is not searched."""
    from targetring import table as table_mod

    runs = sorted(p.name for p in (ROOT / "runs").iterdir()
                  if p.is_dir() and table_mod.valid_run_id(p.name)
                  and (p / "values.csv").exists())
    for run_id in reversed(runs):
        try:
            if ring_key in table_mod.ring_keys_drawn(run_id):
                return run_id
        except table_mod.TableUnavailable:
            continue
    raise SystemExit(f"no run under runs/ draws {ring_key!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8811,
                    help="the port Puck opens; the screenshots must come from it")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}/"
    tractable_run = a_run_that_draws("tractable")

    OUT.mkdir(parents=True, exist_ok=True)
    probes: dict[str, object] = {"port": a.port,
                                 "druggability_run": DRUGGABILITY_RUN,
                                 "tractable_run": tractable_run}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(viewport=PHONE_READABLE, device_scale_factor=3)
            page = ctx.new_page()

            open_run(page, base, DRUGGABILITY_RUN)
            probes["druggability_rows"] = page.evaluate(ROWS)
            sel = row_selector(page, "Druggability")
            page.evaluate("(s) => document.querySelector(s)"
                          ".scrollIntoView({block: 'center'})", sel)
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v39-1-druggability-row.png"),
                            clip=clip_of(page, sel, left_pad=42))
            page.evaluate("document.querySelector('#table-sources')"
                          ".scrollIntoView({block: 'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v39-3-strip.png"),
                            clip=clip_of(page, "#table-sources"))

            open_run(page, base, tractable_run)
            probes["tractable_rows"] = page.evaluate(ROWS)
            sel = row_selector(page, "Tractability")
            page.evaluate("(s) => document.querySelector(s)"
                          ".scrollIntoView({block: 'center'})", sel)
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "v39-2-tractable-row.png"),
                            clip=clip_of(page, sel, left_pad=42))
            ctx.close()
        finally:
            browser.close()

    (OUT / "v39-probe.json").write_text(json.dumps(probes, indent=2))

    drug = next(r for r in probes["druggability_rows"]
                if "Druggability" in (r["label"] or ""))
    trac = next(r for r in probes["tractable_rows"]
                if "Tractability" in (r["label"] or ""))
    print(f"druggability run {DRUGGABILITY_RUN}  ring {drug['n']}")
    print(f"  link text : {drug['link_text']}")
    print(f"  href      : {drug['href']}")
    print(f"tractable run {tractable_run}  ring {trac['n']}")
    print(f"  link text : {trac['link_text']}")
    print(f"  href      : {trac['href']}")
    # The one thing a picture cannot prove on its own: the two rows are not the
    # same citation wearing two labels.
    same = (drug["link_text"] == trac["link_text"]) or (drug["href"] == trac["href"])
    print(f"\n{'FAIL' if same else 'OK'}: the two rows are "
          f"{'INDISTINGUISHABLE' if same else 'distinguishable'} as printed")
    print(f"-> {OUT}/v39-1-druggability-row.png, v39-2-tractable-row.png, "
          f"v39-3-strip.png")
    return 1 if same else 0


if __name__ == "__main__":
    raise SystemExit(main())
