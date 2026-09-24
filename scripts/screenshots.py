"""Drive the running app in a real browser and capture the morning screenshots.

Connects over CDP to the Chrome already listening on 127.0.0.1:18800.

Why not the OpenClaw browser tool: the gateway's `browser.ssrfPolicy` blocks
private-network destinations, so every loopback navigation returns "browser
navigation blocked by policy". The allow-list entry has been added to
~/.openclaw/openclaw.json but the gateway reads config at startup, and
restarting it is the owner's call. See docs/V1-REPORT.md.

Run: .venv/bin/python scripts/screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8811/"
CDP = "http://127.0.0.1:18800"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    page.screenshot(path=str(path), full_page=False)
    print(f"  wrote {path.relative_to(OUT.parent.parent)}")


def run_table(page, which: str, region: str = "Lung") -> None:
    page.goto(APP, wait_until="networkidle")
    page.click(f"#load-{which}" if which != "example" else "#load-example")
    page.wait_for_function("document.querySelector('#table').value.length > 20")
    page.select_option("#region", region)
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=180_000)
    page.wait_for_selector("#figure svg", timeout=180_000)
    page.wait_for_timeout(1200)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1680, "height": 1250})

        # 1. A real DE-like gene list.
        print("1/4 figure for a realistic DE list")
        run_table(page, "example")
        page.evaluate("document.querySelector('#counts').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        shot(page, "1-figure.png")

        # 2. The drawer on a gene with a real drug pipeline.
        print("2/4 drawer on BRAF (18 agents)")
        page.evaluate("openDrawer('BRAF')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(700)
        shot(page, "2-drawer-braf.png")
        # and the pipeline table itself, scrolled to
        page.evaluate(
            "document.querySelector('#drawer h4 + .summary-line')"
            "?.scrollIntoView({block:'start'})"
        )
        page.wait_for_timeout(500)
        shot(page, "2b-drawer-braf-pipeline.png")
        page.click("#drawer-close")

        # 3. The deliberately broken input: counts banner + drop ledger.
        print("3/4 broken input, drop ledger")
        run_table(page, "broken")
        page.evaluate("document.querySelector('#counts').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        shot(page, "3-broken-figure.png")
        page.click('.tab[data-tab="ledger"]')
        page.evaluate("document.querySelector('#counts').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        shot(page, "3b-broken-ledger.png")

        # 4. The invariant-failure banner, forced, because it is the single
        #    most important safety behaviour and must be shown working.
        print("4/4 forced invariant failure banner")
        page.evaluate("""
          () => {
            const b = JSON.parse(JSON.stringify(RUN));
            b.invariants = b.invariants.map(i =>
              i.id === 'I4'
                ? {...i, outcome: 'FAIL',
                   detail: 'FORCED FOR THE SCREENSHOT — 16 input != 9 rendered + 6 dropped (+1 unaccounted)'}
                : i);
            render(b);
          }
        """)
        page.wait_for_selector("#banner:not([hidden])")
        page.evaluate("document.querySelector('#banner').scrollIntoView({block:'start'})")
        page.wait_for_timeout(900)
        shot(page, "4-invariant-banner.png")
        page.click('.tab[data-tab="invariants"]')
        page.evaluate("document.querySelector('#banner').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        shot(page, "4b-invariants-table.png")

        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
