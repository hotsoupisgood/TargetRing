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

    # 3 of the brief: the two controls Puck is deciding separately stay.
    check(bool(page.query_selector("#go")), "the Draw the figure button is still there")
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
        print("=== the tool ===")
        tool_checks(page, args.base)
        print("=== the p format ===")
        p_format_checks(page, args.base)
        print("=== the p column ===")
        p_column_checks(page, args.base)
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
