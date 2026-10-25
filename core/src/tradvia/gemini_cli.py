# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Gemini CLI (`gemini -p`, JSON output) as planner and executor (ADR 0010).

Authenticated by GEMINI_API_KEY. Tools are restricted with `--allowed-tools`: planning is
read-only; execution can edit files and run only `make check`/`make test` (no git, no web
fetch or search, so nothing leaves the machine except the provider call). The CLI has no
turn flag: exit code 53 is its turn-limit stop, and a run that used more tool calls than the
configured limit is rejected afterwards. Time is bounded by the subprocess timeout.
"""

from __future__ import annotations

import json

from .agents import CliAgent, Cost, Run, count

PLAN_TOOLS = "read_file,list_directory,glob,search_file_content"
EXEC_TOOLS = ("read_file,list_directory,glob,search_file_content,write_file,replace,"
              "run_shell_command(make check),run_shell_command(make test)")
TURN_LIMIT_EXIT = 53


def _tokens(stats: dict) -> tuple[int | None, int | None, int | None]:
    """Sum prompt/output/cached tokens over the models; None when nothing was reported."""
    models = stats.get("models")
    totals: dict[str, int | None] = {"in": None, "out": None, "cache": None}
    for model in (models.values() if isinstance(models, dict) else ()):
        tokens = model.get("tokens") if isinstance(model, dict) else None
        if not isinstance(tokens, dict):
            continue
        for key, names in (("in", ("prompt", "input")), ("out", ("candidates", "output")),
                           ("cache", ("cached",))):
            value = next((count(tokens[n]) for n in names if n in tokens), None)
            if value is not None:
                totals[key] = (totals[key] or 0) + value
    return totals["in"], totals["out"], totals["cache"]


class GeminiCliAgent(CliAgent):
    name = "gemini-cli"
    label = "Gemini CLI"
    binary = "gemini"
    install = "npm i -g @google/gemini-cli"

    def command(self, prompt: str, execute: bool, max_turns: int) -> list[str]:
        return ["gemini", "-p", prompt, "--output-format", "json",
                "--approval-mode", "auto_edit" if execute else "default",
                "--allowed-tools", EXEC_TOOLS if execute else PLAN_TOOLS]

    def parse(self, proc, max_turns: int, duration_s: float) -> Run:
        try:
            data = json.loads(proc.stdout)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (TypeError, ValueError):
            raise self.fail(proc, "invalid JSON: " + str(proc.stderr or proc.stdout or "")) from None
        if proc.returncode == TURN_LIMIT_EXIT:
            raise RuntimeError(f"{self.label} stopped at the turn limit ({max_turns} turns)")
        error = data.get("error")
        if proc.returncode != 0 or error:
            detail = error.get("message") if isinstance(error, dict) else error
            raise self.fail(proc, str(detail or proc.stderr or ""))
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else {}
        calls = count(tools.get("totalCalls")) or 0
        if calls > max_turns:
            raise RuntimeError(f"{self.label} stopped at the turn limit ({max_turns} turns)")
        tokens_in, tokens_out, tokens_cache = _tokens(stats)
        return Run(str(data.get("response") or ""),
                   Cost(None, tokens_in, tokens_out, tokens_cache, duration_s), calls)
