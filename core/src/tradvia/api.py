# SPDX-License-Identifier: FSL-1.1-ALv2
"""Read-only GitHub proxy with cache (slice 1) and GitHub webhooks stored in Postgres (slice 2).

Run with: uvicorn tradvia.api:app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections import OrderedDict
from typing import Any, Callable, NamedTuple
from urllib.parse import urlencode

import requests
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .events import HANDLED_EVENTS, PayloadError, payload_ops
from .store import PgStore, Store, StoreUnavailable

GITHUB_API = "https://api.github.com"
MAX_WEBHOOK_BYTES = 1024 * 1024
ALLOWED_PARAMS = ("state", "per_page", "page", "labels", "sort", "direction")
MAX_ENTRIES = 500
UPSTREAM_TIMEOUT = 10
DEFAULT_PER_PAGE = 30
MAX_PER_PAGE = 100


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


def _store_unavailable() -> JSONResponse:
    return JSONResponse({"error": "storage unavailable"}, status_code=503)


def per_page(value: str | None) -> int:
    try:
        n = int(value) if value else DEFAULT_PER_PAGE
    except ValueError:
        n = DEFAULT_PER_PAGE
    return max(1, min(n, MAX_PER_PAGE))


def github_issue(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": r["number"],
        "title": r["title"],
        "state": r["state"],
        "state_reason": r.get("state_reason"),
        "labels": [{"name": n} for n in r["labels"]],
        "user": {"login": r.get("author")},
        "html_url": r.get("html_url"),
        "created_at": r.get("created_at"),
        "updated_at": r["updated_at"],
        "closed_at": r.get("closed_at"),
    }


def github_pull(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": r["number"],
        "title": r["title"],
        "state": r["state"],
        "merged_at": r.get("merged_at"),
        "head": {"ref": r.get("branch")},
        "user": {"login": r.get("author")},
        "html_url": r.get("html_url"),
        "created_at": r.get("created_at"),
        "updated_at": r["updated_at"],
    }


def github_run(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r.get("name"),
        "display_title": r.get("display_title"),
        "event": r.get("event"),
        "status": r.get("status"),
        "conclusion": r.get("conclusion"),
        "html_url": r.get("html_url"),
        "run_attempt": r.get("run_attempt"),
        "run_started_at": r.get("started_at"),
        "created_at": r.get("started_at"),
        "updated_at": r["updated_at"],
    }


def create_app(
    fetch: Fetch | None = None,
    now: Callable[[], float] = time.monotonic,
    store: Store | None = None,
) -> Starlette:
    fetch = fetch or default_fetch
    webhook_secret = os.environ.get("TRADVIA_WEBHOOK_SECRET", "")
    db: Store | None = store
    if db is None and os.environ.get("DATABASE_URL"):
        db = PgStore(os.environ["DATABASE_URL"])  # lazy: does not connect here
    repo = os.environ.get("TRADVIA_REPO", "Tradvia/tradvia")
    ttl = float(os.environ.get("TRADVIA_API_CACHE_TTL", "60"))
    token = os.environ.get("GITHUB_TOKEN", "")
    patterns = allowed_patterns(repo)
    store_route = re.compile(
        rf"repos/{re.escape(repo)}/(?:(?P<kind>issues|pulls)"
        r"|actions/workflows/(?P<wf>agent|claude)\.yml/runs)"
    )
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

    def remember(url: str, entry: _Entry) -> None:
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
            remember(url, entry)
            return reply(entry)
        if 200 <= up.status < 300:
            entry = _Entry(now() + ttl, up.etag, up.status, up.body, up.content_type)
            remember(url, entry)
            return reply(entry)
        status = 502 if up.status == 304 else up.status
        return JSONResponse({"error": "upstream error"}, status_code=status)

    def valid_signature(body: bytes, header: str) -> bool:
        digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        expected = ("sha256=" + digest).encode()
        return hmac.compare_digest(expected, header.encode("utf-8", "replace"))

    async def read_body(request: Request) -> bytes | None:
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_WEBHOOK_BYTES:
            return None
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_WEBHOOK_BYTES:
                return None
        return bytes(body)

    async def webhook(request: Request) -> Response:
        if not webhook_secret:  # fail closed
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await read_body(request)
        if body is None:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature or not valid_signature(body, signature):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid json"}, status_code=400)

        accepted = JSONResponse({"ok": True}, status_code=202)
        event = request.headers.get("x-github-event", "")
        if event not in HANDLED_EVENTS:  # includes ping
            return accepted
        repository = payload.get("repository")
        if not isinstance(repository, dict) or repository.get("full_name") != repo:
            return accepted
        delivery = request.headers.get("x-github-delivery", "")
        if not delivery:
            return JSONResponse({"error": "missing delivery id"}, status_code=400)
        try:
            ops = payload_ops(event, payload)
        except PayloadError:
            return JSONResponse({"error": "invalid payload"}, status_code=400)
        if db is None:
            return JSONResponse({"error": "storage not configured"}, status_code=503)
        try:
            await run_in_threadpool(db.apply, delivery, event, ops)
        except StoreUnavailable:
            return JSONResponse({"error": "storage unavailable"}, status_code=503)
        except Exception:  # never log or echo: payloads and DSNs may be sensitive
            return JSONResponse({"error": "internal error"}, status_code=500)
        return accepted  # also for duplicate deliveries

    async def store_health(request: Request) -> Response:
        if db is None:
            return JSONResponse({"ok": False}, status_code=503)
        try:
            stats = await run_in_threadpool(db.stats)
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True, **stats})

    async def store_read(request: Request) -> Response:
        path = "repos/" + request.path_params["path"]
        raw = request.scope.get("raw_path") or b""
        m = store_route.fullmatch(path)
        if b"%" in raw.split(b"?", 1)[0] or not path_allowed(path) or not m:
            return _not_found()
        resource = m.group("kind") or "runs"
        workflow = m.group("wf")
        params = request.query_params
        limit = per_page(params.get("per_page"))
        state = params.get("state", "open")
        if state not in ("open", "closed", "all"):
            return JSONResponse({"error": "invalid state"}, status_code=400)
        if db is None:
            return _store_unavailable()
        try:
            if resource == "issues":
                labels = [s for s in params.get("labels", "").split(",") if s]
                rows = await run_in_threadpool(db.list_issues, labels, state, limit)
            elif resource == "pulls":
                rows = await run_in_threadpool(db.list_pulls, state, limit)
            else:
                rows = await run_in_threadpool(db.list_runs, workflow + ".yml", limit)
        except Exception:  # StoreUnavailable or anything else: the panel falls back
            return _store_unavailable()
        if rows is None:
            return _store_unavailable()
        if resource == "issues":
            return JSONResponse([github_issue(r) for r in rows])
        if resource == "pulls":
            return JSONResponse([github_pull(r) for r in rows])
        return JSONResponse(
            {"total_count": len(rows), "workflow_runs": [github_run(r) for r in rows]}
        )

    return Starlette(
        routes=[
            Route("/api/webhooks/github", webhook, methods=["POST"]),
            Route("/api/store/health", store_health, methods=["GET"]),
            Route("/api/store/repos/{path:path}", store_read, methods=["GET"]),
            Route("/api/healthz", healthz, methods=["GET"]),
            Route("/api/github/{path:path}", github, methods=["GET"]),
        ]
    )


app = create_app()
