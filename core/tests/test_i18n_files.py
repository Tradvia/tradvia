# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Locale files (ADR 0011): pt, en and es ship together, and a language is one JSON file."""

import json
import re
import shutil
from pathlib import Path

from tradvia import manual

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "dashboard" / "i18n"
DOCS = ROOT / "dashboard" / "docs.html"
REQUIRED = {"pt", "en", "es"}
# Keys whose value may be empty in a language: English needs no "not translated yet" note.
MAY_BE_EMPTY: set = set()


def locales():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(I18N.glob("*.json"))}


def test_the_three_required_languages_ship_with_the_same_keys():
    files = locales()
    assert REQUIRED <= set(files)
    keys = set(files["en"])
    for code, strings in files.items():
        assert set(strings) == keys, (code, sorted(set(strings) ^ keys))
        for key, value in strings.items():
            assert isinstance(value, str)
            assert value.strip() or (code, key) in MAY_BE_EMPTY, (code, key)


def test_docs_page_keys_all_exist_and_none_are_orphans():
    html = DOCS.read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-html)?="([\w.]+)"', html))
    en = locales()["en"]
    assert used <= set(en), sorted(used - set(en))
    docs_keys = {k for k in en if k.startswith("docs.")}
    assert docs_keys == used, sorted(docs_keys ^ used)


def test_docs_page_no_longer_duplicates_strings_in_spans():
    html = DOCS.read_text(encoding="utf-8")
    assert 'class="pt"' not in html and 'class="en"' not in html
    assert "const I18N" not in html
    assert ".innerHTML" not in html.replace("tpl.innerHTML", "")


def test_language_selector_matches_the_files():
    html = DOCS.read_text(encoding="utf-8")
    options = set(re.findall(r'<option value="(\w+)">', html))
    assert options == {p.stem for p in I18N.glob("*.json")}


def test_markup_in_translations_is_limited_and_the_same_in_every_language():
    files = locales()
    for key, en_value in files["en"].items():
        for code, strings in files.items():
            value = strings[key]
            tags = re.findall(r"</?([a-zA-Z]+)", value)
            assert set(tags) <= {"code", "strong", "em", "a"}, (code, key, tags)
            for href in re.findall(r'href="([^"]*)"', value):
                assert re.match(r"^(https://|manual/|#)", href), (code, key, href)
            # Same links and the same amount of inline code as the English text.
            assert re.findall(r'href="([^"]*)"', value) == re.findall(r'href="([^"]*)"', en_value), (code, key)
            assert value.count("<code>") == en_value.count("<code>"), (code, key)


def test_the_status_words_stay_canonical_in_every_language():
    # Shipped / In progress / Partial / Planned are the CLAUDE.md terms; translations keep them.
    files = locales()
    for code, strings in files.items():
        assert "Shipped" in strings["docs.statusNote"], code
        assert "Planned" in strings["docs.statusNote"], code


def test_a_new_language_is_one_file(tmp_path):
    i18n = tmp_path / "i18n"
    shutil.copytree(I18N, i18n)
    fr = json.loads((i18n / "en.json").read_text(encoding="utf-8"))
    fr.update({"lang.label": "FR", "lang.html": "fr", "manual.search": "Rechercher dans le manuel"})
    (i18n / "fr.json").write_text(json.dumps(fr), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n")
    manual.build(tmp_path, tmp_path / "out", i18n=i18n)
    page = (tmp_path / "out" / "a.html").read_text(encoding="utf-8")
    assert '<option value="fr">FR</option>' in page
    # The new language gets its own tree, with its labels and English for the rest.
    french = (tmp_path / "out" / "fr" / "a.html").read_text(encoding="utf-8")
    assert "Rechercher dans le manuel" in french and '<html lang="fr">' in french
    order = re.findall(r'<option value="(\w+)"', page)
    assert order[:3] == ["pt", "en", "es"] and order[-1] == "fr"


def test_placeholders_are_the_same_in_every_language():
    files = locales()
    for key, en_value in files["en"].items():
        expected = sorted(re.findall(r"\{\w+\}", en_value))
        for code, strings in files.items():
            assert sorted(re.findall(r"\{\w+\}", strings[key])) == expected, (code, key)


def test_the_panel_has_no_dictionary_of_its_own_and_offers_every_language():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "  pt: {" not in html and "  en: {" not in html
    assert 'fetch("i18n/" + code + ".json")' in html
    header = html[html.index('<select id="lang"'):]
    header = header[:header.index("</select>")]
    files = {p.stem for p in I18N.glob("*.json")}
    assert set(re.findall(r'<option value="(\w+)">', header)) == files
    prefs = re.search(r"const PREF_LANGS = \[([^\]]*)\]", html).group(1)
    assert set(re.findall(r'"(\w+)"', prefs)) == files
    settings = re.search(r'settingsSelect\(t\("settings.language"\), prefs.lang, (\[\[.*?\]\])', html).group(1)
    assert set(re.findall(r'\["(\w+)", ', settings)) == files


def test_every_key_the_panel_asks_for_exists():
    # t("literal") calls only; computed keys ("kind_" + kind, "faq." + id) are covered elsewhere.
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    asked = set(re.findall(r'\bt\("([\w.]+)"\)', html))
    asked |= set(re.findall(r'data-i18n(?:-placeholder)?="([\w.]+)"', html))
    en = {k[len("app."):] for k in locales()["en"] if k.startswith("app.")}
    assert asked and asked <= en, sorted(asked - en)
