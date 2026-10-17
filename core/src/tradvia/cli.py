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

from .graph import build_graph
from .integrations import DiscordREST, GitHubREST


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


def run(issue: int, wait_timeout: int) -> dict:
    github = GitHubREST(env("GITHUB_TOKEN"), env("TRADVIA_REPO"))
    chat = DiscordREST(env("DISCORD_BOT_TOKEN"), env("DISCORD_CHANNEL_ID"))
    app = build_graph(github, chat)

    card_id = f"CARD-{issue}"
    config = {"configurable": {"thread_id": card_id}}
    result = app.invoke({"id": card_id, "status": "BACKLOG",
                         "source": {"provider": "github", "issue_number": issue}}, config)

    while result.get("__interrupt__"):
        pending = result["__interrupt__"][0].value
        print(f"⏸  {card_id} WAITING_HUMAN — answer in Discord (timeout {wait_timeout}s)")
        reply = chat.wait_for_reply(pending["message_id"], wait_timeout)
        if reply is None:
            print("⌛ No answer in time. The card stays in WAITING_HUMAN.")
            return result
        print(f"💬 {reply['by']}: {reply['text']}")
        result = app.invoke(Command(resume=reply), config)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Tradvia v0.1 hello world")
    parser.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    parser.add_argument("--wait", type=int, default=1800, help="seconds to wait for a human reply")
    args = parser.parse_args()
    load_dotenv()

    card = run(args.issue, args.wait)
    summary = {k: card.get(k) for k in ("id", "status", "assignee", "file_path",
                                        "pull_request_url", "cost")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("history:")
    for h in card.get("history", []):
        print(f"  {h['at']}  {h['from']:>13} -> {h['to']:<13} by {h['by']}: {h['reason']}")


if __name__ == "__main__":
    main()
