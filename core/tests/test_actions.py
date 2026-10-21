# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

from tradvia.actions import (
    ActionError,
    CardNotFound,
    agent_login,
    apply_order,
    awaiting_plan,
    backlog_string,
    merge_block,
    merge_title,
    new_backlog,
    parse_backlog_input,
    parse_pr_description,
    replaced_run_ids,
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
    # The agent takes the first human comment after the plan as the answer: the window closes.
    assert not awaiting_plan([plan, human], BOT)
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


def test_parse_pr_description():
    body = ("Closes #9\n\n### Claude Code\nFiz X.\nE Y.\n\n"
            "Estimated cost (API-equivalent): $0.4200 · 31s\n\n### Ignored scratch files\n- a")
    assert parse_pr_description(body) == {"summary": "Fiz X.\nE Y.", "cost_usd": 0.42, "duration_s": 31.0}
    assert parse_pr_description("Card · estimated cost so far (API-equivalent): $1.5000") == {
        "summary": None, "cost_usd": 1.5, "duration_s": None}
    assert parse_pr_description(None)["cost_usd"] is None


def _run(name, conclusion="success"):
    return {"name": name, "status": "completed", "conclusion": conclusion}


def test_merge_block_and_ignorable_cla():
    ok = [_run("core-tests"), _run("license-boundary"), _run("cla")]
    assert merge_block(ok) is None
    bad_cla = [_run("core-tests"), _run("license-boundary"), _run("cla", "failure")]
    assert "cla" in merge_block(bad_cla)
    assert merge_block(bad_cla, ignore_cla=True) is None
    bad_tests = [_run("core-tests", "failure"), _run("license-boundary"), _run("cla", "failure")]
    assert "core-tests" in merge_block(bad_tests, ignore_cla=True)
    # missing or pending checks are left to GitHub's own branch rules
    assert merge_block([]) is None
    assert merge_block([{"name": "core-tests", "status": "in_progress", "conclusion": None}]) is None


def test_merge_title_needs_a_conventional_commit():
    assert merge_title({"title": "feat(core): x", "number": 3}) == "feat(core): x (#3)"
    assert merge_title({"title": "fix!: y", "number": 4}) == "fix!: y (#4)"
    with pytest.raises(ActionError):
        merge_title({"title": "Add x", "number": 3})


def test_backlog_string_and_input():
    view = [{"issue_number": 44, "state": "open"}, {"issue_number": 7, "state": "closed"},
            {"issue_number": 45, "state": None}]
    assert backlog_string(view) == "44 45"
    assert parse_backlog_input("  44   50 ") == "44 50"
    assert parse_backlog_input(None) == "" and parse_backlog_input("") == ""
    for bad in ("44,50", "a", 44, "44 $(x)"):
        with pytest.raises(ActionError):
            parse_backlog_input(bad)


def test_replaced_run_ids_keeps_the_newest_per_queue():
    runs = [
        {"id": 1, "workflow_id": 9, "created_at": "2026-01-01T10:00:00Z"},
        {"id": 2, "workflow_id": 9, "created_at": "2026-01-01T12:00:00Z"},
        {"id": 3, "workflow_id": 9, "created_at": "2026-01-01T11:00:00Z"},
        {"id": 4, "workflow_id": 8, "created_at": "2026-01-01T09:00:00Z"},
    ]
    assert replaced_run_ids(runs) == {1, 3}
    assert replaced_run_ids([]) == set()
