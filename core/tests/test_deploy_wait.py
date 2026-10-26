# SPDX-License-Identifier: FSL-1.1-ALv2
"""The deploy wait alert: the threshold, how long a deploy has waited, and what the panel gets."""

from datetime import datetime, timezone

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import BASE, client, clock, env, login, make_client, store  # noqa: E402,F401
from test_deployments import gh  # noqa: E402,F401
from test_pulls import signed  # noqa: E402,F401
from tradvia.actions import DEFAULT_WAIT_ALERT_MINUTES, mark_waits, wait_alert_minutes  # noqa: E402

NOW = 1_800_000_000.0


def iso_at(now, minutes_ago):
    """A GitHub-style UTC timestamp `minutes_ago` before `now` (negative: in the future)."""
    return datetime.fromtimestamp(now - minutes_ago * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("raw, expected", [
    (None, 30), ("", 30), ("45", 45), (" 12 ", 12), ("0", 30), ("-5", 30), ("4.5", 30), ("soon", 30),
])
def test_threshold_keeps_the_default_for_anything_unusable(raw, expected):
    assert wait_alert_minutes(raw) == expected


def test_default_threshold_is_thirty_minutes():
    assert DEFAULT_WAIT_ALERT_MINUTES == 30


def test_a_deploy_is_long_only_after_the_threshold_not_at_it():
    deploys = [
        {"run_id": 1, "created_at": iso_at(NOW, 30)},
        {"run_id": 2, "created_at": iso_at(NOW, 31)},
        {"run_id": 3, "created_at": iso_at(NOW, 5)},
        {"run_id": 4, "created_at": iso_at(NOW, -2)},  # clock skew: never negative
        {"run_id": 5, "created_at": None},
        {"run_id": 6, "created_at": "not a date"},
    ]
    got = mark_waits(deploys, NOW, 30)
    assert [(d["run_id"], d["long_wait"], d["waiting_minutes"]) for d in got] == [
        (1, False, 30), (2, True, 31), (3, False, 5), (4, False, 0), (5, False, None), (6, False, None),
    ]
    assert "long_wait" not in deploys[0]  # the input list is not changed


def test_the_panel_gets_the_flags_and_the_default_threshold(signed, gh, clock):
    now = clock()
    gh.runs[0]["created_at"] = iso_at(now, 31)  # run 5 has waited 31 minutes
    gh.runs[1]["created_at"] = iso_at(now, 5)   # run 6 has waited 5 minutes
    d = signed.get(BASE + "/deployments/pending").json()
    assert d["wait_alert_minutes"] == DEFAULT_WAIT_ALERT_MINUTES
    got = {x["run_id"]: (x["long_wait"], x["waiting_minutes"]) for x in d["deploys"]}
    assert got == {5: (True, 31), 6: (False, 5)}


def test_the_threshold_is_read_from_the_environment(store, gh, clock, monkeypatch):
    monkeypatch.setenv("TRADVIA_DEPLOY_WAIT_ALERT_MINUTES", "45")
    panel = make_client(store, gh, clock)
    login(panel)
    now = clock()
    gh.runs[0]["created_at"] = iso_at(now, 31)  # 31 minutes: not long with a 45-minute threshold
    gh.runs[1]["created_at"] = iso_at(now, 50)  # 50 minutes: long
    d = panel.get(BASE + "/deployments/pending").json()
    assert d["wait_alert_minutes"] == 45
    assert {x["run_id"]: x["long_wait"] for x in d["deploys"]} == {5: False, 6: True}
