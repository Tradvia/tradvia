# SPDX-License-Identifier: FSL-1.1-ALv2
"""Subscription mode of the orchestrator chat (ADR 0012).

A project may let its chat answer on a Claude subscription (Claude Code with
`CLAUDE_CODE_OAUTH_TOKEN`) instead of a metered API key. Anthropic's terms assume ordinary,
individual use of a subscription and do not let a product store the token or use one
person's subscription to serve others. So the mode is:

- the token owner's alone (`CLAUDE_CODE_OAUTH_TOKEN_OWNER`, a GitHub login);
- paced on purpose: at least `MIN_INTERVAL_S` between answers and at most `MAX_PER_HOUR`
  in any hour, counted over every project the server hosts (it is one subscription). A
  project may be stricter in `.tradvia.yml`, never faster;
- never stored by Tradvia: the token lives in the owner's own secrets.

Everything here is pure; `api.py` keeps the clock, the lock and the store.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MIN_INTERVAL_S = 60
MAX_PER_HOUR = 20
MAX_INTERVAL_S = 3600
WINDOW_S = 3600
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
OWNER_ENV = "CLAUDE_CODE_OAUTH_TOKEN_OWNER"
# Audit action of one subscription answer, dated when the answer may start (a reservation:
# a later message is scheduled after it even while this one still waits).
AUDIT_ACTION = "subscription_answer"
PROVIDER = "claude-code"


@dataclass(frozen=True)
class Pace:
    min_interval_s: int = MIN_INTERVAL_S
    max_per_hour: int = MAX_PER_HOUR

    def public(self) -> dict[str, int]:
        return {"min_interval_seconds": self.min_interval_s, "max_per_hour": self.max_per_hour}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def parse_pace(section: object) -> Pace:
    """The `subscription` section of `.tradvia.yml`. Never raises, never looser than the ceiling.

    `min_interval_seconds` below 60 becomes 60 (above an hour, an hour); `max_per_hour` above
    20 becomes 20. Anything invalid keeps the ceiling, so a typo can only make the pace slower.
    """
    if not isinstance(section, dict):
        return Pace()
    interval = _number(section.get("min_interval_seconds"))
    per_hour = _number(section.get("max_per_hour"))
    min_interval = MIN_INTERVAL_S if interval is None else int(min(max(interval, MIN_INTERVAL_S),
                                                                   MAX_INTERVAL_S))
    max_hour = MAX_PER_HOUR if per_hour is None or per_hour < 1 else int(min(per_hour, MAX_PER_HOUR))
    return Pace(min_interval_s=min_interval, max_per_hour=max_hour)


def server_owner(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    owner = (env.get(OWNER_ENV) or "").strip().lstrip("@")
    return owner or None


def server_token(env: Mapping[str, str] | None = None) -> bool:
    """Whether the server has a subscription token. Only presence: the value is never read."""
    env = os.environ if env is None else env
    return bool((env.get(TOKEN_ENV) or "").strip())


def is_owner(login: str | None, owner: str | None) -> bool:
    return bool(login and owner and login.casefold() == owner.casefold())


def schedule(now: float, starts: Iterable[float], pace: Pace) -> tuple[float, str | None]:
    """`(start, reason)`: the earliest start at or after `now` that keeps the pace.

    `starts` are the start times already taken, in every project, possibly in the future (a
    message still waiting for its turn). The answer starts at least `min_interval_s` after the
    latest of them, and only when fewer than `max_per_hour` fall in the hour before it.
    `reason` is None when it can start now, else "interval" or "hourly": the rule that held it.
    """
    taken = sorted(starts)
    at, reason = now, None
    if taken and taken[-1] + pace.min_interval_s > at:
        at, reason = taken[-1] + pace.min_interval_s, "interval"
    while True:
        window = [t for t in taken if at - WINDOW_S < t <= at]
        if len(window) < pace.max_per_hour:
            return at, reason
        # Wait until enough of them leave the hour: then one fewer than the limit remains.
        at, reason = window[len(window) - pace.max_per_hour] + WINDOW_S, "hourly"


def used(now: float, starts: Iterable[float]) -> int:
    """Answers that started within the last hour, as of `now` (a waiting one does not count)."""
    return sum(1 for t in starts if now - WINDOW_S < t <= now)


def notice(wait_s: int, reason: str, pace: Pace, owner: str) -> dict[str, Any]:
    """The chat message that explains a deliberate wait. `refs` carries the numbers for the
    dashboard, which says it in the reader's language; `text` is the plain fallback."""
    why = (f"at most {pace.max_per_hour} answers an hour" if reason == "hourly"
           else f"at least {pace.min_interval_s} s between answers")
    return {
        "kind": "pace",
        "text": (f"Waiting {wait_s} s before answering: this project's chat uses the subscription "
                 f"mode of @{owner}, which keeps {why} on purpose. An API key in Connections "
                 "answers without this wait."),
        "refs": [{"type": "pace", "reason": reason, "seconds": wait_s, "owner": owner,
                  **pace.public()}],
    }
