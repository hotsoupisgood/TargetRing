"""Stage 7 screenshots: the phenotype table (BRAF), the summary block with its
cost/cap line, and one refused-summary case.

The refused case is drawn from a hand-built mock response injected straight
into the DOM via `phenotypeSectionHtml()` — never a live call — per the stage
7 instructions ("one refused-summary case (from a mock, not a live call)").
The table and the real summary come from the single live call this stage is
budgeted for (`loadPhenotypes('BRAF')`), which also produces the table for
free (7a is structured-source-only; only 7b is the paid call).

Same CDP connection as `scripts/screenshots.py` — see that file for why.

Run: .venv/bin/python scripts/stage7_phenotype_shots.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8811/"
CDP = "http://127.0.0.1:18800"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

BRAF_TABLE = "gene,logFC,padj\nBRAF,2.41,0.0003\n"

MOCK_REFUSED = {
    "gene": "BRAF", "ensembl_id": "ENSG00000157764", "mouse_symbol": "Braf",
    "state": "has_rows",
    "rows": [
        {"row_id": 1, "mp_id": "MP:0000074", "mp_label": "abnormal heart morphology",
         "model_id": "MGI:4946646", "pmid": "21514245",
         "title": "Braf hypomorphic mutation leads to cardiac defects",
         "level": "asserted",
         "level_label": "abstract asserts target manipulation altered the phenotype (unverified)",
         "abstract_status": "fetched",
         "sentence": "Braf mutant embryos show abnormal heart looping and septal defects."},
        {"row_id": 2, "mp_id": "MP:0002169", "mp_label": "no abnormal phenotype detected",
         "model_id": "MGI:4946650", "pmid": "17396120", "title": "A control cross",
         "level": "asserted-negative", "level_label": "no abnormal phenotype detected",
         "abstract_status": "abstract unavailable", "sentence": "gene not named in abstract"},
    ],
    "n_distinct_pmids": 20, "n_articles_fetched": 20, "max_articles_cap": 40,
    "summary_max_rows_cap": 40, "level_counts": {"asserted": 1, "asserted-negative": 1},
    "source": [{"legend_text": "Open Targets (mousePhenotypes.biologicalModels, "
                               "per-paper table) · platform-docs.opentargets.org"},
               {"legend_text": "Europe PMC (per-PMID article fetch for the "
                               "phenotype table) · europepmc.org"}],
    "summary": {
        "attempted": True, "ok": False,
        "reason": ("summary refused: claim 0's quote 'this text is not in any row' "
                   "is not a substring of the title/sentence/mp_label of any row it "
                   "cites ([1]); the model paraphrased instead of quoting verbatim, "
                   "which this check cannot tell apart from a fabricated claim, so "
                   "both are refused the same way"),
        "model": "claude-opus-5", "prompt_hash": "deadbeef01234567",
        "input_tokens": 1342, "output_tokens": 210, "cost_usd": 0.01198,
        "account": {"cap": 5.0, "spent": 0.01198, "calls": 1, "remaining": 4.98802,
                   "purpose": "the phenotype-page summary, one call per gene, "
                              "summarising only the table it sits above"},
        "claims": [], "cached": False,
    },
}


def shot(page, name: str, selector: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    page.locator(selector).first.scroll_into_view_if_needed()
    page.locator(selector).screenshot(path=str(path))
    print(f"  wrote {path.relative_to(OUT.parent.parent)}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1400, "height": 1000})
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        print("1/3 submit BRAF, open drawer, load phenotype table + summary (the one live call)")
        page.goto(APP, wait_until="networkidle")
        page.fill("#table", BRAF_TABLE)
        page.click("#go")
        page.wait_for_selector("#figure svg", timeout=120_000)
        page.evaluate("openDrawer('BRAF')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(500)
        # The phenotype section is a collapsed <details> — open it before clicking load.
        page.evaluate("""
          [...document.querySelectorAll('#drawer-body details.sect summary')]
            .find(s => s.textContent.includes('mouse phenotype table'))?.click();
        """)
        page.wait_for_timeout(200)
        page.click("#phenotype-panel button")
        page.wait_for_function(
            "document.querySelector('#phenotype-panel table') || "
            "document.querySelector('#phenotype-panel .note')",
            timeout=120_000)
        page.wait_for_timeout(500)

        shot(page, "stage7-1-phenotype-table.png", "#phenotype-panel .scroll-x")
        # The summary sits right after the table+source line inside the same panel.
        has_summary = page.evaluate(
            "!!document.querySelector('#phenotype-panel h4')")
        if has_summary:
            shot(page, "stage7-2-summary-cost-cap.png", "#phenotype-panel")
        else:
            print("  NOTE: no successful summary block rendered (see #phenotype-panel note) —"
                  " screenshotting the panel as-is instead")
            shot(page, "stage7-2-summary-cost-cap.png", "#phenotype-panel")

        print("2/3 mock refused-summary case (no live call)")
        page.evaluate(
            "(raw) => { document.querySelector('#phenotype-panel').innerHTML = "
            "phenotypeSectionHtml(JSON.parse(raw)); }",
            json.dumps(MOCK_REFUSED),
        )
        page.wait_for_timeout(300)
        shot(page, "stage7-3-summary-refused-mock.png", "#phenotype-panel")

        print("done")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
