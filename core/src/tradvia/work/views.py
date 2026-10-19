# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Progress notations as pure projections of the work model.

Each function takes a list of WorkItem and returns plain data (dicts and
lists) that a UI, a report or a connector can render. Nothing here draws.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Sequence

from .model import Category, WorkItem, validate

# ---------------------------------------------------------------- helpers


def working_days(start: date, end: date) -> list[date]:
    """Mon–Fri dates from start to end, inclusive."""
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _leaves(by_id: dict[str, WorkItem]) -> list[WorkItem]:
    parents = {i.parent_id for i in by_id.values() if i.parent_id}
    return [i for i in by_id.values() if i.id not in parents]


# ---------------------------------------------------------------- Kanban

DEFAULT_COLUMNS: tuple[Category, ...] = (
    Category.TODO, Category.IN_PROGRESS, Category.WAITING, Category.REVIEW, Category.DONE,
)


def kanban(items: Iterable[WorkItem], columns: Sequence[Category] = DEFAULT_COLUMNS) -> list[dict]:
    """Leaf items grouped by status category, in column order."""
    leaves = _leaves(validate(items))
    return [
        {"column": col.value,
         "items": [i.id for i in sorted(leaves, key=lambda x: (x.due or date.max, x.id))
                   if i.category is col]}
        for col in columns
    ]


# ---------------------------------------------------------------- Gantt


def gantt(items: Iterable[WorkItem]) -> list[dict]:
    """Rows with dates, duration, dependencies and finish-to-start conflicts.

    Rows are in dependency order (a dependency always comes before the items
    that need it), then by start date. A conflict means the item is scheduled
    to start before one of its dependencies is due.
    """
    by_id = validate(items)
    scheduled = {k: v for k, v in by_id.items() if v.start or v.due}

    order: list[str] = []
    seen: set[str] = set()

    def place(k: str) -> None:
        if k in seen:
            return
        seen.add(k)
        for dep in by_id[k].depends_on:
            if dep in scheduled:
                place(dep)
        order.append(k)

    for k in sorted(scheduled, key=lambda k: (scheduled[k].start or scheduled[k].due, k)):
        place(k)

    rows = []
    for k in order:
        it = by_id[k]
        start = it.start or it.due
        end = it.due or it.start
        conflicts = [d for d in it.depends_on
                     if by_id[d].due and start and by_id[d].due >= start and not it.milestone]
        conflicts += [d for d in it.depends_on
                      if it.milestone and by_id[d].due and start and by_id[d].due > start]
        rows.append({
            "id": it.id, "title": it.title, "start": start, "end": end,
            "duration_days": (end - start).days + 1 if start and end else None,
            "milestone": it.milestone, "depends_on": list(it.depends_on),
            "conflicts": sorted(set(conflicts)), "done": it.done, "assignee": it.assignee,
        })
    return rows


# ---------------------------------------------------------------- Burndown


def burndown(items: Iterable[WorkItem], sprint: str, start: date, end: date,
             today: date | None = None) -> list[dict]:
    """Ideal vs remaining scope for one sprint, per working day.

    Scope is story points when every item in the sprint has them, otherwise
    estimated hours, otherwise item count. `remaining` is None after `today`.
    """
    by_id = validate(items)
    sprint_items = [i for i in _leaves(by_id) if i.sprint == sprint]
    if not sprint_items:
        raise ValueError(f"No items in sprint {sprint!r}")

    if all(i.points is not None for i in sprint_items):
        size, unit = (lambda i: i.points), "points"
    elif all(i.estimate_hours is not None for i in sprint_items):
        size, unit = (lambda i: i.estimate_hours), "hours"
    else:
        size, unit = (lambda i: 1.0), "items"

    total = sum(size(i) for i in sprint_items)
    days = working_days(start, end)
    if not days:
        raise ValueError("Sprint has no working days")
    step = total / (len(days) - 1) if len(days) > 1 else total

    out = []
    for n, d in enumerate(days):
        burned = sum(size(i) for i in sprint_items if i.completed_on and i.completed_on <= d)
        out.append({
            "day": d, "unit": unit,
            "ideal": round(max(total - step * n, 0.0), 2),
            "remaining": None if today and d > today else round(total - burned, 2),
        })
    return out


# ---------------------------------------------------------------- WBS / EAP


def wbs(items: Iterable[WorkItem]) -> list[dict]:
    """Hierarchical breakdown with WBS codes (1, 1.1, 1.1.2…) and rolled-up progress.

    Progress of a parent is the share of its leaf effort that is done,
    weighted by estimate_hours (1 per leaf when there is no estimate).
    """
    by_id = validate(items)
    children: dict[str | None, list[WorkItem]] = {}
    for it in by_id.values():
        children.setdefault(it.parent_id, []).append(it)
    for lst in children.values():
        lst.sort(key=lambda i: (i.start or date.max, i.id))

    def node(it: WorkItem, code: str) -> dict:
        kids = [node(c, f"{code}.{n}") for n, c in enumerate(children.get(it.id, []), 1)]
        if kids:
            effort = sum(k["effort"] for k in kids)
            done_effort = sum(k["effort"] * k["progress"] for k in kids)
        else:
            effort = it.estimate_hours if it.estimate_hours is not None else 1.0
            done_effort = effort if it.done else 0.0
        return {"code": code, "id": it.id, "title": it.title, "effort": effort,
                "progress": round(done_effort / effort, 4) if effort else 0.0, "children": kids}

    return [node(it, str(n)) for n, it in enumerate(children.get(None, []), 1)]


# ---------------------------------------------------------------- Calendar / Timeline


def timeline(items: Iterable[WorkItem], start: date | None = None, end: date | None = None) -> list[dict]:
    """Due dates and milestones grouped by day, oldest first."""
    by_id = validate(items)
    days: dict[date, list[dict]] = {}
    for it in by_id.values():
        if not it.due:
            continue
        if (start and it.due < start) or (end and it.due > end):
            continue
        days.setdefault(it.due, []).append(
            {"id": it.id, "title": it.title, "milestone": it.milestone, "done": it.done})
    return [{"date": d, "items": sorted(v, key=lambda x: (not x["milestone"], x["id"]))}
            for d, v in sorted(days.items())]


# ---------------------------------------------------------------- Workload / Capacity


def workload(items: Iterable[WorkItem], window_start: date, window_end: date,
             capacity_hours_per_day: dict[str, float], default_capacity: float = 8.0) -> list[dict]:
    """Allocated vs available hours per assignee (people and agents) in a window.

    An open item's estimate is spread evenly over its working days; only the
    days inside the window count. Done items and items without dates or
    estimates are ignored.
    """
    by_id = validate(items)
    window = set(working_days(window_start, window_end))
    load: dict[str, float] = {}
    kind: dict[str, str] = {}

    for it in _leaves(by_id):
        if it.done or not it.assignee or it.estimate_hours is None:
            continue
        s, e = it.start or it.due, it.due or it.start
        if not s or not e:
            continue
        span = working_days(s, e) or [s]
        per_day = it.estimate_hours / len(span)
        load[it.assignee] = load.get(it.assignee, 0.0) + per_day * len([d for d in span if d in window])
        kind[it.assignee] = it.assignee_kind

    people = sorted(set(load) | set(capacity_hours_per_day))
    rows = []
    for p in people:
        capacity = capacity_hours_per_day.get(p, default_capacity) * len(window)
        allocated = round(load.get(p, 0.0), 2)
        util = round(allocated / capacity, 4) if capacity else None
        rows.append({"assignee": p, "kind": kind.get(p, "human"), "allocated_hours": allocated,
                     "capacity_hours": capacity, "utilization": util,
                     "overloaded": bool(util is not None and util > 1.0)})
    return rows
