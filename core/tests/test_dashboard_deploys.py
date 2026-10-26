# SPDX-License-Identifier: FSL-1.1-ALv2
"""The deploy panel's pure helpers (dashboard/index.html), run in Node when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def long_wait_count(deploys):
    text = HTML.read_text(encoding="utf-8")
    start = text.index("function safeGithubUrl")
    end = text.index("// cards:")
    script = text[start:end] + "\nconsole.log(JSON.stringify(longWaitCount(JSON.parse(process.argv[1]))));"
    out = subprocess.run([NODE, "-e", script, json.dumps(deploys)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_only_the_deploys_the_server_flags_count_as_long_waits():
    deploys = [{"run_id": 1, "long_wait": True}, {"run_id": 2, "long_wait": False}, {"run_id": 3}]
    assert long_wait_count(deploys) == 1


def test_no_deploys_means_no_long_waits():
    assert long_wait_count([]) == 0
