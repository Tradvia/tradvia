# SPDX-License-Identifier: FSL-1.1-ALv2
"""Proactive alerts: the three checks, one alert per occurrence, delivery to both channels."""

from datetime import datetime, timezone

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_push import (  # noqa: E402,F401
    KEYS, REPO, SLUG, URL, FakeGitHub, FakeSender, env, login, store, subscribe,
)
from tradvia import alerts  # noqa: E402
from tradvia.api import create_app  # noqa: E402

NOW = 1_800_000_000.0
LINK = f"https://github.com/{REPO}/issues/7"


def iso_at(now, minutes_ago):
    return datetime.fromtimestamp(now - minutes_ago * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeDiscord:
    name = "Discord"

    def __init__(self):
        self.posts = []
        self.fail = False

    def post(self, text):
        if self.fail:
            raise RuntimeError("discord down")
        self.posts.append(text)
        return str(len(self.posts))


class GitHubWithDeploys(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.runs = []
        self.pending = {}

    def list_runs(self, token, repo, status):
        return list(self.runs)

    def list_pending_deployments(self, token, repo, run_id):
        return self.pending.get(run_id, [])


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * 60


@pytest.fixture
def gh(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "service-token")
    return GitHubWithDeploys()


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def discord():
    return FakeDiscord()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(store, gh, sender, discord, clock):
    return create_app(store=store, identity=gh, push_sender=sender, clock=clock,
                      alert_discord=discord)


@pytest.fixture
def pid(store):
    return store.ensure_project(REPO)


def waiting_run(gh, clock, minutes, run_id=5, issue=7, branch=None):
    gh.runs = [{
        "id": run_id, "name": "deploy", "path": ".github/workflows/deploy.yml",
        "head_branch": branch or f"tradvia/issue-{issue}", "html_url": f"https://x/runs/{run_id}",
        "created_at": iso_at(clock.now, minutes),
    }]
    gh.pending = {run_id: [{"environment": {"name": "prod"}, "current_user_can_approve": True}]}


def add_cost(store, pid, number, usd, cid):
    store.upsert_comment(pid, {
        "id": cid, "issue_number": number, "author": "bot", "body": "", "created_at": STAMP,
        "updated_at": STAMP, "cost_usd": usd, "tokens_in": 0, "tokens_out": 0, "tokens_cache": 0,
    })


def add_run(store, pid, run_id, minutes_ago, clock, status="in_progress", attempt=1):
    store.upsert_workflow_run(pid, {
        "id": run_id, "workflow": ".github/workflows/agent.yml", "event": "issues",
        "status": status, "conclusion": None, "issue_number": 7, "started_at": STAMP,
        "ended_at": None, "updated_at": iso_at(clock.now, minutes_ago), "name": "agent",
        "display_title": "t", "html_url": f"https://x/runs/{run_id}", "run_attempt": attempt,
    })


STAMP = "2026-01-01T00:00:00Z"


# --- the checks -----------------------------------------------------------------------------


def deploy_alerts(minutes_waiting, **kw):
    run = {"id": 5, "created_at": iso_at(NOW, minutes_waiting), "head_branch": "tradvia/issue-7",
           "html_url": "https://x/runs/5", **kw}
    pend = {5: [{"environment": {"name": "prod"}}]}
    return alerts.stuck_deploys([run], pend, NOW, REPO, SLUG, 30)


def test_a_deploy_alerts_only_past_the_limit():
    assert deploy_alerts(30) == []
    assert deploy_alerts(31) == [alerts.Alert("deploy_stuck", SLUG, 7, "deploy_stuck:5", LINK)]


def test_a_replaced_deploy_never_alerts():
    old = {"id": 4, "created_at": iso_at(NOW, 90), "workflow_id": 1}
    new = {"id": 5, "created_at": iso_at(NOW, 10), "workflow_id": 1}
    pend = {4: [{"environment": {"name": "prod"}}], 5: [{"environment": {"name": "prod"}}]}
    assert alerts.stuck_deploys([old, new], pend, NOW, REPO, SLUG, 30) == []


def costs(*per_card):
    return [{"issue_number": n, "cost_usd": usd} for n, usd in per_card]


def test_cost_above_three_times_the_median_alerts():
    base = [(n, 1.0) for n in range(1, 7)]
    assert alerts.cost_outliers(costs(*base, (7, 3.0)), REPO, SLUG) == []
    got = alerts.cost_outliers(costs(*base, (7, 3.5)), REPO, SLUG)
    assert [(a.number, a.key, a.link) for a in got] == [(7, "cost_outlier:7", LINK)]


def test_card_cost_is_summed_over_its_plans():
    base = [(n, 1.0) for n in range(1, 7)]
    got = alerts.cost_outliers(costs(*base, (7, 2.0), (7, 2.0)), REPO, SLUG)
    assert [a.number for a in got] == [7]


def test_few_cards_or_a_zero_median_do_not_alert():
    assert alerts.cost_outliers(costs((1, 1.0), (2, 1.0), (3, 90.0)), REPO, SLUG) == []
    zeros = [(n, 0.0) for n in range(1, 7)]
    assert alerts.cost_outliers(costs(*zeros, (7, 9.0)), REPO, SLUG) == []


def run(updated_minutes_ago, status="in_progress", attempt=2):
    return {"id": 9, "status": status, "issue_number": 7, "run_attempt": attempt,
            "updated_at": iso_at(NOW, updated_minutes_ago)}


def test_a_run_without_updates_alerts_past_the_limit():
    assert alerts.dead_runs([run(10)], NOW, REPO, SLUG, 30) == []
    assert alerts.dead_runs([run(30)], NOW, REPO, SLUG, 30) == []
    got = alerts.dead_runs([run(31)], NOW, REPO, SLUG, 30)
    assert [(a.key, a.link) for a in got] == [("run_dead:9:2", LINK)]


def test_a_finished_run_is_not_dead():
    assert alerts.dead_runs([run(300, status="completed")], NOW, REPO, SLUG, 30) == []


@pytest.mark.parametrize("raw, expected", [(None, 3.0), ("5", 5.0), ("0", 3.0), ("x", 3.0), ("-1", 3.0)])
def test_factor_keeps_the_default_for_anything_unusable(raw, expected):
    assert alerts.cost_factor(raw) == expected


@pytest.mark.parametrize("raw, expected", [(None, 30), ("45", 45), ("0", 30), ("4.5", 30), ("x", 30)])
def test_stale_minutes_keeps_the_default_for_anything_unusable(raw, expected):
    assert alerts.run_stale_minutes(raw) == expected


# --- the loop: one alert per occurrence ----------------------------------------------------


def test_two_ticks_with_the_same_occurrence_send_one_message_per_channel(
        app, store, gh, sender, discord, clock):
    ana = login(app, "ana")
    assert subscribe(ana, "ana").status_code == 200
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    app.state.alert_tick()
    assert len(discord.posts) == 1
    assert [p for _, p in sender.sent] == [{"project": SLUG, "card": 7, "kind": "deploy_stuck"}]


def test_message_has_the_kind_and_the_card_link_only(app, gh, discord, clock):
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    (text,) = discord.posts
    assert LINK in text and SLUG in text and text.startswith(alerts.LABELS["deploy_stuck"])


def test_advancing_the_clock_without_resolving_does_not_repeat(app, gh, discord, clock):
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    clock.advance(120)
    app.state.alert_tick()
    assert len(discord.posts) == 1


def test_resolved_and_back_alerts_again(app, store, pid, discord, clock):
    add_run(store, pid, 9, 45, clock)
    app.state.alert_tick()
    assert len(discord.posts) == 1
    add_run(store, pid, 9, 0, clock)  # the run reported again: resolved
    app.state.alert_tick()
    assert len(discord.posts) == 1
    clock.advance(45)  # silent again
    app.state.alert_tick()
    assert len(discord.posts) == 2


def test_a_new_attempt_of_a_dead_run_is_a_new_occurrence(app, store, pid, discord, clock):
    add_run(store, pid, 9, 45, clock, attempt=1)
    app.state.alert_tick()
    add_run(store, pid, 9, 45, clock, attempt=2)
    app.state.alert_tick()
    assert len(discord.posts) == 2


def test_cost_outlier_through_the_loop(app, store, pid, discord, clock):
    for n in range(1, 7):
        add_cost(store, pid, n, 1.0, n)
    add_cost(store, pid, 7, 4.0, 7)
    app.state.alert_tick()
    app.state.alert_tick()
    assert len(discord.posts) == 1 and LINK in discord.posts[0]


def test_a_failed_check_keeps_what_was_announced(app, gh, discord, clock):
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    saved = gh.list_runs
    gh.list_runs = lambda *a: (_ for _ in ()).throw(RuntimeError("github down"))
    app.state.alert_tick()
    gh.list_runs = saved
    app.state.alert_tick()
    assert len(discord.posts) == 1


# --- delivery -------------------------------------------------------------------------------


def test_discord_failure_does_not_stop_push(app, gh, sender, discord, clock):
    assert subscribe(login(app, "ana"), "ana").status_code == 200
    discord.fail = True
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    assert len(sender.sent) == 1


def test_push_failure_does_not_stop_discord(app, gh, sender, discord, clock):
    assert subscribe(login(app, "ana"), "ana").status_code == 200

    def boom(sub, payload):
        raise RuntimeError(sub["endpoint"])

    sender.send = boom
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    assert len(discord.posts) == 1


def test_alert_kinds_can_be_muted_and_follow_the_role(app, store, gh, sender, clock):
    ana, bob = login(app, "ana"), login(app, "bob")
    subscribe(ana, "ana")
    subscribe(bob, "bob")  # triage: may not approve deploys
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    assert [e for e, _ in sender.sent] == [URL["ana"]]


def test_a_muted_kind_gets_no_push(app, store, pid, gh, sender, clock):
    from test_push import headers
    ana = login(app, "ana")
    subscribe(ana, "ana")
    r = ana.put(f"/api/projects/{REPO}/push/prefs", json={"kinds": {"deploy_stuck": False}},
                headers=headers(ana))
    assert r.status_code == 200
    waiting_run(gh, clock, 45)
    app.state.alert_tick()
    assert sender.sent == []
