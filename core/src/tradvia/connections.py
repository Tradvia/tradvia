# SPDX-License-Identifier: FSL-1.1-ALv2
"""Project connections (ADR 0009): providers, models and secrets configured in Tradvia.

Each flow role (planner, executor, reviewer, orchestrator) uses a provider and a model.
`.tradvia.yml` gives the default, the Tradvia screen overrides it per project, and every
value shows where it came from. Provider secrets are stored encrypted (a key derived from
TRADVIA_SECRET_KEY), are never returned (only "configured at ... by ..."), and every change
is audited without the value. Delivery to the executor: a GitHub Actions repository secret
written at configuration time (sealed box, as GitHub requires), or an environment variable of
the run on a self-hosted runner.

This module does not log. Errors carry no secret and go through `redact`.
Everything here is blocking; `api.py` calls it through a thread pool.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from .actions import GitHubError
from .store import Store

FLOW_ROLES = ("planner", "executor", "reviewer", "orchestrator")
MAX_SECRET_LENGTH = 4096


class ConnectionInputError(ValueError):
    """Invalid input (400). The message is safe to show: it never carries a secret."""


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    kind: str  # "ai" | "cli" | "repository"
    status: str  # Shipped | In progress | Partial | Planned (CLAUDE.md, rule 5)
    env: str | None  # variable the executor reads the secret from
    models: tuple[str, ...]  # allowed models; empty: not assignable to a flow role
    note: str


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "anthropic", "Anthropic", "ai", "Partial", "ANTHROPIC_API_KEY",
        ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
        "The planner calls it today; the other roles still read the choice only.",
    ),
    Provider(
        "claude-code", "Claude Code (OAuth token)", "cli", "Shipped", "CLAUDE_CODE_OAUTH_TOKEN",
        (), "Runs the executor on the subscription (claude.yml).",
    ),
    Provider(
        "gemini", "Google Gemini", "ai", "In progress", "GEMINI_API_KEY",
        ("gemini-2.5-pro", "gemini-2.5-flash"),
        "Secret and model are stored and delivered; no executor uses them yet.",
    ),
    Provider(
        "openai", "OpenAI", "ai", "In progress", "OPENAI_API_KEY",
        ("gpt-5", "gpt-5-mini"),
        "Secret and model are stored and delivered; no executor uses them yet.",
    ),
    Provider("github", "GitHub", "repository", "Shipped", None, (), "Connected by the GitHub App."),
    Provider("gitlab", "GitLab", "repository", "Planned", None, (), "Planned, issue #60."),
)
BY_KEY = {p.key: p for p in PROVIDERS}
SECRET_PROVIDERS = tuple(p.key for p in PROVIDERS if p.env)

SOURCE_YML = "tradvia.yml"
SOURCE_TRADVIA = "tradvia"
SOURCE_UNSET = "unset"


# --- pure validation ------------------------------------------------------------------


def validate_role(role: object) -> str:
    if not isinstance(role, str) or role not in FLOW_ROLES:
        raise ConnectionInputError(f"unknown role (use one of {', '.join(FLOW_ROLES)})")
    return role


def validate_secret_provider(provider: object) -> str:
    if not isinstance(provider, str) or provider not in SECRET_PROVIDERS:
        raise ConnectionInputError(f"unknown provider (use one of {', '.join(SECRET_PROVIDERS)})")
    return provider


def validate_assignment(role: object, provider: object, model: object) -> tuple[str, str, str]:
    """`(role, provider, model)` when valid; ConnectionInputError otherwise."""
    role = validate_role(role)
    spec = BY_KEY.get(provider) if isinstance(provider, str) else None
    if spec is None or not spec.models:
        names = ", ".join(p.key for p in PROVIDERS if p.models)
        raise ConnectionInputError(f"unknown provider (use one of {names})")
    if not isinstance(model, str) or model not in spec.models:
        raise ConnectionInputError(f"unknown model for {spec.key} (use one of {', '.join(spec.models)})")
    return role, spec.key, model


def validate_secret_value(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectionInputError("the secret must be a non-empty text")
    if len(value) > MAX_SECRET_LENGTH or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConnectionInputError("the secret has an invalid format")
    return value.strip()


# --- precedence -------------------------------------------------------------------------


def resolve(
    yml_defaults: Mapping[str, Mapping[str, str]] | None,
    db_rows: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Effective provider and model per role, with where each came from.

    `.tradvia.yml` is the default; a row configured in Tradvia overrides it as a whole
    (provider and model together, so a model never meets a provider it does not belong to).
    """
    yml_defaults, db_rows = yml_defaults or {}, db_rows or {}
    out: dict[str, dict[str, Any]] = {}
    for role in FLOW_ROLES:
        if role in db_rows:
            row = db_rows[role]
            out[role] = {
                "provider": row["provider"], "model": row["model"], "source": SOURCE_TRADVIA,
                "updated_by": row.get("updated_by"), "updated_at": row.get("updated_at"),
            }
        elif role in yml_defaults:
            out[role] = {
                "provider": yml_defaults[role]["provider"], "model": yml_defaults[role]["model"],
                "source": SOURCE_YML, "updated_by": None, "updated_at": None,
            }
        else:
            out[role] = {
                "provider": None, "model": None, "source": SOURCE_UNSET,
                "updated_by": None, "updated_at": None,
            }
    return out


# --- masking ----------------------------------------------------------------------------

MASK = "***"
_SECRET_SHAPES = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})"
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Hide known secret values and anything shaped like a provider key or token."""
    for value in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(value, MASK)
    return _SECRET_SHAPES.sub(MASK, text)


class SecretEnv(dict):
    """Environment for one execution. Pass it to the subprocess only: it never prints."""

    def __repr__(self) -> str:
        return f"SecretEnv({sorted(self)})"

    __str__ = __repr__


# --- delivery to GitHub Actions ------------------------------------------------------------


class RepoSecrets(Protocol):
    """What delivery needs from GitHub. Tests inject a fake; `HttpGitHub` implements it."""

    def get_actions_public_key(self, token: str, repo: str) -> tuple[str, str]:
        """`(key_id, base64 public key)` of the repository."""

    def put_actions_secret(
        self, token: str, repo: str, name: str, encrypted_value: str, key_id: str
    ) -> None: ...

    def delete_actions_secret(self, token: str, repo: str, name: str) -> None: ...


def seal(public_key_b64: str, value: str) -> str:
    """libsodium sealed box (X25519 + XSalsa20-Poly1305), base64, as GitHub requires."""
    from nacl.public import PublicKey, SealedBox

    box = SealedBox(PublicKey(base64.b64decode(public_key_b64)))
    return base64.b64encode(box.encrypt(value.encode())).decode()


# --- service ----------------------------------------------------------------------------------


class Cipher(Protocol):
    def encrypt_secret(self, plain: str) -> str: ...
    def decrypt_secret(self, cipher: str) -> str | None: ...


class Connections:
    def __init__(
        self,
        store: Store,
        cipher: Cipher,
        repo_secrets: RepoSecrets | None,
        clock: Callable[[], float],
    ) -> None:
        self.store, self.cipher, self.repo_secrets, self.clock = store, cipher, repo_secrets, clock

    def _audit(self, pid: int, actor: str, action: str, result: str, detail: str) -> None:
        try:
            self.store.append_audit(pid, {
                "actor_login": actor, "action": action, "issue_number": None, "result": result,
                "detail": detail[:200], "idempotency_key": None, "created_at": self.clock(),
            })
        except Exception:  # the change already happened: never turn that into an error
            pass

    # reading: never the value
    def state(
        self, pid: int, yml_defaults: Mapping[str, Mapping[str, str]] | None
    ) -> dict[str, Any]:
        secrets = self.store.list_connection_secrets(pid)
        roles = resolve(yml_defaults, self.store.list_connection_roles(pid))
        providers = []
        for p in PROVIDERS:
            entry: dict[str, Any] = {
                "key": p.key, "name": p.name, "kind": p.kind, "status": p.status,
                "models": list(p.models), "note": p.note,
            }
            if p.env:
                s = secrets.get(p.key)
                entry["secret"] = (
                    {"configured": True, "set_at": s["set_at"], "set_by": s["set_by"]}
                    if s else {"configured": False}
                )
            providers.append(entry)
        return {"roles": roles, "providers": providers}

    # writing
    def set_role(self, pid: int, actor: str, role: object, provider: object, model: object) -> None:
        role, provider, model = validate_assignment(role, provider, model)
        self.store.put_connection_role(pid, role, provider, model, actor, self.clock())
        self._audit(pid, actor, "set_connection", "ok", f"role={role} provider={provider} model={model}")

    def set_secret(
        self, pid: int, actor: str, repo: str, token: str | None, provider: object, value: object
    ) -> dict[str, Any]:
        """Stores the secret encrypted, then delivers it to GitHub Actions.

        Returns `{"replaced": bool, "github_actions": "ok" | "failed" | "skipped"}`.
        """
        provider = validate_secret_provider(provider)
        plain = validate_secret_value(value)
        replaced = self.store.put_connection_secret(
            pid, provider, self.cipher.encrypt_secret(plain), actor, self.clock()
        )
        action = "secret_replace" if replaced else "secret_set"
        delivery = self._deliver(repo, token, provider, plain)
        self._audit(pid, actor, action, "ok", f"provider={provider} github_actions={delivery}")
        return {"replaced": replaced, "github_actions": delivery}

    def _deliver(self, repo: str, token: str | None, provider: str, plain: str) -> str:
        if self.repo_secrets is None or not token:
            return "skipped"
        try:
            key_id, key = self.repo_secrets.get_actions_public_key(token, repo)
            self.repo_secrets.put_actions_secret(
                token, repo, BY_KEY[provider].env or "", seal(key, plain), key_id
            )
        except Exception:  # never echo: the message may carry the request
            return "failed"
        return "ok"

    def delete_secret(
        self, pid: int, actor: str, repo: str, token: str | None, provider: object
    ) -> dict[str, Any]:
        provider = validate_secret_provider(provider)
        removed = self.store.delete_connection_secret(pid, provider)
        delivery = "skipped"
        if self.repo_secrets is not None and token:
            try:
                self.repo_secrets.delete_actions_secret(token, repo, BY_KEY[provider].env or "")
                delivery = "ok"
            except GitHubError as e:
                delivery = "ok" if e.status == 404 else "failed"
            except Exception:
                delivery = "failed"
        self._audit(pid, actor, "secret_remove", "ok",
                    f"provider={provider} existed={removed} github_actions={delivery}")
        return {"removed": removed, "github_actions": delivery}

    # self-hosted runner: environment of one execution
    def runner_env(self, pid: int) -> SecretEnv:
        env = SecretEnv()
        for provider in self.store.list_connection_secrets(pid):
            cipher = self.store.get_connection_secret(pid, provider)
            plain = self.cipher.decrypt_secret(cipher) if cipher else None
            spec = BY_KEY.get(provider)
            if plain is not None and spec and spec.env:
                env[spec.env] = plain
        return env
