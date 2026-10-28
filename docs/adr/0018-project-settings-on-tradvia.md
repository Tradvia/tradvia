# ADR 0018: project settings live on Tradvia, per project, not in .tradvia.yml (budget first)

- Status: Accepted
- Date: 2026-10-28

## Context

Tradvia is multi-tenant, and the tenant is the project: one Git repository. Everything (cards,
runs, costs, tokens, connections, audit) is bound to that project and never crosses into
another. That complete isolation is what makes Tradvia trustworthy.

The per-card budget (ADR 0006) and the monthly ceiling (#131) were read from `.tradvia.yml`. A
setting in the repository mixes the project's operation with its code. Changing a budget needs
a commit and a pull request, and the panel cannot show or change it. The owner's rule is that
these customizations belong to the project on Tradvia and are not written in configuration
files.

## Decision

1. **Project settings are stored with the project** in `project_settings` (key = project and
   setting name), changed on Tradvia's **Settings** screen, and audited. One project never reads
   or writes another project's settings.
2. **Budget first.** The `budget` setting holds the per-card limits (`max_minutes`, `max_steps`,
   `max_usd`) and the optional `monthly_usd`. A new project has the safe per-card defaults
   (30 min, 40 steps, $5) and **no monthly ceiling**.
3. **Who changes it:** whoever holds the `budget_control` interaction in that project (admin by
   default, ADR 0004), with a signed-in session and CSRF. Anyone with a role in the project sees
   it; nobody without one does. Input is validated strictly; a stored value is read leniently, so
   nothing can remove or loosen a limit.
4. **The file has no say.** A `budget` section in `.tradvia.yml` is accepted and ignored, and the
   Settings screen says so. There is one source of truth.
5. **The agent reads the same setting.** The card flow in GitHub Actions reads it from the store
   (`DATABASE_URL`). With a store configured but unreadable, the card does not start, so it never
   runs past the project's own limits. Without a store it runs with the defaults and no ceiling.

## Consequences

- An admin changes a budget on the panel, with effect on the next card start, and the change is
  in the project's audit log.
- Projects stay isolated: the setting is keyed by project like everything else.
- Interactions (roles per action) and the risk policy followed the same path (#177): the setting
  `policy` of the project, edited on Settings, Project roles and risk. Changing it needs `admin`,
  fixed, because it decides who may do everything else; `manage_connections` stays locked to
  `admin`. The `interactions` and `risk` sections of `.tradvia.yml` are ignored (even when
  invalid) and the panel names them. A stored value that cannot be read keeps the ADR 0004
  defaults; a store that cannot be reached fails closed, on the panel and in the agent.
- Repository hosts: today a project is a GitHub repository. GitLab (#60) and Bitbucket will key
  their projects the same way, so settings stay bound to the repository whatever the host.

## Status

| Capability | Status |
|---|---|
| Budget setting per project, Settings screen, audit, agent reads it | In progress: offline tests; checked with a mocked API |
| Interactions and risk policy as project settings, Settings screen, audit, agent reads it | In progress: offline tests; checked with a mocked API |
