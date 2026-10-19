# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""End-to-end tests of the hello world flow with fake GitHub, chat and planner."""

import pytest
from langgraph.types import Command

from tradvia.graph import build_graph, classify_reply, safe_path
from tradvia.llm import _offline_plan
from tradvia.state import InvalidTransition, transition


class FakeGitHub:
    def __init__(self):
        self.branches, self.files, self.prs, self.comments = [], {}, [], []

    def get_issue(self, number):
        return {"number": number, "title": "Add hello world", "body": "Say hi.",
                "url": f"https://github.com/Tradvia/tradvia/issues/{number}"}

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
    p = _offline_plan(title, body, number)
    p["_cost"] = {"usd": 0.01, "tokens_in": 100, "tokens_out": 50}
    return p


def start(app, issue=1):
    config = {"configurable": {"thread_id": f"CARD-{issue}"}}
    result = app.invoke({"id": f"CARD-{issue}", "status": "BACKLOG",
                         "source": {"provider": "github", "issue_number": issue}}, config)
    return result, config


def statuses(card):
    return [h["to"] for h in card["history"]]


def test_approved_flow_opens_pull_request():
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=planner)
    result, config = start(app)

    # Paused at the approval gate, nothing written to GitHub yet
    assert result["__interrupt__"]
    assert result["status"] == "WAITING_HUMAN"
    assert gh.prs == [] and gh.branches == []
    assert "Aprovado" in chat.messages[0]

    result = app.invoke(Command(resume={"text": "Aprovado!", "by": "anderson"}), config)

    assert result["status"] == "IN_REVIEW_PR"
    assert result["pull_request_url"].endswith("/pull/2")
    assert gh.branches == ["tradvia/issue-1"]
    assert ("tradvia/issue-1", "docs/agent-notes/1-add-hello-world.md") in gh.files
    assert "Closes #1" in gh.prs[0][2]
    assert statuses(result) == ["AGENT_WORKING", "WAITING_HUMAN", "AGENT_WORKING", "IN_REVIEW_PR"]
    assert result["cost"]["usd"] == pytest.approx(0.01)
    assert all(step["done"] for step in result["plan"])


def test_rejected_flow_returns_to_backlog_without_pr():
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=planner)
    _, config = start(app)
    result = app.invoke(Command(resume={"text": "Rejeitado", "by": "anderson"}), config)
    assert result["status"] == "BACKLOG"
    assert gh.prs == [] and gh.branches == []


def test_feedback_replans_and_asks_again():
    gh, chat = FakeGitHub(), FakeChat()
    app = build_graph(gh, chat, planner=planner)
    _, config = start(app)
    result = app.invoke(Command(resume={"text": "use inglês no arquivo", "by": "anderson"}), config)
    assert result["__interrupt__"] and result["status"] == "WAITING_HUMAN"
    assert len(chat.messages) == 2
    result = app.invoke(Command(resume={"text": "ok", "by": "anderson"}), config)
    assert result["status"] == "IN_REVIEW_PR"
    assert result["cost"]["usd"] == pytest.approx(0.02)  # two planner calls


def approve_and_open_pr(gh, chat):
    app = build_graph(gh, chat, planner=planner)
    _, config = start(app)
    app.invoke(Command(resume={"text": "Aprovado", "by": "anderson"}), config)


def test_github_chat_on_same_issue_posts_single_pr_comment():
    gh, chat = FakeGitHub(), FakeChat()
    gh.repo = chat.repo = "Tradvia/tradvia"
    chat.issue = 1
    approve_and_open_pr(gh, chat)
    assert gh.comments == []
    assert sum("/pull/2" in m for m in chat.messages) == 1


def test_other_chat_keeps_issue_comment_and_chat_message():
    gh, chat = FakeGitHub(), FakeChat()  # e.g. Discord: no repo/issue attributes
    approve_and_open_pr(gh, chat)
    assert len(gh.comments) == 1 and "/pull/2" in gh.comments[0][1]
    assert sum("/pull/2" in m for m in chat.messages) == 1


@pytest.mark.parametrize("text,expected", [
    ("Aprovado", "approved"), ("aprovado, pode seguir", "approved"), ("LGTM", "approved"),
    ("Não", "rejected"), ("rejeitado", "rejected"), ("mude o nome do arquivo", "feedback"),
])
def test_classify_reply(text, expected):
    assert classify_reply(text) == expected


@pytest.mark.parametrize("path", ["/etc/passwd", "../x", ".github/workflows/x.yml",
                                  "core/LICENSE.md", "CLA.md", ".tradvia.yml",
                                  "sub/.tradvia.yml", ""])
def test_agents_cannot_touch_sensitive_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_contract_blocks_invalid_transitions():
    with pytest.raises(InvalidTransition):
        transition("BACKLOG", "DONE", "agent", "skip review")
