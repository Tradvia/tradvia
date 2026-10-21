# SPDX-License-Identifier: FSL-1.1-ALv2
"""Map GitHub payloads (webhook or REST) to store operations. Pure functions."""

from __future__ import annotations

import re
from typing import Any

Op = tuple[str, dict[str, Any]]

HANDLED_EVENTS = ("issues", "issue_comment", "pull_request", "workflow_run")
_ISSUE_IN_BRANCH = re.compile(r"(?:issue|card)-(\d+)")


class PayloadError(ValueError):
    """The payload does not have the shape GitHub documents."""


def _login(obj: Any) -> str | None:
    return (obj or {}).get("login")


def issue_record(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": issue["id"],
        "number": issue["number"],
        "title": issue.get("title") or "",
        "state": issue["state"],
        "labels": [lb["name"] for lb in issue.get("labels") or []],
        "author": _login(issue.get("user")),
        "created_at": issue.get("created_at"),
        "updated_at": issue["updated_at"],
        "closed_at": issue.get("closed_at"),
        "html_url": issue.get("html_url"),
        "state_reason": issue.get("state_reason"),
    }


def comment_record(comment: dict[str, Any], issue_number: int) -> dict[str, Any]:
    from .actions import agent_login  # lazy: actions -> backlog -> events would be circular

    body = comment.get("body") or ""
    author = _login(comment.get("user"))
    # Only the agent's plan comments carry a cost line; a human quoting one must not count.
    agent = author == agent_login()
    cost_usd, _ = parse_cost(body) if agent else (None, None)
    tokens = _token_fields(body if agent else None)
    return {
        "id": comment["id"],
        "issue_number": issue_number,
        "author": author,
        "author_association": comment.get("author_association"),
        "body": body,
        "created_at": comment.get("created_at"),
        "updated_at": comment["updated_at"],
        "cost_usd": cost_usd,
        **tokens,
    }


_NUM = r"(\d+(?:\.\d+)?)"
_SECS = r"(?:\s*[·•|-]\s*" + _NUM + r"\s*s\b)?"
# Same patterns, in the same order, as parseCost()/parseTokens() in dashboard/index.html.
_COST_PATTERNS = (
    re.compile(r"estimated cost \(api-equivalent\):\s*\$" + _NUM + _SECS, re.I),
    re.compile(r"\bcost:\s*\$" + _NUM + _SECS, re.I),
    re.compile(r"cost so far[^$\n]*\$" + _NUM, re.I),
)


def parse_cost(body: str | None) -> tuple[float | None, float | None]:
    """`(usd, seconds)` from the cost line Tradvia writes in a PR description.

    The PR body itself is not stored (it can be long and is not needed for reads);
    only these two numbers are, so the cost KPIs work when the panel reads the store.
    """
    if not isinstance(body, str):
        return None, None
    for pattern in _COST_PATTERNS:
        m = pattern.search(body)
        if m:
            groups = m.groups()
            seconds = float(groups[1]) if len(groups) > 1 and groups[1] is not None else None
            return float(groups[0]), seconds
    return None, None


# Thousands grouped by space, NBSP, narrow NBSP, dot or comma, or plain digits.
_INT = r"(\d{1,3}(?:[   .,]\d{3})+|\d+)"
_SEP = r"\s*[·•|-]\s*"
_TOKENS = re.compile(
    r"\btokens:\s*" + _INT + r"\s*in" + _SEP + _INT + r"\s*out(?:" + _SEP + _INT + r"\s*cache)?",
    re.I,
)


def parse_tokens(body: str | None) -> tuple[int, int, int] | None:
    """`(in, out, cache)` from the `Tokens: 12 345 in · 3 210 out · 80 000 cache` line."""
    if not isinstance(body, str):
        return None
    m = _TOKENS.search(body)
    if not m:
        return None
    n_in, n_out, n_cache = (int(re.sub(r"\D", "", g)) if g else 0 for g in m.groups())
    return n_in, n_out, n_cache


def _token_fields(body: str | None) -> dict[str, int | None]:
    tokens = parse_tokens(body)
    keys = ("tokens_in", "tokens_out", "tokens_cache")
    return dict(zip(keys, tokens)) if tokens else dict.fromkeys(keys)


_SO_FAR = re.compile(r"cost so far[^$\n]*\$" + _NUM, re.I)


def parse_cost_so_far(body: str | None) -> float | None:
    """The PR's "estimated cost so far": planning plus execution, for cards whose plan
    comments carry no cost line (written before plan costs were recorded)."""
    m = _SO_FAR.search(body) if isinstance(body, str) else None
    return float(m.group(1)) if m else None


def pull_request_record(pr: dict[str, Any]) -> dict[str, Any]:
    cost_usd, cost_seconds = parse_cost(pr.get("body"))
    return {
        "id": pr["id"],
        "number": pr["number"],
        "title": pr.get("title") or "",
        "state": pr["state"],
        "merged": bool(pr.get("merged") or pr.get("merged_at")),
        "branch": (pr.get("head") or {}).get("ref"),
        "author": _login(pr.get("user")),
        "created_at": pr.get("created_at"),
        "updated_at": pr["updated_at"],
        "merged_at": pr.get("merged_at"),
        "html_url": pr.get("html_url"),
        "cost_usd": cost_usd,
        "cost_seconds": cost_seconds,
        "cost_so_far_usd": parse_cost_so_far(pr.get("body")),
        **_token_fields(pr.get("body")),
    }


def workflow_run_record(run: dict[str, Any]) -> dict[str, Any]:
    issue_number = None
    for pr in run.get("pull_requests") or []:
        issue_number = pr.get("number")
        break
    if issue_number is None:
        m = _ISSUE_IN_BRANCH.search(run.get("head_branch") or "")
        issue_number = int(m.group(1)) if m else None
    status = run.get("status")
    return {
        "id": run["id"],
        "workflow": run.get("path") or "",
        "event": run.get("event"),
        "status": status,
        "conclusion": run.get("conclusion"),
        "issue_number": issue_number,
        "started_at": run.get("run_started_at") or run.get("created_at"),
        "ended_at": run.get("updated_at") if status == "completed" else None,
        "updated_at": run["updated_at"],
        "name": run.get("name"),
        "display_title": run.get("display_title"),
        "html_url": run.get("html_url"),
        "run_attempt": run.get("run_attempt"),
    }


def payload_ops(event: str, payload: dict[str, Any]) -> list[Op]:
    """Operations to apply for one webhook delivery. Raises PayloadError."""
    try:
        if event == "issues":
            return [("issue", issue_record(payload["issue"]))]
        if event == "issue_comment":
            issue = payload["issue"]
            ops: list[Op] = []
            if "pull_request" not in issue:
                ops.append(("issue", issue_record(issue)))
            ops.append(("comment", comment_record(payload["comment"], issue["number"])))
            return ops
        if event == "pull_request":
            return [("pull_request", pull_request_record(payload["pull_request"]))]
        if event == "workflow_run":
            return [("workflow_run", workflow_run_record(payload["workflow_run"]))]
    except (KeyError, TypeError, AttributeError) as e:
        raise PayloadError("invalid payload") from e
    return []
