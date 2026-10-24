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
from .policy import Policy, load_policy, role_from_github, role_of
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
        r = self._get(token, f"/repos/{repo}/collaborators/{login}/permission")
        if r.status_code != 200:
            return None, None
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

    def add_label(self, token: str, repo: str, number: int, label: str) -> None:
        self._call(token, "POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": [label]})

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

    # roles
    def role_of(self, session: Session, repo: str) -> str | None:
        """Role of the signed-in user in `repo`, cached for ROLE_TTL per session."""
        now = self.now()
        cache = session.rec["role_cache"]
        hit = cache.get(repo)
        if hit and now - hit["at"] < ROLE_TTL:
            return hit["role"]
        token = self.keys.decrypt(session.rec["token_enc"])
        if token is None:
            return None
        try:
            role = role_of(_UserRoles(self.github, token, repo), session.login)
        except Exception:  # GitHub down or token revoked: no role, and do not cache it
            return None
        cache[repo] = {"role": role, "at": now}
        self.store.update_session(
            session.rec["id"], session.rec["expires_at"], session.rec["last_used_at"], cache
        )
        return role

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
        if len(self._policies) > 1000:
            self._policies.clear()
        self._policies[key] = (now, policy)
        return policy

    def can(self, session: Session, repo: str, interaction: str, fresh: bool = True) -> Access:
        """Role, policy and whether the role meets the minimum for `interaction`."""
        role = self.role_of(session, repo)
        if role is None:
            return Access(None, None, False)
        policy = self.policy_for(session, repo, fresh)
        return Access(role, policy, policy.allows(interaction, role))
