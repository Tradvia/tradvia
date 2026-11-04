# SPDX-License-Identifier: FSL-1.1-ALv2
"""The panel's "Mark as done" for a person request (request_human, #120)."""

from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(encoding="utf-8")


def test_strings_exist_in_both_languages():
    from panel_i18n import has_everywhere

    for key in ("kind_human_request", "humanRequest", "humanRequestHint", "humanDone",
                "humanDoneConfirm", "humanLive", "act_human_done"):
        assert has_everywhere(key)


def test_the_button_posts_to_the_human_done_action():
    assert '"/cards/" + item.number + "/human-done"' in HTML
    assert 'it.kind === "human_request"' in HTML


def test_only_http_links_become_the_live_session_link():
    assert "url.protocol === \"https:\" || url.protocol === \"http:\"" in HTML
