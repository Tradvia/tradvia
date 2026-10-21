# SPDX-License-Identifier: FSL-1.1-ALv2
"""The PR cost survives the store: parsed from the body on write, served on read."""

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from tradvia.api import create_app  # noqa: E402
from tradvia.events import parse_cost, pull_request_record  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

REPO = "Tradvia/tradvia"
AGENT_BODY = (
    "Closes #44\n\nOpened by **@Agent-Coder** after human approval in GitHub.\n\n"
    "Card `CARD-44` · estimated cost so far (API-equivalent): $1.1636\n\n"
    "### Claude Code\nDone.\n\nEstimated cost (API-equivalent): $1.0051 · 285s"
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
    assert cost == {1: {"usd": 1.0051, "seconds": 285.0}, 2: None}
