# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Hello World hybrid flow:

GitHub issue -> @Agent-Architect plans -> question in Discord -> human approves
-> @Agent-Coder creates a branch, commits the file and opens a pull request.

Card statuses follow core/contracts/card-state.v1.json.
"""

from __future__ import annotations

import functools
import inspect
import os
import re
import unicodedata
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .budget import (Budget, check, format_monthly, format_usage, monthly_check, run_limits,
                     warning_notice)
from .checkpoints import make_checkpointer
from .claude_code import StagnationError, _timeout, exec_max_turns
from .decisions import classify_reply
from .guardrails import block_message, review
from .integrations import Chat, GitHub
from .llm import architect_plan
from .policy import Policy, role_of
from .risk import classify
from .state import Card, add_usage, transition
from .tracing import Tracer, get_tracer

MAX_REPLANS = 3  # default; override with TRADVIA_MAX_REPLANS
_CONVENTIONAL = re.compile(r"(feat|fix|docs|ci|chore|refactor|test|perf|build)(\([^)]*\))?!?:")


def max_replans() -> int:
    """Plan adjustments allowed per card: TRADVIA_MAX_REPLANS (positive integer), default 3."""
    try:
        n = int(os.environ.get("TRADVIA_MAX_REPLANS", ""))
    except ValueError:
        return MAX_REPLANS
    return n if n > 0 else MAX_REPLANS


def tokens_line(cost: dict | None) -> str:
    """`Tokens: 12 345 in · 3 210 out · 80 000 cache` (space as thousands separator).

    Parsed back by parse_tokens() in events.py and parseTokens() in dashboard/index.html.
    """
    c = cost or {}

    def fmt(key: str) -> str:
        return f"{int(c.get(key) or 0):,}".replace(",", " ")

    return f"Tokens: {fmt('tokens_in')} in · {fmt('tokens_out')} out · {fmt('tokens_cache')} cache"


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


def _is_protected(path: str) -> bool:
    try:
        safe_path(path)
    except ValueError:
        return True
    return False


def is_scratch(path: str) -> bool:
    """Executor scratch: inside a dot directory (.tmpchk, .cache, .claude) or a *.tmp/*.bak/*~ file."""
    parts = [s for s in path.replace("\\", "/").split("/") if s]
    return (any(d.startswith(".") for d in parts[:-1])
            or bool(parts and any(fnmatch(parts[-1], g) for g in ("*.tmp", "*.bak", "*~"))))


def _chat_is_issue_thread(chat: Chat, github: GitHub, number: int) -> bool:
    repo = getattr(chat, "repo", None)
    return (repo is not None and repo == getattr(github, "repo", None)
            and getattr(chat, "issue", None) == number)


def build_graph(github: GitHub, chat: Chat,
                planner: Callable[[str, str, int], dict] = architect_plan,
                checkpointer=None, executor=None, cwd: str = ".",
                policy: Policy | None = None, budget: Budget | None = None,
                month_spend: Callable[[], float] | None = None,
                tracer: Tracer | None = None):
    policy = policy or Policy()
    budget = budget or Budget()
    tracer = tracer or get_tracer()
    exec_params = inspect.signature(executor.execute).parameters if executor else {}
    takes_on_tool = "on_tool" in exec_params
    takes_limits = "max_turns" in exec_params and "timeout" in exec_params

    def traced(name: str, agent: str):
        """Run a node inside the span `node.<name>` of the card trace (logic unchanged)."""
        def decorate(fn):
            @functools.wraps(fn)
            def run(card: Card):
                with tracer.span(f"node.{name}", card_id=card["id"], agent=agent,
                                 **{"issue.number": card["source"]["issue_number"],
                                    "status.from": card.get("status")}) as sp:
                    out = fn(card)
                    sp.set(**{"status.to": (out or {}).get("status"),
                              "plan_error": (out or {}).get("plan_error")})
                    return out
            return run
        return decorate

    def tool_span(name: str, summary: str) -> None:
        with tracer.span(f"tool.{name}", tool=name, summary=summary):
            pass

    def model_name(agent: object, default: str) -> str:
        return str(getattr(agent, "name", None) or getattr(agent, "__name__", None) or default)

    def step_usage(steps: int, cost: dict) -> dict:
        return {"steps": steps, "minutes": (cost.get("duration_s") or 0) / 60,
                "usd": cost.get("usd") or 0.0}

    def stop_budget(card: Card, by: str) -> dict:
        """100% of a limit: comment the consumption on the issue and wait for a human."""
        number = card["source"]["issue_number"]
        text = (f"🛑 **{card['id']}** — execution budget exhausted; the agent was stopped. "
                f"{format_usage(card.get('usage'), budget)}. Raise `budget` in `.tradvia.yml` "
                "or split the card, then restart it (remove and add the `agent:ready` label).")
        if _chat_is_issue_thread(chat, github, number):
            chat.post(text)
        else:
            github.comment(number, text)
        return transition(card["status"], "WAITING_HUMAN", by, "Budget exhausted")

    def say(card: Card, text: str) -> None:
        number = card["source"]["issue_number"]
        if _chat_is_issue_thread(chat, github, number):
            chat.post(text)
        else:
            github.comment(number, text)

    warned_months: set[str] = set()

    def monthly_gate(card: Card) -> dict | None:
        """Project ceiling, checked only when a card starts; None lets the card start."""
        cap = policy.monthly_usd
        if month_spend is None or cap is None:
            return None
        try:
            spent = float(month_spend())
        except Exception as e:  # never loosen the lock: an unreadable spend stops the start
            say(card, f"🛑 **{card['id']}** — could not read this month's spend ({type(e).__name__}) "
                      "to check the monthly budget; the card was not started. Restart it "
                      "(remove and add the `agent:ready` label) once the store is reachable.")
            return {"plan_error": "Monthly spend unreadable",
                    **transition(card.get("status", "BACKLOG"), "BACKLOG", "human",
                                 "Monthly spend unreadable")}
        level = monthly_check(spent, cap)
        if level == "stop":
            say(card, f"🛑 **{card['id']}** — monthly budget exhausted; no new card starts this "
                      f"month. {format_monthly(spent, cap)}. An admin raises the monthly "
                      "budget in Tradvia (Settings, Project budget), then restart the card "
                      "(remove and add the `agent:ready` label).")
            return {"plan_error": "Monthly budget exhausted",
                    **transition(card.get("status", "BACKLOG"), "BACKLOG", "human",
                                 "Monthly budget exhausted")}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if level == "warn" and month not in warned_months:
            warned_months.add(month)
            say(card, f"🟠 **{card['id']}** — {spent / cap * 100:.0f}% of the monthly budget "
                      f"used. {format_monthly(spent, cap)}. New cards stop at 100%.")
        return None

    def start_work(card: Card) -> dict:
        blocked = monthly_gate(card)
        if blocked is not None:
            return blocked
        number = card["source"]["issue_number"]
        issue = github.get_issue(number)
        return {
            "plan_error": None,
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
        level = check(card.get("usage"), budget)
        if level == "stop":
            return {"plan_error": "Budget exhausted", **stop_budget(card, "@Agent-Architect")}
        if level == "warn":
            body += "\n\n" + warning_notice(card.get("usage"), budget)
        try:
            with tracer.span("model.planner", model=model_name(planner, "planner")) as sp:
                p = planner(card["title"], body, card["source"]["issue_number"])
                c = p.get("_cost") or {}
                sp.set(usd=c.get("usd"), tokens_in=c.get("tokens_in"),
                       tokens_out=c.get("tokens_out"), tool_calls=int(p.get("_tool_calls") or 0))
        except (RuntimeError, ValueError) as e:
            why = str(e)[:500]  # the reason plus the excerpt of the answer (PlanError)
            chat.post(f"🔴 **{card['id']}** — o planejamento falhou: {why}. Nenhum plano foi "
                      "publicado. Ajuste a descrição da issue (ou divida o card) e reinicie-o "
                      "removendo e adicionando o rótulo `agent:ready`.")
            return {"plan_error": why,
                    **transition(card["status"], "WAITING_HUMAN", "@Agent-Architect",
                                 f"Planner failed: {why}")}
        return {
            "plan_error": None,
            "assignee": "@Agent-Architect",
            "plan": [{"id": i + 1, "title": s, "done": False} for i, s in enumerate(p.get("plan", []))],
            "file_path": safe_path(p["file_path"]) if p.get("file_path") else "",
            "file_content": p.get("file_content", ""),
            "pending_question": {"asked_by": "@Agent-Architect", "text": p["question"],
                                 "summary": p.get("summary", "")},
            "cost": p.get("_cost", {}),
            "plan_cost": p.get("_cost", {}),
            "usage": step_usage(1 + int(p.get("_tool_calls") or 0), p.get("_cost") or {}),
        }

    def ask_human(card: Card) -> dict:
        q = card["pending_question"]
        steps = "\n".join(f"{s['id']}. {s['title']}" for s in card["plan"])
        target = f"Arquivo: `{card['file_path']}`\n" if card.get("file_path") else ""
        pc = card.get("plan_cost") or {}
        has_cost = any(pc.get(k) for k in ("usd", "tokens_in", "tokens_out", "tokens_cache"))
        cost_lines = (f"\nEstimated cost (API-equivalent): ${pc.get('usd', 0):.4f}\n"
                      f"{tokens_line(pc)}\n") if has_cost else ""
        text = (f"🟡 **{card['id']} — {card['title']}**\n{q.get('summary', '')}\n{steps}\n"
                f"{target}\n❓ {q['text']}\n{cost_lines}"
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
            return "risk_gate"
        if last == "feedback":
            if sum(a["decision"] == "feedback" for a in card["approvals"]) < max_replans():
                return "plan"
            return "replan_limit"
        return END

    def replan_limit(card: Card) -> dict:
        number = card["source"]["issue_number"]
        text = (f"Limite de {max_replans()} ajustes atingido. Atualize a descrição da issue com o "
                "que foi combinado e reinicie o card (remova e adicione o rótulo `agent:ready`).")
        # With GitHubIssueChat on this very issue, chat.post already is the issue comment.
        if _chat_is_issue_thread(chat, github, number):
            chat.post(text)
        else:
            github.comment(number, text)
        return transition(card["status"], "WAITING_HUMAN", "@Agent-Architect", "Replan limit reached")

    def block(card: Card, guard: dict) -> dict:
        """Guardrail hit: tell the chat, keep the reason on the card, wait for a human."""
        chat.post(block_message(card["id"], guard))
        rules = ", ".join(sorted({f["rule"] for f in guard["findings"]}))
        return {"guardrail": guard,
                **transition(card["status"], "WAITING_HUMAN", "@Agent-Coder",
                             f"Guardrail: {rules} ({guard['phase']})")}

    def _describe_action(card: Card, action: str) -> str:
        if action == "open_pull_request":
            number = card["source"]["issue_number"]
            target = f" com o arquivo `{card['file_path']}`" if card.get("file_path") else ""
            return f"criar a branch `tradvia/issue-{number}`{target} e abrir um pull request."
        return f"executar `{action}` no card {card['id']}."

    def risk_gate(card: Card) -> dict:
        """Pass straight through when the policy says free; else announce and suspend."""
        action = card.get("pending_action") or "open_pull_request"
        # Guardrail review comes first: nothing blocked reaches approval or execution.
        texts = {"card/title": card.get("title", ""),
                 "card/plan": "\n".join(s["title"] for s in card.get("plan", [])),
                 "card/action": _describe_action(card, action)}
        if card.get("file_content"):
            texts[card.get("file_path") or "card/file_content"] = card["file_content"]
        guard = {"phase": "pre_execution", **review(texts)}
        if guard["state"] == "blocked":
            return block(card, guard)
        level = classify(action)
        required = policy.risk_requirement(level)
        if required is None:
            return {"guardrail": guard,
                    "risk": {"action": action, "level": level, "state": "approved"}}
        # The gate state is keyed by the card thread and carries the project (repo) id.
        project = getattr(github, "repo", None)
        chat.post(f"🟠 **{card['id']}** — ação `{action}` (risco `{level}`) precisa de aprovação "
                  f"de quem tem o papel `{required}` ou superior. Responda **Aprovado** ou "
                  f"**Rejeitado**.\nSerá feito: {_describe_action(card, action)}")
        return {"guardrail": guard,
                "risk": {"project_id": project, "action": action, "level": level,
                         "required_role": required, "state": "pending"},
                **transition(card["status"], "WAITING_HUMAN", "@Agent-Coder",
                             f"Risk gate: {action} ({level}) needs {required}")}

    def risk_wait(card: Card) -> dict:
        # Separate from risk_gate so the announcement is not posted again on resume.
        risk = card["risk"]
        reply = interrupt({"card_id": card["id"], "project_id": risk.get("project_id"),
                           "action": risk["action"], "required_role": risk["required_role"]})
        by = reply.get("by", "human")
        decision = classify_reply(reply["text"])
        if decision == "feedback":
            return {}  # not a decision: keep waiting
        role = role_of(github, by)
        if not policy.allows_risk(risk["level"], role):
            chat.post(f"⛔ **{card['id']}** — @{by} não pode aprovar `{risk['action']}`: "
                      f"é preciso o papel `{risk['required_role']}` ou superior. "
                      "A ação continua suspensa.")
            return {}
        entry = {"by": by, "decision": decision, "comment": reply["text"],
                 "action": risk["action"], "role": role}
        if decision == "rejected":
            chat.post(f"🔴 **{card['id']}** — `{risk['action']}` rejeitada por @{by}. "
                      "Nada foi executado.")
            return {"approvals": [entry], "risk": {**risk, "state": "rejected"},
                    **transition(card["status"], "BACKLOG", by, "Risk gate: action rejected")}
        return {"approvals": [entry], "risk": {**risk, "state": "approved"},
                **transition(card["status"], "AGENT_WORKING", by, "Risk gate: action approved")}

    def route_risk_gate(card: Card) -> str:
        if card["guardrail"]["state"] == "blocked":
            return END
        return "risk_wait" if card["risk"]["state"] == "pending" else "open_pr"

    def route_risk_wait(card: Card) -> str:
        return {"approved": "open_pr", "rejected": END}.get(card["risk"]["state"], "risk_wait")

    def open_pr(card: Card) -> dict:
        number = card["source"]["issue_number"]
        branch = f"tradvia/issue-{number}"
        files = {card["file_path"]: card["file_content"]} if card.get("file_path") else {}
        extra, cost, used = "", {}, {}
        level = check(card.get("usage"), budget) if executor else "ok"
        if level == "stop":
            return stop_budget(card, "@Agent-Coder")
        if not executor and not files:
            why = ("o plano não trouxe `file_path` e nenhum executor está configurado "
                   "(use `TRADVIA_EXECUTOR=claude-code` com `TRADVIA_PLANNER=claude-code`)")
            chat.post(f"🟡 **{card['id']}** — {why}. Nenhum PR foi aberto.")
            return transition(card["status"], "WAITING_HUMAN", "@Agent-Coder", f"No executor: {why}")
        if executor:
            kwargs = {"on_tool": tool_span} if takes_on_tool else {}
            if takes_limits:  # the run cannot go past what is left of the card's budget
                turns, seconds = run_limits(card.get("usage"), budget, exec_max_turns(),
                                            _timeout())
                if turns < 1:
                    return stop_budget(card, "@Agent-Coder")
                kwargs.update(max_turns=turns, timeout=seconds)
            if level == "warn":
                kwargs["budget_notice"] = warning_notice(card.get("usage"), budget)
            try:
                with tracer.span("model.executor",
                                 model=model_name(executor, type(executor).__name__)) as sp:
                    run = executor.execute(card, cwd, **kwargs)
                    sp.set(usd=run.get("cost_usd"), tokens_in=run.get("tokens_in"),
                           tokens_out=run.get("tokens_out"),
                           tool_calls=int(run.get("tool_calls") or 0))
            except StagnationError as e:
                why = (f"o agente repetiu `{e.tool}` {e.count} vezes seguidas e a nova tentativa "
                       "com a mensagem reflexiva também falhou")
                chat.post(f"🟡 **{card['id']}** — estagnação detectada: {why}. Nenhum PR foi "
                          "aberto; revise o card e responda com um ajuste.")
                return transition(card["status"], "WAITING_HUMAN", "@Agent-Coder",
                                  f"Stagnation: {e.tool} repeated {e.count} times, "
                                  "reflective retry also failed")
            except RuntimeError as e:
                why = str(e)[:300]
                chat.post(f"🔴 **{card['id']}** — a implementação falhou: {why}. Nenhum PR foi "
                          "aberto. Ajuste a descrição da issue (ou divida o card) e reinicie-o "
                          "removendo e adicionando o rótulo `agent:ready`.")
                return transition(card["status"], "WAITING_HUMAN", "@Agent-Coder",
                                  f"Executor failed: {why}")
            scratch = [f for f in run.get("untracked_files", [])
                       if is_scratch(f) and not _is_protected(f)]
            changed = [f for f in run["changed_files"] if f not in scratch]
            blocked = []
            for f in changed:
                try:
                    safe_path(f)
                except ValueError:
                    blocked.append(f)
            cost = {"usd": run["cost_usd"], "duration_s": run["duration_s"]}
            used = step_usage(1 + int(run.get("tool_calls") or 0), cost)
            if blocked or not changed:
                if blocked:
                    why = f"alterou arquivos protegidos: {', '.join('`' + b + '`' for b in blocked)}"
                elif scratch:
                    why = ("só criou arquivos temporários ignorados: "
                           + ", ".join(f"`{s}`" for s in scratch))
                else:
                    why = "não alterou nenhum arquivo"
                chat.post(f"🟡 **{card['id']}** — o Claude Code {why}. Nenhum PR foi aberto; "
                          "revise o working tree e responda com um ajuste.")
                return {"cost": cost, "usage": used, **transition(card["status"], "WAITING_HUMAN",
                                                   "@Agent-Coder", f"Executor {why}")}
            files, skipped = {}, []
            for f in changed:
                if not Path(cwd, f).is_file():
                    continue
                try:
                    files[f] = Path(cwd, f).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    skipped.append(f)
            if not files:
                why = "só alterou arquivos binários ou removidos, que não vão para o PR"
                chat.post(f"🟡 **{card['id']}** — o Claude Code {why}. Nenhum PR foi aberto.")
                return {"cost": cost, "usage": used,
                        **transition(card["status"], "WAITING_HUMAN", "@Agent-Coder",
                                     f"Executor {why}")}
            extra = (f"\n\n### Claude Code\n{run['summary']}\n\n"
                     f"Estimated cost (API-equivalent): ${run['cost_usd']:.4f} · {run['duration_s']:.0f}s\n"
                     f"{tokens_line(run)}")
            if skipped:
                extra += ("\n\nBinary files not included in this PR (add them manually): "
                          + ", ".join(f"`{b}`" for b in skipped))
            if scratch:
                extra += ("\n\n### Ignored scratch files\n"
                          + "\n".join(f"- `{s}`" for s in scratch)
                          + "\n\nNot included in this PR; a human should review them.")
        body = (f"Closes #{number}\n\nOpened by **@Agent-Coder** after human approval in "
                f"{getattr(chat, 'name', 'chat')}.\n\n"
                f"Card `{card['id']}` · estimated cost so far (API-equivalent): "
                f"${card.get('cost', {}).get('usd', 0) + cost.get('usd', 0):.4f}\n"
                f"{format_usage(add_usage(card.get('usage'), used), budget)}{extra}")
        # Guardrail review before any branch or pull request exists.
        guard = {"phase": "pre_pr",
                 **review({**files, "pull_request/body": body,
                           "pull_request/title": _conventional_title(card["title"])})}
        if guard["state"] == "blocked":
            return {"cost": cost, "usage": used, **block(card, guard)}
        github.create_branch(branch)
        for path, content in files.items():
            github.put_file(branch, path, content, f"{_conventional_title(card['title'])} (#{number})")
        url = github.open_pull_request(branch, _conventional_title(card["title"]), body)
        # With GitHubIssueChat on this very issue, chat.post already is the issue comment.
        if not _chat_is_issue_thread(chat, github, number):
            github.comment(number, f"🤖 Pull request opened: {url}")
        chat.post(f"🟢 PR aberto para **{card['id']}**: {url}")
        return {"guardrail": guard, "assignee": "@Agent-Coder", "branch": branch, "cost": cost, "usage": used, "pull_request_url": url,
                "plan": [{**s, "done": True} for s in card["plan"]],
                **transition(card["status"], "IN_REVIEW_PR", "@Agent-Coder", "Pull request opened")}

    g = StateGraph(Card)
    for name, fn, agent in (("start_work", start_work, "human"),
                            ("plan", plan, "@Agent-Architect"),
                            ("ask_human", ask_human, "@Agent-Architect"),
                            ("wait_human", wait_human, "human"),
                            ("risk_gate", risk_gate, "@Agent-Coder"),
                            ("risk_wait", risk_wait, "human"),
                            ("open_pr", open_pr, "@Agent-Coder"),
                            ("replan_limit", replan_limit, "@Agent-Architect")):
        g.add_node(name, traced(name, agent)(fn))
    g.add_edge(START, "start_work")
    g.add_conditional_edges("start_work", lambda c: END if c.get("plan_error") else "plan",
                            ["plan", END])
    g.add_conditional_edges("plan", lambda c: END if c.get("plan_error") else "ask_human",
                            ["ask_human", END])
    g.add_edge("ask_human", "wait_human")
    g.add_conditional_edges("wait_human", route_after_reply, ["risk_gate", "plan", "replan_limit", END])
    g.add_conditional_edges("risk_gate", route_risk_gate, ["risk_wait", "open_pr", END])
    g.add_conditional_edges("risk_wait", route_risk_wait, ["risk_wait", "open_pr", END])
    g.add_edge("open_pr", END)
    g.add_edge("replan_limit", END)
    return g.compile(checkpointer=checkpointer or make_checkpointer())
