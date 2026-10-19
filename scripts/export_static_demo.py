#!/usr/bin/env python3
"""Export the read-only Crohn's demo as a static site under `docs/demo/`.

Why this exists: the demo mode (`AGENTS.md` rule 10 as amended, 2026-10-15)
already answers nothing but GETs, for nothing but the four runs in
`config.DEMO_RUNS`, from nothing but files those runs wrote. A surface with that
shape does not need a process behind it. Crawling it once and writing the
answers to disk gives the same pages on GitHub Pages with no box to pay for, no
port on a public interface and — the part rule 10 cares about — no machine that
could be made to fetch, analyse or call a model, because there is no machine.

What it does NOT do: touch `targetring/static/`, `runs/`, or the server's
demo-mode behaviour. The exported `index.html` is a rewritten COPY with
`scripts/static_demo_shim.js` injected ahead of `app.js`; the app's own files
are read, never written.

How it crawls: starts `uvicorn` on a loopback port with `TARGETRING_DEMO=1` and
no `ANTHROPIC_API_KEY` (cleared from the child's environment, whatever the
parent shell has), then asks for every route `static/app.js` can ask for. The
forbidden ones are asked for too, and the export asserts each is still refused
— so a run of this script is also a check that the gate holds.

    .venv/bin/python scripts/export_static_demo.py
    .venv/bin/python scripts/export_static_demo.py --out /tmp/try --keep-slides

Nothing here is paid and nothing here is live: the only host contacted is
127.0.0.1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

# The page files `index.html` and `header-wash.css` ask for by absolute path.
PAGE_FILES = ("app.js", "app.css", "header-wash.css",
              "header-wash.png", "header-wash-dark.png")

# The flat JSON routes `init()` and the demo panel read.
# v4.2 adds `/api/criteria` — the three GO / NO-GO criteria and their bounds,
# read from `recommend.current_thresholds()`, which the Table tab opens with.
FLAT_JSON = ("/api/health", "/api/regions", "/api/orderings",
             "/api/example", "/api/runs", "/api/criteria")

# Per-run artefacts the WEB UI loads or links to. `figure-slide.svg` and
# `figure-slide.png` are deliberately absent: they are the deck export, nothing
# in `app.js` references them, and the two of them are 1.4 MB per run — 38% of
# the whole export for a file no page opens. `--keep-slides` puts them back.
RUN_ARTEFACTS = ("figure.png", "figure.svg", "figure-web.svg",
                 "legend.png", "legend.svg",
                 "manifest.json", "values.csv", "ledger.csv", "caption.txt")
SLIDE_ARTEFACTS = ("figure-slide.svg", "figure-slide.png")

# Per-run JSON payloads, saved as `<name>.json` because `/api/run/<id>/values`
# is a directory on a static host and `values` is not a filename.
#
# `/api/run/<id>/scales` is not here: it answers only for a run still in this
# process's memory ("not in memory; re-submit the table"), and the page asks for
# it only from `showRun`, which a disk-loaded run never reaches. Exporting a
# route the demo itself cannot answer would be exporting a 404.
# v4.0 adds `scales`. The comment this replaces said it "answers only for a run
# still in this process's memory" and that the page asks for it only from
# `showRun`, which a disk-loaded run never reaches — both were true, and both
# stopped being true in v4.0: the route answers from `values.csv`, and
# `showToolFigure` asks for it to fill the decode panel beside the circle.
# Leaving it out was why the published page had two empty bordered boxes where
# the cell states and the ring scales belong (found by the UI audit, 2026-10-19).
RUN_JSON = ("values", "evidence", "scales")

# Every path the demo gate must still refuse, asked for so the export can say it
# was. Mirrors `app._DEMO_FORBIDDEN_*` and `tests/test_demo.py`.
MUST_REFUSE = ("/api/diseases?q=crohn", "/api/contrasts?disease=EFO_0000384",
               "/api/contrast/E-GEOD-101794/g2_g1/genes?n=60",
               "/api/autopick?disease=EFO_0000384",
               "/api/region-for?disease=Crohn%20disease",
               "/api/analyse/progress/x")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(port: int, run_ids: tuple[str, ...]) -> subprocess.Popen:
    """uvicorn, loopback only, demo mode, demonstrably no key.

    `ANTHROPIC_API_KEY` is *popped* rather than left to the demo gate: the gate
    refuses the routes that would spend, but a box that holds a key it cannot
    use is still a box that holds a key (rule 10: "A demo box never holds an API
    key"). The child cannot read one from anywhere — nothing in the app reads
    `.env` itself, the shell does.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["TARGETRING_DEMO"] = "1"
    env["TARGETRING_DEMO_RUNS"] = ",".join(run_ids)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "targetring.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def wait_ready(client: httpx.Client, proc: subprocess.Popen) -> dict:
    for _ in range(100):
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode()[-2000:]
            raise SystemExit(f"uvicorn exited {proc.returncode}:\n{err}")
        try:
            r = client.get("/api/health", timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise SystemExit("the demo server never answered /api/health")


_ABS_PATH = re.compile(r"/Users/[^\s\"']*")


def scrub(obj):
    """Strip this machine out of a payload before it is published.

    One real hit today: `manifest.llm.ledger_path`, the absolute path of
    `runs/spend-ledger.jsonl`. The page falls back to the relative name when the
    key is missing (`llmAccountsHtml`), so dropping it loses no sentence — and
    the ledger FILE is not exported at all, by any route. The regex is the
    backstop for the next manifest field that quietly carries a home directory.
    """
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k != "ledger_path"}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return _ABS_PATH.sub("(local path, not published)", obj)
    return obj


def date_the_spend(manifest: dict) -> dict:
    """Stamp a published manifest's dollar totals with the date they were measured.

    v4.0, Puck 2026-10-19: "just date it because the costs all change", and the
    requirement that every dollar figure on the page or in the exported manifest
    carries its measured date.

    `spend.manifest_block` writes `accounts_measured_utc` from v4.0 on, so a run
    rendered after this change arrives already dated and is left exactly as it
    is. The runs being published were written on 2026-10-18 and have no such key
    — and the date that is TRUE of their numbers is the run's own timestamp,
    because that is when the fold happened. It is not today: re-exporting an old
    manifest tomorrow must not relabel yesterday's balances as current, which is
    the entire failure Puck named.

    So: never overwritten, only filled in, and only from the manifest's own
    recorded timestamp.
    """
    llm = manifest.get("llm")
    if not isinstance(llm, dict) or llm.get("accounts_measured_utc"):
        return manifest
    when = manifest.get("timestamp_utc")
    if not when:
        return manifest
    llm["accounts_measured_utc"] = when
    llm["accounts_measured_note"] = (
        "this manifest predates the measured-date field; the totals are the "
        "ledger as folded when this run was written, so the run's own timestamp "
        "is when they were measured")
    return manifest


def write(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def get(client: httpx.Client, url: str, expect: int = 200) -> httpx.Response:
    r = client.get(url, timeout=120.0)
    if r.status_code != expect:
        raise SystemExit(f"GET {url} -> {r.status_code} (wanted {expect}): "
                         f"{r.text[:300]}")
    return r


def only_exported_artefacts(body: Any, keep_slides: bool) -> Any:
    """Trim `artefacts` to the files this export actually writes.

    The payload's `artefacts` list is read off the RUN's directory by
    `table.artefacts_on_disk`, which is right for the app — the app serves that
    directory. The export does not: `SLIDE_ARTEFACTS` is deliberately left out
    (the deck pair is +1.4 MB a run and nothing on the page shows it), so the
    exported `values.json` was advertising two files the exported tree does not
    contain, and `diskRunDownloads` duly set two menu items pointing at them.

    Found by the 2026-10-19 button-flow audit: `Slide SVG (16:9)` and
    `Slide PNG (16:9)` were live items in the Figure menu of the published
    page, and both 404ed on click. `diskRunDownloads` already removes an item
    whose file is not in the list — it was being told the wrong list.

    So the trim happens here rather than in `view.py`: the server's answer is
    correct about the server, and the export is the thing that knows what it
    chose to leave behind.
    """
    if keep_slides or not isinstance(body, dict):
        return body
    arts = body.get("artefacts")
    if isinstance(arts, list):
        body["artefacts"] = [a for a in arts if a not in SLIDE_ARTEFACTS]
    return body


def save_json(client: httpx.Client, out: Path, url: str, rel: str,
              keep_slides: bool = False) -> int:
    # `date_the_spend` runs on every payload, not just the manifest: the spend
    # table the PAGE draws comes from `values.json`'s own `llm` block
    # (`renderCaveats` is called with the table payload for a run read off
    # disk), so dating only the manifest would date the file nobody reads and
    # leave the rendered dollars bare. It is a no-op on a payload with no `llm`.
    body = only_exported_artefacts(
        date_the_spend(scrub(get(client, url).json())), keep_slides)
    return write(out / rel, json.dumps(body, indent=1, sort_keys=False).encode())


def rewrite_index(html: str) -> str:
    """The exported copy of `index.html`: relative asset paths, shim first.

    Asset paths are rewritten here rather than left to the shim because the
    stylesheet links are parsed before any script runs — a `<link href="/app.css">`
    on a site served at `/targetring/demo/` is a 404 and an unstyled page, and no
    amount of JS afterwards un-does the flash.
    """
    for name in PAGE_FILES:
        html = html.replace(f'"/{name}"', f'"{name}"')
    if '"app.js"' not in html:
        raise SystemExit("index.html no longer loads /app.js by that name")
    html = html.replace('<script src="app.js"></script>',
                        '<script src="static-demo.js"></script>\n'
                        '<script src="app.js"></script>')
    if "static-demo.js" not in html:
        raise SystemExit("could not inject the shim before app.js")

    # The site nav. `static/index.html` carries the markup and `app.css` hides
    # it unless <body> says the sibling page exists; this is the one place that
    # knows it does, because this is the script that writes into `docs/`
    # beside `docs/flow/`. A one-attribute switch rather than an injected
    # block: the masthead stays assembled in one file, so the two pages cannot
    # drift apart, and the live app keeps serving a nav-less header.
    if '<body class="landing">' not in html:
        raise SystemExit("index.html no longer opens <body class=\"landing\"> — "
                         "the site-nav switch has nowhere to go")
    html = html.replace('<body class="landing">',
                        '<body class="landing" data-site="pages">')
    if 'nav class="sitenav"' not in html:
        raise SystemExit("index.html no longer carries nav.sitenav; the "
                         "exported page would have no link to docs/flow/")

    # v4.2, Puck 2026-10-19: "no published version please". The run-card index
    # and its "Read-only demo" paragraph are already hidden in tool mode — the
    # exported page opens IN one run and never lists cards — so the markup is
    # removed outright rather than left in the document as prose about what kind
    # of copy this is. Nothing on the exported page references `#demo-panel`:
    # `app.js` fills it only when the server reports a demo WITHOUT a featured
    # run, which this export never is.
    html = _DEMO_PANEL.sub("", html)
    for phrase in BANNED_ON_THE_PAGE:
        if phrase.lower() in html.lower():
            raise SystemExit(
                f"the exported page still says {phrase!r} — the page is the "
                "tool and may not describe itself as a demo or a copy")
    return html


# One `<section>`, matched by its id and closed by the first `</section>`,
# because that is how it is written in `static/index.html` (no nested section).
_DEMO_PANEL = re.compile(r'\s*<section id="demo-panel".*?</section>', re.S)

# Sentences the exported page may not carry, from Puck's own list: "remove ...
# every sentence that calls it a demo, read-only, static copy, published
# version, or explains that there is no server". Checked against the HTML the
# browser parses, not against the JS comments that explain why it is gone.
BANNED_ON_THE_PAGE = ("read-only demo", "static copy", "static export",
                      "published version", "there is no server",
                      "nothing is computed here")


def check_shim_refusal(app_module) -> None:
    """The shim repeats `app.DEMO_REFUSAL` as a literal; assert it still matches."""
    shim = (REPO / "scripts" / "static_demo_shim.js").read_text()
    sentence = app_module.DEMO_REFUSAL
    halves = [sentence[:60], sentence[60:]]
    joined = re.sub(r'"\s*\+\s*\n?\s*"', "", shim)
    if sentence not in joined:
        raise SystemExit(
            "scripts/static_demo_shim.js no longer carries app.DEMO_REFUSAL "
            f"verbatim; update it to:\n{sentence!r}\n(halves: {halves})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "docs" / "demo"),
                    help="where to write the site (default docs/demo)")
    ap.add_argument("--keep-slides", action="store_true",
                    help="also export figure-slide.svg/.png (+1.4 MB per run, "
                         "referenced by no page)")
    # v4.0, Puck 2026-10-19: "just have one example". The published site is the
    # tool opened IN one run, so the export writes one run and the box it crawls
    # is gated to that one -- there is then no second run to deep-link to, which
    # is the honest version of "no run-card list".
    ap.add_argument("--all-runs", action="store_true",
                    help="export every run in config.DEMO_RUNS instead of only "
                         "the featured one (the published site is one run)")
    args = ap.parse_args()

    os.environ["TARGETRING_DEMO"] = "1"
    from targetring import app as app_module  # noqa: PLC0415
    from targetring import config  # noqa: PLC0415
    check_shim_refusal(app_module)

    run_ids = tuple(config.DEMO_RUNS)
    if not args.all_runs:
        featured = config.DEMO_FEATURED_RUN
        if featured not in run_ids:
            raise SystemExit(
                f"the featured run {featured!r} is not in config.DEMO_RUNS "
                f"{run_ids}: the page would open a run its own gate refuses")
        run_ids = (featured,)
        # The CHILD's allowlist too, not just this loop's. The gate is what makes
        # the crawl an export of a ONE-RUN box rather than a one-run slice of a
        # four-run box: with `DEMO_RUNS` still naming four, `/api/runs.json`
        # would list three runs whose files were never written, and the page
        # would offer cards pointing at 404s.
        os.environ["TARGETRING_DEMO_RUNS"] = featured
        os.environ["TARGETRING_DEMO_FEATURED_RUN"] = featured
    missing = [r for r in run_ids if not (config.RUNS_DIR / r).is_dir()]
    if missing:
        raise SystemExit(f"these demo runs are not on disk: {missing}")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    port = free_port()
    proc = start_server(port, run_ids)
    total, files = 0, 0
    try:
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}")
        health = wait_ready(client, proc)
        if not health.get("demo"):
            raise SystemExit("the server did not come up in demo mode")
        print(f"demo server on 127.0.0.1:{port}, runs: {len(run_ids)}")

        # The gate, re-checked rather than trusted. A static export of a surface
        # that had started answering /api/analyse would be an export of the wrong
        # surface.
        for path in MUST_REFUSE:
            r = client.get(path, timeout=10.0)
            if r.status_code != 403:
                raise SystemExit(f"{path} answered {r.status_code}, not 403 — "
                                 "the demo gate is not holding; refusing to export")
        stranger = client.get("/api/run/20260926T001147Z-054913/manifest.json")
        if stranger.status_code != 404:
            raise SystemExit("a run outside the allowlist was served; refusing to export")
        print(f"gate holds: {len(MUST_REFUSE)} forbidden routes 403, "
              "an unlisted run 404s")

        # 1. the page itself
        html = rewrite_index(get(client, "/").text)
        total += write(out / "index.html", html.encode()); files += 1
        total += write(out / "static-demo.js",
                       (REPO / "scripts" / "static_demo_shim.js").read_bytes()); files += 1
        for name in PAGE_FILES:
            total += write(out / name, get(client, f"/{name}").content); files += 1

        # 2. the flat payloads
        for path in FLAT_JSON:
            total += save_json(client, out, path, path.lstrip("/") + ".json"); files += 1

        listed = {r["run_id"] for r in get(client, "/api/runs").json()["runs"]}
        if listed != set(run_ids):
            raise SystemExit(f"/api/runs listed {sorted(listed)}, "
                             f"expected {sorted(run_ids)}")

        # 3. per run: payloads, artefacts, one baked drawer per gene
        for run_id in run_ids:
            for name in RUN_JSON:
                total += save_json(client, out, f"/api/run/{run_id}/{name}",
                                   f"api/run/{run_id}/{name}.json",
                                   args.keep_slides); files += 1
            wanted = RUN_ARTEFACTS + (SLIDE_ARTEFACTS if args.keep_slides else ())
            for name in wanted:
                r = client.get(f"/api/run/{run_id}/{name}", timeout=120.0)
                if r.status_code == 404:
                    print(f"  {run_id}: no {name} (the run never wrote one)")
                    continue
                if r.status_code != 200:
                    raise SystemExit(f"{run_id}/{name} -> {r.status_code}")
                body = r.content
                if name == "manifest.json":
                    body = json.dumps(
                        date_the_spend(scrub(json.loads(body))), indent=1).encode()
                total += write(out / "api" / "run" / run_id / name, body); files += 1

            # The clickable genes are the ones the payload itself named — the
            # same list `renderTable` uses to decide which labels are links, so
            # the export cannot offer a drawer the page will not open, or miss
            # one it will.
            genes = get(client, f"/api/run/{run_id}/values").json().get("baked_drawers") or []
            for gene in genes:
                total += save_json(client, out, f"/api/run/{run_id}/drawer/{gene}",
                                   f"api/run/{run_id}/drawer/{gene}.json"); files += 1
            print(f"  {run_id}: {len(genes)} drawers")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # 4. the two files GitHub Pages needs
    docs = out.parent
    (docs / ".nojekyll").write_text("")
    (docs / "index.html").write_text(_REDIRECT)

    print(f"\n{files} files, {total / 1e6:.1f} MB under {out}")
    print(f"serve it:  python -m http.server --directory {docs.parent} 8000")
    print(f"then open: http://127.0.0.1:8000/{docs.parent.name}/docs/demo/")
    return 0


_REDIRECT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>targetring</title>
<meta http-equiv="refresh" content="0; url=./demo/">
<link rel="canonical" href="./demo/">
</head><body>
<p>targetring's read-only demo is at <a href="./demo/">./demo/</a>.</p>
</body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
