# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Risk levels and the permission gateway, offline with fake GitHub and chat."""

import pytest
from langgraph.types import Command

from tradvia.graph import build_graph
from tradvia.policy import Policy, PolicyError, RiskPolicy, parse_policy
from tradvia.risk import DEFAULT_ACTION_RISK, classify
from tradvia.llm import _offline_plan


class FakeGitHub:
    repo = "Tradvia/tradvia"

    def __init__(self, roles=None):
        self.roles = roles or {}
        self.branches, self.files, self.prs, self.comments = [], {}, [], []

    def get_issue(self, number):
        return {"number": number, "title": "Add hello world", "body": "Say hi.",
                "url": f"https://github.com/Tradvia/tradvia/issues/{number}"}

    def get_collaborator_role(self, login):
        return self.roles.get(login)

    def create_branch(self, name):
        self.branches.append(name)

    def put_file(self, branch, path, content, message):
        self.files[(branch, path)] = content

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

    def wait_for_reply(self, after_message_id, timeout_s):
        raise AssertionError("tests resume the graph directly")


def planner(title, body, number):
    return _offline_plan(title, body, number)


def reply(text, by):
    return Command(resume={"text": text, "by": by})


def reach_gate(policy, roles=None, action=None):
    """Run to the plan approval, approve it, and return (app, config, gh, chat, result)."""
    gh, chat = FakeGitHub({"plan": "write", **(roles or {})}), FakeChat()
    app = build_graph(gh, chat, planner=planner, policy=policy)
    config = {"configurable": {"thread_id": "CARD-1"}}
    state = {"id": "CARD-1", "status": "BACKLOG",
             "source": {"provider": "github", "issue_number": 1}}
    if action:
        state["pending_action"] = action
    app.invoke(state, config)
    return app, config, gh, chat, app.invoke(reply("Aprovado", "plan"), config)


@pytest.mark.parametrize("action,level", [
    ("get_issue", "read"), ("read_repo", "read"),
    ("create_branch", "reversible_write"), ("put_file", "reversible_write"),
    ("open_pull_request", "reversible_write"), ("comment", "reversible_write"),
    ("merge_pr", "destructive_external"), ("delete", "destructive_external"),
    ("publish", "destructive_external"), ("deploy", "destructive_external"),
    ("send_external_message", "destructive_external"),
])
def test_default_map(action, level):
    assert DEFAULT_ACTION_RISK[action] == level
    assert classify(action) == level


def test_undeclared_action_fails_closed():
    assert classify("rm_rf_everything") == "destructive_external"
    assert classify("x", {"x": "bogus"}) == "destructive_external"
    assert classify("x", {"x": "read"}) == "read"


@pytest.mark.parametrize("action", [None, "get_issue", "create_branch"])
def test_read_and_reversible_write_run_without_approval(action):
    _, _, gh, _, result = reach_gate(Policy(), action=action)
    assert "__interrupt__" not in result
    assert result["status"] == "IN_REVIEW_PR"
    assert gh.branches == ["tradvia/issue-1"]


def test_destructive_suspends_and_describes():
    _, _, gh, chat, result = reach_gate(Policy(), action="merge_pr")
    assert result["__interrupt__"]
    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == []
    assert "merge_pr" in chat.messages[-1] and "maintain" in chat.messages[-1]
    assert result["risk"]["project_id"] == "Tradvia/tradvia"


@pytest.mark.parametrize("approver", ["maint", "boss"])
def test_destructive_resumes_after_sufficient_role(approver):
    app, config, gh, _, _ = reach_gate(Policy(), {"maint": "maintain", "boss": "admin"},
                                       action="deploy")
    result = app.invoke(reply("Aprovado", approver), config)
    assert result["status"] == "IN_REVIEW_PR"
    assert gh.branches == ["tradvia/issue-1"]


def test_insufficient_role_is_refused_and_card_stays_suspended():
    app, config, gh, chat, _ = reach_gate(Policy(), {"dev": "write"}, action="deploy")
    result = app.invoke(reply("Aprovado", "dev"), config)
    assert result["__interrupt__"] and result["status"] == "WAITING_HUMAN"
    assert "não pode aprovar" in chat.messages[-1]
    assert gh.branches == [] and gh.prs == []
    # Strangers and bots never count either.
    app.invoke(reply("Aprovado", "stranger"), config)
    assert gh.branches == []


def test_rejection_ends_the_action():
    app, config, gh, _, _ = reach_gate(Policy(), {"maint": "maintain"}, action="deploy")
    result = app.invoke(reply("Rejeitado", "maint"), config)
    assert "__interrupt__" not in result
    assert result["status"] == "BACKLOG"
    assert result["risk"]["state"] == "rejected"
    assert gh.branches == [] and gh.prs == []


def test_rejection_by_insufficient_role_is_ignored():
    app, config, _, _, _ = reach_gate(Policy(), {"dev": "write"}, action="deploy")
    result = app.invoke(reply("Rejeitado", "dev"), config)
    assert result["__interrupt__"] and result["status"] == "WAITING_HUMAN"


def test_undeclared_action_in_graph_needs_approval():
    _, _, _, _, result = reach_gate(Policy(), action="something_new")
    assert result["__interrupt__"]
    assert result["risk"]["level"] == "destructive_external"


def test_custom_policy_is_honored():
    policy = parse_policy("risk:\n  reversible_write: write\n  destructive_external: admin\n")
    app, config, gh, _, result = reach_gate(policy, {"maint": "maintain", "boss": "admin"},
                                            action="create_branch")
    # `plan` has write, which meets reversible_write: write; but approval is still asked.
    assert result["__interrupt__"]
    assert gh.branches == []
    assert app.invoke(reply("ok", "boss"), config)["status"] == "IN_REVIEW_PR"

    app, config, gh, _, _ = reach_gate(policy, {"maint": "maintain"}, action="deploy")
    assert app.invoke(reply("Aprovado", "maint"), config)["status"] == "WAITING_HUMAN"
    assert gh.branches == []


def test_free_levels_in_policy_skip_the_gate():
    policy = parse_policy("risk:\n  destructive_external: free\n")
    _, _, gh, _, result = reach_gate(policy, action="deploy")
    assert result["status"] == "IN_REVIEW_PR" and gh.branches


@pytest.mark.parametrize("text", [
    "risk:\n  nonsense: free\n",
    "risk:\n  read: superuser\n",
    "risk:\n  destructive_external: 3\n",
    "risk: free\n",
])
def test_invalid_risk_section_raises(text):
    with pytest.raises(PolicyError):
        parse_policy(text)


def test_policy_helpers():
    p = Policy()
    assert p.risk == RiskPolicy()
    assert p.risk_requirement("read") is None
    assert p.risk_requirement("destructive_external") == "maintain"
    assert p.risk_requirement("unknown") == "maintain"
    assert p.allows_risk("destructive_external", "admin")
    assert not p.allows_risk("destructive_external", "write")
    assert not p.allows_risk("destructive_external", None)
    assert p.allows_risk("read", None)
