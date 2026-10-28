# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tradvia MCP server (ADR 0012): the orchestrator's agent tools over the Model Context Protocol.

`tradvia mcp` serves MCP on stdio, so an MCP client (Claude Code, Kiro, Gemini CLI, ...) can read
the cards of a project, start or create them, and answer a plan with an adjustment. The server
holds no rules of its own: each call goes to the panel's agent routes with the agent's token
(`TRADVIA_TOKEN`), and the panel applies the token's scope, the tool allow-list and the human
gate. Configuration: `TRADVIA_URL`, `TRADVIA_PROJECT` (owner/repo) and `TRADVIA_TOKEN`.

stdout is the protocol channel: nothing else may be printed to it. Diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, TextIO

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "tradvia", "version": "0.1.0"}
INSTRUCTIONS = ("Tradvia: a card is a GitHub issue; read it before you act on it. Approving a plan, "
                "merging and deploying are for a person: asking for them queues a pending item "
                "for that person.")
TIMEOUT_S = 30
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
INTERNAL_ERROR = -32603
# JSON Schema type of each argument; any other argument is a string.
ARG_TYPES = {"number": "integer", "run_id": "integer", "limit": "integer", "order": "array"}


class ToolFailed(Exception):
    """A call that did not succeed. Its message goes to the agent, and never carries the token."""


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class AgentClient:
    """The agent routes of one project, over HTTP."""

    def __init__(self, base_url: str, project: str, token: str, http: Any = None):
        owner, sep, repo = project.partition("/")
        if not sep or not owner or not repo or "/" in repo:
            raise ValueError("TRADVIA_PROJECT must be owner/repo")
        if http is None:
            import requests

            http = requests
        self.token = token
        self.base = f"{base_url.rstrip('/')}/api/projects/{project}/agent"
        self.http = http

    def _safe(self, text: str) -> str:
        return text.replace(self.token, "[token]") if self.token else text

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if method == "POST":  # a fresh key per call: the panel refuses an exact repeat in 5 s
            headers["X-Tradvia-Idempotency-Key"] = uuid.uuid4().hex
        try:
            response = self.http.request(method, self.base + path, json=body, headers=headers,
                                         timeout=TIMEOUT_S)
        except Exception as e:  # connection errors can echo the URL; the token never appears
            raise ToolFailed(self._safe(f"could not reach Tradvia: {type(e).__name__}")) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise ToolFailed(self._safe(str(message or f"Tradvia answered {response.status_code}")))
        if not isinstance(data, dict):
            raise ToolFailed("Tradvia answered something that is not JSON")
        return data

    def tools(self) -> list[dict[str, Any]]:
        tools = self._send("GET", "/tools").get("tools")
        if not isinstance(tools, list):
            raise ToolFailed("unexpected answer for the tool list")
        return tools

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._send("POST", "/call", {"tool": name, "args": args})


def tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    properties = {str(a): {"type": ARG_TYPES.get(str(a), "string")} for a in tool.get("args", [])}
    return {"name": tool["name"], "description": tool.get("description", ""),
            "inputSchema": {"type": "object", "properties": properties,
                            "additionalProperties": False}}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _dispatch(method: str, params: dict[str, Any], client: AgentClient) -> dict[str, Any]:
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool_schema(t) for t in client.tools()]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            raise _RpcError(INVALID_PARAMS, "tools/call needs a name and an arguments object")
        try:
            result = client.call(name, args)
        except ToolFailed as e:  # a failed tool is a result with isError, not a protocol error
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")


def handle(message: Any, client: AgentClient) -> dict[str, Any] | None:
    """The reply to one JSON-RPC message, or None for a notification (no id, no reply)."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "invalid request")
    if "id" not in message:
        return None  # notifications/initialized and the like
    msg_id, method = message["id"], message.get("method")
    params = message.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return _error(msg_id, INVALID_REQUEST, "invalid request")
    try:
        return {"jsonrpc": "2.0", "id": msg_id, "result": _dispatch(method, params, client)}
    except _RpcError as e:
        return _error(msg_id, e.code, str(e))
    except ToolFailed as e:  # from tools/list: the agent cannot see the tools right now
        return _error(msg_id, INTERNAL_ERROR, str(e))


def serve(stdin: TextIO, stdout: TextIO, client: AgentClient) -> None:
    """One JSON message per line in, one reply per line out, until stdin closes."""
    while True:
        line = stdin.readline()
        if not line:
            return
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: dict[str, Any] | None = _error(None, PARSE_ERROR, "parse error")
        else:
            reply = handle(message, client)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> None:
    url = os.environ.get("TRADVIA_URL", "")
    token = os.environ.get("TRADVIA_TOKEN", "")
    project = os.environ.get("TRADVIA_PROJECT", "")
    missing = [name for name, value in (("TRADVIA_URL", url), ("TRADVIA_TOKEN", token),
                                        ("TRADVIA_PROJECT", project)) if not value]
    if missing:
        print(f"tradvia mcp needs {', '.join(missing)} (see docs/mcp.md)", file=sys.stderr)
        raise SystemExit(2)
    try:
        client = AgentClient(url, project, token)
    except ValueError as e:
        print(f"tradvia mcp: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    serve(sys.stdin, sys.stdout, client)
