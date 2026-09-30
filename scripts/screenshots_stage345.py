"""Screenshots for stages 3-5: the stepped-flow strip, the heatmap (real and
null-contrast fixtures), and the terse-mode gene table.

Same CDP connection as `scripts/screenshots.py` — see that file for why (the
gateway's browser tool blocks loopback navigation).

Run: .venv/bin/python scripts/screenshots_stage345.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8811/"
CDP = "http://127.0.0.1:18800"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def shot(page, name: str, selector: str | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    if selector:
        page.locator(selector).first.scroll_into_view_if_needed()
        page.locator(selector).screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=False)
    print(f"  wrote {path.relative_to(OUT.parent.parent)}")


def shot_union(page, name: str, selectors: list[str]) -> None:
    """Tight crop around several elements together, for a strip that is more
    than one DOM node (steps 1+2 are two sibling divs, not one)."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    box = page.evaluate(
        """(sels) => {
            const rects = sels.map(s => document.querySelector(s).getBoundingClientRect());
            const x = Math.min(...rects.map(r => r.x));
            const y = Math.min(...rects.map(r => r.y));
            const right = Math.max(...rects.map(r => r.x + r.width));
            const bottom = Math.max(...rects.map(r => r.y + r.height));
            return {x, y, width: right - x, height: bottom - y};
        }""",
        selectors,
    )
    page.screenshot(path=str(path), clip=box)
    print(f"  wrote {path.relative_to(OUT.parent.parent)}")


def pick_disease_and_contrast(page, query: str, accession: str, contrast_id: str) -> None:
    page.goto(APP, wait_until="networkidle")
    page.fill("#disease", query)
    page.wait_for_timeout(500)  # DISEASE_SETTLE_MS
    page.wait_for_selector("#disease-hits .hit", timeout=15_000)
    page.locator("#disease-hits .hit").first.click()
    page.wait_for_selector("#contrast-panel:not([hidden]) table.picker", timeout=15_000)
    row = page.locator(f"tr:has(code:text-is('{accession}')):has(code:text-is('{contrast_id}'))")
    row.scroll_into_view_if_needed()
    row.click()
    page.wait_for_selector("#use-contrast", timeout=10_000)
    page.click("#use-contrast")
    page.wait_for_selector("#gene-table-panel table.gene-table", timeout=30_000)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1680, "height": 1250})
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        # 1. Steps 1-2 collapsed, step 3 (terse gene table) live.
        print("1/5 disease -> contrast -> gene table (terse, the default)")
        pick_disease_and_contrast(page, "Crohn's disease", "E-GEOD-101794", "g2_g1")
        page.wait_for_timeout(400)
        shot(page, "s3-1-terse-gene-table.png", selector="#gene-table-panel")

        # 2. The collapsed stepped-flow strip: steps 1+2, cropped tight.
        print("2/5 the collapsed stepped-flow strip (steps 1-2)")
        page.evaluate(
            "document.querySelector('#disease-chosen').scrollIntoView({block:'start'})")
        page.wait_for_timeout(300)
        shot_union(page, "s5-1-collapsed-steps-1-2.png",
                  ["#disease-chosen", "#contrast-panel"])

        # 3. Verbose mode, for contrast.
        print("3/5 verbose mode (toggle off)")
        page.uncheck("#terse-toggle")
        page.wait_for_timeout(300)
        shot(page, "s3-2-verbose-gene-table.png", selector="#gene-table-panel")
        page.check("#terse-toggle")
        page.wait_for_timeout(300)
        shot(page, "s3-3-terse-gene-table-cropped.png", selector="#gene-table-panel")

        # 4. Step 4: heatmap + selection, real contrast.
        print("4/5 step 4 — heatmap, real contrast (E-GEOD-101794 g2_g1)")
        page.click("#continue-heatmap")
        page.wait_for_selector("#heatmap-panel img.heatmap-img", timeout=15_000)
        page.wait_for_function(
            "document.querySelector('#heatmap-panel img.heatmap-img').complete",
            timeout=120_000)
        page.wait_for_timeout(500)
        shot(page, "s4-1-heatmap-real-contrast.png", selector="#heatmap-panel")

        # Select a few genes and show the collapsed steps 1-4 strip together.
        boxes = page.locator("#gene-table-panel input[type=checkbox]")
        # terse table's checkboxes are back after re-checking terse -- re-open
        # step 3 briefly is not needed, selection state lives independent of
        # which mode is displayed. Select via the still-mounted step-3 table
        # before it collapsed would be cleaner, so re-derive from GENE_TABLE.
        page.evaluate(
            "SELECTED = new Set(GENE_TABLE.rows.slice(0, 5).map(r => r.ensembl_id)); "
            "syncHeatmapCount();"
        )
        page.wait_for_timeout(300)
        shot(page, "s4-2-heatmap-selection-count.png", selector="#heatmap-panel")

        # 5. The null-contrast fixture's heatmap, for comparison. E-GEOD-57945
        #    g6_g4 is the plan's own null contrast (Crohn's, *normal* tissue
        #    vs control).
        print("5/5 step 4 — heatmap, null contrast (E-GEOD-57945 g6_g4)")
        pick_disease_and_contrast(page, "Crohn's disease", "E-GEOD-57945", "g6_g4")
        page.click("#continue-heatmap")
        page.wait_for_selector("#heatmap-panel img.heatmap-img", timeout=15_000)
        page.wait_for_function(
            "document.querySelector('#heatmap-panel img.heatmap-img').complete",
            timeout=120_000)
        page.wait_for_timeout(500)
        shot(page, "s4-3-heatmap-null-contrast.png", selector="#heatmap-panel")

        page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
