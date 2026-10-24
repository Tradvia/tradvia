# SPDX-License-Identifier: FSL-1.1-ALv2
"""Stagnation in the executor and the graph, with a fake stream-json runner: offline."""

import json
from types import SimpleNamespace

import pytest

from tradvia.claude_code import ClaudeCodeExecutor, StagnationError
from test_claude_code import approved_card_app
from test_hello_world import FakeChat, FakeGitHub


def tool_use(name, **params):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": params}]}})


def tool_result(text):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": text}]}})


def stream_runner(scripts):
    """One list of stream-json lines per `claude` call; git reports no changes."""
    calls, queue = [], list(scripts)

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert "stream-json" in cmd and "--verbose" in cmd
        return SimpleNamespace(returncode=0, stdout="\n".join(queue.pop(0)), stderr="")

    runner.calls = calls
    return runner


LOOP = [tool_use("Bash", command="make check"), tool_result("boom")]
RESULT = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.2,
                     "duration_ms": 1000, "is_error": False})
CARD = {"source": {"issue_number": 1}, "title": "T", "plan": []}


def claude_calls(runner):
    return [c for c in runner.calls if c[0] == "claude"]


def test_warn_restarts_once_with_reflective_message(tmp_path):
    changed = [tool_use("Read", file_path="core/src/a.py"), tool_result("ok"), RESULT]
    runner = stream_runner([LOOP * 3, changed])
    run = ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path))
    calls = claude_calls(runner)
    assert len(calls) == 2 and run["summary"] == "done" and run["cost_usd"] == 0.2
    assert "You repeated" not in calls[0][2]
    assert "You repeated Bash 3 times; change your approach or ask for help." in calls[1][2]


def test_repeat_after_warning_raises_stagnation_error(tmp_path):
    runner = stream_runner([LOOP * 3, LOOP * 3])
    with pytest.raises(StagnationError) as e:
        ClaudeCodeExecutor(runner=runner).execute(CARD, str(tmp_path))
    assert e.value.tool == "Bash" and e.value.count == 4


def test_executor_respects_repeat_limit(tmp_path):
    runner = stream_runner([LOOP * 2 + [RESULT]])
    ClaudeCodeExecutor(runner=runner, repeat_limit=5).execute(CARD, str(tmp_path))
    assert len(claude_calls(runner)) == 1


def test_stagnation_returns_card_to_waiting_human_with_reason(tmp_path):
    runner = stream_runner([LOOP * 3, LOOP * 3])
    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ClaudeCodeExecutor(runner=runner), tmp_path)
    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == []
    assert "`Bash`" in chat.messages[-1] and "4 vezes" in chat.messages[-1]
    assert result["history"][-1]["reason"].startswith("Stagnation: Bash repeated 4 times")
