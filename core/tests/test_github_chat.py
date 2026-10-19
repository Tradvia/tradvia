# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

from tradvia.integrations import GitHubIssueChat
from tradvia.policy import Interactions, Policy


class Resp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    """Minimal stand-in for requests.Session over the issue comments API."""

    def __init__(self, comments, permissions=None):
        self.headers = {}
        self.comments = comments
        self.permissions = permissions or {}
        self.posted = []
        self.permission_calls = []

    def post(self, url, json, timeout):
        c = {"id": 1000 + len(self.posted), "created_at": "2026-10-19T00:00:00Z",
             "body": json["body"], "author_association": "NONE", "user": {"login": "github-actions[bot]", "type": "Bot"}}
        self.posted.append(c)
        return Resp(c)

    def get(self, url, params=None, timeout=None):
        if "/collaborators/" in url:
            login = url.split("/collaborators/")[1].split("/")[0]
            self.permission_calls.append(login)
            if login in self.permissions:
                return Resp({"role_name": self.permissions[login]})
            return Resp({"message": "Not Found"}, 404)
        assert params.get("since") == "2026-10-19T00:00:00Z"
        return Resp(self.comments)


def comment(id, body, assoc, login="someone", kind="User"):
    return {"id": id, "body": body, "author_association": assoc, "user": {"login": login, "type": kind}}


def test_only_trusted_humans_after_the_question_can_answer():
    s = FakeSession([
        comment(900, "Aprovado", "OWNER", "anderson"),           # before the question
        comment(1001, "Aprovado", "NONE", "stranger"),            # outsider
        comment(1002, "Aprovado", "CONTRIBUTOR", "past-pr-author"),
        comment(1003, "ok", "MEMBER", "ci-bot", kind="Bot"),     # bot
        comment(1005, "Ajuste: use docs/notes", "COLLABORATOR", "maria"),
        comment(1004, "Aprovado", "OWNER", "anderson"),          # earliest valid one wins
    ], permissions={"anderson": "admin", "maria": "write", "past-pr-author": "read"})
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    mid = chat.post("plan?")
    assert mid == "1000" and s.headers["Authorization"] == "Bearer t"
    assert chat.wait_for_reply(mid, timeout_s=0) == {"text": "Aprovado", "by": "anderson"}


def test_times_out_without_a_trusted_reply():
    s = FakeSession([comment(1001, "Aprovado", "NONE", "stranger")])
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) is None


def test_private_org_member_with_write_access_can_approve():
    # Private org membership: the Actions token sees CONTRIBUTOR, not MEMBER.
    s = FakeSession([
        comment(1001, "Aprovado", "CONTRIBUTOR", "drive-by", ),
        comment(1002, "Aprovado", "CONTRIBUTOR", "adutraadm"),
    ], permissions={"drive-by": "read", "adutraadm": "admin"})
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 5, poll_s=0, session=s)
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) == {"text": "Aprovado", "by": "adutraadm"}
    assert s.permission_calls == ["drive-by", "adutraadm"]


@pytest.mark.parametrize("role,accepted", [("read", False), ("triage", False),
                                           ("write", True), ("admin", True)])
def test_approve_plan_default_policy_by_role(role, accepted):
    s = FakeSession([comment(1001, "Aprovado", "COLLABORATOR", "pat")], permissions={"pat": role})
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    expected = {"text": "Aprovado", "by": "pat"} if accepted else None
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) == expected


@pytest.mark.parametrize("role,accepted", [("write", False), ("maintain", False), ("admin", True)])
def test_approve_plan_policy_requiring_admin(role, accepted):
    s = FakeSession([comment(1001, "Aprovado", "OWNER", "pat")], permissions={"pat": role})
    policy = Policy(interactions=Interactions(approve_plan="admin"))
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s, policy=policy)
    expected = {"text": "Aprovado", "by": "pat"} if accepted else None
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) == expected


@pytest.mark.parametrize("login,kind", [("ci[bot]", "User"), ("robot", "Bot")])
def test_bots_never_approve_even_with_admin_role(login, kind):
    s = FakeSession([comment(1001, "Aprovado", "OWNER", login, kind=kind)],
                    permissions={login: "admin"})
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) is None
