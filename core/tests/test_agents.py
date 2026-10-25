# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Coding agent adapters (ADR 0010) with fake runners: no real CLI is ever called."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from tradvia.agents import NOT_REPORTED, Cost, make_agent
from tradvia.cli import make_executor, make_planner
from tradvia.codex_cli import CodexCliAgent
from tradvia.connections import BY_KEY
from tradvia.gemini_cli import EXEC_TOOLS, PLAN_TOOLS, GeminiCliAgent
from tradvia.graph import build_graph
from test_hello_world import FakeChat, FakeGitHub, planner, start

PLAN = {"summary": "s", "plan": ["a", "b"], "question": "Posso?"}
CARD = {"source": {"issue_number": 1}, "title": "T", "plan": []}
PROVIDERS = ["claude-code", "gemini-cli", "codex-cli"]


def output(provider, text, cmd, reported=True, tool_calls=3):
    """What the fake CLI prints for `text`, in the format of each provider."""
    if provider == "claude-code":
        data = {"type": "result", "result": text, "duration_ms": 2000, "num_turns": tool_calls,
                "is_error": False}
        if reported:
            data.update(total_cost_usd=0.5, usage={"input_tokens": 100, "output_tokens": 20,
                                                   "cache_read_input_tokens": 5})
        return json.dumps(data)
    if provider == "gemini-cli":
        models = {"m": {"tokens": {"prompt": 100, "candidates": 20, "cached": 5}}}
        stats = {"models": models if reported else {}, "tools": {"totalCalls": tool_calls}}
        return json.dumps({"response": text, "stats": stats})
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": text}}]
    events += [{"type": "item.completed", "item": {"type": "command_execution"}}] * tool_calls
    if reported:
        events.append({"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 5, "output_tokens": 20}})
    return "\n".join(json.dumps(e) for e in events)


def fake_runner(tmp_path, provider, text, files=(), status=None, **kw):
    calls = []

    def runner(cmd, **opts):
        calls.append(cmd)
        if cmd[0] == "git":
            lines = status if status is not None else [f"?? {f}" for f in files]
            return SimpleNamespace(returncode=0, stdout="".join(l + "\0" for l in lines), stderr="")
        for f in files:
            (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / f).write_text(f"content of {f}")
        return SimpleNamespace(returncode=0, stdout=output(provider, text, cmd, **kw), stderr="")

    runner.calls = calls
    return runner


def failing(returncode, stdout="", stderr=""):
    return lambda cmd, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def agent_of(provider, runner, **kw):
    return make_agent(provider, runner=runner, **kw)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_valid_plan_has_the_architect_shape(tmp_path, provider):
    agent = agent_of(provider, fake_runner(tmp_path, provider, json.dumps(PLAN)))
    p = agent.plan("T", "B", 1, cwd=str(tmp_path))
    assert p["plan"] == ["a", "b"] and p["question"] == "Posso?" and p["_tool_calls"] == 3
    assert p["_cost"]["tokens_in"] == 100 and p["_cost"]["tokens_out"] == 20


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("text", ["not json", json.dumps({"plan": [], "question": "q"}),
                                  json.dumps({"plan": ["a"]})])
def test_invalid_plan_raises_a_clear_error(tmp_path, provider, text):
    agent = agent_of(provider, fake_runner(tmp_path, provider, text))
    with pytest.raises(ValueError, match="plan"):
        agent.plan("T", "B", 1, cwd=str(tmp_path))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cli_failures_are_clear(tmp_path, provider):
    def missing(cmd, **kw):
        raise FileNotFoundError

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(RuntimeError, match="not found"):
        agent_of(provider, missing).plan("T", "B", 1, cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="timed out"):
        agent_of(provider, slow).plan("T", "B", 1, cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        agent_of(provider, failing(0, "oops")).plan("T", "B", 1, cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="boom"):
        agent_of(provider, failing(1, "", "boom")).plan("T", "B", 1, cwd=str(tmp_path))


@pytest.mark.parametrize("provider", ["gemini-cli", "codex-cli"])
def test_non_zero_exit_with_valid_output_fails(tmp_path, provider):
    runner = failing(1, output(provider, "x", []), "boom")
    with pytest.raises(RuntimeError, match="failed"):
        agent_of(provider, runner).plan("T", "B", 1, cwd=str(tmp_path))


def test_error_text_is_redacted(tmp_path):
    secret = "sk-" + "a" * 30
    runner = failing(1, json.dumps({"error": {"message": f"bad key {secret}"}}))
    with pytest.raises(RuntimeError) as e:
        GeminiCliAgent(runner=runner).plan("T", "B", 1, cwd=str(tmp_path))
    assert secret not in str(e.value)


@pytest.mark.parametrize("provider", ["gemini-cli", "codex-cli"])
def test_turn_limit(tmp_path, provider):
    runner = fake_runner(tmp_path, provider, "done", tool_calls=5)
    with pytest.raises(RuntimeError, match="turn limit"):
        agent_of(provider, runner, max_turns=2).execute(CARD, str(tmp_path))


def test_gemini_turn_limit_exit_code(tmp_path):
    runner = failing(53, json.dumps({"error": {"message": "max turns"}}))
    with pytest.raises(RuntimeError, match="turn limit"):
        GeminiCliAgent(runner=runner).plan("T", "B", 1, cwd=str(tmp_path))


def test_limits_are_passed_to_the_cli(tmp_path):
    runner = fake_runner(tmp_path, "gemini-cli", json.dumps(PLAN))
    GeminiCliAgent(runner=runner, timeout=7).plan("T", "B", 1, cwd=str(tmp_path))
    assert runner.calls[0][runner.calls[0].index("--allowed-tools") + 1] == PLAN_TOOLS
    runner = fake_runner(tmp_path, "gemini-cli", "ok", ["docs/a.md"])
    GeminiCliAgent(runner=runner).execute(CARD, str(tmp_path))
    cmd = runner.calls[0]
    assert cmd[cmd.index("--allowed-tools") + 1] == EXEC_TOOLS
    assert "git" not in EXEC_TOOLS and "web" not in EXEC_TOOLS


def test_codex_sandbox_per_role(tmp_path):
    runner = fake_runner(tmp_path, "codex-cli", json.dumps(PLAN))
    CodexCliAgent(runner=runner).plan("T", "B", 1, cwd=str(tmp_path))
    assert runner.calls[0][runner.calls[0].index("--sandbox") + 1] == "read-only"
    runner = fake_runner(tmp_path, "codex-cli", "ok", ["docs/a.md"])
    CodexCliAgent(runner=runner).execute(CARD, str(tmp_path))
    cmd = runner.calls[0]
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=false" in cmd


@pytest.mark.parametrize("provider", PROVIDERS)
def test_execute_lists_changed_files_and_summary(tmp_path, provider):
    runner = fake_runner(tmp_path, provider, "did it", ["docs/a.md"])
    run = agent_of(provider, runner).execute(CARD, str(tmp_path))
    assert run["changed_files"] == ["docs/a.md"] and run["untracked_files"] == ["docs/a.md"]
    assert run["summary"] == "did it" and run["tool_calls"] == 3
    assert run["tokens_in"] == 100 and run["tokens_out"] == 20 and run["tokens_cache"] == 5


@pytest.mark.parametrize("provider", PROVIDERS)
def test_protected_files_are_blocked_and_card_returns_to_waiting_human(tmp_path, provider):
    runner = fake_runner(tmp_path, provider, "done", [".github/workflows/x.yml", "docs/a.md"])
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=planner, executor=agent_of(provider, runner),
                      cwd=str(tmp_path))
    _, config = start(app)
    result = app.invoke(Command(resume={"text": "Aprovado", "by": "anderson"}), config)
    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.files == {}
    assert ".github/workflows/x.yml" in chat.messages[-1]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cost_not_reported_is_flagged_not_invented(tmp_path, provider):
    runner = fake_runner(tmp_path, provider, "ok", ["docs/a.md"], reported=False)
    run = agent_of(provider, runner).execute(CARD, str(tmp_path))
    assert run["tokens_reported"] is False and run["tokens_in"] == 0
    if provider != "claude-code":
        assert run["usd_reported"] is False and run["cost_usd"] == 0.0


def test_codex_never_reports_usd(tmp_path):
    runner = fake_runner(tmp_path, "codex-cli", "ok", ["docs/a.md"])
    run = CodexCliAgent(runner=runner).execute(CARD, str(tmp_path))
    assert run["usd_reported"] is False and run["tokens_reported"] is True


def test_claude_code_reports_usd(tmp_path):
    runner = fake_runner(tmp_path, "claude-code", "ok", ["docs/a.md"])
    run = agent_of("claude-code", runner).execute(CARD, str(tmp_path))
    assert run["usd_reported"] is True and run["cost_usd"] == 0.5


def test_cost_label():
    assert Cost().label() == f"{NOT_REPORTED} · {NOT_REPORTED}"
    assert Cost(0.5, 10, 2).label() == "US$ 0.5000 · 10 in / 2 out"


def test_factory_and_cli_wiring():
    assert type(make_agent("gemini-cli")).__name__ == "GeminiCliAgent"
    assert type(make_agent("codex-cli")).__name__ == "CodexCliAgent"
    assert type(make_agent("claude-code")).__name__ == "ClaudeCodeAgent"
    with pytest.raises(ValueError, match="Unknown coding agent"):
        make_agent("kiro")
    assert callable(make_planner("gemini-cli")) and make_executor("codex-cli").name == "codex-cli"
    with pytest.raises(SystemExit):
        make_executor("kiro")
    with pytest.raises(SystemExit):
        make_planner("antigravity")


def test_catalog_entries_and_no_ide_support_declared():
    assert BY_KEY["gemini-cli"].env == "GEMINI_API_KEY"
    assert BY_KEY["codex-cli"].env == "OPENAI_API_KEY"
    assert BY_KEY["gemini-cli"].status == BY_KEY["codex-cli"].status == "In progress"
    assert not {"kiro", "antigravity"} & set(BY_KEY)
