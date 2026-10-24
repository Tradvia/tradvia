# SPDX-License-Identifier: FSL-1.1-ALv2
"""Which cards wait for a human, and which role may answer them. Pure and offline.

A card is a plain dict built from the store (see `cards_of`):
`{number, title, state, comments: [{author, body}], pulls: [{state, branch}]}`, comments oldest
first. The signals are the ones the dashboard uses for WAITING_HUMAN and IN_REVIEW_PR.
"""

from __future__ import annotations

import re
from typing import Any

from .actions import _PLAN_RE, agent_login
from .policy import Policy

PLAN_READY = "plan_ready"
AGENT_QUESTION = "agent_question"
PR_OPENED = "pr_opened"
BUDGET_LIMIT = "budget_limit"
ADJUST_LIMIT = "adjust_limit"
KINDS = (PLAN_READY, AGENT_QUESTION, PR_OPENED, BUDGET_LIMIT, ADJUST_LIMIT)

# The policy interaction that answers each kind (docs/projects.md).
ROLE_FOR_KIND = {
    PLAN_READY: "approve_plan",
    AGENT_QUESTION: "approve_plan",
    ADJUST_LIMIT: "approve_plan",
    PR_OPENED: "merge_pr",
    BUDGET_LIMIT: "budget_control",
}

# graph.py posts "Limite de N ajustes atingido"; the budget stop uses the same style.
_ADJUST_LIMIT_RE = re.compile(r"Limite de \d+ ajustes atingido", re.I)
_BUDGET_LIMIT_RE = re.compile(r"Limite de or[çc]amento|budget limit", re.I)
_ISSUE_IN_BRANCH = re.compile(r"^(?:tradvia/issue-(\d+)|claude/issue-(\d+)-)")


def issue_of_branch(branch: str | None) -> int | None:
    """Card number of an agent branch (`tradvia/issue-N` or `claude/issue-N-...`)."""
    m = _ISSUE_IN_BRANCH.match(branch or "")
    return int(m.group(1) or m.group(2)) if m else None


def _by(comment: dict[str, Any], login: str) -> bool:
    return (comment.get("author") or "").lower() == login.lower()


def pending_kind(card: dict[str, Any], agent: str | None = None) -> str | None:
    """Why the card waits for a human, or None when it does not."""
    if card.get("state") == "closed":
        return None
    login = agent or agent_login()
    number = card["number"]
    if any(
        p.get("state") == "open" and issue_of_branch(p.get("branch")) == number
        for p in card.get("pulls") or []
    ):
        return PR_OPENED
    comments = card.get("comments") or []
    last = max((i for i, c in enumerate(comments) if _by(c, login)), default=None)
    if last is None or any(not _by(c, login) for c in comments[last + 1:]):
        return None  # the agent never spoke, or a human already answered
    body = comments[last].get("body") or ""
    if _ADJUST_LIMIT_RE.search(body):
        return ADJUST_LIMIT
    if _BUDGET_LIMIT_RE.search(body):
        return BUDGET_LIMIT
    if _PLAN_RE.search(body):
        return PLAN_READY
    if "❓" in body:
        return AGENT_QUESTION
    return None


def can_answer(policy: Policy, role: str | None, kind: str) -> bool:
    return kind in ROLE_FOR_KIND and policy.allows(ROLE_FOR_KIND[kind], role)


def pending_for(
    role: str | None, policy: Policy, cards: list[dict[str, Any]], agent: str | None = None
) -> list[dict[str, Any]]:
    """`[{number, title, kind}]` of the cards `role` may answer, by card number.

    No role, or an unknown one, never answers anything: the list is empty.
    """
    out = []
    for card in cards:
        kind = pending_kind(card, agent)
        if kind is not None and can_answer(policy, role, kind):
            out.append({"number": card["number"], "title": card.get("title") or "", "kind": kind})
    return sorted(out, key=lambda c: c["number"])


def affected_cards(ops: list[tuple[str, dict[str, Any]]]) -> set[int]:
    """Card numbers touched by store operations (issue, comment, pull request, workflow run)."""
    found: set[Any] = set()
    for kind, rec in ops:
        if kind == "issue":
            found.add(rec.get("number"))
        elif kind == "pull_request":
            found.add(issue_of_branch(rec.get("branch")))
        else:
            found.add(rec.get("issue_number"))
    return {n for n in found if isinstance(n, int)}


def cards_of(db: Any, project_id: int, numbers: set[int] | None = None) -> list[dict[str, Any]]:
    """Open cards of a project (optionally only `numbers`) as `pending_kind` expects them."""
    issues = [
        i for i in db.list_issues(project_id, [], "open", 100_000) or []
        if numbers is None or i["number"] in numbers
    ]
    wanted = {i["number"] for i in issues}
    comments: dict[int, list[dict[str, Any]]] = {}
    for c in db.list_comments(project_id, sorted(wanted)):
        comments.setdefault(c["issue_number"], []).append(c)
    pulls: dict[int, list[dict[str, Any]]] = {}
    for p in db.list_pulls(project_id, "open", 100_000) or []:
        n = issue_of_branch(p.get("branch"))
        if n in wanted:
            pulls.setdefault(n, []).append(p)
    return [
        {
            "number": i["number"], "title": i["title"], "state": i["state"],
            "comments": comments.get(i["number"], []), "pulls": pulls.get(i["number"], []),
        }
        for i in issues
    ]


def kinds_of(db: Any, project_id: int, numbers: set[int]) -> dict[int, str]:
    """`{number: kind}` for the given cards that wait for a human right now."""
    found = {c["number"]: pending_kind(c) for c in cards_of(db, project_id, numbers)}
    return {n: k for n, k in found.items() if k}
