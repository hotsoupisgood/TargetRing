"""v3.8, live: the per-column dim filter's three treatments, measured off the PNG.

The tests assert the payload, the markup and the stylesheet. They cannot assert the
thing that actually matters about this feature, which is that the three treatments
are **telling apart on a screen, in greyscale and in an exported PNG** — and
distinguishable from hatched `no_data`, because grey already means "we have no
value here" on this grid and a second meaning for it would be
`AGENTS.md:17`'s plausible wrong output in a new place.

So this script drives the real page in a real browser, screenshots the grid, and
reads the pixels back:

  * per treatment, the gene-column **rail** luminance, the **mean luminance** of
    the row's data cells, and their **local contrast** (standard deviation);
  * the same three for a hatched `no_data` cell, which is what the treatments must
    not be confused with;
  * all of it in greyscale as well as in colour, since greyscale is the case where
    a hue-only encoding would collapse.

It also proves the claim the tests can only prove server-side: that **clearing the
filter restores the grid exactly**, by comparing `#table-grid` innerHTML before the
filter and after clearing it, byte for byte.

Taken on a run opened from disk with `?run=<id>` — no figure in the session and
nothing in `_runs` — because that is the case a reader most often arrives in and
the one v3.7 found broken.

    v38-0-three-states.png   the three treatments in adjacent rows, tight crop
    v38-1-count-line.png     the count line above a filtered grid
    v38-2-page.png           1440 x 900, the table tab as a reader meets it
    v38-3-greyscale.png      v38-0 desaturated, which is the test of the encoding
    v38-4-control.png        the dim control, which must not look like §3.2's
    v38-probe.json           computed styles, the luminances, the restore check

Run:  .venv/bin/python scripts/v38_dim_probe.py --run <run_id> [--port 8811]
The server is expected to be up already; this script starts nothing, writes nothing
into the run, and fetches no source (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
LAPTOP = {"width": 1440, "height": 900}
# Puck reads these on a phone. A tight crop at 3x pixels is legible there; a
# downscaled full page is not (the reasoning is `scripts/v37_shots.py`'s).
SCALE = 3

# What the page is asked to dim on, chosen at run time: the column has to have
# both measured and unmeasured genes for all three treatments to be on screen at
# once, and there is no point pinning one.
PROBE_STYLES = """() => {
  const px = (el, p) => el ? getComputedStyle(el)[p] : null;
  const rows = [...document.querySelectorAll('#table-grid tbody tr')];
  const pick = (cls) => rows.find((r) => r.classList.contains(cls));
  const plain = rows.find((r) => !r.className.trim());
  const dimmed = pick('dim-out'), un = pick('dim-unevaluable');
  const of = (tr) => {
    if (!tr) return null;
    const gene = tr.querySelector('.gene-col');
    const cell = [...tr.querySelectorAll('td')].find((t) => !t.classList.contains('kcell'));
    const tag = tr.querySelector('.dimtag');
    return {
      gene: (tr.querySelector('.gene') || {}).textContent,
      classes: tr.className,
      rail: px(gene, 'boxShadow'),
      gene_bg: px(gene, 'backgroundColor'),
      cell_opacity: px(cell, 'opacity'),
      tag_text: tag ? tag.textContent.trim() : null,
      tag_px: tag ? parseFloat(px(tag, 'fontSize')) : null,
      tag_colour: tag ? px(tag, 'color') : null,
      title: tr.getAttribute('title'),
    };
  };
  const nodata = document.querySelector('#table-grid td.st-no_data');
  return {
    n_rows: rows.length,
    match: of(plain), dimmed: of(dimmed), unevaluable: of(un),
    // `no_data` is the thing the three treatments must not be mistaken for, and
    // its encoding is unchanged by any of this.
    no_data_cell: nodata ? {
      text: nodata.textContent.trim(),
      background: px(nodata, 'backgroundImage').slice(0, 80),
      opacity: px(nodata, 'opacity'),
    } : null,
    dim_key_columns: [...document.querySelectorAll('#table-grid thead th.dim-key')]
      .map((t) => t.textContent.trim().split('\\n')[0]),
    count_line: (document.querySelector('#table-dim-counts') || {}).textContent,
    count_line_px: parseFloat(px(document.querySelector('#table-dim-counts'), 'fontSize')),
    // Item 1's assertion, on screen rather than in the payload: the counts a
    // reader sees above the grid are the run's, and dimming did not move them.
    table_counts: (document.querySelector('#table-meta .counts') || {}).textContent,
    // Item 4: is the filter stated where a screenshot of the grid would catch it?
    count_line_above_grid: !!document.querySelector(
      '#table-dim-counts + #table-grid'),
    smallest_new_px: Math.min(...[...document.querySelectorAll(
      '#table-dim, #table-dim-counts, .dimtag')].flatMap((el) =>
      [el, ...el.querySelectorAll('*')].map((e) => parseFloat(getComputedStyle(e).fontSize))
      .filter((n) => n > 0))),
  };
}"""


def band(img: Image.Image, box: tuple[float, float, float, float]) -> dict[str, float]:
    """Mean luminance and local contrast over a rectangle, 0-255."""
    crop = img.convert("L").crop(tuple(int(round(v)) for v in box))
    px = list(crop.getdata())
    if not px:
        return {"mean": -1.0, "sd": -1.0}
    return {"mean": round(statistics.fmean(px), 2),
            "sd": round(statistics.pstdev(px), 2) if len(px) > 1 else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--port", type=int, default=8811)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    report: dict[str, object] = {"run": a.run, "port": a.port, "base": base}

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport=LAPTOP, device_scale_factor=SCALE).new_page()
        pg.goto(f"{base}/?run={a.run}", wait_until="networkidle")
        pg.wait_for_selector("#table-grid table.grid")

        report["arrived_without_a_figure_in_session"] = pg.evaluate(
            "() => typeof RUN === 'undefined' || RUN === null")

        # --- the grid as it is, before anything is dimmed -------------------
        before_html = pg.eval_on_selector("#table-grid", "el => el.innerHTML")
        before_counts = pg.eval_on_selector("#table-meta .counts", "el => el.textContent")

        # A column with both measured and unmeasured genes, off the payload the
        # page already has, and a bound between its smallest and largest value.
        chosen = pg.evaluate("""() => {
          const d = TABLE.data;
          for (const r of d.rings.filter((x) => x.dimmable)) {
            const vs = d.genes.map((g) => g.cells[r.key])
              .filter((c) => c.state === 'value').map((c) => c.value).sort((x, y) => x - y);
            const missing = d.genes.filter((g) => g.cells[r.key].state !== 'value').length;
            if (vs.length >= 2 && missing && vs[0] !== vs[vs.length - 1])
              return { key: r.key, label: r.column_label,
                       threshold: vs[Math.floor(vs.length / 2)] };
          }
          return null;
        }""")
        if not chosen:
            print("no column on this run has both measured and unmeasured genes",
                  file=sys.stderr)
            return 2
        report["dimmed_on"] = chosen

        def set_dim() -> None:
            pg.select_option("#dim-col", chosen["key"])
            pg.select_option("#dim-op", "ge")
            pg.fill("#dim-thr", str(chosen["threshold"]))
            pg.click("#dim-add")
            pg.wait_for_selector("#table-grid tbody tr.dim-out")
            pg.wait_for_selector("#table-grid tbody tr.dim-unevaluable")

        # --- does clearing restore the grid exactly? -------------------------
        # Done FIRST and on the unsorted grid, so the comparison isolates the dim
        # filter. `TABLE.dim = []` is set synchronously and the grid is redrawn
        # after a fetch, so the wait is on the DOM having lost the classes rather
        # than on the state variable — waiting on the variable reads the old HTML.
        set_dim()
        pg.click("#dim-clear")
        pg.wait_for_function("() => (TABLE.dim || []).length === 0 && "
                             "document.querySelectorAll('#table-grid [class*=dim-]')"
                             ".length === 0")
        after_html = pg.eval_on_selector("#table-grid", "el => el.innerHTML")
        report["restore"] = {
            "grid_html_identical": after_html == before_html,
            "bytes_before": len(before_html), "bytes_after": len(after_html),
            "counts_line_identical":
                pg.eval_on_selector("#table-meta .counts", "el => el.textContent")
                == before_counts,
            "count_line_gone": pg.eval_on_selector(
                "#table-dim-counts", "el => el.innerHTML") == "",
            "no_dim_class_left": pg.evaluate(
                "() => document.querySelectorAll('#table-grid [class*=dim-]').length"),
        }

        # --- and now the shots -----------------------------------------------
        # Sorted by the same column so the three treatments land in adjacent rows:
        # matches, then the ones that miss, then the no-value block below its own
        # divider. That is what makes a tight side-by-side crop possible at all.
        pg.eval_on_selector(f"#table-grid thead th[data-ordering='ring:{chosen['key']}']",
                            "el => el.click()")
        pg.wait_for_function("() => TABLE.sort.length === 1")
        set_dim()

        report["styles"] = st = pg.evaluate(PROBE_STYLES)

        # Fail loudly rather than measure a treatment that is not there. The first
        # run of this script found the dimmed row with `box-shadow: none`, because an
        # unterminated comment in `app.css` had made the CSS parser swallow the rule
        # after it — and every text assertion in the test suite still passed. An
        # instrument that reports a missing encoding as a small number is worse than
        # no instrument.
        problems = []
        if st["dimmed"]["cell_opacity"] == "1":
            problems.append("the dimmed row's data cells are at full opacity")
        for name in ("dimmed", "unevaluable"):
            if st[name]["rail"] in (None, "none"):
                problems.append(f"the {name} row has no rail")
            if not st[name]["tag_text"]:
                problems.append(f"the {name} row has no word on it")
            if (st[name]["tag_px"] or 0) < 12.0:
                problems.append(f"the {name} row's word is under the 12.0 px floor")
        if st["match"]["rail"] not in (None, "none"):
            problems.append("a MATCHING row carries a treatment; clearing the filter "
                            "would then have something to undo")
        if st["unevaluable"]["cell_opacity"] != "1":
            problems.append("the unevaluable row is faded — it is unresolved, not "
                            "de-emphasised (PLAN §3.2 rule 1)")
        if not st["count_line_above_grid"]:
            problems.append("the count line is not directly above the grid")
        if st["smallest_new_px"] < 12.0:
            problems.append(f"new type at {st['smallest_new_px']} px, under the floor")
        report["problems"] = problems

        # --- the crop, and the pixels ---------------------------------------
        # The union of one row of each treatment plus the head, cut at the dimmed
        # column's right edge so the crop stays phone-legible.
        geom = pg.evaluate("""(key) => {
          // PAGE coordinates, not element-relative: the crops are taken with
          // `page.screenshot(clip=...)`, which is the only screenshot that takes a
          // rectangle, and the pixel measurements below subtract the clip's own
          // origin back off. One coordinate system, converted once.
          const rel = (el) => {
            const b = el.getBoundingClientRect();
            return { x: b.left + scrollX, y: b.top + scrollY,
                     w: b.width, h: b.height };
          };
          const g = document.querySelector('#table-grid');
          const rows = [...document.querySelectorAll('#table-grid tbody tr')];
          const idx = (cls) => rows.findIndex((r) => r.classList.contains(cls));
          const plainAt = rows.findIndex((r) => !r.className.trim());
          const first = Math.max(0, Math.min(plainAt, idx('dim-out')) - 1);
          const last = Math.min(rows.length - 1,
            Math.max(idx('dim-out'), idx('dim-unevaluable')) + 1);
          const th = [...document.querySelectorAll('#table-grid thead th')];
          const keyTh = th.find((t) => t.dataset.ordering === 'ring:' + key);
          const dataCell = (r) => [...r.querySelectorAll('td')].find(
            (t) => !t.classList.contains('kcell'));
          const named = {};
          const put = (name, r) => { named[name] = { row: rel(r),
            gene: rel(r.querySelector('.gene-col')), cell: rel(dataCell(r)) }; };
          put('match', rows[plainAt]);
          put('dim-out', rows[idx('dim-out')]);
          put('dim-unevaluable', rows[idx('dim-unevaluable')]);
          // A hatched `no_data` cell in a row that is NOT dimmed, so the comparison
          // is against the encoding as it normally reads.
          // A hatched cell that is NOT in a faded row, so the comparison is against
          // the `no_data` encoding as it normally reads.
          const nd = [...document.querySelectorAll('#table-grid td.st-no_data')]
            .find((t) => !t.closest('tr').classList.contains('dim-out'));
          return {
            grid: rel(g), head: rel(th[0]),
            first: rel(rows[first]), last: rel(rows[last]),
            key_right: keyTh ? rel(keyTh).x + rel(keyTh).w : rel(th[3]).x,
            scroll_width: g.scrollWidth, scroll_left: g.scrollLeft,
            no_data: nd ? rel(nd) : null, named,
          };
        }""", chosen["key"])
        report["geometry"] = geom

        grid_png = OUT / "v38-0-three-states.png"
        # The clip: the head plus the rows either side of the two treated ones, cut
        # at the dimmed column's right edge. Tight, because Puck reads on a phone and
        # a 1,390 px grid downscaled to 390 px puts 12 px type at 3.4 px.
        clip = {"x": geom["grid"]["x"], "y": geom["head"]["y"],
                "width": min(geom["key_right"], geom["grid"]["x"]
                             + geom["grid"]["w"]) - geom["grid"]["x"] + 2,
                "height": geom["last"]["y"] + geom["last"]["h"] - geom["head"]["y"] + 2}
        # `full_page` so the clip is in PAGE coordinates: the grid sits below the
        # fold on a 900 px viewport and a viewport-relative clip misses it.
        pg.screenshot(path=str(grid_png), clip=clip, full_page=True)
        report["clip"] = clip

        # The count line with the grid under it, which is item 4's whole point: a
        # screenshot of a filtered table has to say it is filtered.
        pg.screenshot(path=str(OUT / "v38-1-count-line.png"), full_page=True,
                      clip=_union_clip(pg, "#table-dim-counts", "#table-grid",
                                       max_h=430))
        pg.screenshot(path=str(OUT / "v38-2-page.png"))
        # The control on its own, because "it must not look like §3.2's" is a claim
        # about a thing you look at.
        pg.screenshot(path=str(OUT / "v38-4-control.png"), full_page=True,
                      clip=_union_clip(pg, "#table-dim", "#table-dim", max_h=340))

        # --- greyscale, off the PNG we just wrote ---------------------------
        img = Image.open(grid_png)
        grey = img.convert("L")
        grey.save(OUT / "v38-3-greyscale.png")
        S = SCALE
        ox, oy = clip["x"], clip["y"]

        def rect(r, pad=2):
            """An element's box in the crop's own pixels."""
            return ((r["x"] - ox) * S + pad, (r["y"] - oy) * S + pad,
                    (r["x"] - ox + r["w"]) * S - pad,
                    (r["y"] - oy + r["h"]) * S - pad)

        def rail(r):
            """The 5 CSS px inset band on the gene column, inset by half a CSS pixel
            at each edge so the measurement is the band and not its antialiasing."""
            return ((r["x"] - ox) * S + S // 2, (r["y"] - oy) * S + 4 * S,
                    (r["x"] - ox) * S + 5 * S - S // 2,
                    (r["y"] - oy + r["h"]) * S - 4 * S)

        measured: dict[str, object] = {}
        for name, parts in geom["named"].items():
            measured[name] = {
                "rail": band(img, rail(parts["gene"])),
                "rail_grey": band(grey, rail(parts["gene"])),
                "data_cell": band(img, rect(parts["cell"])),
                "data_cell_grey": band(grey, rect(parts["cell"])),
                "gene_cell_grey": band(grey, rect(parts["gene"])),
            }
        if geom["no_data"]:
            measured["hatched_no_data_cell"] = {
                "data_cell": band(img, rect(geom["no_data"])),
                "data_cell_grey": band(grey, rect(geom["no_data"])),
            }
        report["luminance"] = measured

        # The separations, stated as numbers rather than as an impression. Mean
        # luminance for dimmed-vs-match (opacity), rail luminance for
        # dimmed-vs-unevaluable (hue -> lightness), and local contrast for
        # anything-vs-the-hatch (texture).
        g = lambda k, p="data_cell_grey": measured[k][p]["mean"]
        sd = lambda k, p="data_cell_grey": measured[k][p]["sd"]
        report["separation_greyscale"] = {
            "dimmed_vs_match_mean": round(g("dim-out") - g("match"), 2),
            "unevaluable_vs_dimmed_rail_mean": round(
                measured["dim-unevaluable"]["rail_grey"]["mean"]
                - measured["dim-out"]["rail_grey"]["mean"], 2),
            "unevaluable_vs_match_gene_cell_mean": round(
                measured["dim-unevaluable"]["gene_cell_grey"]["mean"]
                - measured["match"]["gene_cell_grey"]["mean"], 2),
            "hatch_local_contrast_sd": sd("hatched_no_data_cell")
            if "hatched_no_data_cell" in measured else None,
            "dimmed_cell_local_contrast_sd": sd("dim-out"),
        }

        br.close()

    (OUT / "v38-probe.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("dimmed_on", "separation_greyscale", "restore")}, indent=2))
    print(f"\nwrote {OUT}/v38-probe.json and five PNGs")
    probs = report.get("problems") or []
    if not report["restore"]["grid_html_identical"]:
        probs = list(probs) + ["clearing the filter did not restore the grid exactly"]
    sep = report["separation_greyscale"]
    # The three treatments have to be apart in greyscale, not only in colour. The
    # thresholds are what the encoding was designed to clear, not what it happened
    # to measure: opacity is the dimmed row's whole channel and the rails are the
    # glance-level discriminator.
    if abs(sep["dimmed_vs_match_mean"]) < 25:
        probs.append(f"dimmed and matching rows are only "
                     f"{sep['dimmed_vs_match_mean']} luminance apart in greyscale")
    if abs(sep["unevaluable_vs_dimmed_rail_mean"]) < 35:
        probs.append(f"the two rails are only "
                     f"{sep['unevaluable_vs_dimmed_rail_mean']} apart in greyscale")
    if probs:
        print("\nFAIL:\n  " + "\n  ".join(probs), file=sys.stderr)
        return 1
    print("PASS — three treatments distinguishable in colour and in greyscale, "
          "and clearing restores the grid byte for byte")
    return 0


def _union_clip(pg, a: str, b: str, max_h: int = 500) -> dict[str, float]:
    """A clip covering two elements, in PAGE coordinates, capped in height so the
    crop stays readable on a phone."""
    return pg.evaluate("""([a, b, maxH]) => {
      const x = document.querySelector(a).getBoundingClientRect();
      const y = document.querySelector(b).getBoundingClientRect();
      return { x: Math.min(x.left, y.left) + scrollX - 2,
               y: Math.min(x.top, y.top) + scrollY - 2,
               width: Math.min(Math.max(x.width, y.width) + 4, 1400),
               height: Math.min(Math.max(x.bottom, y.bottom)
                                - Math.min(x.top, y.top) + 4, maxH) };
    }""", [a, b, max_h])


if __name__ == "__main__":
    raise SystemExit(main())
