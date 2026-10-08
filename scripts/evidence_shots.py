#!/usr/bin/env python
"""The evidence tab, live in the browser.

    .venv/bin/python scripts/evidence_shots.py [--port 8812] [--run <id>]

The tests assert the payload; this is the other half — the real stylesheet, the
real wrapping, the three severities as printed, and both colour schemes. Crops
follow `stage12_shots.py`: a narrow viewport at `deviceScaleFactor` 3 rather
than a laptop width, because Puck reads these on a phone and a 1,390 px panel
downscaled to a phone puts 12 px type at 3.4 px.

    evidence-light.png    the surface in light mode
    evidence-dark.png     the same run in dark mode, which is its own render
    evidence-probe.json   what the DOM actually says, so a shot that looks
                          right can be checked against the payload

`--run` defaults to the newest run on disk that carries a firing contradiction,
because a screenshot of a surface where every rule cleared demonstrates nothing.
The server is expected to be up already. This script starts nothing, fetches no
source and makes no paid call (AGENTS.md rules 8 and 9).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
PHONE_READABLE = {"width": 760, "height": 1500}

#: What the DOM is asked for. Every number here is also in the payload, so a
#: shot that looks right can be checked rather than trusted.
PROBE = """() => {
  const q = (s) => [...document.querySelectorAll(s)];
  return {
    tab_is_active: !!document.querySelector('.tab.active[data-tab="evidence"]'),
    pane_visible: !document.querySelector('#pane-evidence').hidden,
    cards: q('.ev-card').length,
    contradictions: q('.ev-findings > li.ev-contradiction').length,
    flags: q('.ev-findings > li.ev-flag').length,
    unevaluable: q('.ev-findings > li.ev-unevaluable').length,
    counts_line: (document.querySelector('.ev-counts') || {}).textContent
      ?.replace(/\\s+/g, ' ').trim(),
    note: (document.querySelector('#evidence .note') || {}).textContent
      ?.replace(/\\s+/g, ' ').trim(),
    first_card: (q('.ev-card')[0] || {}).textContent
      ?.replace(/\\s+/g, ' ').trim().slice(0, 400),
    // The rule-7 guard in the rendered DOM rather than in the payload, and it
    // reads the HEADLINES only. The pane as a whole necessarily contains
    // "score" and "recommend" because it says it does not produce them, so a
    // whole-pane match would fire on the disclaimer. What must never carry a
    // verdict is the sentence a finding states.
    banned_in_headlines: q('.ev-headline').map((e) =>
      e.textContent.toLowerCase()).filter((t) =>
      ['verdict', 'score', 'ranked', 'recommend', 'should']
        .some((w) => t.includes(w))),
  };
}"""


def a_run_with_a_contradiction(port: int) -> str:
    """The newest run carrying a firing contradiction, else a firing flag, else
    the newest readable one. A shot of an all-clear surface shows nothing."""
    base = f"http://127.0.0.1:{port}"
    with urllib.request.urlopen(f"{base}/api/runs?limit=200") as r:
        runs = [x["run_id"] for x in json.load(r)["runs"]]
    fallback = ""
    for rid in runs:
        try:
            with urllib.request.urlopen(f"{base}/api/run/{rid}/evidence") as r:
                d = json.load(r)
        except Exception:  # noqa: BLE001  -- a run the table cannot load
            continue
        fallback = fallback or rid
        if d["finding_counts"]["with_contradiction"]:
            return rid
    return fallback


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8812)
    ap.add_argument("--run", default="")
    args = ap.parse_args()

    run_id = args.run or a_run_with_a_contradiction(args.port)
    if not run_id:
        print("no run on disk carries a loadable evidence surface", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{args.port}/?run={run_id}"
    print(f"run {run_id}")

    probes = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            page = browser.new_page(viewport=PHONE_READABLE,
                                    device_scale_factor=3,
                                    color_scheme=scheme)
            page.goto(url, wait_until="networkidle")
            page.click('.tab[data-tab="evidence"]')
            page.wait_for_selector(".ev-card", timeout=15000)
            page.wait_for_timeout(300)
            probes[scheme] = page.evaluate(PROBE)
            page.screenshot(path=str(OUT / f"evidence-{scheme}.png"),
                            full_page=True)
            # The card carrying the contradiction, on its own. A full-page shot
            # of a 20,000 px surface proves it rendered; this is the one a
            # reader can actually read, and it is the thing the surface is for.
            card = page.locator(".ev-card").filter(
                has=page.locator(".ev-contradiction")).first
            if card.count():
                card.screenshot(
                    path=str(OUT / f"evidence-contradiction-{scheme}.png"))
            page.close()
        browser.close()

    (OUT / "evidence-probe.json").write_text(
        json.dumps({"run_id": run_id, "probes": probes}, indent=2) + "\n")
    for scheme, pr in probes.items():
        print(f"\n--- {scheme} ---")
        for k, v in pr.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
