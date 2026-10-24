# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Stagnation detection (ADR 0006, phase 1): an agent repeating the same call in a loop.

One monitor per run, no global state. Everything it records carries the `project_id`,
so nothing is shared between projects.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

DEFAULT_REPEAT_LIMIT = 3
SUMMARY_CHARS = 200
VOLATILE_KEYS = frozenset({"timestamp", "ts", "time", "now", "date", "datetime",
                           "created_at", "updated_at", "request_id"})


def _normalize(value):
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
                if str(k).lower() not in VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def normalize_call(tool: str, params: object) -> str:
    """Canonical key of a call: lowercase tool, sorted keys, collapsed strings, no volatile keys."""
    name = re.sub(r"\s+", " ", str(tool)).strip().lower()
    return f"{name} {json.dumps(_normalize(params if params is not None else {}), sort_keys=True, default=str)}"


def reflective_message(tool: str, count: int) -> str:
    return f"You repeated {tool} {count} times; change your approach or ask for help."


@dataclass(frozen=True)
class StagnationMonitor:
    repeat_limit: int = DEFAULT_REPEAT_LIMIT
    project_id: int | str | None = None
    calls: list = field(default_factory=list, compare=False)
    outputs: list = field(default_factory=list, compare=False)
    _state: dict = field(default_factory=lambda: {"key": None, "tool": "", "count": 0,
                                                  "warned": False}, compare=False, repr=False)

    @property
    def repeated_tool(self) -> str:
        return self._state["tool"]

    @property
    def repeat_count(self) -> int:
        return self._state["count"]

    def record_call(self, tool: str, params: object = None) -> str:
        """Record a call; return 'ok', 'warn' (limit reached the first time) or 'stop'."""
        key, s = normalize_call(tool, params), self._state
        self.calls.append({"project_id": self.project_id, "tool": str(tool), "key": key})
        if key == s["key"]:
            s["count"] += 1
        else:
            s.update(key=key, tool=str(tool), count=1)
        if s["count"] < self.repeat_limit:
            return "ok"
        if s["warned"]:
            return "stop"
        s["warned"] = True
        return "warn"

    def record_output(self, text: str) -> dict:
        """Store an intermediate output as a truncated summary plus a hash."""
        text = str(text)
        entry = {"project_id": self.project_id, "summary": text[:SUMMARY_CHARS],
                 "hash": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()}
        self.outputs.append(entry)
        return entry
