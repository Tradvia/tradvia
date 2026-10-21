# SPDX-License-Identifier: FSL-1.1-ALv2
"""The PR cost survives the store: parsed from the body on write, served on read."""

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from tradvia.api import create_app  # noqa: E402
from tradvia.events import (  # noqa: E402
    comment_record,
    issue_record,
    parse_cost,
    parse_tokens,
    pull_request_record,
)
from tradvia.store import MemoryStore  # noqa: E402

REPO = "Tradvia/tradvia"
AGENT_BODY = (
    "Closes #44\n\nOpened by **@Agent-Coder** after human approval in GitHub.\n\n"
    "Card `CARD-44` · estimated cost so far (API-equivalent): $1.1636\n\n"
    "### Claude Code\nDone.\n\nEstimated cost (API-equivalent): $1.0051 · 285s\n"
    "Tokens: 12 345 in · 3 210 out · 80 000 cache"
)


@pytest.mark.parametrize("body, expected", [
    (AGENT_BODY, (1.0051, 285.0)),  # same choice as parseCost() in the dashboard
    ("Estimated cost (API-equivalent): $0.4200", (0.42, None)),
    ("cost: $2 · 31s", (2.0, 31.0)),
    ("Card `c` · estimated cost so far (API-equivalent): $0.5000", (0.5, None)),
    ("no cost here", (None, None)),
    (None, (None, None)),
])
def test_parse_cost(body, expected):
    assert parse_cost(body) == expected


def pr(number, body):
    return {"id": 700 + number, "number": number, "title": f"PR {number}", "state": "open",
            "head": {"ref": f"tradvia/issue-{number}"}, "user": {"login": "github-actions[bot]"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "html_url": f"https://github.com/{REPO}/pull/{number}", "body": body}


def test_record_keeps_the_cost_but_not_the_body():
    rec = pull_request_record(pr(1, AGENT_BODY))
    assert (rec["cost_usd"], rec["cost_seconds"]) == (1.0051, 285.0)
    assert "body" not in rec


def test_store_read_serves_the_cost(monkeypatch):
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.delenv("TRADVIA_PROJECTS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = MemoryStore()
    pid = store.ensure_project(REPO)
    store.upsert_pull_request(pid, pull_request_record(pr(1, AGENT_BODY)))
    store.upsert_pull_request(pid, pull_request_record(pr(2, "no cost")))
    body = TestClient(create_app(store=store)).get(
        f"/api/store/repos/{REPO}/pulls?state=all").json()
    cost = {p["number"]: p["tradvia_cost"] for p in body}
    assert cost == {
        1: {"usd": 1.0051, "seconds": 285.0, "tokens_in": 12345, "tokens_out": 3210,
            "tokens_cache": 80000},
        2: None,
    }


PLAN_BODY = (
    "🟡 **CARD-44 — T**\nResumo\n1. passo\n\n❓ Posso?\n"
    "Estimated cost (API-equivalent): $0.2500\nTokens: 1 000 in · 200 out · 5 000 cache\n"
    "Responda **Aprovado**, **Rejeitado** ou escreva um ajuste."
)


def comment(cid, login, body, minute=0):
    return {"id": cid, "user": {"login": login}, "body": body,
            "created_at": f"2026-01-01T00:{minute:02d}:00Z",
            "updated_at": f"2026-01-01T00:{minute:02d}:00Z"}


@pytest.mark.parametrize("body, expected", [
    ("Tokens: 12 345 in · 3 210 out · 80 000 cache", (12345, 3210, 80000)),
    ("Tokens: 12 345 in · 3 210 out · 80 000 cache", (12345, 3210, 80000)),
    ("Tokens: 12 345 in · 3 210 out · 80 000 cache", (12345, 3210, 80000)),
    ("Tokens: 12.345 in · 3.210 out · 80.000 cache", (12345, 3210, 80000)),
    ("tokens: 12,345 in | 3,210 out | 80,000 cache", (12345, 3210, 80000)),
    ("Tokens: 12345 in · 3210 out · 80000 cache", (12345, 3210, 80000)),
    ("Tokens: 7 in · 0 out · 0 cache", (7, 0, 0)),
    ("Tokens: 7 in · 2 out", (7, 2, 0)),
    ("no tokens", None),
    ("Tokens: many", None),
    (None, None),
])
def test_parse_tokens(body, expected):
    assert parse_tokens(body) == expected


def test_pr_record_keeps_the_tokens():
    rec = pull_request_record(pr(1, AGENT_BODY))
    assert (rec["tokens_in"], rec["tokens_out"], rec["tokens_cache"]) == (12345, 3210, 80000)
    bare = pull_request_record(pr(2, "no cost"))
    assert (bare["tokens_in"], bare["tokens_out"], bare["tokens_cache"]) == (None, None, None)


def test_comment_record_keeps_cost_and_tokens_of_agent_plans_only():
    mine = comment_record(comment(1, "github-actions[bot]", PLAN_BODY), 44)
    assert mine["cost_usd"] == 0.25
    assert (mine["tokens_in"], mine["tokens_out"], mine["tokens_cache"]) == (1000, 200, 5000)
    human = comment_record(comment(2, "ana", PLAN_BODY), 44)
    assert human["cost_usd"] is None and human["tokens_in"] is None
    plain = comment_record(comment(3, "github-actions[bot]", "🟢 PR aberto: x"), 44)
    assert plain["cost_usd"] is None and plain["tokens_cache"] is None
    assert mine["body"] == PLAN_BODY


def test_store_serves_every_plan_of_a_card(monkeypatch):
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.delenv("TRADVIA_PROJECTS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = MemoryStore()
    pid = store.ensure_project(REPO)
    issue = {"id": 5, "number": 44, "title": "T", "state": "open", "labels": [{"name": "agent:ready"}],
             "user": {"login": "ana"}, "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"}
    store.upsert_issue(pid, issue_record(issue))
    for cid, minute, text in ((1, 1, PLAN_BODY), (2, 5, PLAN_BODY.replace("$0.2500", "$0.5000")),
                              (3, 6, "obrigado")):
        store.upsert_comment(pid, comment_record(comment(cid, "github-actions[bot]", text, minute), 44))
    got = TestClient(create_app(store=store)).get(
        f"/api/store/repos/{REPO}/issues?state=all&labels=agent:ready").json()
    plans = got[0]["tradvia_plan_cost"]
    assert [p["usd"] for p in plans] == [0.25, 0.5]
    assert plans[0]["tokens_in"] == 1000 and plans[0]["tokens_cache"] == 5000


def test_backfill_writes_plan_and_pr_tokens(monkeypatch):
    from tradvia import backfill as bf

    monkeypatch.delenv("DATABASE_URL", raising=False)
    issue = {"id": 5, "number": 44, "title": "T", "state": "open", "labels": [{"name": "agent:ready"}],
             "user": {"login": "ana"}, "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z"}

    def get(path, params):
        if path.endswith("/issues"):
            return [issue]
        if path.endswith("/comments"):
            return [comment(1, "github-actions[bot]", PLAN_BODY, 1)]
        if path.endswith("/pulls"):
            return [pr(1, AGENT_BODY)]
        return {"workflow_runs": []}

    store = MemoryStore()
    bf.backfill(store, get, REPO)
    pid = store.ensure_project(REPO)
    assert store.list_plan_costs(pid)[0]["tokens_out"] == 200
    assert store.list_pulls(pid, "all", 10)[0]["tokens_cache"] == 80000
