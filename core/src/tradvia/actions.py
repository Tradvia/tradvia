# SPDX-License-Identifier: FSL-1.1-ALv2
"""Panel actions (ADR 0004 phase 2): pure helpers for creating, starting and answering cards.

Everything here is free of I/O. The GitHub calls sit behind `GitHubActions`, every one made
with the signed-in user's own token, so the GitHub history shows who acted.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

from .backlog import parse_depends

DEFAULT_AGENT_LOGIN = "github-actions[bot]"
MAX_TITLE = 256
MAX_BODY = 10_000
MAX_TEXT = 2_000
DIRECTIONS = ("up", "down")
DECISIONS = ("approve", "reject", "adjust")

# Plan posted by ask_human (graph.py): contains "❓" and "Responda **Aprovado**, ...".
_PLAN_RE = re.compile(r"Responda\s+\**Aprovado", re.I)


class ActionError(ValueError):
    """The request is invalid; the message is safe to show to the user."""


class CardNotFound(ActionError):
    """The card is not part of the project (or of its backlog)."""


class GitHubError(Exception):
    """GitHub answered with an error status. The message carries no response body."""

    def __init__(self, status: int) -> None:
        super().__init__(f"github answered {status}")
        self.status = status


class GitHubActions(Protocol):
    """What the panel actions need from GitHub. Every call takes the user's token."""

    def get_issue(self, token: str, repo: str, number: int) -> dict[str, Any]: ...
    def create_issue(self, token: str, repo: str, title: str, body: str) -> dict[str, Any]: ...
    def add_label(self, token: str, repo: str, number: int, label: str) -> None: ...
    def comment(self, token: str, repo: str, number: int, body: str) -> None: ...
    def list_comments(self, token: str, repo: str, number: int) -> list[dict[str, Any]]: ...


def agent_login(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("TRADVIA_AGENT_LOGIN", "").strip() or DEFAULT_AGENT_LOGIN


def parse_reply(decision: object, text: object) -> str:
    """The comment for a decision: "Aprovado", "Rejeitado" or "Ajuste: <text>"."""
    if decision == "approve":
        return "Aprovado"
    if decision == "reject":
        return "Rejeitado"
    if decision != "adjust":
        raise ActionError("decision must be one of: approve, reject, adjust")
    cleaned = text.strip() if isinstance(text, str) else ""
    if not cleaned:
        raise ActionError("text is required to adjust the plan")
    if len(cleaned) > MAX_TEXT:
        raise ActionError(f"text is too long (at most {MAX_TEXT} characters)")
    return f"Ajuste: {cleaned}"


def plan_comment(comments: list[dict[str, Any]], login: str) -> str | None:
    """Body of the agent's last comment when it is the plan and nobody answered it yet.

    The agent takes the first human comment after the plan as the answer (any text is
    approval, rejection or an adjustment), so a reply already posted closes the window.
    """
    def by_agent(c: dict[str, Any]) -> bool:
        return ((c.get("user") or {}).get("login") or "").lower() == login.lower()

    last = max((i for i, c in enumerate(comments) if by_agent(c)), default=None)
    if last is None:
        return None
    if any(not by_agent(c) for c in comments[last + 1:]):
        return None
    body = comments[last].get("body") or ""
    return body if "❓" in body or _PLAN_RE.search(body) else None


def awaiting_plan(comments: list[dict[str, Any]], login: str) -> bool:
    """True only when the last comment by the agent is the plan (comments oldest first)."""
    return plan_comment(comments, login) is not None


def reorder(items: list[dict[str, Any]], number: int, direction: str) -> list[dict[str, Any]]:
    """A copy of `items` with `number` moved one place up or down; the ends stay put."""
    if direction not in DIRECTIONS:
        raise ActionError("direction must be 'up' or 'down'")
    out = list(items)
    index = next((i for i, it in enumerate(out) if it["issue_number"] == number), None)
    if index is None:
        raise CardNotFound("card is not in the backlog")
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(out):
        out[index], out[target] = out[target], out[index]
    return out


def apply_order(items: list[dict[str, Any]], order: object) -> list[dict[str, Any]]:
    """`items` in the given order, which must list every backlog card exactly once."""
    by_number = {it["issue_number"]: it for it in items}
    if (
        not isinstance(order, list)
        or any(not isinstance(n, int) or isinstance(n, bool) for n in order)
        or sorted(order) != sorted(by_number)
    ):
        raise ActionError("order must list every card of the backlog exactly once")
    return [by_number[n] for n in order]


def new_backlog(items: list[dict[str, Any]], number: int, body: str | None = None) -> list[dict[str, Any]]:
    """`items` with the card appended at the end (nothing changes if it is already there)."""
    if any(it["issue_number"] == number for it in items):
        return list(items)
    return [*items, {"issue_number": number, "depends_on": parse_depends(body), "note": ""}]


def parse_new_card(data: dict[str, Any]) -> tuple[str, str]:
    """`(title, description)` of a new card."""
    title = data.get("title")
    description = data.get("description", "")
    if not isinstance(title, str) or not title.strip():
        raise ActionError("title is required")
    if len(title.strip()) > MAX_TITLE:
        raise ActionError(f"title is too long (at most {MAX_TITLE} characters)")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > MAX_BODY:
        raise ActionError(f"description must be text of at most {MAX_BODY} characters")
    return title.strip(), description
