# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Execution budgets (ADR 0006): defaults, 80% notice, 100% stop, usage, per-project scope."""

from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from tradvia.budget import (TOKEN_PRICES, Budget, MonthlyBudget, check, estimate_usd,
                            format_monthly, format_usage, month_spend, monthly_check,
                            parse_budget, parse_monthly_budget, parse_monthly_usd, remaining,
                            run_limits, usage_ratio)
from tradvia.graph import build_graph
from tradvia.budget import settings_budget, validate_settings
from tradvia.policy import Policy, parse_policy
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


# --- monthly ceiling (#131) -------------------------------------------------------------------

NOW = datetime(2026, 3, 15, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [-1, 0, "10", True, False, None, float("nan"), float("inf"), [5]])
def test_invalid_monthly_usd_means_no_ceiling(bad):
    assert parse_monthly_usd({"monthly_usd": bad}) is None
    assert parse_monthly_budget({"monthly_usd": bad}) == MonthlyBudget()


def test_valid_monthly_usd_and_the_per_card_limits_stay_untouched():
    section = {"monthly_usd": 20, "max_usd": 2}
    assert parse_monthly_usd(section) == 20.0
    assert parse_budget(section) == Budget(max_usd=2.0)
    assert parse_monthly_usd(None) is None and parse_monthly_usd("x") is None
    # The file has no say (ADR 0018): the ceiling comes from the project's setting.
    assert parse_policy("budget:\n  monthly_usd: 7.5\n").monthly_usd is None
    assert settings_budget({"monthly_usd": 7.5})[1] == 7.5
    assert settings_budget({"monthly_usd": "abc"})[1] is None


def test_month_spend_sums_plans_and_prs_of_the_current_utc_month_only():
    plans = [{"created_at": "2026-03-01T00:00:00Z", "cost_usd": 1.0},
             {"created_at": "2026-03-31T23:59:59Z", "cost_usd": 0.5},
             {"created_at": "2026-02-28T23:59:59Z", "cost_usd": 9.0},
             {"created_at": "2026-04-01T00:00:00Z", "cost_usd": 9.0}]
    pulls = [{"created_at": "2026-03-10T00:00:00Z", "cost_usd": 2.0, "cost_so_far_usd": 3.5},
             {"created_at": "2026-03-11T00:00:00Z", "cost_usd": None, "cost_so_far_usd": 7.0},
             {"created_at": "2026-02-10T00:00:00Z", "cost_usd": 9.0}]
    assert month_spend(plans, pulls, NOW) == pytest.approx(3.5)  # cost_so_far never added
    assert month_spend([], [], NOW) == 0


def test_month_spend_counts_a_row_with_an_unreadable_date():
    assert month_spend([{"created_at": None, "cost_usd": 1.0}], [], NOW) == 1.0


def test_monthly_check_thresholds():
    assert monthly_check(7.99, 10) == "ok"
    assert monthly_check(8.0, 10) == "warn"
    assert monthly_check(9.99, 10) == "warn"
    assert monthly_check(10.0, 10) == "stop"
    assert monthly_check(1000, None) == "ok"
    assert "$8.0000/$10" in format_monthly(8, 10)


def monthly_app(spend, policy=None):
    gh, chat = FakeGitHub(), FakeChat()
    policy = Policy(monthly_usd=10.0) if policy is None else policy
    return gh, chat, build_graph(gh, chat, planner=Planner(), policy=policy, month_spend=spend)


def test_new_card_does_not_start_at_100_percent_of_the_month():
    planner_calls = []
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=lambda *a: planner_calls.append(a),
                      policy=Policy(monthly_usd=10.0), month_spend=lambda: 10.0)
    result, _ = start(app)
    assert planner_calls == [] and chat.messages == []
    assert result["status"] == "BACKLOG"  # card-state.v1 has no BACKLOG -> WAITING_HUMAN
    assert result["history"][-1]["reason"] == "Monthly budget exhausted"
    assert len(gh.comments) == 1 and "$10.0000/$10" in gh.comments[0][1]


def test_new_card_starts_with_a_warning_at_80_percent_once():
    gh, chat, app = monthly_app(lambda: 8.0)
    result, _ = start(app)
    assert result["pending_question"]  # it planned
    assert len(gh.comments) == 1 and "80%" in gh.comments[0][1]
    start(app, issue=2)  # same month, same graph: no second warning
    assert len(gh.comments) == 1


def test_no_ceiling_or_no_spend_reader_changes_nothing():
    for policy, spend in ((Policy(), lambda: 999.0), (Policy(monthly_usd=10.0), None)):
        gh, _, app = monthly_app(spend, policy)
        result, _ = start(app)
        assert result["pending_question"] and gh.comments == []


def test_unreadable_spend_stops_the_start_instead_of_loosening():
    def boom():
        raise RuntimeError("db down")

    gh, _, app = monthly_app(boom)
    result, _ = start(app)
    assert result["status"] == "BACKLOG" and not result.get("pending_question")
    assert len(gh.comments) == 1 and "could not read" in gh.comments[0][1]


def test_card_in_progress_finishes_its_own_budget_when_the_month_runs_out(tmp_path):
    spent = [0.0]
    gh, chat, ex = FakeGitHub(), FakeChat(), Executor(tmp_path)
    app = build_graph(gh, chat, planner=Planner(), executor=ex, cwd=str(tmp_path),
                      policy=Policy(monthly_usd=10.0), month_spend=lambda: spent[0])
    _, config = start(app)
    spent[0] = 12.0  # the month is over the ceiling while the card waits for approval
    replanned = reply(app, config, "change it")
    assert replanned["pending_question"] and gh.comments == []  # plan still ran
    reply(app, config, "approved")
    assert len(ex.calls) == 1 and len(gh.prs) == 1  # open_pr still ran
    assert not any("monthly" in c[1] for c in gh.comments)


def test_dashboard_renders_the_monthly_budget_with_both_languages():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(
        encoding="utf-8")
    assert "monthlyBudgetKpi()" in html and "mb.cap_usd" in html and "mb.spent_usd" in html
    assert html.count("monthlyBudget:") == 2  # pt and en


def test_per_card_limit_is_unchanged_with_a_monthly_ceiling():
    gh = FakeGitHub()
    app = build_graph(gh, FakeChat(), planner=Planner(tool_calls=9), budget=Budget(max_steps=10),
                      policy=Policy(monthly_usd=100.0), month_spend=lambda: 0.0)
    _, config = start(app)
    result = reply(app, config, "change it")
    assert result["history"][-1]["reason"] == "Budget exhausted"

# --- the run is derived from the card's remaining balance (ADR 0006, unchanged limit) --------


def test_remaining_is_never_negative():
    b = Budget(max_minutes=10, max_steps=10, max_usd=1)
    assert remaining({"steps": 4, "minutes": 2.5, "usd": 0.25}, b) == {
        "steps": 6, "minutes": 7.5, "usd": 0.75}
    assert remaining({"steps": 47, "minutes": 99, "usd": 9}, b) == {
        "steps": 0, "minutes": 0.0, "usd": 0.0}
    assert remaining(None, b) == {"steps": 10, "minutes": 10, "usd": 1}


def test_run_limits_leave_one_step_for_the_run_itself():
    b = Budget(max_minutes=10, max_steps=40, max_usd=5)
    assert run_limits(None, b, 80, 900) == (39, 600)  # capped by the card, not by the defaults
    assert run_limits({"steps": 10, "minutes": 9.99}, b, 80, 900) == (29, 1)
    assert run_limits({"steps": 1}, Budget(max_steps=100, max_minutes=60), 80, 900) == (80, 900)
    assert run_limits({"steps": 39}, b, 80, 900)[0] == 0  # no balance for a single turn
    assert run_limits({"steps": 47, "minutes": 99}, b, 80, 900) == (0, 1)


def test_estimate_usd_uses_the_price_table():
    assert estimate_usd(1_000_000, 0, 0) == TOKEN_PRICES["default"][0]
    assert estimate_usd(0, 1_000_000, 1_000_000) == TOKEN_PRICES["default"][1] + TOKEN_PRICES[
        "default"][2]
    assert estimate_usd(None, None, None) == 0.0
    assert estimate_usd(100, 20, 5, model="unknown") == estimate_usd(100, 20, 5)


class LimitedExecutor(Executor):
    """Records the limits the graph hands to each run."""

    def execute(self, card, cwd=".", budget_notice="", max_turns=None, timeout=None):
        self.limits = (max_turns, timeout)
        return super().execute(card, cwd, budget_notice)


def test_run_never_goes_past_the_remaining_balance(tmp_path):
    ex = LimitedExecutor(tmp_path)
    app = build_graph(FakeGitHub(), FakeChat(), planner=Planner(tool_calls=0), executor=ex,
                      cwd=str(tmp_path), budget=Budget(max_steps=3, max_minutes=2))
    _, config = start(app)  # the plan used 1 of 3 steps
    reply(app, config, "approved")
    turns, seconds = ex.limits
    assert 1 + turns <= 3 - 1 and turns == 1  # steps used by the run: 1 + tool_calls
    assert seconds == 120


def test_no_balance_for_one_turn_stops_without_calling_the_agent(tmp_path):
    gh, ex = FakeGitHub(), LimitedExecutor(tmp_path)
    app = build_graph(gh, FakeChat(), planner=Planner(tool_calls=0), executor=ex,
                      cwd=str(tmp_path), budget=Budget(max_steps=2))
    _, config = start(app)  # 1 of 2 steps used: 50%, but no step left besides the run itself
    result = reply(app, config, "approved")
    assert not hasattr(ex, "limits") and gh.prs == []
    assert result["history"][-1]["reason"] == "Budget exhausted"


def test_executor_without_limit_parameters_still_runs(tmp_path):
    ex = Executor(tmp_path)  # the old signature: no max_turns/timeout
    app = build_graph(FakeGitHub(), FakeChat(), planner=Planner(), executor=ex, cwd=str(tmp_path))
    _, config = start(app)
    reply(app, config, "approved")
    assert len(ex.calls) == 1


# --- the project's budget setting (ADR 0018) ------------------------------------------------


def test_validate_settings_accepts_a_full_setting_and_no_ceiling():
    assert validate_settings({"max_minutes": 15, "max_steps": 20, "max_usd": 1.5,
                              "monthly_usd": None}) == {
        "max_minutes": 15.0, "max_steps": 20, "max_usd": 1.5, "monthly_usd": None}
    assert validate_settings({}) == {"max_minutes": 30.0, "max_steps": 40, "max_usd": 5.0,
                                     "monthly_usd": None}


@pytest.mark.parametrize("bad", [
    {"max_usd": 0}, {"max_usd": -1}, {"max_steps": 2.5}, {"monthly_usd": "100"},
    {"monthly_usd": 0}, {"max_minutes": 99999}, {"max_usd": True}, {"other": 1}, [], "x",
])
def test_validate_settings_refuses_what_it_cannot_store(bad):
    with pytest.raises(ValueError):
        validate_settings(bad)
