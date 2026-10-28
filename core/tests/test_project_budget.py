# SPDX-License-Identifier: FSL-1.1-ALv2
"""The project's budget is set on Tradvia, per project, never in .tradvia.yml (ADR 0018)."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, B, client, clock, csrf_headers, env, gh, login, make_client, store,
)

from tradvia import cli  # noqa: E402

FULL = {"max_minutes": 15, "max_steps": 20, "max_usd": 1.5, "monthly_usd": 50}


def url(repo):
    return f"/api/projects/{repo}/settings/budget"


@pytest.fixture
def signed(client):
    login(client)  # ana: admin of A, read in B
    return client


def put(c, repo, body, csrf=True):
    return c.put(url(repo), json=body, headers=csrf_headers(c) if csrf else {})


def test_a_new_project_has_no_ceiling_and_the_default_card_limits(signed):
    body = signed.get(url(A)).json()
    assert body["monthly_usd"] is None and body["max_usd"] == 5.0 and body["max_steps"] == 40
    assert body["can_edit"] is True and body["updated_by"] is None
    assert body["month"]["cap_usd"] is None


def test_an_admin_sets_the_budget_and_it_is_audited(signed, store):
    r = put(signed, A, FULL)
    assert r.status_code == 200
    body = r.json()
    assert (body["max_minutes"], body["max_steps"], body["max_usd"], body["monthly_usd"]) == (15.0, 20, 1.5, 50.0)
    assert body["updated_by"] == "ana" and body["month"]["cap_usd"] == 50.0
    rows = [x for x in store.audit if x["action"] == "set_budget"]
    assert rows and rows[-1]["result"] == "ok" and "monthly_usd=50" in rows[-1]["detail"]
    assert put(signed, A, {**FULL, "monthly_usd": None}).json()["monthly_usd"] is None


def test_each_project_has_its_own_budget(signed, store):
    assert put(signed, A, FULL).status_code == 200
    other = signed.get(url(B)).json()  # B never sees A's setting
    assert other["monthly_usd"] is None and other["max_usd"] == 5.0 and other["updated_by"] is None
    assert store.get_setting(store.ensure_project(B), "budget") is None
    assert store.get_setting(store.ensure_project(A), "budget")["value"]["monthly_usd"] == 50.0


def test_a_reader_sees_but_cannot_change_the_budget(signed, store):
    body = signed.get(url(B)).json()  # ana is only 'read' in B
    assert body["can_edit"] is False
    r = put(signed, B, FULL)
    assert r.status_code == 403 and "budget needs at least 'admin'" in r.json()["error"]
    assert store.get_setting(store.ensure_project(B), "budget") is None
    denied = [x for x in store.audit if x["action"] == "set_budget"]
    assert denied and denied[-1]["result"] == "denied"


def test_invalid_input_is_refused_and_nothing_changes(signed, store):
    for bad in ({"max_usd": 0}, {"monthly_usd": "100"}, {"max_steps": 1.5}, {"other": 1}, ["x"]):
        assert put(signed, A, bad).status_code == 400
    assert store.get_setting(store.ensure_project(A), "budget") is None


def test_changing_needs_csrf_and_a_known_project(signed, store):
    assert put(signed, A, FULL, csrf=False).status_code == 403
    assert store.get_setting(store.ensure_project(A), "budget") is None
    assert signed.get("/api/projects/Nope/nope/settings/budget").status_code == 404


def test_without_a_session_the_budget_is_not_shown(store, gh, clock):
    c = make_client(store, gh, clock)
    assert c.get(url(A)).status_code == 401


def test_the_file_budget_is_ignored_and_the_panel_is_told(signed, gh, store):
    gh.policy = "budget:\n  monthly_usd: 99999\n  max_usd: 9\n"
    body = signed.get(url(A)).json()
    assert body["file_budget_ignored"] is True
    assert body["monthly_usd"] is None and body["max_usd"] == 5.0


def test_the_agent_reads_no_setting_without_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.project_budget_setting(A) is None
