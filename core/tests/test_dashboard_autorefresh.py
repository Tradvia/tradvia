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
