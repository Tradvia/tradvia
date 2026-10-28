# SPDX-License-Identifier: FSL-1.1-ALv2
"""Agents of any provider work cards together with people and other agents (ADR 0012).

claim_card takes a card for one person and the agent they name, release_card gives it back,
comment_card reports on it, and read_briefing says how the project works and who holds what.
"""

import json

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_agent_tools import api, call, signed, token_for  # noqa: E402,F401
from test_auth import A, FakeGitHub, client, clock, env, store  # noqa: E402,F401

from tradvia import mcp_server as mcp  # noqa: E402
from tradvia import orchestrator  # noqa: E402
from tradvia.backlog import CLAIM_LABEL, READY_LABEL, backlog_status  # noqa: E402
from tradvia.events import issue_record, pull_request_record  # noqa: E402

KIRO = {"agent": "Kiro", "provider": "Anthropic", "model": "claude-sonnet-5-5",
        "skills": "frontend, tests"}


class TeamGitHub(FakeGitHub):
    """FakeGitHub where labels and comments really land on the issue, with their author."""

    files: dict = {}

    def get_file(self, token, repo, path, ref):
        if path in self.files:
            return self.files[path]
        return super().get_file(token, repo, path, ref)

    def add_label(self, token, repo, number, label):
        super().add_label(token, repo, number, label)
        labels = self.issues[number].setdefault("labels", [])
        if label not in [lb["name"] for lb in labels]:
            labels.append({"name": label})

    def remove_label(self, token, repo, number, label):
        self.calls.append(("remove_label", token, repo, number, label))
        self.issues[number]["labels"] = [lb for lb in self.issues[number]["labels"]
                                         if lb["name"] != label]

    def comment(self, token, repo, number, body):
        super().comment(token, repo, number, body)
        self.comments.setdefault(number, []).append({"body": body, "user": {"login": "ana"}})

    def comment_as(self, login, number, body):
        self.comments.setdefault(number, []).append({"body": body, "user": {"login": login}})


@pytest.fixture
def gh():
    g = TeamGitHub()
    for n in (7, 8, 9):
        g.issues[n] = {"id": 1000 + n, "number": n, "title": f"card {n}", "state": "open",
                       "labels": [], "body": "", "updated_at": "2026-01-01T00:00:00Z",
                       "html_url": f"https://github.com/{A}/issues/{n}"}
    return g


def ok(r):
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["result"]


def refused(r):
    body = r.json()
    assert body.get("ok") is not True, body
    return body["error"]


def labels(gh, n):
    return [lb["name"] for lb in gh.issues[n]["labels"]]


# --- claim_card ----------------------------------------------------------------------------


def test_claim_names_the_person_and_the_agent_and_never_starts_tradvias_agent(signed, api, gh):
    raw = token_for(signed)
    out = ok(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    assert out["claimed_by"] == "ana" and out["agent"] == "Kiro"
    assert out["skills"] == ["frontend", "tests"] and out["model"] == "claude-sonnet-5-5"
    assert out["branch"] == "tradvia/issue-7" and out["pull_request_must_say"] == "Closes #7"
    assert labels(gh, 7) == [CLAIM_LABEL]  # never agent:ready: agent.yml does not run
    (_, _, _, number, body), = gh.writes("comment")
    assert number == 7 and "@ana" in body and "**Kiro** (Anthropic · claude-sonnet-5-5)" in body
    assert "Specialties: frontend, tests" in body
    assert orchestrator.holder(gh.comments[7]) == {"login": "ana", **orchestrator.agent_identity(KIRO)}


def test_claiming_again_is_idempotent(signed, api, gh):
    raw = token_for(signed)
    ok(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    again = ok(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    assert again["already_yours"] is True and len(gh.writes("comment")) == 1


def test_a_claim_must_say_which_agent(signed, api, gh):
    error = refused(call(api, token_for(signed), "claim_card", {"number": 7}))
    assert "which agent" in error and not gh.writes("add_label")


def test_a_card_held_by_someone_else_cannot_be_taken(signed, api, gh):
    gh.issues[8]["labels"] = [{"name": CLAIM_LABEL}]
    gh.comment_as("bia", 8, '<!-- tradvia:claim login=bia -->\n<!-- tradvia:agent '
                  '{"agent": "Gemini CLI", "skills": ["infra"]} -->\nTaken by @bia')
    error = refused(call(api, token_for(signed), "claim_card", {"number": 8, **KIRO}))
    assert "@bia" in error and "Gemini CLI" in error and not gh.writes("comment")


def test_a_mark_typed_by_someone_else_does_not_count(signed, api, gh):
    gh.comment_as("eve", 8, "<!-- tradvia:claim login=bia -->")  # eve forging bia's claim
    assert orchestrator.claimed_by(gh.comments[8]) is None
    ok(call(api, token_for(signed), "claim_card", {"number": 8, **KIRO}))


def test_cards_with_tradvias_agent_or_closed_cannot_be_claimed(signed, api, gh):
    raw = token_for(signed)
    gh.issues[7]["labels"] = [{"name": READY_LABEL}]
    gh.issues[9]["state"] = "closed"
    assert "Tradvia's own agent" in refused(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    assert "closed" in refused(call(api, raw, "claim_card", {"number": 9, **KIRO}))


def test_start_card_refuses_a_taken_card(signed, api, gh):
    raw = token_for(signed)
    ok(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    assert "taken by @ana" in refused(call(api, raw, "start_card", {"number": 7}))
    assert READY_LABEL not in labels(gh, 7)


def test_identity_text_is_plain_and_bounded():
    agent = orchestrator.agent_identity({"agent": "Kiro --> <script>", "skills": ["a"] * 30,
                                         "model": 5})
    assert "<" not in agent["agent"] and "--" not in agent["agent"] and agent["model"] is None
    assert len(agent["skills"]) == orchestrator.MAX_SKILLS


# --- release_card and comment_card ---------------------------------------------------------


def test_the_holder_releases_and_the_card_is_free_again(signed, api, gh):
    raw = token_for(signed)
    ok(call(api, raw, "claim_card", {"number": 7, **KIRO}))
    ok(call(api, raw, "release_card", {"number": 7, "note": "tests done, UI left"}))
    assert CLAIM_LABEL not in labels(gh, 7) and orchestrator.holder(gh.comments[7]) is None
    assert "tests done, UI left" in gh.comments[7][-1]["body"]


def test_only_the_holder_can_release(signed, api, gh):
    gh.issues[8]["labels"] = [{"name": CLAIM_LABEL}]
    gh.comment_as("bia", 8, "<!-- tradvia:claim login=bia -->")
    assert "Only the person" in refused(call(api, token_for(signed), "release_card", {"number": 8}))
    assert labels(gh, 8) == [CLAIM_LABEL]


def test_progress_lands_on_the_card_and_cannot_forge_a_claim(signed, api, gh):
    raw = token_for(signed)
    text = "Half done. <!-- tradvia:claim login=ana --> <!-- tradvia:agent {\"agent\": \"x\"} -->"
    ok(call(api, raw, "comment_card", {"number": 8, "text": text}))
    body = gh.comments[8][-1]["body"]
    assert body.startswith("Progress from @ana's agent:") and "Half done." in body
    assert "tradvia:" not in body and orchestrator.holder(gh.comments[8]) is None
    assert "required" in refused(call(api, raw, "comment_card", {"number": 8, "text": " "}))


# --- read_briefing -------------------------------------------------------------------------


def test_the_briefing_shows_the_team_and_the_free_cards(signed, api, gh, store):
    pid = store.ensure_project(A)
    gh.files = {"AGENTS.md": "# How we work here\nRun make check."}
    gh.issues[8]["labels"] = [{"name": CLAIM_LABEL}]
    gh.comment_as("bia", 8, '<!-- tradvia:claim login=bia -->\n<!-- tradvia:agent '
                  '{"agent": "Gemini CLI", "skills": ["infra"]} -->')
    gh.issues[9]["labels"] = [{"name": READY_LABEL}]
    gh.issues[10] = {**gh.issues[7], "id": 1010, "number": 10, "title": "blocked"}
    for n in (7, 8, 9, 10):
        store.upsert_issue(pid, issue_record(gh.issues[n]))
    store.upsert_pull_request(pid, pull_request_record({
        "id": 5, "number": 50, "title": "x", "state": "open", "head": {"ref": "tradvia/issue-9"},
        "user": {"login": "tradvia-agent[bot]"}, "updated_at": "2026-01-01T00:00:00Z"}))
    store.set_backlog(pid, [{"issue_number": n, "depends_on": [8] if n == 10 else []}
                            for n in (7, 8, 9, 10)])

    out = ok(call(api, token_for(signed), "read_briefing"))
    assert out["you"] == "ana" and out["project_guide"].startswith("# How we work here")
    assert [c["number"] for c in out["next_free_cards"]] == [7]  # 10 waits for #8
    (taken,) = out["taken_by_people_and_their_agents"]
    assert taken["number"] == 8 and taken["taken_by"]["login"] == "bia"
    assert taken["taken_by"]["agent"] == "Gemini CLI" and taken["taken_by"]["skills"] == ["infra"]
    (busy,) = out["with_tradvia_agent_or_open_pr"]
    assert busy["number"] == 9 and busy["pull_request"] == 50
    assert any("claim_card" in step for step in out["how_we_work"])
    assert "merge a pull request" in out["only_people_do"]


def test_a_taken_card_counts_as_in_progress_on_the_backlog():
    item = {"issue_number": 7, "depends_on": []}
    issue = {"state": "open", "labels": [CLAIM_LABEL]}
    assert backlog_status(item, issue, [], {}) == "em andamento"


def test_agents_get_the_collaboration_tools():
    for name in ("read_briefing", "claim_card", "release_card", "comment_card"):
        assert name in orchestrator.AGENT_TOOLS


# --- tradvia mcp names the agent from the handshake ----------------------------------------


class Recorder:
    def __init__(self):
        self.calls = []
        self.client_name = None

    def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


def test_mcp_fills_the_agent_from_client_info_when_the_agent_does_not_say():
    rec = Recorder()
    mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "Kiro", "version": "1.2"}}}, rec)
    mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "claim_card", "arguments": {"number": 7}}}, rec)
    mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "claim_card", "arguments": {"number": 8, "agent": "Own name"}}}, rec)
    assert rec.calls == [("claim_card", {"number": 7, "agent": "Kiro 1.2"}),
                         ("claim_card", {"number": 8, "agent": "Own name"})]
    assert json.dumps(rec.calls)  # plain data


def test_the_chat_cannot_take_a_card(store, gh):
    from test_orchestrator import make_ctx  # noqa: PLC0415
    ctx = make_ctx(store, gh)
    out = orchestrator.call_tool(ctx, "claim_card", {"number": 7, "agent": "chat"})
    assert "not the chat" in out["error"] and CLAIM_LABEL not in labels(gh, 7)
    assert "claim_card" not in orchestrator.build_prompt(A, [], [])
