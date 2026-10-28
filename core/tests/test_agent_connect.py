# SPDX-License-Identifier: FSL-1.1-ALv2
""""Connect my AI" (ADR 0012): a person lists their own API tokens to connect their agent."""

import json
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_agent_tools import api, bearer, signed, token_for  # noqa: E402,F401
from test_auth import A, B, BASE, client, clock, csrf_headers, env, gh, store  # noqa: E402,F401

TOKENS = BASE + "/tokens"
DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"


def test_the_list_shows_my_tokens_without_their_values(signed, store):
    raw = token_for(signed, role="write")
    token_for(signed)
    r = signed.get(TOKENS)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["login"] == "ana" and body["project"] == A and body["role"] == "admin"
    assert body["mcp_http"] == f"/api/projects/{A}/mcp" and body["max_days"] == 90
    assert [t["role"] for t in body["tokens"]] == ["triage", "write"]  # newest first
    assert all(t["active"] for t in body["tokens"])
    text = json.dumps(body)
    assert raw not in text and "token_hash" not in text and "token_enc" not in text


def test_a_revoked_token_is_listed_as_inactive(signed):
    token_for(signed)
    (tok,) = signed.get(TOKENS).json()["tokens"]
    assert signed.delete(f"{TOKENS}/{tok['id']}", headers=csrf_headers(signed)).status_code == 200
    (tok,) = signed.get(TOKENS).json()["tokens"]
    assert tok["active"] is False and tok["revoked_at"] is not None


def test_offered_roles_never_exceed_my_own(signed, gh):
    gh.perm[A] = ("write", "write")
    assert signed.get(TOKENS).json()["roles"] == ["read", "triage", "write"]


def test_listing_needs_a_session_and_access(signed, api, gh):
    raw = token_for(signed)
    assert api.get(TOKENS).status_code == 401
    assert api.get(TOKENS, headers=bearer(raw, idem=False)).status_code in (401, 403)
    reader = signed.get(f"/api/projects/{B}/tokens").json()  # ana is only 'read' in B
    assert reader["role"] == "read" and reader["roles"] == ["read"]
    assert signed.get("/api/projects/Nope/nope/tokens").status_code == 404


def test_tokens_are_listed_per_project(signed, gh):
    gh.perm[B] = ("admin", "admin")
    token_for(signed)
    assert signed.get(f"/api/projects/{B}/tokens").json()["tokens"] == []


def test_the_panel_has_the_connect_screen_in_both_languages():
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="agent-section"' in html and "renderAgentConnect();" in html
    assert html.count("agentTitle:") == 2 and html.count("agentOnce:") == 2
    # the token is never put into a URL, storage or the generated snippets
    assert "localStorage.setItem(\"agent" not in html and '"${TRADVIA_TOKEN}"' in html
