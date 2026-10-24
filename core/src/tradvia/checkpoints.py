# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Durable execution (ADR 0006): checkpointer selection, thread ids, history and fork.

With `DATABASE_URL` the graph checkpoints to Postgres (`langgraph-checkpoint-postgres`,
imported lazily); without it the in-memory saver is used and nothing connects anywhere.

Every thread id starts with the project id, and every checkpoint carries it in its
metadata: history and fork never cross projects.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

LOCAL_PROJECT_ID = 0  # project id when there is no database (offline, local runs)
FORK_SEP = "~fork-"


def make_checkpointer(database_url: str | None = None):
    """`PostgresSaver` when a database URL is given (or in DATABASE_URL), else `MemorySaver`."""
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL")
    if not url:
        return MemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(url, max_size=3, open=True, kwargs={
        "autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


def thread_id(project_id: int, repo: str, issue: int) -> str:
    """Stable thread of a card: the same card always resumes the same thread."""
    return f"{project_id}:{repo}#{issue}"


def thread_config(project_id: int, repo: str, issue: int) -> dict:
    """Run config: stable thread id, and the project id recorded in each checkpoint."""
    return {"configurable": {"thread_id": thread_id(project_id, repo, issue)},
            "metadata": {"project_id": project_id}}


def _thread_of(config: dict) -> str:
    return config["configurable"]["thread_id"]


def _check_project(config: dict, project_id: int | None) -> None:
    if project_id is not None and not _thread_of(config).startswith(f"{project_id}:"):
        raise ValueError(f"Thread {_thread_of(config)!r} does not belong to project {project_id}")


def _node(snapshot) -> str:
    if snapshot.next:
        return ", ".join(snapshot.next)
    writes = (snapshot.metadata or {}).get("writes") or {}
    return ", ".join(writes) or (snapshot.metadata or {}).get("source", "")


def list_checkpoints(app, config: dict, project_id: int | None = None) -> list[dict[str, Any]]:
    """Checkpoints of one thread, newest first: step, node, status, checkpoint id, date.

    Snapshots recorded under another project are skipped.
    """
    _check_project(config, project_id)
    rows = []
    for snap in app.get_state_history(config):
        meta = snap.metadata or {}
        if project_id is not None and meta.get("project_id", project_id) != project_id:
            continue
        rows.append({
            "step": meta.get("step"),
            "node": _node(snap),
            "status": (snap.values or {}).get("status"),
            "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
            "created_at": snap.created_at,
        })
    return rows


def fork(app, config: dict, checkpoint_id: str, project_id: int | None = None) -> dict:
    """Copy one checkpoint into a new thread `<thread>~fork-<n>` of the same project.

    The original thread is left untouched. Returns `{thread_id, checkpoint_id}`.
    """
    _check_project(config, project_id)
    source = _thread_of(config)
    cfg = {"configurable": {"thread_id": source, "checkpoint_ns": "",
                            "checkpoint_id": checkpoint_id}}
    saver = app.checkpointer
    found = saver.get_tuple(cfg)
    known = {r["checkpoint_id"] for r in list_checkpoints(app, config, project_id)}
    if found is None or checkpoint_id not in known:
        raise ValueError(f"Checkpoint {checkpoint_id!r} not found in thread {source!r}")

    n = 1
    while saver.get_tuple({"configurable": {"thread_id": f"{source}{FORK_SEP}{n}",
                                            "checkpoint_ns": ""}}) is not None:
        n += 1
    new_thread = f"{source}{FORK_SEP}{n}"
    checkpoint = dict(found.checkpoint)
    metadata = {**found.metadata, "parents": {}, "forked_from": f"{source}@{checkpoint_id}"}
    if project_id is not None:
        metadata["project_id"] = project_id
    saver.put({"configurable": {"thread_id": new_thread, "checkpoint_ns": ""}},
              checkpoint, metadata, checkpoint["channel_versions"])
    return {"thread_id": new_thread, "checkpoint_id": checkpoint_id}
