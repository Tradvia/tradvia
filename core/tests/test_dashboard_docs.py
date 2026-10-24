# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""The docs page (dashboard/docs.html) renders, mirrors ADR 0007 and is linked from the dashboard."""

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "dashboard" / "docs.html"
INDEX = ROOT / "dashboard" / "index.html"
ADR_DIR = ROOT / "docs" / "adr"
STATUSES = {"Shipped", "In progress", "Partial", "Planned"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.capabilities = []  # {"id", "ref", "badges"}
        self.steps = []
        self._stack = []  # open capability/step entries
        self._badge = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a":
            self.links.append(a)
        entry = None
        if "data-capability" in a:
            entry = {"id": a["data-capability"], "ref": a.get("data-ref"), "badges": []}
            self.capabilities.append(entry)
        elif "data-step" in a:
            entry = {"id": a["data-step"], "ref": a.get("data-ref"), "badges": []}
            self.steps.append(entry)
        if entry is not None:
            self._stack.append((tag, entry))
        if "badge" in (a.get("class") or "").split():
            self._badge = True

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1][0] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._badge and self._stack:
            self._stack[-1][1]["badges"].append(data.strip())
        self._badge = False


def parse(path):
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def adr_status():
    """Card number -> status, from the ADR 0007 delivery table."""
    text = next(ADR_DIR.glob("0007-*.md")).read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*\d+\s*\|\s*#(\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*$", text, re.M)
    return {n: s for n, s in rows}


def test_docs_page_has_the_three_sections():
    page = parse(DOCS)
    assert {"capabilities", "guide", "technical"} <= page.ids
    assert {c["id"] for c in page.capabilities} >= {
        "cards", "plans", "prs", "deploys", "backlog", "kpis", "connections"}
    assert len(page.steps) == 5


def test_every_item_has_exactly_one_valid_status():
    page = parse(DOCS)
    for item in page.capabilities + page.steps:
        assert len(item["badges"]) == 1, item
        assert item["badges"][0] in STATUSES, item


def test_statuses_match_adr_0007():
    page = parse(DOCS)
    table = adr_status()
    assert table["56"] == "Partial"
    checked = 0
    for item in page.capabilities + page.steps:
        if item["ref"]:
            assert item["badges"] == [table[item["ref"]]], item
            checked += 1
    assert checked >= 5
    refs = {i["ref"] for i in page.capabilities}
    assert {"45", "46", "56", "58"} <= refs


def test_shipped_claims_are_only_the_store_backed_ones():
    page = parse(DOCS)
    shipped = {c["id"] for c in page.capabilities if c["badges"] == ["Shipped"]}
    assert shipped == {"backlog", "kpis"}


def test_help_link_points_to_the_docs_page():
    links = [a for a in parse(INDEX).links if a.get("id") == "help-link"]
    assert len(links) == 1
    assert links[0]["href"] == "docs.html"
    html = INDEX.read_text(encoding="utf-8")
    assert 'help: "Ajuda"' in html and 'help: "Help"' in html


def test_docs_page_i18n_and_back_link():
    html = DOCS.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n="(\w+)"', html))
    for key in keys:
        assert html.count(f"{key}:") >= 2, key
    back = [a for a in parse(DOCS).links if a.get("id") == "back-link"]
    assert back and back[0]["href"] == "./"
    assert "tradvia.lang" in html and "tradvia.lang" in INDEX.read_text(encoding="utf-8")


def test_every_adr_is_linked():
    hrefs = " ".join(a.get("href", "") for a in parse(DOCS).links)
    adrs = sorted(ADR_DIR.glob("000*.md"))
    assert len(adrs) >= 8
    for adr in adrs:
        assert f"docs/adr/{adr.name}" in hrefs, adr.name
