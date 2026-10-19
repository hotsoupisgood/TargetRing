#!/usr/bin/env python3
"""Audit the published pages with a real browser, and report what is wrong.

v4.0, Puck 2026-10-19: "this looks bad. do a full audit of the ui please. revise
until you are happy with your work", with a phone screenshot of two empty boxes.

It drives Chromium over the tree `docs/` actually serves, at a phone width and a
laptop width, and reports MACHINE-CHECKABLE defects only -- the classes of thing
a screenshot review misses and a human reviewer should not have to count:

* **empty boxes**: a bordered, visible element with no text and no image in it,
  which is the finding in the screenshot;
* **contrast below WCAG AA**, computed from the composited colours actually
  painted, with the 4.5:1 / 3:1 split by font size;
* **overflow**: an element whose content is wider or taller than its own box, or
  that spills past the viewport;
* **clipping**: text cut off by a scroll boundary;
* **dead controls**: a button or link that no handler is bound to and that goes
  nowhere;
* **broken images**: `naturalWidth === 0`;
* **console errors and failed requests**.

It also opens every tab, every disclosure and a drawer, because a defect behind
a click is still a defect.

    python3 scripts/ui_audit.py --base http://127.0.0.1:8911/TargetRing
    python3 scripts/ui_audit.py --shots docs/showcase    # also write the four

Needs a Playwright with chromium; `~/ytpt/.venv` has one, so this script is run
with that interpreter rather than the repo's own venv (targetring does not
depend on a browser and should not start to).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PHONE = {"width": 390, "height": 844}
DESK = {"width": 1280, "height": 900}

# The audit, as one injected function. It runs in the page so it can read
# composited colour and measured geometry -- both of which only exist in a
# browser -- and returns plain data for Python to group and print.
PROBE = r"""
() => {
  const out = [];
  const seen = new Set();
  const add = (kind, el, note) => {
    const path = (e) => {
      const bits = [];
      for (let x = e; x && x.nodeType === 1 && bits.length < 4; x = x.parentElement) {
        bits.unshift(x.id ? "#" + x.id
          : x.tagName.toLowerCase() + (x.className && typeof x.className === "string"
              ? "." + x.className.trim().split(/\s+/).slice(0, 2).join(".") : ""));
        if (x.id) break;
      }
      return bits.join(" ");
    };
    const where = path(el);
    const key = kind + "|" + where + "|" + note;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({kind, where, note});
  };

  const vis = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== "none" && s.visibility !== "hidden"
      && parseFloat(s.opacity) > 0.05 && r.width > 1 && r.height > 1;
  };

  // --- composited background, walking up until something is opaque -----------
  const srgb = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92
    : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
  const parse = (s) => {
    const m = (s || "").match(/[\d.]+/g);
    if (!m) return null;
    return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1];
  };
  const over = (fg, bg) => fg.slice(0, 3).map((c, i) => c * fg[3] + bg[i] * (1 - fg[3]));
  const bgOf = (el) => {
    let acc = [255, 255, 255];
    const chain = [];
    for (let x = el; x; x = x.parentElement) chain.push(x);
    for (const x of chain.reverse()) {
      const c = parse(getComputedStyle(x).backgroundColor);
      if (c && c[3] > 0) acc = over(c, acc);
    }
    return acc;
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };

  const BORDERED = (s) =>
    (parseFloat(s.borderTopWidth) > 0 || parseFloat(s.borderLeftWidth) > 0)
    && s.borderTopStyle !== "none";

  // Does an ancestor scroll in this axis? A wide table inside `.scroll-x` is
  // not an overflow defect -- it is a scroll region doing its job -- and
  // reporting it buries the real ones. Checked on the ANCESTOR CHAIN rather
  // than on the element, which is the distinction the first pass of this audit
  // got wrong and spent 222 findings on.
  const scrollsX = (el) => {
    for (let x = el.parentElement; x; x = x.parentElement) {
      const o = getComputedStyle(x).overflowX;
      if (o === "auto" || o === "scroll") return true;
    }
    return false;
  };
  // Absolutely positioned or fixed: its size is not its parent's problem, and a
  // dropdown wider than its trigger is the normal case.
  const floating = (el) => {
    for (let x = el; x && x !== document.body; x = x.parentElement) {
      const p = getComputedStyle(x).position;
      if (p === "absolute" || p === "fixed") return true;
    }
    return false;
  };
  // ...and an ancestor whose overflow comes ONLY from such a descendant is not
  // overflowing either: `scrollWidth` on a static parent counts its absolutely
  // positioned children, so one 350 px dropdown made `main`, `#result` and
  // `#pane-figure` all report 553 px of content. Three findings, one dropdown,
  // nothing wrong.
  const onlyFloatingChildren = (el) =>
    [...el.children].length > 0
    && [...el.querySelectorAll("*")].some((x) => {
      const p = getComputedStyle(x).position;
      return (p === "absolute" || p === "fixed")
        && x.getBoundingClientRect().right > el.getBoundingClientRect().right + 1;
    });
  // An overlay: fixed, viewport-sized, translucent, and empty BY DESIGN. `#scrim`
  // is the drawer's backdrop. An empty box is a defect when it is a panel in the
  // layout, not when it is a sheet of dim over one.
  const overlay = (el, s, r) =>
    (s.position === "fixed" || s.position === "absolute")
    && r.width >= innerWidth - 2 && r.height >= innerHeight - 2;
  const FORM = (t) => ["INPUT", "SELECT", "TEXTAREA", "OPTION", "PROGRESS",
                       "METER"].includes(t);

  document.querySelectorAll("body *").forEach((el) => {
    if (!vis(el)) return;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const tag = el.tagName;
    if (["SCRIPT", "STYLE", "SVG", "PATH", "G", "RECT", "TEXT", "DEFS",
         "MARKER", "LINE", "CIRCLE", "USE", "CLIPPATH", "IMAGE"].includes(tag)) return;

    const text = (el.innerText || "").trim();
    const hasMedia = el.querySelector("img, svg, canvas, video, input, select, textarea");

    // 1. EMPTY BOXES. A bordered or filled container, big enough to read as a
    // panel, with nothing in it. The screenshot's finding.
    // A form control with a value in it is not an empty box: its text is in
    // `.value`, which `innerText` cannot see. `#disease` carrying "Crohn's
    // disease" was reported as empty by the first pass of this audit.
    const emptyControl = FORM(tag) && !(el.value || el.placeholder);
    if (!text && !hasMedia && r.height >= 16 && r.width >= 60
        && (!FORM(tag) || emptyControl) && !overlay(el, s, r)
        && (BORDERED(s) || (parse(s.backgroundColor) || [0,0,0,0])[3] > 0.02)
        && el.children.length === 0) {
      add("empty-box", el, `${Math.round(r.width)}x${Math.round(r.height)} px, `
        + `border ${s.borderTopWidth} ${s.borderTopStyle}, bg ${s.backgroundColor}`
        + (FORM(tag) ? " (a control with no value and no placeholder)" : ""));
    }

    // 2. OVERFLOW past the viewport, and past the element's own box. Neither
    // counts when an ancestor scrolls in that axis or when the element floats:
    // see `scrollsX` / `floating`.
    if (r.right > innerWidth + 1.5 && s.position !== "fixed"
        && !scrollsX(el) && !floating(el)) {
      add("overflow-viewport", el,
        `right edge at ${Math.round(r.right)} px in a ${innerWidth} px viewport`);
    }
    if (el.scrollWidth > el.clientWidth + 2 && s.overflowX === "visible"
        && el.clientWidth > 0 && !scrollsX(el) && !floating(el)
        && !onlyFloatingChildren(el) && !el.closest("svg")) {
      add("overflow-self", el,
        `content ${el.scrollWidth} px wide in a ${el.clientWidth} px box, overflow visible`);
    }
    if (el.scrollHeight > el.clientHeight + 2 && s.overflowY === "hidden"
        && text) {
      add("clipped", el,
        `content ${el.scrollHeight} px tall in a ${el.clientHeight} px box, overflow hidden`);
    }

    // 3. CONTRAST, on leaf elements that carry their own text.
    const ownText = [...el.childNodes]
      .filter((x) => x.nodeType === 3 && x.textContent.trim()).length > 0;
    if (ownText) {
      const fg = parse(s.color);
      if (fg) {
        const bg = bgOf(el);
        const got = ratio(over(fg, bg), bg);
        const px = parseFloat(s.fontSize);
        const bold = (parseInt(s.fontWeight, 10) || 400) >= 700;
        const large = px >= 24 || (bold && px >= 18.67);
        const need = large ? 3.0 : 4.5;
        if (got < need - 0.02) {
          add("contrast", el, `${got.toFixed(2)}:1 needs ${need}:1 `
            + `(${s.color} on rgb(${bg.map(Math.round).join(",")}), `
            + `${px}px${bold ? " bold" : ""}) — "${text.slice(0, 40)}"`);
        }
      }
    }

    // 4. TINY TEXT. Not a WCAG failure, but Puck's own review floor.
    if (ownText && parseFloat(s.fontSize) < 11.5 && text.length > 3) {
      add("tiny-text", el, `${s.fontSize} — "${text.slice(0, 40)}"`);
    }
  });

  // 5. BROKEN IMAGES.
  document.querySelectorAll("img").forEach((im) => {
    if (im.complete && im.naturalWidth === 0) {
      add("broken-image", im, im.getAttribute("src") || "(no src)");
    }
  });

  // 6. DEAD CONTROLS. A visible button with no onclick, no listener we can see
  // and no form to submit; a link with no href or href="#".
  document.querySelectorAll("button").forEach((b) => {
    if (!vis(b)) return;
    if (!b.onclick && !b.form && b.type !== "submit"
        && !b.closest("summary") && !b.dataset.tab && !b.dataset.gene
        && !b.id.includes("close")) {
      add("maybe-dead-button", b, `"${(b.innerText || "").trim().slice(0, 30)}" `
        + `has no onclick property (may use addEventListener)`);
    }
  });
  // A visible `<a>` with no href is not a link, it is text that looks like one.
  // Ten of these -- every item in the three download menus -- were the audit's
  // sharpest finding on 2026-10-19: `render()` fills those hrefs and never runs
  // on the disk path. Checked only when VISIBLE, so a closed `<details>`
  // menu's items are still reached (the audit opens every disclosure first).
  document.querySelectorAll("a").forEach((a) => {
    if (!vis(a)) return;
    const h = a.getAttribute("href");
    if (h === null || h === "" || h === "#") {
      add("dead-link", a, `"${(a.innerText || "").trim().slice(0, 30)}" href=${h}`);
    }
  });

  return out;
}
"""


def open_everything(page) -> None:
    """Every tab, every disclosure, one drawer. A defect behind a click counts."""
    for d in page.query_selector_all("details:not([open])"):
        try:
            d.evaluate("(e) => e.open = true")
        except Exception:
            pass
    page.wait_for_timeout(200)


def audit_page(page, url: str, size: dict, label: str) -> dict:
    errors: list[str] = []
    failed: list[str] = []
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("requestfailed",
            lambda r: failed.append(f"{r.method} {r.url} — {r.failure}"))
    page.set_viewport_size(size)
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(700)

    findings: list[dict] = []
    tabs = page.query_selector_all(".tab")
    panes = [None] + [t.get_attribute("data-tab") for t in tabs]
    for want in panes:
        if want:
            t = page.query_selector(f'.tab[data-tab="{want}"]')
            if not t:
                continue
            t.click()
            page.wait_for_timeout(500)
        open_everything(page)
        for f in page.evaluate(PROBE):
            f["view"] = f"{label} / {want or 'first view'}"
            findings.append(f)

    # One drawer, from whichever surface offers a gene. Dispatched rather than
    # clicked: see the note in `main` about overlapping arcs.
    try:
        page.query_selector('.tab[data-tab="table"]').click()
        page.wait_for_timeout(500)
        g = page.evaluate("""() => {
          const el = document.querySelector('#table-grid .gene')
            || document.querySelector('#figure [id^="cell|"]');
          if (!el) return null;
          el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          return el.textContent.trim() || el.id;
        }""")
        if g:
            page.wait_for_timeout(1100)
            if page.query_selector("#drawer:not([hidden])"):
                open_everything(page)
                for f in page.evaluate(PROBE):
                    f["view"] = f"{label} / drawer"
                    findings.append(f)
                page.query_selector("#drawer-close").click()
    except Exception as e:  # noqa: BLE001
        findings.append({"kind": "drawer-unreachable", "where": "#table-grid .gene",
                         "note": str(e)[:160], "view": label})
    return {"findings": findings, "console": errors, "requestfailed": failed}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8911/TargetRing")
    ap.add_argument("--shots", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    report: dict = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        # Pinned LIGHT, because the pages are and the audit must see what a
        # reader sees -- auditing them under an emulated dark scheme would be
        # auditing a rendering the stylesheet refuses to produce.
        # `device_scale_factor=1`, so a screenshot's pixel width IS the CSS
        # width it was taken at -- 390 and 1280. A 2x shot is a nicer image and
        # a worse record: the filename says 390 and the file says 780.
        ctx = b.new_context(color_scheme="light", device_scale_factor=1)
        page = ctx.new_page()
        for label, size in (("phone-390", PHONE), ("desktop-1280", DESK)):
            report[f"tool/{label}"] = audit_page(
                page, f"{args.base}/demo/", size, label)
        for label, size in (("phone-390", PHONE), ("desktop-1280", DESK)):
            page.set_viewport_size(size)
            page.goto(f"{args.base}/flow/", wait_until="networkidle")
            page.wait_for_timeout(700)
            fs = []
            for nd in page.query_selector_all("svg g.node"):
                nd.click()
                page.wait_for_timeout(120)
                for f in page.evaluate(PROBE):
                    f["view"] = f"flow/{label}"
                    fs.append(f)
            report[f"flow/{label}"] = {"findings": fs, "console": [],
                                       "requestfailed": []}

        if args.shots:
            out = Path(args.shots)
            out.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size(PHONE)
            page.goto(f"{args.base}/demo/", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / "09-tool-phone.png"), full_page=True)
            page.set_viewport_size(DESK)
            page.goto(f"{args.base}/demo/", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / "10-tool-desktop.png"), full_page=True)
            # A drawer open, on the desktop width, from the figure itself --
            # which is the interaction this revision added.
            #
            # Dispatched rather than clicked through Playwright's actionability
            # checks: the arcs overlap by design (twelve concentric bands on one
            # spoke), so the outer ring's path "intercepts pointer events" for
            # the inner one and the retry loop never settles. A real reader
            # clicks whichever band is on top and gets that gene's drawer, which
            # is exactly what dispatching the event on one does.
            opened = page.evaluate("""() => {
              const el = document.querySelector('#figure [id^="cell|"]')
                || document.querySelector('#table-grid .gene');
              if (!el) return null;
              el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
              return el.id || el.textContent.trim();
            }""")
            page.wait_for_timeout(1500)
            if opened and page.query_selector("#drawer:not([hidden])"):
                page.screenshot(path=str(out / "11-drawer.png"), full_page=True)
            else:
                print(f"  WARNING: no drawer opened (clicked {opened!r}); "
                      "11-drawer.png not written")
            page.goto(f"{args.base}/flow/", wait_until="networkidle")
            page.wait_for_timeout(900)
            page.screenshot(path=str(out / "12-flow.png"), full_page=True)
            print(f"screenshots in {out}")
        b.close()

    total = 0
    for view, r in report.items():
        by: dict[str, list[dict]] = {}
        for f in r["findings"]:
            by.setdefault(f["kind"], []).append(f)
        print(f"\n=== {view} ===")
        if r["console"]:
            print(f"  console errors: {len(r['console'])}")
            for e in r["console"][:6]:
                print(f"    {e[:170]}")
        if r["requestfailed"]:
            print(f"  failed requests: {len(r['requestfailed'])}")
            for e in r["requestfailed"][:6]:
                print(f"    {e[:170]}")
        total += len(r["console"]) + len(r["requestfailed"])
        for kind, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
            print(f"  {kind}: {len(items)}")
            for f in items[:8]:
                print(f"    [{f['view']}] {f['where']}: {f['note'][:150]}")
            total += len(items)
    print(f"\nTOTAL {total} finding(s)")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
