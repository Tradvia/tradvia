# SPDX-License-Identifier: FSL-1.1-ALv2
"""The panel's strings per language, read from dashboard/i18n/<lang>.json (ADR 0011)."""

import json
from pathlib import Path

I18N = Path(__file__).resolve().parents[2] / "dashboard" / "i18n"
LANGS = ("pt", "en", "es")


def panel_strings(code):
    """The keys index.html's t() sees for one language: the "app." entries, prefix dropped."""
    data = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))
    return {k[len("app."):]: v for k, v in data.items() if k.startswith("app.")}


def has_everywhere(*keys):
    """Every key exists, non-empty, in every language."""
    for code in LANGS:
        strings = panel_strings(code)
        for key in keys:
            assert str(strings.get(key, "")).strip(), (code, key)
    return True
