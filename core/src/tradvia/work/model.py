# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Neutral work model shared by every view and every connector (ADR 0002).

A WorkItem is a task, deliverable or milestone, done by a person or an agent.
Kanban, Gantt, Burndown, WBS, Timeline and Workload are projections of a list
of WorkItems; no view owns data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable, Literal


class Status(str, Enum):
    """Lifecycle of a work item (card-state contract v1, ADR 0001)."""

    BACKLOG = "BACKLOG"
    AGENT_WORKING = "AGENT_WORKING"
    WAITING_HUMAN = "WAITING_HUMAN"
    IN_REVIEW_PR = "IN_REVIEW_PR"
    DONE = "DONE"


class Category(str, Enum):
    """Tool-agnostic status category. Kanban columns and connectors use it."""

    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    REVIEW = "REVIEW"
    DONE = "DONE"


CATEGORY_OF: dict[Status, Category] = {
    Status.BACKLOG: Category.TODO,
    Status.AGENT_WORKING: Category.IN_PROGRESS,
    Status.WAITING_HUMAN: Category.WAITING,
    Status.IN_REVIEW_PR: Category.REVIEW,
    Status.DONE: Category.DONE,
}

AssigneeKind = Literal["human", "agent"]


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    status: Status = Status.BACKLOG
    parent_id: str | None = None          # WBS hierarchy
    assignee: str | None = None
    assignee_kind: AssigneeKind = "human"
    start: date | None = None
    due: date | None = None
    estimate_hours: float | None = None   # effort, used by workload and WBS rollup
    points: float | None = None           # story points, used by burndown
    sprint: str | None = None
    depends_on: tuple[str, ...] = ()      # finish-to-start dependencies
    milestone: bool = False
    completed_on: date | None = None
    external: dict = field(default_factory=dict, compare=False, hash=False)  # {"jira": "PROJ-12", ...}

    @property
    def category(self) -> Category:
        return CATEGORY_OF[self.status]

    @property
    def done(self) -> bool:
        return self.status is Status.DONE


class InvalidProject(ValueError):
    pass


def validate(items: Iterable[WorkItem]) -> dict[str, WorkItem]:
    """Check ids, references, dates and cycles. Returns the items by id."""
    by_id: dict[str, WorkItem] = {}
    for it in items:
        if it.id in by_id:
            raise InvalidProject(f"Duplicate id {it.id!r}")
        by_id[it.id] = it

    for it in by_id.values():
        if it.parent_id is not None and it.parent_id not in by_id:
            raise InvalidProject(f"{it.id}: unknown parent {it.parent_id!r}")
        for dep in it.depends_on:
            if dep not in by_id:
                raise InvalidProject(f"{it.id}: unknown dependency {dep!r}")
            if dep == it.id:
                raise InvalidProject(f"{it.id}: depends on itself")
        if it.start and it.due and it.start > it.due:
            raise InvalidProject(f"{it.id}: start {it.start} is after due {it.due}")
        if it.done and it.completed_on is None:
            raise InvalidProject(f"{it.id}: DONE items need completed_on")

    _assert_acyclic(by_id, lambda i: [i.parent_id] if i.parent_id else [], "hierarchy")
    _assert_acyclic(by_id, lambda i: list(i.depends_on), "dependencies")
    return by_id


def _assert_acyclic(by_id: dict[str, WorkItem], edges, what: str) -> None:
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in by_id}

    def visit(k: str, path: list[str]) -> None:
        color[k] = GREY
        for nxt in edges(by_id[k]):
            if color[nxt] == GREY:
                cycle = path[path.index(nxt):] + [nxt] if nxt in path else [k, nxt]
                raise InvalidProject(f"Cycle in {what}: {' -> '.join(cycle)}")
            if color[nxt] == WHITE:
                visit(nxt, path + [nxt])
        color[k] = BLACK

    for k in by_id:
        if color[k] == WHITE:
            visit(k, [k])
