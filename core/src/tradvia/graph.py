# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Hello World hybrid flow:

GitHub issue -> @Agent-Architect plans -> question in Discord -> human approves
-> @Agent-Coder creates a branch, commits the file and opens a pull request.

Card statuses follow core/contracts/card-state.v1.json.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .integrations import Chat, GitHub
from .llm import architect_plan
from .state import Card, transition

MAX_REPLANS = 3
_CONVENTIONAL = re.compile(r"(feat|fix|docs|ci|chore|refactor|test|perf|build)(\([^)]*\))?!?:")
APPROVE = ("aprovado", "aprovo", "approved", "approve", "sim", "yes", "ok", "pode", "lgtm")
REJECT = ("rejeitado", "rejeito", "reject", "rejected", "nao", "no", "cancela", "cancel")


def classify_reply(text: str) -> str:
    """Return 'approved', 'rejected' or 'feedback' for a human chat reply."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()
    first = re.split(r"[\s,.!:;]+", t, maxsplit=1)[0] if t else ""
    if first in APPROVE:
        return "approved"
    if first in REJECT:
        return "rejected"
    return "feedback"


def _conventional_title(title: str) -> str:
    """Prefix `feat: ` unless the title already starts with a Conventional Commit type."""
    return title if _CONVENTIONAL.match(title.strip()) else f"feat: {title}"


def safe_path(path: str) -> str:
    """Agents may only add files inside the repository, never CI or licensing files."""
    raw = path.strip().replace("\\", "/")
    parts = [s for s in raw.split("/") if s not in ("", ".")]
    p = "/".join(parts)
    if (not p or raw.startswith("/") or ".." in parts or parts[0] in (".github", ".git")
            or parts[-1] in ("LICENSE.md", "CLA.md", "CODEOWNERS", ".tradvia.yml")):
        raise ValueError(f"Path not allowed for agents: {path!r}")
    return p


def _chat_is_issue_thread(chat: Chat, github: GitHub, number: int) -> bool:
    repo = getattr(chat, "repo", None)
    return (repo is not None and repo == getattr(github, "repo", None)
            and getattr(chat, "issue", None) == number)


def build_graph(github: GitHub, chat: Chat,
                planner: Callable[[str, str, int], dict] = architect_plan,
                checkpointer=None, executor=None, cwd: str = "."):
    def start_work(card: Card) -> dict:
        number = card["source"]["issue_number"]
        issue = github.get_issue(number)
        return {
            "title": issue["title"],
            "description": issue["body"],
            "source": {**card["source"], "issue_url": issue["url"]},
            "assignee": "@Agent-Architect",
            "plan": [],
            "approvals": [],
            "pending_question": None,
            "branch": None,
            "pull_request_url": None,
            **transition(card.get("status", "BACKLOG"), "AGENT_WORKING", "human",
                         "Card assigned to agents"),
        }

    def plan(card: Card) -> dict:
        body = card["description"]
        feedback = [a["comment"] for a in card.get("approvals", []) if a["decision"] == "feedback"]
        if feedback:
            body += "\n\nHuman feedback to address:\n" + "\n".join(f"- {f}" for f in feedback)
        p = planner(card["title"], body, card["source"]["issue_number"])
        return {
            "assignee": "@Agent-Architect",
            "plan": [{"id": i + 1, "title": s, "done": False} for i, s in enumerate(p.get("plan", []))],
            "file_path": safe_path(p["file_path"]) if p.get("file_path") else "",
            "file_content": p.get("file_content", ""),
            "pending_question": {"asked_by": "@Agent-Architect", "text": p["question"],
                                 "summary": p.get("summary", "")},
            "cost": p.get("_cost", {}),
        }

    def ask_human(card: Card) -> dict:
        q = card["pending_question"]
        steps = "\n".join(f"{s['id']}. {s['title']}" for s in card["plan"])
        target = f"Arquivo: `{card['file_path']}`\n" if card.get("file_path") else ""
        text = (f"🟡 **{card['id']} — {card['title']}**\n{q.get('summary', '')}\n{steps}\n"
                f"{target}\n❓ {q['text']}\n"
                f"Responda **Aprovado**, **Rejeitado** ou escreva um ajuste.")
        message_id = chat.post(text)
        return {"pending_question": {**q, "channel": getattr(chat, "name", "chat").lower(),
                                     "message_id": message_id},
                **transition(card["status"], "WAITING_HUMAN", "@Agent-Architect",
                             "Approval gate before writing code")}

    def wait_human(card: Card) -> dict:
        # Pauses the run. The runner resumes it with the human reply.
        reply = interrupt({"card_id": card["id"],
                           "message_id": card["pending_question"]["message_id"]})
        decision = classify_reply(reply["text"])
        return {
            "approvals": [{"by": reply.get("by", "human"), "decision": decision,
                           "comment": reply["text"]}],
            "pending_question": None,
            **(transition(card["status"], "BACKLOG", "human", "Rejected in chat")
               if decision == "rejected" else
               transition(card["status"], "AGENT_WORKING", "human",
                          "Approved in chat" if decision == "approved" else "Feedback in chat")),
        }

    def route_after_reply(card: Card) -> str:
        last = card["approvals"][-1]["decision"]
        if last == "approved":
            return "open_pr"
        if last == "feedback" and sum(a["decision"] == "feedback" for a in card["approvals"]) < MAX_REPLANS:
            return "plan"
        return END

    def open_pr(card: Card) -> dict:
        number = card["source"]["issue_number"]
        branch = f"tradvia/issue-{number}"
        files = {card["file_path"]: card["file_content"]} if card.get("file_path") else {}
        extra, cost = "", {}
        if not executor and not files:
            why = ("o plano não trouxe `file_path` e nenhum executor está configurado "
                   "(use `TRADVIA_EXECUTOR=claude-code` com `TRADVIA_PLANNER=claude-code`)")
            chat.post(f"🟡 **{card['id']}** — {why}. Nenhum PR foi aberto.")
            return transition(card["status"], "WAITING_HUMAN", "@Agent-Coder", f"No executor: {why}")
        if executor:
            run = executor.execute(card, cwd)
            blocked = []
            for f in run["changed_files"]:
                try:
                    safe_path(f)
                except ValueError:
                    blocked.append(f)
            cost = {"usd": run["cost_usd"], "duration_s": run["duration_s"]}
            if blocked or not run["changed_files"]:
                why = (f"alterou arquivos protegidos: {', '.join('`' + b + '`' for b in blocked)}"
                       if blocked else "não alterou nenhum arquivo")
                chat.post(f"🟡 **{card['id']}** — o Claude Code {why}. Nenhum PR foi aberto; "
                          "revise o working tree e responda com um ajuste.")
                return {"cost": cost, **transition(card["status"], "WAITING_HUMAN",
                                                   "@Agent-Coder", f"Executor {why}")}
            files, skipped = {}, []
            for f in run["changed_files"]:
                if not Path(cwd, f).is_file():
                    continue
                try:
                    files[f] = Path(cwd, f).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    skipped.append(f)
            if not files:
                why = "só alterou arquivos binários ou removidos, que não vão para o PR"
                chat.post(f"🟡 **{card['id']}** — o Claude Code {why}. Nenhum PR foi aberto.")
                return {"cost": cost, **transition(card["status"], "WAITING_HUMAN",
                                                   "@Agent-Coder", f"Executor {why}")}
            extra = (f"\n\n### Claude Code\n{run['summary']}\n\n"
                     f"Estimated cost (API-equivalent): ${run['cost_usd']:.4f} · {run['duration_s']:.0f}s")
            if skipped:
                extra += ("\n\nBinary files not included in this PR (add them manually): "
                          + ", ".join(f"`{b}`" for b in skipped))
        github.create_branch(branch)
        for path, content in files.items():
            github.put_file(branch, path, content, f"{_conventional_title(card['title'])} (#{number})")
        body = (f"Closes #{number}\n\nOpened by **@Agent-Coder** after human approval in "
                f"{getattr(chat, 'name', 'chat')}.\n\n"
                f"Card `{card['id']}` · estimated cost so far (API-equivalent): "
                f"${card.get('cost', {}).get('usd', 0) + cost.get('usd', 0):.4f}{extra}")
        url = github.open_pull_request(branch, _conventional_title(card["title"]), body)
        # With GitHubIssueChat on this very issue, chat.post already is the issue comment.
        if not _chat_is_issue_thread(chat, github, number):
            github.comment(number, f"🤖 Pull request opened: {url}")
        chat.post(f"🟢 PR aberto para **{card['id']}**: {url}")
        return {"assignee": "@Agent-Coder", "branch": branch, "cost": cost, "pull_request_url": url,
                "plan": [{**s, "done": True} for s in card["plan"]],
                **transition(card["status"], "IN_REVIEW_PR", "@Agent-Coder", "Pull request opened")}

    g = StateGraph(Card)
    g.add_node("start_work", start_work)
    g.add_node("plan", plan)
    g.add_node("ask_human", ask_human)
    g.add_node("wait_human", wait_human)
    g.add_node("open_pr", open_pr)
    g.add_edge(START, "start_work")
    g.add_edge("start_work", "plan")
    g.add_edge("plan", "ask_human")
    g.add_edge("ask_human", "wait_human")
    g.add_conditional_edges("wait_human", route_after_reply, ["open_pr", "plan", END])
    g.add_edge("open_pr", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
