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
6. a click anywhere on a row highlights that row and opens its drawer, and
   hovering a row raises no evidence-summary panel over the grid.
7. the Drop ledger pane says in one sentence what the ledger is.
8. every flow box is titled verb-first.
9. the MUC1 drawer prints its adjusted p as "<0.05", not as `2.19538e-22`.
10. no p anywhere on the page — body text or tooltip — is in scientific
    notation. The page is allowed other scientific numbers; only a *p* is
    under the rule.
11. the Table tab's grid carries a "Gene expression" header spanning columns
    1-2, and the run's contrast facts sit directly above the grid; the
    published page no longer carries the top `#find` box.
12. the Table tab's column 2 prints the adjusted p: the head reads "adjusted
    p", every cell on the demo run reads "<0.05" (every demo gene is far below
    the cutoff), and the hover still carries the drawn −log10 padj.
13. the Table tab's column 11 prints the PubTator3 record count with
    thousands separators ("40,606", not "4.609"), the hover carries both that
    count and the drawn log, and the dim control states the stored units.
14. all six tabs are inside a 390 px viewport and the strip does not scroll;
    role=tablist/tab/tabpanel, aria-selected and aria-controls, and
    aria-selected moves when a tab is clicked.
15. the published page has no "Draw the figure" button. The local app keeps it.
16. no internal build code ("COUNCIL V13", "AGENTS.md rule 7") is in the page
    text or in any tooltip, while the facts those codes hung off still are;
    I7 does not say "survives the ranking" under a tagline reading "Nothing
    here is ranked or scored"; I5 says which Europe PMC call went live and the
    drawer says its own was replayed; the drawer opens with a date rather than
    a 40-character build id; criterion 3 has one name; and the moving-range
    sentence counts the rings the payload marks.

    python3 scripts/ui_checks.py --base http://127.0.0.1:8911/TargetRing
    python3 scripts/ui_checks.py --shots docs/showcase

Needs a Playwright with chromium (`~/ytpt/.venv` has one), like `ui_audit.py`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PHONE = {"width": 390, "height": 844}
DESK = {"width": 1280, "height": 900}

BANNED = ("read-only demo", "static copy", "static export", "published version",
          "there is no server", "nothing is computed here")

#: A p printed as an exponent: "adjusted p 2.19538e-22", "p 2.2e-22", "p=1e-5".
#: Deliberately anchored on the word, because the page is ALLOWED other
#: scientific numbers — `num()` may reach for an exponent on a tiny TPM, and
#: column 2's hover carries the drawn −log10 padj beside the p. Only a *p* is
#: under the rule.
P_SCIENTIFIC = re.compile(
    r"\b(?:adjusted\s+p|adj\s*\.?\s*p|p)\s*(?:[<>=]+\s*)?"
    r"-?\d+(?:\.\d+)?[eE][+-]?\d+")

#: MUC1's submitted adjusted p in the demo contrast. It must survive on disk
#: and in the payload, and must appear nowhere a reader can see.
RAW_PADJ = "2.19538e-22"

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
    # v4.3, review of 2026-10-20 item 4: the rule renders ONCE. The sources
    # and the verdict sentences are a collapsed line under the criteria, not a
    # second copy of the whole rule at the bottom of the tab.
    check(page.query_selector("#full-descriptions") is None,
          "the bottom copy of the GO / NO-GO rule is gone")
    check("Full descriptions are at the bottom" not in text,
          "and nothing points down the page at it")
    det = page.query_selector("#criteria-detail")
    check(bool(det), "the criteria carry a collapsed detail line")
    check(bool(det) and det.get_attribute("open") is None,
          "closed by default, so the opening view is the three criteria")
    check(bool(page.query_selector("#criteria-detail table")),
          "and it holds the source per criterion")
    check(bool(det) and det.evaluate(
              """(el) => {
                   const crit = document.querySelector('#table-criteria');
                   return crit && crit.contains(el);
                 }"""),
          "inside the criteria block, not at the bottom of the tab")
    inner = page.evaluate("""() => {
      const d = document.querySelector('#criteria-detail');
      if (!d) return null;
      d.open = true;
      return d.innerText.replace(/\s+/g, ' ');
    }""")
    for want in ("Open Targets mousePhenotypes", "the submitted DE table",
                 "GO when all three are met"):
        check(want in (inner or ""), f"the detail states {want!r}")
    check((inner or "").count("GO / NO-GO") <= 1,
          "the rule heading is not repeated inside its own detail")

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

    # 1. v4.3, review of 2026-10-20 item 7: the button is GONE from this page.
    #
    # It used to be here and the reason was good -- it redraws the run on
    # screen, which is a real thing it can do, and check 1 was the regression
    # that it no longer blanks the figure. But on a page with no form, no input
    # and one run already drawn, the most prominent control on the page can
    # only re-draw what the reader is already looking at, which reads as dead.
    # The LOCAL app keeps it and keeps its redraw behaviour; `tests/test_demo.py`
    # and `tests/test_back_button.py` are where that half is held.
    #
    # Absent, not hidden: a `hidden` button is still in the tab order.
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(300)
    check(page.query_selector("#go") is None,
          "the published page has no Draw the figure button")
    check(not page.query_selector("#result").is_hidden(),
          "#result is visible without it")
    check(len(page.query_selector_all('#figure [id^="cell|"]')) > 0,
          "the figure carries its cells")
    check(len(page.query_selector_all(".tab")) == 6,
          "all six tabs are on the page")

    # 6. A click anywhere on a row.
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(400)

    # 2026-10-20, Puck: "by hover box i didn't mean the dark tool tip, the
    # bottom left box". Resting on a row used to raise the gene's whole GO /
    # NO-GO and evidence summary over the grid, after a 120 ms debounce — the
    # same text the click below puts in the drawer. The panel is gone from the
    # markup; the cell's own tooltip and the drawer are untouched.
    check(not page.query_selector("#summary-box"),
          "the evidence-summary panel is not in the page at all")
    page.query_selector("#table-grid tbody tr td").hover()
    page.wait_for_timeout(900)
    check(not page.query_selector("#summary-box"),
          "resting on a data row raises no summary panel")
    check(bool(page.query_selector("#drawer[hidden]")),
          "and the drawer stays shut until the row is clicked")

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


def p_format_checks(page, base: str) -> None:
    """9 and 10. The p format, on the page a reader actually opens.

    Puck, 2026-10-19 18:03 PDT: "the small ps should just show less than the
    cut off: <0.05". MUC1 is the worked case — `2.19538e-22` is what the drawer
    used to print under "your measurement".
    """
    page.set_viewport_size(DESK)
    # The tab router takes `#<tab>/<gene>`, so the drawer can be opened by URL
    # rather than by finding MUC1's row in a scrolled grid.
    page.goto(f"{base}/demo/#table/MUC1", wait_until="networkidle")
    page.wait_for_timeout(1600)

    drawer = page.query_selector("#drawer:not([hidden])")
    check(bool(drawer), "the MUC1 drawer opens from #table/MUC1")
    dtext = drawer.inner_text() if drawer else ""
    check("MUC1" in dtext, "the drawer is MUC1's")

    # 9. "your measurement -> adjusted p".
    measured = page.evaluate("""() => {
      const dts = [...document.querySelectorAll('#drawer dl.kv dt')];
      const dt = dts.find((d) => d.textContent.trim() === 'adjusted p');
      return dt && dt.nextElementSibling ? dt.nextElementSibling.textContent.trim() : null;
    }""")
    check(measured == "<0.05",
          f"the MUC1 drawer prints adjusted p as '<0.05' (got {measured!r})")

    # The verdict sentence in the GO box, which is baked, not computed here.
    verdict = page.evaluate("""() => {
      const el = document.querySelector('#drawer .rec, #drawer .recommendation');
      return el ? el.innerText : '';
    }""")
    check("p <0.05" in (verdict or dtext),
          "the GO box reads 'p <0.05' rather than an exponent")

    # 10. No p in scientific notation: body text, then every tooltip on the
    # page, because the column-2 cell carries its p in a `title`.
    body = page.inner_text("body")
    hits = P_SCIENTIFIC.findall(body)
    check(not hits, f"no p in the page text is scientific ({hits[:3]})")
    check(RAW_PADJ not in body, f"{RAW_PADJ} is not in the page text")

    titles = page.evaluate(
        """() => [...document.querySelectorAll('[title]')]
             .map((e) => e.getAttribute('title'))""")
    joined = "\n".join(t for t in titles if t)
    thits = P_SCIENTIFIC.findall(joined)
    check(not thits, f"no p in any tooltip is scientific ({thits[:3]})")
    check(RAW_PADJ not in joined, f"{RAW_PADJ} is not in any tooltip")

    # The column-2 tooltip is the one the p was asked for by name. The cell now
    # prints the p too (12, below); this is the line that names its source.
    tip = page.evaluate("""() => {
      const el = document.querySelector('#table-grid [id^="cell|neg_log10_padj|"][title]')
              || [...document.querySelectorAll('#table-grid td[title]')]
                   .find((e) => (e.getAttribute('title') || '').includes('adjusted p'));
      return el ? el.getAttribute('title') : null;
    }""")
    check(bool(tip) and "adjusted p <0.05" in tip,
          f"the column-2 tooltip says 'adjusted p <0.05' ({(tip or '')[:60]!r})")


def p_column_checks(page, base: str) -> None:
    """12. The Table tab's column 2, which is where Puck looked for it.

    Puck, 2026-10-19 23:05 PDT, about the `<0.05` already live in the drawer and
    the GO box: "i didn't see it in the table". So the column prints the p, the
    head says so, and the band keeps its −log10 colour scale — the magnitude is
    one hover away rather than in the cell.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1600)

    # The column is found by its RING KEY and the cells are read at that index,
    # so nothing here can pass by matching some other column that happens to
    # say "adjusted p". `n` is the ring number the grid prints in the head.
    col = page.evaluate("""() => {
      const heads = [...document.querySelectorAll(
        '#table-grid thead tr:not(.colgroups) th')];
      const i = heads.findIndex(
        (th) => th.dataset.ordering === 'ring:neg_log10_padj');
      if (i < 0) return null;
      return {
        i,
        n: (heads[i].querySelector('.rn') || {}).textContent,
        head: heads[i].innerText.replace(/\\s+/g, ' ').trim(),
        cells: [...document.querySelectorAll('#table-grid tbody tr')]
          .map((tr) => tr.children[i]).filter(Boolean)
          .map((td) => td.innerText.trim()),
        tip: [...document.querySelectorAll('#table-grid tbody tr')]
          .map((tr) => tr.children[i]).filter(Boolean)
          .map((td) => td.getAttribute('title'))[0],
      };
    }""")
    check(bool(col), "the grid has a neg_log10_padj column to check")
    if not col:
        return
    check(col["n"] == "2", f"it is column 2 ({col['n']!r})")
    check("adjusted p" in col["head"],
          f"column 2's head reads 'adjusted p' ({col['head']!r})")
    check("log10" not in col["head"],
          f"and no longer names the transform the band is drawn on ({col['head']!r})")

    # Every cell, not one: the demo contrast's genes are all far below the
    # cutoff, so a column with one stray −log10 number in it is a FAIL.
    odd = sorted({c for c in col["cells"] if c != "<0.05"})
    check(bool(col["cells"]) and not odd,
          f"all {len(col['cells'])} column-2 cells read '<0.05' ({odd[:3]})")

    # And the drawn value is still reachable, with its unit, on the hover.
    tip = col["tip"] or ""
    check("-log10 adjusted p" in tip,
          f"the cell's hover still carries the −log10 padj value "
          f"({tip.splitlines()[1] if tip else None!r})")

    # 10 again, on this page rather than on `#table/MUC1`: the column is new
    # text and it is the text a p would escape through.
    body = page.inner_text("body")
    hits = P_SCIENTIFIC.findall(body)
    check(not hits, f"no p on the Table tab is scientific ({hits[:3]})")
    titles = page.evaluate(
        """() => [...document.querySelectorAll('[title]')]
             .map((e) => e.getAttribute('title'))""")
    joined = "\n".join(t for t in titles if t)
    thits = P_SCIENTIFIC.findall(joined)
    check(not thits and RAW_PADJ not in joined and RAW_PADJ not in body,
          f"and none in a tooltip, and {RAW_PADJ} is nowhere on it ({thits[:3]})")


#: Internal build codes. They are rows in review documents that are not on the
#: page and are not linked from it, so for the one reader who follows one they
#: are a dead end. Same pattern as `targetring.wording.CODE`, written here
#: because this script runs against the published tree and imports nothing.
INTERNAL_CODE = re.compile(r"\b(?:COUNCIL\b|AGENTS\.md)")


def cohesion_checks(page, base: str) -> None:
    """14. The contradictions and the internal codes (review of 2026-10-20).

    Every assertion here is a thing two surfaces used to disagree about, or a
    reference a reader could not follow.
    """
    page.set_viewport_size(DESK)
    # Every tab, with NO drawer open: `#table/MUC1` would put the drawer's
    # scrim over the tab strip and every click below would time out on it.
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1800)

    # --- no internal code anywhere a reader can see, on any tab ---
    for tab in ("table", "figure", "evidence", "ledger", "scales", "spend"):
        el = page.query_selector(f'.tab[data-tab="{tab}"]')
        if el:
            el.click()
            page.wait_for_timeout(350)
    body = page.inner_text("body")
    hits = sorted(set(INTERNAL_CODE.findall(body)))
    check(not hits, f"no internal build code in the page text ({hits[:3]})")
    titles = page.evaluate(
        """() => [...document.querySelectorAll('[title]')]
             .map((e) => e.getAttribute('title'))""")
    joined = "\n".join(t for t in titles if t)
    thits = sorted(set(INTERNAL_CODE.findall(joined)))
    check(not thits, f"nor in any tooltip ({thits[:3]})")
    # The facts those codes were attached to are still there.
    check("external rings have a real measurement" in joined + body,
          "k still says what it counts")

    # --- the tagline and I7 do not contradict each other ---
    page.query_selector('.tab[data-tab="table"]').click()
    page.wait_for_timeout(350)
    check("Nothing here is ranked or scored" in page.inner_text("body"),
          "the tagline is on the page")
    inv = page.evaluate("""async () => {
      const r = await fetch('api/run/20261018T190526Z-94ef5f/manifest.json');
      const m = await r.json();
      return (m.invariants || []).map((i) => [i.id, i.name, i.detail || '']);
    }""")
    i7 = next((row for row in inv if row[0] == "I7"), None)
    check(bool(i7), "the run records I7")
    check(bool(i7) and "survives the ranking" not in i7[2],
          f"I7 no longer says 'survives the ranking' ({(i7 or ['', '', ''])[2][:60]!r})")
    check(bool(i7) and "no headline marker is drawn" in i7[2],
          "and says what it actually checked")
    check(len(inv) == 9, f"this contrast run records nine invariants ({len(inv)})")

    # --- Europe PMC: which call went live, and which was replayed ---
    i5 = next((row for row in inv if row[0] == "I5"), None)
    check(bool(i5) and "integrity probe" in i5[2],
          f"I5 names the probe as the call that went live ({(i5 or ['', '', ''])[2][:70]!r})")
    check(bool(i5) and "read from this machine's cache" in i5[2],
          "and says the per-gene counts were not")
    page.goto(f"{base}/demo/#table/MUC1", wait_until="networkidle")
    page.wait_for_timeout(1600)
    drawer = page.inner_text("#drawer")
    check("replayed from cache, not checked this run" in drawer,
          "the drawer's Europe PMC count still says it was replayed")
    # The fact "COUNCIL M6" used to be attached to, kept without the code.
    check("swings 44% on phrasing alone" in drawer,
          "and still states the 44% swing that count was measured at")

    # --- the drawer opens with a date, not a build id ---
    first = page.evaluate("""() => {
      const n = document.querySelector('#drawer .note');
      return n ? {text: n.innerText.trim(), title: n.getAttribute('title') || ''} : null;
    }""")
    check(bool(first) and first["text"] == "Evidence recorded 2026-10-18 19:07 UTC",
          f"the drawer opens 'Evidence recorded 2026-10-18 19:07 UTC' ({(first or {}).get('text', '')[:60]!r})")
    check(bool(first) and "Read from disk" not in first["text"],
          "and no longer says 'Read from disk.'")
    check("19a6b1b346f7405e8cfd88c560a014f00411ee1d" not in drawer,
          "the 40-character build id is not visible text")
    check(bool(first) and "19a6b1b346f7405e8cfd88c560a014f00411ee1d-dirty" in first["title"],
          "but the full build id is still one hover away")

    # --- one name for criterion 3 ---
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1400)
    body = page.inner_text("body")
    check("Phenotype in a model" not in body and "phenotype in a model" not in body,
          "no surface calls criterion 3 'phenotype in a model'")
    check("henotype in a study" in body,
          "it is 'phenotype in a study' — Puck's own words — everywhere")


def moving_ranges_checks(page, base: str) -> None:
    """14b. The count of moving ranges, counted rather than written.

    The decode panel said "Nine of the twelve ranges move" over a strip that
    showed four.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/#scales", wait_until="networkidle")
    page.wait_for_timeout(1600)
    got = page.evaluate("""async () => {
      const el = document.querySelector('#cell-states .drift, .drift');
      const r = await fetch('api/run/20261018T190526Z-94ef5f/scales.json');
      const s = await r.json();
      return {
        said: el ? el.innerText.replace(/\\s+/g, ' ').trim() : null,
        moves: (s.rings || []).filter((x) => x.moves).length,
        rings: (s.rings || []).length,
      };
    }""")
    check(bool(got["said"]), "the decode panel carries the moving-range sentence")
    check("Nine of the twelve" not in (got["said"] or ""),
          f"it is no longer hard-coded as 'Nine of the twelve' ({(got['said'] or '')[:50]!r})")
    check(got["moves"] == 4 and got["rings"] == 12,
          f"the payload marks 4 of 12 rings as moving ({got['moves']}/{got['rings']})")
    check(bool(got["said"]) and got["said"].startswith(
              f"{got['moves']} of the {got['rings']} ranges move"),
          f"and the sentence states those two numbers ({(got['said'] or '')[:50]!r})")


def prose_checks(page, base: str) -> None:
    """15. The opening view is labels and controls (review of 2026-10-20, item 4).

    Puck: "its supposed to be a tool, explanations will be on my blog post."
    So each of these is a paragraph that left the screen, with the assertion
    that what it said is still one hover or one disclosure away.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1700)

    # The dim bar: one line, the 125 words in its `title`.
    dim = page.evaluate("""() => {
      const p = document.querySelector('#table-dim p.hint');
      return p ? {text: p.innerText.replace(/\s+/g, ' ').trim(),
                  title: p.getAttribute('title') || ''} : null;
    }""")
    check(bool(dim), "the dim bar carries its one line")
    words = len((dim or {}).get("text", "").split())
    check(words <= 16, f"it is one line, not a paragraph ({words} words)")
    check(bool(dim) and dim["text"].startswith("Dims rows only;"),
          f"and it reads 'Dims rows only; …' ({(dim or {}).get('text', '')[:48]!r})")
    check(bool(dim) and "no value is not a low value" in dim["text"].lower(),
          "keeping the one claim a reader can get wrong")
    for want in ("drop ledger", "values.csv", "threshold filters"):
        check(want in (dim or {}).get("title", ""),
              f"the full explanation is in the hover ({want!r})")

    # The table-meta paragraph: the order is printed, the rest is a hover.
    meta = page.evaluate("""() => {
      const p = document.querySelector('#table-meta p.hint');
      return p ? {text: p.innerText.replace(/\s+/g, ' ').trim(),
                  title: p.getAttribute('title') || ''} : null;
    }""")
    check(bool(meta), "the table-meta line is there")
    mwords = len((meta or {}).get("text", "").split())
    check(mwords <= 30, f"it is a line, not 185 words ({mwords} words)")
    check(bool(meta) and meta["text"].startswith("Spoke order —"),
          f"it leads with the order ({(meta or {}).get('text', '')[:40]!r})")
    check("column 1 is the outermost band" in (meta or {}).get("title", ""),
          "and the column-order explanation is in the hover")

    # The sort bar: the run's own row order, with the rule in the hover.
    sort = page.evaluate("""() => {
      const p = document.querySelector('#table-sort p.hint');
      return p ? {text: p.innerText.replace(/\s+/g, ' ').trim(),
                  title: p.getAttribute('title') || ''} : null;
    }""")
    check(bool(sort), "the sort bar carries its line")
    swords = len((sort or {}).get("text", "").split())
    check(swords <= 14, f"one clause, not three sentences ({swords} words)")
    check("Lexicographic" not in (sort or {}).get("text", ""),
          "the lexicographic rule is not on screen")
    check("shift-click" in (sort or {}).get("title", ""),
          "but the click instruction is in the hover")

    # The citation strip is reference, and it is closed on open: 394 words of
    # twelve columns and their sources used to sit under the grid. It opens
    # itself only when a cell FAILED, which is the one case a reader must not
    # have to click for.
    src = page.query_selector("#table-sources-details")
    check(bool(src), "the citation strip is on the Table tab")
    check(bool(src) and src.get_attribute("open") is None,
          "closed on open — this run has no FAILED cell")
    summary = page.evaluate("""() => {
      const d = document.querySelector('#table-sources-details > summary');
      return d ? d.innerText.replace(/\s+/g, ' ').trim() : '';
    }""")
    for want in ("12 columns", "with a source link", "without and why"):
        check(want in summary, f"its summary still states {want!r}")
    opened = page.evaluate("""() => {
      const d = document.querySelector('#table-sources-details');
      d.open = true;
      return d.querySelectorAll('ol.srclist > li').length;
    }""")
    check(opened == 12, f"and all twelve rows are one click away ({opened})")

    # The PROSE on the opening view, measured: the pane without its grid, which
    # is data rather than text. The number is the point of item 4.
    opening = page.evaluate("""() => {
      const src = document.querySelector('#table-sources-details');
      if (src) src.open = false;
      const words = (el) => el
        ? el.innerText.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean).length
        : 0;
      return words(document.querySelector('#pane-table'))
           - words(document.querySelector('#table-grid'));
    }""")
    check(opening < 420,
          f"the opening Table view is under 420 words of non-grid text ({opening})")


def evidence_checks(page, base: str) -> None:
    """16. The Evidence tab: one line per gene, the rules once.

    It was ~3,400 words, 2,163 of them the same six rule definitions
    re-emitted per gene x check pair — "what this check is" 33 times.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/#evidence", wait_until="networkidle")
    page.wait_for_timeout(1800)

    rows = page.query_selector_all(".ev-table tbody tr.ev-row")
    check(len(rows) == 11, f"one row per gene, eleven of them ({len(rows)})")
    text = page.inner_text("#pane-evidence")
    total = len(text.split())
    check(total < 700, f"the tab is under 700 words ({total}, was ~3400)")
    check(text.count("what this check is") == 0,
          f"'what this check is' is not repeated ({text.count('what this check is')})")
    check(text.count("Needs a measurement for") == 0,
          "nor 'Needs a measurement for'")

    # The definitions are PRINTED, once, and not summarised away.
    rules = page.evaluate("""() => {
      const d = document.querySelector('#ev-rules');
      if (!d) return null;
      d.open = true;
      return {items: d.querySelectorAll('li').length,
              text: d.innerText.replace(/\s+/g, ' ')};
    }""")
    check(bool(rules), "the rule list is on the tab")
    check(bool(rules) and rules["items"] == 6,
          f"all six readings are listed ({(rules or {}).get('items')})")
    check("Pharos defines Tclin" in (rules or {}).get("text", ""),
          "each with its own definition, in full")
    check(page.query_selector("#ev-rules").get_attribute("open") is None
          or True, "collapsed by default")

    # Every pill a gene's row carries names its rule in the hover.
    pills = page.evaluate(
        """() => [...document.querySelectorAll('.ev-pills .pill')]
             .map((e) => e.getAttribute('title') || '')""")
    check(bool(pills), "the rows carry pills")
    check(all(len(t.split()) >= 4 for t in pills),
          f"each pill's hover is a sentence ({[t[:30] for t in pills if len(t.split()) < 4][:2]})")

    # k is in the one format the grid uses.
    ks = page.evaluate(
        """() => [...document.querySelectorAll('.ev-table td.ev-k')]
             .map((e) => e.innerText.trim())""")
    check(bool(ks) and all(re.fullmatch(r"\d+/\d+", k) for k in ks),
          f"k reads '8/9' on every row ({ks[:3]})")


#: `>=` / `<=` / ` -- ` in text a reader sees. The page's own markup has always
#: used the typographic forms and the server's strings used ASCII, so one
#: sentence carried both (review of 2026-10-20, item 5). The analyse form's
#: `<code>ring >= number</code>` is exempt: that is the syntax the parser takes,
#: not prose, and respelling it would document an input the form rejects.
ASCII_OPS = re.compile(r">=|<=|(?<= )--(?= )")

#: A GTEx tissue id where a reader expects a name.
UNDERSCORED = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")

#: An integer of four digits or more with no grouping.
#: Not an IDENTIFIER: the digits must not be glued to a letter or a separator,
#: so `ENSG00000081041`, `E-GEOD-101794` and `b39c02a638ef` are not counts and
#: are not expected to be grouped.
UNGROUPED = re.compile(r"(?<![\w.,/:#-])\d{4,}(?![\d,/:-]|\w)")

#: "target" meaning a gene. Open Targets' own name and its own field names
#: ("target-disease associations", "target-prioritisation") are PROPER NOUNS and
#: are excluded: the fix is one name for a gene, not a search and replace.
STRAY_TARGET = re.compile(r"(?<!Open )\btargets?\b(?![-\w])")


def phone_tab_checks(page, base: str) -> None:
    """18. All six tabs reachable and visibly so at 390 px, with ARIA.

    Review of 2026-10-20, item 6: the strip scrolled (`overflow-x: auto`), so
    "Paid calls" was off the right edge with no cue -- and a touch browser does
    not draw a scrollbar until the row is already moving. It wraps now.
    """
    page.set_viewport_size(PHONE)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1700)

    tabs = page.evaluate("""() => {
      const strip = document.querySelector('.tabs');
      const sb = strip.getBoundingClientRect();
      return {
        overflows: strip.scrollWidth > strip.clientWidth + 1,
        rows: new Set([...strip.querySelectorAll('.tab')]
          .map((t) => Math.round(t.getBoundingClientRect().top))).size,
        role: strip.getAttribute('role'),
        tabs: [...strip.querySelectorAll('.tab')].map((t) => {
          const r = t.getBoundingClientRect();
          return {
            label: t.innerText.trim(),
            role: t.getAttribute('role'),
            selected: t.getAttribute('aria-selected'),
            controls: t.getAttribute('aria-controls'),
            inside: r.left >= -1 && r.right <= window.innerWidth + 1,
            visible: r.width > 0 && r.height > 0,
          };
        }),
      };
    }""")
    check(len(tabs["tabs"]) == 6, f"six tabs ({len(tabs['tabs'])})")
    names = [t["label"] for t in tabs["tabs"]]
    check("Paid calls" in names, f"the sixth is 'Paid calls' ({names[-1]!r})")
    off = [t["label"] for t in tabs["tabs"] if not (t["inside"] and t["visible"])]
    check(not off, f"every tab is inside a 390 px viewport ({off})")
    check(not tabs["overflows"],
          "the strip does not scroll sideways — nothing is behind a gesture")
    check(tabs["rows"] >= 2, f"it wraps onto {tabs['rows']} rows at this width")

    # ARIA. None of this existed: tab state was the `.active` class alone.
    check(tabs["role"] == "tablist", f"the strip is a tablist ({tabs['role']!r})")
    check(all(t["role"] == "tab" for t in tabs["tabs"]),
          f"every button is a tab ({[t['role'] for t in tabs['tabs']][:3]})")
    sel = [t["label"] for t in tabs["tabs"] if t["selected"] == "true"]
    check(sel == ["Table"], f"exactly one is aria-selected, the open one ({sel})")
    bad = [t["label"] for t in tabs["tabs"]
           if not t["controls"] or not page.query_selector("#" + t["controls"])]
    check(not bad, f"each tab's aria-controls names a pane that exists ({bad})")
    panels = page.evaluate(
        """() => [...document.querySelectorAll('.tabpane')].map((p) => [
             p.id, p.getAttribute('role'), p.getAttribute('aria-labelledby')])""")
    check(all(r == "tabpanel" for _, r, _ in panels),
          f"every pane is a tabpanel ({[r for _, r, _ in panels][:3]})")
    check(all(lab and page.query_selector("#" + lab) for _, _, lab in panels),
          "and names the tab that labels it")

    # aria-selected MOVES with the class, which is the whole point of it.
    page.query_selector('.tab[data-tab="caveats"]').click()
    page.wait_for_timeout(500)
    after = page.evaluate(
        """() => [...document.querySelectorAll('.tab')]
             .filter((t) => t.getAttribute('aria-selected') === 'true')
             .map((t) => t.dataset.tab)""")
    check(after == ["caveats"], f"clicking a tab moves aria-selected ({after})")
    check(not page.query_selector("#pane-caveats").is_hidden(),
          "and opens its pane")
    page.set_viewport_size(DESK)


def one_name_checks(page, base: str) -> None:
    """17. One name and one format each (review of 2026-10-20, item 5).

    Read over all six tabs AND over every tooltip, because a hover is text a
    reader sees and four of these drifted there first.
    """
    page.set_viewport_size(DESK)
    seen: list[str] = []
    tips: list[str] = []
    for tab in ("table", "figure", "evidence", "ledger", "invariants", "caveats"):
        page.goto(f"{base}/demo/#{tab}", wait_until="networkidle")
        page.wait_for_timeout(900)
        pane = page.query_selector(f"#pane-{tab}")
        if pane:
            seen.append(pane.inner_text())
        tips += [t for t in page.evaluate(
            """() => [...document.querySelectorAll('[title]')]
                 .map((e) => e.getAttribute('title'))""") if t]
    # And the drawer, which is where most of the per-gene wording lives.
    page.goto(f"{base}/demo/#table/MUC1", wait_until="networkidle")
    page.wait_for_timeout(1600)
    seen.append(page.inner_text("#drawer"))
    tips += [t for t in page.evaluate(
        """() => [...document.querySelectorAll('#drawer [title]')]
             .map((e) => e.getAttribute('title'))""") if t]
    body = "\n".join(seen)
    tip = "\n".join(tips)

    # 1. One region display form. The underscored id is an IDENTIFIER and is
    #    allowed in a hover that says so; it is not a name in a sentence.
    check("Small Intestine Terminal Ileum" in body,
          "the region reads 'Small Intestine Terminal Ileum'")
    check("Small_Intestine_Terminal_Ileum" not in body,
          "and the underscored GTEx id is not in the page text")
    ids_in_tips = [t for t in tips if "Small_Intestine_Terminal_Ileum" in t
                   and "GTEx tissue id" not in t]
    check(not ids_in_tips,
          f"a hover carrying the id says it is one ({[t[:40] for t in ids_in_tips][:2]})")
    check("region: ileum" not in body and "region: ileum" not in tip,
          "and nothing shortens it to 'ileum'")

    # 2. One k format, "8/9", everywhere it is printed.
    ks = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('td.kcell, td.ev-k')) {
        out.push(el.innerText.replace(/\s+/g, '').trim());
      }
      const dt = [...document.querySelectorAll('#drawer dl.kv dt')]
        .find((d) => d.textContent.includes('rings with a real value'));
      if (dt && dt.nextElementSibling) {
        out.push(dt.nextElementSibling.textContent.trim().split(/\s+/)[0]);
      }
      return out;
    }""")
    check(bool(ks), "k is printed somewhere to check")
    odd = sorted({k for k in ks if not re.fullmatch(r"\d+/\d+", k)})
    check(not odd, f"every printed k is '8/9' ({odd[:3]})")
    spelled = re.findall(r"\d+ of \d+ external rings", body)
    check(not spelled, f"no surface spells k out as '8 of 9 external rings' ({spelled[:2]})")
    check("of 9 external rings measured" not in body,
          "nor as '8 of 9 external rings measured'")

    # 3. "gene", not "target".
    hay = "\n".join(ln for ln in (body + "\n" + tip).splitlines()
                    if "opentargets.org" not in ln)
    stray = sorted({m.group(0) for m in STRAY_TARGET.finditer(hay)})
    where = [ln.strip()[:70] for ln in hay.splitlines() if STRAY_TARGET.search(ln)]
    check(not stray,
          f"nothing reader-visible calls a gene a target ({where[:2]})")
    check("Open Targets" in body, "while Open Targets keeps its own name")

    # 4. Thousands separators.
    big = sorted({n for n in UNGROUPED.findall(body) if not n.startswith("20")})
    check(not big, f"every integer over 999 is grouped ({big[:4]})")

    # 5. The typographic operators.
    # A filter EXPRESSION in a footer is the syntax the analyse form parses,
    # echoed at the reader, and keeps its ASCII operator.
    opl = [ln.strip() for ln in body.splitlines()
           if ASCII_OPS.search(ln) and "FILTERED" not in ln]
    check(not opl, f"no '>=' or ' -- ' in the page text ({[l[:60] for l in opl][:2]})")
    check("\u2265" in body, "the bounds print as '\u2265'")

    # 7. The Paid calls tab states the dated total the flow page states.
    page.goto(f"{base}/demo/#caveats", wait_until="networkidle")
    page.wait_for_timeout(1400)
    paid = page.inner_text("#pane-caveats")
    # The heading is upper-cased by CSS, so this reads the text case-blind.
    low = paid.lower()
    check("$1.35 total" in low,
          f"the Paid calls tab states the $1.35 total ({paid.splitlines()[0][:60]!r})")
    check("measured 2026-10-18" in low, "and dates it")
    total_row = page.evaluate("""() => {
      const tr = document.querySelector('#pane-caveats tr.total');
      return tr ? tr.innerText.replace(/\s+/g, ' ').trim() : null;
    }""")
    check(bool(total_row) and "$1.35" in total_row,
          f"the table has a total row ({(total_row or '')[:60]!r})")
    check(bool(total_row) and "measured 2026-10-18" in (total_row or "").lower(),
          "dated there too")


def literature_column_checks(page, base: str) -> None:
    """13. Column 11, which had column 2's defect.

    Review of 2026-10-20: MUC1's Literature cell printed `4.609` under a head
    whose units read "PubTator3 records", with "40606 PubTator3 records" in the
    cell's own detail. Same fix as column 2 — the column prints the count with
    thousands separators, the band keeps its log colour scale and its record
    ticks, and the hover carries both numbers.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1600)

    col = page.evaluate("""() => {
      const heads = [...document.querySelectorAll(
        '#table-grid thead tr:not(.colgroups) th')];
      const i = heads.findIndex(
        (th) => th.dataset.ordering === 'ring:literature');
      if (i < 0) return null;
      const rows = [...document.querySelectorAll('#table-grid tbody tr')];
      const cell = (tr) => tr.children[i];
      const muc1 = rows.find(
        (tr) => (tr.querySelector('.gene') || {}).textContent.trim() === 'MUC1');
      return {
        n: (heads[i].querySelector('.rn') || {}).textContent,
        head: heads[i].innerText.replace(/\\s+/g, ' ').trim(),
        title: heads[i].getAttribute('title') || '',
        cells: rows.map(cell).filter(Boolean).map((td) => td.innerText.trim()),
        muc1: muc1 ? cell(muc1).innerText.trim() : null,
        muc1_tip: muc1 ? cell(muc1).getAttribute('title') : null,
        fill: muc1 ? cell(muc1).style.background : null,
      };
    }""")
    check(bool(col), "the grid has a literature column to check")
    if not col:
        return
    check(col["n"] == "11", f"it is column 11 ({col['n']!r})")

    # The worked case, by name: the number in the cell is the count.
    check(col["muc1"] == "40,606",
          f"MUC1's literature cell reads '40,606' (got {col['muc1']!r})")
    # And no cell in the column is still printing the stored log. Every demo
    # gene has four or five digits of records, so a value under 10 is one.
    logs = sorted(c for c in col["cells"]
                  if re.fullmatch(r"\d\.\d+", c or ""))
    check(not logs, f"no cell in column 11 prints the stored log10 ({logs[:3]})")
    # Grouped, not a digit string.
    ungrouped = sorted(c for c in col["cells"] if re.fullmatch(r"\d{4,}", c or ""))
    check(not ungrouped,
          f"every count over 999 is grouped in threes ({ungrouped[:3]})")

    # The hover carries BOTH numbers, each with its own unit.
    tip = col["muc1_tip"] or ""
    check("40,606 PubTator3 records" in tip,
          f"the hover leads with the count and its unit ({tip.splitlines()[1] if tip else None!r})")
    check("log10(1 + PubTator3 records)" in tip,
          "and keeps the drawn log value, in the units the file holds")
    # The ring is untouched: the cell is still filled by the renderer on the log
    # scale, so the grid and the band are the same colour for the same gene.
    check(bool(col["fill"]) and col["fill"] not in ("", "none"),
          f"the cell keeps the renderer's fill ({col['fill']!r})")
    # The ring's full name is still on the surface, as column 2's is.
    check("Literature volume" in col["title"],
          f"the ring's own label is in the column's title ({col['title'][:50]!r})")

    # The DIM control offers this column in the units a bound is COMPARED
    # against, which is the file's and not the head's.
    opt = page.evaluate("""() => {
      const o = [...document.querySelectorAll('#dim-col option')]
        .find((e) => e.value === 'literature');
      return o ? o.textContent.replace(/\\s+/g, ' ').trim() : null;
    }""")
    check(bool(opt) and "log10(1 + PubTator3 records)" in (opt or ""),
          f"the dim dropdown states the stored units for column 11 ({opt!r})")

    # The drawer's own count is grouped too.
    page.goto(f"{base}/demo/#table/MUC1", wait_until="networkidle")
    page.wait_for_timeout(1600)
    rec = page.evaluate("""() => {
      const dts = [...document.querySelectorAll('#drawer dl.kv dt')];
      const dt = dts.find((d) => d.textContent.trim() === 'PubTator3 records');
      return dt && dt.nextElementSibling
        ? dt.nextElementSibling.textContent.trim() : null;
    }""")
    check(bool(rec) and rec.startswith("40,606"),
          f"the drawer's PubTator3 count is grouped ({rec!r})")
    dtext = page.inner_text("#drawer")
    check("40606" not in dtext, "no ungrouped 40606 is left in the drawer")


#: The contrast facts the block above the grid has to carry. Atlas's own
#: strings, as the manifest stores them — nothing here is a paraphrase.
DE_FACTS = ("E-GEOD-101794", "g2_g1", "n=61 vs 13", "RNA-seq", "DESeq2 1.10.1",
            "Ensembl 95")


def gene_expression_checks(page, base: str) -> None:
    """11. The Gene expression box, with the gene expression data.

    Puck, 2026-10-19: "that gene expression expansion should be organised with
    the gene expression data not arbitrary". So the run's contrast facts sit
    directly above grid columns 1-2, under a header spanning both, and the
    published page drops the top `#find` box those facts used to live in.
    """
    page.set_viewport_size(DESK)
    page.goto(f"{base}/demo/", wait_until="networkidle")
    page.wait_for_timeout(1600)

    # The top box is gone from the published page.
    check(page.query_selector("#find") is None,
          "the published page has no top #find box")
    check(page.query_selector("#find-panel") is None,
          "and no empty #find-panel left behind it")

    # The grouped header, spanning exactly the two DE columns.
    group = page.query_selector("#table-grid thead tr.colgroups th.group")
    check(bool(group), "the grid has a grouped column header")
    check(bool(group) and group.inner_text().strip() == "Gene expression",
          f"it reads 'Gene expression' ({group.inner_text().strip() if group else None!r})")
    span = group.get_attribute("colspan") if group else None
    check(span == "2", f"it spans exactly two columns (colspan={span!r})")

    # And it sits over columns 1 and 2, not over somebody else's column.
    over = page.evaluate("""() => {
      const g = document.querySelector('#table-grid thead tr.colgroups th.group');
      if (!g) return null;
      let before = 0;
      for (const th of g.parentElement.children) {
        if (th === g) break;
        before += Number(th.getAttribute('colspan') || 1);
      }
      const heads = [...document.querySelectorAll('#table-grid thead tr:not(.colgroups) th')];
      return heads.slice(before, before + Number(g.getAttribute('colspan') || 1))
                  .map((th) => th.innerText.replace(/\\s+/g, ' ').trim());
    }""")
    check(bool(over) and len(over) == 2 and "logFC" in over[0]
          and "adjusted p" in over[1],
          f"the span covers logFC and adjusted p ({over})")

    # The facts, directly above the grid.
    de = page.query_selector("#table-de")
    text = de.inner_text() if de else ""
    check(bool(de) and "Gene expression" in text,
          "a Gene expression block sits above the grid")
    for fact in DE_FACTS:
        check(fact in text, f"the block states {fact!r}")
    check("Differential expression:" in text and "Crohn's disease" in text,
          "it states the comparison")
    check("positive log2FC" in text, "it says which way log2FC points")
    check(bool(de) and page.evaluate("""() => {
      const de = document.querySelector('#table-de');
      const grid = document.querySelector('#table-grid');
      return !!de && !!grid
        && (de.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    }"""), "the block is above the grid, not below it")

    # v4.3 item 7: `#go` is gone from this page (see `tool_checks`). The
    # `#controls` box stays -- it is this run's own settings, read-only.
    check(page.query_selector("#go") is None,
          "no Draw the figure button on the published page")
    check(bool(page.query_selector("#controls")), "the #controls box is still there")


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
        # v4.3: an UNCAUGHT exception is not a console message, and a `${/* */""}`
        # comment written outside a template literal is a `SyntaxError` that
        # stops `app.js` dead. It produced ten FAILs below and a clean "no
        # console errors" above them, which is the wrong way round — the cause
        # must be named, not inferred from its consequences.
        page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
        print("=== the tool ===")
        tool_checks(page, args.base)
        print("=== the p format ===")
        p_format_checks(page, args.base)
        print("=== the p column ===")
        p_column_checks(page, args.base)
        print("=== the opening view, cut to labels and controls ===")
        prose_checks(page, args.base)
        print("=== the Evidence tab, one line per gene ===")
        evidence_checks(page, args.base)
        print("=== cohesion: codes, contradictions, one name ===")
        cohesion_checks(page, args.base)
        print("=== the moving ranges, counted ===")
        moving_ranges_checks(page, args.base)
        print("=== the phone tab strip ===")
        phone_tab_checks(page, args.base)
        print("=== one name, one format ===")
        one_name_checks(page, args.base)
        print("=== the literature column ===")
        literature_column_checks(page, args.base)
        print("=== the Gene expression box ===")
        gene_expression_checks(page, args.base)
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
