# SPDX-License-Identifier: FSL-1.1-ALv2
"""Proactive alerts: a stuck deploy queue, a card whose cost is far above the project's median,
and a run that stopped reporting. Each check is a pure function of data and `now`.

An alert goes out once per occurrence, with the link to the card, by Discord and Web Push. The
message carries the kind, the project and the link only: no title or text from GitHub. Slack and
outbound webhooks are out of scope (a separate decision).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import push
from .actions import _seconds_since, deploy_view, mark_waits, wait_alert_minutes
from .integrations import Chat
from .pending import ALERT_KINDS, COST_OUTLIER, DEPLOY_STUCK, RUN_DEAD, issue_of_branch
from .store import Store

DEFAULT_COST_FACTOR = 3.0
MIN_COST_CARDS = 5  # fewer cards than this have no meaningful median
DEFAULT_RUN_STALE_MINUTES = 30
DEFAULT_INTERVAL_S = 300
ACTIVE_STATUSES = ("in_progress", "queued")

LABELS = {
    DEPLOY_STUCK: "Deploy parado aguardando aprovação",
    COST_OUTLIER: "Custo muito acima da mediana do projeto",
    RUN_DEAD: "Run sem atualização",
}


@dataclass(frozen=True)
class Alert:
    """One occurrence. `key` is stable while it lasts; `number` is 0 when there is no card."""

    kind: str
    project: str
    number: int
    key: str
    link: str


def _positive(raw: str | None, default: float, cast: Callable[[str], float]) -> float:
    """`raw` as a positive number; anything else keeps `default`, so a typo cannot switch it off."""
    try:
        value = cast(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value > 0 else default


def cost_factor(raw: str | None) -> float:
    """Read from TRADVIA_COST_ALERT_FACTOR."""
    return _positive(raw, DEFAULT_COST_FACTOR, float)


def run_stale_minutes(raw: str | None) -> int:
    """Read from TRADVIA_RUN_STALE_MINUTES."""
    return int(_positive(raw, DEFAULT_RUN_STALE_MINUTES, int))


def alert_interval(raw: str | None) -> int:
    """Seconds between checks, read from TRADVIA_ALERT_INTERVAL_S."""
    return int(_positive(raw, DEFAULT_INTERVAL_S, int))


def card_link(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def stuck_deploys(
    runs: list[dict[str, Any]], pending: dict[int, list[dict[str, Any]]], now: float,
    repo: str, project: str, alert_minutes: int | None = None,
) -> list[Alert]:
    """Deploys waiting for approval past the limit (replaced ones never alert)."""
    minutes = alert_minutes or wait_alert_minutes(None)
    out = []
    for d in mark_waits(deploy_view(runs, pending), now, minutes):
        if d["replaced"] or not d["long_wait"]:
            continue
        number = issue_of_branch(d.get("branch")) or 0
        link = card_link(repo, number) if number else (d.get("html_url") or "")
        out.append(Alert(DEPLOY_STUCK, project, number, f"{DEPLOY_STUCK}:{d['run_id']}", link))
    return out


def cost_outliers(
    costs: list[dict[str, Any]], repo: str, project: str,
    factor: float = DEFAULT_COST_FACTOR, min_cards: int = MIN_COST_CARDS,
) -> list[Alert]:
    """Cards whose summed cost is above `factor` times the project's median card cost."""
    per_card: dict[int, float] = {}
    for c in costs:
        per_card[c["issue_number"]] = per_card.get(c["issue_number"], 0.0) + float(c["cost_usd"])
    if len(per_card) < min_cards:
        return []
    median = statistics.median(per_card.values())
    if median <= 0:
        return []
    return [
        Alert(COST_OUTLIER, project, n, f"{COST_OUTLIER}:{n}", card_link(repo, n))
        for n, total in sorted(per_card.items())
        if total > factor * median
    ]


def dead_runs(
    runs: list[dict[str, Any]], now: float, repo: str, project: str,
    stale_minutes: int = DEFAULT_RUN_STALE_MINUTES,
) -> list[Alert]:
    """Runs still in progress whose last update is older than `stale_minutes`."""
    out = []
    for r in runs:
        if r.get("status") not in ACTIVE_STATUSES:
            continue
        idle = _seconds_since(r.get("updated_at"), now)
        if idle is None or idle <= stale_minutes * 60:
            continue
        number = r.get("issue_number") or 0
        link = card_link(repo, number) if number else (r.get("html_url") or "")
        key = f"{RUN_DEAD}:{r['id']}:{r.get('run_attempt') or 1}"
        out.append(Alert(RUN_DEAD, project, number, key, link))
    return out


class AlertLedger:
    """Remembers which occurrences were already announced, in memory only.

    A key leaves the ledger when its condition stops holding (deploy approved, run updated), so
    the same occurrence coming back later is a new one and alerts again. Nothing is persisted:
    a process restart can resend alerts that are still active. An occurrence counts as announced
    when `fresh` returns it, whether or not a channel then delivers it.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def fresh(
        self, alerts: Iterable[Alert], project: str, kinds: Iterable[str] = ALERT_KINDS
    ) -> list[Alert]:
        """The alerts of `project` not announced yet. `alerts` is everything active now for
        `kinds`; remembered keys of those kinds that are not in it are forgotten."""
        alerts = list(alerts)
        kinds = tuple(kinds)
        active = {(project, a.key) for a in alerts}
        self._seen = {
            s for s in self._seen
            if s in active or s[0] != project or not s[1].startswith(tuple(k + ":" for k in kinds))
        }
        out = []
        for a in alerts:
            if (project, a.key) not in self._seen:
                self._seen.add((project, a.key))
                out.append(a)
        return out


def message(alert: Alert) -> str:
    return f"{LABELS[alert.kind]} · {alert.project} · {alert.link}"


def deliver(
    alerts: list[Alert], push_sender: push.Sender | None, discord: Chat | None, db: Store | None,
    project_id: int, slug: str, can_answer: Callable[[str, str], bool],
) -> None:
    """Send each alert to Discord and to the Web Push subscribers who may answer it.

    A failure in one channel never stops the other, nor the next alert, and is not logged: the
    push endpoint must not reach a log (see `push.notify`).
    """
    for a in alerts:
        if discord is not None:
            try:
                discord.post(message(a))
            except Exception:
                pass
        if push_sender is not None and db is not None:
            try:
                push.notify(db, push_sender, project_id, slug, {a.number: a.kind}, can_answer)
            except Exception:
                pass
