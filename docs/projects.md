<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
# Projects

The **project** is the unit of scope for everything Tradvia stores (ADR 0006, phase 0).
Status: **Shipped** for storage and API. Memory, skills and per-project policies are
**Planned** for the next phases.

## Model

Table `projects`:

| Column | Meaning |
|---|---|
| `id` | `bigserial` primary key |
| `slug` | unique, derived from the repository (`Tradvia/tradvia` → `tradvia-tradvia`) |
| `kind` | `github_repo` (the only kind today) |
| `external_id` | unique, `owner/repo` |
| `created_at` | creation time |
| `settings` | `jsonb`, empty for now |

`deliveries`, `issues`, `comments`, `pull_requests` and `workflow_runs` have
`project_id bigint NOT NULL REFERENCES projects (id)` and the primary key
`(project_id, id)`: GitHub ids are only unique inside a project's data set, and a
delivery id is recorded once per project. Indexes on `issue_number` start with
`project_id`.

### Migration

`schema.sql` is idempotent and runs on the first database connection. It creates
`projects`, inserts `Tradvia/tradvia`, adds `project_id`, assigns every existing row
with `NULL` to `Tradvia/tradvia`, sets `NOT NULL` and replaces single-column primary
keys by `(project_id, id)` only when they are not composite yet. Running it again
changes nothing.

## Registering projects

`TRADVIA_PROJECTS` is a comma separated list of `owner/repo` (default: `TRADVIA_REPO`,
which defaults to `Tradvia/tradvia`). Only those repositories are accepted; each one
gets a `projects` row the first time it is used.

- **Webhook**: the project comes from `repository.full_name`. A repository that is
  not registered is answered `202` and nothing is written.
- **Backfill**: `tradvia backfill --project owner/repo` (default `TRADVIA_REPO`); the
  repository must be listed in `TRADVIA_PROJECTS`.
- **Proxy and store routes**: the `{repo}` of `/api/github/repos/{repo}/...` and
  `/api/store/repos/{repo}/...` must be registered, otherwise `404`.

## Isolation

Every `Store` method takes `project_id` first and every query on a stored table
filters by it; a test scans `store.py` to enforce it. Two projects can hold the same
ids and issue numbers without ever seeing each other's rows.

## Visibility

For now every registered project is public. Visibility by role arrives with login
(ADR 0004); `GET /api/projects` already returns a `public` flag for that.

## API

- `GET /api/projects` → `[{"slug", "kind", "external_id", "public": true}]`.
- `GET /api/store/health` → `{"ok", "issues", "runs", "last_delivery", "projects":
  {"owner/repo": {"issues", "runs", "pull_requests", "last_delivery"}}}`. The
  top-level `issues`, `runs` and `last_delivery` are **obsolete** (first registered
  project); use `projects`.
- `GET /api/store/repos/{repo}/issues|pulls|actions/workflows/{agent|claude}.yml/runs`
  as before, scoped to the project `{repo}`.

## Dashboard

The header has a project selector fed by `/api/projects`. It keeps `?repo=` in the URL
and is hidden when the API is not available.
