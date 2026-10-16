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

# A list that exercises the v2 Tier-1 fixes on genes whose answers can be stated
# independently -- the council's own liver set. EPHA2 has three approved drugs
# and none of them is an EPHA2 drug; KCNH2 is the canonical anti-target; BRAF
# and PCSK9 are genuine approvals; TMEM229B is Tdark; ACTB is a housekeeping
# negative control.
COUNCIL_LIST = """gene,logFC,padj
PCSK9,2.81,0.00002
TTR,3.10,0.00008
ASGR1,1.92,0.0004
SERPINA1,2.44,0.0001
APOB,2.05,0.0006
HMGCR,1.41,0.002
BRAF,2.41,0.0003
EPHA2,1.77,0.0011
DDR1,1.12,0.004
KCNH2,-1.83,0.0021
GFAP,-3.31,0.00015
HBB,2.66,0.0009
ACTB,0.15,0.04
TMEM229B,0.88,0.012
EGFR,1.95,0.0007
"""


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    page.screenshot(path=str(path), full_page=False)
    print(f"  wrote {path.relative_to(OUT.parent.parent)}")


def _go(page, region: str) -> None:
    page.select_option("#region", region)
    page.click("#go")
    page.wait_for_selector("#result:not([hidden])", timeout=300_000)
    page.wait_for_selector("#figure svg", timeout=300_000)
    page.wait_for_timeout(1200)


def run_table(page, which: str, region: str = "Lung") -> None:
    page.goto(APP, wait_until="networkidle")
    page.click("#load-example" if which == "example" else f"#load-{which}")
    page.wait_for_function("document.querySelector('#table').value.length > 20")
    _go(page, region)


def run_text(page, table: str, region: str, filters: str = "",
             drop_no_data: bool = False, ordering: str = "") -> None:
    page.goto(APP, wait_until="networkidle")
    page.fill("#table", table)
    # v2.5. The filter box and the spoke-order select are part of a run now, so
    # a screenshot of a filtered figure is driven the way a user drives it.
    if filters:
        page.fill("#filters", filters)
    if drop_no_data:
        page.check("#drop-no-data")
    if ordering:
        page.select_option("#ordering", ordering)
    _go(page, region)


def top(page, selector: str = "#counts") -> None:
    page.evaluate(f"document.querySelector('{selector}')?.scrollIntoView({{block:'start'}})")
    page.wait_for_timeout(600)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1680, "height": 1250})
        # v3.0. The CDP Chrome runs off a persistent profile, so it had a disk
        # copy of `app.js` and `app.css` from before the front end changed and
        # kept serving them: the shots came back with the old inline render and
        # the new download menus unstyled, and the app looked broken when it was
        # not. The server sends `no-store` now, which stops it recurring, but a
        # copy cached under the OLD headers is still valid to reuse -- so the
        # cache is cleared once here, before the first navigation.
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        # 1. A real DE-like gene list.
        print("1/8 figure for a realistic DE list")
        run_table(page, "example", "Liver")
        top(page)
        shot(page, "1-figure.png")

        # 2. The drawer on a gene with a real drug pipeline.
        print("2/8 drawer on BRAF")
        page.evaluate("openDrawer('BRAF')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(700)
        shot(page, "2-drawer-braf.png")
        page.evaluate(
            "document.querySelector('#drawer h4 + .summary-line')"
            "?.scrollIntoView({block:'start'})"
        )
        page.wait_for_timeout(500)
        shot(page, "2b-drawer-braf-pipeline.png")
        page.click("#drawer-close")

        # 3. COUNCIL V1, the headline Tier-1 fix, on the council's own list.
        print("3/8 the council's liver set — V1, V2, V4 on real data")
        run_text(page, COUNCIL_LIST, "Liver")
        top(page)
        shot(page, "5-council-list-figure.png")

        print("4/8 EPHA2 — three approved drugs, no attributable indication")
        page.evaluate("openDrawer('EPHA2')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(700)
        shot(page, "5b-drawer-epha2-unattributed.png")
        page.evaluate(
            "[...document.querySelectorAll('#drawer td')]"
            ".find(e => e.textContent.trim() === 'clinical_phase')"
            "?.scrollIntoView({block:'center'})"
        )
        page.wait_for_timeout(500)
        shot(page, "5c-epha2-clinical-phase-cell.png")
        page.click("#drawer-close")

        print("5/8 KCNH2 — the cardiac-liability flag")
        page.evaluate("openDrawer('KCNH2')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(700)
        shot(page, "5d-drawer-kcnh2-liability.png")
        page.click("#drawer-close")

        # 3b. v2.2 item 2's condition, met in v2.4 off the gene query: sponsor
        #     and live status, which is what says whether a target is crowded,
        #     plus the categorised stop reasons that came back with it.
        print("5b/10 BRAF — trial sponsors, live status, categorised stops")
        page.evaluate("openDrawer('BRAF')")
        page.wait_for_selector("#drawer:not([hidden])")
        page.wait_for_timeout(1500)
        page.evaluate(
            "[...document.querySelectorAll('#drawer h4')]"
            ".find(e => e.textContent.startsWith('trials'))"
            "?.scrollIntoView({block:'start'})"
        )
        page.wait_for_timeout(600)
        shot(page, "7-drawer-trial-sponsors.png")

        # 3c. v2.6 replaces the numbered monospace key with a scale per ring,
        #     so the shot is renamed to what it now shows: twelve scales, each
        #     in its own colours and its own units, the activity ring's source
        #     named on it (Puck's v2.2 condition), the TDL ring's four discrete
        #     levels, and the hatched chemical-probe cells beside them.
        print("5c/10 the ring scales — each ring's own domain, units and source")
        page.click("#drawer-close")
        top(page)
        page.wait_for_timeout(400)
        shot(page, "7b-ring-scales-and-sources.png")

        # v3.0. The download row is three grouped menus rather than the row of
        # nine the new artefacts would otherwise have made it, and the shot has
        # to show one OPEN or it shows nothing: a closed `<details>` looks like
        # fewer downloads rather than like the same set, organised.
        print("5c2/10 the grouped downloads, Figure menu open")
        page.evaluate("document.querySelectorAll('details.dl')[0].open = true")
        page.wait_for_timeout(400)
        top(page)
        page.wait_for_timeout(400)
        shot(page, "12-downloads-grouped.png")
        page.evaluate(
            "document.querySelectorAll('details.dl').forEach(d => d.open = false)")

        # 3d. v2.5 item 2: the same list, filtered. Two thresholds, one of them
        #     on a ring several of these genes have no value for, so the figure
        #     carries both outcomes at once -- genes removed into the ledger and
        #     genes kept and marked ~ because the filter could not be evaluated.
        print("5d/10 the council list with two threshold filters active")
        run_text(page, COUNCIL_LIST, "Liver",
                 filters="literature >= 2\nregional_specificity >= 0.5")
        top(page)
        shot(page, "8-filtered-figure.png")
        page.click('.tab[data-tab="ledger"]')
        top(page)
        shot(page, "8b-filtered-ledger.png")
        page.click('.tab[data-tab="figure"]')
        marked = page.evaluate(
            "Object.entries(RUN.genes).find(([, g]) => g.unfiltered_rings.length)?.[0]")
        if marked:
            page.evaluate(f"openDrawer({marked!r})")
            page.wait_for_selector("#drawer:not([hidden])")
            page.wait_for_timeout(700)
            shot(page, "8c-drawer-kept-not-filtered.png")
            page.click("#drawer-close")
        else:
            print("  (no gene was kept-unevaluated on this run; 8c not retaken)")

        # 4. The deliberately broken input: counts + drop ledger.
        print("6/8 broken input, drop ledger")
        run_table(page, "broken", "Liver")
        top(page)
        shot(page, "3-broken-figure.png")
        page.click('.tab[data-tab="ledger"]')
        top(page)
        shot(page, "3b-broken-ledger.png")

        # 5. UX A1: the two notices, separately and together. This is the
        #    distinction the whole seam exists for, so it is shown, not described.
        print("7/8 outage vs invariant failure")
        page.click('.tab[data-tab="figure"]')
        page.evaluate("""
          () => {
            const b = JSON.parse(JSON.stringify(RUN));
            b.invariants = b.invariants.map(i =>
              i.id === 'I5'
                ? {...i, outcome: 'NOT_EVALUATED',
                   detail: 'SIMULATED FOR THE SCREENSHOT - Europe PMC returned HTTP 503 '
                         + 'text/html from nginx for every gene; no echo could be checked',
                   affects: 'Europe PMC sets no ring value (PLAN 2, COUNCIL M6); '
                          + 'the literature ring is PubTator3 and is unaffected'}
                : i);
            render(b);
          }
        """)
        page.wait_for_selector("#conditions:not([hidden])")
        top(page, "#conditions")
        shot(page, "6-outage-is-not-a-failure.png")

        page.evaluate("""
          () => {
            const b = JSON.parse(JSON.stringify(RUN));
            b.invariants = b.invariants.map(i => {
              if (i.id === 'I5') return {...i, outcome: 'NOT_EVALUATED',
                detail: 'SIMULATED - Europe PMC HTTP 503 from nginx, no echo checked',
                affects: 'Europe PMC sets no ring value; the literature ring is unaffected'};
              if (i.id === 'I4') return {...i, outcome: 'FAIL',
                detail: 'SIMULATED - 16 input != 9 rendered + 6 dropped (+1 unaccounted)'};
              return i;
            });
            render(b);
          }
        """)
        page.wait_for_selector("#banner:not([hidden])")
        page.wait_for_selector("#conditions:not([hidden])")
        top(page, "#banner")
        shot(page, "6b-both-notices-together.png")

        print("8/8 forced invariant failure, alone")
        page.evaluate("""
          () => {
            const b = JSON.parse(JSON.stringify(RUN));
            b.invariants = b.invariants.map(i =>
              i.id === 'I4'
                ? {...i, outcome: 'FAIL',
                   detail: 'SIMULATED FOR THE SCREENSHOT - 16 input != 9 rendered + '
                         + '6 dropped (+1 unaccounted)'}
                : i);
            render(b);
          }
        """)
        page.wait_for_selector("#banner:not([hidden])")
        top(page, "#banner")
        shot(page, "4-invariant-banner.png")
        page.click('.tab[data-tab="invariants"]')
        top(page, "#banner")
        shot(page, "4b-invariants-table.png")

        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
