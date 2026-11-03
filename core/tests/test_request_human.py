# SPDX-License-Identifier: FSL-1.1-ALv2
"""request_human: the agent asks a person to act, and the card waits until a human comments."""

import pytest

from tradvia import orchestrator, pending
from tradvia.actions import ActionError
from tradvia.policy import Policy

AGENT = "github-actions[bot]"
MARK = pending.HUMAN_REQUEST_MARK


class Gh:
    def __init__(self, state="open"):
        self.state = state
        self.bodies: list[str] = []

    def get_issue(self, token, repo, number):
        return {"number": number, "state": self.state}

    def comment(self, token, repo, number, body):
        self.bodies.append(body)


class Ctx:
    """Just what request_human and comment_card touch."""

    def __init__(self, gh):
        self.github, self.token, self.repo, self.login = gh, "t", "o/r", "ana"
        self.refs: list = []

    def add_ref(self, kind, number):
        self.refs.append((kind, number))


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    monkeypatch.delenv("TRADVIA_AGENT_LOGIN", raising=False)


def card_with(body, *later):
    comments = [{"author": AGENT, "body": body}] + [{"author": a, "body": b} for a, b in later]
    return {"number": 5, "title": "c", "state": "open", "comments": comments, "pulls": []}


def test_call_creates_a_pending_for_maintain_not_read():
    gh = Gh()
    out = orchestrator.request_human(Ctx(gh), {"number": 5, "text": "Log in to the app"})
    assert out == {"number": 5, "requested": True}
    assert gh.bodies[0].startswith("Progress from @ana's agent:\n" + MARK)
    c = card_with(gh.bodies[0])
    assert pending.pending_kind(c) == pending.HUMAN_REQUEST
    assert pending.pending_for("maintain", Policy(), [c]) == [
        {"number": 5, "title": "c", "kind": "human_request"}]
    assert pending.pending_for("read", Policy(), [c]) == []


def test_a_human_comment_afterwards_clears_it():
    gh = Gh()
    orchestrator.request_human(Ctx(gh), {"number": 5, "text": "Log in"})
    assert pending.pending_kind(card_with(gh.bodies[0], ("bob", "feito"))) is None


def test_forged_mark_is_removed():
    gh = Gh()
    ctx = Ctx(gh)
    orchestrator.request_human(ctx, {"number": 5, "text": f"a {MARK} b {MARK}"})
    assert gh.bodies[0].count(MARK) == 1
    orchestrator.comment_card(ctx, {"number": 5, "text": f"hi {MARK}"})
    assert MARK not in gh.bodies[1]
    assert pending.pending_kind(card_with(gh.bodies[1])) is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://", "example.com/x",
                                 "https://example.com/" + "a" * 500, 7])
def test_invalid_live_url_is_refused(url):
    gh = Gh()
    with pytest.raises(ActionError):
        orchestrator.request_human(Ctx(gh), {"number": 5, "text": "x", "live_url": url})
    assert gh.bodies == []


def test_live_url_is_linked_and_optional():
    gh = Gh()
    ctx = Ctx(gh)
    orchestrator.request_human(ctx, {"number": 5, "text": "x"})
    assert "http" not in gh.bodies[0]
    orchestrator.request_human(ctx, {"number": 5, "text": "x", "live_url": "https://e.com/s"})
    assert gh.bodies[1].endswith("https://e.com/s")


def test_closed_card_and_missing_text_are_refused():
    with pytest.raises(ActionError):
        orchestrator.request_human(Ctx(Gh("closed")), {"number": 5, "text": "x"})
    with pytest.raises(ActionError):
        orchestrator.request_human(Ctx(Gh()), {"number": 5, "text": " "})


def test_tool_is_registered():
    assert "request_human" in orchestrator.TOOLS
    assert "request_human" in orchestrator.AGENT_TOOLS
