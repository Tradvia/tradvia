# SPDX-License-Identifier: FSL-1.1-ALv2
"""Project backlog: dependency parsing, computed status and the `tradvia backlog` CLI."""

from __future__ import annotations

import argparse
import os
import re
from typing import Any

from .events import issue_record
from .store import Store

READY_LABEL = "agent:ready"
BRANCH_PREFIX = "tradvia/issue-"

# "Depende do #44 (…)" anywhere in the body, up to the end of that sentence or line.
_DEPENDS_LINE = re.compile(r"\b(?:depende\s+d[oae]s?|depends\s+on)\b([^.\n]*)", re.I)
_ISSUE_REF = re.compile(r"#(\d+)")


def parse_depends(body: str | None) -> list[int]:
    """Issue numbers from phrases such as "Depende do #44, #45" or "Depends on #44"."""
    found: set[int] = set()
    for line in _DEPENDS_LINE.findall(body or ""):
        found.update(int(n) for n in _ISSUE_REF.findall(line))
    return sorted(found)


def backlog_status(
    item: dict[str, Any],
    issue: dict[str, Any] | None,
    pulls: list[dict[str, Any]],
    dep_issues: dict[int, dict[str, Any]],
) -> str:
    """`concluído`, `em andamento`, `bloqueado` or `pronto`, checked in that order.

    `pulls` are the open pull requests. A dependency missing from `dep_issues`
    counts as open.
    """
    if issue is not None and issue["state"] == "closed":
        return "concluído"
    number = item["issue_number"]
    has_pr = any(p.get("branch") == f"{BRANCH_PREFIX}{number}" for p in pulls)
    if has_pr or (issue is not None and READY_LABEL in issue["labels"]):
        return "em andamento"
    for dep in item["depends_on"]:
        found = dep_issues.get(dep)
        if found is None or found["state"] != "closed":
            return "bloqueado"
    return "pronto"


def _numbers(args: list[str]) -> list[int]:
    numbers: list[int] = []
    for arg in args:
        if not arg.isdigit() or int(arg) < 1:
            raise SystemExit(f"Invalid issue number: {arg!r}")
        n = int(arg)
        if n in numbers:
            raise SystemExit(f"Duplicate issue number: {n}")
        numbers.append(n)
    return numbers


def main(
    store: Store | None = None,
    get: Any = None,
    argv: list[str] | None = None,
) -> list[dict[str, Any]]:
    from .api import registered_projects
    from .backfill import github_get_json

    parser = argparse.ArgumentParser(prog="tradvia backlog")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("set", "show"):
        p = sub.add_parser(name)
        p.add_argument("--project", default=os.getenv("TRADVIA_REPO", "Tradvia/tradvia"),
                       help="repository owner/repo (must be listed in TRADVIA_PROJECTS)")
        if name == "set":
            p.add_argument("issues", nargs="+", help="issue numbers, in backlog order")
    args = parser.parse_args(argv if argv is not None else [])
    repo = args.project
    if repo not in registered_projects():
        raise SystemExit(f"Project {repo!r} is not listed in TRADVIA_PROJECTS")
    numbers = _numbers(args.issues) if args.action == "set" else []
    if store is None:
        from .store import PgStore

        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise SystemExit("Missing environment variable DATABASE_URL")
        store = PgStore(dsn)
    project_id = store.ensure_project(repo)

    if args.action == "set":
        get = get or github_get_json(os.getenv("GITHUB_TOKEN", ""))
        items = []
        for n in numbers:
            issue = get(f"repos/{repo}/issues/{n}", {})
            if "id" in issue and "pull_request" not in issue:
                # Cards not started yet are not backfilled (only `agent:ready` issues are),
                # so store each one here: the backlog needs its title, state and labels.
                store.upsert_issue(project_id, issue_record(issue))
            items.append({"issue_number": n, "depends_on": parse_depends(issue.get("body")),
                          "note": ""})
        store.set_backlog(project_id, items)
        print(f"backlog set: {len(items)} items")
        return items

    items = store.list_backlog(project_id)
    titles = {i["number"]: i["title"]
              for i in store.list_issues(project_id, [], "all", 10_000) or []}
    for item in items:
        deps = ", ".join(f"#{d}" for d in item["depends_on"]) or "-"
        title = titles.get(item["issue_number"], "?")
        print(f"{item['position'] + 1:>3}. #{item['issue_number']} {title} (depends on: {deps})")
    return items
