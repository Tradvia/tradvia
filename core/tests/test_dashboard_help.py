# SPDX-License-Identifier: FSL-1.1-ALv2
"""The in-app Help is a FAQ in pt, en and es, searchable without accents (#101, ADR 0011)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from panel_i18n import LANGS, panel_strings

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def js(expr, lang="en"):
    text = HTML.read_text(encoding="utf-8")
    code = text[text.index("// Help: frequently asked questions"):text.index("let helpQuery")]
    strings = json.dumps(panel_strings(lang), ensure_ascii=False)
    prelude = f"const S = {strings};\nconst tr = (k) => S[k];\n"
    out = subprocess.run([NODE, "-e", prelude + code + f"\nconsole.log(JSON.stringify({expr}));"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_every_question_has_every_language_a_known_topic_and_a_unique_id():
    faq, cats = js("HELP_FAQ"), set(js("HELP_CATS"))
    assert len(faq) >= 25 and len({f["id"] for f in faq}) == len(faq)
    lines = {}
    for lang in LANGS:
        strings = panel_strings(lang)
        for c in cats:
            assert strings[f"faq.cat.{c}"].strip(), (lang, c)
        for f in js("helpEntries(tr)", lang):
            assert f["cat"] in cats
            assert f["q"].strip() and f["a"] and all(x.strip() for x in f["a"]), (lang, f["id"])
            lines.setdefault(f["id"], set()).add(len(f["a"]))
    # The same paragraphs and bullets in every language.
    assert all(len(n) == 1 for n in lines.values()), lines


def test_search_ignores_accents_and_case_and_needs_every_word():
    ids = js('helpMatches(helpEntries(tr), "APROVACAO deploy", "").map(f => f.id)', "pt")
    assert "deploy" in ids
    assert js('helpMatches(helpEntries(tr), "orçamento", "settings").map(f => f.id)', "pt")[0] == "budget"
    assert js('helpMatches(helpEntries(tr), "presupuesto", "settings").map(f => f.id)', "es")[0] == "budget"
    assert js('helpMatches(helpEntries(tr), "zzzz", "")') == []
    assert {f["cat"] for f in js('helpMatches(helpEntries(tr), "", "ai")')} == {"ai"}


def test_no_answer_promises_a_button_the_panel_does_not_have():
    # Each button the FAQ names is spelled the way the panel itself labels it, in every language.
    buttons = ("agentCreate", "newCard", "approveWorkflows", "updateBranch", "deploys", "agentTitle",
               "agentRevoke")
    for lang in LANGS:
        strings = panel_strings(lang)
        faq = " ".join(v for k, v in strings.items() if k.startswith("faq."))
        for key in buttons:
            assert strings[key] in faq, (lang, key, strings[key])
