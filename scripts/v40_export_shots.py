"""v4.0, live: the offline export, opened from `file://`, with nothing serving it.

This is the acceptance test for the export, not an illustration of it. The tests in
`tests/test_export.py` assert the file's bytes; this drives the real thing in a real
browser from a `file://` URL and answers the four questions that decide whether Puck
can use it on another machine:

  1. **Does it make any network request at all?** Every request the page attempts is
     recorded, including the ones a browser makes for you. The assertion is the
     strong form: exactly ONE request, and it is the document itself.
  2. **Does the grid render?** Fourteen rows, every ring a column, no blank cell.
  3. **Does sorting work, including shift-click multi-key?** Clicked, not simulated:
     the same `th.onclick` the app wires, over the grades this build froze.
  4. **Does the dim filter work?** Set through its own control, and the count line
     read back off the page.

     v40-1-page.png            the whole export: provenance, self-check, grid
     v40-2-sorted.png          sorted by one column, with the no-value divider
     v40-3-multikey.png        two keys, both chips, the second added by shift-click
     v40-4-dimmed.png          a dim filter active: three treatments and the count line
     v40-5-retired-ring.png    the older run, whose ring 3 is the retired `druggability`
     v40-probe.json            requests, self-check verdict, row orders, count line

Run:  .venv/bin/python scripts/v40_export_shots.py
It starts no server and needs none — that is the point. It fetches no source and
spends nothing (AGENTS.md rules 8 and 9).
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
EXPORTS = ROOT / "docs" / "exports"

# Narrow viewport at 3x pixels, for the same reason `v37_shots.py` gives: Puck reads
# these on a phone, and a 1,390 px page downscaled to a phone puts 12 px type at
# 3.4 px. `UX-v3-rounds.md` B12's floor is about what is actually legible.
PHONE_READABLE = {"width": 820, "height": 1500}

NEWEST = "20260928T044236Z-893dec"
RETIRED_RING_RUN = "20260924T061611Z-e8d4a4"

# The two keys the multi-key frame uses, and they are not arbitrary: on this run
# `tractable` has ties AND a no-value block, so `logfc` as a second key both breaks
# ties among the measured genes and orders the unplaceable block within itself.
# Chosen by asking Python which pairs reorder at all; 64 of them do.
SORT_1 = "ring:tractable"
SORT_2 = "ring:logfc"

# All three dim treatments at once: 6 match, 5 have a value and miss, 3 have no
# value on this column and are therefore neither.
DIM_RING = "regional_specificity"
DIM_THRESHOLD = "1.6"

GRID = """() => {
  const rows = [...document.querySelectorAll('#table-grid tbody tr')]
    .filter((tr) => !tr.classList.contains('missing-divider'));
  const heads = [...document.querySelectorAll('#table-grid thead th')]
    .map((th) => th.textContent.replace(/\\s+/g, ' ').trim());
  return {
    heads: heads,
    genes: rows.map((tr) => (tr.querySelector('.gene') || {}).textContent),
    blank_cells: rows.reduce((n, tr) => n + [...tr.querySelectorAll('td')]
      .filter((td) => !td.textContent.trim()).length, 0),
    state_words: [...new Set([...document.querySelectorAll('#table-grid td')]
      .map((td) => td.className))].sort(),
    divider: (document.querySelector('.missing-divider td') || {}).textContent,
    sort_chips: [...document.querySelectorAll('#table-sort .key')]
      .map((k) => k.textContent.replace(/\\s+/g, ' ').trim()),
    sort_note: (document.querySelector('#table-sort .hint') || {}).textContent
      .replace(/\\s+/g, ' ').trim(),
    dim_tally: (document.querySelector('#table-dim-counts .tally') || {}).textContent,
    dim_note: (document.querySelector('#table-dim-counts p') || {}).textContent,
    dim_tags: [...document.querySelectorAll('#table-grid .dimtag')]
      .map((t) => t.textContent),
    selfcheck: document.querySelector('#export-selfcheck').textContent
      .replace(/\\s+/g, ' ').trim().slice(0, 240),
    selfcheck_failed: !!document.querySelector('#export-selfcheck .banner-bad'),
    drawer_buttons: document.querySelectorAll('#table-grid button.gene').length,
    citations: document.querySelectorAll('#table-sources .srclist > li').length,
    ledger_rows: document.querySelectorAll('#table-ledger tbody tr').length,
    invariants: document.querySelectorAll('#invariants tbody tr').length,
  };
}"""


def shot(page, name: str, selector: str | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    if selector:
        page.locator(selector).screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=True)
    print(f"  wrote {path.relative_to(ROOT)}")


def band(page, name: str, top_sel: str, bottom_sel: str, rows: int | None = None,
         pad: int = 10) -> None:
    """A crop from the top of one element to the bottom of another.

    Puck reads these on a phone. A full-page shot of this export is ~11,000 px
    tall, and a picture of everything at once is a picture of nothing: downscaled
    to a phone it puts the 12 px floor `UX-v3-rounds.md` B12 exists to protect at
    under 3 px. So the frames are bands, at 3x pixels, each showing one claim.
    """
    a = page.locator(top_sel).first.bounding_box()
    if rows is not None:
        rs = page.locator("#table-grid tbody tr")
        n = min(rows, rs.count()) - 1
        b = rs.nth(n).bounding_box()
    else:
        b = page.locator(bottom_sel).first.bounding_box()
    clip = {"x": max(0, a["x"] - pad), "y": max(0, a["y"] - pad),
            "width": min(page.viewport_size["width"], a["width"] + 2 * pad),
            "height": (b["y"] + b["height"]) - a["y"] + 2 * pad}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    # `full_page`, or the clip is silently truncated at the bottom of the
    # viewport and the frame is a picture of the top of what was asked for.
    page.screenshot(path=str(path), clip=clip, full_page=True)
    print(f"  wrote {path.relative_to(ROOT)}  ({clip['height']:.0f} px tall)")


def open_export(ctx, path: Path):
    """Open the file, recording every request the page attempts."""
    page = ctx.new_page()
    requests: list[dict[str, str]] = []
    page.on("request", lambda r: requests.append(
        {"url": r.url, "resource": r.resource_type, "method": r.method}))
    console: list[str] = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
    page.goto(path.as_uri())
    page.wait_for_selector("#table-grid table.grid", timeout=30_000)
    page.wait_for_timeout(300)
    return page, requests, console


def th_for(page, ordering: str):
    return page.locator(f'#table-grid thead th[data-ordering="{ordering}"]')


# Every cell as text, in row and column order. The one comparison that answers
# "does the export agree with the app": not the payload, which is the same function
# on both sides by construction, but the GRID A READER SEES, drawn by the app's
# `renderTable` in two documents that reached it by different routes.
CELLS = """() => [...document.querySelectorAll('#table-grid tbody tr')]
  .filter((tr) => !tr.classList.contains('missing-divider'))
  .map((tr) => [...tr.querySelectorAll('th,td')]
    .map((c) => c.className + '=' + c.textContent.replace(/\\s+/g, ' ').trim())
    .join(' | '))"""


def compare_with_the_app(ctx, base: str, run_id: str, sort: str,
                         export_cells: list[str]) -> dict[str, object]:
    """Open the SAME run at the SAME sort in the served app and diff the grid.

    The export and the app build their payload with one function and draw it with
    one renderer, so this should be identical — which is exactly why it is worth
    asking. A difference here is the failure mode the whole design is arranged
    against, and it would not show up in any test of either side alone.
    """
    page = ctx.new_page()
    page.goto(f"{base}?run={run_id}", wait_until="networkidle")
    page.wait_for_selector("#table-grid table.grid", timeout=60_000)
    for ordering in [s for s in sort.split(",") if s]:
        page.locator(f'#table-grid thead th[data-ordering="{ordering}"]').click(
            modifiers=["Shift"] if ordering != sort.split(",")[0] else [])
        page.wait_for_timeout(250)
    app_cells = page.evaluate(CELLS)
    page.close()
    same = app_cells == export_cells
    return {"base": base, "run_id": run_id, "sort": sort, "identical": same,
            "app_rows": len(app_cells),
            "first_difference": next(
                (f"row {i}: app {a!r} vs export {b!r}"
                 for i, (a, b) in enumerate(zip(app_cells, export_cells)) if a != b),
                "" if same else "row counts differ")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--newest", default=NEWEST)
    ap.add_argument("--retired", default=RETIRED_RING_RUN)
    ap.add_argument("--compare-app", default="",
                    help="base URL of the running app, e.g. http://127.0.0.1:8811. "
                         "Optional: the export itself needs no server, and this is "
                         "the cross-check that it agrees with one.")
    args = ap.parse_args()

    probe: dict[str, object] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=PHONE_READABLE, device_scale_factor=3)

        path = EXPORTS / f"targetring-{args.newest}-table.html"
        page, requests, console = open_export(ctx, path)
        probe["file"] = path.name
        probe["file_bytes"] = path.stat().st_size

        # 1. the question the export lives or dies on.
        probe["requests"] = requests
        offsite = [r for r in requests if not r["url"].startswith("file:")]
        probe["network_requests"] = offsite
        probe["console"] = console

        probe["opened"] = page.evaluate(GRID)
        shot(page, "v40-1-page.png")
        # What the file says it is and what it says it is not, in one frame: the
        # provenance stamp and the self-check verdict are the two things that make
        # a detached artefact auditable rather than merely plausible.
        band(page, "v40-6-provenance.png", "#export-selfcheck", "#export-provenance")
        band(page, "v40-7-states.png", "#table-grid", "", rows=6)

        # 3. click-to-sort, on a ring column. `tractable` is chosen because it has
        # ties and a no-value block, so the second key below has something to do.
        th_for(page, SORT_1).click()
        page.wait_for_timeout(250)
        probe["one_key"] = page.evaluate(GRID)
        shot(page, "v40-2-sorted.png", "#result")

        # 3b. shift-click ADDS a second key rather than replacing the first, and
        # the pair is one where the second key demonstrably moves rows -- a
        # screenshot of a multi-key sort that happened to be the single-key order
        # would be a picture of nothing.
        th_for(page, SORT_2).click(modifiers=["Shift"])
        page.wait_for_timeout(250)
        probe["two_keys"] = page.evaluate(GRID)
        shot(page, "v40-3-multikey.png", "#result")
        band(page, "v40-8-multikey-band.png", "#table-sort", "", rows=7)

        # 4. the dim filter, through its own control. This column has all three
        # treatments on this run: genes that match, genes with a value that miss,
        # and genes with no value on it at all, which are neither.
        page.select_option("#dim-col", DIM_RING)
        page.select_option("#dim-op", "ge")
        page.fill("#dim-thr", DIM_THRESHOLD)
        page.click("#dim-add")
        page.wait_for_timeout(250)
        probe["dimmed"] = page.evaluate(GRID)
        shot(page, "v40-4-dimmed.png", "#result")
        band(page, "v40-9-dim-band.png", "#table-dim-counts", "", rows=8)

        # Clearing restores the grid rather than rebuilding it.
        page.click("#dim-clear")
        page.wait_for_timeout(250)
        probe["dim_cleared"] = page.evaluate(GRID)

        # Nothing was requested by any of that, either.
        probe["requests_after_interaction"] = requests

        # Clearing the dim filter left the two-key sort in place -- dimming is view
        # state and touches no order -- so this grid is still `SORT_1, SORT_2`, and
        # it is the one the served app is asked to reproduce.
        export_cells = page.evaluate(CELLS)
        probe["export_cells"] = export_cells
        page.close()

        if args.compare_app:
            probe["vs_app"] = compare_with_the_app(
                ctx, args.compare_app, args.newest, f"{SORT_1},{SORT_2}",
                export_cells)

        # 5. the other schema era: ring 3 is the retired `druggability`.
        path2 = EXPORTS / f"targetring-{args.retired}-table.html"
        page2, req2, console2 = open_export(ctx, path2)
        probe["retired"] = {
            "file": path2.name, "file_bytes": path2.stat().st_size,
            "requests": req2,
            "network_requests": [r for r in req2 if not r["url"].startswith("file:")],
            "console": console2,
            "grid": page2.evaluate(GRID),
            # Column 3 is the fifth `th`: Gene, k, ring 1, ring 2, ring 3.
            "ring3": page2.evaluate(
                "() => document.querySelectorAll('#table-grid thead th')[4]"
                ".textContent.replace(/\\s+/g,' ').trim()"),
            "ring3_citation": page2.evaluate(
                "() => document.querySelectorAll('#table-sources .srclist > li')[2]"
                ".textContent.replace(/\\s+/g,' ').trim()"),
        }
        shot(page2, "v40-5-retired-ring.png", "#result")
        page2.close()
        browser.close()

    (OUT / "v40-probe.json").write_text(json.dumps(probe, indent=2))
    print(f"  wrote {(OUT / 'v40-probe.json').relative_to(ROOT)}")

    bad = []
    for label, reqs in (("newest", probe["requests_after_interaction"]),
                        ("retired", probe["retired"]["requests"])):
        off = [r for r in reqs if not r["url"].startswith("file:")]
        if off:
            bad.append(f"{label}: {len(off)} NETWORK request(s): "
                       + ", ".join(r["url"] for r in off[:5]))
        if len(reqs) != 1:
            bad.append(f"{label}: {len(reqs)} request(s) in total, expected 1 "
                       f"(the document): " + ", ".join(r["url"][-60:] for r in reqs))
    if probe["opened"]["selfcheck_failed"] or probe["retired"]["grid"]["selfcheck_failed"]:
        bad.append("the export's own self-check FAILED in the browser")
    if probe["opened"]["blank_cells"]:
        bad.append(f"{probe['opened']['blank_cells']} blank cell(s) in the grid")
    if probe["opened"]["drawer_buttons"]:
        bad.append("the export drew a gene as a clickable button; there is no drawer")
    if probe["one_key"]["genes"] == probe["two_keys"]["genes"]:
        bad.append("shift-click did not change the order, so this run cannot show "
                   "that the second key did anything -- pick another pair")
    if len(probe["two_keys"]["sort_chips"]) != 2:
        bad.append(f"shift-click left {len(probe['two_keys']['sort_chips'])} sort "
                   f"key(s), not 2: it replaced the first instead of adding to it")
    if probe["dim_cleared"]["dim_tags"]:
        bad.append("a dim treatment survived clearing the filter")
    if set(probe["dimmed"]["dim_tags"]) != {"dimmed", "cannot be evaluated"}:
        bad.append(f"the dim frame does not show both non-match treatments: "
                   f"{sorted(set(probe['dimmed']['dim_tags']))}")
    if "vs_app" in probe and not probe["vs_app"]["identical"]:
        bad.append("THE EXPORT AND THE APP DREW DIFFERENT GRIDS: "
                   + str(probe["vs_app"]["first_difference"]))

    print()
    if bad:
        for b in bad:
            print("FAIL " + b)
        return 1
    print(f"PASS  1 request per file and it is the file itself; "
          f"self-check PASS on both; "
          f"{len(probe['opened']['genes'])} rows, "
          f"{probe['opened']['citations']} citations, "
          f"{probe['opened']['invariants']} invariants")
    print("      dim: " + " ".join(str(probe["dimmed"]["dim_tally"]).split()))
    if "vs_app" in probe:
        print(f"      the served app at {probe['vs_app']['base']} drew the same "
              f"{probe['vs_app']['app_rows']} rows, cell for cell, at the same sort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
