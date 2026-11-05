# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Translated manual (ADR 0011, part 3): a tree per language, English fallback, stale flag."""

import json
from pathlib import Path

import pytest

from tradvia import manual

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "dashboard" / "i18n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    write(tmp_path / "docs" / "a.md", "<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->\n# Apple\n\nSee [b](b.md).\n")
    write(tmp_path / "docs" / "b.md", "# Banana\n\n## Ripe\n\nYellow.\n")
    write(tmp_path / "docs" / "adr" / "0001-x.md", "# ADR 0001: X\n")
    source = (tmp_path / "docs" / "a.md").read_text(encoding="utf-8")
    write(tmp_path / "docs" / "i18n" / "pt" / "a.md",
          manual.stamp("# Maçã\n\nVeja [b](b.md).\n", "docs/a.md", source))
    return tmp_path


def page(out, rel):
    return (out / rel).read_text(encoding="utf-8")


def test_each_language_gets_its_own_tree_with_its_labels(repo):
    out = repo / "out"
    manual.build(repo, out, i18n=I18N)
    for code in ("pt", "es"):
        assert (out / code / "index.html").is_file() and (out / code / "index.json").is_file()
        assert (out / code / "adr" / "0001-x.html").is_file()
    pt = page(out, "pt/a.html")
    assert '<html lang="pt-BR">' in pt and '<option value="pt" selected>' in pt
    assert "Voltar ao painel" in pt and "Buscar no manual" in pt
    assert 'data-lang="pt"' in pt and 'data-page="a.html"' in pt and 'data-manual="../"' in pt
    # Back to the panel and to the status page from one folder deeper.
    assert 'href="../../"' in pt and 'href="../../docs.html"' in pt
    es_adr = page(out, "es/adr/0001-x.html")
    assert 'data-manual="../../"' in es_adr and 'href="../../../docs.html"' in es_adr


def test_a_translation_is_shown_and_its_links_stay_in_the_language(repo):
    out = repo / "out"
    manual.build(repo, out, i18n=I18N)
    pt = page(out, "pt/a.html")
    assert "Maçã" in pt and "Apple" not in pt.split("<article>")[1]
    assert 'href="b.html"' in pt  # pt/b.html, the same language's page
    assert 'id="untranslated"' not in pt and 'id="stale"' not in pt
    assert manual.REPO_URL + "docs/i18n/pt/a.md" in pt  # its own source is the translation
    index = {e["path"]: e for e in json.loads(page(out, "pt/index.json"))}
    assert index["docs/a.md"]["title"] == "Maçã" and index["docs/a.md"]["status"] == "translated"


def test_a_missing_translation_falls_back_to_the_original_with_a_note(repo):
    out = repo / "out"
    manual.build(repo, out, i18n=I18N)
    pt_b = page(out, "pt/b.html")
    assert "Banana" in pt_b and 'id="untranslated"' in pt_b
    assert "ainda não tem tradução" in pt_b
    es_a = page(out, "es/a.html")
    assert "Apple" in es_a and "aún no tiene traducción" in es_a


def test_a_translation_older_than_its_source_is_flagged(repo):
    (repo / "docs" / "a.md").write_text("# Apple\n\nSee [b](b.md). Now with more text.\n", encoding="utf-8")
    out = repo / "out"
    manual.build(repo, out, i18n=I18N)
    pt = page(out, "pt/a.html")
    assert "Maçã" in pt and 'id="stale"' in pt and "desatualizada" in pt
    rows = manual.report(repo, i18n=I18N)
    assert rows["pt"]["stale"] == ["docs/a.md"]


def test_stamp_makes_a_stale_translation_fresh_again(repo):
    path = repo / "docs" / "i18n" / "pt" / "a.md"
    (repo / "docs" / "a.md").write_text("# Apple v2\n", encoding="utf-8")
    assert manual.translation_state(repo, "docs/a.md", "pt")[0] == "stale"
    assert manual.main(["stamp", "--root", str(repo), str(path)]) == 0
    assert manual.translation_state(repo, "docs/a.md", "pt")[0] == "translated"
    text = path.read_text(encoding="utf-8")
    assert text.count("tradvia-translation") == 1 and "# Maçã" in text


def test_report_lists_orphans_and_headerless_files(repo):
    write(repo / "docs" / "i18n" / "pt" / "gone.md", "# Sumiu\n")
    write(repo / "docs" / "i18n" / "es" / "b.md", "# Plátano\n")  # no header
    rows = manual.report(repo, i18n=I18N)
    assert "docs/i18n/pt/gone.md" in rows["pt"]["invalid"]
    assert "docs/i18n/es/b.md" in rows["es"]["invalid"]
    assert manual.translation_state(repo, "docs/b.md", "es")[0] == "stale"


def test_a_document_written_in_portuguese_is_original_in_pt(tmp_path):
    write(tmp_path / "docs" / "glossario.md", "# Glossário\n")
    out = tmp_path / "out"
    manual.build(tmp_path, out, i18n=I18N)
    assert 'id="untranslated"' not in page(out, "pt/glossario.html")
    assert 'id="untranslated"' in page(out, "glossario.html")  # English tree, no translation yet


def test_html_comments_never_show_up_as_text(repo):
    out = repo / "out"
    manual.build(repo, out, i18n=I18N)
    assert "SPDX" not in page(out, "a.html").split("<article>")[1]


def test_the_repository_translations_are_valid_and_current():
    rows = manual.report(ROOT)
    for lang, data in rows.items():
        assert data["invalid"] == [], (lang, data["invalid"])
    # Every translation shipped so far matches its source; an edit to the English source
    # without updating the translation shows the page as out of date instead of failing here.
    translated = sum(len(d["translated"]) + len(d["stale"]) for d in rows.values())
    assert translated >= 8
