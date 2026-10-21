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
from tradvia.auth import AuthError, safe_return_path  # noqa: E402
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
        {"slug": "tradvia-tradvia", "external_id": A, "role": "admin"},
        {"slug": "acme-widgets", "external_id": B, "role": "read"},
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
