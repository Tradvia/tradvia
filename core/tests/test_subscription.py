# SPDX-License-Identifier: FSL-1.1-ALv2
"""The pure rules of the chat's subscription mode (ADR 0012)."""

import pytest

from tradvia import subscription
from tradvia.subscription import Pace, notice, parse_pace, schedule, used

NOW = 1_800_000_000.0


@pytest.mark.parametrize("section, expected", [
    (None, (60, 20)),
    ({}, (60, 20)),
    ({"min_interval_seconds": 120, "max_per_hour": 5}, (120, 5)),
    ({"min_interval_seconds": 5, "max_per_hour": 500}, (60, 20)),  # never faster
    ({"min_interval_seconds": 99_999}, (3600, 20)),
    ({"min_interval_seconds": "soon", "max_per_hour": "many"}, (60, 20)),
    ({"min_interval_seconds": True, "max_per_hour": 0}, (60, 20)),
    ({"min_interval_seconds": float("nan"), "max_per_hour": float("inf")}, (60, 20)),
    ({"max_per_hour": 2.9}, (60, 2)),
    ("fast", (60, 20)),
])
def test_parse_pace_is_never_looser_than_the_ceiling_and_never_raises(section, expected):
    pace = parse_pace(section)
    assert (pace.min_interval_s, pace.max_per_hour) == expected


def test_first_answer_starts_now():
    assert schedule(NOW, [], Pace()) == (NOW, None)


def test_an_answer_waits_the_interval_after_the_latest_start():
    assert schedule(NOW, [NOW - 3600, NOW - 10], Pace()) == (NOW + 50, "interval")


def test_a_waiting_reservation_pushes_the_next_one_further():
    assert schedule(NOW, [NOW - 30, NOW + 30], Pace()) == (NOW + 90, "interval")


def test_the_hourly_limit_waits_for_the_oldest_to_leave_the_hour():
    starts = [NOW - 3000 + 60 * i for i in range(20)]  # 20 answers, a minute apart
    start, reason = schedule(NOW, starts, Pace())
    assert (start, reason) == (NOW + 600, "hourly")  # the first of them leaves at NOW + 600


def test_the_hourly_limit_of_a_stricter_project():
    pace = Pace(min_interval_s=60, max_per_hour=2)
    assert schedule(NOW, [NOW - 120, NOW - 60], pace) == (NOW - 120 + 3600, "hourly")


def test_used_counts_only_answers_already_started_in_the_hour():
    assert used(NOW, [NOW - 4000, NOW - 100, NOW, NOW + 50]) == 2


def test_the_notice_says_why_and_points_to_an_api_key():
    msg = notice(50, "interval", Pace(), "ana")
    assert msg["kind"] == "pace"
    assert msg["refs"] == [{"type": "pace", "reason": "interval", "seconds": 50, "owner": "ana",
                            "min_interval_seconds": 60, "max_per_hour": 20}]
    assert "@ana" in msg["text"] and "60 s between answers" in msg["text"] and "API key" in msg["text"]
    assert "20 answers an hour" in notice(600, "hourly", Pace(), "ana")["text"]


def test_owner_and_token_come_from_the_server_and_only_presence_is_read():
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x", "CLAUDE_CODE_OAUTH_TOKEN_OWNER": " @Ana "}
    assert subscription.server_token(env) is True and subscription.server_owner(env) == "Ana"
    assert subscription.server_token({}) is False and subscription.server_owner({}) is None
    assert subscription.is_owner("ana", "Ana") and not subscription.is_owner("bob", "Ana")
    assert not subscription.is_owner("ana", None) and not subscription.is_owner(None, "ana")
