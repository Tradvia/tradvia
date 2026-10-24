# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Execution budgets (ADR 0006): defaults, 80% notice, 100% stop, usage, per-project scope."""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from tradvia.budget import Budget, check, format_usage, parse_budget, usage_ratio
from tradvia.graph import build_graph
from tradvia.state import add_usage


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")


@pytest.mark.parametrize("field, default", [("max_minutes", 30.0), ("max_steps", 40),
                                            ("max_usd", 5.0)])
@pytest.mark.parametrize("bad", [-1, 0, "10", True, False, None, float("nan"), float("inf"), [5]])
def test_invalid_value_falls_back_to_the_field_default(field, default, bad):
    assert getattr(parse_budget({field: bad}), field) == default


def test_defaults_valid_values_and_unknown_keys():
    assert parse_budget(None) == parse_budget("x") == parse_budget([]) == Budget()
    b = parse_budget({"max_minutes": 5, "max_steps": 10, "max_usd": 0.5, "bogus": 1})
    assert (b.max_minutes, b.max_steps, b.max_usd) == (5, 10, 0.5)
    assert parse_budget({"max_steps": 2.5}).max_steps == 40
    # a bad field never affects the others
    assert parse_budget({"max_steps": 0, "max_usd": 1}) == Budget(max_usd=1.0)


def test_check_uses_the_highest_ratio():
    b = Budget(max_minutes=10, max_steps=10, max_usd=10)
    assert check({"steps": 7}, b) == "ok"
    assert check({"steps": 1, "usd": 8}, b) == "warn"
    assert check({"minutes": 10}, b) == "stop"
    assert usage_ratio(None, b) == 0
    assert "8/10 steps" in format_usage({"steps": 8, "minutes": 1, "usd": 0.5}, b)


def test_add_usage_sums():
    assert add_usage({"steps": 1, "usd": 0.5}, {"steps": 2, "minutes": 1.5}) == {
        "steps": 3, "minutes": 1.5, "usd": 0.5}


class FakeGitHub:
    def __init__(self):
        self.prs, self.comments = [], []

    def get_issue(self, number):
        return {"number": number, "title": "Add hello world", "body": "Say hi.",
                "url": f"https://github.com/Tradvia/tradvia/issues/{number}"}

    def create_branch(self, name):
        pass

    def put_file(self, branch, path, content, message):
        pass

    def open_pull_request(self, branch, title, body):
        self.prs.append((branch, title, body))
        return "https://github.com/Tradvia/tradvia/pull/2"

    def comment(self, number, body):
        self.comments.append((number, body))


class FakeChat:
    def __init__(self):
        self.messages = []

    def post(self, text):
        self.messages.append(text)
        return str(len(self.messages))


class Planner:
    def __init__(self, tool_calls=0, usd=0.0):
        self.bodies, self.tool_calls, self.usd = [], tool_calls, usd

    def __call__(self, title, body, number):
        self.bodies.append(body)
        return {"summary": "s", "plan": ["a"], "question": "ok?", "file_path": "hello.txt",
                "file_content": "hi", "_tool_calls": self.tool_calls,
                "_cost": {"usd": self.usd, "duration_s": 0}}


class Executor:
    def __init__(self, tmp_path, tool_calls=0, usd=0.0):
        self.calls, self.tmp, self.tool_calls, self.usd = [], tmp_path, tool_calls, usd

    def execute(self, card, cwd=".", budget_notice=""):
        self.calls.append(budget_notice)
        (self.tmp / "out.txt").write_text("x")
        return {"changed_files": ["out.txt"], "untracked_files": [], "cost_usd": self.usd,
                "duration_s": 60.0, "tool_calls": self.tool_calls, "summary": "done"}


def start(app, project="Tradvia/a", issue=1):
    config = {"configurable": {"thread_id": f"{project}#CARD-{issue}"}}
    result = app.invoke({"id": f"CARD-{issue}", "project_id": project, "status": "BACKLOG",
                         "source": {"provider": "github", "issue_number": issue}}, config)
    return result, config


def reply(app, config, text):
    return app.invoke(Command(resume={"text": text, "by": "h"}), config)


def test_warning_notice_reaches_the_next_prompt_exactly_once():
    planner = Planner(tool_calls=7)  # 1 + 7 = 8 of 10 steps
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, budget=Budget(max_steps=10))
    _, config = start(app)
    assert "SYSTEM NOTICE" not in planner.bodies[0]
    result = reply(app, config, "please add a docstring")
    assert planner.bodies[1].count("SYSTEM NOTICE") == 1
    assert result["usage"]["steps"] == 16  # the second plan was still allowed (8 < 10)


def test_executor_gets_the_notice_at_80_percent(tmp_path):
    ex = Executor(tmp_path)
    app = build_graph(FakeGitHub(), FakeChat(), planner=Planner(tool_calls=7), executor=ex,
                      cwd=str(tmp_path), budget=Budget(max_steps=10))
    _, config = start(app)
    reply(app, config, "approved")
    assert len(ex.calls) == 1 and ex.calls[0].count("SYSTEM NOTICE") == 1


def test_stop_at_100_percent_does_not_call_the_agent(tmp_path):
    gh, chat, ex = FakeGitHub(), FakeChat(), Executor(tmp_path)
    app = build_graph(gh, chat, planner=Planner(tool_calls=9), executor=ex, cwd=str(tmp_path),
                      budget=Budget(max_steps=10))
    _, config = start(app)
    result = reply(app, config, "approved")
    assert ex.calls == [] and gh.prs == []
    assert result["status"] == "WAITING_HUMAN"
    assert result["history"][-1]["reason"] == "Budget exhausted"
    assert len(gh.comments) == 1
    assert "10/10 steps" in gh.comments[0][1] and "exhausted" in gh.comments[0][1]


def test_stop_before_replanning_does_not_call_the_planner():
    gh, planner = FakeGitHub(), Planner(tool_calls=9)
    app = build_graph(gh, FakeChat(), planner=planner, budget=Budget(max_steps=10))
    _, config = start(app)
    result = reply(app, config, "change it")
    assert len(planner.bodies) == 1
    assert result["status"] == "WAITING_HUMAN" and len(gh.comments) == 1


def test_usage_in_state_and_pr_body(tmp_path):
    gh = FakeGitHub()
    app = build_graph(gh, FakeChat(), planner=Planner(usd=0.1),
                      executor=Executor(tmp_path, tool_calls=3, usd=0.25), cwd=str(tmp_path))
    _, config = start(app)
    result = reply(app, config, "approved")
    assert result["usage"] == {"steps": 1 + 1 + 3, "minutes": 1.0, "usd": 0.35}
    assert "Budget used: 5/40 steps · 1.0/30 min · $0.3500/$5" in gh.prs[0][2]


def test_usage_is_isolated_per_project():
    app = build_graph(FakeGitHub(), FakeChat(), planner=Planner(tool_calls=4),
                      checkpointer=MemorySaver())
    a, cfg_a = start(app, "Tradvia/a")
    b, _ = start(app, "Tradvia/b")
    assert a["project_id"] == "Tradvia/a" and b["project_id"] == "Tradvia/b"
    assert a["usage"]["steps"] == b["usage"]["steps"] == 5  # not 10: nothing leaks across
    state = app.get_state(cfg_a).values
    assert state["project_id"] == "Tradvia/a" and state["usage"]["steps"] == 5
