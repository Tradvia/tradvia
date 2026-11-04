# ADR 0011: an in-app manual, and i18n across PT-BR, English and Spanish

- Status: Accepted
- Date: 2026-10-24

## Context

`dashboard/docs.html` (behind the panel's "Ajuda"/"Help" link) is two
different things wearing one page:

1. A short, bilingual (PT/EN) quick-start and capability-status section,
   written directly in the page.
2. A **"Documentação técnica" list that is ten ADRs and four `docs/*.md`
   files, each just a link to `github.com/Tradvia/tradvia/blob/main/...`.**
   Nothing there is rendered inside Tradvia; reading it means leaving the
   panel for GitHub. ADR 0007's whole premise — Tradvia as the single
   interaction surface — stops at the help link.

The bilingual part doesn't scale either: every string is duplicated by hand
as `<span class="pt">…</span><span class="en">…</span>` (33 pairs today),
with CSS hiding whichever one doesn't match `<html lang>`. A third language
means hand-tripling every string, in the same file as the content.

Card #101 asks for a real, navigable manual inside Tradvia that does not
depend on GitHub to read, and i18n infrastructure that scales. This ADR
treats those as two separate decisions — they have different costs and
different owners over time.

## Decision

### 1. The manual is pre-rendered at deploy time, served as static HTML

`docs/*.md` and `docs/adr/*.md` stay the source of truth, versioned where
they already are — this is not about moving content, only about how it
reaches the reader:

- A deploy-time script (`scripts/build-manual.py`) renders every `docs/*.md`
  and `docs/adr/*.md` to static HTML fragments under `dashboard/manual/`,
  with a generated table of contents (`dashboard/manual/index.json`:
  path, title, section). It runs as a step in `deploy-oci.yml`, the same
  place `backfill`/`backlog` post-deploy operations already run — no new
  service, no new always-on dependency.
- The live API process gets **no new dependency**: rendering happens once
  per deploy, not per request. The one new dependency this adds
  (a Markdown-to-HTML library — exact package picked during
  implementation) is scoped to the build script only, with the reason
  stated in that PR per `CLAUDE.md`.
- `dashboard/manual/` is served as static files next to `dashboard/index.html`
  and `docs.html` (same host, same `/runs/` path prefix) — no new route
  needed in `api.py`.
- `test_every_adr_is_linked`-style coverage extends to the manual: a test
  asserts every `docs/*.md` and `docs/adr/*.md` file has a page in
  `dashboard/manual/index.json`, so a new doc can't silently go unlisted the
  way nothing enforced the GitHub-link list staying complete before.
- The existing capability-status table and quick-start guide stay exactly
  where they are (`docs.html`), unchanged in substance — the manual is new
  surface area, not a replacement for the part of `docs.html` that already
  honors `CLAUDE.md`'s Shipped/In progress/Partial/Planned rule.

**Why pre-rendered over serving Markdown live from the checkout:** the API
process would need filesystem access to a repository checkout (today only
`review_pr`'s runner has that, via `TRADVIA_RUNNER_DIR_*`, and only on the
self-hosted runner — not guaranteed on the API host), plus a Markdown
renderer in the always-running process. Pre-rendering at deploy time needs
neither: the output is static files, and the content is exactly what was
on `main` at deploy time (same freshness the dashboard itself already has,
rebuilt on every push that touches `dashboard/**`).

### 2. UI strings move to locale files, three languages required

Two different things, two different fixes:

- **UI chrome** (nav labels, the quick-start steps, capability badges'
  text) moves from hand-duplicated spans to `dashboard/i18n/<lang>.json`,
  loaded by `docs.html`/`index.html` at runtime and selected the same way
  the `#lang` selector already works. **Three languages are a hard
  requirement, not a starting point: `pt` (PT-BR), `en`, and `es`** — all
  three ship together in this delivery, not `pt`/`en` now and `es` later.
  Adding a further language beyond these three is still just one JSON file
  and extending the `<select>` — no HTML edits to existing content.
- **Manual content** (the rendered `docs/*.md` / ADRs): the owner has
  decided — everything in the manual that can reasonably be translated
  ships in all three languages, ADRs included, not only the user-facing
  guide pages.
  `CLAUDE.md`'s "Docs and code in English" stays the rule for the
  repository's own source documents (the canonical ADR a contributor reads
  and edits in a PR is still the English file under `docs/adr/`) — this
  ADR does not change that. What it adds is a **translated rendering** on
  top: `docs/i18n/<lang>/adr/*.md` and `docs/i18n/<lang>/*.md` (mirroring
  `docs/`'s own layout, one file per language per source file) hold the
  PT-BR and Spanish versions, and `scripts/build-manual.py` renders
  whichever translation exists for the language the reader picked, falling
  back to the English source when a translation is missing — "everything
  that's possible" means the manual is never blocked on 100% translation
  coverage; a page simply shows in English with a visible note until its
  translation lands.
  Staleness is a required check, not an optional one: the build script (or
  a `make` target CI runs) compares each translated file's last-touched
  commit against its English source's and fails the build — or, at
  minimum, visibly flags the page as "translation may be out of date" —
  when the source changed more recently than the translation. Silent drift
  is not acceptable given three languages are now a standing commitment.

## Consequences

- Reading any linked doc or ADR from the panel never leaves Tradvia again.
- Adding a UI language beyond the three required (`pt`, `en`, `es`) is a
  JSON file, not an HTML edit.
- The live API gains no new runtime dependency; the one new dependency this
  introduces lives in a deploy-time script only.
- Manual-content translation (ADRs included) is a real, ongoing
  authoring/maintenance cost, not just infrastructure — every new or
  changed ADR and doc now has translation work to schedule, not only the
  English source to write.
- A page without a translation yet degrades to English with a visible
  note, rather than blocking the whole manual on full coverage; a
  translation that falls behind its English source is flagged, not
  silently stale.

## As built (part 1, #101)

Where the implementation differs from the decision above, and why:

- **A module, not a script.** The renderer is `core/src/tradvia/manual.py`, run as
  `python -m tradvia.manual build` (or `make manual`), instead of `scripts/build-manual.py`.
  Living in `core/` puts it under `core-tests` like every other piece of code; it is still
  never imported by the API, and it imports the Markdown library only inside `renderer()`.
- **Full pages, not fragments.** Every document becomes a complete page
  (`dashboard/manual/adr/0011-….html`, `dashboard/manual/deploy-oci.html`) with the list of
  documents, the page's own contents and stable heading anchors (GitHub-style, deduplicated),
  so a section link works without JavaScript. `dashboard/manual/index.html` is the table of
  contents; `index.json` keeps path, page, title, section and headings, plus each section's
  plain text for the search.
- **Search is full text, per section.** It loads `index.json` on the first keystroke and
  lists matching sections (page › heading, with a snippet), accent-insensitive.
- **What is rendered:** `README.md`, `CONTRIBUTING.md`, `docs/*.md`, `docs/adr/*.md` and
  any other `docs/**/*.md` except `docs/i18n/` (reserved for part 3's translations). A link
  between two of them stays in the manual; a link to any other file of the repository goes
  to GitHub.
- **Dependency:** `markdown-it-py` (MIT), pinned to an exact version in the deploy step and
  `>=3` in the `dev`/`manual` extras for the tests. Raw HTML in Markdown is escaped, and
  images from other hosts become links, since the panel's CSP does not load them.
- **Chrome in three languages already.** The manual's own labels (search, contents, back)
  ship in `pt`, `en` and `es`, sharing the panel's `tradvia.lang` choice; the content is
  the English source with a visible note until part 3 brings translations.
- The deploy also runs when only `docs/**`, `README.md` or `CONTRIBUTING.md` change, so the
  manual is never older than `main`.

## As built (part 2, #101)

- `dashboard/i18n/<lang>.json` holds flat, namespaced keys: `docs.*` for the docs page,
  `manual.*` for the manual's labels, `lang.label`/`lang.html`/`lang.name` for the language
  itself. `pt`, `en` and `es` ship with the same keys; a test fails on any missing or extra key.
- `docs.html` has no strings of its own any more: elements carry `data-i18n` (text) or
  `data-i18n-html` (text with `<code>`, `<strong>`, `<em>` or a link to the manual, GitHub or
  an anchor). The page fetches the chosen language plus `en` as the fallback, and rebuilds
  that small markup element by element: nothing is inserted as raw HTML.
- The manual reads its labels from the same files at build time, and its language selector
  lists every file found: adding a language is one JSON file (plus one `<option>` in
  `docs.html`), which a test proves with a fourth language.
- The capability status words (Shipped, In progress, Partial, Planned) stay in English in
  every language: they are the CLAUDE.md terms, and the badges are checked against ADR 0007.
- Not in this part: the main panel (`index.html`) still has its own inline `pt`/`en`
  dictionary and help FAQ (row 2b below).

## Delivery

| Order | Capability | Status |
|---|---|---|
| 1 | `python -m tradvia.manual build`, static `dashboard/manual/`, wired into `deploy-oci.yml` (#101) | Shipped |
| 2 | `dashboard/i18n/{pt,en,es}.json` replacing hand-duplicated spans (all three ship together): the docs page and the manual (#101) | Shipped |
| 2b | The main panel (`index.html`, its inline `pt`/`en` dictionary and help FAQ) moved to the same files, adding `es` | Planned |
| 3 | Manual content — guide pages and ADRs — translated into `pt`/`en`/`es` as `docs/i18n/<lang>/...`, English fallback and staleness check when a translation is missing or behind | Planned |
