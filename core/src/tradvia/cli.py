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

from .checkpoints import LOCAL_PROJECT_ID, fork, list_checkpoints, make_checkpointer, thread_config
from .agents import PROVIDERS as CODING_AGENTS, make_agent
from .claude_code import ClaudeCodeExecutor, claude_code_plan
from .budget import store_month_spend
from .graph import build_graph
from .integrations import DiscordREST, GitHubIssueChat, GitHubREST
from .llm import _offline_plan, architect_plan
from .policy import Policy, PolicyError, load_policy, role_of, with_budget


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
    if kind in CODING_AGENTS:
        return make_agent(kind).plan
    if kind == "offline":
        return _offline_plan
    if kind in ("", "litellm"):
        return architect_plan
    sys.exit(f"Unknown TRADVIA_PLANNER {kind!r}: use {', '.join(CODING_AGENTS)}, litellm or offline")


def make_executor(kind: str | None = None, repeat_limit: int = 3,
                  project_id: int | str | None = None):
    kind = kind or os.getenv("TRADVIA_EXECUTOR", "")
    if kind == "claude-code":
        return ClaudeCodeExecutor(repeat_limit=repeat_limit, project_id=project_id)
    if kind in CODING_AGENTS:
        return make_agent(kind, repeat_limit=repeat_limit, project_id=project_id)
    if kind in ("", "note"):
        return None
    sys.exit(f"Unknown TRADVIA_EXECUTOR {kind!r}: use {', '.join(CODING_AGENTS)} or note")


def project_id_of(repo: str) -> int:
    """Project of the repository, as the API resolves it; a fixed local id without a database."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return LOCAL_PROJECT_ID
    from .store import PgStore

    return PgStore(url).ensure_project(repo)


def project_budget_setting(repo: str) -> object:
    """The project's budget setting (ADR 0018) from the store; None without a database, which
    means the defaults: per-card limits of Budget() and no monthly ceiling. Raises if a database
    is configured but cannot be read, so a card never runs past the project's own limits."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    from .budget import SETTINGS_KEY
    from .store import PgStore

    store = PgStore(url)
    row = store.get_setting(store.ensure_project(repo), SETTINGS_KEY)
    return row["value"] if row else None


def make_month_spend(repo: str):
    """Callable with this month's spend of the project; None without a database (no history)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    from .store import PgStore

    store = PgStore(url)
    return lambda: store_month_spend(store, store.ensure_project(repo))


def _durable_command(argv: list[str], name: str) -> None:
    parser = argparse.ArgumentParser(prog=f"tradvia {name}")
    parser.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    if name == "fork":
        parser.add_argument("--checkpoint", required=True, help="checkpoint id from `history`")
    args = parser.parse_args(argv)
    load_dotenv()

    repo = env("TRADVIA_REPO")
    project_id = project_id_of(repo)
    config = thread_config(project_id, repo, args.issue)
    app = build_graph(None, None, checkpointer=make_checkpointer())
    try:
        if name == "history":
            print(f"thread {config['configurable']['thread_id']}")
            for r in list_checkpoints(app, config, project_id):
                print(f"{r['step']!s:>4}  {r['node'] or '-':<24} {r['status'] or '-':<14} "
                      f"{r['created_at']}  {r['checkpoint_id']}")
        else:
            out = fork(app, config, args.checkpoint, project_id)
            print(f"forked {out['thread_id']} from checkpoint {out['checkpoint_id']}")
    except ValueError as e:
        sys.exit(str(e))


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
    try:
        policy = with_budget(policy, project_budget_setting(env("TRADVIA_REPO")))
    except Exception as e:  # never run without the project's own limits once a store exists
        github.comment(issue, f"Could not read this project's budget from Tradvia "
                              f"({type(e).__name__}). The card was not started; restart it once "
                              "the store is reachable.")
        return not_started

    actor = os.getenv("TRADVIA_ACTOR")
    if actor and not policy.allows("start_card", role_of(github, actor)):
        github.comment(issue, f"@{actor} does not have permission to start a card: "
                              f"it needs at least the `{policy.interactions.start_card}` role.")
        return not_started

    chat = make_chat(chat_kind, issue, policy)
    repo = env("TRADVIA_REPO")
    app = build_graph(github, chat, planner=make_planner(), executor=make_executor(
        repeat_limit=policy.stagnation.repeat_limit, project_id=github.repo),
                      checkpointer=make_checkpointer(), policy=policy, budget=policy.budget,
                      month_spend=make_month_spend(repo))

    # Checkpoints and usage are keyed by project (repository) and never cross projects.
    config = thread_config(project_id_of(repo), repo, issue)
    snapshot = app.get_state(config)
    pending = [i for t in snapshot.tasks for i in t.interrupts]
    if snapshot.next and pending:
        print(f"↻ {card_id} resumes from its last checkpoint (waiting for a human)", flush=True)
        result = {**snapshot.values, "__interrupt__": pending}
    elif snapshot.next:
        print(f"↻ {card_id} resumes from its last checkpoint", flush=True)
        result = app.invoke(None, config)
    else:
        result = app.invoke({"id": card_id, "project_id": github.repo, "status": "BACKLOG",
                             "source": {"provider": "github", "issue_number": issue}}, config)

    if chat_kind == "github" and not os.getenv("DATABASE_URL") and wait_timeout == 0:
        print("⚠ DATABASE_URL is not set: checkpoints are in memory, so durable waiting is "
              "disabled. Waiting for the reply in this job instead (--wait 3600).", flush=True)
        wait_timeout = 3600

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
    if sys.argv[1:2] == ["backlog"]:
        from .backlog import main as backlog_main

        load_dotenv()
        backlog_main(argv=sys.argv[2:])
        return
    if sys.argv[1:2] in (["history"], ["fork"]):
        _durable_command(sys.argv[2:], sys.argv[1])
        return
    if sys.argv[1:2] == ["mcp"]:
        from .mcp_server import main as mcp_main

        load_dotenv()
        mcp_main()
        return
    if sys.argv[1:2] in (["token"], ["card"]) or sys.argv[1:2] == ["--url"]:
        from .remote import main as remote_main

        load_dotenv()
        remote_main(argv=sys.argv[1:])
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
    if card.get("plan_error"):  # no plan was published: fail the run, so Actions shows it red
        sys.exit(f"Planning failed: {card['plan_error']}")


if __name__ == "__main__":
    main()
