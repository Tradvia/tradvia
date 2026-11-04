# SPDX-License-Identifier: FSL-1.1-ALv2
"""Recurring cards (#133): a project's schedule creates a card at the right time, not started.

The schedules are a setting of the project on Tradvia (ADR 0018, key `schedules`), never a file.
Each one creates a card (an issue) once per slot: daily, weekly or monthly, at an hour in UTC.
The card is only created. Starting it, and approving its plan, stay with a person, under the
budget and risk rules of every other card (ADR 0004, ADR 0006).

The slot a schedule last created is kept in the store (key `schedules_state`) and written
*before* the issue is created, so the same slot never creates a card twice, even across a
restart. A new or changed schedule starts from its current slot: it waits for the next one
instead of firing for a time that already passed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

SETTING_KEY = "schedules"
STATE_KEY = "schedules_state"
EVERY = ("day", "week", "month")
MAX_ITEMS = 20
MAX_TITLE = 200
MAX_BODY = 4000
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MARK = "<!-- tradvia:schedule id={id} slot={slot} -->"


def _int(data: dict, key: str, low: int, high: int, default: int | None = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number from {low} to {high}")
    return value


def validate_item(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("each schedule must be an object")
    allowed = {"id", "title", "body", "every", "hour", "weekday", "day", "enabled"}
    unknown = sorted(str(k) for k in data if k not in allowed)
    if unknown:
        raise ValueError(f"unknown key(s): {', '.join(unknown)}")
    sid = data.get("id")
    if not isinstance(sid, str) or not _ID.match(sid):
        raise ValueError("id must be 1-40 lowercase letters, digits or dashes")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
        raise ValueError(f"title is required (at most {MAX_TITLE} characters)")
    body = data.get("body", "")
    if not isinstance(body, str) or len(body) > MAX_BODY:
        raise ValueError(f"body must be text (at most {MAX_BODY} characters)")
    every = data.get("every")
    if every not in EVERY:
        raise ValueError("every must be 'day', 'week' or 'month'")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    item = {"id": sid, "title": title.strip(), "body": body, "every": every,
            "hour": _int(data, "hour", 0, 23, 9), "enabled": enabled}
    if every == "week":
        item["weekday"] = _int(data, "weekday", 0, 6, 0)  # 0 = Monday
    if every == "month":
        item["day"] = _int(data, "day", 1, 28, 1)  # up to 28: every month has it
    return item


def validate_setting(data: object) -> dict[str, Any]:
    """The schedules to store, or ValueError with a message safe to show."""
    if not isinstance(data, dict) or set(data) - {"items"}:
        raise ValueError("send an object with 'items'")
    raw = data.get("items") or []
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise ValueError(f"items must be a list of at most {MAX_ITEMS} schedules")
    items = [validate_item(i) for i in raw]
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("each schedule needs its own id")
    return {"items": items}


def read_setting(value: object) -> list[dict[str, Any]]:
    """The stored schedules, or none when the value is missing or broken. Never raises."""
    try:
        return validate_setting(value)["items"] if value is not None else []
    except (ValueError, TypeError):
        return []


def latest_slot(item: dict[str, Any], now: float) -> datetime:
    """The most recent time, at or before `now`, at which `item` is due (UTC)."""
    at = datetime.fromtimestamp(now, timezone.utc)
    if item["every"] == "day":
        slot = at.replace(hour=item["hour"], minute=0, second=0, microsecond=0)
        return slot if slot <= at else slot - timedelta(days=1)
    if item["every"] == "week":
        slot = at.replace(hour=item["hour"], minute=0, second=0, microsecond=0)
        slot -= timedelta(days=(slot.weekday() - item["weekday"]) % 7)
        return slot if slot <= at else slot - timedelta(days=7)
    slot = at.replace(day=item["day"], hour=item["hour"], minute=0, second=0, microsecond=0)
    if slot <= at:
        return slot
    year, month = (slot.year, slot.month - 1) if slot.month > 1 else (slot.year - 1, 12)
    return slot.replace(year=year, month=month)


def next_slot(item: dict[str, Any], now: float) -> datetime:
    """The first time after `now` at which `item` is due (UTC)."""
    last = latest_slot(item, now)
    if item["every"] == "day":
        return last + timedelta(days=1)
    if item["every"] == "week":
        return last + timedelta(days=7)
    year, month = (last.year, last.month + 1) if last.month < 12 else (last.year + 1, 1)
    return last.replace(year=year, month=month)


def slot_id(slot: datetime) -> str:
    return slot.strftime("%Y-%m-%dT%H:00Z")


def due(items: list[dict[str, Any]], state: dict[str, str], now: float) -> list[tuple[dict, str]]:
    """`(item, slot)` for each enabled schedule whose latest slot was not created yet."""
    out = []
    for item in items:
        if not item.get("enabled", True):
            continue
        slot = slot_id(latest_slot(item, now))
        if state.get(item["id"]) != slot:
            out.append((item, slot))
    return out


def baseline(items: list[dict[str, Any]], before: list[dict[str, Any]], state: dict[str, str],
             now: float) -> dict[str, str]:
    """State after saving `items`: a new or changed schedule starts at its current slot (it
    waits for the next one); an unchanged one keeps what it created; a removed one is dropped."""
    old = {i["id"]: i for i in before}
    out = {}
    for item in items:
        if old.get(item["id"]) == item and item["id"] in state:
            out[item["id"]] = state[item["id"]]
        else:
            out[item["id"]] = slot_id(latest_slot(item, now))
    return out


def card_text(item: dict[str, Any], slot: str) -> tuple[str, str]:
    """Title and body of the card a schedule creates for `slot`."""
    title = f"{item['title']} — {slot[:10]}"
    body = (f"{item['body']}\n\n" if item["body"] else "") + (
        f"Card criado pelo agendamento `{item['id']}` ({slot}). Ele não foi iniciado: "
        "inicie-o no painel quando quiser; o plano continua precisando de aprovação.\n\n"
        + MARK.format(id=item["id"], slot=slot))
    return title, body
