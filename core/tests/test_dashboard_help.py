# SPDX-License-Identifier: FSL-1.1-ALv2
"""The in-app Help is a FAQ in both languages, searchable without accents (#101)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def js(expr):
    text = HTML.read_text(encoding="utf-8")
    code = text[text.index("// Help: frequently asked questions"):text.index("let helpQuery")]
    out = subprocess.run([NODE, "-e", code + f"\nconsole.log(JSON.stringify({expr}));"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_every_question_has_both_languages_a_known_topic_and_a_unique_id():
    faq, cats = js("HELP_FAQ"), {c["id"] for c in js("HELP_CATS")}
    assert len(faq) >= 25 and len({f["id"] for f in faq}) == len(faq)
    for f in faq:
        assert f["cat"] in cats
        for lang in ("pt", "en"):
            assert f["q"][lang].strip() and f["a"][lang] and all(x.strip() for x in f["a"][lang])
        assert len(f["a"]["pt"]) == len(f["a"]["en"]), f["id"]


def test_search_ignores_accents_and_case_and_needs_every_word():
    ids = js('helpMatches(HELP_FAQ, "APROVACAO deploy", "", "pt").map(f => f.id)')
    assert "deploy" in ids
    assert js('helpMatches(HELP_FAQ, "orçamento", "settings", "pt").map(f => f.id)')[0] == "budget"
    assert js('helpMatches(HELP_FAQ, "zzzz", "", "en")') == []
    assert {f["cat"] for f in js('helpMatches(HELP_FAQ, "", "ai", "en")')} == {"ai"}


def test_no_answer_promises_a_button_the_panel_does_not_have():
    text = HTML.read_text(encoding="utf-8")
    for label in ("Gerar token", "Novo card", "Aprovar workflows", "Atualizar branch", "Deploys pendentes",
                  "Conectar minha IA", "Revogar", "Create token", "New card", "Approve workflows"):
        assert text.count(label) >= 2, label  # once in the i18n table, once in the FAQ
