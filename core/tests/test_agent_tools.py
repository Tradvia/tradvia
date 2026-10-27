# SPDX-License-Identifier: FSL-1.1-ALv2
"""Agents (ADR 0012): the orchestrator's tools through an API token, and only those."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A,
    B,
    BASE,
    TOKEN,
    client,
    clock,
    csrf_headers,
    env,
    gh,
    login,
    make_client,
    store,
)

AGENT = BASE + "/agent"
_keys = iter(range(10_000))


def key():
    return f"agent-key-{next(_keys):08d}"


@pytest.fixture
def signed(client):
    login(client)  # ana, admin of A in the fake GitHub
    return client


@pytest.fixture
def api(store, gh, clock):
    """A client with no cookie: only a bearer token identifies it."""
    return make_client(store, gh, clock)


def token_for(signed, role="triage", project=A):
    r = signed.post(f"/api/projects/{project}/tokens", json={"role": role},
                    headers=csrf_headers(signed))
    assert r.status_code == 201, r.text
    return r.json()["token"]


def bearer(raw, idem=True):
    headers = {"Authorization": f"Bearer {raw}"}
    if idem:
        headers["X-Tradvia-Idempotency-Key"] = key()
    return headers


def call(api, raw, tool, args=None):
    return api.post(AGENT + "/call", json={"tool": tool, "args": args or {}}, headers=bearer(raw))


def test_the_catalog_has_the_agent_tools_and_not_the_human_ones(signed, api):
    r = api.get(AGENT + "/tools", headers=bearer(token_for(signed), idem=False))
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert {"read_card", "create_card", "start_card", "reply_plan"} <= names
    assert not names & {"review_pr", "approve_plan", "merge_pr", "approve_deploy"}


def test_agent_routes_need_an_api_token_and_a_session_is_not_an_agent(signed, api, gh):
    assert api.get(AGENT + "/tools").status_code == 401
    r = signed.post(AGENT + "/call", json={"tool": "create_card", "args": {"title": "x"}},
                    headers=csrf_headers(signed))
    assert r.status_code == 403 and "API token" in r.json()["error"]
    assert not gh.writes("create_issue")


def test_a_token_of_another_project_is_refused(signed, api, gh, store):
    gh.perm[B] = ("admin", "admin")
    raw = token_for(signed)  # issued for A
    r = api.post(f"/api/projects/{B}/agent/call", json={"tool": "create_card", "args": {"title": "x"}},
                 headers=bearer(raw))
    assert r.status_code == 403 and "another project" in r.json()["error"]
    assert not gh.writes("create_issue")


def test_an_agent_creates_a_card_with_the_owner_token_and_is_audited(signed, api, gh, store):
    raw = token_for(signed)
    r = call(api, raw, "create_card", {"title": "Via MCP", "description": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    (write,) = gh.writes("create_issue")
    assert write[1] == TOKEN  # the owner's GitHub identity, never the server's
    rows = store.list_audit(store.ensure_project(A), None)
    tool_row = next(row for row in rows if row["action"] == "orchestrator:create_card")
    assert (tool_row["actor_login"], tool_row["result"], tool_row["via"]) == ("ana", "ok", "api_token")
    call_row = next(row for row in rows if row["action"] == "agent_call")
    assert (call_row["result"], call_row["via"]) == ("ok", "api_token")


def test_an_agent_starts_a_card(signed, api, gh):
    raw = token_for(signed)
    gh.issues[8] = {"number": 8, "state": "open", "labels": []}
    assert call(api, raw, "start_card", {"number": 8}).status_code == 200
    (write,) = gh.writes("add_label")
    assert write[1] == TOKEN and write[4] == "agent:ready"


def test_an_agent_reads_a_card(signed, api):
    r = call(api, token_for(signed), "read_card", {"number": 7})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_review_pr_is_never_available_to_an_agent(signed, api, gh, store):
    r = call(api, token_for(signed), "review_pr", {"number": 3})
    assert r.status_code == 400 and "not available to agents" in r.json()["error"]
    # the audit rows of a card are read by its number, so the refusal is found under issue 3
    rows = store.list_audit(store.ensure_project(A), 3)
    assert any(row["action"] == "orchestrator:review_pr" and row["result"] == "denied"
               and row["via"] == "api_token" for row in rows)


@pytest.mark.parametrize("tool", ["approve_plan", "merge_pr", "approve_deploy"])
def test_an_agent_cannot_approve_merge_or_deploy_and_the_person_gets_a_pending_item(
        signed, api, gh, store, tool):
    raw = token_for(signed, role="maintain")
    r = call(api, raw, tool, {"number": 7, "run_id": 7})
    assert r.status_code == 400 and "the human" in r.json()["error"]
    assert not gh.writes("comment") and not gh.writes("merge_pull_request")
    messages = store.list_messages(store.ensure_project(A), 50)
    assert any(m.get("kind") == "pending" for m in messages)


def test_an_agent_only_adjusts_a_plan_and_approval_stays_with_the_person(signed, api, gh, store):
    raw = token_for(signed, role="write")
    r = call(api, raw, "reply_plan", {"number": 7, "decision": "approve", "text": "Aprovado"})
    assert r.status_code == 400 and "Only 'adjust'" in r.json()["error"]
    assert not gh.writes("comment")
    messages = store.list_messages(store.ensure_project(A), 50)
    assert any(m.get("kind") == "pending" for m in messages)


@pytest.mark.parametrize("text", ["Aprovado, pode seguir", "approve it", "Rejeitado",
                                  "ok\nAprovado", "sim, aprovo"])
def test_an_adjustment_can_never_be_read_as_an_approval(text):
    # The comment an adjustment leaves is read by the graph as the human's reply. It must be
    # feedback whatever the agent writes, or an agent could approve a plan (ADR 0004, ADR 0012).
    from tradvia.actions import parse_reply
    from tradvia.graph import classify_reply

    comment = parse_reply("adjust", text)
    assert comment.startswith("Ajuste: ")
    assert classify_reply(comment) == "feedback"


def test_an_adjustment_is_refused_when_the_card_is_not_waiting_for_a_plan_reply(signed, api, gh):
    raw = token_for(signed, role="write")
    r = call(api, raw, "reply_plan", {"number": 7, "decision": "adjust", "text": "Mais testes"})
    assert r.status_code == 400 and "not waiting" in r.json()["error"]
    assert not gh.writes("comment")


def test_a_read_token_cannot_create_or_start_cards(signed, api, gh):
    raw = token_for(signed, role="read")
    assert call(api, raw, "create_card", {"title": "x", "description": ""}).status_code == 403
    assert call(api, raw, "start_card", {"number": 7}).status_code == 403
    assert not gh.writes("create_issue") and not gh.writes("add_label")


def test_a_repeated_key_creates_the_card_once(signed, api, gh):
    raw = token_for(signed)
    headers = {"Authorization": f"Bearer {raw}", "X-Tradvia-Idempotency-Key": key()}
    body = {"tool": "create_card", "args": {"title": "once", "description": ""}}
    first = api.post(AGENT + "/call", json=body, headers=headers)
    second = api.post(AGENT + "/call", json=body, headers=headers)
    assert first.status_code == 200
    assert second.json().get("duplicate") is True
    assert len(gh.writes("create_issue")) == 1


def test_unknown_tools_and_bad_bodies_are_400(signed, api):
    raw = token_for(signed)
    assert call(api, raw, "drop_everything").status_code == 400
    assert api.post(AGENT + "/call", data="not json", headers=bearer(raw)).status_code == 400
    assert api.post(AGENT + "/call", json={"tool": 3}, headers=bearer(raw)).status_code == 400


MCP = BASE + "/mcp"


def rpc(method, params=None, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def test_mcp_over_http_lists_and_calls_the_tools_with_the_owner_token(signed, api, gh):
    raw = token_for(signed)
    listed = api.post(MCP, json=rpc("tools/list"), headers=bearer(raw, idem=False)).json()
    assert "create_card" in {t["name"] for t in listed["result"]["tools"]}
    called = api.post(MCP, json=rpc("tools/call", {"name": "create_card", "arguments": {
        "title": "Via HTTP", "description": ""}}), headers=bearer(raw, idem=False)).json()
    assert called["result"]["isError"] is False
    (write,) = gh.writes("create_issue")
    assert write[1] == TOKEN


def test_mcp_over_http_refusals_are_tool_errors_and_reach_the_person(signed, api, gh, store):
    raw = token_for(signed, role="maintain")
    reply = api.post(MCP, json=rpc("tools/call", {"name": "approve_plan", "arguments": {"number": 7}}),
                     headers=bearer(raw, idem=False)).json()
    assert reply["result"]["isError"] is True
    assert "the human" in reply["result"]["content"][0]["text"]
    assert any(m.get("kind") == "pending" for m in store.list_messages(store.ensure_project(A), 50))
    assert not gh.writes("comment")


def test_mcp_over_http_needs_a_token_and_accepts_notifications(signed, api):
    assert api.post(MCP, json=rpc("tools/list")).status_code == 401
    r = signed.post(MCP, json=rpc("tools/list"), headers=csrf_headers(signed))
    assert r.status_code == 403 and "API token" in r.json()["error"]
    raw = token_for(signed)
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert api.post(MCP, json=note, headers=bearer(raw, idem=False)).status_code == 202
    assert api.get(MCP, headers=bearer(raw, idem=False)).status_code == 405


def test_mcp_over_http_a_retry_with_the_same_key_creates_one_card(signed, api, gh):
    raw = token_for(signed)
    headers = {"Authorization": f"Bearer {raw}", "X-Tradvia-Idempotency-Key": key()}
    body = rpc("tools/call", {"name": "create_card", "arguments": {"title": "retry", "description": ""}})
    api.post(MCP, json=body, headers=headers)
    api.post(MCP, json=body, headers=headers)
    assert len(gh.writes("create_issue")) == 1
    bad = api.post(MCP, json=body, headers={**headers, "X-Tradvia-Idempotency-Key": "no good"})
    assert bad.status_code == 400
