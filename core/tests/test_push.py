# SPDX-License-Identifier: FSL-1.1-ALv2
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import push  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.push import PushError  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

REPO = "Tradvia/tradvia"
SLUG = "tradvia-tradvia"
ORIGIN = "https://app.test"
SECRET = "whsec_topsecret"
AGENT = "github-actions[bot]"
PLAN = "Plano secreto: reescrever o módulo de pagamentos\nResponda **Aprovado**"
STAMP = "2026-01-01T00:00:00Z"
URL = {u: f"https://push.example.com/send/{u}" for u in ("ana", "bob", "eve")}
KEYS = {"p256dh": "BPubKey_0123-abc", "auth": "authSecret-123"}


class FakeGitHub:
    """One fake per test: the login code is the user's name, the token is `tok-<name>`."""

    def __init__(self):
        self.perm = {"ana": "admin", "bob": "triage", "eve": None}

    def exchange_code(self, code, redirect_uri, code_verifier):
        return "tok-" + code

    def get_user(self, token):
        name = token[4:]
        return {"id": len(name), "login": name, "avatar_url": None}

    def get_permission(self, token, repo, login):
        role = self.perm.get(login)
        return (role, role)

    def get_file(self, token, repo, path, ref):
        return None  # default policy


class FakeSender:
    def __init__(self):
        self.sent = []  # (endpoint, payload)
        self.fail = {}  # endpoint -> status

    def send(self, sub, payload):
        if sub["endpoint"] in self.fail:
            raise PushError(self.fail[sub["endpoint"]])
        self.sent.append((sub["endpoint"], payload))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.setenv("TRADVIA_PROJECTS", REPO)
    monkeypatch.setenv("TRADVIA_DOMAIN", "app.test")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("TRADVIA_SECRET_KEY", "k" * 43)
    monkeypatch.setenv("TRADVIA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TRADVIA_VAPID_PUBLIC_KEY", "BPublicKey")
    monkeypatch.setenv("TRADVIA_VAPID_PRIVATE_KEY", "private-never-shown")
    monkeypatch.delenv("TRADVIA_AGENT_LOGIN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def app(store, gh, sender):
    return create_app(store=store, identity=gh, push_sender=sender)


def login(app, name):
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    r = client.get("/api/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert client.get("/api/auth/callback", params={"code": name, "state": state}).status_code == 302
    return client


def headers(client):
    return {"Origin": ORIGIN, "X-Tradvia-CSRF": client.get("/api/auth/me").json()["csrf"]}


def sub_body(name):
    return {"endpoint": URL[name], "keys": KEYS}


def subscribe(client, name):
    return client.post(f"/api/projects/{REPO}/push/subscription", json=sub_body(name),
                       headers=headers(client))


def webhook(app, event, payload, delivery):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    h = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sig}
    return TestClient(app).post("/api/webhooks/github", content=body, headers=h)


def issue_event(number=5, state="open"):
    return {"action": "opened", "repository": {"full_name": REPO}, "issue": {
        "id": 1000 + number, "number": number, "title": "Dado sensível no título", "state": state,
        "labels": [], "user": {"login": "ana"}, "created_at": STAMP, "updated_at": STAMP,
    }}


def comment_event(body, author=AGENT, number=5, cid=1):
    payload = issue_event(number)
    payload["action"] = "created"
    payload["comment"] = {"id": cid, "body": body, "user": {"login": author},
                          "created_at": STAMP, "updated_at": STAMP}
    return payload


def pr_event(number=5, state="open"):
    return {"action": "opened", "repository": {"full_name": REPO}, "pull_request": {
        "id": 77, "number": 50, "title": "PR com título sensível", "state": state,
        "head": {"ref": f"tradvia/issue-{number}"}, "user": {"login": AGENT},
        "created_at": STAMP, "updated_at": STAMP, "body": "corpo secreto",
    }}


def two_subscribers(app):
    ana, bob = login(app, "ana"), login(app, "bob")
    assert subscribe(ana, "ana").status_code == 200
    assert subscribe(bob, "bob").status_code == 200
    assert webhook(app, "issues", issue_event(), "seed").status_code == 202  # card 5 exists
    return ana, bob


def test_plan_comment_pushes_to_who_can_answer(app, sender):
    # approve_plan needs write: ana (admin) is notified, bob (triage) is not.
    two_subscribers(app)
    assert webhook(app, "issues", issue_event(), "d0").status_code == 202
    assert sender.sent == []
    assert webhook(app, "issue_comment", comment_event(PLAN), "d1").status_code == 202
    assert sender.sent == [(URL["ana"], {"project": SLUG, "card": 5, "kind": "plan_ready"})]


def test_payload_has_only_project_card_and_kind(app, sender):
    two_subscribers(app)
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    (_, payload), = sender.sent
    assert set(payload) == {"project", "card", "kind"}
    text = json.dumps(payload)
    assert "secreto" not in text and "sensível" not in text and "pagamentos" not in text
    assert push.payload("p", 3, "pr_opened") == {"project": "p", "card": 3, "kind": "pr_opened"}


def test_pr_opened_needs_the_merge_role(app, store, gh, sender):
    gh.perm["bob"] = "maintain"
    two_subscribers(app)
    webhook(app, "pull_request", pr_event(), "d1")
    # both ana (admin) and bob (maintain) may merge
    assert sorted(e for e, p in sender.sent) == [URL["ana"], URL["bob"]]
    assert {p["kind"] for _, p in sender.sent} == {"pr_opened"}


def test_triage_user_gets_no_merge_push(app, sender):
    two_subscribers(app)
    webhook(app, "pull_request", pr_event(), "d1")
    assert [e for e, _ in sender.sent] == [URL["ana"]]


def test_no_push_for_events_that_need_no_human(app, sender):
    two_subscribers(app)
    webhook(app, "issue_comment", comment_event("Executando…"), "d1")
    webhook(app, "issue_comment", comment_event("oi", author="ana", cid=2), "d2")
    webhook(app, "pull_request", pr_event(state="closed"), "d3")
    assert sender.sent == []


def test_no_push_when_the_card_was_already_pending(app, sender):
    two_subscribers(app)
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    assert len(sender.sent) == 1
    webhook(app, "issues", issue_event(), "d2")  # unrelated update of the same card
    assert len(sender.sent) == 1


def test_duplicate_delivery_sends_once(app, sender):
    two_subscribers(app)
    assert webhook(app, "issue_comment", comment_event(PLAN), "same").status_code == 202
    assert webhook(app, "issue_comment", comment_event(PLAN), "same").status_code == 202
    assert len(sender.sent) == 1


def test_expired_subscription_is_removed(app, store, sender):
    two_subscribers(app)
    sender.fail[URL["ana"]] = 410
    assert webhook(app, "issue_comment", comment_event(PLAN), "d1").status_code == 202
    assert [s["login"] for s in store.list_subscriptions(1)] == ["bob"]


def test_404_also_removes_it_and_other_errors_keep_it(app, store, sender):
    ana, _ = two_subscribers(app)
    sender.fail[URL["ana"]] = 500
    assert webhook(app, "issue_comment", comment_event(PLAN), "d1").status_code == 202
    assert len(store.list_subscriptions(1)) == 2
    sender.fail[URL["ana"]] = 404
    webhook(app, "issue_comment", comment_event("Executando", cid=2), "d2")
    webhook(app, "issue_comment", comment_event(PLAN, cid=3), "d3")
    assert [s["login"] for s in store.list_subscriptions(1)] == ["bob"]


def test_sender_crash_never_changes_the_202(app, sender):
    two_subscribers(app)

    def boom(sub, payload):
        raise RuntimeError(sub["endpoint"])

    sender.send = boom
    assert webhook(app, "issue_comment", comment_event(PLAN), "d1").status_code == 202


def test_disabled_preference_is_respected(app, sender):
    ana, _ = two_subscribers(app)
    r = ana.put(f"/api/projects/{REPO}/push/prefs", json={"kinds": {"plan_ready": False}},
                headers=headers(ana))
    assert r.status_code == 200 and r.json()["kinds"]["plan_ready"] is False
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    assert sender.sent == []
    webhook(app, "pull_request", pr_event(), "d2")  # pr_opened is still on
    assert [(e, p["kind"]) for e, p in sender.sent] == [(URL["ana"], "pr_opened")]


def test_enabled_preference_is_sent(app, sender):
    ana, _ = two_subscribers(app)
    ana.put(f"/api/projects/{REPO}/push/prefs", json={"kinds": {"plan_ready": False}},
            headers=headers(ana))
    ana.put(f"/api/projects/{REPO}/push/prefs", json={"kinds": {"plan_ready": True}},
            headers=headers(ana))
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    assert [e for e, _ in sender.sent] == [URL["ana"]]


def test_user_without_a_role_gets_no_push(app, store, sender):
    eve = login(app, "eve")
    assert subscribe(eve, "eve").status_code == 403  # no role: cannot even subscribe
    store.save_subscription(1, "eve", URL["eve"], "k", "a")  # even a stale row gets nothing
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    assert sender.sent == []


def test_subscriber_without_a_live_session_gets_no_push(app, store, sender):
    store.ensure_project(REPO)
    store.save_subscription(1, "ghost", "https://push.example.com/send/ghost", "k", "a")
    webhook(app, "issue_comment", comment_event(PLAN), "d1")
    assert sender.sent == []


def test_prefs_default_to_all_on_and_reject_unknown_kinds(app):
    ana = login(app, "ana")
    r = ana.get(f"/api/projects/{REPO}/push/prefs")
    assert r.json()["kinds"] == {k: True for k in (
        "plan_ready", "agent_question", "pr_opened", "budget_limit", "adjust_limit")}
    for bad in ({"kinds": {"title": True}}, {"kinds": {"plan_ready": "no"}}, {"kinds": []}, {}):
        out = ana.put(f"/api/projects/{REPO}/push/prefs", json=bad, headers=headers(ana))
        assert out.status_code == 400


# subscription routes -------------------------------------------------------------


def test_subscribe_and_unsubscribe(app, store):
    ana = login(app, "ana")
    assert subscribe(ana, "ana").status_code == 200
    assert store.list_subscriptions(1) == [{"login": "ana", "endpoint": URL["ana"], **KEYS}]
    out = ana.request("DELETE", f"/api/projects/{REPO}/push/subscription",
                      json={"endpoint": URL["ana"]}, headers=headers(ana))
    assert out.status_code == 200 and store.list_subscriptions(1) == []


def test_unsubscribe_only_removes_your_own(app, store):
    ana, bob = two_subscribers(app)
    out = bob.request("DELETE", f"/api/projects/{REPO}/push/subscription",
                      json={"endpoint": URL["ana"]}, headers=headers(bob))
    assert out.status_code == 200
    assert sorted(s["login"] for s in store.list_subscriptions(1)) == ["ana", "bob"]


def test_a_new_endpoint_replaces_the_old_one(app, store):
    ana = login(app, "ana")
    subscribe(ana, "ana")
    body = {"endpoint": "https://push.example.com/send/ana-new", "keys": KEYS}
    ana.post(f"/api/projects/{REPO}/push/subscription", json=body, headers=headers(ana))
    assert [s["endpoint"] for s in store.list_subscriptions(1)] == [body["endpoint"]]


def test_mutations_need_the_csrf_header_and_origin(app, store):
    ana = login(app, "ana")
    url = f"/api/projects/{REPO}/push/subscription"
    assert ana.post(url, json=sub_body("ana"), headers={"Origin": ORIGIN}).status_code == 403
    assert ana.post(url, json=sub_body("ana"),
                    headers={"X-Tradvia-CSRF": headers(ana)["X-Tradvia-CSRF"]}).status_code == 403
    assert ana.put(f"/api/projects/{REPO}/push/prefs", json={"kinds": {}},
                   headers={"Origin": ORIGIN}).status_code == 403
    assert store.list_subscriptions(1) == []


def test_anonymous_gets_401(app):
    anon = TestClient(app, base_url=ORIGIN)
    assert anon.get(f"/api/projects/{REPO}/pending").status_code == 401
    assert anon.post(f"/api/projects/{REPO}/push/subscription", json=sub_body("ana")).status_code == 401
    assert anon.get(f"/api/projects/{REPO}/push/prefs").status_code == 401


@pytest.mark.parametrize("endpoint", [
    "http://push.example.com/x", "https://127.0.0.1/x", "https://localhost/x",
    "https://169.254.169.254/latest", "https://user:pw@push.example.com/x", "https://intranet/x",
    "javascript:alert(1)", "https://push.example.com/" + "a" * 3000, 7,
])
def test_endpoint_must_be_a_public_https_url(app, store, endpoint):
    ana = login(app, "ana")
    out = ana.post(f"/api/projects/{REPO}/push/subscription", json={"endpoint": endpoint, "keys": KEYS},
                   headers=headers(ana))
    assert out.status_code == 400 and store.list_subscriptions(1) == []


def test_subscription_keys_are_validated(app):
    ana = login(app, "ana")
    for keys in ({}, {"p256dh": "x y", "auth": "a"}, {"p256dh": "k"}, {"p256dh": "k", "auth": 3}):
        out = ana.post(f"/api/projects/{REPO}/push/subscription",
                       json={"endpoint": URL["ana"], "keys": keys}, headers=headers(ana))
        assert out.status_code == 400


def test_push_key_route_and_off_switch(store, gh, monkeypatch):
    on = create_app(store=store, identity=gh, push_sender=FakeSender())
    r = TestClient(on).get("/api/push/key")
    assert r.json() == {"key": "BPublicKey"} and "private-never-shown" not in r.text
    monkeypatch.delenv("TRADVIA_VAPID_PRIVATE_KEY")
    off = create_app(store=store, identity=gh)
    assert TestClient(off).get("/api/push/key").json() == {"key": None}
    assert subscribe(login(off, "ana"), "ana").status_code == 404


def test_default_sender_needs_both_keys(monkeypatch):
    assert isinstance(push.default_sender(), push.WebPushSender)
    assert "private-never-shown" not in repr(push.default_sender())
    monkeypatch.delenv("TRADVIA_VAPID_PUBLIC_KEY")
    assert push.default_sender() is None and push.public_key() is None


# pending inbox -------------------------------------------------------------------


def seed_pending(app):
    webhook(app, "issue_comment", comment_event(PLAN), "p1")
    webhook(app, "pull_request", pr_event(number=6), "p2")  # a PR for card 6
    webhook(app, "issues", issue_event(number=6), "p3")


def test_pending_route_lists_by_role(app, gh):
    seed_pending(app)
    ana, bob = login(app, "ana"), login(app, "bob")
    out = ana.get(f"/api/projects/{REPO}/pending").json()
    assert out["count"] == 2
    assert [(i["number"], i["kind"]) for i in out["items"]] == [(5, "plan_ready"), (6, "pr_opened")]
    assert out["items"][0]["link"] == f"/runs/?repo={REPO}&card=5"
    assert bob.get(f"/api/projects/{REPO}/pending").json() == {"count": 0, "items": []}


def test_pending_count_is_in_the_me_payload(app):
    seed_pending(app)
    ana, bob = login(app, "ana"), login(app, "bob")
    assert ana.get("/api/auth/me").json()["projects"][0]["pending_count"] == 2
    assert bob.get("/api/auth/me").json()["projects"][0]["pending_count"] == 0


def test_user_without_a_role_has_an_empty_inbox(app):
    seed_pending(app)
    eve = login(app, "eve")
    assert eve.get(f"/api/projects/{REPO}/pending").json() == {"count": 0, "items": []}
    assert eve.get("/api/auth/me").json()["projects"][0]["pending_count"] == 0


def test_unknown_project_is_404(app):
    ana = login(app, "ana")
    assert ana.get("/api/projects/Other/repo/pending").status_code == 404
