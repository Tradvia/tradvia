# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Evals harness (ADR 0012): golden tasks with an expected outcome, run through a planner.

A `Task` says what a good plan looks like (step range, words it must mention, paths it must not
target). `run_task` asks a planner for a plan, validates it with `claude_code.parse_plan` and
returns a `Result` with pass/fail, steps, cost and tokens. Planners are plain callables
`planner(task) -> plan dict` carrying `_cost`; see runner.py for the scripted and live ones.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from ..agents import Cost
from ..claude_code import parse_plan

TASKS_DIR = Path(__file__).parent / "tasks"


@dataclass(frozen=True)
class Expect:
    min_steps: int = 1
    max_steps: int = 10
    must_mention: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    body: str
    expect: Expect = field(default_factory=Expect)


@dataclass(frozen=True)
class Result:
    task_id: str
    passed: bool
    reasons: list[str]
    steps: int
    cost: dict
    duration_s: float

    def as_dict(self) -> dict:
        return asdict(self)


Planner = Callable[[Task], dict]


def _strings(value: object, name: str, tid: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
        raise ValueError(f"task {tid}: expect.{name} must be a list of non-empty strings")
    return tuple(value)


def parse_task(data: object) -> Task:
    """Validate one task document (a mapping loaded from YAML)."""
    if not isinstance(data, dict):
        raise ValueError("task must be a mapping")
    for key in ("id", "title", "body"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"task is missing '{key}'")
    tid = data["id"]
    raw = data.get("expect") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"task {tid}: 'expect' must be a mapping")
    lo, hi = raw.get("min_steps", 1), raw.get("max_steps", 10)
    for name, value in (("min_steps", lo), ("max_steps", hi)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"task {tid}: expect.{name} must be a non-negative integer")
    if lo > hi:
        raise ValueError(f"task {tid}: expect.min_steps is greater than max_steps")
    expect = Expect(lo, hi, _strings(raw.get("must_mention"), "must_mention", tid),
                    _strings(raw.get("forbidden_paths"), "forbidden_paths", tid))
    return Task(tid, data["title"], data["body"], expect)


def load_tasks(directory: Path | str = TASKS_DIR) -> list[Task]:
    """Load every `*.yaml` golden task in `directory`, sorted by id; ids must be unique."""
    tasks = []
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            tasks.append(parse_task(yaml.safe_load(path.read_text(encoding="utf-8"))))
        except (ValueError, yaml.YAMLError) as e:
            raise ValueError(f"{path.name}: {e}") from None
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids in " + str(directory))
    return sorted(tasks, key=lambda t: t.id)


def _check(task: Task, plan: dict) -> list[str]:
    exp, reasons = task.expect, []
    steps = len(plan["plan"])
    if steps < exp.min_steps:
        reasons.append(f"{steps} steps, expected at least {exp.min_steps}")
    if steps > exp.max_steps:
        reasons.append(f"{steps} steps, expected at most {exp.max_steps}")
    text = "\n".join(str(v) for v in (plan.get("summary"), plan.get("question"),
                                      *plan["plan"])).lower()
    for word in exp.must_mention:
        if word.lower() not in text:
            reasons.append(f"plan does not mention '{word}'")
    path = str(plan.get("file_path") or "").lstrip("./")
    for forbidden in exp.forbidden_paths:
        if path == forbidden or (forbidden.endswith("/") and path.startswith(forbidden)):
            reasons.append(f"plan targets forbidden path '{forbidden}'")
    return reasons


def _cost(plan: dict, duration_s: float) -> dict:
    raw = plan.get("_cost") if isinstance(plan.get("_cost"), dict) else {}
    return Cost(raw.get("usd"), raw.get("tokens_in"), raw.get("tokens_out"), raw.get("tokens_cache"),
                duration_s).as_dict()


def run_task(task: Task, planner: Planner) -> Result:
    """Run one golden task. A planner or validation error is a failed result, not an exception."""
    start = time.monotonic()
    try:
        plan = planner(task)
        cost_info = plan.get("_cost") if isinstance(plan, dict) else None
        plan = parse_plan(json.dumps({k: v for k, v in plan.items() if k != "_cost"}), "Eval")
        plan["_cost"] = cost_info
    except Exception as e:  # noqa: BLE001 - any planner failure is a failed task
        return Result(task.id, False, [f"planner error: {e}"], 0,
                      Cost(duration_s=time.monotonic() - start).as_dict(),
                      round(time.monotonic() - start, 3))
    duration = time.monotonic() - start
    reasons = _check(task, plan)
    return Result(task.id, not reasons, reasons, len(plan["plan"]), _cost(plan, duration),
                  round(duration, 3))
