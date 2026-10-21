# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Access policy (ADR 0004, phase 1): who may act through Tradvia.

The policy lives in `.tradvia.yml` and is always read from the default branch.
It fails closed: unknown keys or invalid roles raise PolicyError.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Protocol

import yaml

POLICY_FILE = ".tradvia.yml"
ROLES = ("read", "triage", "write", "maintain", "admin")


class PolicyError(ValueError):
    """The policy file is invalid; the interaction must be refused."""


def role_rank(role: str) -> int:
    return ROLES.index(role)


@dataclass(frozen=True)
class Interactions:
    start_card: str = "triage"
    approve_plan: str = "write"
    mention_assistant: str = "write"
    live_takeover: str = "maintain"
    budget_control: str = "admin"


@dataclass(frozen=True)
class Policy:
    view: str = "inherit"
    interactions: Interactions = Interactions()

    def allows(self, interaction: str, role: str | None) -> bool:
        """True when `role` meets the minimum role of `interaction`. No role never does."""
        if role is None or role not in ROLES:
            return False
        return role_rank(role) >= role_rank(getattr(self.interactions, interaction))


class RoleSource(Protocol):
    base: str

    def get_file(self, path: str, ref: str) -> str | None: ...
    def get_collaborator_role(self, login: str) -> str | None: ...


def _role(value: object, where: str) -> str:
    if not isinstance(value, str) or value not in ROLES:
        raise PolicyError(f"{where}: invalid role {value!r} (use one of {', '.join(ROLES)})")
    return value


def _section(data: dict, key: str, allowed: tuple[str, ...]) -> dict:
    section = data.get(key) or {}
    if not isinstance(section, dict):
        raise PolicyError(f"{key!r} must be a mapping")
    unknown = sorted(str(k) for k in section if k not in allowed)
    if unknown:
        raise PolicyError(f"unknown key(s) under {key!r}: {', '.join(unknown)}")
    return section


def parse_policy(text: str | None) -> Policy:
    try:
        data = yaml.safe_load(text) if text else None
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid YAML: {e}") from e
    if data is None:
        return Policy()
    if not isinstance(data, dict):
        raise PolicyError("top level must be a mapping")
    unknown = sorted(str(k) for k in data if k not in ("access", "interactions"))
    if unknown:
        raise PolicyError(f"unknown top-level key(s): {', '.join(unknown)}")

    view = "inherit"
    access = _section(data, "access", ("view",))
    if "view" in access:
        view = access["view"]
        if view != "inherit":
            view = _role(view, "access.view")

    names = tuple(f.name for f in fields(Interactions))
    raw = _section(data, "interactions", names)
    interactions = Interactions(**{k: _role(v, f"interactions.{k}") for k, v in raw.items()})
    return Policy(view=view, interactions=interactions)


def role_from_github(permission: str | None, role_name: str | None = None) -> str | None:
    """Maps the collaborator-permission answer to a policy role.

    `role_name` is the granular role (triage, maintain...); `permission` is the legacy
    admin/write/read/none. Anything unknown, including `none`, is no role at all.
    """
    if role_name in ROLES:
        return role_name
    return permission if permission in ROLES else None


def role_of(github: RoleSource, login: str | None) -> str | None:
    """GitHub role of a person, or None (not a collaborator). Bots never count."""
    if not login or login.endswith("[bot]"):
        return None
    return github.get_collaborator_role(login)


def load_policy(github: RoleSource) -> Policy:
    """Read .tradvia.yml from the default branch only (never a PR branch or the working tree)."""
    return parse_policy(github.get_file(POLICY_FILE, github.base))
