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
REQUIRED_CHECKS = ("core-tests", "license-boundary", "cla")
IGNORABLE_CHECK = "cla"  # the only required check a maintainer may ignore (and it is audited)
DEPLOY_WORKFLOW = "deploy-oci.yml"
DEPLOY_REF = "main"
_FAILED = ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale")
_CONVENTIONAL_TITLE = re.compile(r"[a-z]+(\([^)\s][^)]*\))?!?: \S")
_COST_RE = re.compile(
    r"Estimated cost \(API-equivalent\):\s*\$([0-9]+(?:\.[0-9]+)?)(?:\s*·\s*([0-9]+)s)?"
)
_COST_SO_FAR_RE = re.compile(r"estimated cost so far \(API-equivalent\):\s*\$([0-9]+(?:\.[0-9]+)?)")
_BACKLOG_RE = re.compile(r"[0-9]+(?: [0-9]+)*")

# Plan posted by ask_human (graph.py): contains "❓" and "Responda **Aprovado**, ...".
_PLAN_RE = re.compile(r"Responda\s+\**Aprovado", re.I)


class ActionError(ValueError):
    """The request is invalid; the message is safe to show to the user."""


class CardNotFound(ActionError):
    """The card is not part of the project (or of its backlog)."""


class GitHubError(Exception):
    """GitHub answered with an error status. `str()` carries no response body.

    `reason` is GitHub's own error text, kept only to tell a missing App permission from a
    refusal for the person; it is never shown or logged.
    """

    def __init__(self, status: int, reason: str = "") -> None:
        super().__init__(f"github answered {status}")
        self.status = status
        self.reason = reason

    @property
    def app_permission_missing(self) -> bool:
        """True when the GitHub App itself lacks a permission (not the person)."""
        return "not accessible by integration" in self.reason.lower()


class GitHubActions(Protocol):
    """What the panel actions need from GitHub. Every call takes the user's token."""

    def get_issue(self, token: str, repo: str, number: int) -> dict[str, Any]: ...
    def create_issue(self, token: str, repo: str, title: str, body: str) -> dict[str, Any]: ...
    def add_label(self, token: str, repo: str, number: int, label: str) -> None: ...
    def comment(self, token: str, repo: str, number: int, body: str) -> None: ...
    def list_comments(self, token: str, repo: str, number: int) -> list[dict[str, Any]]: ...

    # Pull requests and deploys (ADR 0007).
    def get_pull(self, token: str, repo: str, number: int) -> dict[str, Any]:
        """The pull request as GitHub returns it, plus `files` (filename, additions, deletions, patch)."""
        ...

    def list_checks(self, token: str, repo: str, sha: str) -> list[dict[str, Any]]:
        """Check runs of a commit: `name`, `status`, `conclusion`."""
        ...

    def list_review_comments(self, token: str, repo: str, number: int) -> list[dict[str, Any]]: ...
    def merge_pull(self, token: str, repo: str, number: int, title: str, sha: str) -> None: ...
    def delete_branch(self, token: str, repo: str, branch: str) -> None: ...
    def update_branch(self, token: str, repo: str, number: int) -> None: ...
    def list_runs(self, token: str, repo: str, status: str) -> list[dict[str, Any]]: ...
    def list_pending_deployments(self, token: str, repo: str, run_id: int) -> list[dict[str, Any]]: ...

    def review_pending_deployment(
        self, token: str, repo: str, run_id: int, environment_ids: list[int], approve: bool,
        comment: str,
    ) -> None: ...

    def cancel_run(self, token: str, repo: str, run_id: int) -> None: ...

    def dispatch_workflow(
        self, token: str, repo: str, workflow: str, ref: str, inputs: dict[str, str],
    ) -> None: ...


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


# --- pull requests and deploys (ADR 0007) -----------------------------------------------


def parse_pr_description(body: str | None) -> dict[str, Any]:
    """The executor's summary and cost from a PR description written by the agent.

    `summary` is the text under "### Claude Code" (None when absent); `cost_usd` and
    `duration_s` come from the "Estimated cost" line (None when absent).
    """
    text = body or ""
    summary = None
    m = re.search(r"^### Claude Code\s*\n(.*?)(?=\n\s*\nEstimated cost|\n### |\Z)", text, re.S | re.M)
    if m and m.group(1).strip():
        summary = m.group(1).strip()
    cost = _COST_RE.search(text)
    usd = float(cost.group(1)) if cost else None
    duration = float(cost.group(2)) if cost and cost.group(2) else None
    if usd is None:
        so_far = _COST_SO_FAR_RE.search(text)
        usd = float(so_far.group(1)) if so_far else None
    return {"summary": summary, "cost_usd": usd, "duration_s": duration}


def check_state(checks: list[dict[str, Any]], name: str) -> str:
    """"passed", "failed", "pending" or "missing" for the check called `name`."""
    runs = [c for c in checks if c.get("name") == name]
    if not runs:
        return "missing"
    if any(c.get("conclusion") in _FAILED for c in runs):
        return "failed"
    if any(c.get("status") != "completed" for c in runs):
        return "pending"
    return "passed"


def required_checks(checks: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": n, "state": check_state(checks, n)} for n in REQUIRED_CHECKS]


def merge_block(checks: list[dict[str, Any]], ignore_cla: bool = False) -> str | None:
    """Why the merge is refused, or None. A failed required check refuses it.

    Only the `cla` check can be ignored, and only when the user asked for it explicitly.
    """
    failed = [
        c["name"] for c in required_checks(checks)
        if c["state"] == "failed" and not (ignore_cla and c["name"] == IGNORABLE_CHECK)
    ]
    if not failed:
        return None
    hint = "" if failed == [IGNORABLE_CHECK] else " Only the 'cla' check can be ignored."
    return f"Merge refused: the required check(s) failed: {', '.join(failed)}.{hint}"


def merge_title(pull: dict[str, Any]) -> str:
    """The squash commit title: the PR's Conventional Commit title plus ` (#n)`."""
    title = (pull.get("title") or "").strip()
    if not _CONVENTIONAL_TITLE.match(title):
        raise ActionError(
            "The pull request title must be a Conventional Commit (e.g. 'feat(core): ...') "
            "to be squash-merged."
        )
    return f"{title} (#{pull['number']})"


def parse_flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ActionError(f"{key} must be true or false")
    return value


def backlog_string(view: list[dict[str, Any]]) -> str:
    """The backlog order for the deploy workflow: the open cards of the stored order."""
    return " ".join(str(it["issue_number"]) for it in view if it.get("state") != "closed")


def parse_backlog_input(value: object) -> str:
    """The `backlog` input of the deploy: card numbers separated by spaces, or empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ActionError("backlog must be card numbers separated by spaces")
    cleaned = " ".join(value.split())
    if cleaned and (len(cleaned) > 500 or not _BACKLOG_RE.fullmatch(cleaned)):
        raise ActionError("backlog must be card numbers separated by spaces (e.g. 44 50 45)")
    return cleaned


def _newest_first(r: dict[str, Any]) -> tuple[str, int]:
    return (r.get("created_at") or "", r["id"])


def replaced_run_ids(runs: list[dict[str, Any]]) -> set[int]:
    """Runs waiting in the same queue (workflow) that a newer waiting run has superseded."""
    newest: dict[Any, dict[str, Any]] = {}
    for r in runs:
        key = r.get("workflow_id") or r.get("path") or r.get("name")
        best = newest.get(key)
        if best is None or _newest_first(r) > _newest_first(best):
            newest[key] = r
    keep = {r["id"] for r in newest.values()}
    return {r["id"] for r in runs if r["id"] not in keep}


def deploy_view(
    runs: list[dict[str, Any]], pending: dict[int, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Deploys waiting for approval, newest first, with the superseded ones flagged."""
    replaced = replaced_run_ids(runs)
    out = []
    for r in sorted(runs, key=_newest_first, reverse=True):
        envs = pending.get(r["id"]) or []
        if not envs:
            continue
        out.append({
            "run_id": r["id"],
            "title": r.get("display_title") or r.get("name"),
            "branch": r.get("head_branch"),
            "html_url": r.get("html_url"),
            "created_at": r.get("created_at"),
            "environments": [(e.get("environment") or {}).get("name") for e in envs],
            "can_approve": any(e.get("current_user_can_approve") for e in envs),
            "replaced": r["id"] in replaced,
        })
    return out
