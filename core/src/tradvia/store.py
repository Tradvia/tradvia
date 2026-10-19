# SPDX-License-Identifier: FSL-1.1-ALv2
"""Storage for GitHub data received by webhook (API slice 2).

`Store` is the small interface; `MemoryStore` serves tests and `PgStore` serves
production (psycopg 3, a pool of at most 3 connections). Every SQL statement is
a constant with `%s`/`%(name)s` parameters: never build SQL from strings.
"""

from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from importlib import resources
from typing import Any, Iterator, Protocol

Op = tuple[str, dict[str, Any]]

KINDS = ("issue", "comment", "pull_request", "workflow_run")
UPSERT_METHODS = {
    "issue": "upsert_issue",
    "comment": "upsert_comment",
    "pull_request": "upsert_pull_request",
    "workflow_run": "upsert_workflow_run",
}
_TABLES = {
    "issue": "issues",
    "comment": "comments",
    "pull_request": "pull_requests",
    "workflow_run": "workflow_runs",
}
MAX_POOL = 3
CONNECT_TIMEOUT = 3


class StoreUnavailable(Exception):
    """The database cannot be reached right now."""


class Store(Protocol):
    def apply(self, delivery_id: str, event: str, ops: list[Op]) -> bool:
        """Record the delivery and all upserts in ONE transaction.

        Returns False (writing nothing) if the delivery was already recorded.
        If any upsert raises, nothing is written, not even the delivery id.
        """

    def upsert_issue(self, rec: dict[str, Any]) -> None: ...
    def upsert_comment(self, rec: dict[str, Any]) -> None: ...
    def upsert_pull_request(self, rec: dict[str, Any]) -> None: ...
    def upsert_workflow_run(self, rec: dict[str, Any]) -> None: ...
    def stats(self) -> dict[str, Any]:
        """`{"issues": n, "runs": n, "last_delivery": iso | None}`."""


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.deliveries: dict[str, dict[str, Any]] = {}
        self.tables: dict[str, dict[Any, dict[str, Any]]] = {t: {} for t in _TABLES.values()}
        self._clock = 0

    def _upsert(self, kind: str, rec: dict[str, Any]) -> None:
        table = self.tables[_TABLES[kind]]
        old = table.get(rec["id"])
        if old is None or rec["updated_at"] >= old["updated_at"]:
            table[rec["id"]] = copy.deepcopy(rec)

    def apply(self, delivery_id: str, event: str, ops: list[Op]) -> bool:
        with self._lock:
            if delivery_id in self.deliveries:
                return False
            backup = (copy.deepcopy(self.deliveries), copy.deepcopy(self.tables))
            try:
                self._clock += 1
                self.deliveries[delivery_id] = {"event": event, "seq": self._clock}
                for kind, rec in ops:
                    self._upsert(kind, rec)
            except BaseException:
                self.deliveries, self.tables = backup
                raise
            return True

    def upsert_issue(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert("issue", rec)

    def upsert_comment(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert("comment", rec)

    def upsert_pull_request(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert("pull_request", rec)

    def upsert_workflow_run(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert("workflow_run", rec)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            last = max(self.deliveries, key=lambda d: self.deliveries[d]["seq"], default=None)
            return {
                "issues": len(self.tables["issues"]),
                "runs": len(self.tables["workflow_runs"]),
                "last_delivery": last,
            }


_INSERT_DELIVERY = (
    "INSERT INTO deliveries (id, event) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING"
)

_UPSERT_SQL = {
    "issue": """
        INSERT INTO issues (id, number, title, state, labels, author,
                            created_at, updated_at, closed_at)
        VALUES (%(id)s, %(number)s, %(title)s, %(state)s, %(labels)s, %(author)s,
                %(created_at)s, %(updated_at)s, %(closed_at)s)
        ON CONFLICT (id) DO UPDATE SET
            number = excluded.number, title = excluded.title, state = excluded.state,
            labels = excluded.labels, author = excluded.author,
            created_at = excluded.created_at, updated_at = excluded.updated_at,
            closed_at = excluded.closed_at
        WHERE excluded.updated_at > issues.updated_at
    """,
    "comment": """
        INSERT INTO comments (id, issue_number, author, author_association, body,
                              created_at, updated_at)
        VALUES (%(id)s, %(issue_number)s, %(author)s, %(author_association)s, %(body)s,
                %(created_at)s, %(updated_at)s)
        ON CONFLICT (id) DO UPDATE SET
            issue_number = excluded.issue_number, author = excluded.author,
            author_association = excluded.author_association, body = excluded.body,
            created_at = excluded.created_at, updated_at = excluded.updated_at
        WHERE excluded.updated_at > comments.updated_at
    """,
    "pull_request": """
        INSERT INTO pull_requests (id, number, title, state, merged, branch, author,
                                   created_at, updated_at, merged_at)
        VALUES (%(id)s, %(number)s, %(title)s, %(state)s, %(merged)s, %(branch)s,
                %(author)s, %(created_at)s, %(updated_at)s, %(merged_at)s)
        ON CONFLICT (id) DO UPDATE SET
            number = excluded.number, title = excluded.title, state = excluded.state,
            merged = excluded.merged, branch = excluded.branch, author = excluded.author,
            created_at = excluded.created_at, updated_at = excluded.updated_at,
            merged_at = excluded.merged_at
        WHERE excluded.updated_at > pull_requests.updated_at
    """,
    "workflow_run": """
        INSERT INTO workflow_runs (id, workflow, event, status, conclusion,
                                   issue_number, started_at, ended_at, updated_at)
        VALUES (%(id)s, %(workflow)s, %(event)s, %(status)s, %(conclusion)s,
                %(issue_number)s, %(started_at)s, %(ended_at)s, %(updated_at)s)
        ON CONFLICT (id) DO UPDATE SET
            workflow = excluded.workflow, event = excluded.event,
            status = excluded.status, conclusion = excluded.conclusion,
            issue_number = excluded.issue_number, started_at = excluded.started_at,
            ended_at = excluded.ended_at, updated_at = excluded.updated_at
        WHERE excluded.updated_at > workflow_runs.updated_at
    """,
}

_STATS_SQL = """
    SELECT (SELECT count(*) FROM issues),
           (SELECT count(*) FROM workflow_runs),
           (SELECT max(received_at) FROM deliveries)
"""


def load_schema() -> str:
    return resources.files("tradvia").joinpath("schema.sql").read_text(encoding="utf-8")


class PgStore:
    """PostgreSQL store. Nothing connects at construction: the pool opens on first use."""

    def __init__(self, dsn: str, max_size: int = MAX_POOL) -> None:
        self._dsn = dsn
        self._max_size = min(max_size, MAX_POOL)
        self._pool: Any = None
        self._lock = threading.Lock()

    def _get_pool(self) -> Any:
        with self._lock:
            if self._pool is not None:
                return self._pool
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                self._dsn,
                min_size=1,
                max_size=self._max_size,
                open=False,
                timeout=CONNECT_TIMEOUT,
                kwargs={"connect_timeout": CONNECT_TIMEOUT},
            )
            try:
                pool.open(wait=True, timeout=CONNECT_TIMEOUT)
                with pool.connection() as conn:
                    conn.execute(load_schema())
            except Exception:  # never echo: the message may carry the DSN
                pool.close()
                raise StoreUnavailable("database unavailable") from None
            self._pool = pool
            return pool

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        import psycopg
        from psycopg_pool import PoolClosed, PoolTimeout

        pool = self._get_pool()
        try:
            with pool.connection() as conn:
                yield conn
        except (psycopg.OperationalError, PoolTimeout, PoolClosed):
            raise StoreUnavailable("database unavailable") from None

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def apply(self, delivery_id: str, event: str, ops: list[Op]) -> bool:
        with self._conn() as conn:
            with conn.transaction():
                cur = conn.execute(_INSERT_DELIVERY, (delivery_id, event))
                if cur.rowcount == 0:
                    return False
                for kind, rec in ops:
                    conn.execute(_UPSERT_SQL[kind], rec)
        return True

    def _upsert(self, kind: str, rec: dict[str, Any]) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(_UPSERT_SQL[kind], rec)

    def upsert_issue(self, rec: dict[str, Any]) -> None:
        self._upsert("issue", rec)

    def upsert_comment(self, rec: dict[str, Any]) -> None:
        self._upsert("comment", rec)

    def upsert_pull_request(self, rec: dict[str, Any]) -> None:
        self._upsert("pull_request", rec)

    def upsert_workflow_run(self, rec: dict[str, Any]) -> None:
        self._upsert("workflow_run", rec)

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(_STATS_SQL).fetchone()
        last = row[2].isoformat() if row[2] else None
        return {"issues": row[0], "runs": row[1], "last_delivery": last}
