# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Claude Code planner/executor tests with a fake runner: the real CLI is never called."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from tradvia.cli import make_executor, make_planner
from tradvia.claude_code import ClaudeCodeExecutor, claude_code_plan, run_claude
from tradvia.graph import build_graph
from tradvia.llm import architect_plan
from test_hello_world import FakeChat, FakeGitHub, planner, start

PLAN = {"summary": "s", "plan": ["a", "b"], "question": "Posso?"}


def claude_output(result, cost=0.5, ms=2000, is_error=False):
    return json.dumps({"result": result, "total_cost_usd": cost, "duration_ms": ms,
                       "num_turns": 3, "is_error": is_error})


def fake_runner(tmp_path, result, files=(), status=None):
    """`claude` writes `files` into tmp_path; `git status` lists them (or `status`)."""
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "git":
            lines = status if status is not None else [f"?? {f}" for f in files]
            return SimpleNamespace(returncode=0, stdout="".join(l + "\0" for l in lines), stderr="")
        for f in files:
            (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / f).write_text(f"content of {f}")
        return SimpleNamespace(returncode=0, stdout=claude_output(result), stderr="")

    runner.calls = calls
    return runner


def test_run_claude_parses_json_and_limits_tools(tmp_path):
    runner = fake_runner(tmp_path, "hi")
    data = run_claude("p", str(tmp_path), "Read,Glob", 5, runner=runner)
    assert data["result"] == "hi" and data["total_cost_usd"] == 0.5
    cmd = runner.calls[0]
    assert cmd[:3] == ["claude", "-p", "p"] and "--output-format" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Glob"


def test_run_claude_errors_are_clear(tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError

    with pytest.raises(RuntimeError, match="not found"):
        run_claude("p", str(tmp_path), "Read", runner=missing)
    bad = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="oops", stderr="")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        run_claude("p", str(tmp_path), "Read", runner=bad)
    err = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=claude_output("x", is_error=True),
                                            stderr="")
    with pytest.raises(RuntimeError, match="failed"):
        run_claude("p", str(tmp_path), "Read", runner=err)

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(RuntimeError, match="timed out"):
        run_claude("p", str(tmp_path), "Read", runner=slow)


def test_plan_has_architect_shape_and_cost(tmp_path):
    p = claude_code_plan("T", "B", 1, runner=fake_runner(tmp_path, json.dumps(PLAN)),
                         cwd=str(tmp_path))
    assert p["plan"] == ["a", "b"] and p["question"] == "Posso?"
    assert p["_cost"]["usd"] == 0.5 and p["_cost"]["duration_s"] == 2


@pytest.mark.parametrize("result", ["not json", json.dumps({"plan": [], "question": "q"}),
                                    json.dumps({"plan": ["a"]})])
def test_invalid_plan_raises_clear_error(tmp_path, result):
    with pytest.raises(ValueError, match="Claude Code plan"):
        claude_code_plan("T", "B", 1, runner=fake_runner(tmp_path, result), cwd=str(tmp_path))


def approved_card_app(gh, chat, executor, cwd):
    app = build_graph(gh, chat, planner=planner, executor=executor, cwd=str(cwd))
    _, config = start(app)
    return app.invoke(Command(resume={"text": "Aprovado", "by": "anderson"}), config)


def test_executor_happy_path_opens_pr_with_many_files(tmp_path):
    files = ["core/src/a.py", "core/tests/test_a.py", "docs/a.md"]
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "did it", files))
    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ex, tmp_path)

    assert result["status"] == "IN_REVIEW_PR"
    assert sorted(p for _, p in gh.files) == sorted(files)
    assert gh.files[("tradvia/issue-1", "docs/a.md")] == "content of docs/a.md"
    assert "did it" in gh.prs[0][2] and "$0.5000" in gh.prs[0][2]
    assert result["cost"]["usd"] == pytest.approx(0.51)  # planner 0.01 + executor 0.5
    assert result["cost"]["duration_s"] == pytest.approx(2)


def test_executor_blocks_github_changes_and_returns_to_waiting_human(tmp_path):
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done",
                                               [".github/workflows/x.yml", "docs/a.md"]))
    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ex, tmp_path)

    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == [] and gh.files == {}
    assert ".github/workflows/x.yml" in chat.messages[-1]


def test_executor_checks_both_sides_of_a_rename(tmp_path):
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done", status=["R  docs/b.md", "CODEOWNERS"]))
    assert ex.execute({"source": {"issue_number": 1}, "title": "t"}, str(tmp_path))["changed_files"] \
        == ["docs/b.md", "CODEOWNERS"]


def test_defaults_stay_offline_note(monkeypatch):
    monkeypatch.delenv("TRADVIA_PLANNER", raising=False)
    monkeypatch.delenv("TRADVIA_EXECUTOR", raising=False)
    assert make_planner() is architect_plan
    assert make_executor() is None
    assert make_planner("claude-code") is claude_code_plan
    assert isinstance(make_executor("claude-code"), ClaudeCodeExecutor)
