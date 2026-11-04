# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""The in-app manual (ADR 0011): every doc rendered inside Tradvia, never only linked to GitHub."""

import json
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tradvia import manual

ROOT = Path(__file__).resolve().parents[2]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        if a.get("id"):
            self.ids.append(a["id"])


def links(html):
    parser = Links()
    parser.feed(html)
    return parser


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("manual")
    docs = manual.build(ROOT, out)
    return out, docs


def repo_markdown():
    found = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md")}
    found = {p for p in found if not p.startswith("docs/i18n/")}
    return found | {"README.md", "CONTRIBUTING.md"}


def test_every_doc_and_adr_has_a_page_in_the_index(built):
    out, _ = built
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert {entry["path"] for entry in index} == repo_markdown()
    for entry in index:
        assert (out / entry["page"]).is_file(), entry["page"]
        assert entry["title"] and entry["section"] in manual.GROUPS
    adrs = [e for e in index if e["section"] == "adr"]
    assert len(adrs) == len(list((ROOT / "docs" / "adr").glob("[0-9]*.md"))) >= 23


def test_links_between_docs_stay_inside_the_manual(built):
    out, docs = built
    pages = {d.page for d in docs} | {"index.html"}
    outside = {"..", "../docs.html"}  # the dashboard and its status page, at /runs/
    sources = {d.source for d in docs}
    for doc in docs:
        page = (out / doc.page).read_text(encoding="utf-8")
        for href in links(page).hrefs:
            if href.startswith(("http://", "https://", "#", "mailto:")):
                # A doc of the manual is never read on GitHub, except through the "Source on
                # GitHub" link of its own page; other repository files (CLA.md, code) are.
                if href.startswith(manual.REPO_URL):
                    path = href[len(manual.REPO_URL):].split("#")[0]
                    assert path not in sources or path == doc.source, (doc.page, href)
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(doc.page), href.split("#")[0]))
            assert target in pages or target in outside, (doc.page, href)


def test_a_cross_link_points_at_the_rendered_page_and_its_anchor(built):
    out, _ = built
    deploy = (out / "deploy-oci.html").read_text(encoding="utf-8")
    assert 'href="adr/0005-hosting-on-oci-always-free.html"' in deploy
    adr = (out / "adr" / "0013-chat-needs-an-api-key.html").read_text(encoding="utf-8")
    assert 'href="../orchestrator.html"' in adr


def test_rewrite_link_rules():
    pages = {"docs/mcp.md": "mcp.html", "docs/adr/0012-x.md": "adr/0012-x.html", "README.md": "readme.html"}
    rw = manual.rewrite_link
    assert rw("adr/0012-x.md#decision", "docs/mcp.md", "mcp.html", pages) == "adr/0012-x.html#decision"
    assert rw("../mcp.md", "docs/adr/0012-x.md", "adr/0012-x.html", pages) == "../mcp.html"
    assert rw("../README.md", "docs/mcp.md", "mcp.html", pages) == "readme.html"
    # Any other file of the repository is read on GitHub; absolute URLs and anchors are kept.
    assert rw("../core/contracts/card-state.v1.json", "docs/mcp.md", "mcp.html", pages) == (
        manual.REPO_URL + "core/contracts/card-state.v1.json")
    assert rw("https://example.com/a.md", "docs/mcp.md", "mcp.html", pages) == "https://example.com/a.md"
    assert rw("#tools", "docs/mcp.md", "mcp.html", pages) == "#tools"


def render(text, source="docs/x.md"):
    doc = manual.Doc(source, manual.page_for(source), manual.group_of(source))
    manual.render(manual.renderer(), doc, text, {source: doc.page})
    return doc


def test_headings_get_stable_unique_anchors_and_feed_the_contents():
    doc = render("# Title\n\nIntro text.\n\n## Decision\n\nBody one.\n\n### 1. The manual, pre-rendered\n\n"
                 "## Decision\n\nBody two `code`.\n")
    assert doc.title == "Title"
    assert [(h.level, h.id) for h in doc.headings] == [
        (2, "decision"), (3, "1-the-manual-pre-rendered"), (2, "decision-1")]
    assert '<h2 id="decision">' in doc.body and '<h2 id="decision-1">' in doc.body
    assert 'href="#decision-1"' in doc.body
    assert doc.text[""] == "Intro text."
    assert doc.text["decision-1"] == "Body two code."


def test_raw_html_in_markdown_is_escaped_and_tables_render():
    doc = render("# T\n\n<script>alert(1)</script>\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                 "[x](javascript:alert(1))\n")
    assert "<script>" not in doc.body
    assert "&lt;script&gt;" in doc.body
    assert "<table>" in doc.body and "<td>1</td>" in doc.body
    assert 'href="javascript' not in doc.body


def test_external_images_become_links_and_badges_stay_text():
    doc = render("# T\n\n![diagram](https://img.example/d.png)\n\n"
                 "[![Open in Codespaces](https://github.com/badge.svg)](https://codespaces.new/x)\n")
    assert "<img" not in doc.body
    assert '<a href="https://img.example/d.png">diagram</a>' in doc.body
    assert '<a href="https://codespaces.new/x">Open in Codespaces</a>' in doc.body


def test_translations_folder_is_not_a_page_of_its_own():
    assert manual.page_for("docs/i18n/pt/adr/0001-x.md") is None
    assert manual.page_for("docs/adr/0001-x.md") == "adr/0001-x.html"
    assert manual.page_for("README.md") == "readme.html"
    assert manual.page_for("core/README.md") is None


def test_pages_carry_the_three_languages_search_and_the_way_back(built):
    out, _ = built
    page = (out / "adr" / "0011-in-app-manual-and-scalable-i18n.html").read_text(encoding="utf-8")
    for code in ("pt", "en", "es"):
        assert f'<option value="{code}">' in page
    assert 'id="search"' in page and 'data-root="../"' in page
    assert 'href="../../"' in page  # back to the dashboard, at /runs/
    assert 'href="../../docs.html"' in page  # the status page stays the source of real status
    assert 'id="untranslated"' in page
    assert "tradvia.lang" in page
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="g-adr"' in index and 'data-root=""' in index


def test_index_json_carries_section_text_for_full_text_search(built):
    out, _ = built
    index = {e["path"]: e for e in json.loads((out / "index.json").read_text(encoding="utf-8"))}
    adr = index["docs/adr/0011-in-app-manual-and-scalable-i18n.md"]
    decision = next(h for h in adr["headings"] if h["id"] == "decision")
    assert decision["level"] == 2 and decision["text"] == "Decision"
    assert any("pre-rendered" in h["body"] for h in adr["headings"])


def test_build_replaces_the_previous_output(tmp_path):
    stale = tmp_path / "out" / "gone.html"
    stale.parent.mkdir()
    stale.write_text("old")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\nSee [b](b.md).\n")
    (tmp_path / "docs" / "b.md").write_text("# B\n")
    docs = manual.build(tmp_path, tmp_path / "out")
    assert [d.page for d in docs] == ["a.html", "b.html"]
    assert not stale.exists()
    assert 'href="b.html"' in (tmp_path / "out" / "a.html").read_text()


def test_the_api_never_imports_the_markdown_renderer():
    api = (ROOT / "core" / "src" / "tradvia" / "api.py").read_text(encoding="utf-8")
    assert "markdown_it" not in api and "tradvia.manual" not in api and "from .manual" not in api
    source = (ROOT / "core" / "src" / "tradvia" / "manual.py").read_text(encoding="utf-8")
    # Imported lazily, inside renderer(), so `import tradvia.manual` needs nothing extra.
    assert not re.search(r"^(from markdown_it|import markdown_it)", source, re.M)


def test_deploy_renders_the_manual_before_uploading_the_dashboard():
    wf = (ROOT / ".github" / "workflows" / "deploy-oci.yml").read_text(encoding="utf-8")
    render_at = wf.index("python -m tradvia.manual build --out dashboard/manual")
    assert render_at < wf.index("rsync -az --delete dashboard/")
    assert re.search(r'markdown-it-py==\d+\.\d+\.\d+', wf)
    assert '"docs/**"' in wf
    assert "dashboard/manual/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
