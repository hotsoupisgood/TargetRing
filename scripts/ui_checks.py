#!/usr/bin/env python3
"""Behaviour checks over the published tool, with a real browser.

v4.2, Puck 2026-10-19. `scripts/ui_audit.py` answers "is anything on this page
drawn wrong"; this script answers "does the page still do what it was asked
to", one assertion per line of output, over the tree `docs/` serves under a
`/TargetRing/` prefix — the path shape GitHub Pages uses.

What it holds, in the order Puck asked for it:

1. "draw the figure breaks everything": clicking `#go` leaves `#result`
   visible, with the figure and the tabs still on the page.
2. no sentence on the page calls it a demo, read-only, a static copy, a
   published version, or explains that there is no server.
3. every ring cell and every table cell has a hover tooltip carrying the ring,
   the gene, the value with its unit (or the state word) and the source.
4. Table is the first tab and the opening view; the second is Circos.
5. the Table tab opens with the three GO / NO-GO criteria and their bounds.
6. a click anywhere on a row highlights that row and opens its drawer.
7. the Drop ledger pane says in one sentence what the ledger is.
8. every flow box is titled verb-first.

    python3 scripts/ui_checks.py --base http://127.0.0.1:8911/TargetRing
    python3 scripts/ui_checks.py --shots docs/showcase

Needs a Playwright with chromium (`~/ytpt/.venv` has one), like `ui_audit.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PHONE = {"width": 390, "height": 844}
DESK = {"width": 1280, "height": 900}

BANNED = ("read-only demo", "static copy", "static export", "published version",
          "there is no server", "nothing is computed here")

VERB_FIRST = ("Enter", "Search", "Pick", "Choose", "Load", "Keep", "Join",
              "Mark", "Check", "Draw", "Decide", "Bake")

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    RESULTS.append((bool(ok), what))
    print(("  ok   " if ok else "  FAIL ") + what)


def tool_checks(page, base: str) -> None:
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1200)

    # 4. Table first, and it is the view the page opens in.
    tabs = [t.inner_text().strip() for t in page.query_selector_all(".tab")]
    check(tabs[:2] == ["Table", "Circos"], f"tab order starts Table, Circos ({tabs[:2]})")
    check(not page.query_selector("#pane-table").is_hidden(),
          "the Table pane is the opening view")
    check(page.evaluate("() => location.hash") in ("#table", ""),
          "the opening hash is #table")

    # 5. The three criteria, with their bounds, at the top of the Table tab.
    crit = page.query_selector("#table-criteria")
    text = crit.inner_text() if crit else ""
    check("three criteria" in text.lower(), "the Table tab opens with the criteria heading")
    check(len(page.query_selector_all("#table-criteria ol.criteria li")) == 3,
          "three numbered criteria are listed")
    for bound in ("1", "0.05"):
        check(bound in text, f"the bounds are on the page ({bound!r})")
    check("Full descriptions are at the bottom" in text,
          "the note points at the full descriptions")
    check(bool(page.query_selector("#full-descriptions table")),
          "the rule in full is at the bottom of the Table tab")

    # 7. The ledger says what it is.
    page.query_selector('.tab[data-tab="ledger"]').click()
    page.wait_for_timeout(300)
    lede = page.query_selector("#pane-ledger .pane-lede")
    check(bool(lede) and len(lede.inner_text().strip()) > 40,
          "the Drop ledger pane carries its one-sentence description")
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(300)

    # 2. Nothing on the page describes itself as a demo or a copy.
    body = page.inner_text("body").lower()
    for phrase in BANNED:
        check(phrase not in body, f"the page does not say {phrase!r}")

    # 3. Tooltips. The table's is a `title`; the circle's is `#cell-tip`.
    td = page.query_selector("#table-grid tbody tr td[title]")
    tip = td.get_attribute("title") if td else ""
    check(len(tip.splitlines()) >= 3, "a table cell's tooltip has ring, value and source lines")
    page.query_selector('.tab[data-tab="figure"]').click()
    page.wait_for_timeout(500)
    shown = page.evaluate("""() => {
      const el = document.querySelector('#figure [id^="cell|"]');
      if (!el) return null;
      const b = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('mousemove', {bubbles: true,
        clientX: b.x + b.width / 2, clientY: b.y + b.height / 2}));
      const t = document.querySelector('#cell-tip');
      return t && !t.hidden ? t.textContent : null;
    }""")
    check(bool(shown) and len((shown or "").splitlines()) >= 3,
          "a ring cell shows a positioned tooltip with its value and source")

    # 1. The button that used to blank the page.
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(300)
    page.query_selector("#go").click()
    page.wait_for_timeout(1400)
    check(not page.query_selector("#result").is_hidden(),
          "#result is still visible after clicking Draw the figure")
    check(len(page.query_selector_all('#figure [id^="cell|"]')) > 0,
          "the figure still carries its cells after Draw the figure")
    check(len(page.query_selector_all(".tab")) == 6,
          "the six tabs survive Draw the figure")

    # 6. A click anywhere on a row.
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(400)
    gene = page.evaluate("""() => {
      const tr = document.querySelector('#table-grid tbody tr');
      const cell = tr.querySelector('td');
      cell.dispatchEvent(new MouseEvent('click', {bubbles: true}));
      return (tr.querySelector('.gene') || {}).textContent.trim();
    }""")
    page.wait_for_timeout(1200)
    check(bool(page.query_selector("#table-grid tr.row-selected")),
          f"a click on a value cell highlights the whole row ({gene})")
    check(bool(page.query_selector("#drawer:not([hidden])")),
          "the same click opens that gene's drawer")
    page.query_selector("#drawer-close").click()
    page.wait_for_timeout(500)
    check(not page.query_selector("#table-grid tr.row-selected"),
          "closing the drawer clears the highlight")


def flow_checks(page, base: str) -> None:
    page.set_viewport_size(DESK)
    page.goto(f"{base}/flow/", wait_until="networkidle")
    page.wait_for_timeout(800)
    titles = page.evaluate(
        """() => [...document.querySelectorAll('svg g.node text.title')]
             .map((t) => t.textContent.trim())""")
    check(len(titles) == 12, f"the flowchart draws 12 boxes ({len(titles)})")
    bad = [t for t in titles
           if t.split(". ", 1)[-1].split(" ")[0] not in VERB_FIRST]
    check(not bad, f"every box is titled verb-first ({bad})")


def shots(page, base: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out / "16-table-default.png"), full_page=True)

    # The ring tooltip, left on screen while the shot is taken.
    page.query_selector('.tab[data-tab="figure"]').click()
    page.wait_for_timeout(700)
    page.evaluate("""() => {
      const el = document.querySelector('#figure [id^="cell|"]');
      const b = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('mousemove', {bubbles: true,
        clientX: b.x + b.width / 2, clientY: b.y + b.height / 2}));
    }""")
    page.wait_for_timeout(400)
    page.screenshot(path=str(out / "17-hover-tooltip.png"))

    # The grid is scrolled to first, so the shot carries BOTH the highlighted
    # row and the drawer it opened — the drawer is a full-height panel and a
    # row 900 px up the document would be off the top of the frame.
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(500)
    page.evaluate("""() => {
      const tr = document.querySelector('#table-grid tbody tr');
      tr.scrollIntoView({block: 'center'});
      tr.querySelector('td').dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }""")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(out / "18-row-highlight-drawer.png"))

    page.goto(f"{base}/flow/", wait_until="networkidle")
    page.wait_for_timeout(900)
    page.screenshot(path=str(out / "19-flow-verbs.png"), full_page=True)
    print(f"screenshots in {out}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8911/TargetRing")
    ap.add_argument("--shots", default="")
    args = ap.parse_args(argv)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(color_scheme="light", device_scale_factor=1)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        print("=== the tool ===")
        tool_checks(page, args.base)
        print("=== the flowchart ===")
        flow_checks(page, args.base)
        check(not errors, f"no console errors ({errors[:3]})")
        if args.shots:
            shots(page, args.base, Path(args.shots))
        b.close()

    failed = [w for ok, w in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
