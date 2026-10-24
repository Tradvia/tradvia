# SPDX-License-Identifier: FSL-1.1-ALv2
"""The dashboard's runs pagination (dashboard/index.html), run in Node when available."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
TEXT = HTML.read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def paginate(count, page, size=20):
    start = TEXT.index("// pagination (pure)")
    end = TEXT.index("// end pagination")
    script = (TEXT[start:end] + "\nconst items = Array.from({length: Number(process.argv[1])}, (_, i) => i);"
              "\nconst r = paginate(items, Number(process.argv[2]), Number(process.argv[3]));"
              "\nconsole.log(JSON.stringify(r));")
    out = subprocess.run([NODE, "-e", script, str(count), str(page), str(size)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
@pytest.mark.parametrize("page,rows", [(0, 20), (1, 20), (2, 5)])
def test_slices_45_items_into_three_pages(page, rows):
    r = paginate(45, page)
    assert (len(r["rows"]), r["page"], r["pages"], r["total"]) == (rows, page, 3, 45)
    assert r["rows"][0] == page * 20


@needs_node
def test_out_of_range_page_is_clamped():
    assert paginate(45, -3)["page"] == 0
    r = paginate(45, 99)
    assert (r["page"], len(r["rows"])) == (2, 5)


@needs_node
def test_empty_list_has_one_empty_page():
    r = paginate(0, 5)
    assert (r["rows"], r["pages"], r["page"], r["total"]) == ([], 1, 0, 0)


@needs_node
def test_exactly_one_page_of_items():
    r = paginate(20, 0)
    assert (len(r["rows"]), r["pages"]) == (20, 1)


def test_board_columns_scroll_internally():
    rule = re.search(r"\.col \{([^}]*)\}", TEXT).group(1)
    assert "max-height" in rule
    assert "overflow-y: auto" in rule


def test_render_runs_uses_paginate():
    body = TEXT[TEXT.index("function renderRuns"):TEXT.index("function render()")]
    assert "paginate(" in body


def test_card_rendering_selectors_are_intact():
    for state in ("AGENT_WORKING", "WAITING_HUMAN", "BLOCKED", "IN_REVIEW_PR", "DONE"):
        assert f".col.{state} h3" in TEXT
    assert ".card" in TEXT
    assert "function renderBacklogColumn" in TEXT
