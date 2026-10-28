# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Coding agent adapters (ADR 0010): the planner/executor of a card is a replaceable CLI.

`CodingAgent` is the interface; `ClaudeCodeAgent` (claude_code.py), `GeminiCliAgent`
(gemini_cli.py) and `CodexCliAgent` (codex_cli.py) implement it. Tradvia governs the card,
the human gate and the pull request; the agent only plans (JSON) or edits the working tree.
Protected paths are enforced after the run by the graph, from `changed_files`, for every agent.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .claude_code import (EXEC_PROMPT, PLAN_PROMPT, add_costs, changed_files, exec_max_turns,
                          plan_max_turns, plan_with_repair, untracked_files, _timeout)
from .connections import redact

NOT_REPORTED = "não informado"
PROVIDERS = ("claude-code", "gemini-cli", "codex-cli")


class CodingAgent(Protocol):
    """What the orchestrator needs from a planner/executor, whatever the CLI behind it."""

    name: str

    def plan(self, title: str, body: str, number: int, cwd: str = ".", notice: str = "") -> dict:
        """Plan dict: summary, plan (list of steps), question, `_cost`, `_tool_calls`."""

    def execute(self, card: dict, cwd: str = ".", budget_notice: str = "") -> dict:
        """Edit the working tree; return changed_files, untracked_files, cost_usd, tokens_*,
        duration_s, tool_calls and summary."""


@dataclass(frozen=True)
class Cost:
    """Cost of one run. `None` means the provider did not report it (shown as 'não informado')."""

    usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None
    duration_s: float = 0.0

    @property
    def tokens_reported(self) -> bool:
        return any(v is not None for v in (self.tokens_in, self.tokens_out, self.tokens_cache))

    def as_dict(self) -> dict:
        """Numbers for the panel (0 when unreported) plus flags telling which are real."""
        return {"usd": self.usd or 0.0, "tokens_in": self.tokens_in or 0,
                "tokens_out": self.tokens_out or 0, "tokens_cache": self.tokens_cache or 0,
                "duration_s": round(self.duration_s, 3), "usd_reported": self.usd is not None,
                "tokens_reported": self.tokens_reported}

    def label(self) -> str:
        usd = f"US$ {self.usd:.4f}" if self.usd is not None else NOT_REPORTED
        tokens = (f"{self.tokens_in or 0} in / {self.tokens_out or 0} out"
                  if self.tokens_reported else NOT_REPORTED)
        return f"{usd} · {tokens}"


def count(value: object) -> int | None:
    """A non-negative integer count, or None when the provider gave something else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


@dataclass(frozen=True)
class Run:
    """Parsed result of one CLI call."""

    text: str
    cost: Cost
    tool_calls: int


class CliAgent:
    """Shared flow of the headless adapters; subclasses build the command and parse its output."""

    name = ""
    label = ""  # shown in messages, e.g. "Gemini CLI"
    binary = ""
    install = ""

    def __init__(self, runner: Callable = subprocess.run, max_turns: int | None = None,
                 timeout: int | None = None, env: Mapping[str, str] | None = None, **_ignored):
        self.runner, self.max_turns, self.timeout, self.env = runner, max_turns, timeout, env

    def command(self, prompt: str, execute: bool, max_turns: int) -> list[str]:
        raise NotImplementedError

    def parse(self, proc, max_turns: int, duration_s: float) -> Run:
        raise NotImplementedError

    def run(self, prompt: str, cwd: str, execute: bool, max_turns: int) -> Run:
        limit = self.timeout or _timeout()
        extra = {"env": self.env} if self.env is not None else {}
        started = time.monotonic()
        try:
            proc = self.runner(self.command(prompt, execute, max_turns), cwd=cwd,
                               capture_output=True, text=True, timeout=limit, **extra)
        except FileNotFoundError:
            raise RuntimeError(f"{self.label} not found: install it ({self.install}) "
                               f"and put `{self.binary}` on PATH") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{self.label} timed out after {limit}s") from None
        return self.parse(proc, max_turns, time.monotonic() - started)

    def fail(self, proc, detail: str) -> RuntimeError:
        secrets = list((self.env or {}).values())
        return RuntimeError(f"{self.label} failed (exit {proc.returncode}): "
                            f"{redact(detail[:300], secrets)}")

    def plan(self, title: str, body: str, number: int, cwd: str = ".", notice: str = "") -> dict:
        prompt = PLAN_PROMPT.format(number=number, title=title, body=body)
        prompt += f"\n\n{notice}" if notice else ""

        def ask(text: str) -> Run:
            return self.run(text, cwd, False, plan_max_turns())

        plan, runs = plan_with_repair(ask, lambda result: result.text, prompt, self.label)
        plan["_cost"] = add_costs([result.cost.as_dict() for result in runs])
        plan["_tool_calls"] = sum(result.tool_calls for result in runs)
        return plan

    def execute(self, card: dict, cwd: str = ".", budget_notice: str = "") -> dict:
        steps = "\n".join(f"{s['id']}. {s['title']}" for s in card.get("plan", []))
        prompt = EXEC_PROMPT.format(number=card["source"]["issue_number"], title=card["title"],
                                    body=card.get("description", ""), plan=steps)
        if budget_notice:
            prompt += f"\n\n{budget_notice}"
        result = self.run(prompt, cwd, True, self.max_turns or exec_max_turns())
        cost = result.cost.as_dict()
        return {"changed_files": changed_files(cwd, self.runner),
                "untracked_files": untracked_files(cwd, self.runner), "cost_usd": cost["usd"],
                "tokens_in": cost["tokens_in"], "tokens_out": cost["tokens_out"],
                "tokens_cache": cost["tokens_cache"], "duration_s": cost["duration_s"],
                "usd_reported": cost["usd_reported"], "tokens_reported": cost["tokens_reported"],
                "tool_calls": result.tool_calls, "summary": result.text.strip()}


def make_agent(provider: str, **kwargs) -> CodingAgent:
    """Adapter for a provider key (`claude-code`, `gemini-cli`, `codex-cli`)."""
    if provider == "claude-code":
        from .claude_code import ClaudeCodeAgent

        return ClaudeCodeAgent(**kwargs)
    if provider == "gemini-cli":
        from .gemini_cli import GeminiCliAgent

        return GeminiCliAgent(**kwargs)
    if provider == "codex-cli":
        from .codex_cli import CodexCliAgent

        return CodexCliAgent(**kwargs)
    raise ValueError(f"Unknown coding agent {provider!r}: use {', '.join(PROVIDERS)}")
