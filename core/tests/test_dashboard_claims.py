# SPDX-License-Identifier: FSL-1.1-ALv2
"""The board shows who holds a card taken through MCP (ADR 0012), run in Node when available."""

from test_dashboard_kpis import HTML, js  # noqa: F401  (js skips the module without node)

CLAIM = ('<!-- tradvia:claim login=bia -->\n<!-- tradvia:agent {"agent": "Kiro", "provider": '
         '"Anthropic", "model": "claude-sonnet-5-5", "skills": ["frontend", "tests"]} -->\nTaken by @bia')


def comment(login, body):
    return {"user": {"login": login}, "body": body}


def test_the_holder_and_their_agent_are_read_from_the_claim_comment():
    held = js("claimHolder(c)", c=[comment("bia", CLAIM)])
    assert held == {"login": "bia", "agent": "Kiro", "provider": "Anthropic",
                    "model": "claude-sonnet-5-5", "skills": ["frontend", "tests"]}
    assert js("claimLine(claimHolder(c))", c=[comment("bia", CLAIM)]) == \
        "@bia · Kiro (Anthropic · claude-sonnet-5-5)"


def test_a_mark_written_by_someone_else_does_not_count():
    assert js("claimHolder(c)", c=[comment("eve", CLAIM)]) is None


def test_a_release_by_the_holder_frees_the_card():
    c = [comment("bia", CLAIM), comment("bia", "<!-- tradvia:release login=bia -->\nReleased")]
    assert js("claimHolder(c)", c=c) is None
    c.append(comment("ana", "<!-- tradvia:claim login=ana -->"))
    held = js("claimHolder(c)", c=c)
    assert held["login"] == "ana" and held["agent"] is None and held["skills"] == []


def test_a_broken_agent_mark_still_names_the_person():
    body = '<!-- tradvia:claim login=bia -->\n<!-- tradvia:agent {"agent": } -->'
    assert js("claimHolder(c)", c=[comment("bia", body)])["login"] == "bia"


def test_a_claimed_card_is_agent_working_until_its_pull_request_opens():
    issue = {"number": 7, "title": "x", "state": "open", "labels": [{"name": "agent:claimed"}]}
    assert js("deriveCardState(i, [], [])", i=issue) == "AGENT_WORKING"
    pr = {"state": "open", "head": {"ref": "tradvia/issue-7"}}
    assert js("deriveCardState(i, [p], [])", i=issue, p=pr) == "IN_REVIEW_PR"
    assert js("deriveCardState(i, [], [])", i={**issue, "state": "closed"}) == "DONE"
    store_issue = {**issue, "labels": ["agent:claimed"]}  # the store sends label names
    assert js("isClaimed(i)", i=store_issue) is True


def test_board_cards_are_ready_plus_claimed_once_and_never_a_pull_request():
    ready = [{"number": 1}, {"number": 2}, {"number": 3, "pull_request": {}}]
    claimed = [{"number": 2}, {"number": 9}]
    assert [i["number"] for i in js("mergeCards(r, c)", r=ready, c=claimed)] == [1, 2, 9]


def test_the_board_and_backlog_wiring_is_in_the_page():
    from panel_i18n import has_everywhere

    html = HTML.read_text(encoding="utf-8")
    assert '"/issues?labels=" + CLAIM_LABEL' in html and "await loadClaims(repo, cards)" in html
    assert has_everywhere("freeOnly", "claimedBy")
    assert "if (backlogFreeOnly && b.status !== \"pronto\") continue;" in html
