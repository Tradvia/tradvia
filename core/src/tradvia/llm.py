# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Model routing through LiteLLM: reasoning models plan, cheaper models code."""

from __future__ import annotations

import json
import os
import re

from . import prompts
from .connections import redact

EXCERPT_CHARS = 200

ARCHITECT_PROMPT = prompts.text("architect")  # versioned in prompts/ (#125)


def _offline_plan(title: str, body: str, number: int) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "task"
    return {
        "summary": f"Create a hello-world note for issue #{number}.",
        "plan": ["Read the issue", "Write a markdown note", "Open a pull request"],
        "file_path": f"docs/agent-notes/{number}-{slug}.md",
        "file_content": f"# {title}\n\nCreated by @Agent-Coder for issue #{number}.\n\n{body}\n",
        "question": "Posso criar esse arquivo e abrir o Pull Request?",
        "_cost": {"usd": 0.0, "tokens_in": 0, "tokens_out": 0, "tokens_cache": 0},
    }


class PlanError(ValueError):
    """A planner answer that cannot be used as a plan.

    `reason` says what is wrong, `excerpt` quotes the text around the problem, and `answer`
    is the whole answer, which the repair prompt quotes.
    """

    def __init__(self, reason: str, answer: str, excerpt: str):
        self.reason, self.answer, self.excerpt = reason, answer, excerpt
        super().__init__(f"{reason}. {excerpt}")


def _candidate(text: str) -> str:
    """The JSON object inside a planner answer: markdown fences and prose around it are dropped."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1]


def answer_excerpt(text: str, pos: int | None = None) -> str:
    """About EXCERPT_CHARS of `text`: around `pos` when given, else from its start."""
    if not text.strip():
        return "The answer was empty"
    begin = 0 if pos is None else max(0, pos - EXCERPT_CHARS // 2)
    quoted = " ".join(redact(text[begin:begin + EXCERPT_CHARS]).split())
    lead = "…" if begin > 0 else ""
    tail = "…" if begin + EXCERPT_CHARS < len(text) else ""
    where = "Near the error" if pos is not None else "The answer began"
    return f"{where}: «{lead}{quoted}{tail}»"


def parse_answer(text: str, what: str) -> dict:
    """The JSON object of a planner answer; when it is not JSON, PlanError says what came back."""
    answer = text or ""
    candidate = _candidate(answer)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        excerpt = answer_excerpt(candidate, e.pos) if candidate else answer_excerpt(answer)
        raise PlanError(f"{what} is not valid JSON: {e}", answer, excerpt) from None


def _parse_json(text: str) -> dict:
    return json.loads(_candidate(text))


def architect_plan(title: str, body: str, number: int) -> dict:
    """Return a plan dict. Uses TRADVIA_PLANNER_MODEL, or an offline stub when
    TRADVIA_OFFLINE=1 (no API key needed, for demos and tests)."""
    if os.getenv("TRADVIA_OFFLINE") == "1":
        return _offline_plan(title, body, number)

    import litellm  # imported lazily so offline mode needs no key

    model = os.getenv("TRADVIA_PLANNER_MODEL", "anthropic/claude-sonnet-5-5")
    resp = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": prompts.active_text("architect")},
                  {"role": "user", "content": f"Issue #{number}: {title}\n\n{body}"}],
        max_tokens=2000,
    )
    content = resp.choices[0].message.content or ""
    plan = parse_answer(content, "Planner response")
    usage = getattr(resp, "usage", None)
    try:
        usd = float(litellm.completion_cost(completion_response=resp))
    except Exception:
        usd = 0.0
    plan["_prompt"] = prompts.active_ref("architect")
    plan["_cost"] = {
        "usd": usd,
        "tokens_in": getattr(usage, "prompt_tokens", 0) or 0,
        "tokens_out": getattr(usage, "completion_tokens", 0) or 0,
        "tokens_cache": 0,
    }
    for key in ("file_path", "file_content", "question"):
        if not plan.get(key):
            raise PlanError(f"Planner response is missing '{key}'", content,
                            answer_excerpt(content))
    return plan
