# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Access policy (ADR 0004, phase 1): who may act through Tradvia.

The policy lives in `.tradvia.yml` and is always read from the default branch.
It fails closed: unknown keys or invalid roles raise PolicyError.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Protocol

import yaml

from .budget import Budget, parse_budget
from .connections import ConnectionInputError, validate_assignment
from .risk import DESTRUCTIVE_EXTERNAL, RISK_LEVELS

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
    merge_pr: str = "maintain"
    update_branch: str = "maintain"
    # Also approving, rejecting and cancelling deploys, and the post-deploy operation.
    approve_deploy: str = "maintain"
    # Providers, models and secrets of the project (ADR 0009). Locked to admin: .tradvia.yml
    # may not lower it, because a secret is behind it.
    manage_connections: str = "admin"


@dataclass(frozen=True)
class Stagnation:
    repeat_limit: int = 3


@dataclass(frozen=True)
class RiskPolicy:
    """Per risk level: 'free' (no approval) or the minimum role that must approve."""

    read: str = "free"
    reversible_write: str = "free"
    destructive_external: str = "maintain"


@dataclass(frozen=True)
class Policy:
    view: str = "inherit"
    interactions: Interactions = Interactions()
    stagnation: Stagnation = Stagnation()
    risk: RiskPolicy = RiskPolicy()
    budget: Budget = Budget()
    # Default provider and model per flow role: `{role: {"provider": ..., "model": ...}}`.
    providers: dict[str, dict[str, str]] = field(default_factory=dict)

    def risk_requirement(self, level: str) -> str | None:
        """None when `level` runs free, else the minimum approving role. Unknown levels fail closed."""
        if level not in RISK_LEVELS:
            level = DESTRUCTIVE_EXTERNAL
        value = getattr(self.risk, level)
        return None if value == "free" else value

    def allows_risk(self, level: str, role: str | None) -> bool:
        """True when `level` is free or `role` meets its minimum approving role."""
        required = self.risk_requirement(level)
        if required is None:
            return True
        return role is not None and role in ROLES and role_rank(role) >= role_rank(required)

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
    unknown = sorted(str(k) for k in data
                     if k not in ("access", "interactions", "stagnation", "risk", "budget",
                                  "providers"))
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
    if interactions.manage_connections != "admin":
        raise PolicyError("interactions.manage_connections is fixed to admin and cannot be lowered")

    stagnation = Stagnation()
    raw_stagnation = _section(data, "stagnation", ("repeat_limit",))
    if "repeat_limit" in raw_stagnation:
        limit = raw_stagnation["repeat_limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
            raise PolicyError(f"stagnation.repeat_limit must be an integer >= 2, got {limit!r}")
        stagnation = Stagnation(repeat_limit=limit)

    raw_risk = _section(data, "risk", RISK_LEVELS)
    risk = RiskPolicy(**{k: v if v == "free" else _role(v, f"risk.{k}")
                         for k, v in raw_risk.items()})
    # Never raises: invalid budget values fall back to the safe defaults.
    return Policy(view=view, interactions=interactions, stagnation=stagnation, risk=risk,
                  budget=parse_budget(data.get("budget")), providers=_providers(data))


def _providers(data: dict) -> dict[str, dict[str, str]]:
    """The optional `providers` section: `role: {provider, model}`, validated like the rest."""
    section = data.get("providers") or {}
    if not isinstance(section, dict):
        raise PolicyError("'providers' must be a mapping")
    out: dict[str, dict[str, str]] = {}
    for role, value in section.items():
        if not isinstance(value, dict) or sorted(map(str, value)) != ["model", "provider"]:
            raise PolicyError(f"providers.{role}: use exactly 'provider' and 'model'")
        try:
            _, provider, model = validate_assignment(role, value["provider"], value["model"])
        except ConnectionInputError as e:
            raise PolicyError(f"providers.{role}: {e}") from None
        out[role] = {"provider": provider, "model": model}
    return out


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
