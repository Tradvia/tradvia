# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Execution budgets per card (ADR 0006): a warning at 80%, a hard stop at 100%.

The limits are a setting of the project, made on Tradvia's Settings screen and stored with the
project (ADR 0018), never in `.tradvia.yml`. Reading a stored value never raises: an invalid
value falls back to the safe default of that field, so nothing can remove or loosen a limit.
Writing goes through `validate_settings`, which refuses invalid input instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

WARN_AT = 0.8
_ALL_PULLS = 100_000

WARNING_NOTICE = (
    "SYSTEM NOTICE: this card has used {pct:.0f}% of its execution budget. Do not start new "
    "lines of work. Either synthesize what you already know into a final answer now, or "
    "ask the human for help stating exactly what is blocking you."
)


@dataclass(frozen=True)
class Budget:
    max_minutes: float = 30.0
    max_steps: int = 40
    max_usd: float = 5.0


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value) or value <= 0:
        return None
    return float(value)


def parse_budget(section: object) -> Budget:
    """Build a Budget from the raw `budget` mapping; anything invalid keeps the default."""
    if not isinstance(section, dict):
        return Budget()
    values: dict[str, float | int] = {}
    for f in fields(Budget):
        n = _positive(section.get(f.name))
        if n is None:
            continue
        if f.name == "max_steps":
            if n != int(n):
                continue
            values[f.name] = int(n)
        else:
            values[f.name] = n
    return Budget(**values)


@dataclass(frozen=True)
class MonthlyBudget:
    """Project-wide monthly ceiling. None means no ceiling; there is no numeric default."""

    monthly_usd: float | None = None


def parse_monthly_usd(section: object) -> float | None:
    """`budget.monthly_usd`; anything invalid is None (no ceiling), never a hidden one."""
    if not isinstance(section, dict):
        return None
    return _positive(section.get("monthly_usd"))


def parse_monthly_budget(section: object) -> MonthlyBudget:
    return MonthlyBudget(monthly_usd=parse_monthly_usd(section))


# --- the project's budget setting (ADR 0018) ------------------------------------------------

SETTINGS_KEY = "budget"
# Upper bounds for what the Settings screen accepts; reading is lenient, writing is strict.
LIMITS = {"max_minutes": 1440.0, "max_steps": 10_000, "max_usd": 10_000.0, "monthly_usd": 1_000_000.0}


def settings_budget(value: object) -> tuple[Budget, float | None]:
    """`(per-card Budget, monthly ceiling or None)` from the stored setting; never raises."""
    return parse_budget(value), parse_monthly_usd(value)


def settings_view(value: object) -> dict[str, Any]:
    """What the Settings screen shows: the values in force, defaults filled in."""
    budget, monthly = settings_budget(value)
    return {"max_minutes": budget.max_minutes, "max_steps": budget.max_steps,
            "max_usd": budget.max_usd, "monthly_usd": monthly,
            "defaults": {"max_minutes": Budget().max_minutes, "max_steps": Budget().max_steps,
                         "max_usd": Budget().max_usd, "monthly_usd": None}}


def validate_settings(data: object) -> dict[str, Any]:
    """The setting to store, or ValueError with a message safe to show. `monthly_usd` null (or
    absent) means no ceiling; each per-card limit must be positive and within LIMITS."""
    if not isinstance(data, dict):
        raise ValueError("send an object with max_minutes, max_steps, max_usd and monthly_usd")
    unknown = sorted(set(data) - set(LIMITS))
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for name, top in LIMITS.items():
        value = data.get(name)
        if value is None:
            if name == "monthly_usd":
                out[name] = None
                continue
            value = getattr(Budget(), name)
        n = _positive(value)
        if n is None or n > top:
            raise ValueError(f"{name} must be a number greater than 0 and at most {top:g}")
        if name == "max_steps":
            if n != int(n):
                raise ValueError("max_steps must be a whole number")
            n = int(n)
        out[name] = n
    return out


def _parse_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _amount(value: object) -> float:
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(n) or math.isinf(n) else n


def month_spend(plans: list[dict], pulls: list[dict], now: datetime) -> float:
    """USD spent in the UTC month of `now`: plan comments plus executor cost of PRs.

    Same rule as docs/dashboard-kpis.md: the PR `cost_so_far_usd` already includes the plans,
    so it is never added. A row whose date cannot be read counts as spent, so a bad timestamp
    can only tighten the ceiling.
    """
    ref = _parse_utc(now) or datetime.now(timezone.utc)
    total = 0.0
    for row in [*plans, *pulls]:
        if row.get("cost_usd") is None:
            continue
        at = _parse_utc(row.get("created_at"))
        if at is None or (at.year, at.month) == (ref.year, ref.month):
            total += max(_amount(row["cost_usd"]), 0.0)
    return total


def store_month_spend(store: Any, project_id: int, now: datetime | None = None) -> float:
    """`month_spend` over everything the store holds for the project; raises if it cannot read."""
    plans = store.list_plan_costs(project_id)
    pulls = store.list_pulls(project_id, "all", _ALL_PULLS) or []
    return month_spend(plans, pulls, now or datetime.now(timezone.utc))


def monthly_check(spent: float, monthly_usd: float | None) -> str:
    """'ok', 'warn' (>= 80% of the ceiling) or 'stop' (>= 100%). No ceiling is always 'ok'."""
    if monthly_usd is None:
        return "ok"
    ratio = spent / monthly_usd
    if ratio >= 1.0:
        return "stop"
    return "warn" if ratio >= WARN_AT else "ok"


def format_monthly(spent: float, cap: float | None) -> str:
    return f"Monthly budget used: ${spent:.4f}/${cap:g}" if cap is not None \
        else f"Monthly spend: ${spent:.4f} (no ceiling)"


def usage_ratio(usage: dict | None, budget: Budget) -> float:
    """Highest consumed fraction across the three limits."""
    u = usage or {}
    return max(
        (u.get("minutes") or 0) / budget.max_minutes,
        (u.get("steps") or 0) / budget.max_steps,
        (u.get("usd") or 0) / budget.max_usd,
    )


def check(usage: dict | None, budget: Budget) -> str:
    """'ok', 'warn' (>= 80% of any limit) or 'stop' (>= 100%)."""
    ratio = usage_ratio(usage, budget)
    if ratio >= 1.0:
        return "stop"
    return "warn" if ratio >= WARN_AT else "ok"


def remaining(usage: dict | None, budget: Budget) -> dict[str, float]:
    """What is left of each limit of the card (steps, minutes, usd); never negative."""
    u = usage or {}
    return {"steps": max(budget.max_steps - (u.get("steps") or 0), 0),
            "minutes": max(budget.max_minutes - (u.get("minutes") or 0), 0.0),
            "usd": max(budget.max_usd - (u.get("usd") or 0), 0.0)}


def run_limits(usage: dict | None, budget: Budget, default_turns: int,
               default_timeout: int) -> tuple[int, int]:
    """`(max_turns, timeout_s)` for one run so it cannot go past the card's remaining balance.

    The graph counts 1 + tool_calls steps per run, hence one step stays for the run itself.
    `max_turns` is 0 when the balance does not allow a single turn; the caller stops the card.
    """
    left = remaining(usage, budget)
    turns = min(default_turns, int(left["steps"]) - 1)
    timeout = min(default_timeout, max(math.ceil(left["minutes"] * 60), 1))
    return max(turns, 0), timeout


# --- estimated price for providers that only report tokens ------------------------------------

# USD per million tokens: (input, output, cached input). An API-equivalent estimate, not a bill:
# subscriptions and discounts are not modelled. Update it when the provider changes its prices.
TOKEN_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5": (1.25, 10.0, 0.125),
    "gpt-5-codex": (1.25, 10.0, 0.125),
    "gpt-5-mini": (0.25, 2.0, 0.025),
    "default": (1.25, 10.0, 0.125),
}


def estimate_usd(tokens_in: int | None, tokens_out: int | None, tokens_cache: int | None,
                 model: str = "default") -> float:
    """API-equivalent USD for the tokens of a run, from `TOKEN_PRICES` (fallback row if unknown)."""
    price_in, price_out, price_cache = TOKEN_PRICES.get(model, TOKEN_PRICES["default"])
    return ((tokens_in or 0) * price_in + (tokens_out or 0) * price_out
            + (tokens_cache or 0) * price_cache) / 1_000_000


def warning_notice(usage: dict | None, budget: Budget) -> str:
    return WARNING_NOTICE.format(pct=usage_ratio(usage, budget) * 100)


def format_usage(usage: dict | None, budget: Budget) -> str:
    u = usage or {}
    return (f"Budget used: {u.get('steps') or 0}/{budget.max_steps} steps · "
            f"{(u.get('minutes') or 0):.1f}/{budget.max_minutes:g} min · "
            f"${(u.get('usd') or 0):.4f}/${budget.max_usd:g}")
