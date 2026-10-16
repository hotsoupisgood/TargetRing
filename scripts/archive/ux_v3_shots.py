"""UX v3: the same five shots at the end of every round, so the rounds compare.

`scripts/screenshots.py` captures the morning deliverable and changes shape with
the feature being shipped. This one deliberately does not: the same viewport,
the same fixture, the same five frames each time, named `rN-*`. A round is only
reviewable against the one before it if the frame did not move between them.

The viewport is **1440 x 900** -- a 13-inch laptop, which is what UX A4's
"presentation size" argument is the other half of. The morning shots are taken
at 1680 x 1250 and that flatters the figure: a 240 px wider panel is a 240 px
wider scale column, and the question this review has to answer is what the
column does on the smaller screen rather than the larger.

Run: .venv/bin/python scripts/ux_v3_shots.py <round>   # 1, 2 or 3
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.screenshots import APP, CDP, COUNCIL_LIST  # noqa: E402

OUT = ROOT / "docs" / "reviews" / "ux-v3-shots"
LAPTOP = {"width": 1440, "height": 900}


def _run(page, table: str, region: str = "Lung", filters: str = "") -> None:
    page.goto(APP, wait_until="networkidle")
    page.fill("#table", table)
    if filters:
        page.fill("#filters", filters)
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=300_000)
    page.wait_for_selector("#figure svg", timeout=300_000)
    page.wait_for_timeout(1500)


def main() -> int:
    rnd = sys.argv[1] if len(sys.argv) > 1 else "1"
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size(LAPTOP)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        print(f"round {rnd}: 1/5 the page as submitted")
        page.goto(APP, wait_until="networkidle")
        page.fill("#table", COUNCIL_LIST)
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"r{rnd}-1-input.png"))

        print(f"round {rnd}: 2/5 the result, whole page")
        _run(page, COUNCIL_LIST, "Liver")
        page.screenshot(path=str(OUT / f"r{rnd}-2-result-full.png"), full_page=True)

        print(f"round {rnd}: 3/5 the figure in one laptop viewport")
        page.evaluate("document.querySelector('#counts').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / f"r{rnd}-3-figure-viewport.png"))

        print(f"round {rnd}: 4/5 the download row, open")
        page.evaluate("document.querySelector('.dl-row')?.scrollIntoView({block:'center'})")
        page.evaluate("document.querySelector('.dl')?.setAttribute('open','')")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"r{rnd}-4-downloads.png"))
        page.evaluate("document.querySelectorAll('.dl').forEach(d=>d.removeAttribute('open'))")

        print(f"round {rnd}: 5/5 the broken table")
        _run(page, (ROOT / "fixtures" / "de_broken.csv").read_text(), "Lung")
        page.evaluate("document.querySelector('#counts').scrollIntoView({block:'start'})")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / f"r{rnd}-5-broken.png"))

        page.close()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
