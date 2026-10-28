# SPDX-License-Identifier: FSL-1.1-ALv2
"""Board column titles are written words in every language, never the internal state keys."""

import re
from pathlib import Path

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
STATES = ("BACKLOG", "AGENT_WORKING", "WAITING_HUMAN", "BLOCKED", "IN_REVIEW_PR", "DONE")


def test_every_language_has_a_written_title_for_every_column():
    text = HTML.read_text(encoding="utf-8")
    for state in STATES:
        titles = re.findall(rf'COL_{state}: "([^"]*)"', text)
        assert len(titles) >= 2, state  # pt and en
        for title in titles:
            assert title and title != state and "_" not in title, (state, title)
