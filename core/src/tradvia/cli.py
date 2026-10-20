# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Run the Hello World flow for one GitHub issue.

    python -m tradvia.cli --issue 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from langgraph.types import Command

from .claude_code import ClaudeCodeExecutor, claude_code_plan
from .graph import build_graph
from .integrations import DiscordREST, GitHubIssueChat, GitHubREST
from .llm import _offline_plan, architect_plan
from .policy import Policy, PolicyError, load_policy, role_of


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; real environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        sys.exit(f"Missing environment variable {name}. See core/.env.example")
    return value


def make_chat(kind: str, issue: int, policy: Policy | None = None):
    if kind == "github":
        return GitHubIssueChat(env("GITHUB_TOKEN"), env("TRADVIA_REPO"), issue, policy=policy)
    if kind == "discord":
        return DiscordREST(env("DISCORD_BOT_TOKEN"), env("DISCORD_CHANNEL_ID"))
    sys.exit(f"Unknown chat {kind!r}: use github or discord")


def make_planner(kind: str | None = None):
    kind = kind or os.getenv("TRADVIA_PLANNER", "")
    if kind == "claude-code":
        return claude_code_plan
    if kind == "offline":
        return _offline_plan
    if kind in ("", "litellm"):
        return architect_plan
    sys.exit(f"Unknown TRADVIA_PLANNER {kind!r}: use claude-code, litellm or offline")


def make_executor(kind: str | None = None):
    kind = kind or os.getenv("TRADVIA_EXECUTOR", "")
    if kind == "claude-code":
        return ClaudeCodeExecutor()
    if kind in ("", "note"):
        return None
    sys.exit(f"Unknown TRADVIA_EXECUTOR {kind!r}: use claude-code or note")


def run(issue: int, wait_timeout: int, chat_kind: str = "discord") -> dict:
    github = GitHubREST(env("GITHUB_TOKEN"), env("TRADVIA_REPO"),
                        os.getenv("TRADVIA_BASE_BRANCH", "main"))
    card_id = f"CARD-{issue}"
    not_started = {"id": card_id, "status": "BACKLOG",
                   "source": {"provider": "github", "issue_number": issue}}

    try:
        policy = load_policy(github)
    except PolicyError as e:
        github.comment(issue, f"Invalid .tradvia.yml on {github.base}: {e}. "
                              "The card was not started.")
        return not_started

    actor = os.getenv("TRADVIA_ACTOR")
    if actor and not policy.allows("start_card", role_of(github, actor)):
        github.comment(issue, f"@{actor} does not have permission to start a card: "
                              f"it needs at least the `{policy.interactions.start_card}` role.")
        return not_started

    chat = make_chat(chat_kind, issue, policy)
    app = build_graph(github, chat, planner=make_planner(), executor=make_executor())

    config = {"configurable": {"thread_id": card_id}}
    result = app.invoke({"id": card_id, "status": "BACKLOG",
                         "source": {"provider": "github", "issue_number": issue}}, config)

    while result.get("__interrupt__"):
        pending = result["__interrupt__"][0].value
        print(f"⏸  {card_id} WAITING_HUMAN — answer in {chat.name} (timeout {wait_timeout}s)", flush=True)
        reply = chat.wait_for_reply(pending["message_id"], wait_timeout)
        if reply is None:
            print("⌛ No answer in time. The card stays in WAITING_HUMAN.")
            return result
        print(f"💬 {reply['by']}: {reply['text']}")
        result = app.invoke(Command(resume=reply), config)

    return result


def main() -> None:
    if sys.argv[1:2] == ["backfill"]:
        from .backfill import main as backfill_main

        load_dotenv()
        backfill_main(argv=sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Tradvia v0.1 hello world")
    parser.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    parser.add_argument("--wait", type=int, default=1800, help="seconds to wait for a human reply")
    parser.add_argument("--chat", choices=["github", "discord"],
                        default=os.getenv("TRADVIA_CHAT", "discord"),
                        help="approval channel: the issue's comments (github) or Discord")
    args = parser.parse_args()
    load_dotenv()

    card = run(args.issue, args.wait, args.chat)
    summary = {k: card.get(k) for k in ("id", "status", "assignee", "file_path",
                                        "pull_request_url", "cost")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("history:")
    for h in card.get("history", []):
        print(f"  {h['at']}  {h['from']:>13} -> {h['to']:<13} by {h['by']}: {h['reason']}")


if __name__ == "__main__":
    main()
