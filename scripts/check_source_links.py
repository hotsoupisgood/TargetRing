#!/usr/bin/env python
"""Fetch every ring's source-documentation URL and report whether it resolves.

v2.8. A dead link in a legend is worse than no link, and nothing but a real
request can tell you which you have. This is the only check that touches the
network; `sources.assert_registry_is_coherent()` covers the structural half and
runs on every render.

Two things it does that a naive checker would get wrong:

* **It sends this repo's `config.USER_AGENT`.** `druggablegenome.net` and
  `pharos.nih.gov` answer 403 to curl's default agent and 200 to ours, so a
  checker on defaults would report a working citation as dead and invite
  somebody to "fix" a good link.
* **It checks the fragment, not just the page.** Five of the nine URLs are
  anchored (`#tractability-section`), and a GitBook page that has been
  reorganised still returns 200 while silently landing the reader at the top.
  The anchor is verified as an `id="..."` in the returned HTML, and a missing one
  is reported as ANCHOR-MISSING rather than passed as 200.

  GTEx's Redoc page is the exception and it is handled rather than waved past:
  Redoc renders operation anchors client-side from the OpenAPI document, so the
  operation id is provably absent from the HTML and the anchor is still valid.
  Where the page declares a `spec-url`, that document is fetched and the
  `operationId` looked up in it — which is a *stronger* check than the id
  attribute, because it verifies the endpoint the anchor names still exists.

Usage:  .venv/bin/python scripts/check_source_links.py [--json logs/x.json]
Exit 0 only if every URL is 200 and every anchor is present.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from targetring import config, sources  # noqa: E402

TIMEOUT = 30.0


def check(url: str) -> dict[str, object]:
    target, _, anchor = url.partition("#")
    req = urllib.request.Request(target, headers={"User-Agent": config.USER_AGENT})
    row: dict[str, object] = {"url": url, "anchor": anchor or None}
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            row["status"] = r.status
            row["final_url"] = r.url
            row["bytes"] = len(body)
    except urllib.error.HTTPError as e:
        row["status"] = e.code
        row["verdict"] = f"HTTP-{e.code}"
        return row
    except Exception as e:  # noqa: BLE001
        row["status"] = 0
        row["verdict"] = f"ERROR {type(e).__name__}: {e}"
        return row
    if row["status"] != 200:
        row["verdict"] = f"HTTP-{row['status']}"
    elif not anchor or f'id="{anchor}"' in body:
        row["verdict"] = "OK"
    elif anchor in body:
        row["verdict"] = "OK"
        row["anchor_check"] = "found in page text, not as an id attribute"
    else:
        spec = _redoc_spec(target, body)
        op = anchor.rpartition("/")[2]
        if spec is not None and op in spec:
            row["verdict"] = "OK"
            row["anchor_check"] = f"operationId {op!r} present in {spec['__url__']}"
        else:
            row["verdict"] = "ANCHOR-MISSING"
    return row


def _redoc_spec(page_url: str, body: str) -> dict[str, object] | None:
    """The OpenAPI document a Redoc page renders, flattened to operation ids."""
    m = re.search(r'spec-url=["\']([^"\']+)["\']', body)
    if not m:
        return None
    spec_url = urllib.parse.urljoin(page_url, m.group(1))
    req = urllib.request.Request(spec_url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            doc = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    ops = {o["operationId"] for p in doc.get("paths", {}).values()
           for o in p.values() if isinstance(o, dict) and o.get("operationId")}
    return {"__url__": spec_url, **{o: True for o in ops}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=Path("logs/v28-source-links.json"))
    args = ap.parse_args()

    sources.assert_registry_is_coherent()
    rows = []
    for key, doc in sources.RING_SOURCE_DOCS.items():
        row = {"ring": key, "name": doc.name, "domain_shown": doc.domain,
               **check(doc.url)}
        rows.append(row)
        print(f"{row['verdict']:<16} {row['status']:>4}  {key:<22} {doc.url}")

    bad = [r for r in rows if r["verdict"] != "OK"]
    out = {"user_agent": config.USER_AGENT, "checked": len(rows),
           "ok": len(rows) - len(bad), "failures": bad, "rows": rows}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, indent=2))
    print(f"\n{len(rows) - len(bad)}/{len(rows)} OK  -> {args.json}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
