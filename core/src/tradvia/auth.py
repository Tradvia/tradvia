# SPDX-License-Identifier: FSL-1.1-ALv2
"""Sign in with GitHub (ADR 0004 phase 2, ADR 0008): sessions, CSRF and per-project roles.

The GitHub App is only an identity provider (OAuth user-to-server flow with a random
`state` and PKCE). The user token is stored encrypted, is never sent to the browser and
is never logged: this module does not log at all and its errors carry no details.

Everything here is blocking; `api.py` calls it through a thread pool.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, NamedTuple, Protocol
from urllib.parse import quote, urlencode, urlsplit

import requests

from .actions import GitHubError
from .policy import POLICY_SETTING, Policy, ROLES, load_policy, role_from_github, role_of, role_rank, with_policy_setting
from .store import Store

SESSION_COOKIE = "__Host-tradvia_session"
STATE_COOKIE = "__Host-tradvia_login"
CSRF_HEADER = "x-tradvia-csrf"
CALLBACK_PATH = "/api/auth/callback"
DEFAULT_RETURN = "/runs/"

SESSION_TTL = 8 * 3600  # renewed on every use...
SESSION_MAX = 7 * 24 * 3600  # ...up to this absolute limit
STATE_TTL = 600
ROLE_TTL = 300
POLICY_TTL = 60
MAX_RETURN_LENGTH = 512

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"
UPSTREAM_TIMEOUT = 10

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
API_TOKEN_PREFIX = "tvt_"
API_TOKEN_DEFAULT_DAYS = 30
API_TOKEN_MAX_DAYS = 90
# ADR 0014: an `agent` token works cards; a `manager` token is an AI manager below the owner.
TOKEN_KINDS = ("agent", "manager")
# What the owner may let an AI manager do beyond its base, one grant each. Merge, deploy, budget,
# connections and roles are not grantable at all.
GRANTABLE = ("reorder_backlog", "approve_plan")
_API_TOKEN_RE = re.compile(API_TOKEN_PREFIX + r"[A-Za-z0-9_-]{43}")


class AuthError(Exception):
    """Login failed. The message is generic on purpose: it may reach a response."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str
    secret_key: str
    domain: str

    @property
    def origin(self) -> str:
        return "https://" + self.domain

    @property
    def redirect_uri(self) -> str:
        return self.origin + CALLBACK_PATH


def load_config(env: Mapping[str, str] | None = None) -> AuthConfig | None:
    """None (sign-in off) unless client id, client secret and secret key are all set."""
    env = os.environ if env is None else env
    client_id = env.get("TRADVIA_GITHUB_APP_CLIENT_ID", "").strip()
    client_secret = env.get("TRADVIA_GITHUB_APP_CLIENT_SECRET", "").strip()
    secret_key = env.get("TRADVIA_SECRET_KEY", "").strip()
    domain = env.get("TRADVIA_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    if not (client_id and client_secret and secret_key and domain):
        return None
    return AuthConfig(client_id, client_secret, secret_key, domain)


# --- crypto ---------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Keys:
    """Two independent keys derived (HKDF) from TRADVIA_SECRET_KEY."""

    def __init__(self, secret_key: str) -> None:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        def derive(info: bytes) -> bytes:
            return HKDF(hashes.SHA256(), 32, None, info).derive(secret_key.encode())

        self._fernet = Fernet(base64.urlsafe_b64encode(derive(b"tradvia-token-encryption")))
        self._secrets_fernet = Fernet(
            base64.urlsafe_b64encode(derive(b"tradvia-connection-secrets"))
        )
        self._csrf_key = derive(b"tradvia-csrf")

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, cipher: str) -> str | None:
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(cipher.encode()).decode()
        except InvalidToken:
            return None

    # Project connection secrets (connections.py): a key of their own, so the ciphertext of
    # one kind says nothing about the other.
    def encrypt_secret(self, plain: str) -> str:
        return self._secrets_fernet.encrypt(plain.encode()).decode()

    def decrypt_secret(self, cipher: str) -> str | None:
        from cryptography.fernet import InvalidToken

        try:
            return self._secrets_fernet.decrypt(cipher.encode()).decode()
        except InvalidToken:
            return None

    def csrf(self, cookie_token: str) -> str:
        return _b64(hmac.new(self._csrf_key, cookie_token.encode(), hashlib.sha256).digest())


def hash_token(cookie_token: str) -> str:
    return hashlib.sha256(cookie_token.encode()).hexdigest()


def pkce_pair() -> tuple[str, str]:
    """`(code_verifier, S256 code_challenge)`."""
    verifier = secrets.token_urlsafe(48)
    return verifier, _b64(hashlib.sha256(verifier.encode()).digest())


# --- request checks ---------------------------------------------------------------


def safe_return_path(path: str | None) -> str:
    """Only `/runs/...` on this site; anything else becomes `/runs/`."""
    if not path or len(path) > MAX_RETURN_LENGTH or not path.startswith(DEFAULT_RETURN):
        return DEFAULT_RETURN
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return DEFAULT_RETURN
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or "//" in parts.path or "%" in parts.path:
        return DEFAULT_RETURN
    if any(seg in (".", "..") for seg in parts.path.split("/")):
        return DEFAULT_RETURN
    return path


def origin_ok(origin: str | None, referer: str | None, expected_origin: str) -> bool:
    """`Origin` must match; without it, `Referer` must point into this site."""
    if origin:
        return origin == expected_origin
    if referer:
        return referer == expected_origin or referer.startswith(expected_origin + "/")
    return False


# --- GitHub ---------------------------------------------------------------------


class GitHubIdentity(Protocol):
    """What the login needs from GitHub. Tests inject a fake."""

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> str:
        """User access token. Raises AuthError."""

    def get_user(self, token: str) -> dict[str, Any]:
        """`{id, login, avatar_url}`. Raises AuthError."""

    def get_permission(self, token: str, repo: str, login: str) -> tuple[str | None, str | None]:
        """`(permission, role_name)` of the collaborator, or `(None, None)`."""

    def get_file(self, token: str, repo: str, path: str, ref: str) -> str | None:
        """Text of a file (default branch when `ref` is empty); None on 404.

        Raises PolicyUnreadable on 401/403."""


class PolicyUnreadable(Exception):
    """`.tradvia.yml` cannot be read with the user's token: the interaction is refused."""


UNREADABLE_POLICY = (
    "Cannot read .tradvia.yml: the GitHub App needs the Contents: Read-only permission."
)

# Why a person has no role. GitHub's answer is classified, never flattened into "no role":
# each reason has its own message (role_refusal), and a failed lookup is not cached.
ROLE_NONE = "not_collaborator"  # GitHub says: not a collaborator (the plain no-access answer)
ROLE_TOKEN = "sign_in_expired"  # the sign-in token is revoked, expired or unreadable (401)
ROLE_APP = "app_permission"  # GitHub refused the check (403) and it is not a rate limit
ROLE_RATE_LIMIT = "rate_limited"  # GitHub's rate limit (403 or 429)
ROLE_UNAVAILABLE = "github_unavailable"  # unreachable, 5xx, or anything unexpected

_ROLE_NOTICES = {
    ROLE_TOKEN: "Your GitHub sign-in is no longer valid (expired or revoked). Sign in again.",
    ROLE_APP: (
        "GitHub refused the role check for this sign-in. The GitHub App needs Metadata: "
        "Read-only: an owner must check it and accept any update on the installation "
        "(docs/github-app.md), then sign in again."
    ),
    ROLE_RATE_LIMIT: (
        "GitHub's rate limit was reached while checking your role. Try again in a few minutes."
    ),
    ROLE_UNAVAILABLE: "GitHub could not be reached to check your role. Try again.",
}


def role_refusal(reason: str | None) -> str | None:
    """What to say to someone without a role, given why. None keeps the plain no-access answer."""
    return _ROLE_NOTICES.get(reason or "")


class RoleLookupFailed(Exception):
    """GitHub did not confirm a role. `reason` is one of the ROLE_* codes."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_REASON_RE = re.compile(r"[a-z_]{1,64}")


def _reason(data: Any, status: int) -> str:
    """GitHub's OAuth error code (e.g. `incorrect_client_credentials`), safe to show.

    Only a short lowercase code passes; anything else becomes the HTTP status, so no
    token, secret or free text from GitHub reaches the response.
    """
    code = data.get("error") if isinstance(data, dict) else None
    if isinstance(code, str) and _REASON_RE.fullmatch(code):
        return code
    return f"HTTP {status}"


def _github_message(r: requests.Response) -> str:
    """GitHub's error `message` (short), only used to classify a refusal; never shown."""
    try:
        data = r.json()
    except Exception:
        return ""
    return str(data.get("message") or "")[:200] if isinstance(data, dict) else ""


def _lookup_refusal(r: requests.Response) -> str:
    """The ROLE_* code for a refused role lookup. GitHub's text only classifies it; never shown."""
    if r.status_code == 401:
        return ROLE_TOKEN
    if r.status_code == 429 or (r.status_code == 403 and "rate limit" in _github_message(r).lower()):
        return ROLE_RATE_LIMIT
    if r.status_code == 403:
        return ROLE_APP
    return ROLE_UNAVAILABLE


class HttpGitHub:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._id, self._secret = client_id, client_secret

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> str:
        try:
            r = requests.post(
                TOKEN_URL,
                data={
                    "client_id": self._id, "client_secret": self._secret, "code": code,
                    "redirect_uri": redirect_uri, "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=UPSTREAM_TIMEOUT,
            )
            data = r.json() if r.status_code == 200 else {}
        except Exception:  # never echo: the error may carry the request body
            raise AuthError("github unavailable", 502) from None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise AuthError(f"github refused the login ({_reason(data, r.status_code)})", 502)
        return token

    def _get(self, token: str, path: str) -> requests.Response:
        return requests.get(
            GITHUB_API + path,
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
            timeout=UPSTREAM_TIMEOUT,
        )

    def get_user(self, token: str) -> dict[str, Any]:
        try:
            r = self._get(token, "/user")
            data = r.json() if r.status_code == 200 else None
        except Exception:
            raise AuthError("github unavailable", 502) from None
        if not isinstance(data, dict) or not data.get("login") or not isinstance(data.get("id"), int):
            raise AuthError(f"github refused the login (user lookup: HTTP {r.status_code})", 502)
        return {"id": data["id"], "login": data["login"], "avatar_url": data.get("avatar_url")}

    def get_permission(self, token: str, repo: str, login: str) -> tuple[str | None, str | None]:
        """(permission, role_name) of `login`. A refusal raises RoleLookupFailed with its reason."""
        try:
            r = self._get(token, f"/repos/{repo}/collaborators/{login}/permission")
        except Exception:  # never echo: the error may carry request headers
            raise RoleLookupFailed(ROLE_UNAVAILABLE) from None
        if r.status_code == 404:  # not a collaborator: a normal answer, with no role
            return None, None
        if r.status_code != 200:
            raise RoleLookupFailed(_lookup_refusal(r))
        data = r.json()
        return data.get("permission"), data.get("role_name")

    def get_file(self, token: str, repo: str, path: str, ref: str) -> str | None:
        try:
            r = self._get(token, f"/repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else ""))
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                raise PolicyUnreadable(UNREADABLE_POLICY)
            if r.status_code != 200:
                raise RuntimeError("github unavailable")
            return base64.b64decode(r.json()["content"]).decode("utf-8")
        except (PolicyUnreadable, RuntimeError):
            raise
        except Exception:  # never echo: the error may carry request headers
            raise RuntimeError("github unavailable") from None

    # Writes and card reads, always with the user's token (GitHubActions in actions.py).
    def _call(self, token: str, method: str, path: str, **kw: Any) -> Any:
        try:
            r = requests.request(
                method,
                GITHUB_API + path,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=UPSTREAM_TIMEOUT,
                **kw,
            )
            ok, status = r.status_code < 300, r.status_code
            data = (r.json() if r.content else None) if ok else None
        except Exception:  # never echo: the error may carry request headers
            raise GitHubError(502) from None
        if not ok:
            raise GitHubError(status, _github_message(r))
        return data

    def get_issue(self, token: str, repo: str, number: int) -> dict[str, Any]:
        return self._call(token, "GET", f"/repos/{repo}/issues/{number}")

    def create_issue(self, token: str, repo: str, title: str, body: str) -> dict[str, Any]:
        return self._call(
            token, "POST", f"/repos/{repo}/issues", json={"title": title, "body": body}
        )

    def update_issue(
        self, token: str, repo: str, number: int, title: str | None, body: str | None
    ) -> dict[str, Any]:
        fields = {k: v for k, v in (("title", title), ("body", body)) if v is not None}
        return self._call(token, "PATCH", f"/repos/{repo}/issues/{number}", json=fields)

    def add_label(self, token: str, repo: str, number: int, label: str) -> None:
        self._call(token, "POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": [label]})

    def remove_label(self, token: str, repo: str, number: int, label: str) -> None:
        try:
            self._call(token, "DELETE", f"/repos/{repo}/issues/{number}/labels/{quote(label, safe='')}")
        except GitHubError as e:
            if e.status != 404:  # 404: the issue does not have that label any more
                raise

    def comment(self, token: str, repo: str, number: int, body: str) -> None:
        self._call(token, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    def list_comments(self, token: str, repo: str, number: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(1, 11):  # 1000 comments at most; the plan is among the last ones
            chunk = self._call(
                token, "GET", f"/repos/{repo}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            )
            out.extend(chunk)
            if len(chunk) < 100:
                break
        return out

    # Pull requests and deploys (ADR 0007).
    def _pages(self, token: str, path: str, key: str | None = None) -> list[Any]:
        out: list[Any] = []
        for page in range(1, 11):
            data = self._call(token, "GET", path, params={"per_page": 100, "page": page})
            chunk = data.get(key, []) if key else data
            out.extend(chunk)
            if len(chunk) < 100:
                break
        return out

    def get_pull(self, token: str, repo: str, number: int) -> dict[str, Any]:
        pull = self._call(token, "GET", f"/repos/{repo}/pulls/{number}")
        pull["files"] = self._pages(token, f"/repos/{repo}/pulls/{number}/files")
        return pull

    def list_checks(self, token: str, repo: str, sha: str) -> list[dict[str, Any]]:
        return self._pages(token, f"/repos/{repo}/commits/{sha}/check-runs", "check_runs")

    def list_review_comments(self, token: str, repo: str, number: int) -> list[dict[str, Any]]:
        reviews = self._pages(token, f"/repos/{repo}/pulls/{number}/reviews")
        inline = self._pages(token, f"/repos/{repo}/pulls/{number}/comments")
        out = [
            {"user": r.get("user"), "body": r.get("body"), "state": r.get("state"),
             "path": None, "created_at": r.get("submitted_at")}
            for r in reviews
            if (r.get("body") or "").strip() or r.get("state") in ("APPROVED", "CHANGES_REQUESTED")
        ]
        out += [
            {"user": c.get("user"), "body": c.get("body"), "state": "COMMENTED",
             "path": c.get("path"), "created_at": c.get("created_at")}
            for c in inline
        ]
        return sorted(out, key=lambda c: c.get("created_at") or "")

    def merge_pull(self, token: str, repo: str, number: int, title: str, sha: str) -> None:
        self._call(
            token, "PUT", f"/repos/{repo}/pulls/{number}/merge",
            json={"merge_method": "squash", "commit_title": title, "sha": sha},
        )

    # Actions secrets of the repository (connections.py, ADR 0009).
    def get_actions_public_key(self, token: str, repo: str) -> tuple[str, str]:
        data = self._call(token, "GET", f"/repos/{repo}/actions/secrets/public-key")
        return data["key_id"], data["key"]

    def put_actions_secret(
        self, token: str, repo: str, name: str, encrypted_value: str, key_id: str
    ) -> None:
        self._call(
            token, "PUT", f"/repos/{repo}/actions/secrets/{name}",
            json={"encrypted_value": encrypted_value, "key_id": key_id},
        )

    def delete_actions_secret(self, token: str, repo: str, name: str) -> None:
        self._call(token, "DELETE", f"/repos/{repo}/actions/secrets/{name}")

    def delete_branch(self, token: str, repo: str, branch: str) -> None:
        self._call(token, "DELETE", f"/repos/{repo}/git/refs/heads/{quote(branch, safe='/')}")

    def update_branch(self, token: str, repo: str, number: int) -> None:
        self._call(token, "PUT", f"/repos/{repo}/pulls/{number}/update-branch")

    def list_runs(self, token: str, repo: str, status: str) -> list[dict[str, Any]]:
        data = self._call(
            token, "GET", f"/repos/{repo}/actions/runs", params={"status": status, "per_page": 50}
        )
        return data.get("workflow_runs", [])

    def list_pending_deployments(self, token: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        return self._call(token, "GET", f"/repos/{repo}/actions/runs/{run_id}/pending_deployments")

    def review_pending_deployment(
        self, token: str, repo: str, run_id: int, environment_ids: list[int], approve: bool,
        comment: str,
    ) -> None:
        self._call(
            token, "POST", f"/repos/{repo}/actions/runs/{run_id}/pending_deployments",
            json={
                "environment_ids": environment_ids,
                "state": "approved" if approve else "rejected",
                "comment": comment,
            },
        )

    def cancel_run(self, token: str, repo: str, run_id: int) -> None:
        self._call(token, "POST", f"/repos/{repo}/actions/runs/{run_id}/cancel")

    def approve_run(self, token: str, repo: str, run_id: int) -> None:
        # Starts a run GitHub holds for approval (fork PRs, first contributions): Actions write.
        self._call(token, "POST", f"/repos/{repo}/actions/runs/{run_id}/approve")

    def dispatch_workflow(
        self, token: str, repo: str, workflow: str, ref: str, inputs: dict[str, str],
    ) -> None:
        self._call(
            token, "POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json={"ref": ref, "inputs": inputs},
        )


class _UserRoles:
    """`policy.RoleSource` backed by the signed-in user's own token."""

    base = ""  # empty: the contents API then answers for the default branch

    def __init__(self, github: GitHubIdentity, token: str, repo: str) -> None:
        self._github, self._token, self._repo = github, token, repo

    def get_file(self, path: str, ref: str) -> str | None:
        return self._github.get_file(self._token, self._repo, path, ref)

    def get_collaborator_role(self, login: str) -> str | None:
        permission, role_name = self._github.get_permission(self._token, self._repo, login)
        return role_from_github(permission, role_name)


# --- sessions -----------------------------------------------------------------------


class Session(NamedTuple):
    token: str  # the cookie value; never leaves the server except in the cookie
    rec: dict[str, Any]
    max_age: int  # seconds left, for the renewed cookie

    @property
    def login(self) -> str:
        return self.rec["login"]


class LoginStart(NamedTuple):
    url: str
    state: str


class Access(NamedTuple):
    role: str | None
    policy: Policy | None  # None when the role is missing: the file is not even read
    allowed: bool
    reason: str | None = None  # why there is no role (a ROLE_* code), for the answer


class Auth:
    def __init__(
        self,
        config: AuthConfig,
        store: Store,
        github: GitHubIdentity | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.store = store
        self.github = github or HttpGitHub(config.client_id, config.client_secret)
        self.now = now
        self.keys = Keys(config.secret_key)
        self._policies: dict[tuple[str, str], tuple[float, Policy]] = {}

    # login
    def start_login(self, next_path: str | None) -> LoginStart:
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        now = self.now()
        self.store.put_login_state(
            state, verifier, safe_return_path(next_path), now + STATE_TTL, now
        )
        query = urlencode(
            {
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return LoginStart(f"{AUTHORIZE_URL}?{query}", state)

    def finish_login(
        self, code: str | None, state: str | None, state_cookie: str | None
    ) -> tuple[Session, str]:
        """Validates the one-time state, signs the user in. Returns `(session, return_path)`."""
        if not code or not state or not state_cookie:
            raise AuthError("invalid login")
        if not hmac.compare_digest(state.encode(), state_cookie.encode()):
            raise AuthError("invalid login")
        pending = self.store.take_login_state(state, self.now())
        if pending is None:
            raise AuthError("invalid login")
        token = self.github.exchange_code(code, self.config.redirect_uri, pending["code_verifier"])
        user = self.github.get_user(token)
        now = self.now()
        cookie_token = secrets.token_urlsafe(32)
        rec = {
            "id": hash_token(cookie_token),
            "user_id": user["id"],
            "login": user["login"],
            "avatar_url": user.get("avatar_url"),
            "token_enc": self.keys.encrypt(token),
            "created_at": now,
            "expires_at": now + SESSION_TTL,
            "absolute_expires_at": now + SESSION_MAX,
            "last_used_at": now,
            "role_cache": {},
        }
        self.store.create_session(rec)
        return Session(cookie_token, rec, SESSION_TTL), safe_return_path(pending["return_path"])

    # session
    def session_from(self, cookie_token: str | None, renew: bool = True) -> Session | None:
        """The live session for a cookie value; tampered, unknown or expired means None."""
        if not cookie_token or not _TOKEN_RE.fullmatch(cookie_token):
            return None
        sid = hash_token(cookie_token)
        rec = self.store.get_session(sid)
        if rec is None:
            return None
        now = self.now()
        if rec["expires_at"] <= now or rec["absolute_expires_at"] <= now:
            self.store.delete_session(sid)
            return None
        if self.keys.decrypt(rec["token_enc"]) is None:  # secret key changed
            self.store.delete_session(sid)
            return None
        if renew:
            rec["expires_at"] = min(now + SESSION_TTL, rec["absolute_expires_at"])
            rec["last_used_at"] = now
            self.store.update_session(sid, rec["expires_at"], now)
        return Session(cookie_token, rec, max(1, int(rec["expires_at"] - now)))

    def logout(self, cookie_token: str | None) -> None:
        if cookie_token and _TOKEN_RE.fullmatch(cookie_token):
            self.store.delete_session(hash_token(cookie_token))

    def csrf_token(self, session: Session) -> str:
        return self.keys.csrf(session.token)

    def csrf_ok(self, session: Session, header: str | None) -> bool:
        return bool(header) and hmac.compare_digest(
            self.csrf_token(session).encode(), (header or "").encode("utf-8", "replace")
        )

    def origin_ok(self, origin: str | None, referer: str | None) -> bool:
        return origin_ok(origin, referer, self.config.origin)

    # API tokens (ADR 0009)
    def create_api_token(
        self, session: Session, repo: str, role: str, ttl_days: int, kind: str = "agent"
    ) -> tuple[int, str, float]:
        """New token for the signed-in user in one project. Returns `(id, token, expires_at)`.

        The token is shown once; only its hash is stored. It can never exceed the role the
        user has in `repo` right now. A `manager` token (ADR 0014) is an AI manager: only an
        admin of the project makes one, and it always has the role `write`.
        """
        if kind not in TOKEN_KINDS:
            raise AuthError(f"kind must be one of {', '.join(TOKEN_KINDS)}")
        if kind == "manager":
            role = "write"
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}")
        if not 1 <= ttl_days <= API_TOKEN_MAX_DAYS:
            raise AuthError(f"expires_in_days must be between 1 and {API_TOKEN_MAX_DAYS}")
        own = self.role_of(session, repo)
        if own is None:
            raise AuthError(f"You have no access to {repo}.", 403)
        if role_rank(role) > role_rank(own):
            raise AuthError(f"Your role in {repo} is '{own}': a token cannot have '{role}'.", 403)
        if kind == "manager" and own != "admin":
            raise AuthError("Only an admin of the project makes an AI manager token.", 403)
        now = self.now()
        raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = now + ttl_days * 86400
        token_id = self.store.create_api_token({
            "login": session.login, "project_id": self.store.ensure_project(repo), "role": role,
            "token_hash": hash_token(raw), "token_enc": session.rec["token_enc"],
            "created_at": now, "expires_at": expires_at, "kind": kind,
        })
        return token_id, raw, expires_at

    # AI manager grants (ADR 0014)
    def _own_manager_token(self, session: Session, repo: str, token_id: int) -> dict[str, Any]:
        """The caller's own active manager token in `repo`, or AuthError. Only an admin."""
        if self.role_of(session, repo) != "admin":
            raise AuthError("Only an admin of the project grants or revokes an AI manager's "
                            "permissions.", 403)
        pid = self.store.ensure_project(repo)
        tok = next((t for t in self.store.list_api_tokens(session.login, pid)
                    if t["id"] == token_id), None)
        if tok is None:
            raise AuthError("Token not found.", 404)
        if tok.get("kind") != "manager":
            raise AuthError("Permissions are granted only to an AI manager token.", 400)
        if tok["revoked_at"] is not None or tok["expires_at"] <= self.now():
            raise AuthError("This token is expired or revoked.", 400)
        return tok

    def grant(self, session: Session, repo: str, token_id: int, permission: str,
              ttl_days: int) -> tuple[int, float]:
        """Lets one AI manager do one more thing for up to `ttl_days` (never past the token).
        Returns `(grant_id, expires_at)`."""
        if permission not in GRANTABLE:
            raise AuthError(f"permission must be one of {', '.join(GRANTABLE)}")
        if not 1 <= ttl_days <= API_TOKEN_MAX_DAYS:
            raise AuthError(f"expires_in_days must be between 1 and {API_TOKEN_MAX_DAYS}")
        tok = self._own_manager_token(session, repo, token_id)
        now = self.now()
        expires_at = min(now + ttl_days * 86400, tok["expires_at"])
        grant_id = self.store.create_grant({
            "token_id": token_id, "permission": permission, "granted_by": session.login,
            "created_at": now, "expires_at": expires_at})
        return grant_id, expires_at

    def revoke_grant(self, session: Session, repo: str, token_id: int, grant_id: int) -> bool:
        self._own_manager_token(session, repo, token_id)
        return self.store.revoke_grant(grant_id, token_id, self.now())

    def active_grants(self, token_id: int) -> frozenset[str]:
        """The permissions an AI manager holds right now: granted, not expired, not revoked."""
        now = self.now()
        return frozenset(g["permission"] for g in self.store.list_grants(token_id)
                         if g["revoked_at"] is None and g["expires_at"] > now)

    def revoke_api_token(self, session: Session, repo: str, token_id: int) -> bool:
        pid = self.store.ensure_project(repo)
        return self.store.revoke_api_token(token_id, session.login, pid, self.now())

    def api_session(self, raw: str | None) -> Session | None:
        """The caller for a bearer token; unknown, expired or revoked means None.

        It behaves like a session of the token's owner, limited to one project and one role.
        """
        if not raw or not _API_TOKEN_RE.fullmatch(raw):
            return None
        rec = self.store.get_api_token(hash_token(raw))
        if rec is None or rec["revoked_at"] is not None or rec["expires_at"] <= self.now():
            return None
        if self.keys.decrypt(rec["token_enc"]) is None:  # secret key changed
            return None
        now = self.now()
        session_rec = {
            "id": f"api-token:{rec['id']}", "login": rec["login"], "token_enc": rec["token_enc"],
            "role_cache": {}, "expires_at": rec["expires_at"], "last_used_at": now,
            "api_token": {"project_id": rec["project_id"], "role": rec["role"],
                          "id": rec["id"], "kind": rec.get("kind") or "agent"},
        }
        return Session(raw, session_rec, max(1, int(rec["expires_at"] - now)))

    # roles
    def role_of(self, session: Session, repo: str) -> str | None:
        """Role of the caller in `repo`; an API token gets the lower of its own and its owner's."""
        return self.role_status(session, repo)[0]

    def role_status(self, session: Session, repo: str) -> tuple[str | None, str | None]:
        """(role, reason): the caller's role in `repo`, or None and the ROLE_* code saying why."""
        role, reason = self._github_role(session, repo)
        scope = session.rec.get("api_token")
        if scope is None or role is None:
            return role, reason
        if scope["project_id"] != self.store.ensure_project(repo):
            return None, ROLE_NONE
        return min(role, scope["role"], key=role_rank), None

    def _github_role(self, session: Session, repo: str) -> tuple[str | None, str | None]:
        """Role of the signed-in user in `repo` and, without one, why. Cached for ROLE_TTL.

        Only an answer from GitHub is cached: a refused or failed lookup is asked again on the
        next request, so a fixed App or a new sign-in takes effect without waiting.
        """
        now = self.now()
        cache = session.rec["role_cache"]
        hit = cache.get(repo)
        if hit and now - hit["at"] < ROLE_TTL:
            return hit["role"], (None if hit["role"] else ROLE_NONE)
        token = self.keys.decrypt(session.rec["token_enc"])
        if token is None:
            return None, ROLE_TOKEN
        try:
            role = role_of(_UserRoles(self.github, token, repo), session.login)
        except RoleLookupFailed as e:
            return None, e.reason
        except Exception:  # never echo: the error may carry the token; and do not cache it
            return None, ROLE_UNAVAILABLE
        cache[repo] = {"role": role, "at": now}
        self.store.update_session(
            session.rec["id"], session.rec["expires_at"], session.rec["last_used_at"], cache
        )
        return role, (None if role else ROLE_NONE)

    def user_token(self, session: Session) -> str | None:
        """The user's GitHub token, decrypted. Never log it or put it in a response."""
        return self.keys.decrypt(session.rec["token_enc"])

    def policy_for(self, session: Session, repo: str, fresh: bool = True) -> Policy:
        """The project's `.tradvia.yml` read with the user's token (ADR 0004 defaults on 404).

        Raises PolicyError (invalid file) or PolicyUnreadable (401/403): callers fail closed.
        `fresh=False` reuses a successful read for POLICY_TTL seconds (display only).
        """
        now = self.now()
        key = (session.rec["id"], repo)
        hit = self._policies.get(key)
        if not fresh and hit and now - hit[0] < POLICY_TTL:
            return hit[1]
        token = self.user_token(session)
        if token is None:
            raise PolicyUnreadable(UNREADABLE_POLICY)
        policy = load_policy(_UserRoles(self.github, token, repo))
        # Roles and risk are the project's setting on Tradvia (ADR 0018), per project. A store
        # that cannot be read raises, and every caller fails closed.
        row = self.store.get_setting(self.store.ensure_project(repo), POLICY_SETTING)
        policy = with_policy_setting(policy, row["value"] if row else None)
        if len(self._policies) > 1000:
            self._policies.clear()
        self._policies[key] = (now, policy)
        return policy

    def forget_policies(self, repo: str) -> None:
        """Drop cached policies of `repo` (its settings changed), for every session."""
        for key in [k for k in self._policies if k[1] == repo]:
            del self._policies[key]

    def can(self, session: Session, repo: str, interaction: str, fresh: bool = True) -> Access:
        """Role, policy and whether the role meets the minimum for `interaction`."""
        role, reason = self.role_status(session, repo)
        if role is None:
            return Access(None, None, False, reason)
        policy = self.policy_for(session, repo, fresh)
        return Access(role, policy, policy.allows(interaction, role))
