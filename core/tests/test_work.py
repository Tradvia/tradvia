# SPDX-License-Identifier: FSL-1.1-ALv2
from datetime import date as D

import pytest

from tradvia.work import (
    CONNECTORS, Category, InMemoryConnector, InvalidProject, Status, StatusMap,
    TrackerConnector, WorkItem as W, burndown, gantt, kanban, timeline, validate, wbs, workload,
)

# Sprint 1: Mon 2026-10-19 .. Fri 2026-10-23
PROJECT = [
    W("P", "Release 1.0"),
    W("A", "API", parent_id="P"),
    W("A1", "Schema", parent_id="A", status=Status.DONE, assignee="ana", start=D(2026, 10, 19),
      due=D(2026, 10, 20), estimate_hours=8, points=3, sprint="S1", completed_on=D(2026, 10, 20)),
    W("A2", "Endpoints", parent_id="A", status=Status.AGENT_WORKING, assignee="coder-agent",
      assignee_kind="agent", start=D(2026, 10, 21), due=D(2026, 10, 23), estimate_hours=24, points=5,
      sprint="S1", depends_on=("A1",)),
    W("B", "Docs", parent_id="P", status=Status.WAITING_HUMAN, assignee="ana", start=D(2026, 10, 22),
      due=D(2026, 10, 23), estimate_hours=8, points=2, sprint="S1"),
    W("M", "Launch", parent_id="P", milestone=True, due=D(2026, 10, 23), depends_on=("A2", "B")),
]


def test_validate_rejects_bad_projects():
    with pytest.raises(InvalidProject, match="Duplicate"):
        validate([W("x", "a"), W("x", "b")])
    with pytest.raises(InvalidProject, match="unknown parent"):
        validate([W("x", "a", parent_id="nope")])
    with pytest.raises(InvalidProject, match="Cycle in dependencies"):
        validate([W("x", "a", depends_on=("y",)), W("y", "b", depends_on=("x",))])
    with pytest.raises(InvalidProject, match="Cycle in hierarchy"):
        validate([W("x", "a", parent_id="y"), W("y", "b", parent_id="x")])
    with pytest.raises(InvalidProject, match="after due"):
        validate([W("x", "a", start=D(2026, 1, 2), due=D(2026, 1, 1))])
    with pytest.raises(InvalidProject, match="completed_on"):
        validate([W("x", "a", status=Status.DONE)])


def test_kanban_groups_leaves_by_category():
    cols = {c["column"]: c["items"] for c in kanban(PROJECT)}
    assert cols == {"TODO": ["M"], "IN_PROGRESS": ["A2"], "WAITING": ["B"], "REVIEW": [], "DONE": ["A1"]}


def test_gantt_orders_by_dependency_and_flags_conflicts():
    rows = gantt(PROJECT)
    ids = [r["id"] for r in rows]
    assert ids.index("A1") < ids.index("A2") < ids.index("M")
    a2 = next(r for r in rows if r["id"] == "A2")
    assert a2["duration_days"] == 3 and a2["conflicts"] == []
    m = next(r for r in rows if r["id"] == "M")
    assert m["milestone"] and m["conflicts"] == []  # deps due on the milestone day: ok

    late = PROJECT[:-1] + [W("M", "Launch", parent_id="P", milestone=True, due=D(2026, 10, 22),
                             depends_on=("A2", "B"))]
    m = next(r for r in gantt(late) if r["id"] == "M")
    assert m["conflicts"] == ["A2", "B"]


def test_burndown_uses_points_and_stops_at_today():
    rows = burndown(PROJECT, "S1", D(2026, 10, 19), D(2026, 10, 23), today=D(2026, 10, 21))
    assert [r["unit"] for r in rows] == ["points"] * 5
    assert [r["ideal"] for r in rows] == [10, 7.5, 5, 2.5, 0]
    assert [r["remaining"] for r in rows] == [10, 7, 7, None, None]


def test_wbs_codes_and_rollup():
    tree = wbs(PROJECT)
    assert len(tree) == 1 and tree[0]["code"] == "1"
    codes = {c["id"]: c["code"] for c in tree[0]["children"]}
    assert codes["A"].startswith("1.") and len(tree[0]["children"]) == 3
    api = next(c for c in tree[0]["children"] if c["id"] == "A")
    assert api["effort"] == 32 and api["progress"] == 0.25
    assert [c["code"] for c in api["children"]] == [api["code"] + ".1", api["code"] + ".2"]


def test_timeline_groups_due_dates():
    days = timeline(PROJECT)
    assert [d["date"] for d in days] == [D(2026, 10, 20), D(2026, 10, 23)]
    assert [i["id"] for i in days[1]["items"]] == ["M", "A2", "B"]  # milestone first


def test_workload_people_and_agents():
    rows = {r["assignee"]: r for r in workload(
        PROJECT, D(2026, 10, 19), D(2026, 10, 23), {"ana": 6, "coder-agent": 6})}
    assert rows["ana"]["allocated_hours"] == 8 and rows["ana"]["capacity_hours"] == 30
    agent = rows["coder-agent"]
    assert agent["kind"] == "agent" and agent["allocated_hours"] == 24 and not agent["overloaded"]

    tight = {r["assignee"]: r for r in workload(PROJECT, D(2026, 10, 21), D(2026, 10, 21), {"coder-agent": 6})}
    assert tight["coder-agent"]["allocated_hours"] == 8 and tight["coder-agent"]["overloaded"]


def test_connectors_registry_and_status_map():
    assert [c.name for c in CONNECTORS] == [
        "Trello", "Jira", "ClickUp", "Monday.com", "Asana", "Wrike", "Smartsheet", "Artia",
        "Microsoft Planner", "KanbanFlow", "Airtable"]
    assert all(c.status == "planned" for c in CONNECTORS)
    sm = StatusMap({"To Do": Category.TODO, "Em andamento": Category.IN_PROGRESS, "Concluído": Category.DONE})
    assert sm.status("  em ANDAMENTO ") is Status.AGENT_WORKING
    assert sm.status("concluido") is Status.DONE
    assert sm.category("unknown") is Category.TODO


def test_in_memory_connector_round_trip():
    conn = InMemoryConnector({"p": PROJECT})
    assert isinstance(conn, TrackerConnector)
    conn.push_status("p", "B", Status.IN_REVIEW_PR)
    cols = {c["column"]: c["items"] for c in kanban(conn.pull("p"))}
    assert cols["REVIEW"] == ["B"]
    with pytest.raises(KeyError):
        conn.push_status("p", "nope", Status.DONE)
