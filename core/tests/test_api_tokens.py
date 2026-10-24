# SPDX-License-Identifier: FSL-1.1-ALv2
"""Programmatic API: bearer tokens for creating and starting cards (ADR 0009)."""

import hashlib

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A,
    B,
    BASE,
    ORIGIN,
    SESSION,
    TOKEN,
    as_role,
    audit_rows,
    client,
    clock,
    csrf_headers,
    env,
    gh,
    login,
    make_client,
    store,
)
from tradvia import remote  # noqa: E402

DAY = 86400
_keys = iter(range(10_000))


def key():
    return f"tok-key-{next(_keys):08d}"


def make_token(client, role="triage", days=None, base=BASE):
    body = {"role": role}
    if days is not None:
        body["expires_in_days"] = days
    return client.post(base + "/tokens", json=body, headers=csrf_headers(client))


@pytest.fixture
def signed(client):
    login(client)
    return client


@pytest.fixture
def api(store, gh, clock):
    """A client with no cookie: only a bearer token identifies it."""
    return make_client(store, gh, clock)


def bearer(raw, idem=True):
    headers = {"Authorization": f"Bearer {raw}"}
    if idem:
        headers["X-Tradvia-Idempotency-Key"] = key()
    return headers


def test_token_creates_a_card_with_the_owner_identity_and_an_audit_row(signed, api, gh, store):
    raw = make_token(signed).json()["token"]
    r = api.post(BASE + "/cards", json={"title": "Via API", "description": "x"}, headers=bearer(raw))
    assert r.status_code == 200 and r.json()["issue_number"] == 101
    (call,) = gh.writes("create_issue")
    assert call[1] == TOKEN  # the owner's own GitHub token, never the server's
    (row,) = audit_rows(store, 101)
    assert (row["actor_login"], row["action"], row["result"], row["via"]) == (
        "ana", "create_card", "ok", "api_token")
    audit = api.get(BASE + "/cards/101/audit", headers=bearer(raw, idem=False))
    assert audit.status_code == 200 and audit.json()["audit"][0]["via"] == "api_token"


def test_session_actions_have_no_via_marker(signed, store):
    signed.post(BASE + "/cards", json={"title": "Panel"},
                headers={**csrf_headers(signed), "X-Tradvia-Idempotency-Key": key()})
    assert audit_rows(store, 101)[0]["via"] is None


def test_token_can_start_a_card(signed, api, gh):
    raw = make_token(signed).json()["token"]
    gh.issues[8] = {"number": 8, "state": "open", "labels": []}
    assert api.post(BASE + "/cards/8/start", headers=bearer(raw)).status_code == 200
    assert gh.writes("add_label")[0][1] == TOKEN


def test_unknown_malformed_and_missing_tokens_are_401(api, gh):
    for header in ("Bearer tvt_" + "a" * 43, "Bearer nonsense", "Bearer ", "Basic abc"):
        r = api.post(BASE + "/cards", json={"title": "x"}, headers={"Authorization": header})
        assert r.status_code == 401
    assert api.post(BASE + "/cards", json={"title": "x"}).status_code == 401
    assert not gh.writes("create_issue")


def test_expired_token_is_401(signed, api, gh, clock):
    raw = make_token(signed, days=1).json()["token"]
    assert api.post(BASE + "/cards", json={"title": "ok"}, headers=bearer(raw)).status_code == 200
    clock.advance(DAY + 1)
    r = api.post(BASE + "/cards", json={"title": "late"}, headers=bearer(raw))
    assert r.status_code == 401 and "expired" in r.json()["error"]
    assert len(gh.writes("create_issue")) == 1


def test_revoked_token_is_401(signed, api, gh):
    made = make_token(signed).json()
    r = signed.delete(f"{BASE}/tokens/{made['id']}", headers=csrf_headers(signed))
    assert r.status_code == 200
    assert api.post(BASE + "/cards", json={"title": "x"},
                    headers=bearer(made["token"])).status_code == 401
    assert signed.delete(f"{BASE}/tokens/{made['id']}",
                         headers=csrf_headers(signed)).status_code == 404
    assert not gh.writes("create_issue")


def test_token_of_another_project_is_refused(signed, api, gh, store):
    gh.perm[B] = ("admin", "admin")
    raw = make_token(signed).json()["token"]  # scoped to A
    r = api.post(f"/api/projects/{B}/cards", json={"title": "x"}, headers=bearer(raw))
    assert r.status_code == 403
    assert not gh.writes("create_issue")
    (row,) = store.list_audit(store.ensure_project(B), None)
    assert row["result"] == "denied" and row["via"] == "api_token"


def test_token_cannot_exceed_the_role_of_its_owner(store, gh, clock):
    triage = as_role(store, gh, clock, "triage")
    r = make_token(triage, role="admin")
    assert r.status_code == 403 and "'triage'" in r.json()["error"]
    assert make_token(triage, role="triage").status_code == 201
    assert make_token(triage, role="nope").status_code == 400
    assert make_token(triage, days=1000).status_code == 400


def test_token_loses_power_when_the_owner_loses_the_role(signed, api, gh, clock):
    raw = make_token(signed).json()["token"]
    gh.perm[A] = ("read", "read")  # demoted on GitHub
    clock.advance(1)
    r = api.post(BASE + "/cards", json={"title": "x"}, headers=bearer(raw))
    assert r.status_code == 403 and not gh.writes("create_issue")


def test_read_only_token_is_refused_on_write_actions(signed, api, gh, store):
    raw = make_token(signed, role="read").json()["token"]
    for path, body in (("/cards", {"title": "x"}), ("/cards/7/start", {})):
        r = api.post(BASE + path, json=body, headers=bearer(raw))
        assert r.status_code == 403 and "'read'" in r.json()["error"]
    assert not gh.writes("create_issue") and not gh.writes("add_label")
    assert audit_rows(store, 7)[0]["result"] == "denied"


def test_token_cannot_approve_merge_or_deploy(signed, api, gh):
    raw = make_token(signed).json()["token"]
    for path in ("/cards/7/reply", "/backlog", "/pulls/3/merge", "/deployments/1/approve",
                 "/operations/post-deploy", "/tokens"):
        r = api.post(BASE + path, json={"decision": "approve"}, headers=bearer(raw))
        assert r.status_code == 403, path
    assert not gh.writes("comment")


def test_bearer_needs_no_csrf_but_cookies_still_do(signed, api, gh):
    raw = make_token(signed).json()["token"]
    # bearer: no Origin, no CSRF header
    assert api.post(BASE + "/cards", json={"title": "a"}, headers=bearer(raw)).status_code == 200
    # cookie: still refused without the CSRF header or with a foreign Origin
    assert signed.post(BASE + "/cards", json={"title": "b"},
                       headers={"Origin": ORIGIN}).status_code == 403
    assert signed.post(BASE + "/cards", json={"title": "b"},
                       headers={"Origin": "https://evil.test",
                                "X-Tradvia-CSRF": csrf_headers(signed)["X-Tradvia-CSRF"]}
                       ).status_code == 403
    assert len(gh.writes("create_issue")) == 1


def test_a_cookie_is_never_replaced_by_a_bearer_token(signed, gh):
    raw = make_token(signed).json()["token"]
    # the cookie rules (and its CSRF check) apply even if a token is also sent
    r = signed.post(BASE + "/cards", json={"title": "x"}, headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403 and not gh.writes("create_issue")


def test_token_management_needs_a_session(api, signed):
    raw = make_token(signed).json()["token"]
    assert api.post(BASE + "/tokens", json={}, headers=bearer(raw)).status_code == 403
    assert api.delete(BASE + "/tokens/1", headers=bearer(raw)).status_code == 403
    assert api.post(BASE + "/tokens", json={}).status_code == 401
    assert make_token(signed, base="/api/projects/Other/repo").status_code == 404


def test_idempotency_key_is_honored_for_tokens(signed, api, gh, clock):
    raw = make_token(signed).json()["token"]
    headers = {"Authorization": f"Bearer {raw}", "X-Tradvia-Idempotency-Key": key()}
    first = api.post(BASE + "/cards", json={"title": "once"}, headers=headers)
    clock.advance(2)
    second = api.post(BASE + "/cards", json={"title": "once"}, headers=headers)
    assert first.status_code == 200 and "duplicate" not in first.json()
    assert second.json()["duplicate"] is True and second.json()["issue_number"] == 101
    assert len(gh.writes("create_issue")) == 1


def test_token_is_stored_only_as_a_hash(signed, store):
    made = make_token(signed).json()
    raw = made["token"]
    assert raw.startswith("tvt_")
    (rec,) = store.api_tokens.values()
    assert rec["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in repr(store.api_tokens) and raw not in repr(store.audit)
    assert TOKEN not in repr(store.api_tokens)  # the GitHub token is encrypted at rest
    assert rec["role"] == "triage" and rec["login"] == "ana"


def test_token_and_session_cookie_never_in_the_audit_of_token_management(signed, store):
    made = make_token(signed).json()
    assert made["token"] not in repr(store.audit)
    assert [r["action"] for r in store.audit] == ["create_token"]


def test_token_requests_are_not_cacheable(signed, api):
    raw = make_token(signed).json()["token"]
    r = api.post(BASE + "/cards", json={"title": "x"}, headers=bearer(raw))
    assert r.headers["cache-control"] == "no-store"
    assert make_token(signed).headers["cache-control"] == "no-store"


# CLI ------------------------------------------------------------------------------


def test_cli_creates_a_token_a_card_and_revokes_it(signed, store, gh, clock, capsys, monkeypatch):
    # The CLI talks to the app through test clients; like requests.request, no cookie jar
    # survives between calls.
    def http(method, url, headers, json=None, timeout=None):
        return make_client(store, gh, clock).request(method, url, headers=headers, json=json)

    monkeypatch.setenv("TRADVIA_SESSION", signed.cookies.get(SESSION))
    out = remote.main(["--url", ORIGIN, "token", "create", "--project", A], http=http)
    assert out["token"] in capsys.readouterr().out and out["role"] == "triage"
    monkeypatch.setenv("TRADVIA_TOKEN", out["token"])
    card = remote.main(["--url", ORIGIN, "card", "create", "--project", A,
                        "--title", "From CLI", "--body", "b", "--start"], http=http)
    assert card["issue_number"] == 101 and gh.writes("add_label")
    remote.main(["--url", ORIGIN, "token", "revoke", "--project", A, str(out["id"])], http=http)
    with pytest.raises(SystemExit, match="401"):
        remote.main(["--url", ORIGIN, "card", "create", "--project", A, "--title", "x"], http=http)


def test_cli_needs_url_and_credentials(monkeypatch):
    monkeypatch.delenv("TRADVIA_URL", raising=False)
    monkeypatch.delenv("TRADVIA_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="TRADVIA_URL"):
        remote.main(["card", "create", "--project", A, "--title", "x"])
    with pytest.raises(SystemExit, match="TRADVIA_TOKEN"):
        remote.main(["--url", ORIGIN, "card", "create", "--project", A, "--title", "x"])
