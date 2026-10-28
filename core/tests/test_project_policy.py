# SPDX-License-Identifier: FSL-1.1-ALv2
"""Roles per interaction and the risk policy are set on Tradvia, per project, never in
.tradvia.yml (ADR 0018)."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, B, BASE, as_role, client, clock, csrf_headers, env, gh, login, make_client, post, store,
)

from tradvia import cli  # noqa: E402
from tradvia.policy import (  # noqa: E402
    Policy, PolicyError, validate_policy_setting, with_policy_setting,
)


def url(repo):
    return f"/api/projects/{repo}/settings/policy"


@pytest.fixture
def signed(client):
    login(client)  # ana: admin of A, read in B
    return client


def put(c, repo, body, csrf=True):
    return c.put(url(repo), json=body, headers=csrf_headers(c) if csrf else {})


def test_a_new_project_has_the_adr_0004_defaults(signed):
    body = signed.get(url(A)).json()
    assert body["interactions"] == body["defaults"]["interactions"]
    assert body["interactions"]["start_card"] == "triage" and body["risk"]["destructive_external"] == "maintain"
    assert body["can_edit"] is True and body["updated_by"] is None and body["file_ignored"] == []
    assert body["locked"] == {"manage_connections": "admin"} and "admin" in body["roles"]


def test_an_admin_changes_roles_and_risk_and_it_is_audited(signed, store):
    r = put(signed, A, {"interactions": {"merge_pr": "admin"}, "risk": {"reversible_write": "write"}})
    assert r.status_code == 200
    body = r.json()
    assert body["interactions"]["merge_pr"] == "admin" and body["interactions"]["start_card"] == "triage"
    assert body["risk"]["reversible_write"] == "write" and body["updated_by"] == "ana"
    rows = [x for x in store.audit if x["action"] == "set_policy"]
    assert rows and rows[-1]["result"] == "ok" and "merge_pr=admin" in rows[-1]["detail"]


def test_the_setting_is_enforced_at_once(store, gh, clock):
    admin = as_role(store, gh, clock, "admin")
    assert put(admin, A, {"interactions": {"start_card": "admin"}}).status_code == 200
    r = post(as_role(store, gh, clock, "write"), BASE + "/cards/7/start")
    assert r.status_code == 403 and "'admin'" in r.json()["error"]


def test_each_project_has_its_own_policy(signed, store):
    assert put(signed, A, {"interactions": {"merge_pr": "admin"}}).status_code == 200
    other = signed.get(url(B)).json()  # B never sees A's setting
    assert other["interactions"]["merge_pr"] == "maintain" and other["updated_by"] is None
    assert store.get_setting(store.ensure_project(B), "policy") is None


def test_only_an_admin_changes_it(signed, store):
    assert signed.get(url(B)).json()["can_edit"] is False  # ana is only 'read' in B
    r = put(signed, B, {"interactions": {"start_card": "read"}})
    assert r.status_code == 403 and "needs 'admin'" in r.json()["error"]
    assert store.get_setting(store.ensure_project(B), "policy") is None
    denied = [x for x in store.audit if x["action"] == "set_policy"]
    assert denied and denied[-1]["result"] == "denied"


@pytest.mark.parametrize("bad", [
    {"interactions": {"start_card": "emperor"}},
    {"interactions": {"manage_connections": "write"}},  # locked to admin: a secret is behind it
    {"interactions": {"fly": "write"}},
    {"risk": {"read": "sometimes"}},
    {"risk": {"nuclear": "admin"}},
    {"budget": {"max_usd": 1}},
    ["x"],
])
def test_invalid_input_is_refused_and_nothing_changes(signed, store, bad):
    assert put(signed, A, bad).status_code == 400
    assert store.get_setting(store.ensure_project(A), "policy") is None


def test_changing_needs_csrf_a_session_and_a_known_project(signed, store, gh, clock):
    assert put(signed, A, {"interactions": {"merge_pr": "admin"}}, csrf=False).status_code == 403
    assert store.get_setting(store.ensure_project(A), "policy") is None
    assert signed.get("/api/projects/Nope/nope/settings/policy").status_code == 404
    assert make_client(store, gh, clock).get(url(A)).status_code == 401


def test_the_file_sections_are_ignored_and_the_panel_is_told(signed, gh):
    gh.policy = "interactions:\n  merge_pr: read\nrisk:\n  destructive_external: free\n"
    body = signed.get(url(A)).json()
    assert body["file_ignored"] == ["interactions", "risk"]
    assert body["interactions"]["merge_pr"] == "maintain" and body["risk"]["destructive_external"] == "maintain"


def test_reading_a_bad_stored_value_keeps_the_defaults():
    assert with_policy_setting(Policy(), {"interactions": {"start_card": "x"}}) == Policy()
    assert with_policy_setting(Policy(), "garbage") == Policy()
    assert with_policy_setting(Policy(), None) == Policy()
    with pytest.raises(PolicyError):
        validate_policy_setting(None)


def test_the_agent_reads_no_setting_without_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.project_setting(A, "policy") is None
