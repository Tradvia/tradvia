# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tradvia MCP server (ADR 0012): the protocol and the HTTP client, with a fake HTTP layer."""

import io
import json

import pytest

from tradvia import mcp_server as mcp

TOKEN = "tvt_" + "Q" * 43
CATALOG = [
    {"name": "create_card", "args": ["title", "description"], "description": "Create a card."},
    {"name": "reply_plan", "args": ["number", "decision", "text"], "description": "Answer a plan."},
]


class Reply:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("no JSON")
        return self._data


class FakeHttp:
    def __init__(self, replies=None, raises=None):
        self.replies = replies or {}
        self.raises = raises
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers,
                           "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.replies[(method, url.rsplit("/agent", 1)[1])]


def client(http):
    return mcp.AgentClient("https://app.example", "Tradvia/tradvia", TOKEN, http=http)


def rpc(method, params=None, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def test_initialize_echoes_a_supported_version_and_declares_tools():
    c = client(FakeHttp())
    reply = mcp.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}), c)
    assert reply["id"] == 1 and reply["result"]["protocolVersion"] == "2024-11-05"
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert reply["result"]["serverInfo"]["name"] == "tradvia"
    unknown = mcp.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}), c)
    assert unknown["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply():
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert mcp.handle(note, client(FakeHttp())) is None


def test_tools_list_turns_the_catalog_into_schemas_with_the_token():
    http = FakeHttp({("GET", "/tools"): Reply(200, {"tools": CATALOG})})
    reply = mcp.handle(rpc("tools/list"), client(http))
    tools = {t["name"]: t for t in reply["result"]["tools"]}
    assert tools["create_card"]["inputSchema"] == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "additionalProperties": False,
    }
    assert tools["reply_plan"]["inputSchema"]["properties"]["number"] == {"type": "integer"}
    assert http.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_a_successful_call_returns_the_json_and_sends_an_idempotency_key():
    http = FakeHttp({("POST", "/call"): Reply(200, {"ok": True, "result": {"number": 9}})})
    reply = mcp.handle(rpc("tools/call", {"name": "create_card",
                                          "arguments": {"title": "T", "description": ""}}),
                       client(http))
    result = reply["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"ok": True, "result": {"number": 9}}
    sent = http.calls[0]
    assert sent["json"] == {"tool": "create_card", "args": {"title": "T", "description": ""}}
    assert sent["headers"]["X-Tradvia-Idempotency-Key"]


def test_a_refusal_is_a_tool_error_not_a_protocol_error():
    message = "'approve_plan' is not available to the orchestrator (ADR 0004)."
    http = FakeHttp({("POST", "/call"): Reply(400, {"error": message})})
    reply = mcp.handle(rpc("tools/call", {"name": "reply_plan", "arguments": {
        "number": 7, "decision": "approve", "text": "ok"}}), client(http))
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    assert "not available" in reply["result"]["content"][0]["text"]


def test_the_token_never_reaches_an_error_message():
    leaking = FakeHttp({("POST", "/call"): Reply(500, {"error": f"leaked {TOKEN}"})})
    reply = mcp.handle(rpc("tools/call", {"name": "read_card", "arguments": {"number": 1}}),
                       client(leaking))
    text = reply["result"]["content"][0]["text"]
    assert TOKEN not in text and "[token]" in text

    broken = FakeHttp(raises=RuntimeError(f"connection to {TOKEN} failed"))
    reply = mcp.handle(rpc("tools/call", {"name": "read_card", "arguments": {}}), client(broken))
    assert TOKEN not in json.dumps(reply) and reply["result"]["isError"] is True


def test_a_failed_tool_list_is_an_internal_error_with_the_reason():
    http = FakeHttp({("GET", "/tools"): Reply(403, {"error": "Agents use an API token."})})
    reply = mcp.handle(rpc("tools/list"), client(http))
    assert reply["error"]["code"] == mcp.INTERNAL_ERROR
    assert "API token" in reply["error"]["message"]


def test_unknown_methods_bad_params_and_bad_messages_are_protocol_errors():
    assert mcp.handle(rpc("resources/list"), client(FakeHttp()))["error"]["code"] == -32601
    bad = mcp.handle(rpc("tools/call", {"arguments": {}}), client(FakeHttp()))
    assert bad["error"]["code"] == mcp.INVALID_PARAMS
    assert mcp.handle([1, 2], client(FakeHttp()))["error"]["code"] == mcp.INVALID_REQUEST


def test_serve_answers_each_line_and_skips_notifications():
    http = FakeHttp({("GET", "/tools"): Reply(200, {"tools": CATALOG})})
    lines = [json.dumps(rpc("initialize", {}, 1)),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             "not json",
             json.dumps(rpc("tools/list", {}, 2))]
    stdout = io.StringIO()
    mcp.serve(io.StringIO("\n".join(lines) + "\n"), stdout, client(http))
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(replies) == 3
    assert replies[0]["id"] == 1
    assert replies[1]["id"] is None and replies[1]["error"]["code"] == mcp.PARSE_ERROR
    assert replies[2]["id"] == 2 and replies[2]["result"]["tools"][0]["name"] == "create_card"


def test_the_client_checks_the_project_and_builds_the_agent_url():
    with pytest.raises(ValueError):
        mcp.AgentClient("https://x", "only-owner", TOKEN, http=FakeHttp())
    with pytest.raises(ValueError):
        mcp.AgentClient("https://x", "a/b/c", TOKEN, http=FakeHttp())
    c = mcp.AgentClient("https://app.example/", "Tradvia/tradvia", TOKEN, http=FakeHttp())
    assert c.base == "https://app.example/api/projects/Tradvia/tradvia/agent"


def test_main_needs_its_three_variables_and_keeps_stdout_clean(monkeypatch, capsys):
    for name in ("TRADVIA_URL", "TRADVIA_TOKEN", "TRADVIA_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        mcp.main()
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert "TRADVIA_URL" in captured.err and captured.out == ""
