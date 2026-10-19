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
    }


def comment_record(comment: dict[str, Any], issue_number: int) -> dict[str, Any]:
    return {
        "id": comment["id"],
        "issue_number": issue_number,
        "author": _login(comment.get("user")),
        "author_association": comment.get("author_association"),
        "body": comment.get("body") or "",
        "created_at": comment.get("created_at"),
        "updated_at": comment["updated_at"],
    }


def pull_request_record(pr: dict[str, Any]) -> dict[str, Any]:
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
