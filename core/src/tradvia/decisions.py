# SPDX-License-Identifier: FSL-1.1-ALv2
"""Reads a human reply as a decision on a plan: approved, rejected or feedback.

Kept apart from graph.py so integrations.py can use the same words without an import cycle.
"""

import re
import unicodedata

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
