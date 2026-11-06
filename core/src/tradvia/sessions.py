# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Session drivers (ADR 0022, ADR 0024): a browser the agent drives and a person can take over.

`SessionDriver` is the interface, in the pattern of the coding agent adapters (ADR 0010): one
class per driver, one entry in `DRIVERS`. `CardSession` is what a card uses: it opens a session
only within the card's budget, records the takeover link on the card, adds the session's cost
to the card's usage, and closes the session when the budget reaches 100% (ADR 0006).

What a driver returns is split on purpose:

- `control_url` drives the browser (CDP). It is a secret for the agent's process only: it is
  never written to the card, a prompt or a log.
- the link a person opens is Tradvia's own (`takeover_link`), which needs a sign-in and the
  project's `live_takeover` role. A driver's raw viewer address is never put on the card,
  since whoever holds it controls the browser.

Only the fake driver exists today; the self-hosted browser of ADR 0024 is Planned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import quote, urlencode

from . import budget as budget_mod

SESSION_MARK = "<!-- tradvia:session -->"
DRIVERS = ("fake",)
PLANNED_DRIVERS = ("self-hosted-browser",)
CLOSE_REASONS = ("done", "budget", "error", "cancelled")


@dataclass
class Session:
    """An open session. `control_url` is secret (see the module docstring)."""

    id: str
    driver: str
    control_url: str
    started_at: float
    closed_at: float | None = None
    close_reason: str | None = None
    usd: float = 0.0
    usd_reported: bool = True

    def minutes(self, now: float) -> float:
        end = self.closed_at if self.closed_at is not None else now
        return max(end - self.started_at, 0.0) / 60.0

    def __repr__(self) -> str:  # never print the control URL
        return f"Session(id={self.id!r}, driver={self.driver!r}, closed={self.closed_at is not None})"


class SessionDriver(Protocol):
    name: str

    def open(self, card: str) -> Session:
        """Start a browser for `card` (`owner/repo#N`) and return its session."""

    def cost(self, session: Session, now: float) -> tuple[float, bool]:
        """(USD so far, whether the provider reports it). Unknown cost is (0.0, False)."""

    def close(self, session: Session) -> None:
        """Stop the browser. Closing twice is harmless."""


@dataclass
class FakeDriver:
    """Offline driver for tests: no browser, a fixed price per minute."""

    usd_per_minute: float = 0.0
    reported: bool = True
    clock: Callable[[], float] = time.time
    name: str = "fake"
    opened: list[Session] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    fail_open: bool = False

    def open(self, card: str) -> Session:
        if self.fail_open:
            raise RuntimeError("the fake browser did not start")
        sid = f"fake-{len(self.opened) + 1}"
        session = Session(id=sid, driver=self.name, control_url=f"ws://fake-browser/{sid}?token=secret-{sid}",
                          started_at=self.clock())
        self.opened.append(session)
        return session

    def cost(self, session: Session, now: float) -> tuple[float, bool]:
        if not self.reported:
            return 0.0, False
        return round(session.minutes(now) * self.usd_per_minute, 6), True

    def close(self, session: Session) -> None:
        if session.id not in self.closed:
            self.closed.append(session.id)


def make_driver(name: str, **kwargs) -> SessionDriver:
    """The driver for a catalog name. Planned drivers are named in the error, not faked."""
    if name == "fake":
        return FakeDriver(**kwargs)
    if name in PLANNED_DRIVERS:
        raise ValueError(f"Session driver {name!r} is Planned (ADR 0024), not available yet")
    raise ValueError(f"Unknown session driver {name!r}: use {', '.join(DRIVERS)}")


def takeover_link(domain: str, repo: str, number: int, session_id: str) -> str:
    """The panel address where a person with the `live_takeover` role takes the session over."""
    query = urlencode({"repo": repo, "card": number, "session": session_id})
    return f"https://{domain}/runs/?{query}"


class BudgetExceeded(RuntimeError):
    """The card's budget is used up: no session may open or stay open."""


@dataclass
class CardSession:
    """A card's browser session, kept within the card's budget.

    `comment` posts a comment on the card (the graph gives the GitHub comment call);
    `usage` returns the card's usage so far (`minutes`, `steps`, `usd`, as in budget.check).
    The session's cost is added to that usage's `usd`; its minutes are not added again,
    since they pass while the card itself runs and are already in its elapsed time.
    """

    driver: SessionDriver
    repo: str
    number: int
    budget: budget_mod.Budget
    usage: Callable[[], dict]
    comment: Callable[[str], None]
    domain: str
    clock: Callable[[], float] = time.time
    session: Session | None = None

    def _usage_with_session(self) -> dict:
        u = dict(self.usage() or {})
        if self.session is not None:
            usd, reported = self.driver.cost(self.session, self.clock())
            self.session.usd, self.session.usd_reported = usd, reported
            u["usd"] = (u.get("usd") or 0) + usd
        return u

    def open(self) -> Session:
        """Open the session and record the takeover link on the card. Refused at 100% of budget."""
        if self.session is not None and self.session.closed_at is None:
            return self.session
        if budget_mod.check(self.usage(), self.budget) == "stop":
            raise BudgetExceeded("The card's budget is used up: the browser session was not opened.")
        session = self.driver.open(f"{self.repo}#{self.number}")
        self.session = session
        link = takeover_link(self.domain, self.repo, self.number, session.id)
        self.comment(f"{SESSION_MARK}\nBrowser session `{quote(session.id)}` opened ({self.driver.name}).\n"
                     f"Take it over: {link}\n"
                     "Only people with the project's live_takeover role can open it.")
        return session

    def tick(self) -> str:
        """'ok', 'warn' or 'stop' for the card with the session's cost; closes the session on 'stop'."""
        state = budget_mod.check(self._usage_with_session(), self.budget)
        if state == "stop" and self.session is not None and self.session.closed_at is None:
            self.close("budget")
        return state

    def close(self, reason: str = "done") -> Session | None:
        """Close the session and say on the card how long it ran and what it cost."""
        if reason not in CLOSE_REASONS:
            raise ValueError(f"reason must be one of {', '.join(CLOSE_REASONS)}")
        s = self.session
        if s is None or s.closed_at is not None:
            return s
        now = self.clock()
        usd, reported = self.driver.cost(s, now)
        s.usd, s.usd_reported = usd, reported
        s.closed_at, s.close_reason = now, reason
        try:
            self.driver.close(s)
        finally:
            cost = f"${usd:.4f}" if reported else "cost not reported"
            why = {"done": "finished", "budget": "the card's budget reached 100%",
                   "error": "an error", "cancelled": "cancelled"}[reason]
            self.comment(f"{SESSION_MARK}\nBrowser session `{quote(s.id)}` closed: {why}. "
                         f"{s.minutes(now):.1f} min, {cost}.")
        return s
