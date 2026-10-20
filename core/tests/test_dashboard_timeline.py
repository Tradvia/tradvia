# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Runs the dashboard timeline functions (dashboard/index.html) in Node, when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

PLAN = "🟡 **CARD-1 — T**\nResumo\n1. passo\n\n❓ Posso seguir?\nResponda **Aprovado**, **Rejeitado** ou escreva um ajuste."


def ev(minute, login, body, **extra):
    return {"event": "commented", "created_at": f"2026-01-01T00:{minute:02d}:00Z",
            "user": {"login": login, "type": "Bot" if "[bot]" in login else "User"},
            "body": body, **extra}


def timeline(events):
    text = HTML.read_text(encoding="utf-8")
    start = text.index("function safeGithubUrl")
    end = text.index("// cards:")
    script = text[start:end] + "\nconsole.log(JSON.stringify(buildTimeline(JSON.parse(process.argv[1]))));"
    out = subprocess.run([NODE, "-e", script, json.dumps(events)],
                         capture_output=True, text=True, check=True).stdout
    return [(i["kind"], i["textKey"]) for i in json.loads(out)]


PLAN_EV = ev(1, "github-actions[bot]", PLAN)
PR_EV = ev(3, "github-actions[bot]", "🟢 PR aberto: https://github.com/Tradvia/tradvia/pull/2")


def test_comment_before_pr_is_approval():
    got = timeline([PLAN_EV, ev(2, "anderson", "Aprovado"), PR_EV])
    assert got == [("plan", "planPosted"), ("reply", "approved"), ("pr", "openedPr")]


def test_comment_after_pr_has_no_effect_and_is_not_awaiting():
    got = timeline([PLAN_EV, ev(2, "anderson", "Aprovado"), PR_EV, ev(4, "anderson", "obrigado")])
    assert got[-1] == ("reply", "noEffect")
    assert ("reply", "awaiting") not in got
    assert ("reply", "approved") in got
