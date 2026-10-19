# SPDX-License-Identifier: FSL-1.1-ALv2
from tradvia.integrations import GitHubIssueChat


class Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    """Minimal stand-in for requests.Session over the issue comments API."""

    def __init__(self, comments):
        self.headers = {}
        self.comments = comments
        self.posted = []

    def post(self, url, json, timeout):
        c = {"id": 1000 + len(self.posted), "created_at": "2026-10-19T00:00:00Z",
             "body": json["body"], "author_association": "NONE", "user": {"login": "github-actions[bot]", "type": "Bot"}}
        self.posted.append(c)
        return Resp(c)

    def get(self, url, params, timeout):
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
    ])
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    mid = chat.post("plan?")
    assert mid == "1000" and s.headers["Authorization"] == "Bearer t"
    assert chat.wait_for_reply(mid, timeout_s=0) == {"text": "Aprovado", "by": "anderson"}


def test_times_out_without_a_trusted_reply():
    s = FakeSession([comment(1001, "Aprovado", "NONE", "stranger")])
    chat = GitHubIssueChat("t", "Tradvia/tradvia", 7, poll_s=0, session=s)
    assert chat.wait_for_reply(chat.post("plan?"), timeout_s=0) is None
