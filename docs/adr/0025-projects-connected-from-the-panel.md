# ADR 0025: Projects connected from the panel

- Status: Accepted
- Date: 2026-11-10

## Context

The owner wants to use Tradvia on other projects, starting with one carried until now in
Antigravity. Until now a project was served only when listed in the server's
`TRADVIA_PROJECTS`, a configuration value changed by a deploy. Project settings belong to
Tradvia's store, per project (ADR 0018), not to configuration files.

## Decision

- A signed-in person connects a repository on the Settings screen. The list comes from the
  Tradvia GitHub App's installations the person can see; only an admin of the repository
  connects it, checked with the person's own token (ADR 0008).
- The project is stored with who connected it and when. The API serves the connected
  projects next to `TRADVIA_PROJECTS`, at once and after a restart; that variable stays as
  the starting list of an installation.
- Connecting creates the repository's webhook (App permission Webhooks: Read and write) and
  loads every issue, comment, pull request and agent run with the person's token. A refused
  webhook does not undo the connection; the screen names what is missing.
- Only with a session and CSRF, never with an API token; every step is audited.

## Status

| Part | Status |
|---|---|
| Connect from Settings, store, webhook, initial load (#210) | In progress (offline tests) |
| Disconnect and remove a project's data | Planned |
| Tradvia's agent on a connected project's repository (#211) | Planned |
| Onboarding card: `AGENTS.md` and a proposed backlog (#212) | Planned |

## Consequences

- The App asks for one more permission (Webhooks: Read and write); installations accept it once.
- Anyone who is admin of a repository with the App installed can add a project to the
  installation. Tradvia is single-tenant per installation today, so this is the owner's
  team; a public multi-tenant service would need its own decision.
