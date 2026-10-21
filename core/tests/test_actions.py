# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

from tradvia.actions import (
    ActionError,
    CardNotFound,
    agent_login,
    apply_order,
    awaiting_plan,
    new_backlog,
    parse_new_card,
    parse_reply,
    plan_comment,
    reorder,
)

BOT = "github-actions[bot]"
PLAN = "🟡 **#8**\n1. passo\n\n❓ ok?\nResponda **Aprovado**, **Rejeitado** ou escreva um ajuste."


def items(*numbers):
    return [{"issue_number": n, "depends_on": [], "note": ""} for n in numbers]


def order(rows):
    return [r["issue_number"] for r in rows]


def test_parse_reply():
    assert parse_reply("approve", None) == "Aprovado"
    assert parse_reply("reject", "ignored") == "Rejeitado"
    assert parse_reply("adjust", "  troque X  ") == "Ajuste: troque X"


@pytest.mark.parametrize("decision, text", [
    ("adjust", None), ("adjust", "   "), ("adjust", "x" * 2001), ("adjust", 5),
    ("maybe", "x"), (None, None),
])
def test_parse_reply_rejects_bad_input(decision, text):
    with pytest.raises(ActionError):
        parse_reply(decision, text)


def test_awaiting_plan_only_when_the_last_agent_comment_is_the_plan():
    human = {"user": {"login": "ana"}, "body": "Aprovado"}
    plan = {"user": {"login": BOT}, "body": PLAN}
    other = {"user": {"login": BOT}, "body": "Rodando o agente."}
    assert awaiting_plan([plan], BOT)
    assert awaiting_plan([plan, human], BOT)  # a human reply does not move the agent
    assert not awaiting_plan([plan, other], BOT)
    assert not awaiting_plan([other, plan, other], BOT)
    assert not awaiting_plan([], BOT)
    assert not awaiting_plan([{"user": {"login": "ana"}, "body": PLAN}], BOT)
    assert not awaiting_plan([{"user": None, "body": PLAN}], BOT)
    assert plan_comment([plan], BOT.upper()) == PLAN


def test_agent_login_default_and_override():
    assert agent_login({}) == BOT
    assert agent_login({"TRADVIA_AGENT_LOGIN": " tradvia-bot[bot] "}) == "tradvia-bot[bot]"


def test_reorder_moves_one_place():
    rows = items(1, 2, 3)
    assert order(reorder(rows, 2, "up")) == [2, 1, 3]
    assert order(reorder(rows, 2, "down")) == [1, 3, 2]
    assert order(rows) == [1, 2, 3]  # the input is untouched


def test_reorder_boundaries_do_nothing():
    rows = items(1, 2, 3)
    assert order(reorder(rows, 1, "up")) == [1, 2, 3]
    assert order(reorder(rows, 3, "down")) == [1, 2, 3]
    assert order(reorder(items(1), 1, "down")) == [1]


def test_reorder_errors():
    with pytest.raises(CardNotFound):
        reorder(items(1), 9, "up")
    with pytest.raises(ActionError):
        reorder(items(1), 1, "sideways")


def test_apply_order():
    rows = items(1, 2, 3)
    assert order(apply_order(rows, [3, 1, 2])) == [3, 1, 2]
    for bad in ([1, 2], [1, 2, 3, 3], [1, 2, 4], "123", [True, 2, 3], None):
        with pytest.raises(ActionError):
            apply_order(rows, bad)


def test_new_backlog_appends_at_the_end_with_dependencies():
    out = new_backlog(items(1, 2), 5, "Depende do #2 e #1.")
    assert order(out) == [1, 2, 5] and out[-1]["depends_on"] == [1, 2]
    assert order(new_backlog(items(1, 2), 2)) == [1, 2]  # no duplicates
    assert order(new_backlog([], 7)) == [7]


def test_parse_new_card():
    assert parse_new_card({"title": " T ", "description": "d"}) == ("T", "d")
    assert parse_new_card({"title": "T"}) == ("T", "")
    for bad in ({}, {"title": ""}, {"title": 3}, {"title": "x" * 257},
                {"title": "T", "description": 4}, {"title": "T", "description": "x" * 10_001}):
        with pytest.raises(ActionError):
            parse_new_card(bad)
