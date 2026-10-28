# SPDX-License-Identifier: FSL-1.1-ALv2
"""An AI manager below the project's owner (ADR 0014).

Base: follow and organize (read, comment, create, start, claim, adjust plans). Approving plans and
reordering the backlog only with a grant from an admin, which expires (at most 90 days) and is
revocable at once. Merge, deploy, budget, connections and roles are never grantable.
"""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_agent_collab import TeamGitHub, gh  # noqa: E402,F401
from test_agent_tools import api, bearer, call, signed  # noqa: E402,F401
from test_auth import (  # noqa: E402,F401
    A, AGENT_PLAN, BASE, client, clock, csrf_headers, env, login, make_client, store,
)

from tradvia import orchestrator  # noqa: E402
from tradvia.auth import GRANTABLE  # noqa: E402

TOKENS = BASE + "/tokens"
AGENT = BASE + "/agent"


def manager_token(signed, days=30):
    r = signed.post(TOKENS, json={"kind": "manager", "expires_in_days": days},
                    headers=csrf_headers(signed))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "manager" and body["role"] == "write"
    return body["id"], body["token"]


def grant(signed, token_id, permission, days=7):
    return signed.post(f"{TOKENS}/{token_id}/grants",
                       json={"permission": permission, "expires_in_days": days},
                       headers=csrf_headers(signed))


def tools(api, raw):
    r = api.get(AGENT + "/tools", headers=bearer(raw, idem=False))
    assert r.status_code == 200
    return {t["name"]: t for t in r.json()["tools"]}


def waiting_plan(gh, n=8):
    gh.comments[n] = [AGENT_PLAN]


# --- making a manager ----------------------------------------------------------------------


def test_only_an_admin_makes_an_ai_manager(store, gh, clock):
    gh.perm[A] = ("write", "write")
    c = make_client(store, gh, clock)
    login(c)
    r = c.post(TOKENS, json={"kind": "manager"}, headers=csrf_headers(c))
    assert r.status_code == 403 and "Only an admin" in r.json()["error"]
    r = c.post(TOKENS, json={"kind": "boss"}, headers=csrf_headers(c))
    assert r.status_code == 400


def test_the_token_list_shows_the_kind_and_the_grants(signed):
    token_id, _ = manager_token(signed)
    assert grant(signed, token_id, "approve_plan").status_code == 201
    body = signed.get(TOKENS).json()
    assert body["can_make_manager"] is True and body["grantable"] == list(GRANTABLE)
    (tok,) = body["tokens"]
    assert tok["kind"] == "manager" and [g["permission"] for g in tok["grants"]] == ["approve_plan"]
    assert tok["grants"][0]["active"] is True and tok["grants"][0]["granted_by"] == "ana"


# --- the base: follow and organize ----------------------------------------------------------


def test_without_grants_a_manager_follows_and_organizes_but_does_not_decide(signed, api, gh, store):
    _, raw = manager_token(signed)
    names = tools(api, raw)
    assert {"read_briefing", "create_card", "start_card", "claim_card", "reply_plan"} <= set(names)
    assert "reorder_backlog" not in names and "AI manager" in names["read_briefing"]["description"]
    waiting_plan(gh)
    r = call(api, raw, "reply_plan", {"number": 8, "decision": "approve"})
    assert r.status_code == 400 and "Only 'adjust'" in r.json()["error"]
    r = call(api, raw, "reorder_backlog", {"number": 8, "direction": "up"})
    assert r.status_code == 400 and "grant from the owner" in r.json()["error"]
    assert call(api, raw, "reply_plan", {"number": 8, "decision": "adjust",
                                         "text": "mais testes"}).json()["ok"] is True


# --- grants ---------------------------------------------------------------------------------


def test_a_granted_manager_approves_a_plan_with_the_panels_own_comment(signed, api, gh):
    token_id, raw = manager_token(signed)
    assert grant(signed, token_id, "approve_plan").status_code == 201
    assert "'approve'" in tools(api, raw)["reply_plan"]["description"]
    waiting_plan(gh)
    out = call(api, raw, "reply_plan", {"number": 8, "decision": "approve"}).json()
    assert out["ok"] is True and out["result"]["posted"] == "Aprovado"
    assert gh.comments[8][-1]["body"] == "Aprovado"
    waiting_plan(gh, 9)  # the human-only name works too, as the same decision
    assert call(api, raw, "reject_plan", {"number": 9}).json()["result"]["posted"] == "Rejeitado"


def test_merge_and_deploy_are_never_a_managers_even_with_grants(signed, api, gh):
    token_id, raw = manager_token(signed)
    for p in GRANTABLE:
        assert grant(signed, token_id, p).status_code == 201
    for tool in ("merge_pr", "approve_deploy"):
        r = call(api, raw, tool, {"number": 1})
        assert r.status_code == 400 and "pending item" in r.json()["error"]
    for p in ("merge_pr", "approve_deploy", "budget_control", "manage_connections", "admin"):
        assert grant(signed, token_id, p).status_code == 400


def test_a_granted_manager_reorders_the_backlog(signed, api, store):
    token_id, raw = manager_token(signed)
    pid = store.ensure_project(A)
    store.set_backlog(pid, [{"issue_number": n} for n in (7, 8, 9)])
    assert grant(signed, token_id, "reorder_backlog").status_code == 201
    assert "reorder_backlog" in tools(api, raw)
    out = call(api, raw, "reorder_backlog", {"number": 9, "direction": "up"}).json()
    assert out["ok"] is True and out["result"]["order"] == [7, 9, 8]


def test_revoking_takes_effect_on_the_next_call(signed, api, gh):
    token_id, raw = manager_token(signed)
    grant_id = grant(signed, token_id, "approve_plan").json()["id"]
    r = signed.delete(f"{TOKENS}/{token_id}/grants/{grant_id}", headers=csrf_headers(signed))
    assert r.status_code == 200
    waiting_plan(gh)
    assert call(api, raw, "reply_plan", {"number": 8, "decision": "approve"}).status_code == 400
    again = signed.delete(f"{TOKENS}/{token_id}/grants/{grant_id}", headers=csrf_headers(signed))
    assert again.status_code == 404


def test_a_grant_expires_and_never_outlives_its_token(signed, api, gh, clock):
    token_id, raw = manager_token(signed, days=5)
    body = grant(signed, token_id, "approve_plan", days=30).json()
    assert body["expires_at"] == signed.get(TOKENS).json()["tokens"][0]["expires_at"]
    short_id, short_raw = manager_token(signed, days=30)
    assert grant(signed, short_id, "approve_plan", days=1).status_code == 201
    clock.advance(2 * 86400)
    waiting_plan(gh)
    assert call(api, short_raw, "reply_plan", {"number": 8, "decision": "approve"}).status_code == 400


def test_grants_are_checked(signed, api):
    token_id, raw = manager_token(signed)
    assert grant(signed, token_id, "approve_plan", days=91).status_code == 400
    assert grant(signed, token_id, "approve_plan", days=0).status_code == 400
    assert grant(signed, 999, "approve_plan").status_code == 404
    r = signed.post(TOKENS, json={"role": "write"}, headers=csrf_headers(signed))
    agent_id = r.json()["id"]
    assert "only to an AI manager" in grant(signed, agent_id, "approve_plan").json()["error"]
    r = api.post(f"{TOKENS}/{token_id}/grants", json={"permission": "approve_plan"},
                 headers=bearer(raw))
    assert r.status_code in (401, 403)  # a token never grants anything, to itself or another


def test_an_ordinary_agent_token_is_unchanged(signed, api, gh):
    r = signed.post(TOKENS, json={"role": "write"}, headers=csrf_headers(signed))
    raw = r.json()["token"]
    assert r.json()["kind"] == "agent"
    assert "AI manager" not in tools(api, raw)["read_briefing"]["description"]
    waiting_plan(gh)
    assert call(api, raw, "reply_plan", {"number": 8, "decision": "approve"}).status_code == 400


def test_the_chat_never_counts_as_a_granted_manager():
    ctx = orchestrator.ToolContext(store=None, project_id=1, repo=A, github=None, token="t",
                                   login="ana", manager=True, grants=frozenset(GRANTABLE))
    assert ctx.granted("approve_plan") is False  # only through an API token
