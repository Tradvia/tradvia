# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""The in-app manual (ADR 0011): the repository's Markdown rendered to static pages.

`python -m tradvia.manual build` runs at deploy time and writes `dashboard/manual/`:
one HTML page per document, an `index.html` with the table of contents and search,
and `index.json` (path, page, title, section, headings). The Markdown under `docs/`
stays the source of truth; nothing here runs in the API process.

markdown-it-py is imported only inside `renderer()`, so importing this module (or the
rest of the package) never needs it; the deploy step and the dev extra install it.
"""

from __future__ import annotations

import argparse
import html
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
    """CommonMark with tables and strikethrough. Raw HTML in the Markdown is escaped."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

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

STRINGS = {
    "pt": {"manual": "Manual", "search": "Buscar no manual", "back": "← Voltar ao painel",
           "status": "Status e guia rápido", "onPage": "Nesta página", "none": "Nada encontrado.",
           "source": "Fonte no GitHub", "contents": "Sumário",
           "intro": "Toda a documentação do Tradvia, lida aqui mesmo. O status real de cada funcionalidade está em Status e guia rápido.",
           "untranslated": "Esta página ainda não tem tradução; mostrando o texto original.",
           "g.project": "Projeto", "g.guides": "Guias", "g.adr": "Decisões de arquitetura (ADRs)",
           "g.evaluations": "Avaliações"},
    "en": {"manual": "Manual", "search": "Search the manual", "back": "← Back to the dashboard",
           "status": "Status and quick start", "onPage": "On this page", "none": "Nothing found.",
           "source": "Source on GitHub", "contents": "Contents",
           "intro": "All of Tradvia's documentation, read right here. The real status of each capability is under Status and quick start.",
           "untranslated": "",
           "g.project": "Project", "g.guides": "Guides", "g.adr": "Architecture decisions (ADRs)",
           "g.evaluations": "Evaluations"},
    "es": {"manual": "Manual", "search": "Buscar en el manual", "back": "← Volver al panel",
           "status": "Estado y guía rápida", "onPage": "En esta página", "none": "No se encontró nada.",
           "source": "Fuente en GitHub", "contents": "Índice",
           "intro": "Toda la documentación de Tradvia, leída aquí mismo. El estado real de cada funcionalidad está en Estado y guía rápida.",
           "untranslated": "Esta página aún no tiene traducción; se muestra el texto original.",
           "g.project": "Proyecto", "g.guides": "Guías", "g.adr": "Decisiones de arquitectura (ADR)",
           "g.evaluations": "Evaluaciones"},
}

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
.meta { color:var(--muted); font-size:12px; margin-top:32px; }
[hidden] { display:none !important; }
@media (max-width: 800px) { .layout { grid-template-columns:1fr; } aside { position:static; max-height:none; }
  aside summary { padding:4px 0; } }
@media (min-width: 801px) { aside summary { display:none; } }
"""

SCRIPT = r"""
const STRINGS = %(strings)s;
const LANGS = Object.keys(STRINGS);
let lang = (navigator.language || "en").slice(0, 2).toLowerCase();
try { const saved = localStorage.getItem("tradvia.lang"); if (saved) lang = saved; } catch (err) { /* ignore */ }
if (!LANGS.includes(lang)) lang = "en";
function render() {
  const s = STRINGS[lang];
  document.documentElement.lang = lang === "pt" ? "pt-BR" : lang;
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = s[n.dataset.i18n] || ""; });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((n) => { n.placeholder = s[n.dataset.i18nPlaceholder]; });
  const note = document.getElementById("untranslated");
  if (note) note.hidden = !s.untranslated || document.body.dataset.lang === lang;
  document.getElementById("lang").value = lang;
}
document.getElementById("lang").addEventListener("change", (e) => {
  lang = e.target.value;
  try { localStorage.setItem("tradvia.lang", lang); } catch (err) { /* ignore */ }
  render();
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
render();
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _rel(target: str, page: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(page) or ".")


SEARCH_RESULTS = '<ul id="results" class="results" hidden></ul><p id="no-results" class="empty" data-i18n="none" hidden></p>'


def _sidebar(docs: list[Doc], page: str) -> str:
    out = ['<input type="search" id="search" data-i18n-placeholder="search" aria-label="Search">',
           '<details id="doc-list" data-collapse open><summary data-i18n="contents"></summary>']
    for group in GROUPS:
        members = [d for d in docs if d.group == group]
        if not members:
            continue
        out.append(f'<div><h2 data-i18n="g.{group}"></h2><ul>')
        for d in members:
            cls = ' class="current"' if d.page == page else ""
            out.append(f'<li{cls}><a href="{_e(_rel(d.page, page))}">{_e(d.title)}</a></li>')
        out.append("</ul></div>")
    out.append("</details>")
    out.append(SEARCH_RESULTS)
    return "\n".join(out)


def _shell(title: str, page: str, sidebar: str, main: str, content_lang: str = "en") -> str:
    prefix = "../" * page.count("/")
    up = prefix + "index.html"
    panel = prefix + "../"
    docs_page = prefix + "../docs.html"
    options = "".join(f'<option value="{k}">{ {"pt": "PT-BR", "en": "EN", "es": "ES"}[k] }</option>'
                      for k in STRINGS)
    script = SCRIPT % {"strings": json.dumps(STRINGS, ensure_ascii=False)}
    return f"""<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
<!-- Generated by `python -m tradvia.manual build` from the repository's Markdown. Do not edit. -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · Tradvia</title>
<style>{CSS}</style>
</head>
<body data-lang="{content_lang}" data-root="{_e(prefix)}">
<header>
  <div>
    <h1><a href="{_e(up)}">Tradvia · <span data-i18n="manual"></span></a></h1>
    <div class="links">
      <a href="{_e(panel)}" data-i18n="back"></a>
      <a href="{_e(docs_page)}" data-i18n="status"></a>
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


def doc_page(doc: Doc, docs: list[Doc]) -> str:
    toc = ""
    if doc.headings:
        items = "".join(f'<li class="h{h.level}"><a href="#{_e(h.id)}">{_e(h.text)}</a></li>'
                        for h in doc.headings)
        toc = (f'<details class="toc" data-collapse open><summary data-i18n="onPage"></summary>'
               f'<ul>{items}</ul></details>')
    content_lang = "pt" if doc.source == "docs/glossario.md" else "en"
    body = doc.body
    end = body.find("</h1>")
    if end >= 0:  # the page's own title first, then what is on it
        end += len("</h1>")
        body = body[:end] + "\n" + toc + body[end:]
    else:
        body = toc + body
    main = (f'<p id="untranslated" class="note" data-i18n="untranslated" hidden></p>\n'
            f'{body}\n<p class="meta"><a href="{_e(REPO_URL + doc.source)}" data-i18n="source"></a>'
            f' · <code>{_e(doc.source)}</code></p>')
    return _shell(doc.title, doc.page, _sidebar(docs, doc.page), main, content_lang)


def index_page(docs: list[Doc]) -> str:
    parts = ['<h1 data-i18n="manual"></h1>', '<p data-i18n="intro"></p>',
             '<nav class="toc" id="contents"><h2 data-i18n="contents"></h2>']
    for group in GROUPS:
        members = [d for d in docs if d.group == group]
        if not members:
            continue
        parts.append(f'<div id="g-{group}"><h2 data-i18n="g.{group}"></h2><ul>')
        for d in members:
            parts.append(f'<li><a href="{_e(d.page)}">{_e(d.title)}</a></li>')
            for h in d.headings:
                if h.level == 2:
                    parts.append(f'<li class="sections"><a href="{_e(d.page)}#{_e(h.id)}">{_e(h.text)}</a></li>')
        parts.append("</ul></div>")
    parts.append("</nav>")
    # The contents are the index's own text; the sidebar holds only the search.
    sidebar = '<input type="search" id="search" data-i18n-placeholder="search" aria-label="Search">' + SEARCH_RESULTS
    return _shell("Manual", "index.html", sidebar, "\n".join(parts))


def build(root: Path, out: Path) -> list[Doc]:
    """Render every document under `root` into `out` (replaced). Returns the docs written."""
    md = renderer()
    docs = [Doc(s, page_for(s) or "", group_of(s)) for s in sources(root)]
    pages = {d.source: d.page for d in docs}
    for doc in docs:
        render(md, doc, (root / doc.source).read_text(encoding="utf-8"), pages)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for doc in docs:
        target = out / doc.page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(doc_page(doc, docs), encoding="utf-8")
    (out / "index.html").write_text(index_page(docs), encoding="utf-8")
    index = [{"path": d.source, "page": d.page, "title": d.title, "section": d.group,
              "intro": d.text.get("", ""),
              "headings": [{"level": h.level, "text": h.text, "id": h.id, "body": d.text.get(h.id, "")}
                           for h in d.headings]}
             for d in docs]
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return docs


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="python -m tradvia.manual")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render the repository's Markdown into the dashboard's manual")
    b.add_argument("--root", type=Path, default=repo, help="repository root (default: this checkout)")
    b.add_argument("--out", type=Path, default=None, help="output directory (default: <root>/dashboard/manual)")
    args = parser.parse_args(argv)
    out = args.out or args.root / "dashboard" / "manual"
    docs = build(args.root, out)
    print(f"manual: {len(docs)} pages written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
