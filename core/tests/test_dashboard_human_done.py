# SPDX-License-Identifier: FSL-1.1-ALv2
"""The panel's "Mark as done" for a person request (request_human, #120)."""

import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(encoding="utf-8")


def keys(lang):
    block = HTML[HTML.index(f"  {lang}: {{"):]
    block = block[:block.index("\n  },")] if "\n  }," in block else block
    return set(re.findall(r"(\w+):", block))


def test_strings_exist_in_both_languages():
    for key in ("kind_human_request", "humanRequest", "humanRequestHint", "humanDone",
                "humanDoneConfirm", "humanLive", "act_human_done"):
        assert key in keys("pt"), key
        assert key in keys("en"), key


def test_the_button_posts_to_the_human_done_action():
    assert '"/cards/" + item.number + "/human-done"' in HTML
    assert 'it.kind === "human_request"' in HTML


def test_only_http_links_become_the_live_session_link():
    assert "url.protocol === \"https:\" || url.protocol === \"http:\"" in HTML
