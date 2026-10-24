# SPDX-License-Identifier: FSL-1.1-ALv2
import hashlib
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import auth  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.actions import GitHubError  # noqa: E402
from tradvia.auth import AuthError, PolicyUnreadable, safe_return_path  # noqa: E402
from tradvia.policy import role_from_github  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

A = "Tradvia/tradvia"
B = "Acme/widgets"
DOMAIN = "app.test"
ORIGIN = "https://app.test"
TOKEN = "ghu_SECRET_user_token_0123456789"
CODE = "code-from-github"
SESSION = auth.SESSION_COOKIE


class FakeGitHub:
    def __init__(self):
        self.perm = {A: ("admin", "admin"), B: ("read", "read")}
        self.perm_calls = 0
        self.exchanges = []
        self.calls = []
        self.policy = None  # .tradvia.yml text, None (404) or an exception to raise
        self.issues = {7: {"number": 7, "state": "open", "labels": []}}
        self.comments = {}
        self.next_number = 100

    def exchange_code(self, code, redirect_uri, code_verifier):
        self.exchanges.append((code, redirect_uri, code_verifier))
        if code != CODE:
            raise AuthError("github refused the login", 502)
        return TOKEN

    def get_user(self, token):
        assert token == TOKEN
        return {"id": 7, "login": "ana", "avatar_url": "https://avatars.githubusercontent.com/u/7"}

    def get_permission(self, token, repo, login):
        assert token == TOKEN and login == "ana"
        self.perm_calls += 1
        return self.perm.get(repo, (None, None))

    # policy file and panel actions: every call records the token it was made with
    def get_file(self, token, repo, path, ref):
        self.calls.append(("get_file", token, repo, path, ref))
        if isinstance(self.policy, Exception):
            raise self.policy
        return self.policy

    def get_issue(self, token, repo, number):
        self.calls.append(("get_issue", token, repo, number))
        if number not in self.issues:
            raise GitHubError(404)
        return self.issues[number]

    def create_issue(self, token, repo, title, body):
        self.calls.append(("create_issue", token, repo, title, body))
        self.next_number += 1
        issue = {
            "id": 1000 + self.next_number, "number": self.next_number, "title": title,
            "body": body, "state": "open", "labels": [], "user": {"login": "ana"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "html_url": f"https://github.com/{repo}/issues/{self.next_number}",
        }
        self.issues[self.next_number] = issue
        return issue

    def add_label(self, token, repo, number, label):
        self.calls.append(("add_label", token, repo, number, label))

    def comment(self, token, repo, number, body):
        self.calls.append(("comment", token, repo, number, body))

    def list_comments(self, token, repo, number):
        self.calls.append(("list_comments", token, repo, number))
        if number not in self.issues:
            raise GitHubError(404)
        return self.comments.get(number, [])

    def writes(self, kind):
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", A)
    monkeypatch.setenv("TRADVIA_PROJECTS", f"{A}, {B}")
    monkeypatch.setenv("TRADVIA_DOMAIN", DOMAIN)
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("TRADVIA_SECRET_KEY", "k" * 43)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def clock():
    t = [1_800_000_000.0]

    class Clock:
        def __call__(self):
            return t[0]

        def advance(self, seconds):
            t[0] += seconds

    return Clock()


def make_client(store, gh, clock, **kw):
    app = create_app(store=store, identity=gh, clock=clock)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False, **kw)


@pytest.fixture
def client(store, gh, clock):
    return make_client(store, gh, clock)


def login(client, next_path=None, code=CODE):
    params = {"next": next_path} if next_path is not None else {}
    r = client.get("/api/auth/login", params=params)
    assert r.status_code == 302
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return client.get("/api/auth/callback", params={"code": code, "state": state})


def me(client):
    return client.get("/api/auth/me").json()


def csrf_headers(client):
    return {"Origin": ORIGIN, "X-Tradvia-CSRF": me(client)["csrf"]}


# full flow ---------------------------------------------------------------------


def test_full_flow(client, gh):
    r = client.get("/api/auth/login", params={"next": "/runs/?repo=Acme/widgets"})
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://github.com/login/oauth/authorize"
    q = parse_qs(loc.query)
    assert q["client_id"] == ["Iv1.client"]
    assert q["redirect_uri"] == [f"{ORIGIN}/api/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and len(q["state"][0]) >= 40

    cb = client.get("/api/auth/callback", params={"code": CODE, "state": q["state"][0]})
    assert cb.status_code == 302 and cb.headers["location"] == "/runs/?repo=Acme/widgets"
    code, redirect_uri, verifier = gh.exchanges[0]
    assert redirect_uri == f"{ORIGIN}/api/auth/callback"
    # PKCE: the challenge sent to GitHub is S256 of the verifier kept on the server
    assert auth._b64(hashlib.sha256(verifier.encode()).digest()) == q["code_challenge"][0]

    data = me(client)
    assert data["enabled"] and data["authenticated"] and data["login"] == "ana"
    assert data["avatar_url"].endswith("/u/7")
    assert data["projects"] == [
        {"slug": "tradvia-tradvia", "external_id": A, "role": "admin", "pending_count": 0,
         "can_start": True, "can_approve": True},
        {"slug": "acme-widgets", "external_id": B, "role": "read", "pending_count": 0,
         "can_start": False, "can_approve": False},
    ]

    out = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert out.status_code == 200
    assert me(client)["authenticated"] is False


def test_session_cookie_attributes(client):
    cb = login(client)
    cookie = next(c for c in cb.headers.get_list("set-cookie") if c.startswith(SESSION + "="))
    low = cookie.lower()
    assert "secure" in low and "httponly" in low and "samesite=lax" in low
    assert "path=/" in low and "domain" not in low and "max-age=28800" in low
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    assert len(token) == 43  # 256 bits
    login_cookie = next(c for c in cb.headers.get_list("set-cookie") if c.startswith(auth.STATE_COOKIE))
    assert "max-age=0" in login_cookie.lower()


def test_login_sets_state_cookie(client):
    r = client.get("/api/auth/login")
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith(auth.STATE_COOKIE.lower() + "=")
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


# state ------------------------------------------------------------------------


def test_callback_without_state_or_code(client):
    assert client.get("/api/auth/callback", params={"code": CODE}).status_code == 400
    assert client.get("/api/auth/callback", params={"state": "x"}).status_code == 400
    assert client.get("/api/auth/callback").status_code == 400


def test_callback_with_unknown_state(client, gh):
    client.get("/api/auth/login")
    r = client.get("/api/auth/callback", params={"code": CODE, "state": "forged"})
    assert r.status_code == 400 and not gh.exchanges
    assert me(client)["authenticated"] is False


def test_state_is_single_use(client, store):
    r = client.get("/api/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    first = client.get("/api/auth/callback", params={"code": CODE, "state": state})
    assert first.status_code == 302
    # replay: the state cookie is gone and the state is deleted
    client.cookies.set(auth.STATE_COOKIE, state, domain=DOMAIN)
    again = client.get("/api/auth/callback", params={"code": CODE, "state": state})
    assert again.status_code == 400 and len(store.sessions) == 1


def test_state_must_match_the_browser_that_started_the_login(store, gh, clock):
    victim = make_client(store, gh, clock)
    attacker = make_client(store, gh, clock)
    r = attacker.get("/api/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    victim.get("/api/auth/login")  # the victim has its own state cookie
    forced = victim.get("/api/auth/callback", params={"code": CODE, "state": state})
    assert forced.status_code == 400 and not store.sessions


def test_expired_state(client, clock, gh):
    r = client.get("/api/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    clock.advance(auth.STATE_TTL + 1)
    r = client.get("/api/auth/callback", params={"code": CODE, "state": state})
    assert r.status_code == 400 and not gh.exchanges


def test_github_refusing_the_code(client, store):
    r = login(client, code="bad")
    assert r.status_code == 502 and not store.sessions
    assert "ghu_" not in r.text


# sessions ---------------------------------------------------------------------


def test_expired_session_is_anonymous(client, clock, store):
    login(client)
    assert me(client)["authenticated"]
    clock.advance(auth.SESSION_TTL + 1)
    assert me(client)["authenticated"] is False
    assert not store.sessions  # removed on sight


def test_session_renews_but_stops_at_seven_days(client, clock):
    login(client)
    for _ in range(6 * 24 // 7):  # one use every 7 hours keeps it alive
        clock.advance(7 * 3600)
        assert me(client)["authenticated"]
    while clock() < 1_800_000_000.0 + auth.SESSION_MAX - 3600:
        clock.advance(3600)
        assert me(client)["authenticated"]
    clock.advance(2 * 3600)
    assert me(client)["authenticated"] is False


def test_unused_session_expires_long_before_the_absolute_limit(client, clock):
    login(client)
    clock.advance(auth.SESSION_MAX - 3600)  # never touched in between: the 8 h window passed
    assert me(client)["authenticated"] is False


def test_tampered_cookie_is_anonymous(client, store, gh, clock):
    login(client)
    value = client.cookies.get(SESSION)
    forged = value[:-1] + ("A" if value[-1] != "A" else "B")
    other = make_client(store, gh, clock, headers={"Cookie": f"{SESSION}={forged}"})
    assert other.get("/api/auth/me").json()["authenticated"] is False
    junk = make_client(store, gh, clock, headers={"Cookie": f"{SESSION}=not-a-token"})
    assert junk.get("/api/auth/me").json()["authenticated"] is False
    # a forged cookie never passes the CSRF filter as a session either
    r = other.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert r.status_code == 200 and len(store.sessions) == 1


def test_logout_invalidates_the_session(client, store, gh, clock):
    login(client)
    value = client.cookies.get(SESSION)
    headers = csrf_headers(client)
    r = client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200 and not store.sessions
    assert f"{SESSION}=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    replay = make_client(store, gh, clock, headers={"Cookie": f"{SESSION}={value}"})
    assert replay.get("/api/auth/me").json()["authenticated"] is False


# CSRF and origin ---------------------------------------------------------------


def test_csrf_header_missing_or_wrong(client, store):
    login(client)
    good = me(client)["csrf"]
    missing = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert missing.status_code == 403
    wrong = client.post("/api/auth/logout", headers={"Origin": ORIGIN, "X-Tradvia-CSRF": good[::-1]})
    assert wrong.status_code == 403
    assert len(store.sessions) == 1
    ok = client.post("/api/auth/logout", headers={"Origin": ORIGIN, "X-Tradvia-CSRF": good})
    assert ok.status_code == 200 and not store.sessions


def test_csrf_token_belongs_to_the_session(store, gh, clock):
    one, two = make_client(store, gh, clock), make_client(store, gh, clock)
    login(one)
    login(two)
    assert me(one)["csrf"] != me(two)["csrf"]
    r = one.post("/api/auth/logout", headers={"Origin": ORIGIN, "X-Tradvia-CSRF": me(two)["csrf"]})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "https://app.test.evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/https://app.test/"},
        {"Referer": "https://app.test.evil.example/x"},
        {},
    ],
)
def test_wrong_or_missing_origin_is_refused(client, store, headers):
    login(client)
    r = client.post("/api/auth/logout", headers={**headers, "X-Tradvia-CSRF": me(client)["csrf"]})
    assert r.status_code == 403 and len(store.sessions) == 1


def test_referer_is_accepted_without_origin(client, store):
    login(client)
    r = client.post(
        "/api/auth/logout",
        headers={"Referer": ORIGIN + "/runs/", "X-Tradvia-CSRF": me(client)["csrf"]},
    )
    assert r.status_code == 200 and not store.sessions


def test_reads_stay_public_and_webhooks_skip_the_filter(client):
    login(client)
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/healthz").status_code == 200
    # unsigned webhook: refused for its signature (401), not for CSRF (403)
    r = client.post("/api/webhooks/github", content=b"{}", headers={"Origin": "https://evil.example"})
    assert r.status_code == 401


# secrets -----------------------------------------------------------------------


def test_token_never_in_responses_headers_or_logs(client, store, caplog):
    with caplog.at_level(logging.DEBUG):
        cb = login(client)
        responses = [cb, client.get("/api/auth/me"), client.get("/api/projects")]
        responses.append(client.post("/api/auth/logout", headers=csrf_headers(client)))
        responses.append(login(client, code="bad"))
    for r in responses:
        assert TOKEN not in r.text and TOKEN not in str(dict(r.headers))
    assert TOKEN not in caplog.text
    assert "client-secret-value" not in caplog.text


def test_token_is_encrypted_at_rest(client, store):
    login(client)
    (rec,) = store.sessions.values()
    assert TOKEN not in str(rec) and rec["token_enc"] != TOKEN
    cookie = client.cookies.get(SESSION)
    assert rec["id"] != cookie and cookie not in str(rec)  # only the hash is stored
    keys = auth.Keys("k" * 43)
    assert keys.decrypt(rec["token_enc"]) == TOKEN
    assert auth.Keys("another secret key").decrypt(rec["token_enc"]) is None


def test_changed_secret_key_drops_the_session(client, store, gh, clock, monkeypatch):
    login(client)
    value = client.cookies.get(SESSION)
    monkeypatch.setenv("TRADVIA_SECRET_KEY", "z" * 43)
    other = make_client(store, gh, clock, headers={"Cookie": f"{SESSION}={value}"})
    assert other.get("/api/auth/me").json()["authenticated"] is False
    assert not store.sessions


# roles -------------------------------------------------------------------------


def test_role_is_cached_for_five_minutes(client, gh, clock):
    login(client)
    me(client)
    assert gh.perm_calls == 2  # one per registered project
    me(client)
    clock.advance(auth.ROLE_TTL - 1)
    me(client)
    assert gh.perm_calls == 2
    gh.perm[A] = ("write", "write")
    clock.advance(2)
    data = me(client)
    assert gh.perm_calls == 4
    assert data["projects"][0]["role"] == "write"


def test_role_mapping():
    assert role_from_github("write", "maintain") == "maintain"
    assert role_from_github("admin", None) == "admin"
    assert role_from_github("read", "custom-role") == "read"
    assert role_from_github("none", None) is None
    assert role_from_github(None, None) is None


def test_non_collaborator_has_no_role(client, gh):
    gh.perm[B] = (None, None)
    login(client)
    assert me(client)["projects"][1]["role"] is None


def test_github_failure_gives_no_role_and_is_not_cached(client, gh):
    def boom(*a):
        raise RuntimeError(TOKEN)

    gh.get_permission = boom
    login(client)
    r = client.get("/api/auth/me")
    assert r.status_code == 200 and TOKEN not in r.text
    assert [p["role"] for p in r.json()["projects"]] == [None, None]


# return path -------------------------------------------------------------------


def test_next_accepts_only_runs_paths(client):
    cb = login(client, "/runs/?repo=Acme/widgets")
    assert cb.headers["location"] == "/runs/?repo=Acme/widgets"
    for bad in ("//evil.com", "https://evil.com", "/outra", "/runs/../x", "/runs//evil.com",
                "/\\evil.com", "/runs/\r\nSet-Cookie: x=1", "/runs/%2e%2e/x", "javascript:alert(1)"):
        client.cookies.clear()
        assert login(client, bad).headers["location"] == "/runs/", bad


def test_callback_ignores_next_in_the_request(client):
    r = client.get("/api/auth/login", params={"next": "/runs/?repo=Acme/widgets"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    cb = client.get("/api/auth/callback", params={"code": CODE, "state": state, "next": "//evil.com"})
    assert cb.headers["location"] == "/runs/?repo=Acme/widgets"


def test_safe_return_path_unit():
    assert safe_return_path(None) == "/runs/"
    assert safe_return_path("/runs/") == "/runs/"
    assert safe_return_path("/runs/?repo=a/b") == "/runs/?repo=a/b"
    assert safe_return_path("/runs") == "/runs/"
    assert safe_return_path("/runs/\x00") == "/runs/"
    assert safe_return_path("/runs/" + "a" * 600) == "/runs/"


# disabled ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["TRADVIA_GITHUB_APP_CLIENT_ID", "TRADVIA_GITHUB_APP_CLIENT_SECRET", "TRADVIA_SECRET_KEY"],
)
def test_auth_is_off_without_all_three_variables(missing, monkeypatch, store, gh, clock):
    monkeypatch.delenv(missing)
    client = make_client(store, gh, clock)
    assert client.get("/api/auth/me").json() == {"enabled": False}
    assert client.get("/api/auth/login").status_code == 404
    assert client.get("/api/auth/callback", params={"code": "c", "state": "s"}).status_code == 404
    assert client.post("/api/auth/logout").status_code == 404
    assert client.get("/api/projects").status_code == 200  # reading still works


def test_load_config():
    assert auth.load_config({}) is None
    cfg = auth.load_config(
        {"TRADVIA_GITHUB_APP_CLIENT_ID": "i", "TRADVIA_GITHUB_APP_CLIENT_SECRET": "s",
         "TRADVIA_SECRET_KEY": "k", "TRADVIA_DOMAIN": "app.tradvia.org"}
    )
    assert cfg.origin == "https://app.tradvia.org"
    assert cfg.redirect_uri == "https://app.tradvia.org/api/auth/callback"


# panel actions with the user's identity -------------------------------------------

SERVER_TOKEN = "ghp_SERVER_token_must_not_be_used"
PLAN = (
    "🟡 **#8 — card**\nresumo\n1. passo\n\n❓ posso seguir?\n"
    "Responda **Aprovado**, **Rejeitado** ou escreva um ajuste."
)
AGENT_PLAN = {"user": {"login": "github-actions[bot]"}, "body": PLAN}
BASE = f"/api/projects/{A}"
_keys = iter(range(10_000))


def key():
    return f"key-{next(_keys):08d}"


def post(client, path, body=None, idem=None, csrf=True):
    headers = csrf_headers(client) if csrf else {"Origin": ORIGIN}
    if idem != "":
        headers["X-Tradvia-Idempotency-Key"] = idem or key()
    return client.post(path, json=body if body is not None else {}, headers=headers)


@pytest.fixture
def signed(client, gh, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    gh.issues[8] = {"number": 8, "state": "open", "labels": []}
    gh.comments[8] = [AGENT_PLAN]
    login(client)
    return client


def as_role(store, gh, clock, role):
    gh.perm[A] = (role, role)
    client = make_client(store, gh, clock)
    login(client)
    return client


def audit_rows(store, number):
    return store.list_audit(store.ensure_project(A), number)


def test_create_card_uses_the_user_token_and_lands_at_the_end(signed, gh, store):
    pid = store.ensure_project(A)
    store.set_backlog(pid, [{"issue_number": 1}, {"issue_number": 2}])
    r = post(signed, BASE + "/cards", {"title": "Novo", "description": "Depende do #2."})
    assert r.status_code == 200 and r.json()["issue_number"] == 101
    (call,) = gh.writes("create_issue")
    assert call[1] == TOKEN and call[1] != SERVER_TOKEN and call[3] == "Novo"
    items = store.list_backlog(pid)
    assert [i["issue_number"] for i in items] == [1, 2, 101]
    assert items[-1]["depends_on"] == [2]
    assert store.list_issues(pid, [], "all", 10)[0]["title"] == "Novo"
    assert r.headers["cache-control"] == "no-store"


def test_create_card_validates_the_title(signed, gh):
    assert post(signed, BASE + "/cards", {"title": "  "}).status_code == 400
    assert post(signed, BASE + "/cards", {"title": "x" * 300}).status_code == 400
    assert not gh.writes("create_issue")


def test_start_card_adds_the_label_with_the_user_token(signed, gh):
    r = post(signed, BASE + "/cards/8/start")
    assert r.status_code == 200
    assert gh.writes("add_label") == [("add_label", TOKEN, A, 8, "agent:ready")]
    assert all(c[1] == TOKEN for c in gh.calls if c[0] != "get_permission")


def test_start_card_in_the_wrong_state(signed, gh):
    gh.issues[8]["labels"] = [{"name": "agent:ready"}]
    assert post(signed, BASE + "/cards/8/start").status_code == 409
    gh.issues[8].update(labels=[], state="closed")
    assert post(signed, BASE + "/cards/8/start").status_code == 409
    gh.issues[8].update(state="open", pull_request={})
    assert post(signed, BASE + "/cards/8/start").status_code == 404
    assert not gh.writes("add_label")


@pytest.mark.parametrize("decision, comment", [
    ("approve", "Aprovado"), ("reject", "Rejeitado"), ("adjust", "Ajuste: use outra lib"),
])
def test_reply_comments_with_the_user_token(signed, gh, decision, comment):
    r = post(signed, BASE + "/cards/8/reply", {"decision": decision, "text": "use outra lib"})
    assert r.status_code == 200
    assert gh.writes("comment") == [("comment", TOKEN, A, 8, comment)]


def test_reply_needs_a_plan_awaiting_an_answer(signed, gh):
    gh.comments[8] = [AGENT_PLAN, {"user": {"login": "github-actions[bot]"}, "body": "Rodando."}]
    assert post(signed, BASE + "/cards/8/reply", {"decision": "approve"}).status_code == 409
    gh.comments[8] = [{"user": {"login": "ana"}, "body": PLAN}]  # not from the agent
    assert post(signed, BASE + "/cards/8/reply", {"decision": "approve"}).status_code == 409
    assert not gh.writes("comment")


def test_reply_validates_the_decision(signed, gh):
    assert post(signed, BASE + "/cards/8/reply", {"decision": "maybe"}).status_code == 400
    assert post(signed, BASE + "/cards/8/reply", {"decision": "adjust", "text": " "}).status_code == 400
    assert not gh.writes("comment")


def test_card_outside_the_project_is_404(signed, gh):
    assert post(signed, BASE + "/cards/999/start").status_code == 404
    assert post(signed, BASE + "/cards/999/reply", {"decision": "approve"}).status_code == 404
    assert signed.get(BASE + "/cards/999/audit").status_code == 404
    assert post(signed, "/api/projects/Other/repo/cards", {"title": "x"}).status_code == 404
    assert not gh.writes("add_label") and not gh.writes("comment")


def test_insufficient_role_is_403_with_a_clear_message(store, gh, clock, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    gh.comments[7] = [AGENT_PLAN]
    triage = as_role(store, gh, clock, "triage")
    assert post(triage, BASE + "/cards/7/start").status_code == 200  # triage may start
    r = post(triage, BASE + "/cards/7/reply", {"decision": "approve"})
    assert r.status_code == 403
    assert "'triage'" in r.json()["error"] and "approve_plan" in r.json()["error"]
    assert "'write'" in r.json()["error"]
    assert not gh.writes("comment")
    reader = as_role(store, gh, clock, "read")
    for path, body in (("/cards", {"title": "x"}), ("/cards/7/start", {})):
        r = post(reader, BASE + path, body)
        assert r.status_code == 403 and "'triage'" in r.json()["error"]
    assert not gh.writes("create_issue")
    assert post(reader, BASE + "/backlog", {"order": []}).status_code == 403


def test_non_collaborator_has_no_access(store, gh, clock):
    gh.perm[A] = (None, None)
    client = make_client(store, gh, clock)
    login(client)
    r = post(client, BASE + "/cards/7/start")
    assert r.status_code == 403 and "no access" in r.json()["error"]
    assert not gh.writes("add_label")


def test_policy_file_can_raise_the_minimum_role(store, gh, clock):
    gh.policy = "interactions:\n  start_card: admin\n"
    write = as_role(store, gh, clock, "write")
    r = post(write, BASE + "/cards/7/start")
    assert r.status_code == 403 and "'admin'" in r.json()["error"]


def test_missing_policy_file_uses_the_adr_defaults(signed, gh):
    gh.policy = None  # 404
    assert post(signed, BASE + "/cards/8/start").status_code == 200
    assert gh.writes("get_file")[-1] == ("get_file", TOKEN, A, ".tradvia.yml", "")  # default branch, user token


def test_unreadable_policy_fails_closed_naming_the_permission(signed, gh, store):
    gh.policy = PolicyUnreadable(auth.UNREADABLE_POLICY)
    r = post(signed, BASE + "/cards/8/start")
    assert r.status_code == 403 and "Contents: Read-only" in r.json()["error"]
    assert not gh.writes("add_label")
    assert audit_rows(store, 8)[0]["result"] == "denied"


def test_invalid_policy_fails_closed(signed, gh):
    gh.policy = "interactions:\n  start_card: emperor\n"
    r = post(signed, BASE + "/cards/8/start")
    assert r.status_code == 403 and ".tradvia.yml" in r.json()["error"]
    assert not gh.writes("add_label")


class _Resp:
    def __init__(self, status, content=""):
        import base64

        self.status_code = status
        self._content = base64.b64encode(content.encode()).decode()

    def json(self):
        return {"content": self._content}


@pytest.mark.parametrize("status, outcome", [(404, None), (200, "x: 1")])
def test_http_get_file(monkeypatch, status, outcome):
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        return _Resp(status, "x: 1")

    monkeypatch.setattr(auth.requests, "get", fake_get)
    http = auth.HttpGitHub("id", "secret")
    assert http.get_file(TOKEN, A, ".tradvia.yml", "") == outcome
    assert seen["url"].endswith(f"/repos/{A}/contents/.tradvia.yml")  # no ref: default branch
    assert seen["auth"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("status", [401, 403])
def test_http_get_file_denied_is_unreadable(monkeypatch, status):
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: _Resp(status))
    with pytest.raises(PolicyUnreadable, match="Contents: Read-only"):
        auth.HttpGitHub("id", "secret").get_file(TOKEN, A, ".tradvia.yml", "")


def test_missing_csrf_is_403_on_every_action(signed, gh):
    for path in ("/cards", "/cards/8/start", "/cards/8/reply", "/backlog"):
        assert post(signed, BASE + path, {"title": "x"}, csrf=False).status_code == 403
    assert not [c for c in gh.calls if c[0] in ("create_issue", "add_label", "comment")]


def test_anonymous_is_401(client, gh):
    r = client.post(BASE + "/cards/8/start", json={})
    assert r.status_code == 401
    assert client.get(BASE + "/cards/8/audit").status_code == 401
    assert not gh.calls


def test_invalid_idempotency_key_is_400(signed):
    assert post(signed, BASE + "/cards/8/start", idem="no good!").status_code == 400


def test_same_request_within_five_seconds_is_ignored(signed, gh, clock):
    k = key()
    first = post(signed, BASE + "/cards/8/start", idem=k)
    clock.advance(3)
    second = post(signed, BASE + "/cards/8/start", idem=k)
    assert first.status_code == 200 and "duplicate" not in first.json()
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert len(gh.writes("add_label")) == 1
    clock.advance(3)  # 6 s after the first: the key is free again
    assert post(signed, BASE + "/cards/8/start", idem=k).status_code == 200
    assert len(gh.writes("add_label")) == 2


def test_a_different_key_is_not_a_repeat(signed, gh):
    post(signed, BASE + "/cards/8/start")
    post(signed, BASE + "/cards/8/start")
    assert len(gh.writes("add_label")) == 2


def test_every_attempt_is_audited(signed, gh, store, clock):
    post(signed, BASE + "/cards/8/start")
    gh.issues[8]["labels"] = [{"name": "agent:ready"}]
    post(signed, BASE + "/cards/8/start")
    post(signed, BASE + "/cards/8/reply", {"decision": "approve"})
    rows = audit_rows(store, 8)
    assert [(r["actor_login"], r["action"], r["result"]) for r in rows] == [
        ("ana", "start_card", "ok"), ("ana", "start_card", "failed"), ("ana", "reply_plan", "ok"),
    ]
    assert rows[2]["detail"] == "decision=approve" and rows[0]["created_at"].endswith("Z")


def test_denied_attempt_is_audited(store, gh, clock):
    reader = as_role(store, gh, clock, "read")
    post(reader, BASE + "/cards/7/start")
    (row,) = audit_rows(store, 7)
    assert (row["actor_login"], row["action"], row["result"]) == ("ana", "start_card", "denied")


def test_audit_route_shows_entries_and_the_plan(signed, gh):
    post(signed, BASE + "/cards/8/start")
    r = signed.get(BASE + "/cards/8/audit")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert [e["action"] for e in data["audit"]] == ["start_card"]
    assert data["awaiting_plan"] is True and data["plan"] == PLAN
    gh.comments[8] = []
    assert signed.get(BASE + "/cards/8/audit").json()["plan"] is None


def test_audit_route_is_closed_to_users_without_the_read_role(store, gh, clock):
    gh.perm[A] = (None, None)
    client = make_client(store, gh, clock)
    login(client)
    r = client.get(BASE + "/cards/8/audit")
    assert r.status_code == 403 and "read" in r.json()["error"]


def test_plan_is_not_fetched_for_users_who_cannot_approve(store, gh, clock):
    gh.issues[8] = {"number": 8, "state": "open", "labels": []}
    gh.comments[8] = [AGENT_PLAN]
    reader = as_role(store, gh, clock, "read")
    data = reader.get(BASE + "/cards/8/audit").json()
    assert data["plan"] is None and not gh.writes("list_comments")


def test_backlog_move_and_order(signed, store, gh):
    pid = store.ensure_project(A)
    store.set_backlog(pid, [{"issue_number": n, "depends_on": [1] if n == 3 else []} for n in (1, 2, 3)])

    def numbers():
        return [i["issue_number"] for i in store.list_backlog(pid)]

    assert post(signed, BASE + "/backlog", {"issue": 3, "direction": "up"}).status_code == 200
    assert numbers() == [1, 3, 2]
    post(signed, BASE + "/backlog", {"issue": 1, "direction": "up"})  # already first
    assert numbers() == [1, 3, 2]
    post(signed, BASE + "/backlog", {"issue": 2, "direction": "down"})  # already last
    assert numbers() == [1, 3, 2]
    assert post(signed, BASE + "/backlog", {"issue": 9, "direction": "up"}).status_code == 404
    assert post(signed, BASE + "/backlog", {"issue": 1, "direction": "left"}).status_code == 400
    assert post(signed, BASE + "/backlog", {"order": [2, 3, 1]}).status_code == 200
    assert numbers() == [2, 3, 1]
    assert [i["depends_on"] for i in store.list_backlog(pid)] == [[], [1], []]
    assert post(signed, BASE + "/backlog", {"order": [1, 2]}).status_code == 400
    assert numbers() == [2, 3, 1]
    assert not gh.writes("comment")


def test_me_reports_what_the_panel_may_show(store, gh, clock):
    gh.perm[A] = ("triage", "triage")
    client = make_client(store, gh, clock)
    login(client)
    mine = me(client)["projects"][0]
    assert (mine["can_start"], mine["can_approve"]) == (True, False)
    gh.policy = PolicyUnreadable(auth.UNREADABLE_POLICY)
    other = make_client(store, gh, clock)
    login(other)
    mine = me(other)["projects"][0]
    assert (mine["can_start"], mine["can_approve"]) == (False, False)


@pytest.mark.parametrize("status, body, expected", [
    (200, {"error": "incorrect_client_credentials", "error_description": "secret abc"},
     "(incorrect_client_credentials)"),
    (200, {"error": "redirect_uri_mismatch"}, "(redirect_uri_mismatch)"),
    (200, {"error": "Bad <script>"}, "(HTTP 200)"),
    (401, {}, "(HTTP 401)"),
])
def test_token_exchange_error_shows_only_github_reason_code(monkeypatch, status, body, expected):
    from types import SimpleNamespace

    import tradvia.auth as auth_mod

    monkeypatch.setattr(auth_mod.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=status, json=lambda: body))
    with pytest.raises(auth_mod.AuthError) as exc:
        auth_mod.HttpGitHub("id", "s3cr3t").exchange_code("code", "https://x/cb", "v")
    msg = str(exc.value)
    assert msg.endswith(expected) and "secret abc" not in msg and "s3cr3t" not in msg
