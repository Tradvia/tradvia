# SPDX-License-Identifier: FSL-1.1-ALv2
import csv
import io
import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import auth  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

A = "Tradvia/tradvia"
B = "Acme/widgets"
ORIGIN = "https://app.test"
TOKEN = "ghu_SECRET_user_token_0123456789"
CODE = "code-from-github"
URL = f"/api/projects/{A}/audit"
T0 = 1_800_000_000.0  # the base of the seeded entries, one minute apart
LEAK = "ghp_" + "a1B2c3D4e5F6g7H8i9J0"


class FakeGitHub:
    def __init__(self):
        self.perm = {A: ("admin", "admin"), B: ("read", "read")}

    def exchange_code(self, code, redirect_uri, code_verifier):
        return TOKEN

    def get_user(self, token):
        return {"id": 7, "login": "ana", "avatar_url": "https://avatars.githubusercontent.com/u/7"}

    def get_permission(self, token, repo, login):
        return self.perm.get(repo, (None, None))

    def get_file(self, token, repo, path, ref):
        return None


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", A)
    monkeypatch.setenv("TRADVIA_PROJECTS", f"{A}, {B}")
    monkeypatch.setenv("TRADVIA_DOMAIN", "app.test")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("TRADVIA_SECRET_KEY", "k" * 43)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def store():
    s = MemoryStore()
    pid = s.ensure_project(A)
    for i in range(5):
        s.append_audit(pid, {
            "actor_login": "ana", "action": "start_card", "issue_number": i or None,
            "result": "ok", "detail": f"n={i}", "idempotency_key": None,
            "created_at": T0 + 60 * i,
        })
    return s


@pytest.fixture
def gh():
    return FakeGitHub()


def make_client(store, gh, signed_in=True):
    app = create_app(store=store, identity=gh, clock=lambda: T0)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    if signed_in:
        r = client.get("/api/auth/login")
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        client.get("/api/auth/callback", params={"code": CODE, "state": state})
    return client


@pytest.fixture
def client(store, gh):
    return make_client(store, gh)


def iso(offset_minutes):
    return datetime.fromtimestamp(T0 + 60 * offset_minutes, timezone.utc).isoformat()


def seed(store, detail, action="note"):
    store.append_audit(store.ensure_project(A), {
        "actor_login": "ana", "action": action, "issue_number": 9, "result": "ok",
        "detail": detail, "idempotency_key": None, "created_at": T0 + 600,
    })


def test_anonymous_is_401(store, gh):
    anon = make_client(store, gh, signed_in=False)
    assert anon.get(URL).status_code == 401


def test_unknown_repo_is_404(client):
    assert client.get("/api/projects/Nobody/nothing/audit").status_code == 404


def test_user_without_a_role_is_refused_like_the_card_audit(store, gh):
    gh.perm[A] = (None, None)
    client = make_client(store, gh)
    card = client.get(f"/api/projects/{A}/cards/8/audit")
    for fmt in ("json", "csv", "jsonl"):
        r = client.get(URL, params={"format": fmt})
        assert r.status_code == card.status_code == 403
        assert r.json() == card.json()


def test_json_is_paged_with_has_more(client):
    r = client.get(URL, params={"limit": 2})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert [e["detail"] for e in data["audit"]] == ["n=0", "n=1"]
    assert (data["limit"], data["offset"], data["has_more"]) == (2, 0, True)
    last = client.get(URL, params={"limit": 2, "offset": 4}).json()
    assert [e["detail"] for e in last["audit"]] == ["n=4"] and last["has_more"] is False
    exact = client.get(URL, params={"limit": 2, "offset": 3}).json()
    assert len(exact["audit"]) == 2 and exact["has_more"] is False
    assert client.get(URL).json()["audit"][0]["issue_number"] is None  # not tied to a card


def test_period_filter_is_from_inclusive_to_exclusive(client):
    r = client.get(URL, params={"from": iso(1), "to": iso(3)})
    assert [e["detail"] for e in r.json()["audit"]] == ["n=1", "n=2"]
    only_from = client.get(URL, params={"from": iso(3)}).json()
    assert [e["detail"] for e in only_from["audit"]] == ["n=3", "n=4"]
    z = iso(2).replace("+00:00", "Z")
    assert [e["detail"] for e in client.get(URL, params={"to": z}).json()["audit"]] == [
        "n=0", "n=1",
    ]


def test_another_project_is_not_listed(store, gh):
    store.append_audit(store.ensure_project(B), {
        "actor_login": "ana", "action": "x", "issue_number": 1, "result": "ok",
        "detail": "other project", "idempotency_key": None, "created_at": T0,
    })
    client = make_client(store, gh)
    assert "other project" not in client.get(URL).text


@pytest.mark.parametrize("params", [
    {"from": "yesterday"}, {"to": "2026-13-45"}, {"limit": "0"}, {"limit": "1001"},
    {"limit": "many"}, {"offset": "-1"}, {"offset": "x"}, {"format": "xml"},
])
def test_invalid_parameters_are_400(client, params):
    r = client.get(URL, params=params)
    assert r.status_code == 400 and "error" in r.json()


def test_csv_and_jsonl_parse_back_to_the_same_rows(client):
    rows = client.get(URL).json()["audit"]
    r = client.get(URL, params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == 'attachment; filename="audit-Tradvia-tradvia.csv"'
    parsed = list(csv.DictReader(io.StringIO(r.text)))
    assert len(parsed) == len(rows) == 5
    for got, want in zip(parsed, rows):
        assert got == {k: "" if v is None else str(v) for k, v in want.items()}

    j = client.get(URL, params={"format": "jsonl"})
    assert j.headers["content-type"].startswith("application/x-ndjson")
    assert j.headers["cache-control"] == "no-store"
    assert j.headers["content-disposition"] == 'attachment; filename="audit-Tradvia-tradvia.jsonl"'
    assert [json.loads(line) for line in j.text.splitlines()] == rows


def test_exports_respect_paging_and_period(client):
    r = client.get(URL, params={"format": "csv", "limit": 2, "offset": 1})
    assert [row["detail"] for row in csv.DictReader(io.StringIO(r.text))] == ["n=1", "n=2"]
    r = client.get(URL, params={"format": "jsonl", "from": iso(4)})
    assert [json.loads(line)["detail"] for line in r.text.splitlines()] == ["n=4"]


def test_a_secret_in_detail_never_leaves_in_any_format(store, gh):
    seed(store, f"token {LEAK} used", action=f"auth {LEAK}")
    client = make_client(store, gh)
    for fmt in ("json", "csv", "jsonl"):
        r = client.get(URL, params={"format": fmt})
        assert r.status_code == 200 and LEAK not in r.text
        assert "***" in r.text
        assert all(LEAK not in v for v in r.headers.values())


def test_a_csv_cell_that_looks_like_a_formula_is_escaped(store, gh):
    seed(store, "=HYPERLINK(\"http://evil\")")
    seed(store, "+1+1")
    seed(store, "@SUM(A1)")
    seed(store, "-2+3")
    client = make_client(store, gh)
    r = client.get(URL, params={"format": "csv"})
    details = [row["detail"] for row in csv.DictReader(io.StringIO(r.text))][5:]
    assert details == ["'=HYPERLINK(\"http://evil\")", "'+1+1", "'@SUM(A1)", "'-2+3"]
    # JSON and JSON Lines keep the value as it is
    raw = [json.loads(line)["detail"] for line in
           client.get(URL, params={"format": "jsonl"}).text.splitlines()][5:]
    assert raw[0] == "=HYPERLINK(\"http://evil\")"
