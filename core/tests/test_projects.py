# SPDX-License-Identifier: FSL-1.1-ALv2
import hashlib
import hmac
import json
import os
import re
from importlib import resources
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

import tradvia.backfill as bf  # noqa: E402
import tradvia.store as store_module  # noqa: E402
from tradvia.api import create_app, registered_projects  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

A = "Tradvia/tradvia"
B = "Acme/widgets"
SECRET = "whsec_projects"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", A)
    monkeypatch.setenv("TRADVIA_PROJECTS", f"{A}, {B}")
    monkeypatch.setenv("TRADVIA_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def post(client, payload, event="issues", delivery="d1"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                 "X-Hub-Signature-256": sig},
    )


def issue_payload(repo, title, number=1):
    return {
        "action": "opened",
        "repository": {"full_name": repo},
        "issue": {
            "id": 1000 + number, "number": number, "title": title, "state": "open",
            "labels": [{"name": "agent:ready"}], "user": {"login": "ana"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "closed_at": None,
        },
    }


def pr_payload(repo, title):
    return {
        "repository": {"full_name": repo},
        "pull_request": {
            "id": 3, "number": 8, "title": title, "state": "open", "merged": False,
            "head": {"ref": "b"}, "user": {"login": "u"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "merged_at": None,
        },
    }


def run_payload(repo, name):
    return {
        "repository": {"full_name": repo},
        "workflow_run": {
            "id": 9, "path": ".github/workflows/agent.yml", "name": name, "event": "issues",
            "status": "completed", "conclusion": "success", "head_branch": "agent/issue-1",
            "run_started_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:05:00Z",
        },
    }


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store))


def base(repo):
    return f"/api/store/repos/{repo}"


# (a) isolation ----------------------------------------------------------------


def test_two_projects_never_see_each_other(client, store):
    # same delivery id, same ids and numbers in both projects
    assert post(client, issue_payload(A, "from A"), delivery="same").status_code == 202
    assert post(client, issue_payload(B, "from B"), delivery="same").status_code == 202
    assert post(client, pr_payload(A, "pr A"), "pull_request", "p-a").status_code == 202
    assert post(client, pr_payload(B, "pr B"), "pull_request", "p-b").status_code == 202
    assert post(client, run_payload(A, "run A"), "workflow_run", "r-a").status_code == 202

    def titles(repo):
        r = client.get(f"{base(repo)}/issues?state=all")
        assert r.status_code == 200
        return [i["title"] for i in r.json()]

    assert titles(A) == ["from A"] and titles(B) == ["from B"]
    assert [p["title"] for p in client.get(f"{base(A)}/pulls?state=all").json()] == ["pr A"]
    assert [p["title"] for p in client.get(f"{base(B)}/pulls?state=all").json()] == ["pr B"]
    runs_a = client.get(f"{base(A)}/actions/workflows/agent.yml/runs")
    assert [r["name"] for r in runs_a.json()["workflow_runs"]] == ["run A"]
    # project B has no runs stored: the store answers 503 and the panel falls back
    assert client.get(f"{base(B)}/actions/workflows/agent.yml/runs").status_code == 503

    projects = client.get("/api/store/health").json()["projects"]
    assert projects[A]["issues"] == 1 and projects[A]["runs"] == 1
    assert projects[B]["issues"] == 1 and projects[B]["runs"] == 0
    assert projects[B]["pull_requests"] == 1


def test_store_reads_are_scoped_in_memory(store):
    a, b = store.ensure_project(A), store.ensure_project(B)
    assert a != b and store.ensure_project(A) == a
    rec = {"id": 1, "number": 1, "title": "t", "state": "open", "labels": [],
           "updated_at": "2026-01-01T00:00:00Z", "created_at": "2026-01-01T00:00:00Z"}
    store.upsert_issue(a, rec)
    assert store.list_issues(b, [], "all", 10) is None
    assert len(store.list_issues(a, [], "all", 10)) == 1
    assert store.stats(b)["issues"] == 0 and store.stats(a)["issues"] == 1


# (b) unregistered repository ------------------------------------------------


def test_webhook_for_unregistered_repo_writes_nothing(client, store):
    r = post(client, issue_payload("evil/repo", "x"))
    assert r.status_code == 202
    assert not store.deliveries and not store.projects
    assert all(not rows for rows in store.tables.values())


# (c) /api/projects and unknown {repo} -----------------------------------------


def test_projects_endpoint_lists_registered(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == [
        {"slug": "tradvia-tradvia", "kind": "github_repo", "external_id": A, "public": True},
        {"slug": "acme-widgets", "kind": "github_repo", "external_id": B, "public": True},
    ]


def test_unregistered_repo_is_404_in_store_and_proxy(client):
    assert client.get("/api/store/repos/Other/repo/issues").status_code == 404
    assert client.get("/api/github/repos/Other/repo/issues").status_code == 404


def test_registered_projects_parsing(monkeypatch):
    monkeypatch.setenv("TRADVIA_PROJECTS", " a/b ,, c/d,a/b ")
    assert registered_projects() == ["a/b", "c/d"]
    monkeypatch.delenv("TRADVIA_PROJECTS")
    monkeypatch.setenv("TRADVIA_REPO", "x/y")
    assert registered_projects() == ["x/y"]


def test_app_construction_does_not_touch_the_store():
    class Exploding(MemoryStore):
        def ensure_project(self, external_id):
            raise AssertionError("touched at construction")

    create_app(store=Exploding())


# (d) backfill by project ---------------------------------------------------


def fake_github(title):
    issue = issue_payload(A, title)["issue"]

    def get(path, params):
        if path.endswith("/issues"):
            return [issue]
        return {"workflow_runs": []} if path.endswith("/runs") else []

    return get


def test_backfill_writes_to_its_own_project(store):
    bf.backfill(store, fake_github("in A"), A)
    bf.backfill(store, fake_github("in B"), B)
    a, b = store.ensure_project(A), store.ensure_project(B)
    assert store.tables["issues"][(a, 1001)]["title"] == "in A"
    assert store.tables["issues"][(b, 1001)]["title"] == "in B"


def test_backfill_project_argument(store):
    counts = bf.main(store=store, get=fake_github("x"), argv=["--project", B])
    assert counts["issues"] == 1
    assert store.stats(store.ensure_project(B))["issues"] == 1
    assert A not in store.projects


def test_backfill_default_project_is_trad_repo(store):
    bf.main(store=store, get=fake_github("x"), argv=[])
    assert list(store.projects) == [A]


def test_backfill_refuses_unregistered_project(store):
    with pytest.raises(SystemExit):
        bf.main(store=store, get=fake_github("x"), argv=["--project", "evil/repo"])
    assert not store.projects


# (e) every query is scoped ------------------------------------------------------

STORED = ("deliveries", "issues", "comments", "pull_requests", "workflow_runs")


def sql_constants():
    src = Path(store_module.__file__).read_text(encoding="utf-8")
    import ast

    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.match(r"\s*(SELECT|UPDATE|DELETE|INSERT)\b", node.value, re.I):
                yield node.value


def test_every_stored_table_query_filters_by_project():
    checked = 0
    for sql in sql_constants():
        if any(re.search(rf"\b{t}\b", sql) for t in STORED):
            assert "project_id" in sql, sql
        checked += 1
    assert checked >= 8


def test_store_methods_require_project_id():
    import inspect

    for cls in (store_module.MemoryStore, store_module.PgStore):
        for name in ("apply", "upsert_issue", "upsert_comment", "upsert_pull_request",
                     "upsert_workflow_run", "stats", "list_issues", "list_pulls", "list_runs"):
            first = list(inspect.signature(getattr(cls, name)).parameters)[1]
            assert first == "project_id", (cls.__name__, name)


# (f) schema migration ---------------------------------------------------------


def schema_text():
    return resources.files("tradvia").joinpath("schema.sql").read_text(encoding="utf-8")


def test_schema_has_idempotent_project_migration():
    sql = schema_text()
    assert re.search(r"CREATE TABLE IF NOT EXISTS projects\b", sql)
    assert "ON CONFLICT DO NOTHING" in sql and "'Tradvia/tradvia'" in sql
    for table in STORED:
        assert re.search(
            rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES",
            sql)
        assert re.search(rf"UPDATE {table} SET project_id = .*?WHERE project_id IS NULL",
                         sql, re.S)
        assert re.search(rf"ALTER TABLE {table} ALTER COLUMN project_id SET NOT NULL", sql)
    assert "ADD PRIMARY KEY (project_id, id)" in sql
    assert "indnatts = 2" in sql  # composite key is only added when not there yet
    assert "ON (project_id" not in sql
    assert re.search(r"INDEX IF NOT EXISTS comments_project_issue_idx ON comments \(project_id", sql)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs a Postgres")
def test_schema_runs_twice_on_postgres():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(store_module.load_schema())
        conn.execute(store_module.load_schema())
        n = conn.execute(
            "SELECT count(*) FROM projects WHERE external_id = 'Tradvia/tradvia'"
        ).fetchone()[0]
    assert n == 1
