"""Shared HTTP client.

Three house rules, all from the reviews, enforced here so no adapter can skip
them:

* QA N1 -- status code is necessary, never sufficient. Every helper validates
  response *shape*, and `json_or_raise` refuses to parse anything that is not
  declared JSON (HPA and MGI both return 200-with-HTML, and a 400 from HPA is
  117 kB of web page).
* AGENTS.md rule 9 -- concurrency capped at 4 per host, and a 429 is a hard run
  failure. There is no retry loop anywhere in this file, on purpose.
* COUNCIL N2 -- Open Targets reports field errors under `errors` and syntax
  errors under a different top-level key, `syntaxError`. Checking only `errors`
  treats a syntax error as success with no data.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import HTTP_TIMEOUT_SECONDS, MAX_CONCURRENCY_PER_HOST, USER_AGENT


class HardFailure(RuntimeError):
    """Abort the run. Never caught to produce an empty ring."""


def describe(e: BaseException) -> str:
    """The reason a call failed, in a form that is never empty.

    Found while ChEMBL was 500-ing during the v2 screenshot pass: several
    httpx exceptions carry no message, so `str(e)` is `''` and a `failed` cell
    recorded its reason as the empty string. The legend promises "call FAILED —
    not the same as absent"; a failure with no reason keeps the distinction and
    throws away the only thing that makes it actionable.
    """
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


class RateLimited(HardFailure):
    """A 429. AGENTS.md rule 9: never a retry loop, always a run failure."""


_sems: dict[str, asyncio.Semaphore] = {}
_sems_lock: asyncio.Lock | None = None


def _semaphore(url: str) -> asyncio.Semaphore:
    host = urlsplit(url).netloc
    sem = _sems.get(host)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENCY_PER_HOST)
        _sems[host] = sem
    return sem


class Http:
    """Thin wrapper over one httpx.AsyncClient with the per-host gate."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> "Http":
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        async with _semaphore(url):
            assert self._client is not None
            r = await self._client.get(url, params=params)
        _check_status(r)
        return r

    async def post_json(
        self, url: str, payload: dict[str, Any], check_status: bool = True
    ) -> httpx.Response:
        async with _semaphore(url):
            assert self._client is not None
            r = await self._client.post(url, json=payload)
        if check_status:
            _check_status(r)
        return r


def _check_status(r: httpx.Response) -> None:
    if r.status_code == 429:
        raise RateLimited(
            f"429 from {r.request.url.host}. Rate limits for this source are unmeasured "
            f"(QA B4/§5); treating as a hard run failure rather than retrying."
        )
    if r.status_code >= 400:
        raise HardFailure(
            f"HTTP {r.status_code} from {r.request.url} "
            f"(content-type={r.headers.get('content-type')!r}, {len(r.content)} B): "
            f"{r.text[:300]!r}"
        )


def json_or_raise(r: httpx.Response) -> Any:
    """Parse JSON only if the server said it is JSON.

    QA B4: HPA's 400 is a full HTML page; MGI and GenTAR return 200-with-HTML.
    `json.loads` on those either throws at 2 a.m. or, with a lenient parser,
    yields an empty result that renders as legitimate absence.
    """
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype.lower():
        raise HardFailure(
            f"expected JSON from {r.request.url}, got content-type {ctype!r} "
            f"({len(r.content)} B, starts {r.text[:120]!r})"
        )
    try:
        return r.json()
    except json.JSONDecodeError as e:
        raise HardFailure(f"undecodable JSON from {r.request.url}: {e}") from e


def check_no_ncbi_error(payload: Any, where: str) -> None:
    """QA B3 rule 2: an `ERROR` key anywhere in an NCBI response is a hard
    failure whatever the status code. Verified live: `db=notadb` returns HTTP
    200 with `{"esearchresult":{"ERROR":"Invalid db name specified: notadb"}}`.
    """
    found = _find_key(payload, "ERROR")
    if found is not None:
        raise HardFailure(f"NCBI returned an ERROR key in a {where} response: {found!r}")


def _find_key(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for v in node:
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    return None


async def graphql(http: Http, url: str, query: str, variables: dict[str, Any] | None = None) -> Any:
    """POST a GraphQL query and refuse anything that is not a clean result.

    COUNCIL N2: two different error keys. QA B1: a 400 must abort the run, never
    render an empty ring.
    """
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    # Open Targets answers a bad field with HTTP 400 and the useful detail in a
    # JSON body. Raising on the status first would bury "Cannot query field
    # 'knownDrugs'" under a generic 400, so the body is inspected first and the
    # status is enforced afterwards.
    r = await http.post_json(url, payload, check_status=False)
    if r.status_code == 429:
        _check_status(r)
    if "json" not in r.headers.get("content-type", "").lower():
        _check_status(r)
        raise HardFailure(
            f"expected JSON from {url}, got content-type "
            f"{r.headers.get('content-type')!r} ({len(r.content)} B)"
        )
    body = r.json()
    if not isinstance(body, dict):
        raise HardFailure(f"GraphQL response from {url} was {type(body).__name__}, not an object")
    if "syntaxError" in body:
        raise HardFailure(f"GraphQL syntax error from {url}: {body['syntaxError']!r}")
    if body.get("errors"):
        raise HardFailure(
            f"GraphQL errors from {url} (HTTP {r.status_code}): "
            f"{json.dumps(body['errors'])[:600]}"
        )
    _check_status(r)
    if "data" not in body:
        raise HardFailure(f"GraphQL response from {url} had no `data` key: {list(body)}")
    return body["data"]
