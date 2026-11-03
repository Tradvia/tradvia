# SPDX-License-Identifier: FSL-1.1-ALv2
"""Canary of a new prompt or model on a share of a project's cards (#128).

The setting lives on Tradvia, per project (ADR 0018), under `canary`:
`{"percent": 0-100, "prompts": [names with a candidate version], "model": str | None}`.
Which cards take part is decided by the card number alone, so a card keeps its side across
plans and adjustments. Reverting sets `percent` to 0 and keeps the rest, so the record stays
(and the audit log has who did it). Plans and pull requests say which side they ran on
(`Prompt: plan@2 (canary)`), which is what `compare` reads to show both sides.
"""

from __future__ import annotations

import hashlib
import re
from statistics import median
from typing import Any

from . import prompts

SETTING_KEY = "canary"
MAX_MODEL = 100
_MODEL_RE = re.compile(r"^[\w./:@-]+$")
_PROMPT_LINE = re.compile(r"^Prompt: (.+)$", re.M)


def validate_setting(data: object) -> dict[str, Any]:
    """The canary to store, or ValueError with a message safe to show."""
    if not isinstance(data, dict):
        raise ValueError("send an object with percent, prompts and model")
    unknown = sorted(str(k) for k in data if k not in ("percent", "prompts", "model"))
    if unknown:
        raise ValueError(f"unknown key(s): {', '.join(unknown)}")
    percent = data.get("percent", 0)
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError("percent must be a whole number from 0 to 100")
    names = data.get("prompts") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("prompts must be a list of prompt names")
    available = prompts.candidates()
    missing = sorted(n for n in names if n not in available)
    if missing:
        raise ValueError(f"no candidate version for: {', '.join(missing)}")
    model = data.get("model") or None
    if model is not None and (not isinstance(model, str) or len(model) > MAX_MODEL
                              or not _MODEL_RE.match(model)):
        raise ValueError("model must be a model id such as anthropic/claude-sonnet-5-5")
    if percent and not names and not model:
        raise ValueError("a canary needs a candidate prompt or a model to test")
    return {"percent": percent, "prompts": sorted(set(names)), "model": model}


def read_setting(value: object) -> dict[str, Any] | None:
    """The stored canary when it is valid and active (percent above 0), else None. Never raises:
    a candidate that left the code, or a broken value, means no canary."""
    try:
        clean = validate_setting(value)
    except (ValueError, TypeError):
        return None
    return clean if clean["percent"] > 0 else None


def chosen(repo: str, number: int, percent: int) -> bool:
    """Whether card `number` of `repo` runs on the canary: a stable bucket from 0 to 99."""
    if percent <= 0:
        return False
    digest = hashlib.sha256(f"{repo.lower()}#{int(number)}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < percent


def side_of(comments: list[dict[str, Any]]) -> str | None:
    """'canary' or 'stable' from the card's latest `Prompt:` line, or None without one."""
    for c in reversed(comments):
        m = _PROMPT_LINE.search(c.get("body") or "")
        if m:
            return "canary" if "(canary" in m.group(1) else "stable"
    return None


def compare(cards: list[dict[str, Any]], pulls: list[dict[str, Any]]) -> dict[str, dict]:
    """Both sides next to each other: cards, merged pull requests and the median plan cost.

    `cards`: `[{number, comments}]` with comment bodies and their `cost_usd`;
    `pulls`: `[{number(card), state, merged}]`.
    """
    out = {s: {"cards": 0, "merged_prs": 0, "plan_costs": []} for s in ("stable", "canary")}
    merged = {p["number"] for p in pulls if p.get("merged")}
    for card in cards:
        side = side_of(card.get("comments") or [])
        if side is None:
            continue
        out[side]["cards"] += 1  # a card counts on the side of its latest plan
        out[side]["merged_prs"] += card["number"] in merged
        for c in card.get("comments") or []:  # each plan's cost on the side it ran on
            plan_side = side_of([c])
            if plan_side is not None and c.get("cost_usd") is not None:
                out[plan_side]["plan_costs"].append(float(c["cost_usd"]))
    for side in out.values():
        costs = side.pop("plan_costs")
        side["median_plan_usd"] = round(median(costs), 4) if costs else None
    return out
