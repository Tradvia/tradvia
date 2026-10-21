# ADR 0007: Tradvia as the single interaction surface

- Status: Accepted
- Date: 2026-10-20

## Context

Today the owner runs Tradvia from four places: an AI chat that acts as the
orchestrator (creates cards, starts them, reviews plans and pull requests),
GitHub issues (approving plans), GitHub pull requests (merging) and GitHub
Actions (approving deploys). On 2026-10-20 that split cost several hours: plan
approvals typed in the AI chat never reached the agent, two cards timed out
waiting for an answer in GitHub, and a deploy waiting for approval blocked the
queue for a day.

The target is that every interaction happens in Tradvia. AI vendors (Anthropic,
Google, OpenAI and others) and repository hosts (GitHub, GitLab) are configured
once and then used through Tradvia; nobody needs to open their screens to run a
project.

## Decision

1. **One screen for every human gate.** Creating, ordering and starting cards,
   answering plans (Aprovado / Rejeitado / Ajuste), reviewing and merging pull
   requests, approving or rejecting deploys and running post-deploy operations
   are actions in Tradvia, executed with the signed-in user's own identity on
   the provider, so the provider's history still shows who did what (ADR 0004).
2. **The orchestrator is a Tradvia agent.** The conversation that plans the
   backlog, starts cards, proposes adjustments and reviews pull requests moves
   into a per-project conversation in Tradvia. It uses a closed set of Tradvia
   tools. It never approves plans, merges or deploys: those gates stay human.
3. **Providers are connections.** AI providers and repository hosts are
   configured per project in Tradvia, with secrets encrypted at rest and never
   returned to the browser. Coding agents (Claude Code, Gemini CLI, Codex CLI)
   are adapters behind one interface; repository hosts (GitHub, GitLab) are
   adapters behind another. A provider is listed as supported only when its
   adapter ships; tools without a stable headless CLI or API stay Planned.
4. **Waiting is durable.** A card waiting for a human pauses and resumes when
   the answer arrives, however late (ADR 0006, durable execution), instead of
   timing out after an hour.
5. **Humans are told, not polled.** An inbox and web push notifications bring
   the owner to the exact pending action.

## Delivery order and status

| Order | Card | Capability | Status |
|---|---|---|---|
| 1 | #45 | Sign in with GitHub, sessions, per-project roles | Planned |
| 2 | #46 | Create, start, order and answer cards in Tradvia | Planned |
| 3 | #56 | Review and merge PRs, approve deploys in Tradvia | Partial |
| 4 | #40 | Durable waiting and resume | Planned |
| 5 | #47 | Inbox and web push notifications | Planned |
| 6 | #57 | Orchestrator conversation inside Tradvia | Planned |
| 7 | #58 | Project connections for AI and repository providers | Planned |
| 8 | #59 | Coding agent adapters (Gemini CLI, Codex CLI) | Planned |
| 9 | #60 | GitLab as a repository provider | Planned |

Already shipped and reused: project scope (#44), backlog in Tradvia (#50),
dashboard reads from the store (#36), deploy-time operations (#54).

## Pull requests and deploys (#56)

Status: **Partial** (implemented and tested offline; production needs the
GitHub App permissions in `docs/github-app.md`).

- The card shows the PR and its checks, read with the user's token. The merge
  is a squash with the PR's Conventional Commit title, followed by deleting the
  branch. A failed required check (`core-tests`, `license-boundary`, `cla`)
  refuses the merge; only `cla` can be ignored, explicitly, and the audit log
  records it.
- Merging, updating a branch, approving or rejecting a deploy, cancelling a
  replaced deploy and running the post-deploy operation are new
  `.tradvia.yml` interactions (`merge_pr`, `update_branch`, `approve_deploy`),
  all defaulting to `maintain`. Being a reviewer of the `production`
  environment is still decided by GitHub, with the user's token.
- A waiting deploy superseded by a newer one in the same queue is shown as
  "replaced" and is the only kind the panel can cancel.
- The post-deploy operation dispatches `deploy-oci` with `backfill` and
  `backlog` (#54); the backlog is pre-filled from the order stored in Tradvia.

## Consequences

- One-time setup still happens on the providers: registering the Tradvia
  GitHub App (and later a GitLab OAuth app) and making the owner a reviewer of
  the `production` environment. After that, day-to-day work needs no other
  screen.
- Tradvia holds user tokens and provider secrets: encryption at rest, CSRF,
  short sessions and an audit log are requirements of the first card (#45), not
  later hardening.
- Until #46 ships, plan approvals still happen in the issue thread.
