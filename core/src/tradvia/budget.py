# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Execution budgets per card (ADR 0006): a warning at 80%, a hard stop at 100%.

The limits live in the `budget` section of `.tradvia.yml`. Parsing never raises: an
invalid value falls back to the safe default of that field, so a typo can never remove
or loosen a limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

WARN_AT = 0.8

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


def warning_notice(usage: dict | None, budget: Budget) -> str:
    return WARNING_NOTICE.format(pct=usage_ratio(usage, budget) * 100)


def format_usage(usage: dict | None, budget: Budget) -> str:
    u = usage or {}
    return (f"Budget used: {u.get('steps') or 0}/{budget.max_steps} steps · "
            f"{(u.get('minutes') or 0):.1f}/{budget.max_minutes:g} min · "
            f"${(u.get('usd') or 0):.4f}/${budget.max_usd:g}")
