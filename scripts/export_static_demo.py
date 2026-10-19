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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

# The page files `index.html` and `header-wash.css` ask for by absolute path.
PAGE_FILES = ("app.js", "app.css", "header-wash.css",
              "header-wash.png", "header-wash-dark.png")

# The flat JSON routes `init()` and the demo panel read.
FLAT_JSON = ("/api/health", "/api/regions", "/api/orderings",
             "/api/example", "/api/runs")

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
RUN_JSON = ("values", "evidence")

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


def save_json(client: httpx.Client, out: Path, url: str, rel: str) -> int:
    body = scrub(get(client, url).json())
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
    return html


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
    args = ap.parse_args()

    os.environ["TARGETRING_DEMO"] = "1"
    from targetring import app as app_module  # noqa: PLC0415
    from targetring import config  # noqa: PLC0415
    check_shim_refusal(app_module)

    run_ids = tuple(config.DEMO_RUNS)
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
                                   f"api/run/{run_id}/{name}.json"); files += 1
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
                    body = json.dumps(scrub(json.loads(body)), indent=1).encode()
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
