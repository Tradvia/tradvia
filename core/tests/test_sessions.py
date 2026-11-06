# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Session driver interface (#119, ADR 0022, ADR 0024), tested offline with the fake driver."""

import pytest

from tradvia import sessions
from tradvia.budget import Budget


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def card_session(usd_per_minute=0.0, usage=None, budget=Budget(max_minutes=30, max_steps=40, max_usd=1.0),
                 reported=True):
    clock = Clock()
    comments = []
    driver = sessions.FakeDriver(usd_per_minute=usd_per_minute, reported=reported, clock=clock)
    cs = sessions.CardSession(driver=driver, repo="Tradvia/tradvia", number=119, budget=budget,
                              usage=lambda: dict(usage or {}), comment=comments.append,
                              domain="app.tradvia.org", clock=clock)
    return cs, driver, clock, comments


def test_the_session_opens_records_the_takeover_link_and_closes():
    cs, driver, clock, comments = card_session(usd_per_minute=0.01)
    s = cs.open()
    assert driver.opened == [s] and s.closed_at is None
    assert len(comments) == 1 and sessions.SESSION_MARK in comments[0]
    assert "https://app.tradvia.org/runs/?repo=Tradvia%2Ftradvia&card=119&session=fake-1" in comments[0]
    assert "live_takeover" in comments[0]
    clock.t += 300  # five minutes
    closed = cs.close("done")
    assert closed is s and s.close_reason == "done" and driver.closed == ["fake-1"]
    assert "closed: finished. 5.0 min, $0.0500." in comments[-1]
    # Closing again changes nothing and posts nothing.
    assert cs.close("done") is s and len(comments) == 2 and driver.closed == ["fake-1"]


def test_the_control_url_never_reaches_the_card():
    cs, driver, clock, comments = card_session()
    s = cs.open()
    clock.t += 60
    cs.close()
    assert "secret-" in s.control_url
    assert not any("secret-" in c or "ws://" in c for c in comments)
    assert "secret" not in repr(s) and "ws://" not in repr(s)


def test_the_session_closes_when_the_card_budget_reaches_100_percent():
    # Budget $1.00; the card spent $0.75 before the session; the browser costs $0.10/min.
    cs, driver, clock, comments = card_session(usd_per_minute=0.10, usage={"usd": 0.75})
    cs.open()
    clock.t += 60  # $0.85 total: over 80%, a warning, still open
    assert cs.tick() == "warn" and cs.session.closed_at is None
    clock.t += 180  # $1.15 total: stop
    assert cs.tick() == "stop"
    assert cs.session.close_reason == "budget" and driver.closed == ["fake-1"]
    assert "the card's budget reached 100%" in comments[-1]
    assert cs.tick() == "stop" and driver.closed == ["fake-1"]  # closed once


def test_no_session_opens_when_the_budget_is_already_used_up():
    cs, driver, clock, comments = card_session(usage={"steps": 40})
    with pytest.raises(sessions.BudgetExceeded):
        cs.open()
    assert driver.opened == [] and comments == []


def test_session_minutes_are_not_counted_twice_but_its_cost_is():
    cs, driver, clock, comments = card_session(usd_per_minute=0.0, usage={"minutes": 10, "usd": 0.2})
    cs.open()
    clock.t += 25 * 60
    assert cs.tick() == "ok"  # 10 card minutes (already wall time), $0.20: under 80%
    assert cs._usage_with_session() == {"minutes": 10, "usd": 0.2}


def test_an_unreported_cost_is_marked_and_never_invented():
    cs, driver, clock, comments = card_session(usd_per_minute=5.0, reported=False)
    cs.open()
    clock.t += 600
    s = cs.close("done")
    assert s.usd == 0.0 and s.usd_reported is False
    assert "cost not reported" in comments[-1]


def test_a_failed_open_leaves_nothing_on_the_card():
    cs, driver, clock, comments = card_session()
    driver.fail_open = True
    with pytest.raises(RuntimeError):
        cs.open()
    assert cs.session is None and comments == []


def test_open_twice_returns_the_same_session():
    cs, driver, clock, comments = card_session()
    assert cs.open() is cs.open()
    assert len(driver.opened) == 1 and len(comments) == 1


def test_catalog_has_only_the_fake_driver_and_names_the_planned_one():
    assert sessions.DRIVERS == ("fake",)
    assert isinstance(sessions.make_driver("fake"), sessions.FakeDriver)
    with pytest.raises(ValueError, match="Planned"):
        sessions.make_driver("self-hosted-browser")
    with pytest.raises(ValueError, match="Unknown"):
        sessions.make_driver("browserbase")


def test_close_reason_is_checked():
    cs, *_ = card_session()
    cs.open()
    with pytest.raises(ValueError):
        cs.close("because")
