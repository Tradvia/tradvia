# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Model routing through LiteLLM: reasoning models plan, cheaper models code."""

from __future__ import annotations

import json
import os
import re

ARCHITECT_PROMPT = """You are @Agent-Architect in a software team.
Read the GitHub issue and propose the smallest change that solves it, as ONE new file.
Answer ONLY with JSON, no markdown fences:
{"summary": "<one sentence>",
 "plan": ["<step>", "..."],
 "file_path": "<path relative to repo root>",
 "file_content": "<full file content>",
 "question": "<one short question for the human to approve or adjust the plan>"}"""


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


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def architect_plan(title: str, body: str, number: int) -> dict:
    """Return a plan dict. Uses TRADVIA_PLANNER_MODEL, or an offline stub when
    TRADVIA_OFFLINE=1 (no API key needed, for demos and tests)."""
    if os.getenv("TRADVIA_OFFLINE") == "1":
        return _offline_plan(title, body, number)

    import litellm  # imported lazily so offline mode needs no key

    model = os.getenv("TRADVIA_PLANNER_MODEL", "anthropic/claude-sonnet-5-5")
    resp = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": ARCHITECT_PROMPT},
                  {"role": "user", "content": f"Issue #{number}: {title}\n\n{body}"}],
        max_tokens=2000,
    )
    plan = _parse_json(resp.choices[0].message.content)
    usage = getattr(resp, "usage", None)
    try:
        usd = float(litellm.completion_cost(completion_response=resp))
    except Exception:
        usd = 0.0
    plan["_cost"] = {
        "usd": usd,
        "tokens_in": getattr(usage, "prompt_tokens", 0) or 0,
        "tokens_out": getattr(usage, "completion_tokens", 0) or 0,
        "tokens_cache": 0,
    }
    for key in ("file_path", "file_content", "question"):
        if not plan.get(key):
            raise ValueError(f"Planner response is missing '{key}'")
    return plan
