# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Card state, following core/contracts/card-state.v1.json."""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict

Status = Literal["BACKLOG", "AGENT_WORKING", "WAITING_HUMAN", "IN_REVIEW_PR", "DONE"]

# Allowed transitions from the contract (from -> set of to). "*" -> BACKLOG is always allowed.
TRANSITIONS: dict[str, set[str]] = {
    "BACKLOG": {"AGENT_WORKING"},
    "AGENT_WORKING": {"WAITING_HUMAN", "IN_REVIEW_PR"},
    "WAITING_HUMAN": {"AGENT_WORKING"},
    "IN_REVIEW_PR": {"AGENT_WORKING", "DONE"},
    "DONE": set(),
}


class InvalidTransition(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def transition(current: str, to: str, by: str, reason: str) -> dict:
    """Validate a status change and return the state update for it."""
    if to != "BACKLOG" and to not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"{current} -> {to} is not allowed by card-state.v1")
    entry = {"from": current, "to": to, "by": by, "reason": reason, "at": now()}
    return {"status": to, "history": [entry], "updated_at": entry["at"]}


def add_cost(a: dict | None, b: dict | None) -> dict:
    a, b = a or {}, b or {}
    return {
        "usd": round(a.get("usd", 0.0) + b.get("usd", 0.0), 6),
        "tokens_in": a.get("tokens_in", 0) + b.get("tokens_in", 0),
        "tokens_out": a.get("tokens_out", 0) + b.get("tokens_out", 0),
        "duration_s": round(a.get("duration_s", 0.0) + b.get("duration_s", 0.0), 3),
    }


class Card(TypedDict, total=False):
    id: str
    title: str
    description: str
    source: dict
    status: Status
    assignee: str
    plan: list[dict]
    file_path: str
    file_content: str
    branch: str | None
    pull_request_url: str | None
    pending_question: dict | None
    approvals: Annotated[list[dict], operator.add]
    cost: Annotated[dict, add_cost]
    history: Annotated[list[dict], operator.add]
    updated_at: str
