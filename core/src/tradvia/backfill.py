# SPDX-License-Identifier: FSL-1.1-ALv2
"""Initial load: read GitHub through the REST API and write to the Store.

Everything is an upsert by GitHub id, so running it again does not duplicate.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Callable

import requests

from .events import (
    comment_record,
    issue_record,
    pull_request_record,
    workflow_run_record,
)
from .store import UPSERT_METHODS, Store

GITHUB_API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 5
WORKFLOWS = ("agent.yml", "claude.yml")

GetJson = Callable[[str, dict[str, str]], Any]


def github_get_json(token: str) -> GetJson:
    def get(path: str, params: dict[str, str]) -> Any:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(f"{GITHUB_API}/{path}", params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    return get


def _pages(get: GetJson, path: str, params: dict[str, str], key: str | None = None):
    for page in range(1, MAX_PAGES + 1):
        data = get(path, {**params, "per_page": str(PER_PAGE), "page": str(page)})
        items = data[key] if key else data
        yield from items
        if len(items) < PER_PAGE:
            return


def backfill(store: Store, get: GetJson, repo: str) -> dict[str, int]:
    project_id = store.ensure_project(repo)
    counts = {"issues": 0, "comments": 0, "pull_requests": 0, "workflow_runs": 0}

    def write(kind: str, rec: dict[str, Any], counter: str) -> None:
        getattr(store, UPSERT_METHODS[kind])(project_id, rec)
        counts[counter] += 1

    issues = _pages(get, f"repos/{repo}/issues", {"labels": "agent:ready", "state": "all"})
    for issue in issues:
        if "pull_request" in issue:
            continue
        write("issue", issue_record(issue), "issues")
        comments = _pages(get, f"repos/{repo}/issues/{issue['number']}/comments", {})
        for comment in comments:
            write("comment", comment_record(comment, issue["number"]), "comments")

    for pr in _pages(get, f"repos/{repo}/pulls", {"state": "all"}):
        write("pull_request", pull_request_record(pr), "pull_requests")

    for workflow in WORKFLOWS:
        path = f"repos/{repo}/actions/workflows/{workflow}/runs"
        for run in _pages(get, path, {}, key="workflow_runs"):
            write("workflow_run", workflow_run_record(run), "workflow_runs")
    return counts


def main(
    store: Store | None = None,
    get: GetJson | None = None,
    argv: list[str] | None = None,
) -> dict[str, int]:
    from .api import registered_projects

    parser = argparse.ArgumentParser(prog="tradvia backfill")
    parser.add_argument(
        "--project",
        default=os.getenv("TRADVIA_REPO", "Tradvia/tradvia"),
        help="repository owner/repo (must be listed in TRADVIA_PROJECTS)",
    )
    repo = parser.parse_args(argv if argv is not None else []).project
    if repo not in registered_projects():
        raise SystemExit(f"Project {repo!r} is not listed in TRADVIA_PROJECTS")
    if store is None:
        from .store import PgStore

        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise SystemExit("Missing environment variable DATABASE_URL")
        store = PgStore(dsn)
    get = get or github_get_json(os.getenv("GITHUB_TOKEN", ""))
    counts = backfill(store, get, repo)
    print("backfill done: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
