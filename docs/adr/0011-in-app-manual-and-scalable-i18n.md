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

## Delivery

| Order | Capability | Status |
|---|---|---|
| 1 | `scripts/build-manual.py`, static `dashboard/manual/`, wired into `deploy-oci.yml` | Planned |
| 2 | `dashboard/i18n/{pt,en,es}.json` replacing hand-duplicated spans (all three ship together) | Planned |
| 3 | Manual content — guide pages and ADRs — translated into `pt`/`en`/`es` as `docs/i18n/<lang>/...`, English fallback and staleness check when a translation is missing or behind | Planned |
