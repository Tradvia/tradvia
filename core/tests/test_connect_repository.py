# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Connecting a repository from the panel (#210, ADR 0025): projects live in the store."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, B, TOKEN, FakeGitHub, clock, csrf_headers, env, login, make_client, store,
)

from tradvia import api as api_mod  # noqa: E402
from tradvia.auth import ConnectRefused  # noqa: E402

NEW = "Acme/antigravity-app"
URL = "/api/connect/repositories"


class ConnectGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.installed = [
            {"repo": NEW, "private": True, "admin": True},
            {"repo": "Acme/readonly", "private": False, "admin": False},
            {"repo": A, "private": False, "admin": True},
        ]
        self.hooks = []
        self.hook_error = None
        self.list_error = None
        self.perm[NEW] = ("admin", "admin")

    def installation_repos(self, token):
        assert token == TOKEN
        if self.list_error:
            raise ConnectRefused(self.list_error)
        return list(self.installed)

    def ensure_webhook(self, token, repo, url, secret):
        assert token == TOKEN
        if self.hook_error:
            raise ConnectRefused(self.hook_error)
        self.hooks.append((repo, url, secret))
        return "created"


@pytest.fixture
def gh():
    return ConnectGitHub()


@pytest.fixture
def loads(monkeypatch):
    calls = []

    def fake_load(db, get, repo, labels="agent:ready"):
        calls.append((repo, labels))
        return {"issues": 3, "comments": 0, "pull_requests": 1, "workflow_runs": 0}

    monkeypatch.setattr(api_mod, "load_repository", fake_load)
    return calls


@pytest.fixture
def signed(store, gh, clock, monkeypatch, loads):
    monkeypatch.setenv("TRADVIA_WEBHOOK_SECRET", "hook-secret-value")
    c = make_client(store, gh, clock)
    login(c)
    return c


def post(c, body):
    return c.post(URL, json=body, headers=csrf_headers(c))


def test_the_list_shows_the_installed_repositories_and_which_are_served(signed):
    body = signed.get(URL).json()
    by = {r["repo"]: r for r in body["repositories"]}
    assert set(by) == {NEW, "Acme/readonly", A}
    assert by[A]["connected"] is True and by[NEW]["connected"] is False
    assert by["Acme/readonly"]["admin"] is False


def test_an_admin_connects_a_repository_and_it_is_served_at_once(signed, store, gh, loads):
    assert NEW not in [p["external_id"] for p in signed.get("/api/projects").json()]
    assert signed.get(f"/api/projects/{NEW}/settings/policy").status_code == 404
    r = post(signed, {"repo": NEW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"repo": NEW, "connected": True, "webhook": "created", "loading": True, "already": False}
    # The project is in the store, connected by the person, and every project route answers.
    rows = store.connected_projects()
    assert [x["external_id"] for x in rows] == [NEW] and rows[0]["connected_by"] == "ana"
    assert NEW in [p["external_id"] for p in signed.get("/api/projects").json()]
    assert signed.get(f"/api/projects/{NEW}/settings/policy").status_code == 200
    assert signed.get(f"/api/store/repos/{NEW}/backlog").status_code == 200
    # Webhook to this site, with the server's secret; every issue loaded, not only agent:ready.
    assert gh.hooks == [(NEW, "https://app.test/api/webhooks/github", "hook-secret-value")]
    assert loads == [(NEW, None)]
    audit = [x for x in store.audit if x["action"] in ("connect_project", "load_project")]
    assert [x["action"] for x in audit] == ["connect_project", "load_project"]
    assert all(x["result"] == "ok" and x["actor_login"] == "ana" for x in audit)
    assert "hook-secret" not in str(store.audit)


def test_a_connected_project_survives_a_restart(signed, store, gh, clock):
    assert post(signed, {"repo": NEW}).status_code == 200
    fresh = make_client(store, gh, clock)  # a new process reads the connected projects
    login(fresh)
    assert NEW in [p["external_id"] for p in fresh.get("/api/projects").json()]


def test_only_the_repository_admin_connects_it(signed, store):
    r = post(signed, {"repo": "Acme/readonly"})
    assert r.status_code == 403 and "'admin'" in r.json()["error"]
    assert store.connected_projects() == []


def test_a_repository_without_the_app_is_refused_with_the_way_out(signed, store, monkeypatch):
    monkeypatch.setenv("TRADVIA_GITHUB_APP_SLUG", "tradvia")
    r = post(signed, {"repo": "Acme/elsewhere"})
    assert r.status_code == 404 and "not installed" in r.json()["error"]
    assert r.json()["install_url"] == "https://github.com/apps/tradvia/installations/new"
    assert store.connected_projects() == []


def test_a_refused_webhook_keeps_the_project_and_says_what_to_do(signed, store, gh):
    gh.hook_error = "GitHub refused the webhook: the Tradvia GitHub App needs the Webhooks: Read and write permission"
    r = post(signed, {"repo": NEW})
    assert r.status_code == 200
    body = r.json()
    assert body["webhook"] == "failed" and "Webhooks: Read and write" in body["webhook_error"]
    assert [x["external_id"] for x in store.connected_projects()] == [NEW]


def test_connecting_again_keeps_the_first_connection(signed, store, clock):
    assert post(signed, {"repo": NEW}).status_code == 200
    first = store.connected_projects()[0]["connected_at"]
    clock.advance(3600)
    r = post(signed, {"repo": NEW})
    assert r.status_code == 200 and r.json()["already"] is True
    assert store.connected_projects()[0]["connected_at"] == first


@pytest.mark.parametrize("bad", [None, {}, {"repo": 7}, {"repo": "no-slash"}, {"repo": "a/../b"},
                                 {"repo": "a/b/c"}, {"repo": "x" * 101 + "/y"}])
def test_bad_input_is_refused(signed, bad, store):
    r = signed.post(URL, json=bad, headers=csrf_headers(signed))
    assert r.status_code == 400 and store.connected_projects() == []


def test_without_a_session_or_with_an_api_token_nothing_happens(store, gh, clock, loads):
    anon = make_client(store, gh, clock)
    assert anon.get(URL).status_code == 401
    r = anon.post(URL, json={"repo": NEW}, headers={"Authorization": "Bearer tvt_whatever"})
    assert r.status_code in (401, 403) and store.connected_projects() == []


def test_post_needs_csrf(signed, store):
    r = signed.post(URL, json={"repo": NEW})
    assert r.status_code == 403 and store.connected_projects() == []


def test_a_refused_listing_is_explained(signed, gh):
    gh.list_error = "GitHub refused to list the App's installations for your account."
    r = signed.get(URL)
    assert r.status_code == 502 and "installations" in r.json()["error"]


def test_the_environment_list_still_counts(signed):
    served = [p["external_id"] for p in signed.get("/api/projects").json()]
    assert served[:2] == [A, B]


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_http_client_lists_installations_and_creates_the_webhook(monkeypatch):
    from tradvia import auth as auth_mod
    gets, posts = [], []
    pages = {
        "/user/installations?per_page=100": Resp(200, {"installations": [{"id": 5}]}),
        "/user/installations/5/repositories?per_page=100&page=1": Resp(200, {"repositories": [
            {"full_name": NEW, "private": True, "permissions": {"admin": True}}]}),
        f"/repos/{NEW}/hooks?per_page=100": Resp(200, []),
    }

    def fake_get(url, headers, timeout):
        path = url[len(auth_mod.GITHUB_API):]
        gets.append(path)
        return pages[path]

    def fake_post(url, headers, json, timeout):
        posts.append((url, json))
        return Resp(201, {})

    monkeypatch.setattr(auth_mod.requests, "get", fake_get)
    monkeypatch.setattr(auth_mod.requests, "post", fake_post)
    client = auth_mod.HttpGitHub("id", "secret")
    assert client.installation_repos("tok") == [{"repo": NEW, "private": True, "admin": True}]
    assert client.ensure_webhook("tok", NEW, "https://app.test/api/webhooks/github", "s3cr3t") == "created"
    url, body = posts[0]
    assert url.endswith(f"/repos/{NEW}/hooks") and body["config"]["secret"] == "s3cr3t"
    assert set(body["events"]) == {"issues", "issue_comment", "pull_request", "workflow_run"}
    # An existing hook to the same address is reused, not duplicated.
    pages[f"/repos/{NEW}/hooks?per_page=100"] = Resp(200, [{"config": {"url": "https://app.test/api/webhooks/github"}}])
    assert client.ensure_webhook("tok", NEW, "https://app.test/api/webhooks/github", "s3cr3t") == "exists"
    assert len(posts) == 1


def test_http_client_names_the_missing_permission_and_never_echoes_the_secret(monkeypatch):
    from tradvia import auth as auth_mod

    def boom(*a, **k):
        raise RuntimeError("connection failed while sending secret s3cr3t")

    monkeypatch.setattr(auth_mod.requests, "get", lambda url, headers, timeout: Resp(403, {}))
    client = auth_mod.HttpGitHub("id", "secret")
    with pytest.raises(ConnectRefused, match="Webhooks: Read and write"):
        client.ensure_webhook("tok", NEW, "https://x/api/webhooks/github", "s3cr3t")
    monkeypatch.setattr(auth_mod.requests, "get", boom)
    with pytest.raises(ConnectRefused) as e:
        client.ensure_webhook("tok", NEW, "https://x/api/webhooks/github", "s3cr3t")
    assert "s3cr3t" not in str(e.value)
