# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

from tradvia import pending
from tradvia.policy import Policy
from tradvia.store import MemoryStore

AGENT = "github-actions[bot]"
PLAN = "Plano\n1. fazer\nResponda **Aprovado**, **Rejeitado** ou **Ajuste: ...**"


def card(number, comments=(), pulls=(), state="open"):
    return {
        "number": number, "title": f"card {number}", "state": state,
        "comments": [{"author": a, "body": b} for a, b in comments],
        "pulls": [{"state": s, "branch": b} for s, b in pulls],
    }


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    monkeypatch.delenv("TRADVIA_AGENT_LOGIN", raising=False)


@pytest.mark.parametrize(
    "c, kind",
    [
        (card(1, [(AGENT, PLAN)]), "plan_ready"),
        (card(2, [(AGENT, "Qual banco?\n❓ Postgres ou SQLite?")]), "agent_question"),
        (card(3, [(AGENT, "Limite de 3 ajustes atingido. Reinicie o card.")]), "adjust_limit"),
        (card(4, [(AGENT, "Limite de orçamento atingido para este card.")]), "budget_limit"),
        (card(5, pulls=[("open", "tradvia/issue-5")]), "pr_opened"),
        (card(6, pulls=[("open", "claude/issue-6-fix")]), "pr_opened"),
        (card(7, [(AGENT, PLAN), ("ana", "Aprovado")]), None),  # a human already answered
        (card(8, [("ana", "oi")]), None),  # the agent never spoke
        (card(9, [(AGENT, "Executando…")]), None),
        (card(10, [(AGENT, PLAN)], state="closed"), None),
        (card(11, pulls=[("closed", "tradvia/issue-11")]), None),
        (card(12, pulls=[("open", "tradvia/issue-99")]), None),  # another card's PR
        (card(13, [(AGENT, PLAN), ("ana", "Ajuste: x"), (AGENT, "novo plano ❓")]), "agent_question"),
    ],
)
def test_pending_kind(c, kind):
    assert pending.pending_kind(c) == kind


def test_agent_login_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRADVIA_AGENT_LOGIN", "tradvia-agent")
    assert pending.pending_kind(card(1, [(AGENT, PLAN)])) is None
    assert pending.pending_kind(card(1, [("Tradvia-Agent", PLAN)])) == "plan_ready"


def test_every_kind_maps_to_an_interaction():
    assert set(pending.ROLE_FOR_KIND) == set(pending.KINDS)
    assert pending.ROLE_FOR_KIND == {
        "plan_ready": "approve_plan", "agent_question": "approve_plan",
        "adjust_limit": "approve_plan", "pr_opened": "merge_pr", "budget_limit": "budget_control",
    }


CARDS = [
    card(1, [(AGENT, PLAN)]),
    card(2, pulls=[("open", "tradvia/issue-2")]),
    card(3, [(AGENT, "Limite de orçamento atingido")]),
    card(4, [(AGENT, "Executando")]),
]


def numbers(role):
    return [c["number"] for c in pending.pending_for(role, Policy(), CARDS)]


def test_pending_per_role():
    assert numbers("read") == []
    assert numbers("triage") == []  # approve_plan needs write
    assert numbers("write") == [1]  # no merge, no budget
    assert numbers("maintain") == [1, 2]  # merges, but no budget control
    assert numbers("admin") == [1, 2, 3]


def test_no_role_gives_an_empty_list():
    assert numbers(None) == []
    assert numbers("owner") == []


def test_policy_changes_who_sees_what():
    from tradvia.policy import parse_policy

    policy = parse_policy("interactions:\n  merge_pr: write\n  approve_plan: triage\n")
    assert [c["number"] for c in pending.pending_for("triage", policy, CARDS)] == [1]
    assert [c["number"] for c in pending.pending_for("write", policy, CARDS)] == [1, 2]


def test_items_are_number_title_kind():
    assert pending.pending_for("admin", Policy(), CARDS[:1]) == [
        {"number": 1, "title": "card 1", "kind": "plan_ready"}
    ]


def test_cards_of_reads_the_store():
    db = MemoryStore()
    pid = db.ensure_project("Tradvia/tradvia")
    stamp = "2026-01-01T00:00:00Z"
    for n, state in ((1, "open"), (2, "open"), (3, "closed")):
        db.upsert_issue(pid, {
            "id": 100 + n, "number": n, "title": f"t{n}", "state": state, "labels": [],
            "updated_at": stamp,
        })
    db.upsert_comment(pid, {
        "id": 1, "issue_number": 1, "author": AGENT, "body": PLAN, "created_at": stamp,
        "updated_at": stamp,
    })
    db.upsert_pull_request(pid, {
        "id": 9, "number": 20, "title": "pr", "state": "open", "branch": "tradvia/issue-2",
        "updated_at": stamp, "created_at": stamp,
    })
    cards = pending.cards_of(db, pid)
    assert sorted(c["number"] for c in cards) == [1, 2]  # closed cards are not loaded
    assert pending.kinds_of(db, pid, {1, 2, 3}) == {1: "plan_ready", 2: "pr_opened"}
    assert pending.kinds_of(db, pid, {2}) == {2: "pr_opened"}


def test_affected_cards():
    ops = [
        ("issue", {"number": 1}),
        ("comment", {"issue_number": 2}),
        ("pull_request", {"branch": "claude/issue-3-x"}),
        ("pull_request", {"branch": "feature/x"}),
        ("workflow_run", {"issue_number": None}),
        ("workflow_run", {"issue_number": 4}),
    ]
    assert pending.affected_cards(ops) == {1, 2, 3, 4}
