# SPDX-License-Identifier: FSL-1.1-ALv2
"""A request_human comment reaches Web Push and, when a channel is set, Discord (#120)."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import test_push as tp  # noqa: E402
from test_push import env  # noqa: E402,F401  (webhook secret and VAPID keys)

from tradvia import pending  # noqa: E402
from tradvia.api import create_app  # noqa: E402

MARK = pending.HUMAN_REQUEST_MARK
LIVE = f"Progress from @bia's agent:\n{MARK}\nLog in\n\nLive session: https://s.example/x"


class Discord:
    name = "Discord"

    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def post(self, text):
        if self.fail:
            raise RuntimeError("discord down")
        self.posts.append(text)
        return "1"


def request_event(author="bia"):
    return tp.comment_event(LIVE, author=author)


def test_a_request_pushes_to_who_holds_live_takeover_and_tells_discord():
    gh, sender, discord = tp.FakeGitHub(), tp.FakeSender(), Discord()
    app = create_app(store=tp.MemoryStore(), identity=gh, push_sender=sender, alert_discord=discord)
    tp.two_subscribers(app)  # ana is admin, bob triage
    assert tp.webhook(app, "issue_comment", request_event(), "r1").status_code == 202
    assert sender.sent == [(tp.URL["ana"], {"project": tp.SLUG, "card": 5, "kind": "human_request"})]
    assert len(discord.posts) == 1 and "https://s.example/x" in discord.posts[0]


def test_without_a_discord_channel_or_with_it_down_nothing_breaks():
    for discord in (None, Discord(fail=True)):
        gh, sender = tp.FakeGitHub(), tp.FakeSender()
        app = create_app(store=tp.MemoryStore(), identity=gh, push_sender=sender,
                         alert_discord=discord)
        tp.two_subscribers(app)
        assert tp.webhook(app, "issue_comment", request_event(), "r1").status_code == 202
        assert [p["kind"] for _, p in sender.sent] == ["human_request"]


def test_discord_alone_still_hears_about_a_request():
    discord = Discord()
    app = create_app(store=tp.MemoryStore(), identity=tp.FakeGitHub(), alert_discord=discord)
    assert tp.webhook(app, "issues", tp.issue_event(), "seed").status_code == 202
    assert tp.webhook(app, "issue_comment", request_event(), "r1").status_code == 202
    assert len(discord.posts) == 1
