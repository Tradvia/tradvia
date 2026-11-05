# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""The in-app manual (ADR 0011): the repository's Markdown rendered to static pages.

`python -m tradvia.manual build` runs at deploy time and writes `dashboard/manual/`:
one HTML page per document, an `index.html` with the table of contents and search,
and `index.json` (path, page, title, section, headings). The Markdown under `docs/`
stays the source of truth; nothing here runs in the API process.

English is the manual's root; every other language of `dashboard/i18n/` gets the same
tree under `dashboard/manual/<lang>/`. A page there is the translation in
`docs/i18n/<lang>/...` when one exists, marked when its source changed after it, and
otherwise the original with a note. `python -m tradvia.manual check` lists both.

markdown-it-py is imported only inside `renderer()`, so importing this module (or the
rest of the package) never needs it; the deploy step and the dev extra install it.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import json
import posixpath
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_URL = "https://github.com/Tradvia/tradvia/blob/main/"
GROUPS = ("project", "guides", "adr", "evaluations")
TOP_LEVEL = {"README.md": "readme.html", "CONTRIBUTING.md": "contributing.html"}
TRANSLATIONS_DIR = "docs/i18n"  # part 3 of ADR 0011: translated renderings, not pages of their own
DEFAULT_LANG = "en"
# Documents not written in English; every other source is.
SOURCE_LANG = {"docs/glossario.md": "pt"}
HEADER = re.compile(r'\A<!--\s*tradvia-translation\s+source="([^"]+)"\s+source-sha256="([0-9a-f]{64})"\s*-->[ \t]*\r?\n')


@dataclass
class Heading:
    level: int
    text: str
    id: str


@dataclass
class Doc:
    source: str  # repo-relative path of the Markdown, e.g. "docs/adr/0011-x.md"
    page: str  # manual-relative path of the page, e.g. "adr/0011-x.html"
    group: str
    title: str = ""
    headings: list[Heading] = field(default_factory=list)
    body: str = ""
    # Plain text per section, for the full-text search: "" is the text before the first h2/h3.
    text: dict[str, str] = field(default_factory=dict)
    # In a language tree: "original", "translated", "stale" (source changed since) or
    # "untranslated" (the original is shown).
    status: str = "original"


# --- translations ----------------------------------------------------------------------

def source_lang(source: str) -> str:
    return SOURCE_LANG.get(source, DEFAULT_LANG)


def translation_path(source: str, lang: str) -> str:
    """Where the `lang` translation of a source lives: docs/ mirrored under docs/i18n/<lang>/."""
    rel = source[len("docs/"):] if source.startswith("docs/") else source
    return f"{TRANSLATIONS_DIR}/{lang}/{rel}"


def source_hash(text: str) -> str:
    """The fingerprint a translation records of the source it was made from."""
    return hashlib.sha256((text.replace("\r\n", "\n").strip() + "\n").encode("utf-8")).hexdigest()


def parse_translation(text: str) -> tuple[str | None, str | None, str]:
    """(source, sha256, body) of a translation file; None, None when the header is missing."""
    m = HEADER.match(text)
    if not m:
        return None, None, text
    return m.group(1), m.group(2), text[m.end():]


def stamp(text: str, source: str, source_text: str) -> str:
    """The translation with its header set to the source's current fingerprint."""
    _, _, body = parse_translation(text)
    return (f'<!-- tradvia-translation source="{source}" source-sha256="{source_hash(source_text)}" -->\n'
            + body.lstrip("\n"))


def translation_state(root: Path, source: str, lang: str) -> tuple[str, str]:
    """(status, markdown) for `source` in `lang`: what that language's page shows."""
    original = (root / source).read_text(encoding="utf-8")
    if lang == source_lang(source):
        return "original", original
    path = root / translation_path(source, lang)
    if not path.is_file():
        return "untranslated", original
    recorded_source, recorded_hash, body = parse_translation(path.read_text(encoding="utf-8"))
    fresh = recorded_source == source and recorded_hash == source_hash(original)
    return ("translated" if fresh else "stale"), body


def page_for(source: str) -> str | None:
    """Manual page of a repo-relative Markdown path, or None when it is not in the manual."""
    if source in TOP_LEVEL:
        return TOP_LEVEL[source]
    if not source.startswith("docs/") or not source.endswith(".md"):
        return None
    if source.startswith(TRANSLATIONS_DIR + "/"):
        return None
    return source[len("docs/"):-len(".md")] + ".html"


def group_of(source: str) -> str:
    if source in TOP_LEVEL:
        return "project"
    parts = source.split("/")
    if len(parts) == 2:
        return "guides"
    return parts[1]


def sources(root: Path) -> list[str]:
    """Every Markdown file the manual renders, repo-relative, in reading order."""
    found = [name for name in TOP_LEVEL if (root / name).is_file()]
    docs = root / "docs"
    if docs.is_dir():
        for path in sorted(docs.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            if page_for(rel):
                found.append(rel)
    order = {g: i for i, g in enumerate(GROUPS)}
    top = list(TOP_LEVEL)
    return sorted(found, key=lambda s: (order.get(group_of(s), len(GROUPS)),
                                        top.index(s) if s in top else len(top), s))


def slugify(text: str) -> str:
    """GitHub-style anchor: lowercase, punctuation dropped, spaces to hyphens."""
    text = unicodedata.normalize("NFC", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-") or "section"


def rewrite_link(href: str, source: str, page: str, pages: dict[str, str]) -> str:
    """A link as written in `source`, made to work from `page` inside the manual.

    Links to another document in the manual stay inside it; links to any other file of
    the repository go to that file on GitHub; absolute URLs and same-page anchors are kept.
    """
    if not href or href.startswith("#"):
        return href
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or href.startswith("/"):
        return href
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source), parts.path))
    if target.startswith("../") or target == "..":
        return href
    frag = "#" + parts.fragment if parts.fragment else ""
    if target in pages:
        here = posixpath.dirname(page) or "."
        return posixpath.relpath(pages[target], here) + frag
    return REPO_URL + target + frag


def renderer():
    """CommonMark with tables and strikethrough. Raw HTML in the Markdown is shown as text,
    never as markup; HTML comments (license headers, notes to editors) are dropped."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

    def raw_html(self, tokens, idx, options, env):
        content = tokens[idx].content
        if re.fullmatch(r"\s*(<!--.*?-->\s*)+", content, re.S):
            return ""
        escaped = html.escape(content)
        return f"<p>{escaped}</p>\n" if tokens[idx].type == "html_block" else escaped

    def heading_close(self, tokens, idx, options, env):
        anchor = tokens[idx].meta.get("anchor")
        link = f' <a class="anchor" href="#{anchor}" aria-hidden="true">#</a>' if anchor else ""
        return f"{link}</{tokens[idx].tag}>\n"

    def image(self, tokens, idx, options, env):
        # Images from other hosts are blocked by the panel's CSP; show them as a link instead.
        token = tokens[idx]
        alt = html.escape(token.content or str(token.attrGet("src") or "") or "image")
        depth = sum(1 if t.type == "link_open" else -1 if t.type == "link_close" else 0
                    for t in tokens[:idx])
        if depth > 0:  # already inside a link (a badge): the alt text is enough
            return alt
        src = html.escape(str(token.attrGet("src") or ""), quote=True)
        return f'<a href="{src}">{alt}</a>'

    md.add_render_rule("html_block", raw_html)
    md.add_render_rule("html_inline", raw_html)
    md.add_render_rule("heading_close", heading_close)
    md.add_render_rule("image", image)
    return md


def _plain(inline) -> str:
    return "".join(c.content if c.type in ("text", "code_inline") else " "
                   for c in (inline.children or []) if c.type in ("text", "code_inline", "softbreak", "hardbreak"))


def render(md, doc: Doc, text: str, pages: dict[str, str]) -> None:
    tokens = md.parse(text)
    seen: dict[str, int] = {}
    section = ""
    chunks: dict[str, list[str]] = {"": []}
    for i, token in enumerate(tokens):
        if token.type in ("fence", "code_block"):
            chunks[section].append(token.content)
        if token.type == "heading_open":
            label = _plain(tokens[i + 1]).strip()
            base = slugify(label)
            n = seen.get(base, 0)
            seen[base] = n + 1
            anchor = base if n == 0 else f"{base}-{n}"
            token.attrSet("id", anchor)
            tokens[i + 2].meta = {"anchor": anchor}
            level = int(token.tag[1])
            if level == 1 and not doc.title:
                doc.title = label
            elif level in (2, 3):
                doc.headings.append(Heading(level, label, anchor))
                section = anchor
                chunks[section] = []
        elif token.type == "inline":
            if tokens[i - 1].type != "heading_open":
                chunks[section].append(_plain(token))
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet("href", rewrite_link(str(child.attrGet("href") or ""),
                                                       doc.source, doc.page, pages))
                elif child.type == "image":
                    child.attrSet("src", rewrite_link(str(child.attrGet("src") or ""),
                                                      doc.source, doc.page, pages))
    doc.title = doc.title or Path(doc.source).stem
    doc.text = {k: " ".join(" ".join(v).split()) for k, v in chunks.items()}
    doc.body = md.renderer.render(tokens, md.options, {})


# --- page chrome --------------------------------------------------------------------

LANGUAGE_ORDER = ("pt", "en", "es")


def load_strings(i18n_dir: Path) -> dict[str, dict[str, str]]:
    """The manual's labels per language, from the panel's own `dashboard/i18n/<lang>.json`.

    Only the `manual.*` keys (prefix dropped) plus `lang.label` and `lang.html` are kept,
    with English filling any key a language lacks. Every language file found becomes a
    tree of the manual; `en` is required.
    """
    found: dict[str, dict[str, str]] = {}
    for path in sorted(i18n_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found[path.stem] = {k[len("manual."):] if k.startswith("manual.") else k: v
                            for k, v in data.items() if k.startswith(("manual.", "lang."))}
    if DEFAULT_LANG not in found:
        raise FileNotFoundError(f"{i18n_dir / 'en.json'} is required")
    rank = {code: i for i, code in enumerate(LANGUAGE_ORDER)}
    ordered = sorted(found, key=lambda c: (rank.get(c, len(rank)), c))
    return {code: {**found[DEFAULT_LANG], **found[code]} for code in ordered}


CSS = """
:root { --bg:#0e1116; --panel:#161b22; --line:#262d37; --text:#e6e9ee; --muted:#8b95a3; --info:#5b9bf0; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a { color:var(--info); text-decoration:none; } a:hover { text-decoration:underline; }
header { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; justify-content:space-between;
  max-width:1200px; margin:0 auto; padding:12px 16px; border-bottom:1px solid var(--line); }
header h1 { font-size:18px; margin:0; } header .links { display:flex; flex-wrap:wrap; gap:4px 16px; }
select, input[type=search] { background:var(--panel); color:var(--text); border:1px solid var(--line);
  border-radius:6px; padding:6px 10px; font:inherit; }
.layout { display:grid; grid-template-columns:280px minmax(0,1fr); gap:24px; max-width:1200px; margin:0 auto; padding:16px; }
aside { position:sticky; top:12px; align-self:start; max-height:calc(100vh - 24px); overflow:auto; }
aside input[type=search] { width:100%; margin-bottom:12px; }
aside h2, .toc h2 { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; margin:16px 0 4px; }
aside ul, .toc ul { list-style:none; margin:0; padding:0; } aside li { padding:2px 0; }
aside li.current > a { color:var(--text); font-weight:600; }
.toc li { padding:2px 0; } .toc li.h3 { padding-left:16px; } .toc .sections { margin:2px 0 8px 12px; font-size:13px; }
.empty { color:var(--muted); }
.results li { padding:4px 0; border-bottom:1px solid var(--line); } .results .hit { color:var(--muted); font-size:12px; }
article { min-width:0; overflow-wrap:break-word; }
article code { overflow-wrap:anywhere; } article pre code { overflow-wrap:normal; }
details.toc { border:1px solid var(--line); border-radius:6px; padding:6px 12px; margin:0 0 16px; }
details.toc summary { cursor:pointer; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
aside summary { cursor:pointer; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
article h1 { font-size:24px; margin:0 0 12px; } article h2 { font-size:19px; margin:28px 0 8px; border-bottom:1px solid var(--line); padding-bottom:4px; }
article h3 { font-size:16px; margin:20px 0 6px; }
article .anchor { color:var(--muted); font-size:.8em; visibility:hidden; }
article h1:hover .anchor, article h2:hover .anchor, article h3:hover .anchor, article h4:hover .anchor { visibility:visible; }
code { background:var(--panel); border:1px solid var(--line); border-radius:4px; padding:0 4px; }
pre { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:12px; overflow:auto; }
pre code { border:0; padding:0; background:none; }
table { border-collapse:collapse; display:block; overflow:auto; margin:12px 0; }
th, td { border:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }
th { background:var(--panel); }
blockquote { margin:12px 0; padding:4px 12px; border-left:3px solid var(--line); color:var(--muted); }
.note { color:var(--muted); font-size:13px; border:1px dashed var(--line); border-radius:6px; padding:6px 10px; }
.note.stale { color:#d9a441; border-color:#d9a441; }
.meta { color:var(--muted); font-size:12px; margin-top:32px; }
[hidden] { display:none !important; }
@media (max-width: 800px) { .layout { grid-template-columns:1fr; } aside { position:static; max-height:none; }
  aside summary { padding:4px 0; } }
@media (min-width: 801px) { aside summary { display:none; } }
"""

SCRIPT = r"""
const LANGS = %(langs)s;
const PAGE = document.body.dataset.page;
const TREE = document.body.dataset.lang;
const MANUAL = document.body.dataset.manual;  // the manual's root (the English tree)
function pageIn(code) {
  return MANUAL + (code === "en" ? "" : code + "/") + PAGE + location.search + location.hash;
}
function preferred() {
  let code = (navigator.language || "en").slice(0, 2).toLowerCase();
  try { const saved = localStorage.getItem("tradvia.lang"); if (saved) code = saved; } catch (err) { /* ignore */ }
  return LANGS.includes(code) ? code : null;
}
// The same page in the reader's language: the panel and the docs page share tradvia.lang.
const wanted = preferred();
if (wanted && wanted !== TREE) location.replace(pageIn(wanted));
document.getElementById("lang").value = TREE;
document.getElementById("lang").addEventListener("change", (e) => {
  try { localStorage.setItem("tradvia.lang", e.target.value); } catch (err) { /* ignore */ }
  location.href = pageIn(e.target.value);
});
const search = document.getElementById("search");
const results = document.getElementById("results");
const docList = document.getElementById("doc-list");
const toc = document.getElementById("contents");
let index = null;
function norm(text) {
  return (text || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}
async function loadIndex() {
  if (index) return index;
  try {
    const res = await fetch(document.body.dataset.root + "index.json");
    index = res.ok ? await res.json() : [];
  } catch (err) { index = []; }
  return index;
}
function link(href, title, sub) {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = href;
  a.textContent = title;
  li.append(a);
  if (sub) {
    const s = document.createElement("div");
    s.className = "hit";
    s.textContent = sub;
    li.append(s);
  }
  return li;
}
function snippet(text, term) {
  const at = norm(text).indexOf(term);
  if (at < 0) return "";
  const from = Math.max(0, at - 40);
  return (from > 0 ? "…" : "") + text.slice(from, at + 80) + (at + 80 < text.length ? "…" : "");
}
async function find() {
  const terms = norm(search.value).split(/\s+/).filter(Boolean);
  const searching = terms.length > 0;
  if (docList) docList.hidden = searching;
  if (toc) toc.hidden = searching;
  results.replaceChildren();
  results.hidden = !searching;
  document.getElementById("no-results").hidden = true;
  if (!searching) return;
  const docs = await loadIndex();
  const root = document.body.dataset.root;
  let hits = 0;
  for (const d of docs) {
    const chunks = [{ id: "", heading: "", body: d.intro }].concat(
      d.headings.map((h) => ({ id: h.id, heading: h.text, body: h.body })));
    for (const c of chunks) {
      const hay = norm(d.title + " " + d.path + " " + c.heading + " " + c.body);
      if (!terms.every((t) => hay.includes(t))) continue;
      const title = c.heading ? d.title + " › " + c.heading : d.title;
      results.append(link(root + d.page + (c.id ? "#" + c.id : ""), title, snippet(c.body, terms[0])));
      hits += 1;
      if (hits >= 60) break;
    }
    if (hits >= 60) break;
  }
  document.getElementById("no-results").hidden = hits > 0;
}
let timer = null;
search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(find, 120); });
// On a phone the lists start closed, so the page's own text comes first.
if (window.matchMedia("(max-width: 800px)").matches) {
  document.querySelectorAll("details[data-collapse]").forEach((d) => { d.open = false; });
}
const q = new URLSearchParams(location.search).get("q");
if (q) { search.value = q; find(); }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _rel(target: str, page: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(page) or ".")


@dataclass
class Tree:
    """One language's copy of the manual."""
    lang: str
    labels: dict[str, str]
    languages: dict[str, dict[str, str]]  # every language's labels, for the selector

    @property
    def base(self) -> str:
        """The tree's folder inside the manual: "" for English, "pt/" for Portuguese."""
        return "" if self.lang == DEFAULT_LANG else self.lang + "/"

    def t(self, key: str) -> str:
        return _e(self.labels.get(key, ""))


def _search_box(tree: Tree) -> str:
    return (f'<input type="search" id="search" placeholder="{tree.t("search")}" aria-label="{tree.t("search")}">'
            f'<ul id="results" class="results" hidden></ul>'
            f'<p id="no-results" class="empty" hidden>{tree.t("none")}</p>')


def _sidebar(tree: Tree, docs: list[Doc], page: str) -> str:
    out = [_search_box(tree), f'<details id="doc-list" data-collapse open><summary>{tree.t("contents")}</summary>']
    for group in GROUPS:
        members = [d for d in docs if d.group == group]
        if not members:
            continue
        out.append(f'<div><h2>{tree.t("g." + group)}</h2><ul>')
        for d in members:
            cls = ' class="current"' if d.page == page else ""
            out.append(f'<li{cls}><a href="{_e(_rel(d.page, page))}">{_e(d.title)}</a></li>')
        out.append("</ul></div>")
    out.append("</details>")
    return "\n".join(out)


def _shell(tree: Tree, title: str, page: str, sidebar: str, main: str) -> str:
    prefix = "../" * page.count("/")  # to the tree's root
    manual = prefix + ("" if tree.lang == DEFAULT_LANG else "../")  # to the English root
    options = "".join(
        f'<option value="{_e(code)}"{" selected" if code == tree.lang else ""}>{_e(s.get("lang.label", code))}</option>'
        for code, s in tree.languages.items())
    script = SCRIPT % {"langs": json.dumps(list(tree.languages))}
    return f"""<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
<!-- Generated by `python -m tradvia.manual build` from the repository's Markdown. Do not edit. -->
<!doctype html>
<html lang="{_e(tree.labels.get("lang.html", tree.lang))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · Tradvia</title>
<style>{CSS}</style>
</head>
<body data-lang="{_e(tree.lang)}" data-page="{_e(page)}" data-root="{_e(prefix)}" data-manual="{_e(manual)}">
<header>
  <div>
    <h1><a href="{_e(prefix + "index.html")}">Tradvia · {tree.t("manual")}</a></h1>
    <div class="links">
      <a href="{_e(manual + "../")}">{tree.t("back")}</a>
      <a href="{_e(manual + "../docs.html")}">{tree.t("status")}</a>
    </div>
  </div>
  <select id="lang" aria-label="Language">{options}</select>
</header>
<div class="layout">
<aside>
{sidebar}
</aside>
<article>
{main}
</article>
</div>
<script>{script}</script>
</body>
</html>
"""


def _note(tree: Tree, doc: Doc) -> str:
    if doc.status == "untranslated":
        return f'<p id="untranslated" class="note">{tree.t("untranslated")}</p>\n'
    if doc.status == "stale":
        return f'<p id="stale" class="note stale">{tree.t("stale")}</p>\n'
    return ""


def doc_page(tree: Tree, doc: Doc, docs: list[Doc]) -> str:
    toc = ""
    if doc.headings:
        items = "".join(f'<li class="h{h.level}"><a href="#{_e(h.id)}">{_e(h.text)}</a></li>'
                        for h in doc.headings)
        toc = (f'<details class="toc" data-collapse open><summary>{tree.t("onPage")}</summary>'
               f'<ul>{items}</ul></details>')
    body = doc.body
    end = body.find("</h1>")
    if end >= 0:  # the page's own title first, then what is on it
        end += len("</h1>")
        body = body[:end] + "\n" + toc + body[end:]
    else:
        body = toc + body
    shown = doc.source if doc.status in ("original", "untranslated") else translation_path(doc.source, tree.lang)
    main = (f'{_note(tree, doc)}{body}\n<p class="meta"><a href="{_e(REPO_URL + shown)}">{tree.t("source")}</a>'
            f' · <code>{_e(shown)}</code></p>')
    return _shell(tree, doc.title, doc.page, _sidebar(tree, docs, doc.page), main)


def index_page(tree: Tree, docs: list[Doc]) -> str:
    parts = [f'<h1>{tree.t("manual")}</h1>', f'<p>{tree.t("intro")}</p>',
             f'<nav class="toc" id="contents"><h2>{tree.t("contents")}</h2>']
    for group in GROUPS:
        members = [d for d in docs if d.group == group]
        if not members:
            continue
        parts.append(f'<div id="g-{group}"><h2>{tree.t("g." + group)}</h2><ul>')
        for d in members:
            parts.append(f'<li><a href="{_e(d.page)}">{_e(d.title)}</a></li>')
            for h in d.headings:
                if h.level == 2:
                    parts.append(f'<li class="sections"><a href="{_e(d.page)}#{_e(h.id)}">{_e(h.text)}</a></li>')
        parts.append("</ul></div>")
    parts.append("</nav>")
    # The contents are the index's own text; the sidebar holds only the search.
    return _shell(tree, "Manual", "index.html", _search_box(tree), "\n".join(parts))


def _i18n_dir(root: Path, i18n: Path | None) -> Path:
    if i18n is not None:
        return i18n
    here = root / "dashboard" / "i18n"
    return here if here.is_dir() else Path(__file__).resolve().parents[3] / "dashboard" / "i18n"


def render_tree(root: Path, out: Path, tree: Tree, md=None) -> list[Doc]:
    """One language's pages, index and index.json under `out`."""
    md = md or renderer()
    docs = [Doc(s, page_for(s) or "", group_of(s)) for s in sources(root)]
    pages = {d.source: d.page for d in docs}
    for doc in docs:
        doc.status, text = translation_state(root, doc.source, tree.lang)
        render(md, doc, text, pages)
    out.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        target = out / doc.page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(doc_page(tree, doc, docs), encoding="utf-8")
    (out / "index.html").write_text(index_page(tree, docs), encoding="utf-8")
    index = [{"path": d.source, "page": d.page, "title": d.title, "section": d.group, "status": d.status,
              "intro": d.text.get("", ""),
              "headings": [{"level": h.level, "text": h.text, "id": h.id, "body": d.text.get(h.id, "")}
                           for h in d.headings]}
             for d in docs]
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return docs


def build(root: Path, out: Path, i18n: Path | None = None) -> list[Doc]:
    """Render the manual under `out` (replaced): English at its root, each other language of
    `i18n` (default: `<root>/dashboard/i18n`, else this checkout's) in `out/<lang>/`.
    Returns the English tree's docs."""
    strings = load_strings(_i18n_dir(root, i18n))
    if out.exists():
        shutil.rmtree(out)
    md = renderer()
    english: list[Doc] = []
    for code, labels in strings.items():
        tree = Tree(code, labels, strings)
        docs = render_tree(root, out / tree.base if tree.base else out, tree, md)
        if code == DEFAULT_LANG:
            english = docs
    return english


def report(root: Path, i18n: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Per language: which sources are translated, stale or untranslated, and which
    translation files are orphans (no source) or have no valid header."""
    languages = [c for c in load_strings(_i18n_dir(root, i18n))]
    all_sources = sources(root)
    out: dict[str, dict[str, list[str]]] = {}
    for lang in languages:
        rows: dict[str, list[str]] = {"translated": [], "stale": [], "untranslated": [], "invalid": []}
        for source in all_sources:
            if lang == source_lang(source):
                continue
            status, _ = translation_state(root, source, lang)
            rows[status].append(source)
            path = root / translation_path(source, lang)
            if path.is_file() and parse_translation(path.read_text(encoding="utf-8"))[0] != source:
                rows["invalid"].append(translation_path(source, lang))
        expected = {translation_path(s, lang) for s in all_sources}
        folder = root / TRANSLATIONS_DIR / lang
        if folder.is_dir():
            for path in sorted(folder.rglob("*.md")):
                rel = path.relative_to(root).as_posix()
                if rel not in expected:
                    rows["invalid"].append(rel)
        out[lang] = rows
    return out


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="python -m tradvia.manual")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render the repository's Markdown into the dashboard's manual")
    b.add_argument("--root", type=Path, default=repo, help="repository root (default: this checkout)")
    b.add_argument("--out", type=Path, default=None, help="output directory (default: <root>/dashboard/manual)")
    c = sub.add_parser("check", help="list missing, out-of-date and invalid translations")
    c.add_argument("--root", type=Path, default=repo)
    c.add_argument("--strict", action="store_true", help="exit 1 on an out-of-date or invalid translation")
    st = sub.add_parser("stamp", help="record the current source fingerprint in translation files")
    st.add_argument("paths", nargs="+", type=Path, help="docs/i18n/<lang>/... files")
    st.add_argument("--root", type=Path, default=repo)
    args = parser.parse_args(argv)
    if args.cmd == "build":
        out = args.out or args.root / "dashboard" / "manual"
        docs = build(args.root, out)
        print(f"manual: {len(docs)} pages per language written to {out}")
        return 0
    if args.cmd == "stamp":
        for path in args.paths:
            path = path if path.is_absolute() else (Path.cwd() / path)
            rel = path.resolve().relative_to(args.root.resolve()).as_posix()
            parts = rel.split("/")
            if len(parts) < 4 or "/".join(parts[:2]) != TRANSLATIONS_DIR:
                print(f"not a translation under {TRANSLATIONS_DIR}/<lang>/: {rel}", file=sys.stderr)
                return 2
            inner = "/".join(parts[3:])
            source = inner if inner in TOP_LEVEL else "docs/" + inner
            text = path.read_text(encoding="utf-8")
            path.write_text(stamp(text, source, (args.root / source).read_text(encoding="utf-8")), encoding="utf-8")
            print(f"stamped {rel} <- {source}")
        return 0
    problems = 0
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for lang, rows in report(args.root).items():
        total = sum(len(rows[k]) for k in ("translated", "stale", "untranslated"))
        print(f"{lang}: {len(rows['translated'])}/{total} translated, {len(rows['stale'])} out of date, "
              f"{len(rows['untranslated'])} not translated yet, {len(rows['invalid'])} invalid")
        for source in rows["stale"]:
            problems += 1
            line = f"{translation_path(source, lang)} is older than {source}: update it, then run stamp"
            print(f"::warning file={translation_path(source, lang)}::{line}" if annotate else f"  stale: {line}")
        for rel in rows["invalid"]:
            problems += 1
            print(f"::warning file={rel}::{rel} has no valid header or no source" if annotate else f"  invalid: {rel}")
    return 1 if args.strict and problems else 0


if __name__ == "__main__":
    sys.exit(main())
