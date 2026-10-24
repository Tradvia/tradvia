# SPDX-License-Identifier: FSL-1.1-ALv2
"""Command line client of the programmatic API (ADR 0009).

    tradvia token create --project o/r [--role triage] [--days 30]   (needs TRADVIA_SESSION)
    tradvia token revoke --project o/r ID                            (needs TRADVIA_SESSION)
    tradvia card create --project o/r --title T [--body B] [--start] (needs TRADVIA_TOKEN)

`TRADVIA_URL` is the panel's address. `TRADVIA_SESSION` is the value of the
`__Host-tradvia_session` cookie of a signed-in browser; it is only used to manage tokens.
The token printed by `token create` is shown once; keep it in `TRADVIA_TOKEN`.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any, Callable

import requests

from .auth import API_TOKEN_DEFAULT_DAYS, CSRF_HEADER, SESSION_COOKIE

TIMEOUT = 15
Http = Callable[..., Any]  # requests.request or a compatible fake


class ApiError(SystemExit):
    """The server refused or failed; the message is safe to print (never the token)."""


def _base(url: str | None) -> str:
    base = (url or os.getenv("TRADVIA_URL", "")).rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ApiError("Set TRADVIA_URL (or --url) to the panel address, e.g. https://app.tradvia.org")
    return base


def _call(http: Http, method: str, url: str, headers: dict[str, str], body: Any = None) -> Any:
    r = http(method, url, headers=headers, json=body, timeout=TIMEOUT)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        message = data.get("error") if isinstance(data, dict) else None
        raise ApiError(f"HTTP {r.status_code}: {message or 'request failed'}")
    return data


def _project_path(base: str, project: str) -> str:
    return f"{base}/api/projects/{project}"


def session_headers(http: Http, base: str, session: str) -> dict[str, str]:
    """Cookie, Origin and CSRF header of a signed-in browser session."""
    cookie = {"Cookie": f"{SESSION_COOKIE}={session}", "Origin": base}
    me = _call(http, "GET", f"{base}/api/auth/me", cookie)
    if not isinstance(me, dict) or not me.get("authenticated"):
        raise ApiError("TRADVIA_SESSION is not a signed-in session")
    return {**cookie, CSRF_HEADER: me["csrf"]}


def bearer_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Tradvia-Idempotency-Key": uuid.uuid4().hex,
    }


def create_token(
    http: Http, base: str, session: str, project: str, role: str, days: int
) -> dict[str, Any]:
    headers = session_headers(http, base, session)
    return _call(http, "POST", _project_path(base, project) + "/tokens", headers,
                 {"role": role, "expires_in_days": days})


def revoke_token(http: Http, base: str, session: str, project: str, token_id: int) -> dict[str, Any]:
    headers = session_headers(http, base, session)
    return _call(http, "DELETE", f"{_project_path(base, project)}/tokens/{token_id}", headers)


def create_card(
    http: Http, base: str, token: str, project: str, title: str, body: str, start: bool
) -> dict[str, Any]:
    out = _call(http, "POST", _project_path(base, project) + "/cards", bearer_headers(token),
                {"title": title, "description": body})
    if start:
        number = out["issue_number"]
        _call(http, "POST", f"{_project_path(base, project)}/cards/{number}/start",
              bearer_headers(token), {})
    return out


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ApiError(f"Missing environment variable {name}")
    return value


def main(argv: list[str] | None = None, http: Http = requests.request) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="tradvia")
    parser.add_argument("--url", help="panel address (default: TRADVIA_URL)")
    groups = parser.add_subparsers(dest="group", required=True)

    token = groups.add_parser("token").add_subparsers(dest="action", required=True)
    create = token.add_parser("create")
    create.add_argument("--project", required=True, help="repository owner/repo")
    create.add_argument("--role", default="triage", help="role of the token (default: triage)")
    create.add_argument("--days", type=int, default=API_TOKEN_DEFAULT_DAYS, help="lifetime in days")
    revoke = token.add_parser("revoke")
    revoke.add_argument("--project", required=True, help="repository owner/repo")
    revoke.add_argument("id", type=int, help="token id")

    card = groups.add_parser("card").add_subparsers(dest="action", required=True)
    new = card.add_parser("create")
    new.add_argument("--project", required=True, help="repository owner/repo")
    new.add_argument("--title", required=True)
    new.add_argument("--body", default="")
    new.add_argument("--start", action="store_true", help="also add agent:ready (starts the planner)")

    args = parser.parse_args(argv)
    base = _base(args.url)
    if args.group == "token" and args.action == "create":
        out = create_token(http, base, _required_env("TRADVIA_SESSION"), args.project,
                           args.role, args.days)
        print(f"token id {out['id']} ({out['role']} in {out['project']}), shown once:")
        print(out["token"])
    elif args.group == "token":
        out = revoke_token(http, base, _required_env("TRADVIA_SESSION"), args.project, args.id)
        print(f"token {args.id} revoked")
    else:
        out = create_card(http, base, _required_env("TRADVIA_TOKEN"), args.project,
                          args.title, args.body, args.start)
        print(json.dumps({k: out.get(k) for k in ("issue_number", "html_url")}, ensure_ascii=False))
    return out
