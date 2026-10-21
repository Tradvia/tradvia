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
from typing import Callable

from .llm import _parse_json

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
               runner: Callable = subprocess.run, timeout: int | None = None) -> dict:
    """Run `claude -p` headless and return its JSON result."""
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--max-turns", str(max_turns), "--allowedTools", allowed_tools]
    try:
        proc = runner(cmd, cwd=cwd, capture_output=True, text=True,
                      timeout=timeout or _timeout())
    except FileNotFoundError:
        raise RuntimeError("Claude Code CLI not found: install it "
                           "(npm i -g @anthropic-ai/claude-code) and put `claude` on PATH") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude Code timed out after {timeout or _timeout()}s") from None
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        raise RuntimeError(f"Claude Code returned invalid JSON (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout or '')[:300]}") from None
    if proc.returncode != 0 or data.get("is_error"):
        if data.get("subtype") == "error_max_turns":
            raise RuntimeError(f"Claude Code stopped at the turn limit ({max_turns} turns)")
        detail = str(data.get("result") or data.get("subtype") or "")[:300]
        raise RuntimeError(f"Claude Code failed: {detail}")
    return data


def _cost(data: dict) -> dict:
    return {"usd": float(data.get("total_cost_usd") or 0.0), "tokens_in": 0, "tokens_out": 0,
            "duration_s": round((data.get("duration_ms") or 0) / 1000, 3)}


def claude_code_plan(title: str, body: str, number: int, runner: Callable = subprocess.run,
                     cwd: str = ".") -> dict:
    """Same signature and return shape as `architect_plan`, minus the single-file fields."""
    data = run_claude(PLAN_PROMPT.format(number=number, title=title, body=body), cwd,
                      PLAN_TOOLS, max_turns=plan_max_turns(), runner=runner)
    try:
        plan = _parse_json(data.get("result", ""))
    except ValueError as e:
        raise ValueError(f"Claude Code plan is not valid JSON: {e}") from None
    steps = plan.get("plan")
    if not (isinstance(steps, list) and steps and all(isinstance(s, str) for s in steps)):
        raise ValueError("Claude Code plan is missing a non-empty 'plan' list of strings")
    if not isinstance(plan.get("question"), str) or not plan["question"]:
        raise ValueError("Claude Code plan is missing 'question'")
    plan.setdefault("summary", "")
    plan["_cost"] = _cost(data)
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


class ClaudeCodeExecutor:
    def __init__(self, runner: Callable = subprocess.run, max_turns: int | None = None):
        self.runner, self.max_turns = runner, max_turns

    def execute(self, card: dict, cwd: str = ".") -> dict:
        steps = "\n".join(f"{s['id']}. {s['title']}" for s in card.get("plan", []))
        prompt = EXEC_PROMPT.format(number=card["source"]["issue_number"], title=card["title"],
                                    body=card.get("description", ""), plan=steps)
        data = run_claude(prompt, cwd, EXEC_TOOLS, self.max_turns or exec_max_turns(), self.runner)
        cost = _cost(data)
        return {"changed_files": changed_files(cwd, self.runner),
                "untracked_files": untracked_files(cwd, self.runner), "cost_usd": cost["usd"],
                "duration_s": cost["duration_s"], "summary": str(data.get("result", "")).strip()}
