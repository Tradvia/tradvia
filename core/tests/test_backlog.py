# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import backlog as bl  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

REPO = "Tradvia/tradvia"
OTHER = "Tradvia/other"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.setenv("TRADVIA_PROJECTS", f"{REPO},{OTHER}")
    monkeypatch.delenv("DATABASE_URL", raising=False)


def issue(n, state="open", labels=()):
    return {
        "id": 1000 + n, "number": n, "title": f"Issue {n}", "state": state,
        "labels": list(labels), "author": "ana", "created_at": f"2026-01-{n:02d}T00:00:00Z",
        "updated_at": f"2026-01-{n:02d}T00:00:00Z", "closed_at": None,
        "html_url": None, "state_reason": None,
    }


def pull(n, branch):
    return {
        "id": 2000 + n, "number": n, "title": f"PR {n}", "state": "open", "merged": False,
        "branch": branch, "author": "bot", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "merged_at": None, "html_url": None,
    }


def item(n, deps=()):
    return {"issue_number": n, "depends_on": list(deps), "note": ""}


def test_parse_depends():
    assert bl.parse_depends("Depende do #45 e #44\nx") == [44, 45]
    assert bl.parse_depends("Intro\n- Depends on #7, #3\nDEPENDS ON #3") == [3, 7]
    assert bl.parse_depends("Fala do #9 sem dependência") == []
    assert bl.parse_depends(None) == []


def test_order_preserved_and_replaced():
    store = MemoryStore()
    pid = store.ensure_project(REPO)
    store.set_backlog(pid, [item(46), item(44), item(45)])
    assert [i["issue_number"] for i in store.list_backlog(pid)] == [46, 44, 45]
    assert [i["position"] for i in store.list_backlog(pid)] == [0, 1, 2]
    store.set_backlog(pid, [item(50), item(44)])
    assert [i["issue_number"] for i in store.list_backlog(pid)] == [50, 44]


def test_isolation_between_projects():
    store = MemoryStore()
    a, b = store.ensure_project(REPO), store.ensure_project(OTHER)
    store.set_backlog(a, [item(1), item(2)])
    store.set_backlog(b, [item(9)])
    assert [i["issue_number"] for i in store.list_backlog(a)] == [1, 2]
    assert [i["issue_number"] for i in store.list_backlog(b)] == [9]


def test_status_cases():
    deps = {1: issue(1, "closed"), 2: issue(2)}
    assert bl.backlog_status(item(5), issue(5, "closed"), [], deps) == "concluído"
    assert bl.backlog_status(item(5), issue(5, labels=["agent:ready"]), [], deps) == "em andamento"
    prs = [pull(8, "tradvia/issue-5")]
    assert bl.backlog_status(item(5), issue(5), prs, deps) == "em andamento"
    assert bl.backlog_status(item(5, [1, 2]), issue(5), [], deps) == "bloqueado"
    assert bl.backlog_status(item(5, [3]), issue(5), [], deps) == "bloqueado"
    assert bl.backlog_status(item(5, [1]), issue(5), [], deps) == "pronto"


def test_api_backlog():
    store = MemoryStore()
    pid = store.ensure_project(REPO)
    other = store.ensure_project(OTHER)
    store.upsert_issue(pid, issue(1, "closed"))
    store.upsert_issue(pid, issue(2))
    store.upsert_issue(pid, issue(3, labels=["agent:ready"]))
    store.set_backlog(pid, [item(2, [1]), item(3), item(4, [3])])
    store.set_backlog(other, [item(99)])
    client = TestClient(create_app(store=store))
    r = client.get(f"/api/store/repos/{REPO}/backlog")
    assert r.status_code == 200
    body = r.json()
    assert [i["issue_number"] for i in body] == [2, 3, 4]
    assert [i["status"] for i in body] == ["pronto", "em andamento", "bloqueado"]
    assert body[0]["title"] == "Issue 2" and body[0]["depends_on"] == [1]
    assert body[2]["title"] is None and body[2]["labels"] == []


def test_api_unregistered_and_no_database():
    client = TestClient(create_app(store=MemoryStore()))
    assert client.get("/api/store/repos/Evil/repo/backlog").status_code == 404
    assert client.get(f"/api/store/repos/{REPO}/backlog/x").status_code == 404
    assert TestClient(create_app()).get(f"/api/store/repos/{REPO}/backlog").status_code == 503


def test_cli_set_and_show(capsys):
    store = MemoryStore()
    bodies = {44: "x", 45: "Depende do #44", 46: "Depends on #44, #45"}

    def get(path, params):
        return {"body": bodies[int(path.rsplit("/", 1)[1])]}

    bl.main(store=store, get=get, argv=["set", "--project", REPO, "46", "44", "45"])
    pid = store.ensure_project(REPO)
    assert [(i["issue_number"], i["depends_on"]) for i in store.list_backlog(pid)] == [
        (46, [44, 45]), (44, []), (45, [44]),
    ]
    bl.main(store=store, argv=["show", "--project", REPO])
    assert "#46" in capsys.readouterr().out


@pytest.mark.parametrize("numbers", [["44", "44"], ["abc"], ["-1"]])
def test_cli_rejects_bad_numbers(numbers):
    with pytest.raises(SystemExit):
        bl.main(store=MemoryStore(), get=lambda p, q: {}, argv=["set", "--project", REPO, *numbers])
