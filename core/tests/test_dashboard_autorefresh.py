# SPDX-License-Identifier: FSL-1.1-ALv2
"""The dashboard's auto-refresh rule (dashboard/index.html), run in Node when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def due(**state):
    text = HTML.read_text(encoding="utf-8")
    start = text.index("// auto-refresh (pure)")
    end = text.index("// end auto-refresh")
    base = {"enabled": True, "hidden": False, "editing": False, "busy": False,
            "store": True, "lastAt": 0, "now": 31000}
    script = text[start:end] + "\nconsole.log(JSON.stringify(autoRefreshDue(JSON.parse(process.argv[1]))));"
    out = subprocess.run([NODE, "-e", script, json.dumps({**base, **state})],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


IDLE_MS = 180000


def editing(**state):
    text = HTML.read_text(encoding="utf-8")
    start = text.index("// auto-refresh (pure)")
    end = text.index("// end auto-refresh")
    base = {"focused": False, "draft": False, "lastEditAt": None, "now": 1000000}
    script = text[start:end] + "\nconsole.log(JSON.stringify(editingActive(JSON.parse(process.argv[1]))));"
    out = subprocess.run([NODE, "-e", script, json.dumps({**base, **state})],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_focused_field_pauses_even_with_an_old_edit():
    assert editing(focused=True, draft=True, lastEditAt=0) is True
    assert editing(focused=True) is True


def test_active_typing_in_an_unfocused_draft_pauses():
    assert editing(draft=True, lastEditAt=999000) is True


def test_abandoned_unfocused_draft_does_not_pause_forever():
    assert editing(draft=True, lastEditAt=1000000 - IDLE_MS - 1000) is False


def test_no_draft_and_no_focus_never_pauses():
    assert editing(lastEditAt=999999) is False
    assert editing() is False


def test_idle_limit_boundary():
    assert editing(draft=True, lastEditAt=1000000 - IDLE_MS + 1) is True
    assert editing(draft=True, lastEditAt=1000000 - IDLE_MS) is False


def test_reloads_every_30s_from_the_store_and_60s_through_the_proxy():
    assert due() is True
    assert due(now=29000) is False
    assert due(store=False, now=31000) is False
    assert due(store=False, now=61000) is True
    assert due(lastAt=None) is True


@pytest.mark.parametrize("flag", ["hidden", "editing", "busy"])
def test_never_while_hidden_typing_or_loading(flag):
    assert due(**{flag: True}) is False


def test_off_when_disabled():
    assert due(enabled=False) is False
