# SPDX-License-Identifier: FSL-1.1-ALv2
"""Storage for GitHub data received by webhook (API slice 2).

`Store` is the small interface; `MemoryStore` serves tests and `PgStore` serves
production (psycopg 3, a pool of at most 3 connections). Every SQL statement is
a constant with `%s`/`%(name)s` parameters: never build SQL from strings.

The project is the unit of scope (ADR 0006, phase 0): every method takes the
`project_id` first and every query on a stored table filters by it.
"""

from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Callable, Iterator, Protocol

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
    """Every method takes the project id first: nothing is stored or read without one."""

    def ensure_project(self, external_id: str) -> int:
        """Id of the `github_repo` project `owner/repo`, created if missing."""

    def list_projects(self) -> list[dict[str, Any]]:
        """`[{id, slug, kind, external_id}]` of every known project."""

    def apply(self, project_id: int, delivery_id: str, event: str, ops: list[Op]) -> bool:
        """Record the delivery and all upserts in ONE transaction.

        Returns False (writing nothing) if the delivery was already recorded.
        If any upsert raises, nothing is written, not even the delivery id.
        """

    def upsert_issue(self, project_id: int, rec: dict[str, Any]) -> None: ...
    def upsert_comment(self, project_id: int, rec: dict[str, Any]) -> None: ...
    def upsert_pull_request(self, project_id: int, rec: dict[str, Any]) -> None: ...
    def upsert_workflow_run(self, project_id: int, rec: dict[str, Any]) -> None: ...
    def stats(self, project_id: int) -> dict[str, Any]:
        """`{"issues": n, "runs": n, "pull_requests": n, "last_delivery": iso | None}`."""

    def project_stats(self) -> dict[str, dict[str, Any]]:
        """`stats` of every project, keyed by `external_id`."""

    # Reads for the dashboard: newest first, timestamps as ISO strings. None means
    # the project has no rows in the table (data not loaded yet); [] means the filter
    # matched nothing. Comments are never read.
    def list_issues(
        self, project_id: int, labels: list[str], state: str, limit: int
    ) -> list[dict[str, Any]] | None: ...
    def list_pulls(
        self, project_id: int, state: str, limit: int
    ) -> list[dict[str, Any]] | None: ...
    def list_runs(
        self, project_id: int, workflow: str, limit: int
    ) -> list[dict[str, Any]] | None: ...

    def list_backlog(self, project_id: int) -> list[dict[str, Any]]:
        """`[{issue_number, position, depends_on, note}]` ordered by position."""

    def set_backlog(self, project_id: int, items: list[dict[str, Any]]) -> None:
        """Replace the project's whole backlog in one transaction; position is the list index."""

    # Sign in (auth.py). Not project scoped. Times are epoch seconds.
    def put_login_state(
        self, state: str, code_verifier: str, return_path: str, expires_at: float, now: float
    ) -> None:
        """Remember a pending login; also drops expired states and sessions."""

    def take_login_state(self, state: str, now: float) -> dict[str, Any] | None:
        """`{code_verifier, return_path}` once: the state is deleted. None if unknown/expired."""

    def create_session(self, rec: dict[str, Any]) -> None:
        """`rec`: id, user_id, login, avatar_url, token_enc, created_at, expires_at,
        absolute_expires_at, last_used_at, role_cache."""

    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    def update_session(
        self, session_id: str, expires_at: float, last_used_at: float,
        role_cache: dict[str, Any] | None = None,
    ) -> None:
        """Renew a session; `role_cache` replaces the cached roles when given."""

    def delete_session(self, session_id: str) -> None: ...


def project_slug(external_id: str) -> str:
    return external_id.replace("/", "-").lower()


class MemoryStore:
    """Tables and deliveries are keyed by `(project_id, id)`, like the Postgres primary keys."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.projects: dict[str, dict[str, Any]] = {}
        self.deliveries: dict[tuple[int, str], dict[str, Any]] = {}
        self.tables: dict[str, dict[tuple[int, Any], dict[str, Any]]] = {
            t: {} for t in _TABLES.values()
        }
        self.backlogs: dict[int, list[dict[str, Any]]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.login_states: dict[str, dict[str, Any]] = {}
        self._clock = 0

    def ensure_project(self, external_id: str) -> int:
        with self._lock:
            row = self.projects.get(external_id)
            if row is None:
                row = {
                    "id": len(self.projects) + 1,
                    "slug": project_slug(external_id),
                    "kind": "github_repo",
                    "external_id": external_id,
                }
                self.projects[external_id] = row
            return row["id"]

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self.projects.values()]

    def _upsert(self, project_id: int, kind: str, rec: dict[str, Any]) -> None:
        table = self.tables[_TABLES[kind]]
        key = (project_id, rec["id"])
        old = table.get(key)
        if old is None or rec["updated_at"] >= old["updated_at"]:
            table[key] = copy.deepcopy(rec)

    def apply(self, project_id: int, delivery_id: str, event: str, ops: list[Op]) -> bool:
        with self._lock:
            if (project_id, delivery_id) in self.deliveries:
                return False
            backup = (copy.deepcopy(self.deliveries), copy.deepcopy(self.tables))
            try:
                self._clock += 1
                self.deliveries[(project_id, delivery_id)] = {"event": event, "seq": self._clock}
                for kind, rec in ops:
                    self._upsert(project_id, kind, rec)
            except BaseException:
                self.deliveries, self.tables = backup
                raise
            return True

    def upsert_issue(self, project_id: int, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert(project_id, "issue", rec)

    def upsert_comment(self, project_id: int, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert(project_id, "comment", rec)

    def upsert_pull_request(self, project_id: int, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert(project_id, "pull_request", rec)

    def upsert_workflow_run(self, project_id: int, rec: dict[str, Any]) -> None:
        with self._lock:
            self._upsert(project_id, "workflow_run", rec)

    def _count(self, project_id: int, table: str) -> int:
        return sum(1 for pid, _ in self.tables[table] if pid == project_id)

    def _stats(self, project_id: int) -> dict[str, Any]:
        mine = [k for k in self.deliveries if k[0] == project_id]
        last = max(mine, key=lambda k: self.deliveries[k]["seq"], default=None)
        return {
            "issues": self._count(project_id, "issues"),
            "runs": self._count(project_id, "workflow_runs"),
            "pull_requests": self._count(project_id, "pull_requests"),
            "last_delivery": last[1] if last else None,
        }

    def stats(self, project_id: int) -> dict[str, Any]:
        with self._lock:
            return self._stats(project_id)

    def project_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {ext: self._stats(p["id"]) for ext, p in self.projects.items()}

    def _list(
        self,
        project_id: int,
        table: str,
        keep: Callable[[dict[str, Any]], bool],
        sort_key: str,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        with self._lock:
            rows = [r for (pid, _), r in self.tables[table].items() if pid == project_id]
            if not rows:
                return None
            rows = [copy.deepcopy(r) for r in rows if keep(r)]
        rows.sort(key=lambda r: r.get(sort_key) or "", reverse=True)
        return rows[:limit]

    def list_issues(
        self, project_id: int, labels: list[str], state: str, limit: int
    ) -> list[dict[str, Any]] | None:
        return self._list(
            project_id,
            "issues",
            lambda r: set(labels) <= set(r["labels"]) and state in ("all", r["state"]),
            "created_at",
            limit,
        )

    def list_pulls(
        self, project_id: int, state: str, limit: int
    ) -> list[dict[str, Any]] | None:
        return self._list(
            project_id,
            "pull_requests",
            lambda r: state in ("all", r["state"]),
            "created_at",
            limit,
        )

    def list_runs(
        self, project_id: int, workflow: str, limit: int
    ) -> list[dict[str, Any]] | None:
        return self._list(
            project_id,
            "workflow_runs",
            lambda r: r["workflow"].rsplit("/", 1)[-1] == workflow,
            "started_at",
            limit,
        )

    def list_backlog(self, project_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.backlogs.get(project_id, []))

    def set_backlog(self, project_id: int, items: list[dict[str, Any]]) -> None:
        rows = [
            {
                "issue_number": it["issue_number"],
                "position": i,
                "depends_on": list(it.get("depends_on", [])),
                "note": it.get("note", ""),
            }
            for i, it in enumerate(items)
        ]
        with self._lock:
            self.backlogs[project_id] = rows

    def put_login_state(
        self, state: str, code_verifier: str, return_path: str, expires_at: float, now: float
    ) -> None:
        with self._lock:
            self.login_states = {k: v for k, v in self.login_states.items() if v["expires_at"] > now}
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires_at"] > now}
            self.login_states[state] = {
                "code_verifier": code_verifier, "return_path": return_path, "expires_at": expires_at,
            }

    def take_login_state(self, state: str, now: float) -> dict[str, Any] | None:
        with self._lock:
            row = self.login_states.pop(state, None)
        if row is None or row["expires_at"] <= now:
            return None
        return {"code_verifier": row["code_verifier"], "return_path": row["return_path"]}

    def create_session(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self.sessions[rec["id"]] = copy.deepcopy(rec)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self.sessions.get(session_id)
            return copy.deepcopy(rec) if rec else None

    def update_session(
        self, session_id: str, expires_at: float, last_used_at: float,
        role_cache: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            rec = self.sessions.get(session_id)
            if rec is not None:
                rec["expires_at"], rec["last_used_at"] = expires_at, last_used_at
                if role_cache is not None:
                    rec["role_cache"] = copy.deepcopy(role_cache)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)


_INSERT_DELIVERY = (
    "INSERT INTO deliveries (project_id, id, event) VALUES (%s, %s, %s) "
    "ON CONFLICT (project_id, id) DO NOTHING"
)

_UPSERT_SQL = {
    "issue": """
        INSERT INTO issues (project_id, id, number, title, state, labels, author,
                            created_at, updated_at, closed_at, html_url, state_reason)
        VALUES (%(project_id)s, %(id)s, %(number)s, %(title)s, %(state)s, %(labels)s,
                %(author)s, %(created_at)s, %(updated_at)s, %(closed_at)s, %(html_url)s,
                %(state_reason)s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            number = excluded.number, title = excluded.title, state = excluded.state,
            labels = excluded.labels, author = excluded.author,
            created_at = excluded.created_at, updated_at = excluded.updated_at,
            closed_at = excluded.closed_at, html_url = excluded.html_url,
            state_reason = excluded.state_reason
        WHERE excluded.updated_at >= issues.updated_at
    """,
    "comment": """
        INSERT INTO comments (project_id, id, issue_number, author, author_association,
                              body, created_at, updated_at)
        VALUES (%(project_id)s, %(id)s, %(issue_number)s, %(author)s,
                %(author_association)s, %(body)s, %(created_at)s, %(updated_at)s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            issue_number = excluded.issue_number, author = excluded.author,
            author_association = excluded.author_association, body = excluded.body,
            created_at = excluded.created_at, updated_at = excluded.updated_at
        WHERE excluded.updated_at >= comments.updated_at
    """,
    "pull_request": """
        INSERT INTO pull_requests (project_id, id, number, title, state, merged, branch,
                                   author, created_at, updated_at, merged_at, html_url)
        VALUES (%(project_id)s, %(id)s, %(number)s, %(title)s, %(state)s, %(merged)s,
                %(branch)s, %(author)s, %(created_at)s, %(updated_at)s, %(merged_at)s,
                %(html_url)s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            number = excluded.number, title = excluded.title, state = excluded.state,
            merged = excluded.merged, branch = excluded.branch, author = excluded.author,
            created_at = excluded.created_at, updated_at = excluded.updated_at,
            merged_at = excluded.merged_at, html_url = excluded.html_url
        WHERE excluded.updated_at >= pull_requests.updated_at
    """,
    "workflow_run": """
        INSERT INTO workflow_runs (project_id, id, workflow, event, status, conclusion,
                                   issue_number, started_at, ended_at, updated_at,
                                   name, display_title, html_url, run_attempt)
        VALUES (%(project_id)s, %(id)s, %(workflow)s, %(event)s, %(status)s,
                %(conclusion)s, %(issue_number)s, %(started_at)s, %(ended_at)s,
                %(updated_at)s, %(name)s, %(display_title)s, %(html_url)s, %(run_attempt)s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            workflow = excluded.workflow, event = excluded.event,
            status = excluded.status, conclusion = excluded.conclusion,
            issue_number = excluded.issue_number, started_at = excluded.started_at,
            ended_at = excluded.ended_at, updated_at = excluded.updated_at,
            name = excluded.name, display_title = excluded.display_title,
            html_url = excluded.html_url, run_attempt = excluded.run_attempt
        WHERE excluded.updated_at >= workflow_runs.updated_at
    """,
}

_STATS_SQL = """
    SELECT (SELECT count(*) FROM issues WHERE project_id = %(project_id)s),
           (SELECT count(*) FROM workflow_runs WHERE project_id = %(project_id)s),
           (SELECT count(*) FROM pull_requests WHERE project_id = %(project_id)s),
           (SELECT max(received_at) FROM deliveries WHERE project_id = %(project_id)s)
"""
_PROJECT_STATS_SQL = """
    SELECT p.external_id,
           (SELECT count(*) FROM issues t WHERE t.project_id = p.id),
           (SELECT count(*) FROM workflow_runs t WHERE t.project_id = p.id),
           (SELECT count(*) FROM pull_requests t WHERE t.project_id = p.id),
           (SELECT max(received_at) FROM deliveries t WHERE t.project_id = p.id)
    FROM projects p
    ORDER BY p.external_id
"""
_ENSURE_PROJECT_SQL = """
    INSERT INTO projects (slug, kind, external_id)
    VALUES (%s, 'github_repo', %s)
    ON CONFLICT (external_id) DO UPDATE SET external_id = excluded.external_id
    RETURNING id
"""
_LIST_PROJECTS_SQL = "SELECT id, slug, kind, external_id FROM projects ORDER BY external_id"

_LIST_ISSUES_SQL = """
    SELECT number, title, state, state_reason, labels, author, html_url,
           created_at, updated_at, closed_at
    FROM issues
    WHERE project_id = %(project_id)s AND labels @> %(labels)s::text[]
          AND (%(state)s = 'all' OR state = %(state)s)
    ORDER BY created_at DESC NULLS LAST, number DESC LIMIT %(limit)s
"""
_LIST_PULLS_SQL = """
    SELECT number, title, state, merged_at, branch, author, html_url,
           created_at, updated_at
    FROM pull_requests
    WHERE project_id = %(project_id)s AND (%(state)s = 'all' OR state = %(state)s)
    ORDER BY created_at DESC NULLS LAST, number DESC LIMIT %(limit)s
"""
_LIST_RUNS_SQL = """
    SELECT id, name, display_title, event, status, conclusion, html_url, run_attempt,
           started_at, updated_at
    FROM workflow_runs
    WHERE project_id = %(project_id)s
          AND right(workflow, length(%(workflow)s) + 1) = '/' || %(workflow)s
    ORDER BY started_at DESC NULLS LAST, id DESC LIMIT %(limit)s
"""
_LIST_BACKLOG_SQL = """
    SELECT issue_number, position, depends_on, note
    FROM backlog_items WHERE project_id = %s ORDER BY position
"""
_DELETE_BACKLOG_SQL = "DELETE FROM backlog_items WHERE project_id = %s"
_INSERT_BACKLOG_SQL = """
    INSERT INTO backlog_items (project_id, issue_number, position, depends_on, note)
    VALUES (%s, %s, %s, %s, %s)
"""
_PURGE_LOGIN_STATES_SQL = "DELETE FROM login_states WHERE expires_at <= to_timestamp(%s)"
_PURGE_SESSIONS_SQL = "DELETE FROM sessions WHERE expires_at <= to_timestamp(%s)"
_PUT_LOGIN_STATE_SQL = """
    INSERT INTO login_states (state, code_verifier, return_path, expires_at)
    VALUES (%s, %s, %s, to_timestamp(%s))
"""
_TAKE_LOGIN_STATE_SQL = """
    DELETE FROM login_states WHERE state = %s AND expires_at > to_timestamp(%s)
    RETURNING code_verifier, return_path
"""
_CREATE_SESSION_SQL = """
    INSERT INTO sessions (id, user_id, login, avatar_url, token_enc, created_at, expires_at,
                          absolute_expires_at, last_used_at, role_cache)
    VALUES (%s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s), to_timestamp(%s),
            to_timestamp(%s), %s)
"""
_GET_SESSION_SQL = """
    SELECT id, user_id, login, avatar_url, token_enc,
           extract(epoch from created_at)::float8, extract(epoch from expires_at)::float8,
           extract(epoch from absolute_expires_at)::float8,
           extract(epoch from last_used_at)::float8, role_cache
    FROM sessions WHERE id = %s
"""
_UPDATE_SESSION_SQL = """
    UPDATE sessions SET expires_at = to_timestamp(%s), last_used_at = to_timestamp(%s)
    WHERE id = %s
"""
_UPDATE_SESSION_ROLES_SQL = """
    UPDATE sessions SET expires_at = to_timestamp(%s), last_used_at = to_timestamp(%s),
                        role_cache = %s
    WHERE id = %s
"""
_DELETE_SESSION_SQL = "DELETE FROM sessions WHERE id = %s"
_SESSION_FIELDS = (
    "id", "user_id", "login", "avatar_url", "token_enc",
    "created_at", "expires_at", "absolute_expires_at", "last_used_at", "role_cache",
)
_NOT_EMPTY_SQL = {
    "issues": "SELECT EXISTS (SELECT 1 FROM issues WHERE project_id = %(project_id)s)",
    "pull_requests": (
        "SELECT EXISTS (SELECT 1 FROM pull_requests WHERE project_id = %(project_id)s)"
    ),
    "workflow_runs": (
        "SELECT EXISTS (SELECT 1 FROM workflow_runs WHERE project_id = %(project_id)s)"
    ),
}


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


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

    def ensure_project(self, external_id: str) -> int:
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    _ENSURE_PROJECT_SQL, (project_slug(external_id), external_id)
                ).fetchone()
        return row[0]

    def list_projects(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(_LIST_PROJECTS_SQL).fetchall()
        return [{"id": r[0], "slug": r[1], "kind": r[2], "external_id": r[3]} for r in rows]

    def apply(self, project_id: int, delivery_id: str, event: str, ops: list[Op]) -> bool:
        with self._conn() as conn:
            with conn.transaction():
                cur = conn.execute(_INSERT_DELIVERY, (project_id, delivery_id, event))
                if cur.rowcount == 0:
                    return False
                for kind, rec in ops:
                    conn.execute(_UPSERT_SQL[kind], {**rec, "project_id": project_id})
        return True

    def _upsert(self, project_id: int, kind: str, rec: dict[str, Any]) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(_UPSERT_SQL[kind], {**rec, "project_id": project_id})

    def upsert_issue(self, project_id: int, rec: dict[str, Any]) -> None:
        self._upsert(project_id, "issue", rec)

    def upsert_comment(self, project_id: int, rec: dict[str, Any]) -> None:
        self._upsert(project_id, "comment", rec)

    def upsert_pull_request(self, project_id: int, rec: dict[str, Any]) -> None:
        self._upsert(project_id, "pull_request", rec)

    def upsert_workflow_run(self, project_id: int, rec: dict[str, Any]) -> None:
        self._upsert(project_id, "workflow_run", rec)

    @staticmethod
    def _stats_row(row: Any) -> dict[str, Any]:
        last = row[3].isoformat() if row[3] else None
        return {"issues": row[0], "runs": row[1], "pull_requests": row[2], "last_delivery": last}

    def stats(self, project_id: int) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(_STATS_SQL, {"project_id": project_id}).fetchone()
        return self._stats_row(row)

    def project_stats(self) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(_PROJECT_STATS_SQL).fetchall()
        return {r[0]: self._stats_row(r[1:]) for r in rows}

    def _query(
        self, project_id: int, table: str, sql: str, params: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        params = {**params, "project_id": project_id}
        with self._conn() as conn:
            if not conn.execute(_NOT_EMPTY_SQL[table], params).fetchone()[0]:
                return None
            cur = conn.execute(sql, params)
            names = [c.name for c in cur.description]
            rows = cur.fetchall()
        return [{n: _iso(v) for n, v in zip(names, row)} for row in rows]

    def list_issues(
        self, project_id: int, labels: list[str], state: str, limit: int
    ) -> list[dict[str, Any]] | None:
        params = {"labels": labels, "state": state, "limit": limit}
        return self._query(project_id, "issues", _LIST_ISSUES_SQL, params)

    def list_pulls(
        self, project_id: int, state: str, limit: int
    ) -> list[dict[str, Any]] | None:
        params = {"state": state, "limit": limit}
        return self._query(project_id, "pull_requests", _LIST_PULLS_SQL, params)

    def list_runs(
        self, project_id: int, workflow: str, limit: int
    ) -> list[dict[str, Any]] | None:
        params = {"workflow": workflow, "limit": limit}
        return self._query(project_id, "workflow_runs", _LIST_RUNS_SQL, params)

    def list_backlog(self, project_id: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(_LIST_BACKLOG_SQL, (project_id,)).fetchall()
        return [
            {"issue_number": r[0], "position": r[1], "depends_on": list(r[2]), "note": r[3]}
            for r in rows
        ]

    def set_backlog(self, project_id: int, items: list[dict[str, Any]]) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(_DELETE_BACKLOG_SQL, (project_id,))
                for i, it in enumerate(items):
                    conn.execute(
                        _INSERT_BACKLOG_SQL,
                        (project_id, it["issue_number"], i,
                         list(it.get("depends_on", [])), it.get("note", "")),
                    )

    def put_login_state(
        self, state: str, code_verifier: str, return_path: str, expires_at: float, now: float
    ) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(_PURGE_LOGIN_STATES_SQL, (now,))
                conn.execute(_PURGE_SESSIONS_SQL, (now,))
                conn.execute(_PUT_LOGIN_STATE_SQL, (state, code_verifier, return_path, expires_at))

    def take_login_state(self, state: str, now: float) -> dict[str, Any] | None:
        with self._conn() as conn:
            with conn.transaction():
                row = conn.execute(_TAKE_LOGIN_STATE_SQL, (state, now)).fetchone()
        return {"code_verifier": row[0], "return_path": row[1]} if row else None

    def create_session(self, rec: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with conn.transaction():
                conn.execute(
                    _CREATE_SESSION_SQL,
                    (rec["id"], rec["user_id"], rec["login"], rec["avatar_url"],
                     rec["token_enc"], rec["created_at"], rec["expires_at"],
                     rec["absolute_expires_at"], rec["last_used_at"], Jsonb(rec["role_cache"])),
                )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(_GET_SESSION_SQL, (session_id,)).fetchone()
        return dict(zip(_SESSION_FIELDS, row)) if row else None

    def update_session(
        self, session_id: str, expires_at: float, last_used_at: float,
        role_cache: dict[str, Any] | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with conn.transaction():
                if role_cache is None:
                    conn.execute(_UPDATE_SESSION_SQL, (expires_at, last_used_at, session_id))
                else:
                    conn.execute(
                        _UPDATE_SESSION_ROLES_SQL,
                        (expires_at, last_used_at, Jsonb(role_cache), session_id),
                    )

    def delete_session(self, session_id: str) -> None:
        with self._conn() as conn:
            with conn.transaction():
                conn.execute(_DELETE_SESSION_SQL, (session_id,))
