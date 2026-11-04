# SPDX-License-Identifier: FSL-1.1-ALv2
"""Board column titles are written words in every language, never the internal state keys."""

STATES = ("BACKLOG", "AGENT_WORKING", "WAITING_HUMAN", "BLOCKED", "IN_REVIEW_PR", "DONE")


def test_every_language_has_a_written_title_for_every_column():
    from panel_i18n import LANGS, panel_strings

    for state in STATES:
        titles = [panel_strings(code).get(f"COL_{state}", "") for code in LANGS]
        for title in titles:
            assert title and title != state and "_" not in title, (state, title)
