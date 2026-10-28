# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Suite runner and planners for the evals harness (ADR 0016).

`scripted` is the default: fixed answers from `scripts/<label>.yaml`, no network, no key.
`live` calls `llm.architect_plan` with TRADVIA_PLANNER_MODEL and needs TRADVIA_EVALS_LIVE=1;
without it litellm is never imported.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from . import Planner, Result, Task, run_task

SCRIPTS_DIR = Path(__file__).parent / "scripts"
LIVE_ENV = "TRADVIA_EVALS_LIVE"


def scripted_planner(label: str, directory: Path | str = SCRIPTS_DIR) -> Planner:
    """Planner answering from `<directory>/<label>.yaml` (task id -> {plan, cost})."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label) or label.startswith("."):
        raise ValueError(f"invalid label '{label}'")
    path = Path(directory) / f"{label}.yaml"
    if not path.is_file():
        raise ValueError(f"no script for label '{label}' ({path})")
    script = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(script, dict):
        raise ValueError(f"{path.name}: expected a mapping of task id to answer")

    def planner(task: Task) -> dict:
        entry = script.get(task.id)
        if not isinstance(entry, dict) or not isinstance(entry.get("plan"), dict):
            raise ValueError(f"script '{label}' has no answer for task '{task.id}'")
        return {**entry["plan"], "_cost": entry.get("cost")}

    return planner


def live_planner() -> Planner:
    """Planner backed by the real model; refused unless TRADVIA_EVALS_LIVE=1."""
    if os.getenv(LIVE_ENV) != "1":
        raise RuntimeError(f"live evals are manual and need {LIVE_ENV}=1 (and an API key)")
    if os.getenv("TRADVIA_OFFLINE") == "1":
        raise RuntimeError("live evals cannot run with TRADVIA_OFFLINE=1 (it stubs the model)")
    from ..llm import architect_plan  # litellm itself is imported lazily inside

    return lambda task: architect_plan(task.title, task.body, 0)


def make_planner(kind: str, label: str) -> Planner:
    if kind == "scripted":
        return scripted_planner(label)
    if kind == "live":
        return live_planner()
    raise ValueError(f"unknown planner '{kind}' (use scripted or live)")


def run_suite(tasks: list[Task], planner: Planner, label: str) -> dict:
    """Run every task; return a JSON-serializable report: label, per-task results and totals."""
    results: list[Result] = [run_task(t, planner) for t in tasks]
    cost = [r.cost for r in results]
    return {
        "label": label,
        "results": [r.as_dict() for r in results],
        "totals": {
            "tasks": len(results),
            "passed": sum(r.passed for r in results),
            "failed": sum(not r.passed for r in results),
            "steps": sum(r.steps for r in results),
            "usd": round(sum(c["usd"] for c in cost), 6),
            "tokens_in": sum(c["tokens_in"] for c in cost),
            "tokens_out": sum(c["tokens_out"] for c in cost),
            "tokens_cache": sum(c["tokens_cache"] for c in cost),
        },
    }
