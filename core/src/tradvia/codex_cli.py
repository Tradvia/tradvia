# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""OpenAI Codex CLI (`codex exec --json`) as planner and executor (ADR 0010).

Authenticated by OPENAI_API_KEY (CODEX_API_KEY is read by the CLI too). Restricted by the
sandbox instead of a tool list: planning runs `read-only`; execution runs `workspace-write`
with network access disabled, so the agent can only touch the working tree. Codex has no turn
flag: a run with more command/file/tool items than the limit is rejected afterwards. Time is
bounded by the subprocess timeout. Codex reports tokens but no price: US$ is 'não informado'.
"""

from __future__ import annotations

import json

from .agents import CliAgent, Cost, Run, count

ACTION_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search")


class CodexCliAgent(CliAgent):
    name = "codex-cli"
    label = "Codex CLI"
    binary = "codex"
    install = "npm i -g @openai/codex"

    def command(self, prompt: str, execute: bool, max_turns: int) -> list[str]:
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check",
               "--sandbox", "workspace-write" if execute else "read-only"]
        if execute:
            cmd += ["-c", "sandbox_workspace_write.network_access=false"]
        return cmd + [prompt]

    def parse(self, proc, max_turns: int, duration_s: float) -> Run:
        events = []
        for line in str(proc.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if not events:
            raise self.fail(proc, "invalid JSON: " + str(proc.stderr or proc.stdout or ""))
        text, calls, usage = "", 0, None
        for event in events:
            kind = event.get("type")
            if kind in ("turn.failed", "error"):
                err = event.get("error") if isinstance(event.get("error"), dict) else event
                raise self.fail(proc, str(err.get("message") or kind))
            if kind == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
            item = event.get("item") if kind == "item.completed" else None
            if isinstance(item, dict):
                if item.get("type") == "agent_message":
                    text = str(item.get("text") or "")
                elif item.get("type") in ACTION_ITEMS:
                    calls += 1
        if proc.returncode != 0:
            raise self.fail(proc, str(proc.stderr or ""))
        if calls > max_turns:
            raise RuntimeError(f"{self.label} stopped at the turn limit ({max_turns} turns)")
        u = usage or {}
        cost = Cost(None, count(u.get("input_tokens")), count(u.get("output_tokens")),
                    count(u.get("cached_input_tokens")), duration_s)
        return Run(text, cost, calls)
