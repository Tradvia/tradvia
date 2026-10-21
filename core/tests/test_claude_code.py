# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Claude Code planner/executor tests with a fake runner: the real CLI is never called."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from tradvia.cli import make_executor, make_planner
from tradvia.claude_code import ClaudeCodeExecutor, _cost, claude_code_plan, run_claude
from tradvia.events import parse_tokens
from tradvia.graph import build_graph, tokens_line
from tradvia.llm import architect_plan
from test_hello_world import FakeChat, FakeGitHub, planner, start

PLAN = {"summary": "s", "plan": ["a", "b"], "question": "Posso?"}


USAGE = {"input_tokens": 12345, "output_tokens": 3210,
         "cache_read_input_tokens": 70000, "cache_creation_input_tokens": 10000}


def claude_output(result, cost=0.5, ms=2000, is_error=False, usage=None):
    data = {"result": result, "total_cost_usd": cost, "duration_ms": ms,
            "num_turns": 3, "is_error": is_error}
    if usage is not None:
        data["usage"] = usage
    return json.dumps(data)


def fake_runner(tmp_path, result, files=(), status=None, usage=None):
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
        return SimpleNamespace(returncode=0, stdout=claude_output(result, usage=usage), stderr="")

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


def test_cost_reads_usage_with_cache():
    cost = _cost(json.loads(claude_output("x", usage=USAGE)))
    assert (cost["tokens_in"], cost["tokens_out"], cost["tokens_cache"]) == (12345, 3210, 80000)
    assert cost["usd"] == 0.5


@pytest.mark.parametrize("usage, expected", [
    ({"input_tokens": 10, "output_tokens": 4}, (10, 4, 0)),  # no cache fields
    ({"input_tokens": 10, "cache_read_input_tokens": 7}, (10, 0, 7)),
    ({"input_tokens": None, "output_tokens": "x"}, (0, 0, 0)),
    ({}, (0, 0, 0)),
    (None, (0, 0, 0)),  # no usage at all
    ("oops", (0, 0, 0)),
])
def test_cost_tolerates_missing_usage(usage, expected):
    cost = _cost(json.loads(claude_output("x", usage=usage)))
    assert (cost["tokens_in"], cost["tokens_out"], cost["tokens_cache"]) == expected


def test_plan_and_executor_carry_the_tokens(tmp_path):
    runner = fake_runner(tmp_path, json.dumps(PLAN), usage=USAGE)
    p = claude_code_plan("T", "B", 1, runner=runner, cwd=str(tmp_path))
    assert (p["_cost"]["tokens_in"], p["_cost"]["tokens_out"], p["_cost"]["tokens_cache"]) == (
        12345, 3210, 80000)
    runner = fake_runner(tmp_path, "did it", ["docs/a.md"], usage=USAGE)
    run = ClaudeCodeExecutor(runner=runner).execute(
        {"source": {"issue_number": 1}, "title": "T", "plan": []}, str(tmp_path))
    assert (run["tokens_in"], run["tokens_out"], run["tokens_cache"]) == (12345, 3210, 80000)


def test_tokens_line_format():
    assert tokens_line({"tokens_in": 12345, "tokens_out": 3210, "tokens_cache": 80000}) == (
        "Tokens: 12 345 in · 3 210 out · 80 000 cache")
    assert tokens_line({}) == "Tokens: 0 in · 0 out · 0 cache"
    assert tokens_line(None) == "Tokens: 0 in · 0 out · 0 cache"


def test_pr_body_and_plan_comment_carry_the_tokens_line(tmp_path):
    usage_planner = lambda title, body, number: {
        **planner(title, body, number),
        "_cost": {"usd": 0.25, "tokens_in": 1000, "tokens_out": 200, "tokens_cache": 5000}}
    runner = fake_runner(tmp_path, "did it", ["docs/a.md"], usage=USAGE)
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=usage_planner, executor=ClaudeCodeExecutor(runner=runner),
                      cwd=str(tmp_path))
    _, config = start(app)
    plan_comment = chat.messages[0]
    assert "Estimated cost (API-equivalent): $0.2500" in plan_comment
    assert "Tokens: 1 000 in · 200 out · 5 000 cache" in plan_comment
    app.invoke(Command(resume={"text": "Aprovado", "by": "anderson"}), config)
    body = gh.prs[0][2]
    assert "Tokens: 12 345 in · 3 210 out · 80 000 cache" in body
    assert body.count("Tokens:") == 1  # the plan tokens are not repeated in the PR
    assert parse_tokens(plan_comment) == (1000, 200, 5000)
    assert parse_tokens(body) == (12345, 3210, 80000)


def test_replanned_comment_reports_only_its_own_plan(tmp_path):
    costs = iter([{"usd": 0.1, "tokens_in": 10, "tokens_out": 1, "tokens_cache": 0},
                  {"usd": 0.3, "tokens_in": 30, "tokens_out": 3, "tokens_cache": 0}])
    replanner = lambda title, body, number: {**planner(title, body, number), "_cost": next(costs)}
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=replanner)
    _, config = start(app)
    app.invoke(Command(resume={"text": "use inglês", "by": "anderson"}), config)
    assert "$0.1000" in chat.messages[0] and "$0.3000" in chat.messages[1]
    assert "$0.4000" not in chat.messages[1]  # not the accumulated card cost


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


def test_claude_code_plan_without_executor_returns_to_waiting_human(tmp_path):
    def no_file_planner(title, body, number):
        return {**PLAN, "_cost": {"usd": 0.01}}

    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=no_file_planner, executor=None, cwd=str(tmp_path))
    _, config = start(app)
    result = app.invoke(Command(resume={"text": "Aprovado", "by": "anderson"}), config)

    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == [] and gh.files == {}
    assert "TRADVIA_EXECUTOR" in chat.messages[-1]


def test_executor_skips_binary_files_and_cites_them(tmp_path):
    inner = fake_runner(tmp_path, "done", ["docs/a.md", "img.png"])

    def runner(cmd, **kw):
        out = inner(cmd, **kw)
        if cmd[0] != "git":
            (tmp_path / "img.png").write_bytes(b"\x89PNG\xff\xfe")
        return out

    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ClaudeCodeExecutor(runner=runner), tmp_path)

    assert result["status"] == "IN_REVIEW_PR"
    assert [p for _, p in gh.files] == ["docs/a.md"]
    assert "`img.png`" in gh.prs[0][2]


class MessageGitHub(FakeGitHub):
    def __init__(self, title):
        super().__init__()
        self.title, self.messages = title, []

    def get_issue(self, number):
        return {**super().get_issue(number), "title": self.title}

    def put_file(self, branch, path, content, message):
        self.messages.append(message)
        super().put_file(branch, path, content, message)


@pytest.mark.parametrize("issue_title, expected", [
    ("feat(dashboard): add panel", "feat(dashboard): add panel"),
    ("fix: crash on start", "fix: crash on start"),
    ("fix(agent)!: breaking", "fix(agent)!: breaking"),
    ("Add hello world", "feat: Add hello world"),
    ("feature request", "feat: feature request"),
])
def test_pr_title_and_commit_prefix_not_duplicated(tmp_path, issue_title, expected):
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done", ["docs/a.md"]))
    gh = MessageGitHub(issue_title)
    approved_card_app(gh, FakeChat(), ex, tmp_path)

    assert gh.prs[0][1] == expected
    assert gh.messages == [f"{expected} (#1)"]


def test_prompts_leave_git_and_pr_to_tradvia():
    from tradvia.claude_code import EXEC_PROMPT, PLAN_PROMPT

    for prompt in (PLAN_PROMPT, EXEC_PROMPT):
        assert "Tradvia creates the branch, makes the commit and opens the pull request" in prompt
    assert "Do not run git" in EXEC_PROMPT


def test_exec_prompt_forbids_temporary_files():
    from tradvia.claude_code import EXEC_PROMPT

    assert "Do not create temporary or verification files in the repository" in EXEC_PROMPT
    assert "outside the repository" in EXEC_PROMPT


@pytest.mark.parametrize("path, expected", [
    (".tmpchk/check.js", True), (".cache/x", True), ("a/.claude/y.md", True),
    ("x.tmp", True), ("d/x.bak", True), ("x~", True),
    ("core/src/a.py", False), (".gitignore", False), ("docs/a.md", False),
])
def test_is_scratch(path, expected):
    from tradvia.graph import is_scratch

    assert is_scratch(path) is expected


def test_untracked_files_parses_only_untracked(tmp_path):
    from tradvia.claude_code import untracked_files

    runner = fake_runner(tmp_path, "x", status=["?? .tmpchk/check.js", " M docs/a.md",
                                                "R  docs/b.md", "docs/old.md", "?? n.py"])
    assert untracked_files(str(tmp_path), runner) == [".tmpchk/check.js", "n.py"]


def test_scratch_files_are_left_out_of_pr_and_cited(tmp_path):
    files = [".tmpchk/check.js", "core/src/a.py"]
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done", files))
    gh = FakeGitHub()
    result = approved_card_app(gh, FakeChat(), ex, tmp_path)

    assert result["status"] == "IN_REVIEW_PR"
    assert [p for _, p in gh.files] == ["core/src/a.py"]
    assert "Ignored scratch files" in gh.prs[0][2] and "`.tmpchk/check.js`" in gh.prs[0][2]


def test_tracked_file_in_dot_directory_is_included(tmp_path):
    ex = ClaudeCodeExecutor(runner=fake_runner(
        tmp_path, "done", [".devcontainer/devcontainer.json"],
        status=[" M .devcontainer/devcontainer.json"]))
    gh = FakeGitHub()
    result = approved_card_app(gh, FakeChat(), ex, tmp_path)

    assert result["status"] == "IN_REVIEW_PR"
    assert [p for _, p in gh.files] == [".devcontainer/devcontainer.json"]
    assert "Ignored scratch files" not in gh.prs[0][2]


def test_scratch_named_files_are_ignored(tmp_path):
    files = ["x.tmp", "x.bak", "x~", "docs/a.md"]
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done", files))
    gh = FakeGitHub()
    approved_card_app(gh, FakeChat(), ex, tmp_path)

    assert [p for _, p in gh.files] == ["docs/a.md"]
    assert all(f"`{s}`" in gh.prs[0][2] for s in ("x.tmp", "x.bak", "x~"))


def test_only_scratch_files_returns_to_waiting_human(tmp_path):
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "done", [".tmpchk/check.js"]))
    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ex, tmp_path)

    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == [] and gh.files == {}
    assert ".tmpchk/check.js" in chat.messages[-1]


def test_pr_body_labels_cost_as_estimate(tmp_path):
    ex = ClaudeCodeExecutor(runner=fake_runner(tmp_path, "did it", ["docs/a.md"]))
    gh = FakeGitHub()
    approved_card_app(gh, FakeChat(), ex, tmp_path)

    body = gh.prs[0][2].lower()
    assert body.count("estimated cost") == 2 and "(api-equivalent)" in body
    assert "cost: $" not in body


def test_defaults_stay_offline_note(monkeypatch):
    monkeypatch.delenv("TRADVIA_PLANNER", raising=False)
    monkeypatch.delenv("TRADVIA_EXECUTOR", raising=False)
    assert make_planner() is architect_plan
    assert make_executor() is None
    assert make_planner("claude-code") is claude_code_plan
    assert isinstance(make_executor("claude-code"), ClaudeCodeExecutor)


def test_run_claude_reports_turn_limit(tmp_path):
    out = json.dumps({"subtype": "error_max_turns", "is_error": True, "num_turns": 31})
    runner = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=out, stderr="")
    with pytest.raises(RuntimeError, match=r"turn limit \(30 turns\)"):
        run_claude("p", str(tmp_path), "Read", 30, runner=runner)


@pytest.mark.parametrize("value, expected", [(None, 80), ("120", 120), ("0", 80), ("x", 80)])
def test_executor_max_turns_from_env(tmp_path, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("TRADVIA_EXEC_MAX_TURNS", raising=False)
    else:
        monkeypatch.setenv("TRADVIA_EXEC_MAX_TURNS", value)
    runner = fake_runner(tmp_path, "done", ["docs/a.md"])
    ClaudeCodeExecutor(runner=runner).execute(
        {"title": "t", "source": {"issue_number": 1}, "plan": []}, str(tmp_path))
    cmd = runner.calls[0]
    assert cmd[cmd.index("--max-turns") + 1] == str(expected)


def test_executor_failure_is_announced_and_opens_no_pr(tmp_path):
    out = json.dumps({"subtype": "error_max_turns", "is_error": True})
    runner = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=out, stderr="")
    gh, chat = FakeGitHub(), FakeChat()
    result = approved_card_app(gh, chat, ClaudeCodeExecutor(runner=runner, max_turns=5), tmp_path)

    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == [] and gh.files == {}
    assert "turn limit (5 turns)" in chat.messages[-1] and "agent:ready" in chat.messages[-1]


@pytest.mark.parametrize("value, expected", [(None, 30), ("45", 45), ("-3", 30), ("x", 30)])
def test_planner_max_turns_from_env(tmp_path, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("TRADVIA_PLAN_MAX_TURNS", raising=False)
    else:
        monkeypatch.setenv("TRADVIA_PLAN_MAX_TURNS", value)
    runner = fake_runner(tmp_path, json.dumps(PLAN))
    claude_code_plan("t", "b", 1, runner=runner, cwd=str(tmp_path))
    cmd = runner.calls[0]
    assert cmd[cmd.index("--max-turns") + 1] == str(expected)


@pytest.mark.parametrize("error", [RuntimeError("Claude Code stopped at the turn limit (30 turns)"),
                                   ValueError("Claude Code plan is not valid JSON: x")])
def test_planner_failure_is_announced_and_posts_no_plan(error):
    def failing(title, body, number):
        raise error

    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=failing)
    result, _ = start(app)

    assert result["status"] == "WAITING_HUMAN"
    assert not result.get("__interrupt__")
    assert len(chat.messages) == 1 and str(error) in chat.messages[0]
    assert "agent:ready" in chat.messages[0]
    assert gh.prs == [] and gh.branches == []
