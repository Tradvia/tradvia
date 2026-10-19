# SPDX-License-Identifier: FSL-1.1-ALv2
import ast
import hashlib
import hmac
import json
import logging
import re
from importlib import resources
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

import tradvia.api as api_module  # noqa: E402
from tradvia.api import MAX_WEBHOOK_BYTES, Upstream, create_app  # noqa: E402
from tradvia.backfill import backfill  # noqa: E402
from tradvia.events import payload_ops  # noqa: E402
from tradvia.store import MemoryStore, StoreUnavailable  # noqa: E402

REPO = "Tradvia/tradvia"
SECRET = "whsec_topsecret"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.setenv("TRADVIA_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def issue_payload(number=1, updated="2026-01-01T00:00:00Z", title="t", repo=REPO):
    return {
        "action": "opened",
        "repository": {"full_name": repo},
        "issue": {
            "id": 1000 + number, "number": number, "title": title, "state": "open",
            "labels": [{"name": "agent:ready"}], "user": {"login": "ana"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": updated, "closed_at": None,
        },
    }


def post(client, payload, event="issues", delivery="d1", secret=SECRET, headers=None):
    body = json.dumps(payload).encode()
    h = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
         "X-Hub-Signature-256": sign(body, secret)}
    h.update(headers or {})
    return client.post("/api/webhooks/github", content=body, headers=h)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store))


def test_valid_signature_stores(client, store):
    r = post(client, issue_payload())
    assert r.status_code == 202
    assert store.stats()["issues"] == 1
    assert store.tables["issues"][1001]["labels"] == ["agent:ready"]


@pytest.mark.parametrize("sig", ["sha256=" + "0" * 64, "garbage", None])
def test_bad_or_missing_signature_is_401(client, store, sig):
    body = json.dumps(issue_payload()).encode()
    headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d1"}
    if sig:
        headers["X-Hub-Signature-256"] = sig
    r = client.post("/api/webhooks/github", content=body, headers=headers)
    assert r.status_code == 401
    assert store.stats()["issues"] == 0 and not store.deliveries


def test_wrong_secret_is_401(client, store):
    assert post(client, issue_payload(), secret="other").status_code == 401
    assert not store.deliveries


def test_no_secret_configured_fails_closed(monkeypatch, store):
    monkeypatch.delenv("TRADVIA_WEBHOOK_SECRET")
    c = TestClient(create_app(store=store))
    assert post(c, issue_payload(), secret="").status_code == 401
    assert not store.deliveries


def test_uses_compare_digest(client, monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(api_module.hmac, "compare_digest", spy)
    post(client, issue_payload())
    assert len(calls) == 1


def test_duplicate_delivery_does_not_duplicate(client, store):
    assert post(client, issue_payload(), delivery="same").status_code == 202
    assert post(client, issue_payload(title="changed"), delivery="same").status_code == 202
    assert len(store.deliveries) == 1
    assert store.stats()["issues"] == 1
    assert store.tables["issues"][1001]["title"] == "t"


def test_other_repository_ignored(client, store):
    assert post(client, issue_payload(repo="evil/repo")).status_code == 202
    assert not store.deliveries and store.stats()["issues"] == 0


def test_unhandled_event_and_ping_ignored(client, store):
    assert post(client, issue_payload(), event="push").status_code == 202
    assert post(client, {"zen": "x", "repository": {"full_name": REPO}}, event="ping").status_code == 202
    assert not store.deliveries


def test_oversize_body_is_413(client, store):
    body = b"x" * (MAX_WEBHOOK_BYTES + 1)
    r = client.post("/api/webhooks/github", content=body,
                    headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d",
                             "X-Hub-Signature-256": sign(body)})
    assert r.status_code == 413
    assert not store.deliveries


def test_invalid_json_and_payload_are_400(client):
    body = b"not json"
    r = client.post("/api/webhooks/github", content=body,
                    headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d",
                             "X-Hub-Signature-256": sign(body)})
    assert r.status_code == 400
    bad = {"repository": {"full_name": REPO}}
    assert post(client, bad).status_code == 400


def test_upsert_respects_updated_at(client, store):
    post(client, issue_payload(updated="2026-02-01T00:00:00Z", title="new"), delivery="a")
    post(client, issue_payload(updated="2026-01-01T00:00:00Z", title="old"), delivery="b")
    assert store.tables["issues"][1001]["title"] == "new"
    post(client, issue_payload(updated="2026-03-01T00:00:00Z", title="newer"), delivery="c")
    assert store.tables["issues"][1001]["title"] == "newer"


def test_comment_and_run_and_pr_events(client, store):
    comment = {
        "repository": {"full_name": REPO},
        "issue": issue_payload(5)["issue"],
        "comment": {"id": 77, "user": {"login": "bob"}, "author_association": "MEMBER",
                    "body": "hi", "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z"},
    }
    assert post(client, comment, "issue_comment", "c1").status_code == 202
    assert store.tables["comments"][77]["author_association"] == "MEMBER"
    assert store.tables["comments"][77]["issue_number"] == 5

    run = {"repository": {"full_name": REPO}, "workflow_run": {
        "id": 9, "path": ".github/workflows/agent.yml", "event": "issues",
        "status": "completed", "conclusion": "success", "head_branch": "agent/issue-5",
        "run_started_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:05:00Z"}}
    assert post(client, run, "workflow_run", "r1").status_code == 202
    assert store.tables["workflow_runs"][9]["issue_number"] == 5
    assert store.stats()["runs"] == 1

    pr = {"repository": {"full_name": REPO}, "pull_request": {
        "id": 3, "number": 8, "title": "p", "state": "closed", "merged": True,
        "head": {"ref": "agent/issue-5"}, "user": {"login": "bot"},
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
        "merged_at": "2026-01-02T00:00:00Z"}}
    assert post(client, pr, "pull_request", "p1").status_code == 202
    assert store.tables["pull_requests"][3]["merged"] is True


def test_health_has_no_comment_bodies(client, store):
    comment = {"repository": {"full_name": REPO}, "issue": issue_payload(5)["issue"],
               "comment": {"id": 77, "body": "private words", "updated_at": "2026-01-01T00:00:00Z"}}
    post(client, comment, "issue_comment", "c1")
    r = client.get("/api/store/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "issues": 1, "runs": 0, "last_delivery": "c1"}
    assert "private words" not in r.text


def test_secret_never_in_response_or_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    responses = [
        post(client, issue_payload()),
        post(client, issue_payload(), secret="other"),
        client.get("/api/store/health"),
    ]
    for r in responses:
        assert SECRET not in r.text and SECRET not in str(r.headers)
    assert SECRET not in caplog.text


class FailingStore(MemoryStore):
    """Fails in the middle of the upserts, after writing the first one."""

    fail = True

    def _upsert(self, kind, rec):
        if self.fail and kind == "comment":
            raise RuntimeError(f"boom {SECRET}")
        super()._upsert(kind, rec)


def test_failure_mid_apply_rolls_back_and_retry_is_processed(caplog):
    store = FailingStore()
    c = TestClient(create_app(store=store), raise_server_exceptions=False)
    payload = {"repository": {"full_name": REPO}, "issue": issue_payload(5)["issue"],
               "comment": {"id": 77, "updated_at": "2026-01-01T00:00:00Z"}}
    r = post(c, payload, "issue_comment", "same")
    assert r.status_code == 500
    assert SECRET not in r.text and SECRET not in caplog.text
    assert not store.deliveries
    assert store.stats()["issues"] == 0

    store.fail = False
    assert post(c, payload, "issue_comment", "same").status_code == 202
    assert "same" in store.deliveries
    assert store.stats()["issues"] == 1 and 77 in store.tables["comments"]


class DownStore(MemoryStore):
    def apply(self, *a, **k):
        raise StoreUnavailable("down")

    def stats(self):
        raise StoreUnavailable("down")


def test_store_unavailable_is_503():
    c = TestClient(create_app(store=DownStore()))
    assert post(c, issue_payload()).status_code == 503
    r = c.get("/api/store/health")
    assert r.status_code == 503 and r.json() == {"ok": False}


def test_no_store_configured_is_503():
    c = TestClient(create_app())
    assert post(c, issue_payload()).status_code == 503


def test_pgstore_driver_imports_resolve():
    """Every name PgStore imports from psycopg/psycopg_pool must exist (even lazy imports)."""
    import importlib

    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    import tradvia.store as store_module

    tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {
            "psycopg",
            "psycopg_pool",
        }:
            module = importlib.import_module(node.module)
            for alias in node.names:
                assert hasattr(module, alias.name), f"{node.module}.{alias.name} does not exist"
                checked += 1
    assert checked >= 3
    from psycopg_pool import PoolClosed, PoolTimeout  # noqa: F401


def test_unreachable_database_does_not_break_the_proxy(monkeypatch):
    pytest.importorskip("psycopg_pool")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return Upstream(200, None, "application/json", b"[]")

    c = TestClient(create_app(fetch=fetch))
    assert c.get("/api/healthz").status_code == 200
    assert c.get(f"/api/github/repos/{REPO}/issues").status_code == 200
    assert calls
    assert c.get("/api/store/health").status_code == 503
    assert post(c, issue_payload()).status_code == 503


# --- backfill -------------------------------------------------------------


def fake_github():
    issue = issue_payload(1)["issue"]
    pr_as_issue = {**issue_payload(2)["issue"], "pull_request": {}}
    comment = {"id": 5, "user": {"login": "x"}, "author_association": "OWNER", "body": "b",
               "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
    pr = {"id": 3, "number": 8, "title": "p", "state": "open", "head": {"ref": "b"},
          "user": {"login": "u"}, "created_at": "2026-01-01T00:00:00Z",
          "updated_at": "2026-01-01T00:00:00Z", "merged_at": None}
    run = {"id": 9, "path": ".github/workflows/agent.yml", "status": "queued",
           "updated_at": "2026-01-01T00:00:00Z"}

    def get(path, params):
        if path.endswith("/issues"):
            assert params["labels"] == "agent:ready"
            return [issue, pr_as_issue]
        if path.endswith("/comments"):
            return [comment]
        if path.endswith("/pulls"):
            return [pr]
        if path.endswith("agent.yml/runs"):
            return {"workflow_runs": [run]}
        if path.endswith("claude.yml/runs"):
            return {"workflow_runs": [{**run, "id": 10, "path": ".github/workflows/claude.yml"}]}
        raise AssertionError(path)

    return get


def test_backfill_is_idempotent():
    store = MemoryStore()
    first = backfill(store, fake_github(), REPO)
    snapshot = {t: dict(rows) for t, rows in store.tables.items()}
    second = backfill(store, fake_github(), REPO)
    assert first == second == {"issues": 1, "comments": 1, "pull_requests": 1, "workflow_runs": 2}
    assert store.stats()["issues"] == 1 and store.stats()["runs"] == 2
    assert {t: dict(rows) for t, rows in store.tables.items()} == snapshot


def test_payload_ops_unhandled_event_is_empty():
    assert payload_ops("push", {}) == []


# --- schema and SQL hygiene (text analysis only, no database) ---------------


def schema_text():
    return resources.files("tradvia").joinpath("schema.sql").read_text(encoding="utf-8")


def test_schema_tables_and_idempotence():
    sql = schema_text()
    for table in ("deliveries", "issues", "comments", "pull_requests", "workflow_runs"):
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", sql)
    creates = re.findall(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\b[^;]*", sql, re.I)
    assert creates and all(re.match(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+IF NOT EXISTS", c, re.I)
                           for c in creates)
    assert not re.search(r"\bDROP\b", sql, re.I)


def test_store_builds_no_sql_from_strings():
    src = Path(api_module.__file__).with_name("store.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
            for side in (n.left, n.right):
                assert not (isinstance(side, ast.Constant) and isinstance(side.value, str))
