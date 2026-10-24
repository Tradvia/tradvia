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
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, NamedTuple
from urllib.parse import urlencode, urlsplit

import requests
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .actions import (
    ActionError,
    CardNotFound,
    GitHubError,
    DEPLOY_REF,
    DEPLOY_WORKFLOW,
    agent_login,
    apply_order,
    awaiting_plan,
    backlog_string,
    deploy_view,
    merge_block,
    merge_title,
    new_backlog,
    parse_backlog_input,
    parse_flag,
    parse_new_card,
    parse_pr_description,
    parse_reply,
    plan_comment,
    reorder,
    replaced_run_ids,
    required_checks,
)
from .auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    STATE_COOKIE,
    STATE_TTL,
    Auth,
    AuthError,
    GitHubIdentity,
    PolicyUnreadable,
    Session,
    load_config,
)
from . import pending, push
from .backlog import READY_LABEL, backlog_status
from .events import HANDLED_EVENTS, PayloadError, issue_record, payload_ops
from .policy import Interactions, PolicyError
from .store import PgStore, Store, StoreUnavailable, project_slug

GITHUB_API = "https://api.github.com"
WEBHOOK_PATH = "/api/webhooks/github"
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
IDEMPOTENCY_HEADER = "x-tradvia-idempotency-key"
IDEMPOTENCY_WINDOW = 5.0  # seconds
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_WEBHOOK_BYTES = 1024 * 1024
ALLOWED_PARAMS = ("state", "per_page", "page", "labels", "sort", "direction")
MAX_ENTRIES = 500
UPSTREAM_TIMEOUT = 10
DEFAULT_PER_PAGE = 30
MAX_DIFF = 200_000
MAX_DEPLOY_RUNS = 20
MAX_COMMENT = 500
# What the GitHub App needs for each panel action (docs/github-app.md).
APP_PERMISSION = {
    "merge_pr": "Contents: Read and write",
    "update_branch": "Pull requests: Read and write",
    "approve_deploy": "Deployments: Read and write",
    "reject_deploy": "Deployments: Read and write",
    "cancel_run": "Actions: Read and write",
    "post_deploy": "Actions: Read and write",
    "view_pull": "Pull requests: Read-only and Checks: Read-only",
    "view_deploys": "Actions: Read-only and Deployments: Read-only",
}
NOT_FOUND_MESSAGE = {
    "merge_pr": "Pull request not found in this project.",
    "update_branch": "Pull request not found in this project.",
    "view_pull": "Pull request not found in this project.",
    "approve_deploy": "Deploy not found in this project.",
    "reject_deploy": "Deploy not found in this project.",
    "cancel_run": "Deploy not found in this project.",
    "view_deploys": "Deploys not found in this project.",
    "post_deploy": "The deploy-oci workflow was not found in this project.",
}
MAX_PER_PAGE = 100
MAX_ENDPOINT = 2048
MAX_KEY = 256
_KEY_B64 = re.compile(r"[A-Za-z0-9_=-]+")
_IP_HOST = re.compile(r"[\d.]+|\[.*\]")


def parse_subscription(data: dict[str, Any]) -> tuple[str, str, str]:
    """`(endpoint, p256dh, auth)` of a browser `PushSubscription`, or ActionError.

    The server later POSTs to the endpoint, so only a public https host name is accepted
    (no IP literal, no localhost or single-label name): the endpoint is a request target.
    """
    endpoint, keys = data.get("endpoint"), data.get("keys")
    if not isinstance(endpoint, str) or not isinstance(keys, dict):
        raise ActionError("endpoint and keys are required")
    url = urlsplit(endpoint)
    host = (url.hostname or "").lower()
    if (
        len(endpoint) > MAX_ENDPOINT or url.scheme != "https" or url.username or url.password
        or "." not in host or host.endswith(".localhost") or _IP_HOST.fullmatch(host)
    ):
        raise ActionError("endpoint must be a public https URL")
    p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
    for value in (p256dh, auth_key):
        if not isinstance(value, str) or not value or len(value) > MAX_KEY or not _KEY_B64.fullmatch(value):
            raise ActionError("keys.p256dh and keys.auth must be base64url text")
    return endpoint, p256dh, auth_key


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


def registered_projects() -> list[str]:
    """Repositories accepted by the API: `TRADVIA_PROJECTS`, default `TRADVIA_REPO`."""
    default = os.environ.get("TRADVIA_REPO", "Tradvia/tradvia")
    raw = os.environ.get("TRADVIA_PROJECTS") or default
    repos: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in repos:
            repos.append(item)
    return repos or [default]


def allowed_patterns(repos: list[str] | str) -> list[re.Pattern[str]]:
    if isinstance(repos, str):
        repos = [repos]
    return [p for repo in repos for p in _repo_patterns(repo)]


def _repo_patterns(repo: str) -> list[re.Pattern[str]]:
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


def github_issue(r: dict[str, Any], plans: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # "tradvia_plan_cost" is not a GitHub field: cost and tokens of each plan comment of the
    # agent on this card, only present when the store has any.
    extra = {"tradvia_plan_cost": [plan_cost(p) for p in plans]} if plans else {}
    return {
        **extra,
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
        # Not a GitHub field: the cost parsed from the PR body, which the store does not keep.
        "tradvia_cost": _cost(r.get("cost_usd"), r.get("cost_seconds"), r),
    }


def _tokens(r: dict[str, Any]) -> dict[str, int]:
    return {k: int(r.get(k) or 0) for k in ("tokens_in", "tokens_out", "tokens_cache")}


def _cost(usd: Any, seconds: Any, tokens: dict[str, Any]) -> dict[str, Any] | None:
    if usd is None:
        return None
    so_far = tokens.get("cost_so_far_usd")
    return {"usd": float(usd), "seconds": float(seconds) if seconds is not None else None,
            "so_far_usd": float(so_far) if so_far is not None else None, **_tokens(tokens)}


def plan_cost(r: dict[str, Any]) -> dict[str, Any]:
    """One plan of the agent on a card (not a GitHub field: the store keeps no comment body)."""
    return {"created_at": r.get("created_at"), "usd": float(r["cost_usd"]), **_tokens(r)}


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
    identity: GitHubIdentity | None = None,
    clock: Callable[[], float] = time.time,
    push_sender: push.Sender | None = None,
) -> Starlette:
    fetch = fetch or default_fetch
    # Web Push is off unless both VAPID keys are set (or a sender is injected, in tests).
    sender: push.Sender | None = push_sender or push.default_sender()
    webhook_secret = os.environ.get("TRADVIA_WEBHOOK_SECRET", "")
    db: Store | None = store
    if db is None and os.environ.get("DATABASE_URL"):
        db = PgStore(os.environ["DATABASE_URL"])  # lazy: does not connect here
    # Sign-in is off unless client id, client secret and secret key are all set.
    auth_config = load_config()
    auth = Auth(auth_config, db, identity, clock) if auth_config and db is not None else None
    repos = registered_projects()
    ttl = float(os.environ.get("TRADVIA_API_CACHE_TTL", "60"))
    token = os.environ.get("GITHUB_TOKEN", "")
    patterns = allowed_patterns(repos)
    store_route = re.compile(
        r"repos/(?P<repo>" + "|".join(re.escape(r) for r in repos) + r")/"
        r"(?:(?P<kind>issues|pulls|backlog)|actions/workflows/(?P<wf>agent|claude)\.yml/runs)"
    )
    cache: OrderedDict[str, _Entry] = OrderedDict()
    project_ids: dict[str, int] = {}

    def project_id_of(repo: str) -> int:
        """Id of a registered project; created on first use (never at construction)."""
        if repo not in project_ids:
            assert db is not None
            project_ids[repo] = db.ensure_project(repo)
        return project_ids[repo]

    def safe_path(path: str) -> bool:
        if not path or "%" in path or "\\" in path:
            return False
        return not any(s in ("", ".", "..") for s in path.split("/"))

    def path_allowed(path: str) -> bool:
        return safe_path(path) and any(p.fullmatch(path) for p in patterns)

    def backlog_view(pid: int) -> list[dict[str, Any]]:
        assert db is not None
        items = db.list_backlog(pid)
        by_number = {i["number"]: i for i in db.list_issues(pid, [], "all", 100_000) or []}
        pulls = db.list_pulls(pid, "open", 100_000) or []
        out = []
        for it in items:
            issue = by_number.get(it["issue_number"])
            out.append(
                {
                    "issue_number": it["issue_number"],
                    "position": it["position"],
                    "title": issue["title"] if issue else None,
                    "state": issue["state"] if issue else None,
                    "labels": issue["labels"] if issue else [],
                    "depends_on": it["depends_on"],
                    "note": it["note"],
                    "status": backlog_status(it, issue, pulls, by_number),
                }
            )
        return out

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
        if not isinstance(repository, dict):
            return accepted
        repo = repository.get("full_name")
        if not isinstance(repo, str) or repo not in repos:
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
            project_id = await run_in_threadpool(project_id_of, repo)
            before = await run_in_threadpool(pending_before, project_id, ops)
            applied = await run_in_threadpool(db.apply, project_id, delivery, event, ops)
        except StoreUnavailable:
            return JSONResponse({"error": "storage unavailable"}, status_code=503)
        except Exception:  # never log or echo: payloads and DSNs may be sensitive
            return JSONResponse({"error": "internal error"}, status_code=500)
        if applied and before is not None:
            try:  # a push failure must never change the answer to GitHub
                await run_in_threadpool(push_entered, repo, project_id, ops, before)
            except Exception:
                pass
        return accepted  # also for duplicate deliveries

    def pending_before(project_id: int, ops: list[Any]) -> dict[int, str] | None:
        """Pending kinds of the cards a delivery touches, before it is applied (None: no push)."""
        assert db is not None
        if sender is None:
            return None
        try:
            numbers = pending.affected_cards(ops)
            if not numbers or not db.list_subscriptions(project_id):
                return None
            return pending.kinds_of(db, project_id, numbers)
        except Exception:
            return None

    def push_entered(repo: str, project_id: int, ops: list[Any], before: dict[int, str]) -> None:
        """Notify for the cards that entered a pending kind in this delivery."""
        assert db is not None and sender is not None
        after = pending.kinds_of(db, project_id, pending.affected_cards(ops))
        entered = {n: k for n, k in after.items() if before.get(n) != k}
        push.notify(db, sender, project_id, project_slug(repo), entered, subscriber_can(repo))

    def subscriber_can(repo: str) -> Callable[[str, str], bool]:
        """Whether a subscriber's cached role may answer a kind; fails closed without a session."""
        def check(login: str, kind: str) -> bool:
            if auth is None or db is None:
                return False
            rec = db.session_for_login(login, clock())
            if rec is None:
                return False
            session = Session("", rec, 0)
            role = auth.role_of(session, repo)
            return pending.can_answer(auth.policy_for(session, repo, fresh=False), role, kind)
        return check

    async def store_health(request: Request) -> Response:
        if db is None:
            return JSONResponse({"ok": False}, status_code=503)
        try:
            default_id = await run_in_threadpool(project_id_of, repos[0])
            stats = await run_in_threadpool(db.stats, default_id)
            per_project = await run_in_threadpool(db.project_stats)
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        # Top-level issues/runs/last_delivery are obsolete (default project): use `projects`.
        return JSONResponse(
            {
                "ok": True,
                "issues": stats["issues"],
                "runs": stats["runs"],
                "last_delivery": stats["last_delivery"],
                "projects": {r: per_project[r] for r in repos if r in per_project},
            }
        )

    async def projects(request: Request) -> Response:
        # Every registered project is public for now; per-role visibility comes with
        # login (ADR 0004).
        return JSONResponse(
            [
                {"slug": project_slug(r), "kind": "github_repo", "external_id": r, "public": True}
                for r in repos
            ]
        )

    async def store_read(request: Request) -> Response:
        path = "repos/" + request.path_params["path"]
        raw = request.scope.get("raw_path") or b""
        m = store_route.fullmatch(path)
        if b"%" in raw.split(b"?", 1)[0] or not m:
            return _not_found()
        if m.group("kind") == "backlog":
            if not safe_path(path):
                return _not_found()
            if db is None:
                return _store_unavailable()
            try:
                pid = await run_in_threadpool(project_id_of, m.group("repo"))
                return JSONResponse(await run_in_threadpool(backlog_view, pid))
            except Exception:
                return _store_unavailable()
        if not path_allowed(path):
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
            pid = await run_in_threadpool(project_id_of, m.group("repo"))
            if resource == "issues":
                labels = [s for s in params.get("labels", "").split(",") if s]
                rows = await run_in_threadpool(db.list_issues, pid, labels, state, limit)
            elif resource == "pulls":
                rows = await run_in_threadpool(db.list_pulls, pid, state, limit)
            else:
                rows = await run_in_threadpool(db.list_runs, pid, workflow + ".yml", limit)
        except Exception:  # StoreUnavailable or anything else: the panel falls back
            return _store_unavailable()
        if rows is None:
            return _store_unavailable()
        if resource == "issues":
            try:
                plans = await run_in_threadpool(db.list_plan_costs, pid)
            except Exception:
                return _store_unavailable()
            by_issue: dict[int, list[dict[str, Any]]] = {}
            for p in plans:
                by_issue.setdefault(p["issue_number"], []).append(p)
            return JSONResponse([github_issue(r, by_issue.get(r["number"])) for r in rows])
        if resource == "pulls":
            return JSONResponse([github_pull(r) for r in rows])
        return JSONResponse(
            {"total_count": len(rows), "workflow_runs": [github_run(r) for r in rows]}
        )

    # --- sign in with GitHub (auth.py, ADR 0008) -----------------------------------

    def private(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
        # __Host- prefix: Secure, Path=/ and no Domain are mandatory.
        response.set_cookie(
            name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
        )

    def clear_cookie(response: Response, name: str) -> None:
        set_cookie(response, name, "", 0)

    async def csrf_guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        """State-changing requests with a session cookie need a matching Origin and CSRF header.

        A cookie that maps to no live session counts as anonymous. Webhooks carry their own
        signature and are not affected.
        """
        cookie = request.cookies.get(SESSION_COOKIE)
        if (
            auth is None
            or request.method not in UNSAFE_METHODS
            or request.url.path == WEBHOOK_PATH
            or cookie is None
        ):
            return await call_next(request)
        if not auth.origin_ok(request.headers.get("origin"), request.headers.get("referer")):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            session = await run_in_threadpool(auth.session_from, cookie, False)
        except Exception:
            return _store_unavailable()
        if session is not None and not auth.csrf_ok(session, request.headers.get(CSRF_HEADER)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    async def auth_login(request: Request) -> Response:
        if auth is None:
            return _not_found()
        try:
            start = await run_in_threadpool(auth.start_login, request.query_params.get("next"))
        except Exception:
            return _store_unavailable()
        response = RedirectResponse(start.url, status_code=302)
        set_cookie(response, STATE_COOKIE, start.state, STATE_TTL)
        return private(response)

    async def auth_callback(request: Request) -> Response:
        if auth is None:
            return _not_found()
        params = request.query_params
        try:
            session, target = await run_in_threadpool(
                auth.finish_login,
                params.get("code"),
                params.get("state"),
                request.cookies.get(STATE_COOKIE),
            )
        except AuthError as e:
            response = JSONResponse({"error": str(e)}, status_code=e.status)
            clear_cookie(response, STATE_COOKIE)
            return private(response)
        except Exception:  # never echo: it may carry tokens or the DSN
            return private(_store_unavailable())
        response = RedirectResponse(target, status_code=302)
        set_cookie(response, SESSION_COOKIE, session.token, session.max_age)
        clear_cookie(response, STATE_COOKIE)
        return private(response)

    async def auth_logout(request: Request) -> Response:
        if auth is None:
            return _not_found()
        try:
            await run_in_threadpool(auth.logout, request.cookies.get(SESSION_COOKIE))
        except Exception:
            return private(_store_unavailable())
        response = JSONResponse({"ok": True})
        clear_cookie(response, SESSION_COOKIE)
        return private(response)

    async def auth_me(request: Request) -> Response:
        if auth is None:
            return private(JSONResponse({"enabled": False}))
        try:
            session = await run_in_threadpool(auth.session_from, request.cookies.get(SESSION_COOKIE))
        except Exception:
            return private(_store_unavailable())
        if session is None:
            response = JSONResponse({"enabled": True, "authenticated": False})
            if SESSION_COOKIE in request.cookies:  # stale or forged: drop it
                clear_cookie(response, SESSION_COOKIE)
            return private(response)
        roles = await run_in_threadpool(lambda: [auth.role_of(session, r) for r in repos])
        can = await run_in_threadpool(lambda: [panel_rights(session, r, x) for r, x in zip(repos, roles)])
        counts = await run_in_threadpool(
            lambda: [len(pending_items(session, r, x)) for r, x in zip(repos, roles)]
        )
        response = JSONResponse(
            {
                "enabled": True,
                "authenticated": True,
                "login": session.login,
                "avatar_url": session.rec["avatar_url"],
                "csrf": auth.csrf_token(session),
                "projects": [
                    {"slug": project_slug(r), "external_id": r, "role": role,
                     "pending_count": n, **rights}
                    for r, role, rights, n in zip(repos, roles, can, counts)
                ],
            }
        )
        set_cookie(response, SESSION_COOKIE, session.token, session.max_age)  # sliding renewal
        return private(response)

    # --- pending inbox and Web Push (pending.py, push.py, ADR 0009) -------------------------

    def pending_items(session: Any, repo: str, role: str | None) -> list[dict[str, Any]]:
        """Cards waiting for this person's role; empty without a role or a readable policy."""
        if auth is None or db is None or role is None:
            return []
        try:
            policy = auth.policy_for(session, repo, fresh=False)
            cards = pending.cards_of(db, project_id_of(repo))
            return pending.pending_for(role, policy, cards)
        except Exception:  # unreadable policy or storage: fail closed, the panel still works
            return []

    def pending_view(session: Any, repo: str, data: dict[str, Any]) -> Outcome:
        items = pending_items(session, repo, auth.role_of(session, repo))
        base = f"/runs/?repo={repo}&card="
        return Outcome(200, {
            "count": len(items),
            "items": [{**it, "link": f"{base}{it['number']}"} for it in items],
        })

    def push_enabled(session: Any, repo: str, data: dict[str, Any]) -> Outcome | None:
        if sender is None:
            return Outcome(404, {"error": "Notifications are not enabled on this server."})
        if auth.role_of(session, repo) is None:
            return Outcome(403, {"error": f"You have no access to {repo}."})
        return None

    def subscribe(session: Any, repo: str, data: dict[str, Any]) -> Outcome:
        refused = push_enabled(session, repo, data)
        if refused:
            return refused
        try:
            endpoint, p256dh, auth_key = parse_subscription(data)
        except ActionError as e:
            return Outcome(400, {"error": str(e)})
        db.save_subscription(project_id_of(repo), session.login, endpoint, p256dh, auth_key)
        return Outcome(200, {"ok": True})

    def unsubscribe(session: Any, repo: str, data: dict[str, Any]) -> Outcome:
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str) or len(endpoint) > MAX_ENDPOINT:
            return Outcome(400, {"error": "endpoint is required"})
        db.delete_subscription(endpoint, session.login, project_id_of(repo))  # only their own
        return Outcome(200, {"ok": True})

    def prefs_view(session: Any, repo: str, data: dict[str, Any]) -> Outcome:
        saved = db.get_prefs(project_id_of(repo), session.login)
        return Outcome(200, {"kinds": {k: push.kind_enabled(saved, k) for k in pending.KINDS}})

    def prefs_save(session: Any, repo: str, data: dict[str, Any]) -> Outcome:
        kinds = push.valid_prefs(data.get("kinds"))
        if kinds is None:
            return Outcome(400, {"error": "kinds must map known event kinds to true or false"})
        pid = project_id_of(repo)
        merged = {**(db.get_prefs(pid, session.login) or {}), **kinds}
        db.set_prefs(pid, session.login, merged)
        return Outcome(200, {"kinds": {k: push.kind_enabled(merged, k) for k in pending.KINDS}})

    def user_route(
        work: Callable[[Any, str, dict[str, Any]], Outcome], body: bool = False
    ) -> Callable[[Request], Any]:
        """A route for the signed-in person on one project; the cookie decides who."""
        async def handler(request: Request) -> Response:
            repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
            if auth is None or db is None or repo not in repos:
                return _not_found()
            try:
                session = await run_in_threadpool(auth.session_from, request.cookies.get(SESSION_COOKIE))
                if session is None:
                    return private(JSONResponse({"error": "Sign in with GitHub first."}, status_code=401))
                data: Any = {}
                if body:
                    try:
                        data = await request.json()
                    except ValueError:
                        data = None
                    if not isinstance(data, dict):
                        return private(JSONResponse({"error": "invalid json"}, status_code=400))
                out = await run_in_threadpool(work, session, repo, data)
            except Exception:
                return private(_store_unavailable())
            return private(JSONResponse(out.body, status_code=out.status))
        return handler

    async def push_key(request: Request) -> Response:
        key = push.public_key() if sender is not None else None
        return private(JSONResponse({"key": key}))

    # --- panel actions with the user's own identity (actions.py, ADR 0004 phase 2) --------

    def panel_rights(session: Any, repo: str, role: str | None) -> dict[str, bool]:
        """What the panel may show as buttons; the routes check again on every request."""
        rights = {"can_start": False, "can_approve": False}
        if role is None:
            return rights
        try:
            policy = auth.policy_for(session, repo, fresh=False)
        except Exception:  # unreadable or invalid policy: fail closed
            return rights
        return {
            "can_start": policy.allows("start_card", role),
            "can_approve": policy.allows("approve_plan", role),
        }

    class Done(NamedTuple):
        number: int | None
        detail: str
        extra: dict[str, Any]

    class Conflict(Exception):
        """The card is in the wrong state for the action."""

    class Refused(Exception):
        """The action is not allowed for this person (403); the message is safe to show."""

    class Outcome(NamedTuple):
        status: int
        body: dict[str, Any]

    def github_failure(action: str, e: GitHubError) -> Outcome:
        """The clear answer for a GitHub error: which permission, whose, or what state."""
        if e.status == 404:
            return Outcome(404, {"error": NOT_FOUND_MESSAGE.get(action, "Card not found in this project.")})
        if e.status in (401, 403):
            if e.app_permission_missing and action in APP_PERMISSION:
                return Outcome(403, {"error": (
                    f"The GitHub App needs {APP_PERMISSION[action]}. "
                    "An owner must add it and accept the new permissions on the installation "
                    "(docs/github-app.md).")})
            if action in ("approve_deploy", "reject_deploy"):
                return Outcome(403, {"error": "You are not a reviewer of the production environment."})
            return Outcome(403, {"error": "GitHub refused the action for your account."})
        if e.status in (405, 409, 422) and action == "merge_pr":
            return Outcome(409, {"error": "GitHub refused the merge: the pull request is not "
                                          "mergeable (conflicts, branch rules or it changed)."})
        if e.status in (409, 422) and action == "update_branch":
            return Outcome(409, {"error": "The branch could not be updated (already up to date, "
                                          "or it has conflicts with main)."})
        return Outcome(502, {"error": "GitHub is unavailable or refused the request."})

    inflight: dict[tuple[str, str], float] = {}
    inflight_lock = threading.Lock()

    def record_audit(
        pid: int, login: str, action: str, number: int | None, result: str, detail: str,
        key: str | None,
    ) -> None:
        assert db is not None
        try:
            db.append_audit(pid, {
                "actor_login": login, "action": action, "issue_number": number,
                "result": result, "detail": detail[:200], "idempotency_key": key,
                "created_at": clock(),
            })
        except Exception:  # the action already ran (or was refused): never turn that into a 500
            pass

    def execute(
        session: Any, repo: str, action: str, interaction: str, number: int | None,
        key: str | None, work: Callable[[str], Done],
    ) -> Outcome:
        assert auth is not None and db is not None
        pid = project_id_of(repo)
        login = session.login

        def log(result: str, detail: str = "", n: int | None = number) -> None:
            record_audit(pid, login, action, n, result, detail, key)

        try:
            access = auth.can(session, repo, interaction)
        except PolicyUnreadable as e:
            log("denied", "policy unreadable")
            return Outcome(403, {"error": str(e)})
        except PolicyError as e:
            log("denied", "invalid policy")
            return Outcome(403, {"error": f"Invalid .tradvia.yml, so the action is refused: {e}"})
        except Exception:
            log("failed", "role check failed")
            return Outcome(502, {"error": "Could not check your permission on GitHub."})
        if not access.allowed:
            required = getattr(
                access.policy.interactions if access.policy else Interactions(), interaction
            )
            log("denied", f"role {access.role or 'none'} below {required}")
            if access.role is None:
                message = f"You have no access to {repo}."
            else:
                message = (f"Your role in {repo} is '{access.role}'; "
                           f"'{interaction}' needs at least '{required}'.")
            return Outcome(403, {"error": message})
        token = auth.user_token(session)
        if token is None:
            log("denied", "session token unreadable")
            return Outcome(401, {"error": "Sign in again."})
        try:
            done = work(token)
        except CardNotFound as e:
            log("failed", str(e))
            return Outcome(404, {"error": str(e)})
        except ActionError as e:
            log("failed", str(e))
            return Outcome(400, {"error": str(e)})
        except Conflict as e:
            log("failed", str(e))
            return Outcome(409, {"error": str(e)})
        except Refused as e:
            log("denied", str(e))
            return Outcome(403, {"error": str(e)})
        except GitHubError as e:
            log("failed", f"github {e.status}")
            return github_failure(action, e)
        except Exception:  # never echo: the message may carry tokens or the DSN
            log("failed", "internal error")
            return Outcome(500, {"error": "internal error"})
        log("ok", done.detail, done.number)
        return Outcome(200, {"ok": True, **done.extra})

    def perform(
        session: Any, repo: str, action: str, interaction: str, number: int | None,
        key: str | None, work: Callable[[str], Done],
    ) -> Outcome:
        """`execute` once per idempotency key: a repeat within 5 s is ignored."""
        if not key:
            return execute(session, repo, action, interaction, number, key, work)
        assert db is not None
        pid, login = project_id_of(repo), session.login
        now = clock()
        previous = db.recent_audit_key(pid, login, key, now - IDEMPOTENCY_WINDOW)
        with inflight_lock:
            for k, at in list(inflight.items()):
                if now - at >= IDEMPOTENCY_WINDOW:
                    del inflight[k]
            busy = (login, key) in inflight
            if previous is None and not busy:
                inflight[(login, key)] = now
        if previous is not None or busy:
            body: dict[str, Any] = {"ok": True, "duplicate": True}
            if previous is not None:
                body.update(result=previous["result"], issue_number=previous["issue_number"])
            return Outcome(200, body)
        try:
            return execute(session, repo, action, interaction, number, key, work)
        finally:
            with inflight_lock:
                inflight.pop((login, key), None)

    def work_create(repo: str, data: dict[str, Any]) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            title, description = parse_new_card(data)
            issue = auth.github.create_issue(token, repo, title, description)
            detail = title[:80]
            try:  # the webhook also brings it; do not fail an issue that already exists
                pid = project_id_of(repo)
                db.upsert_issue(pid, issue_record(issue))
                db.set_backlog(pid, new_backlog(db.list_backlog(pid), issue["number"], description))
            except Exception:
                detail += " (not added to the backlog)"
            return Done(issue["number"], detail, {
                "issue_number": issue["number"], "html_url": issue.get("html_url"),
            })
        return work

    def work_start(repo: str, number: int) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            issue = auth.github.get_issue(token, repo, number)
            if "pull_request" in issue:
                raise CardNotFound("Card not found in this project.")
            if issue.get("state") == "closed":
                raise Conflict("This card is closed.")
            if READY_LABEL in [lb.get("name") for lb in issue.get("labels") or []]:
                raise Conflict("This card was already started.")
            auth.github.add_label(token, repo, number, READY_LABEL)
            return Done(number, READY_LABEL, {"issue_number": number})
        return work

    def work_reply(repo: str, number: int, data: dict[str, Any]) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            text = parse_reply(data.get("decision"), data.get("text"))
            comments = auth.github.list_comments(token, repo, number)
            if not awaiting_plan(comments, agent_login()):
                raise Conflict("This card is not waiting for a reply to the plan.")
            auth.github.comment(token, repo, number, text)
            return Done(number, f"decision={data.get('decision')}", {"issue_number": number})
        return work

    def work_backlog(repo: str, data: dict[str, Any]) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            pid = project_id_of(repo)
            items = db.list_backlog(pid)
            if "order" in data:
                db.set_backlog(pid, apply_order(items, data["order"]))
                return Done(None, "order", {})
            number = data.get("issue")
            if not isinstance(number, int) or isinstance(number, bool):
                raise ActionError("issue must be a card number")
            db.set_backlog(pid, reorder(items, number, data.get("direction")))
            return Done(number, f"move {data.get('direction')}", {"issue_number": number})
        return work

    def work_merge(repo: str, number: int, data: dict[str, Any]) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            ignore_cla = parse_flag(data, "ignore_cla")
            pull = auth.github.get_pull(token, repo, number)
            if pull.get("merged") or pull.get("state") != "open":
                raise Conflict("This pull request is not open.")
            title = merge_title(pull)
            if pull.get("mergeable_state") == "behind":
                raise Conflict("The branch is behind main. Use 'Update branch' first.")
            sha = pull["head"]["sha"]
            checks = auth.github.list_checks(token, repo, sha)
            blocked = merge_block(checks, ignore_cla)
            if blocked:
                raise Conflict(blocked)
            ignored = ignore_cla and any(
                c["name"] == "cla" and c["state"] == "failed" for c in required_checks(checks)
            )
            auth.github.merge_pull(token, repo, number, title, sha)
            head = pull.get("head") or {}
            deleted = False
            if (head.get("repo") or {}).get("full_name") == repo and head.get("ref"):
                try:  # the merge already happened: a branch that stays is not a failure
                    auth.github.delete_branch(token, repo, head["ref"])
                    deleted = True
                except GitHubError:
                    pass
            detail = "squash" + (" ignore_cla (cla failed)" if ignored else "")
            detail += "" if deleted else " (branch not deleted)"
            return Done(number, detail, {"issue_number": number, "branch_deleted": deleted})
        return work

    def work_update_branch(repo: str, number: int) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            auth.github.update_branch(token, repo, number)
            return Done(number, "update branch", {"issue_number": number})
        return work

    def work_review_deploy(
        repo: str, run_id: int, approve: bool, data: dict[str, Any]
    ) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            comment = data.get("comment")
            if comment is not None and (not isinstance(comment, str) or len(comment) > MAX_COMMENT):
                raise ActionError(f"comment must be text of at most {MAX_COMMENT} characters")
            verb = "approve" if approve else "reject"
            pending = auth.github.list_pending_deployments(token, repo, run_id)
            if not pending:
                raise Conflict("This deploy is not waiting for approval.")
            mine = [
                (p.get("environment") or {}).get("id")
                for p in pending if p.get("current_user_can_approve")
            ]
            if not mine:
                raise Refused("You are not a reviewer of the production environment.")
            auth.github.review_pending_deployment(
                token, repo, run_id, mine, approve, (comment or "").strip() or f"{verb}d in Tradvia"
            )
            return Done(None, f"{verb} run={run_id}", {"run_id": run_id})
        return work

    def work_cancel_run(repo: str, run_id: int) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            waiting = auth.github.list_runs(token, repo, "waiting")
            if run_id not in replaced_run_ids(waiting):
                raise Conflict("Only a deploy replaced by a newer one can be cancelled here.")
            auth.github.cancel_run(token, repo, run_id)
            return Done(None, f"cancel replaced run={run_id}", {"run_id": run_id})
        return work

    def work_post_deploy(repo: str, data: dict[str, Any]) -> Callable[[str], Done]:
        def work(token: str) -> Done:
            backfill = parse_flag(data, "backfill")
            backlog = parse_backlog_input(data.get("backlog"))
            auth.github.dispatch_workflow(
                token, repo, DEPLOY_WORKFLOW, DEPLOY_REF,
                {"backfill": "true" if backfill else "false", "backlog": backlog},
            )
            return Done(None, f"post-deploy backfill={backfill} backlog={backlog}", {})
        return work

    def action_route(action: str, interaction: str) -> Callable[[Request], Any]:
        async def handler(request: Request) -> Response:
            repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
            if auth is None or db is None or repo not in repos:
                return _not_found()
            try:
                session = await run_in_threadpool(auth.session_from, request.cookies.get(SESSION_COOKIE))
            except Exception:
                return private(_store_unavailable())
            if session is None:
                return private(JSONResponse({"error": "Sign in with GitHub first."}, status_code=401))
            key = request.headers.get(IDEMPOTENCY_HEADER)
            if key is not None and not _KEY_RE.fullmatch(key):
                return private(JSONResponse({"error": "invalid idempotency key"}, status_code=400))
            try:
                data = await request.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                return private(JSONResponse({"error": "invalid json"}, status_code=400))
            number = request.path_params.get("n")
            if action == "create_card":
                work = work_create(repo, data)
            elif action == "start_card":
                work = work_start(repo, number)
            elif action == "reply_plan":
                work = work_reply(repo, number, data)
            elif action == "merge_pr":
                work = work_merge(repo, number, data)
            elif action == "update_branch":
                work = work_update_branch(repo, number)
            elif action in ("approve_deploy", "reject_deploy"):
                work = work_review_deploy(
                    repo, request.path_params["run_id"], action == "approve_deploy", data
                )
            elif action == "cancel_run":
                work = work_cancel_run(repo, request.path_params["run_id"])
            elif action == "post_deploy":
                work = work_post_deploy(repo, data)
            else:
                number = data.get("issue") if isinstance(data.get("issue"), int) else None
                work = work_backlog(repo, data)
            try:
                out = await run_in_threadpool(
                    perform, session, repo, action, interaction, number, key, work
                )
            except Exception:
                return private(_store_unavailable())
            return private(JSONResponse(out.body, status_code=out.status))
        return handler

    def read_card(session: Any, repo: str, number: int) -> Outcome:
        assert auth is not None and db is not None
        role = auth.role_of(session, repo)
        if role is None:
            return Outcome(403, {"error": f"You need at least the 'read' role in {repo}."})
        rows = db.list_audit(project_id_of(repo), number)
        plan = None
        try:  # the plan is read with the user's token, and only for who may answer it
            if auth.can(session, repo, "approve_plan", fresh=False).allowed:
                token = auth.user_token(session)
                if token:
                    plan = plan_comment(auth.github.list_comments(token, repo, number), agent_login())
        except GitHubError as e:
            if e.status == 404:
                return Outcome(404, {"error": "Card not found in this project."})
        except Exception:
            plan = None
        return Outcome(200, {"audit": rows, "plan": plan, "awaiting_plan": plan is not None})

    async def card_audit(request: Request) -> Response:
        repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
        if auth is None or db is None or repo not in repos:
            return _not_found()
        try:
            session = await run_in_threadpool(auth.session_from, request.cookies.get(SESSION_COOKIE))
            if session is None:
                return private(JSONResponse({"error": "Sign in with GitHub first."}, status_code=401))
            out = await run_in_threadpool(read_card, session, repo, request.path_params["n"])
        except Exception:
            return private(_store_unavailable())
        return private(JSONResponse(out.body, status_code=out.status))

    # --- reading pull requests and deploys with the user's token (ADR 0007) -------------------

    def rights(session: Any, repo: str, *interactions: str) -> dict[str, bool]:
        """Which buttons to show; the write routes check again on every request."""
        out = {}
        for name in interactions:
            try:
                out[name] = auth.can(session, repo, name, fresh=False).allowed
            except Exception:  # unreadable or invalid policy: fail closed
                out[name] = False
        return out

    def pull_diff(pull: dict[str, Any], filename: str) -> dict[str, Any]:
        file = next((f for f in pull["files"] if f.get("filename") == filename), None)
        if file is None:
            raise CardNotFound("File not found in this pull request.")
        patch = file.get("patch")
        if patch is None:
            return {"filename": filename, "diff": None, "truncated": False}
        return {"filename": filename, "diff": patch[:MAX_DIFF], "truncated": len(patch) > MAX_DIFF}

    def view_pull(
        session: Any, repo: str, number: int, token: str, filename: str | None
    ) -> dict[str, Any]:
        pull = auth.github.get_pull(token, repo, number)
        if filename is not None:
            return pull_diff(pull, filename)
        checks = auth.github.list_checks(token, repo, pull["head"]["sha"])
        comments = auth.github.list_review_comments(token, repo, number)
        body = pull.get("body") or ""
        return {
            "number": number,
            "title": pull.get("title"),
            "state": "merged" if pull.get("merged") else pull.get("state"),
            "html_url": pull.get("html_url"),
            "author": (pull.get("user") or {}).get("login"),
            "branch": (pull.get("head") or {}).get("ref"),
            "base": (pull.get("base") or {}).get("ref"),
            "behind": pull.get("mergeable_state") == "behind",
            "mergeable_state": pull.get("mergeable_state"),
            "description": body,
            "executor": parse_pr_description(body),
            "files": [
                {"filename": f["filename"], "status": f.get("status"),
                 "additions": f.get("additions", 0), "deletions": f.get("deletions", 0),
                 "has_diff": f.get("patch") is not None}
                for f in pull["files"]
            ],
            "checks": required_checks(checks),
            "review_comments": [
                {"author": (c.get("user") or {}).get("login"), "body": c.get("body"),
                 "state": c.get("state"), "path": c.get("path"), "created_at": c.get("created_at")}
                for c in comments
            ],
            "can": rights(session, repo, "merge_pr", "update_branch"),
        }

    def view_deploys(session: Any, repo: str, token: str) -> dict[str, Any]:
        runs = auth.github.list_runs(token, repo, "waiting")[:MAX_DEPLOY_RUNS]
        pending = {r["id"]: auth.github.list_pending_deployments(token, repo, r["id"]) for r in runs}
        return {
            "deploys": deploy_view(runs, pending),
            "backlog": backlog_string(backlog_view(project_id_of(repo))),
            "can": rights(session, repo, "approve_deploy"),
        }

    def read_github(
        session: Any, repo: str, action: str, work: Callable[[Any, str], dict[str, Any]]
    ) -> Outcome:
        assert auth is not None and db is not None
        if auth.role_of(session, repo) is None:
            return Outcome(403, {"error": f"You need at least the 'read' role in {repo}."})
        token = auth.user_token(session)
        if token is None:
            return Outcome(401, {"error": "Sign in again."})
        try:
            return Outcome(200, work(session, token))
        except CardNotFound as e:
            return Outcome(404, {"error": str(e)})
        except ActionError as e:
            return Outcome(400, {"error": str(e)})
        except GitHubError as e:
            return github_failure(action, e)
        except Exception:  # never echo: the message may carry tokens or the DSN
            return Outcome(500, {"error": "internal error"})

    def read_route(kind: str) -> Callable[[Request], Any]:
        async def handler(request: Request) -> Response:
            params = request.path_params
            repo = f"{params['owner']}/{params['repo']}"
            if auth is None or db is None or repo not in repos:
                return _not_found()
            filename = params.get("path") if kind == "diff" else None
            if kind == "diff" and not safe_path(filename or ""):
                return _not_found()

            def work(s: Any, t: str) -> dict[str, Any]:
                if kind == "deploys":
                    return view_deploys(s, repo, t)
                return view_pull(s, repo, params["n"], t, filename)

            try:
                session = await run_in_threadpool(auth.session_from, request.cookies.get(SESSION_COOKIE))
                if session is None:
                    return private(JSONResponse({"error": "Sign in with GitHub first."}, status_code=401))
                action = "view_deploys" if kind == "deploys" else "view_pull"
                out = await run_in_threadpool(read_github, session, repo, action, work)
            except Exception:
                return private(_store_unavailable())
            return private(JSONResponse(out.body, status_code=out.status))
        return handler

    return Starlette(
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=csrf_guard)],
        routes=[
            Route("/api/auth/login", auth_login, methods=["GET"]),
            Route("/api/auth/callback", auth_callback, methods=["GET"]),
            Route("/api/auth/logout", auth_logout, methods=["POST"]),
            Route("/api/auth/me", auth_me, methods=["GET"]),
            Route(WEBHOOK_PATH, webhook, methods=["POST"]),
            Route("/api/projects", projects, methods=["GET"]),
            Route("/api/push/key", push_key, methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/pending", user_route(pending_view), methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/push/subscription",
                  user_route(subscribe, body=True), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/push/subscription",
                  user_route(unsubscribe, body=True), methods=["DELETE"]),
            Route("/api/projects/{owner}/{repo}/push/prefs", user_route(prefs_view), methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/push/prefs",
                  user_route(prefs_save, body=True), methods=["PUT"]),
            Route("/api/projects/{owner}/{repo}/cards", action_route("create_card", "start_card"),
                  methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/cards/{n:int}/start",
                  action_route("start_card", "start_card"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/cards/{n:int}/reply",
                  action_route("reply_plan", "approve_plan"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/backlog",
                  action_route("reorder_backlog", "start_card"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/cards/{n:int}/audit", card_audit, methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/pulls/{n:int}", read_route("pull"), methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/pulls/{n:int}/files/{path:path}/diff",
                  read_route("diff"), methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/pulls/{n:int}/merge",
                  action_route("merge_pr", "merge_pr"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/pulls/{n:int}/update-branch",
                  action_route("update_branch", "update_branch"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/deployments/pending",
                  read_route("deploys"), methods=["GET"]),
            Route("/api/projects/{owner}/{repo}/deployments/{run_id:int}/approve",
                  action_route("approve_deploy", "approve_deploy"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/deployments/{run_id:int}/reject",
                  action_route("reject_deploy", "approve_deploy"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/deployments/{run_id:int}/cancel",
                  action_route("cancel_run", "approve_deploy"), methods=["POST"]),
            Route("/api/projects/{owner}/{repo}/operations/post-deploy",
                  action_route("post_deploy", "approve_deploy"), methods=["POST"]),
            Route("/api/store/health", store_health, methods=["GET"]),
            Route("/api/store/repos/{path:path}", store_read, methods=["GET"]),
            Route("/api/healthz", healthz, methods=["GET"]),
            Route("/api/github/{path:path}", github, methods=["GET"]),
        ]
    )


app = create_app()
