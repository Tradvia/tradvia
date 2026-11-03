# SPDX-License-Identifier: FSL-1.1-ALv2
"""Versioned prompts (#125): every plan and PR says which version made it, and a text cannot
change without its version."""

import hashlib
import json

import pytest
from langgraph.types import Command

from tradvia import prompts
from tradvia.claude_code import EXEC_PROMPT, PLAN_PROMPT, ClaudeCodeExecutor, claude_code_plan
from tradvia.graph import build_graph
from tradvia.llm import ARCHITECT_PROMPT
from test_claude_code import claude_output, fake_runner
from test_hello_world import FakeChat, FakeGitHub, planner, start


def test_no_prompt_changed_without_a_new_version():
    stale = prompts.drift()
    assert stale == [], (
        f"Prompt text changed without a new version: {stale}. Bump its version in "
        "core/src/tradvia/prompts/versions.json, update the sha256 and run the evals.")


def test_every_prompt_has_a_file_a_version_and_a_hash():
    reg = json.loads((prompts.HERE / "versions.json").read_text(encoding="utf-8"))
    assert set(reg) == set(prompts.NAMES)
    for name, entry in reg.items():
        assert isinstance(entry["version"], int) and entry["version"] >= 1
        assert len(entry["sha256"]) == 64 and entry["used_by"]


def test_drift_is_detected(tmp_path, monkeypatch):
    for name in prompts.NAMES:
        (tmp_path / f"{name}.txt").write_text(prompts.text(name), encoding="utf-8")
    reg = json.loads((prompts.HERE / "versions.json").read_text(encoding="utf-8"))
    (tmp_path / "versions.json").write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(prompts, "HERE", tmp_path)
    assert prompts.drift() == []
    (tmp_path / "plan.txt").write_text(prompts.text("plan") + " ", encoding="utf-8")
    assert prompts.drift() == ["plan"]


def test_the_modules_use_the_versioned_texts():
    assert PLAN_PROMPT == prompts.text("plan") and EXEC_PROMPT == prompts.text("exec")
    assert ARCHITECT_PROMPT == prompts.text("architect")
    assert prompts.ref("plan") == f"plan@{prompts.load('plan').version}"
    with pytest.raises(KeyError):
        prompts.load("nope")


def test_the_plan_comment_and_the_pr_name_the_prompt_versions(tmp_path):
    plan_json = json.dumps({"summary": "s", "plan": ["a"], "question": "ok?"})

    def versioned_planner(title, body, number):
        return claude_code_plan(title, body, number, runner=fake_runner(tmp_path, plan_json),
                                cwd=str(tmp_path))

    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "did it", ["docs/a.md"]))
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=versioned_planner, executor=ex, cwd=str(tmp_path))
    _, config = start(app)
    assert f"Prompt: {prompts.ref('plan')}" in chat.messages[0]
    app.invoke(Command(resume={"text": "Aprovado", "by": "ana"}), config)
    assert f"Prompt: {prompts.ref('plan')} · {prompts.ref('exec')}" in gh.prs[0][2]


def test_a_planner_without_a_version_adds_no_line():
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=planner)
    start(app)
    assert "Prompt:" not in chat.messages[0]


def test_eval_reports_record_the_prompt_versions():
    from tradvia.evals.runner import run_suite

    assert run_suite([], lambda *a, **k: {}, "x")["prompts"] == prompts.refs()
