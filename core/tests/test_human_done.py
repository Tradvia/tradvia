# SPDX-License-Identifier: FSL-1.1-ALv2
"""human_done: a person says they finished what an agent asked with request_human (#120)."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, BASE, TOKEN, as_role, client, clock, env, gh, post, signed, store,
)
import test_push as tp  # noqa: E402

from tradvia import pending  # noqa: E402
from tradvia.api import create_app, human_request_notice  # noqa: E402

MARK = pending.HUMAN_REQUEST_MARK
ASK = {"user": {"login": "bia"}, "body": f"Progress from @bia's agent:\n{MARK}\nLog in with 2FA"}
LIVE = {"user": {"login": "bia"},
        "body": f"Progress from @bia's agent:\n{MARK}\nLog in\n\nLive session: https://s.example/x"}


def asked(gh, comments=(ASK,)):
    gh.issues[9] = {"number": 9, "state": "open", "labels": []}
    gh.comments[9] = list(comments)


def test_done_posts_the_comment_with_the_users_token(signed, gh):
    asked(gh)
    r = post(signed, BASE + "/cards/9/human-done")
    assert r.status_code == 200
    assert gh.writes("comment") == [
        ("comment", TOKEN, A, 9, "Done — @ana finished what the agent asked.")]
    # the comment closes the pending item
    after = [ASK, {"user": {"login": "ana"}, "body": gh.writes("comment")[0][4]}]
    assert pending.human_request_comment(after) is None


def test_done_needs_an_open_request(signed, gh):
    asked(gh, [ASK, {"user": {"login": "bob"}, "body": "feito"}])  # already answered
    assert post(signed, BASE + "/cards/9/human-done").status_code == 409
    asked(gh, [{"user": {"login": "bia"}, "body": "just progress"}])  # never asked
    assert post(signed, BASE + "/cards/9/human-done").status_code == 409
    asked(gh)
    gh.issues[9]["state"] = "closed"
    assert post(signed, BASE + "/cards/9/human-done").status_code == 409
    assert not gh.writes("comment")


def test_done_needs_live_takeover_and_csrf(store, gh, clock):
    asked(gh)
    writer = as_role(store, gh, clock, "write")  # live_takeover is maintain by default
    r = post(writer, BASE + "/cards/9/human-done")
    assert r.status_code == 403 and "maintain" in r.json()["error"]
    admin = as_role(store, gh, clock, "admin")
    assert post(admin, BASE + "/cards/9/human-done", csrf=False).status_code == 403
    assert not gh.writes("comment")


def test_done_is_audited(signed, gh, store):
    asked(gh)
    assert post(signed, BASE + "/cards/9/human-done").status_code == 200
    rows = store.list_audit(store.ensure_project(A), 9)
    assert rows and rows[-1]["action"] == "human_done" and rows[-1]["result"] == "ok"


def test_live_url_is_read_from_the_request():
    assert pending.live_url_of(pending.human_request_comment([LIVE])) == "https://s.example/x"
    assert pending.live_url_of(pending.human_request_comment([ASK])) is None
    assert pending.live_url_of(None) is None


def test_notice_has_the_card_link_and_the_live_url():
    text = human_request_notice("o/r", {"number": 9, "comments": [LIVE]})
    assert "https://github.com/o/r/issues/9" in text and "https://s.example/x" in text
    assert "Live session" not in human_request_notice("o/r", {"number": 9, "comments": [ASK]})
