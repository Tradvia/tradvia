# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Interface that every project-tool connector implements (ADR 0002).

A connector maps the tool's own items and statuses to WorkItem, so every
Tradvia view works on top of Jira, Trello, ClickUp and the others.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from .model import Category, Status, WorkItem

ConnectorStatus = Literal["planned", "in_progress", "available"]


@runtime_checkable
class TrackerConnector(Protocol):
    key: str

    def pull(self, project: str) -> list[WorkItem]:
        """Read the project's items and return them as WorkItems."""
        ...

    def push_status(self, project: str, item_id: str, status: Status) -> None:
        """Reflect a lifecycle change made in Tradvia back in the tool."""
        ...


@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    name: str
    status: ConnectorStatus = "planned"


# Initial targets. Each one is a self-contained contribution.
CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("trello", "Trello"),
    ConnectorSpec("jira", "Jira"),
    ConnectorSpec("clickup", "ClickUp"),
    ConnectorSpec("monday", "Monday.com"),
    ConnectorSpec("asana", "Asana"),
    ConnectorSpec("wrike", "Wrike"),
    ConnectorSpec("smartsheet", "Smartsheet"),
    ConnectorSpec("artia", "Artia"),
    ConnectorSpec("ms-planner", "Microsoft Planner"),
    ConnectorSpec("kanbanflow", "KanbanFlow"),
    ConnectorSpec("airtable", "Airtable"),
)


# Tool status names differ ("To Do", "Doing", "Em andamento"…). A connector
# declares a mapping to categories; Tradvia derives the lifecycle status.
STATUS_FOR_CATEGORY: dict[Category, Status] = {
    Category.TODO: Status.BACKLOG,
    Category.IN_PROGRESS: Status.AGENT_WORKING,
    Category.WAITING: Status.WAITING_HUMAN,
    Category.REVIEW: Status.IN_REVIEW_PR,
    Category.DONE: Status.DONE,
}


class StatusMap:
    """Case- and accent-insensitive mapping from tool status names to categories."""

    def __init__(self, mapping: dict[str, Category], default: Category = Category.TODO):
        self._map = {self._norm(k): v for k, v in mapping.items()}
        self.default = default

    @staticmethod
    def _norm(s: str) -> str:
        import unicodedata
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().strip().lower()

    def category(self, tool_status: str) -> Category:
        return self._map.get(self._norm(tool_status), self.default)

    def status(self, tool_status: str) -> Status:
        return STATUS_FOR_CATEGORY[self.category(tool_status)]


class InMemoryConnector:
    """Reference implementation used by tests and demos."""

    key = "memory"

    def __init__(self, projects: dict[str, list[WorkItem]] | None = None):
        self.projects = {k: list(v) for k, v in (projects or {}).items()}

    def pull(self, project: str) -> list[WorkItem]:
        return list(self.projects.get(project, []))

    def push_status(self, project: str, item_id: str, status: Status) -> None:
        items = self.projects[project]
        for n, it in enumerate(items):
            if it.id == item_id:
                items[n] = replace(it, status=status)
                return
        raise KeyError(item_id)
