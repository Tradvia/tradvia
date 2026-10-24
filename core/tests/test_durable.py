# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Durable execution (ADR 0006): resume, fork, project isolation, no database offline."""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from tradvia.checkpoints import fork, list_checkpoints, make_checkpointer, thread_config, thread_id
from tradvia.graph import build_graph
from tradvia.llm import _offline_plan

from test_hello_world import FakeChat, FakeGitHub

REPO = "Tradvia/tradvia"


def counting_planner():
    calls = []

    def planner(title, body, number):
        calls.append(number)
        return _offline_plan(title, body, number)

    return planner, calls


def start(app, project_id=1, issue=1):
    config = thread_config(project_id, REPO, issue)
    result = app.invoke({"id": f"CARD-{issue}", "status": "BACKLOG",
                         "source": {"provider": "github", "issue_number": issue}}, config)
    return result, config


def test_thread_id_is_stable_and_scoped_by_project():
    assert thread_id(1, REPO, 40) == "1:Tradvia/tradvia#40"
    assert thread_id(1, REPO, 40) != thread_id(2, REPO, 40)


def test_resume_does_not_replan():
    saver, planner, calls = MemorySaver(), *counting_planner()
    result, config = start(build_graph(FakeGitHub(), FakeChat(), planner=planner,
                                       checkpointer=saver))
    assert result["__interrupt__"] and calls == [1]

    # "Crash": a brand new app on the same saver and thread picks up the checkpoint
    gh = FakeGitHub()
    app = build_graph(gh, FakeChat(), planner=planner, checkpointer=saver)
    snap = app.get_state(config)
    assert snap.next and [i for t in snap.tasks for i in t.interrupts]

    result = app.invoke(Command(resume={"text": "Aprovado", "by": "dev"}), config)
    assert result["status"] == "IN_REVIEW_PR"
    assert calls == [1]
    assert gh.branches == ["tradvia/issue-1"]


def test_fork_creates_new_thread_and_keeps_original():
    saver, planner, _ = MemorySaver(), *counting_planner()
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, checkpointer=saver)
    _, config = start(app)
    before = list_checkpoints(app, config, 1)
    assert len(before) >= 3
    earlier = before[-2]  # after the first node, before planning

    out = fork(app, config, earlier["checkpoint_id"], 1)
    assert out["thread_id"] == f"{config['configurable']['thread_id']}~fork-1"

    forked = {"configurable": {"thread_id": out["thread_id"]}}
    assert app.get_state(forked).values["status"] == earlier["status"]
    assert app.get_state(forked).next
    assert list_checkpoints(app, config, 1) == before  # original untouched
    assert fork(app, config, earlier["checkpoint_id"], 1)["thread_id"].endswith("~fork-2")

    # The fork can run on its own
    result = app.invoke(None, forked)
    assert result["__interrupt__"]
    assert len(list_checkpoints(app, config, 1)) == len(before)


def test_fork_rejects_unknown_checkpoint_and_other_project():
    saver, planner, _ = MemorySaver(), *counting_planner()
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, checkpointer=saver)
    _, config = start(app, project_id=1)
    cid = list_checkpoints(app, config, 1)[0]["checkpoint_id"]
    with pytest.raises(ValueError):
        fork(app, config, "nope", 1)
    with pytest.raises(ValueError):
        fork(app, config, cid, 2)
    with pytest.raises(ValueError):
        list_checkpoints(app, config, 2)


def test_same_issue_in_two_projects_never_mixes():
    saver, planner, _ = MemorySaver(), *counting_planner()
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, checkpointer=saver)
    _, c1 = start(app, project_id=1)
    _, c2 = start(app, project_id=2)
    assert c1["configurable"]["thread_id"] != c2["configurable"]["thread_id"]
    ids1 = {r["checkpoint_id"] for r in list_checkpoints(app, c1, 1)}
    ids2 = {r["checkpoint_id"] for r in list_checkpoints(app, c2, 2)}
    assert ids1 and ids2 and not ids1 & ids2
    with pytest.raises(ValueError):
        fork(app, c1, next(iter(ids2)), 1)


def test_without_database_url_nothing_connects(monkeypatch):
    import psycopg
    import psycopg_pool

    def boom(*a, **k):
        raise AssertionError("tried to connect to a database")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(psycopg, "connect", boom)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", boom)
    assert isinstance(make_checkpointer(), MemorySaver)
    app = build_graph(FakeGitHub(), FakeChat(), planner=_offline_plan)
    assert isinstance(app.checkpointer, MemorySaver)
