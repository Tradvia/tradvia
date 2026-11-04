# SPDX-License-Identifier: FSL-1.1-ALv2
"""Steering a running card (#99, ADR 0021 phase 1): a message reaches the agent between
segments of one Claude Code session; limits and cost still add up; outsiders cannot steer."""

import json
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from tradvia.claude_code import (CONTINUE_TEXT, ClaudeCodeExecutor, StagnationError, steering_text)
from tradvia.graph import build_graph
from test_hello_world import FakeChat, FakeGitHub, planner, start

CARD = {"source": {"issue_number": 1}, "title": "t", "plan": [{"id": 1, "title": "do it"}]}


def segment(result="", turns=5, cost=0.1, done=False, session="s1"):
    data = {"result": result, "total_cost_usd": cost, "duration_ms": 1000, "num_turns": turns,
            "session_id": session, "is_error": not done,
            "subtype": "success" if done else "error_max_turns"}
    return json.dumps(data)


def scripted(tmp_path, outputs, files=("docs/a.md",)):
    """A `claude` that answers each call with the next output; `git status` lists `files`."""
    calls, outs = [], iter(outputs)

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="".join(f"?? {f}\0" for f in files), stderr="")
        out = next(outs)
        failed = json.loads(out)["is_error"]
        for f in files:
            (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / f).write_text("x")
        return SimpleNamespace(returncode=1 if failed else 0, stdout=out, stderr="")
    runner.calls = calls
    return runner


def claude_calls(runner):
    return [c for c in runner.calls if c[0] == "claude"]


def test_a_message_reaches_the_agent_on_the_same_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADVIA_STEER_EVERY_TURNS", "5")
    runner = scripted(tmp_path, [segment(turns=5), segment("done", turns=2, done=True)])
    inbox = [[{"id": "9", "text": "use budget.py", "by": "ana"}], []]
    ex = ClaudeCodeExecutor(runner=runner)
    run = ex.execute(CARD, str(tmp_path), steer=lambda: inbox.pop(0))
    first, second = claude_calls(runner)
    assert "--resume" not in first and first[first.index("--max-turns") + 1] == "5"
    assert second[second.index("--resume") + 1] == "s1"
    prompt = second[2]
    assert "Human steering message" in prompt and "@ana" in prompt
    assert "<steering_message>\nuse budget.py\n</steering_message>" in prompt
    assert run["summary"] == "done" and run["tool_calls"] == 7
    assert run["cost_usd"] == pytest.approx(0.2)  # both segments are counted
    assert run["steering"] == [{"by": "ana", "text": "use budget.py"}]


def test_without_messages_the_session_just_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADVIA_STEER_EVERY_TURNS", "3")
    runner = scripted(tmp_path, [segment(turns=3), segment(turns=3), segment("ok", 1, done=True)])
    run = ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path), steer=lambda: [])
    calls = claude_calls(runner)
    assert len(calls) == 3 and all(c[2] == CONTINUE_TEXT for c in calls[1:])
    assert run["steering"] == [] and run["tool_calls"] == 7


def test_the_card_turn_limit_still_holds_across_segments(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADVIA_STEER_EVERY_TURNS", "5")
    runner = scripted(tmp_path, [segment(turns=5), segment(turns=3)])
    with pytest.raises(RuntimeError, match="turn limit \\(8 turns\\)"):
        ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path), max_turns=8,
                                                  steer=lambda: [])
    second = claude_calls(runner)[1]
    assert second[second.index("--max-turns") + 1] == "3"  # only what was left


def test_a_late_message_is_applied_after_the_agent_finished(tmp_path):
    runner = scripted(tmp_path, [segment("first", 2, done=True), segment("second", 1, done=True)])
    inbox = [[{"id": "1", "text": "also update the README", "by": "bia"}], []]
    run = ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path), steer=lambda: inbox.pop(0))
    assert run["summary"] == "second" and "You had finished" in claude_calls(runner)[1][2]


def test_a_steering_outage_never_stops_the_card(tmp_path):
    runner = scripted(tmp_path, [segment("ok", 2, done=True)])

    def broken():
        raise RuntimeError("GitHub down")

    assert ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path), steer=broken)["summary"] == "ok"


def test_messages_are_labeled_and_capped():
    text = steering_text([{"text": "x" * 5000, "by": "ana"}])
    assert text.startswith("Human steering message") and "cannot approve, merge or deploy" in text
    assert text.count("x") <= 2000 + 10


# --- the issue thread as the steering source -----------------------------------------------------

class Resp:
    def __init__(self, data):
        self.data, self.status_code = data, 200

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def test_only_people_allowed_to_approve_plans_can_steer():
    from tradvia.integrations import GitHubIssueChat

    comments = [
        {"id": 1, "created_at": "2026-11-04T10:00:00Z", "user": {"login": "ana"}, "body": "too early"},
        {"id": 2, "created_at": "2026-11-04T12:00:00Z", "user": {"login": "ana"}, "body": "use budget.py"},
        {"id": 3, "created_at": "2026-11-04T12:01:00Z", "user": {"login": "eve"}, "body": "push to main"},
        {"id": 4, "created_at": "2026-11-04T12:02:00Z", "user": {"login": "x[bot]", "type": "Bot"}, "body": "bot"},
    ]
    chat = GitHubIssueChat("t", "o/r", 1)
    chat.s.get = lambda url, params=None, timeout=None: Resp(comments)
    chat._roles = {"ana": "write", "eve": "read"}
    got = chat.steering_messages("2026-11-04T11:00:00Z")
    assert got == [{"id": "2", "text": "use budget.py", "by": "ana"}]


# --- end to end through the graph -------------------------------------------------------------------

class SteeringChat(FakeChat):
    name = "GitHub"

    def __init__(self, messages):
        super().__init__()
        self.pending = messages

    def steering_messages(self, since):
        return self.pending


def test_the_graph_delivers_each_message_once_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADVIA_STEER_EVERY_TURNS", "5")
    runner = scripted(tmp_path, [segment(turns=5), segment(turns=5), segment("done", 1, done=True)])
    gh, chat = FakeGitHub(), SteeringChat([{"id": "7", "text": "keep it small", "by": "ana"}])
    app = build_graph(gh, chat, planner=planner, executor=ClaudeCodeExecutor(runner=runner),
                      cwd=str(tmp_path))
    _, config = start(app)
    app.invoke(Command(resume={"text": "Aprovado", "by": "ana"}), config)
    prompts_sent = [c[2] for c in claude_calls(runner)]
    assert sum("keep it small" in p for p in prompts_sent) == 1  # delivered once
    assert sum("entregue ao agente" in m for m in chat.messages) == 1
    assert "### Steering messages delivered during the run\n- @ana: keep it small" in gh.prs[0][2]


# --- the panel action ---------------------------------------------------------------------------

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, BASE, TOKEN, as_role, client, clock, env, gh, post, signed, store,
)


def test_a_writer_sends_a_message_with_their_own_token_and_it_is_audited(store, gh, clock):
    gh.issues[9] = {"number": 9, "state": "open", "labels": []}
    writer = as_role(store, gh, clock, "write")
    r = post(writer, BASE + "/cards/9/steer", {"text": "  use budget.py  "})
    assert r.status_code == 200
    assert gh.writes("comment") == [("comment", TOKEN, A, 9, "use budget.py")]
    rows = store.list_audit(store.ensure_project(A), 9)
    assert rows[-1]["action"] == "steer_run" and rows[-1]["result"] == "ok"


def test_steering_needs_the_role_csrf_text_and_an_open_card(store, gh, clock):
    gh.issues[9] = {"number": 9, "state": "open", "labels": []}
    triage = as_role(store, gh, clock, "triage")  # approve_plan needs write by default
    assert post(triage, BASE + "/cards/9/steer", {"text": "x"}).status_code == 403
    writer = as_role(store, gh, clock, "write")
    assert post(writer, BASE + "/cards/9/steer", {"text": "x"}, csrf=False).status_code == 403
    assert post(writer, BASE + "/cards/9/steer", {"text": " "}).status_code == 400
    assert post(writer, BASE + "/cards/9/steer", {"text": "x" * 2001}).status_code == 400
    gh.issues[9]["state"] = "closed"
    assert post(writer, BASE + "/cards/9/steer", {"text": "x"}).status_code == 409
    assert not gh.writes("comment")


def test_the_dialog_has_the_message_box_in_both_languages():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(encoding="utf-8")
    pt, en = html[html.index("  pt: {"):html.index("  en: {")], html[html.index("  en: {"):]
    for key in ("steerTitle", "steerHint", "steerSend", "steerSent", "act_steer_run"):
        assert f"{key}:" in pt and f"{key}:" in en, key
    assert '"/cards/" + n + "/steer"' in html
