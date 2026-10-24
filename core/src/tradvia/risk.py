# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Risk levels for actions (ADR 0006): what an action may do decides who must approve it."""

from __future__ import annotations

READ = "read"
REVERSIBLE_WRITE = "reversible_write"
DESTRUCTIVE_EXTERNAL = "destructive_external"
RISK_LEVELS = (READ, REVERSIBLE_WRITE, DESTRUCTIVE_EXTERNAL)

DEFAULT_ACTION_RISK: dict[str, str] = {
    "get_issue": READ,
    "read_repo": READ,
    "create_branch": REVERSIBLE_WRITE,
    "put_file": REVERSIBLE_WRITE,
    "open_pull_request": REVERSIBLE_WRITE,
    "comment": REVERSIBLE_WRITE,
    "merge_pr": DESTRUCTIVE_EXTERNAL,
    "delete": DESTRUCTIVE_EXTERNAL,
    "publish": DESTRUCTIVE_EXTERNAL,
    "deploy": DESTRUCTIVE_EXTERNAL,
    "send_external_message": DESTRUCTIVE_EXTERNAL,
}


def classify(action: str, overrides: dict[str, str] | None = None) -> str:
    """Risk level of `action`. Fails closed: an undeclared action is destructive_external."""
    level = (overrides or {}).get(action, DEFAULT_ACTION_RISK.get(action))
    return level if level in RISK_LEVELS else DESTRUCTIVE_EXTERNAL
