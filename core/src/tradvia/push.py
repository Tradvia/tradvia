# SPDX-License-Identifier: FSL-1.1-ALv2
"""Web Push for the pending inbox (ADR 0009): VAPID, no paid service.

The notification carries only the project, the card number and the kind of event: never a title,
a comment or any other text from GitHub. The VAPID private key is read from the environment,
used by `WebPushSender` and never logged or returned.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Protocol

from .pending import KINDS
from .store import Store

PUBLIC_ENV = "TRADVIA_VAPID_PUBLIC_KEY"
PRIVATE_ENV = "TRADVIA_VAPID_PRIVATE_KEY"
SUBJECT_ENV = "TRADVIA_VAPID_SUBJECT"
GONE = (404, 410)  # the push service says the subscription no longer exists
TTL = 24 * 3600  # seconds the push service keeps an undelivered message


class PushError(Exception):
    """The push service refused a message; only the HTTP status is kept."""

    def __init__(self, status: int | None = None) -> None:
        super().__init__(f"push failed ({status})")
        self.status = status


class Sender(Protocol):
    def send(self, sub: dict[str, Any], payload: dict[str, Any]) -> None:
        """Deliver `payload` to `sub` (`endpoint`, `p256dh`, `auth`); raise PushError on failure."""


def public_key(env: Mapping[str, str] | None = None) -> str | None:
    """The VAPID public key for the browser, or None when push is off (either key unset)."""
    env = os.environ if env is None else env
    pub, priv = env.get(PUBLIC_ENV, "").strip(), env.get(PRIVATE_ENV, "").strip()
    return pub if pub and priv else None


class WebPushSender:
    """Sends with `pywebpush` (RFC 8030 + RFC 8291 encryption + RFC 8292 VAPID)."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self._private = env.get(PRIVATE_ENV, "").strip()
        domain = env.get("TRADVIA_DOMAIN", "").strip()
        self._subject = env.get(SUBJECT_ENV, "").strip() or (
            f"https://{domain}" if domain else "https://tradvia.org"
        )

    def __repr__(self) -> str:  # never show the key
        return "WebPushSender()"

    def send(self, sub: dict[str, Any], payload: dict[str, Any]) -> None:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(payload, separators=(",", ":")),
                vapid_private_key=self._private,
                vapid_claims={"sub": self._subject},
                ttl=TTL,
                timeout=10,
            )
        except WebPushException as e:
            response = getattr(e, "response", None)
            raise PushError(getattr(response, "status_code", None)) from None
        except Exception:  # network or key errors: the message may carry the endpoint
            raise PushError(None) from None


def default_sender(env: Mapping[str, str] | None = None) -> Sender | None:
    """A `WebPushSender` when both VAPID keys are set, else None (push is off)."""
    return WebPushSender(env) if public_key(env) else None


def payload(project: str, number: int, kind: str) -> dict[str, Any]:
    """What the service worker receives: project slug, card number and event kind. Nothing else."""
    return {"project": project, "card": number, "kind": kind}


def kind_enabled(prefs: dict[str, bool] | None, kind: str) -> bool:
    """No saved choice means every event is on."""
    return True if prefs is None else bool(prefs.get(kind, True))


def valid_prefs(data: object) -> dict[str, bool] | None:
    """The preferences map when it only has known kinds with boolean values, else None."""
    if not isinstance(data, dict) or any(
        k not in KINDS or not isinstance(v, bool) for k, v in data.items()
    ):
        return None
    return dict(data)


def notify(
    db: Store,
    sender: Sender,
    project_id: int,
    project: str,
    transitions: Mapping[int, str],
    can_answer: Callable[[str, str], bool],
) -> int:
    """Push one message per card that entered a pending kind, to who may answer it.

    `transitions` maps a card number to its new kind. `can_answer(login, kind)` says whether the
    person's role (from the cached role of their session) may answer that kind; it must fail
    closed. A subscriber is skipped when the event is off in their preferences. Returns the
    number of messages sent. Errors never escape: a 404/410 deletes the dead subscription, any
    other failure is dropped without logging the endpoint.
    """
    if not transitions:
        return 0
    subs = db.list_subscriptions(project_id)
    prefs: dict[str, dict[str, bool] | None] = {}
    allowed: dict[tuple[str, str], bool] = {}
    sent = 0
    for number, kind in sorted(transitions.items()):
        for sub in subs:
            login = sub["login"]
            if login not in prefs:
                prefs[login] = db.get_prefs(project_id, login)
            if not kind_enabled(prefs[login], kind):
                continue
            if (login, kind) not in allowed:
                try:
                    allowed[(login, kind)] = bool(can_answer(login, kind))
                except Exception:
                    allowed[(login, kind)] = False
            if not allowed[(login, kind)]:
                continue
            try:
                sender.send(sub, payload(project, number, kind))
                sent += 1
            except PushError as e:
                if e.status in GONE:
                    db.delete_subscription(sub["endpoint"])
            except Exception:
                pass
    return sent
