# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Claude Code CLI (`claude -p`) as planner and executor.

Authenticated by the subscription (CLAUDE_CODE_OAUTH_TOKEN), no ANTHROPIC_API_KEY.
Tradvia governs (card, human gate, PR); Claude Code edits the working tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Callable, Mapping

from .connections import redact
from .llm import _parse_json
from .stagnation import DEFAULT_REPEAT_LIMIT, StagnationMonitor, reflective_message

PLAN_TOOLS = "Read,Glob,Grep"
EXEC_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(make check),Bash(make test)"

PLAN_PROMPT = """You are @Agent-Architect in a software team. Read the repository as needed
and propose the smallest change that solves the GitHub issue below. Do not modify anything.
Tradvia creates the branch, makes the commit and opens the pull request, so the plan must
contain only code, test and docs changes: no git or pull request steps.
Answer ONLY with JSON, no markdown fences:
{{"summary": "<one sentence>",
 "plan": ["<step>", "..."],
 "question": "<one short question for the human to approve or adjust the plan>"}}

Issue #{number}: {title}

{body}"""

EXEC_PROMPT = """You are @Agent-Coder. Implement the approved plan below in the working tree.
Follow CLAUDE.md, run `make check` when done and fix failures. Do not touch .github/, .git/,
LICENSE.md, CLA.md or CODEOWNERS.
Do not create temporary or verification files in the repository. If you need scratch space,
use a directory outside the repository (for example a `mktemp -d` path) or write nothing.
Tradvia creates the branch, makes the commit and opens the pull request. Do not run git
commands (checkout, commit, push) or open a pull request: only edit the working tree and run
`make check`. Finish with a short summary of what you changed, with no git or pull request
steps and no statement about whether a pull request was opened.

Issue #{number}: {title}

{body}

Approved plan:
{plan}"""


def _timeout() -> int:
    return int(os.getenv("TRADVIA_CLAUDE_TIMEOUT", "900"))


DEFAULT_EXEC_MAX_TURNS = 80


DEFAULT_PLAN_MAX_TURNS = 30


def _positive_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def plan_max_turns() -> int:
    """Planner turn limit from `TRADVIA_PLAN_MAX_TURNS` (positive integer, default 30)."""
    return _positive_env("TRADVIA_PLAN_MAX_TURNS", DEFAULT_PLAN_MAX_TURNS)


def exec_max_turns() -> int:
    """Executor turn limit from `TRADVIA_EXEC_MAX_TURNS` (positive integer, default 80)."""
    return _positive_env("TRADVIA_EXEC_MAX_TURNS", DEFAULT_EXEC_MAX_TURNS)


def run_claude(prompt: str, cwd: str, allowed_tools: str, max_turns: int = 20,
               runner: Callable = subprocess.run, timeout: int | None = None,
               env: Mapping[str, str] | None = None, disallowed_tools: str | None = None) -> dict:
    """Run `claude -p` headless and return its JSON result.

    `env` replaces the child's environment and `disallowed_tools` adds `--disallowedTools`;
    both are only passed when given. `env` is used both for the orchestrator's restricted
    environment (orchestrator.py; a full replacement so a popped key like
    CLAUDE_CODE_OAUTH_TOKEN really is absent) and for project connection secrets (ADR 0009),
    where the caller merges them on top of `os.environ` before passing them in. Everything
    this function raises or returns is redacted.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--max-turns", str(max_turns), "--allowedTools", allowed_tools]
    if disallowed_tools:
        cmd += ["--disallowedTools", disallowed_tools]
    extra = {"env": env} if env is not None else {}
    secrets = list((env or {}).values())
    try:
        proc = runner(cmd, cwd=cwd, capture_output=True, text=True,
                      timeout=timeout or _timeout(), **extra)
    except FileNotFoundError:
        raise RuntimeError("Claude Code CLI not found: install it "
                           "(npm i -g @anthropic-ai/claude-code) and put `claude` on PATH") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude Code timed out after {timeout or _timeout()}s") from None
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        output = redact((proc.stderr or proc.stdout or "")[:300], secrets)
        raise RuntimeError(f"Claude Code returned invalid JSON (exit {proc.returncode}): "
                           f"{output}") from None
    if proc.returncode != 0 or data.get("is_error"):
        if data.get("subtype") == "error_max_turns":
            raise RuntimeError(f"Claude Code stopped at the turn limit ({max_turns} turns)")
        detail = redact(str(data.get("result") or data.get("subtype") or "")[:300], secrets)
        raise RuntimeError(f"Claude Code failed: {detail}")
    return data


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _cost(data: dict) -> dict:
    """Cost and tokens of one `claude -p` result; `usage` or its cache fields may be missing."""
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return {"usd": float(data.get("total_cost_usd") or 0.0),
            "tokens_in": _count(usage, "input_tokens"),
            "tokens_out": _count(usage, "output_tokens"),
            "tokens_cache": (_count(usage, "cache_read_input_tokens")
                             + _count(usage, "cache_creation_input_tokens")),
            "duration_s": round((data.get("duration_ms") or 0) / 1000, 3)}


def _reported(data: dict) -> dict:
    """Whether the provider gave a cost and token usage (else the panel says 'não informado')."""
    return {"usd_reported": data.get("total_cost_usd") is not None,
            "tokens_reported": isinstance(data.get("usage"), dict)}


def parse_plan(text: str, label: str = "Claude Code") -> dict:
    """Parse and validate a planner answer; shared by every coding agent adapter."""
    try:
        plan = _parse_json(text)
    except ValueError as e:
        raise ValueError(f"{label} plan is not valid JSON: {e}") from None
    steps = plan.get("plan")
    if not (isinstance(steps, list) and steps and all(isinstance(s, str) for s in steps)):
        raise ValueError(f"{label} plan is missing a non-empty 'plan' list of strings")
    if not isinstance(plan.get("question"), str) or not plan["question"]:
        raise ValueError(f"{label} plan is missing 'question'")
    plan.setdefault("summary", "")
    return plan


def claude_code_plan(title: str, body: str, number: int, runner: Callable = subprocess.run,
                     cwd: str = ".", notice: str = "") -> dict:
    """Same signature and return shape as `architect_plan`, minus the single-file fields.

    `notice` is an optional system notice (budget warning) appended to the prompt.
    """
    prompt = PLAN_PROMPT.format(number=number, title=title, body=body)
    data = run_claude(prompt + (f"\n\n{notice}" if notice else ""), cwd,
                      PLAN_TOOLS, max_turns=plan_max_turns(), runner=runner)
    plan = parse_plan(data.get("result", ""))
    plan["_cost"] = {**_cost(data), **_reported(data)}
    plan["_tool_calls"] = _count(data, "num_turns")
    return plan


def _status_entries(cwd: str, runner: Callable = subprocess.run) -> list[tuple[str, str]]:
    """(status, path) pairs from `git status` (renames report both old and new path)."""
    out = runner(["git", "status", "--porcelain", "-z", "-uall"], cwd=cwd,
                 capture_output=True, text=True).stdout or ""
    entries, files, i = out.split("\0"), [], 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        files.append((e[:2], e[3:]))
        if e[0] in "RC" and i < len(entries):  # next entry is the original path
            files.append((e[:2], entries[i]))
            i += 1
    return files


def changed_files(cwd: str, runner: Callable = subprocess.run) -> list[str]:
    """Paths changed in the working tree (renames report both old and new path)."""
    return [path for _, path in _status_entries(cwd, runner)]


def untracked_files(cwd: str, runner: Callable = subprocess.run) -> list[str]:
    """Paths git reports as new and untracked (`??`)."""
    return [path for status, path in _status_entries(cwd, runner) if status == "??"]


class StagnationError(RuntimeError):
    """The agent kept repeating the same call even after the reflective message."""

    def __init__(self, tool: str, count: int):
        self.tool, self.count = tool, count
        super().__init__(f"Stagnation: {tool} repeated {count} times, "
                         "also after the reflective retry")


def _text_of(content: object) -> str:
    """Plain text of a tool_result `content` (a string or a list of text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def _feed(event: dict, monitor: StagnationMonitor) -> tuple[str, str]:
    """Feed one stream-json event to the monitor; return (signal, tool).

    `assistant` events carry `tool_use` blocks (name + input); `user` events carry the
    `tool_result` blocks, the intermediate outputs.
    """
    message = event.get("message")
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, list):
        return "ok", ""
    signal, tool = "ok", ""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if event.get("type") == "assistant" and block.get("type") == "tool_use":
            name = str(block.get("name", ""))
            result = monitor.record_call(name, block.get("input"))
            if result != "ok":
                return result, name
        elif event.get("type") == "user" and block.get("type") == "tool_result":
            monitor.record_output(_text_of(block.get("content")))
    return signal, tool


def _popen_lines(cmd: list[str], cwd: str, timeout: int, tail: list[str]):
    """Yield stdout lines of a live process; kill it when the generator is closed."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        yield from proc.stdout
        proc.wait()
        tail.append(str(proc.returncode))
        if not timer.is_alive():
            tail.append("timeout")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def run_claude_stream(prompt: str, cwd: str, allowed_tools: str, max_turns: int,
                      monitor: StagnationMonitor, runner: Callable = subprocess.run,
                      timeout: int | None = None) -> tuple[dict | None, str, str]:
    """Run `claude -p --output-format stream-json --verbose`, feeding the monitor live.

    Returns (result, signal, tool): `result` is the final JSON object, or None when the
    run was interrupted by a 'warn'/'stop' signal. With the default runner the process
    is streamed and killed on interruption; an injected runner returns the whole output.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), "--allowedTools", allowed_tools]
    limit = timeout or _timeout()
    tail: list[str] = []
    try:
        if runner is subprocess.run:
            lines = _popen_lines(cmd, cwd, limit, tail)
        else:
            proc = runner(cmd, cwd=cwd, capture_output=True, text=True, timeout=limit)
            lines = iter((proc.stdout or "").splitlines())
            tail.append(str(proc.returncode))
            if proc.returncode and proc.stderr:
                tail.append(str(proc.stderr))
        raw: list[str] = []
        final = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                raw.append(line)
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result" or (event.get("type") is None
                                                 and "message" not in event):
                final = event
                continue
            signal, tool = _feed(event, monitor)
            if signal != "ok":
                if hasattr(lines, "close"):
                    lines.close()
                return None, signal, tool
    except FileNotFoundError:
        raise RuntimeError("Claude Code CLI not found: install it "
                           "(npm i -g @anthropic-ai/claude-code) and put `claude` on PATH") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude Code timed out after {limit}s") from None
    if "timeout" in tail:
        raise RuntimeError(f"Claude Code timed out after {limit}s")
    if final is None:
        raise RuntimeError("Claude Code returned invalid JSON "
                           f"(exit {tail[0] if tail else '?'}): {' '.join(raw)[:300]}")
    returncode = tail[0] if tail else "0"
    if returncode != "0" or final.get("is_error"):
        if final.get("subtype") == "error_max_turns":
            raise RuntimeError(f"Claude Code stopped at the turn limit ({max_turns} turns)")
        detail = str(final.get("result") or final.get("subtype") or "")[:300]
        raise RuntimeError(f"Claude Code failed: {detail}")
    return final, "ok", ""


class ClaudeCodeExecutor:
    def __init__(self, runner: Callable = subprocess.run, max_turns: int | None = None,
                 repeat_limit: int = DEFAULT_REPEAT_LIMIT, project_id: int | str | None = None):
        self.runner, self.max_turns = runner, max_turns
        self.repeat_limit, self.project_id = repeat_limit, project_id

    def execute(self, card: dict, cwd: str = ".", budget_notice: str = "") -> dict:
        steps = "\n".join(f"{s['id']}. {s['title']}" for s in card.get("plan", []))
        prompt = EXEC_PROMPT.format(number=card["source"]["issue_number"], title=card["title"],
                                    body=card.get("description", ""), plan=steps)
        if budget_notice:
            prompt += f"\n\n{budget_notice}"
        # One monitor per run: nothing is shared between cards or projects.
        monitor = StagnationMonitor(self.repeat_limit, card.get("project_id", self.project_id))
        turns = self.max_turns or exec_max_turns()
        data, signal, tool = run_claude_stream(prompt, cwd, EXEC_TOOLS, turns, monitor, self.runner)
        if data is None:  # first time at the limit: restart once with the reflective message
            note = reflective_message(tool, monitor.repeat_count)
            data, signal, tool = run_claude_stream(f"{prompt}\n\n{note}", cwd, EXEC_TOOLS, turns,
                                                   monitor, self.runner)
            if data is None:
                raise StagnationError(tool, monitor.repeat_count)
        cost = _cost(data)
        return {"changed_files": changed_files(cwd, self.runner),
                "untracked_files": untracked_files(cwd, self.runner), "cost_usd": cost["usd"],
                "tokens_in": cost["tokens_in"], "tokens_out": cost["tokens_out"],
                "tokens_cache": cost["tokens_cache"], "duration_s": cost["duration_s"],
                **_reported(data), "tool_calls": _count(data, "num_turns"),
                "summary": str(data.get("result", "")).strip()}


class ClaudeCodeAgent:
    """`CodingAgent` (agents.py) backed by Claude Code: delegates to the functions above."""

    name = "claude-code"

    def __init__(self, runner: Callable = subprocess.run, repeat_limit: int = DEFAULT_REPEAT_LIMIT,
                 project_id: int | str | None = None, max_turns: int | None = None):
        self.runner = runner
        self._executor = ClaudeCodeExecutor(runner, max_turns, repeat_limit, project_id)

    def plan(self, title: str, body: str, number: int, cwd: str = ".", notice: str = "") -> dict:
        return claude_code_plan(title, body, number, runner=self.runner, cwd=cwd, notice=notice)

    def execute(self, card: dict, cwd: str = ".", budget_notice: str = "") -> dict:
        return self._executor.execute(card, cwd, budget_notice)
