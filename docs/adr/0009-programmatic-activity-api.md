# ADR 0009: Programmatic API for activities (API tokens)

- Status: Accepted
- Date: 2026-10-24
- Issue: #77 (documented in [#76](https://github.com/Tradvia/tradvia/issues/76))

## Context

Every activity in Tradvia starts as a GitHub issue, goes through the planner and
runs after a human approves the plan. Issue #77 asks for a second way in, with two
possible directions:

- **(a) Quick task**: a one-off task without the plan/approval cycle.
- **(b) Programmatic trigger**: an API/CLI to create activities without the web panel.

## Decision

Build **(b)**. Direction (a) stays **Planned**.

- (b) adds no new execution semantics: a card created through the API is the same
  card, it still goes through the planner and the plan approval gate, and every
  rule of ADR 0004 and ADR 0008 keeps applying.
- (a) would create a path that runs without a plan and without approval. That
  changes the safety model of the control plane (ADR 0003) and needs its own
  decision on what a "small and well-defined" task is, who may run it and with
  which budget. It is not decided here.

### API tokens

- A token is created by a signed-in user (session cookie + CSRF, as in ADR 0008) with
  `POST /api/projects/{owner}/{repo}/tokens` and revoked with
  `DELETE /api/projects/{owner}/{repo}/tokens/{id}`. Bearer tokens cannot manage tokens.
- **Scope**: one project, one role (`read`..`admin`, default `triage`) and an
  expiry (default 30 days, at most 90).
- **A token never exceeds its owner**: the creation is refused if the role is above
  the owner's role in the project, and on every request the effective role is the
  lower of the token's role and the owner's *current* GitHub role. Losing the role
  on GitHub takes the power from the token at once.
- **Storage**: the table `api_tokens` keeps only the SHA-256 of the token
  (`auth.hash_token`), the role, the project, `expires_at` and `revoked_at`. The raw
  token (`tvt_` + 256 random bits) is shown once, at creation.
- **Identity**: the row also keeps the owner's GitHub user token, encrypted with the
  same Fernet key as sessions (ADR 0008). Actions therefore run with the owner's
  GitHub identity, never with the server's `GITHUB_TOKEN`. A consequence is that a
  token stops working when the GitHub user token behind it expires or is revoked;
  the owner then creates a new token after signing in again.
- **Request**: `Authorization: Bearer <token>` is read only when there is no session
  cookie. A request with a cookie always follows the cookie rules, CSRF included. The
  Origin/CSRF check is skipped only for bearer requests, which carry no ambient
  credentials. Unknown, expired or revoked tokens answer 401.
- **Allowed actions**: only `create_card` and `start_card` (and reading the card
  audit). Approving plans, merging, deploys and backlog changes stay panel-only.
  The existing role check (`policy.py`, `.tradvia.yml`) still decides each of them.
- **Audit**: `audit_log` gets a `via` column. Token-triggered entries carry the
  owner as `actor_login` and `via = api_token`, so they show up in
  `GET .../cards/{n}/audit`. Creating and revoking tokens is audited too.
- **Idempotency**: `X-Tradvia-Idempotency-Key` works as for the panel (same login,
  same key, 5 s).
- **CLI**: `tradvia token create|revoke` and `tradvia card create` call this HTTP API.

## Consequences

- A leaked token can create and start cards in one project until it expires or is
  revoked, but cannot approve a plan, merge or deploy.
- Rotating `TRADVIA_SECRET_KEY` invalidates tokens, like sessions.
- Quick tasks without a plan remain **Planned**; see
  [docs/programmatic-api.md](../programmatic-api.md).
- Status: Shipped (token auth, card creation and start). Needs the same App setup as
  ADR 0008.
