# SPDX-License-Identifier: FSL-1.1-ALv2
"""Read-only GitHub proxy with an in-memory cache for the dashboard (API slice 1).

Run with: uvicorn tradvia.api:app
"""

from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from typing import Callable, NamedTuple
from urllib.parse import urlencode

import requests
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

GITHUB_API = "https://api.github.com"
ALLOWED_PARAMS = ("state", "per_page", "page", "labels", "sort", "direction")
MAX_ENTRIES = 500
UPSTREAM_TIMEOUT = 10


class Upstream(NamedTuple):
    status: int
    etag: str | None
    content_type: str
    body: bytes


class _Entry(NamedTuple):
    expires: float
    etag: str | None
    status: int
    body: bytes
    content_type: str


Fetch = Callable[[str, dict[str, str]], Upstream]


def default_fetch(url: str, headers: dict[str, str]) -> Upstream:
    r = requests.get(url, headers=headers, timeout=UPSTREAM_TIMEOUT)
    return Upstream(
        r.status_code,
        r.headers.get("ETag"),
        r.headers.get("Content-Type", "application/json"),
        r.content,
    )


def allowed_patterns(repo: str) -> list[re.Pattern[str]]:
    r = re.escape(repo)
    return [
        re.compile(p)
        for p in (
            rf"repos/{r}/issues",
            rf"repos/{r}/issues/\d+/timeline",
            rf"repos/{r}/issues/\d+/comments",
            rf"repos/{r}/pulls",
            rf"repos/{r}/actions/runs",
            rf"repos/{r}/actions/workflows/(?:agent|claude)\.yml/runs",
        )
    ]


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not found"}, status_code=404)


def create_app(
    fetch: Fetch | None = None,
    now: Callable[[], float] = time.monotonic,
) -> Starlette:
    fetch = fetch or default_fetch
    repo = os.environ.get("TRADVIA_REPO", "Tradvia/tradvia")
    ttl = float(os.environ.get("TRADVIA_API_CACHE_TTL", "60"))
    token = os.environ.get("GITHUB_TOKEN", "")
    patterns = allowed_patterns(repo)
    cache: OrderedDict[str, _Entry] = OrderedDict()

    def path_allowed(path: str) -> bool:
        if not path or "%" in path or "\\" in path:
            return False
        segments = path.split("/")
        if any(s in ("", ".", "..") for s in segments):
            return False
        return any(p.fullmatch(path) for p in patterns)

    def reply(entry: _Entry, stale: bool = False) -> Response:
        headers = {"X-Tradvia-Stale": "1"} if stale else {}
        return Response(
            entry.body,
            status_code=entry.status,
            media_type=entry.content_type,
            headers=headers,
        )

    def store(url: str, entry: _Entry) -> None:
        cache[url] = entry
        cache.move_to_end(url)
        while len(cache) > MAX_ENTRIES:
            cache.popitem(last=False)

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    async def github(request: Request) -> Response:
        path = request.path_params["path"]
        raw = request.scope.get("raw_path") or b""
        if b"%" in raw.split(b"?", 1)[0] or not path_allowed(path):
            return _not_found()

        query = sorted(
            (k, v) for k, v in request.query_params.multi_items() if k in ALLOWED_PARAMS
        )
        url = f"{GITHUB_API}/{path}"
        if query:
            url += "?" + urlencode(query)

        entry = cache.get(url)
        if entry and entry.expires > now():
            cache.move_to_end(url)
            return reply(entry)

        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if entry and entry.etag:
            headers["If-None-Match"] = entry.etag

        try:
            up = await run_in_threadpool(fetch, url, headers)
        except Exception:  # never echo the error: it may carry request headers
            up = None

        if up is None or up.status >= 500:
            if entry:
                return reply(entry, stale=True)
            return JSONResponse({"error": "upstream unavailable"}, status_code=502)
        if up.status == 304 and entry:
            entry = entry._replace(expires=now() + ttl)
            store(url, entry)
            return reply(entry)
        if 200 <= up.status < 300:
            entry = _Entry(now() + ttl, up.etag, up.status, up.body, up.content_type)
            store(url, entry)
            return reply(entry)
        status = 502 if up.status == 304 else up.status
        return JSONResponse({"error": "upstream error"}, status_code=status)

    return Starlette(
        routes=[
            Route("/api/healthz", healthz, methods=["GET"]),
            Route("/api/github/{path:path}", github, methods=["GET"]),
        ]
    )


app = create_app()
