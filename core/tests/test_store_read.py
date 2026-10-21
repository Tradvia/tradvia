# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import backfill as bf  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.events import (  # noqa: E402
    issue_record,
    payload_ops,
    pull_request_record,
    workflow_run_record,
)
from tradvia.store import MemoryStore, StoreUnavailable  # noqa: E402

REPO = "Tradvia/tradvia"
BASE = f"/api/store/repos/{REPO}"
SECRET_BODY = "SECRET-COMMENT-BODY"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def gh_issue(n, state="open", labels=("agent:ready",), created="2026-01-0%dT00:00:00Z"):
    return {
        "id": 1000 + n,
        "number": n,
        "title": f"Issue {n}",
        "state": state,
        "state_reason": "completed" if state == "closed" else None,
        "labels": [{"name": name} for name in labels],
        "user": {"login": "ana"},
        "html_url": f"https://github.com/{REPO}/issues/{n}",
        "created_at": created % n,
        "updated_at": created % n,
        "closed_at": "2026-02-01T00:00:00Z" if state == "closed" else None,
    }


def gh_pr(n, state="open", merged_at=None):
    return {
        "id": 2000 + n,
        "number": n,
        "title": f"PR {n}",
        "state": state,
        "merged_at": merged_at,
        "head": {"ref": f"issue-{n}"},
        "user": {"login": "bot"},
        "html_url": f"https://github.com/{REPO}/pull/{n}",
        "created_at": f"2026-01-0{n}T00:00:00Z",
        "updated_at": f"2026-01-0{n}T00:00:00Z",
    }


def gh_run(n, workflow="agent.yml"):
    return {
        "id": 3000 + n,
        "name": "Agent",
        "display_title": f"Run {n}",
        "path": f".github/workflows/{workflow}",
        "event": "issues",
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://github.com/{REPO}/actions/runs/{3000 + n}",
        "run_attempt": 2,
        "run_started_at": f"2026-01-0{n}T01:00:00Z",
        "created_at": f"2026-01-0{n}T01:00:00Z",
        "updated_at": f"2026-01-0{n}T02:00:00Z",
        "head_branch": f"issue-{n}",
    }


@pytest.fixture
def store():
    s = MemoryStore()
    pid = s.ensure_project(REPO)
    for i in (gh_issue(1), gh_issue(2, state="closed"), gh_issue(3, labels=("bug",))):
        s.upsert_issue(pid, issue_record(i))
    s.upsert_pull_request(pid, pull_request_record(gh_pr(1)))
    s.upsert_pull_request(pid, pull_request_record(gh_pr(2, "closed", "2026-01-03T00:00:00Z")))
    s.upsert_workflow_run(pid, workflow_run_record(gh_run(1)))
    s.upsert_workflow_run(pid, workflow_run_record(gh_run(2)))
    s.upsert_workflow_run(pid, workflow_run_record(gh_run(3, "claude.yml")))
    for op in payload_ops(
        "issue_comment",
        {
            "issue": gh_issue(1),
            "comment": {"id": 9, "user": {"login": "x"}, "body": SECRET_BODY,
                        "updated_at": "2026-01-05T00:00:00Z"},
        },
    ):
        getattr(s, {"issue": "upsert_issue", "comment": "upsert_comment"}[op[0]])(pid, op[1])
    return s


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store))


def test_issues_format_and_order(client):
    r = client.get(f"{BASE}/issues?labels=agent:ready&state=all&per_page=50")
    assert r.status_code == 200
    body = r.json()
    assert [i["number"] for i in body] == [2, 1]
    assert body[1] == {
        "number": 1,
        "title": "Issue 1",
        "state": "open",
        "state_reason": None,
        "labels": [{"name": "agent:ready"}],
        "user": {"login": "ana"},
        "html_url": f"https://github.com/{REPO}/issues/1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "closed_at": None,
    }
    assert body[0]["state_reason"] == "completed"
    assert body[0]["closed_at"] == "2026-02-01T00:00:00Z"


def test_issues_filters(client):
    nums = lambda q: [i["number"] for i in client.get(f"{BASE}/issues?{q}").json()]  # noqa: E731
    assert nums("state=open&labels=agent:ready") == [1]
    assert nums("state=closed&labels=agent:ready") == [2]
    assert nums("state=all&labels=bug") == [3]
    assert nums("state=all") == [3, 2, 1]
    assert nums("state=open") == [3, 1]  # default labels: none


def test_issues_valid_filter_without_match_is_empty_200(client):
    r = client.get(f"{BASE}/issues?labels=nope&state=all")
    assert r.status_code == 200 and r.json() == []


def test_pulls_format_state_and_order(client):
    body = client.get(f"{BASE}/pulls?state=all&per_page=50").json()
    assert [p["number"] for p in body] == [2, 1]
    assert body[0] == {
        "number": 2,
        "title": "PR 2",
        "state": "closed",
        "merged_at": "2026-01-03T00:00:00Z",
        "head": {"ref": "issue-2"},
        "user": {"login": "bot"},
        "html_url": f"https://github.com/{REPO}/pull/2",
        "created_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "tradvia_cost": None,
    }
    assert [p["number"] for p in client.get(f"{BASE}/pulls?state=open").json()] == [1]
    assert client.get(f"{BASE}/pulls?state=closed&per_page=1").json()[0]["number"] == 2


def test_runs_format_and_order(client):
    r = client.get(f"{BASE}/actions/workflows/agent.yml/runs?per_page=30")
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 2
    assert [x["id"] for x in body["workflow_runs"]] == [3002, 3001]
    assert body["workflow_runs"][0] == {
        "id": 3002,
        "name": "Agent",
        "display_title": "Run 2",
        "event": "issues",
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://github.com/{REPO}/actions/runs/3002",
        "run_attempt": 2,
        "run_started_at": "2026-01-02T01:00:00Z",
        "created_at": "2026-01-02T01:00:00Z",
        "updated_at": "2026-01-02T02:00:00Z",
    }
    claude = client.get(f"{BASE}/actions/workflows/claude.yml/runs").json()
    assert [x["id"] for x in claude["workflow_runs"]] == [3003]


def test_runs_valid_filter_without_match_has_zero_total(store):
    only_agent = MemoryStore()
    only_agent.upsert_workflow_run(
        only_agent.ensure_project(REPO), workflow_run_record(gh_run(1))
    )
    c = TestClient(create_app(store=only_agent))
    r = c.get(f"{BASE}/actions/workflows/claude.yml/runs")
    assert r.status_code == 200
    assert r.json() == {"total_count": 0, "workflow_runs": []}


def test_per_page_is_limited(client, store):
    for n in range(10, 130):
        store.upsert_pull_request(
            1, pull_request_record({**gh_pr(1), "id": 5000 + n, "number": n})
        )
    assert len(client.get(f"{BASE}/pulls?state=all&per_page=1000").json()) == 100
    assert len(client.get(f"{BASE}/pulls?state=all&per_page=0").json()) == 1
    assert len(client.get(f"{BASE}/pulls?state=all&per_page=abc").json()) == 30
    assert len(client.get(f"{BASE}/pulls?state=all&per_page=2").json()) == 2


@pytest.mark.parametrize(
    "path",
    [
        f"/api/store/repos/{REPO}/issues/1/comments",
        f"/api/store/repos/{REPO}/issues/1/timeline",
        f"/api/store/repos/{REPO}/actions/runs",
        f"/api/store/repos/{REPO}/actions/workflows/other.yml/runs",
        f"/api/store/repos/{REPO}/comments",
        "/api/store/repos/Other/repo/issues",
        f"/api/store/repos/{REPO}/../Other/issues",
        f"/api/store/repos/{REPO}/issues%2F1",
    ],
)
def test_paths_outside_the_list_are_404(client, path):
    assert client.get(path).status_code == 404


def test_invalid_state_is_400(client):
    assert client.get(f"{BASE}/issues?state=bogus").status_code == 400


RESOURCES = [
    f"{BASE}/issues",
    f"{BASE}/pulls",
    f"{BASE}/actions/workflows/agent.yml/runs",
]


@pytest.mark.parametrize("path", RESOURCES)
def test_503_without_database(path):
    assert TestClient(create_app()).get(path).status_code == 503


class DownStore(MemoryStore):
    def list_issues(self, *a):
        raise StoreUnavailable("down")

    list_pulls = list_runs = list_issues


@pytest.mark.parametrize("path", RESOURCES)
def test_503_when_database_is_down(path):
    assert TestClient(create_app(store=DownStore())).get(path).status_code == 503


@pytest.mark.parametrize("path", RESOURCES)
def test_503_when_table_is_empty(path):
    assert TestClient(create_app(store=MemoryStore())).get(path).status_code == 503


def test_no_comment_body_in_any_response(client):
    for path in RESOURCES + [f"{BASE}/issues?state=all", f"{BASE}/pulls?state=all"]:
        assert SECRET_BODY not in client.get(path).text
    assert SECRET_BODY not in client.get("/api/store/health").text


def test_mappers_fill_new_fields_from_webhook_payload():
    [(_, issue)] = payload_ops("issues", {"issue": gh_issue(2, "closed")})
    assert issue["html_url"].endswith("/issues/2") and issue["state_reason"] == "completed"
    [(_, pr)] = payload_ops("pull_request", {"pull_request": gh_pr(1)})
    assert pr["html_url"].endswith("/pull/1")
    [(_, run)] = payload_ops("workflow_run", {"workflow_run": gh_run(1)})
    assert (run["name"], run["display_title"], run["run_attempt"]) == ("Agent", "Run 1", 2)
    assert run["html_url"].endswith("/actions/runs/3001")


def test_backfill_fills_new_fields_and_refills_old_rows():
    s = MemoryStore()
    # a row stored before the new columns existed
    old = workflow_run_record(gh_run(1))
    for k in ("name", "display_title", "html_url", "run_attempt"):
        old[k] = None
    pid = s.ensure_project(REPO)
    s.upsert_workflow_run(pid, old)

    def get(path, params):
        page = params["page"]
        if path.endswith("/issues"):
            return [gh_issue(1)] if page == "1" else []
        if path.endswith("/comments") or path.endswith("/pulls"):
            return [gh_pr(1)] if path.endswith("/pulls") else []
        if path.endswith("agent.yml/runs"):
            return {"workflow_runs": [gh_run(1)]}
        return {"workflow_runs": []}

    bf.backfill(s, get, REPO)
    assert s.tables["issues"][(pid, 1001)]["html_url"].endswith("/issues/1")
    assert s.tables["pull_requests"][(pid, 2001)]["html_url"].endswith("/pull/1")
    run = s.tables["workflow_runs"][(pid, 3001)]
    assert run["name"] == "Agent" and run["run_attempt"] == 2
    assert run["display_title"] == "Run 1" and run["html_url"]
