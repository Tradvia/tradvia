# ADR 0008: Sessions, CSRF and per-project roles for sign in with GitHub

- Status: Accepted
- Date: 2026-10-21

## Context

ADR 0004 (phase 2) says the panel needs a login before anyone can act through
it, and ADR 0006 makes the project the unit of scope. The panel at
`app.tradvia.org` stays public and read-only without a login.

## Decision

- **Identity**: a Tradvia GitHub App used only for sign in (OAuth user-to-server
  flow, random `state`, PKCE S256). No webhook, minimum permissions. The `state`
  is single use, expires in 10 minutes and is also tied to the browser that
  started the login by a short-lived `__Host-` cookie, so a login cannot be forced
  on someone else.
- **Session**: a row in Postgres `sessions`. The cookie holds 256 random bits;
  the table stores only their SHA-256. Cookie `__Host-tradvia_session` (`Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`). 8 h sliding, 7 days
  absolute. A forged, expired or unknown cookie is treated as anonymous.
- **Token**: the GitHub user token is encrypted with Fernet (key derived with
  HKDF from `TRADVIA_SECRET_KEY`), never sent to the browser, never logged. This
  adds the `cryptography` dependency.
- **CSRF**: state-changing requests that carry a session cookie need `Origin`
  (or `Referer`) equal to `TRADVIA_DOMAIN` and `X-Tradvia-CSRF` equal to
  HMAC(key, session cookie). Webhooks are excluded: they have their own
  signature. Reads stay public.
- **Role**: `role_of(user, project)` asks GitHub for the collaborator permission
  with the user's token, maps it with `policy.role_from_github` to the roles of
  `policy.py` and caches it for 5 minutes per session. Reading `.tradvia.yml`
  policy in the panel comes with the first action.
- **Return path**: `next` is accepted only as `/runs/...`, validated before it is
  stored with the `state`; the callback reads it from the database.
- **Off by default**: without `TRADVIA_GITHUB_APP_CLIENT_ID`,
  `TRADVIA_GITHUB_APP_CLIENT_SECRET` and `TRADVIA_SECRET_KEY` the routes answer 404
  and `/api/auth/me` answers `{"enabled": false}`.

## Addendum: panel actions (ADR 0004 phase 2)

- **Policy read with the user's token**: `.tradvia.yml` is read from the default
  branch with the signed-in user's token (needs the App permission **Contents:
  Read-only**). A 404 means the ADR 0004 defaults, like the agent. A 401/403 or an
  invalid file refuses the action (**fail closed**); the message names the missing
  permission. Successful reads are reused for 60 s only to decide which buttons to
  show; every action reads the file again.
- **Identity**: creating a card, adding `agent:ready` and commenting use the user's
  token, never the server's `GITHUB_TOKEN`.
- **Audit log**: table `audit_log` (project, actor, action, card, result, detail,
  idempotency key, time). Every attempt is recorded, whether it succeeded
  (`ok`), was refused (`denied`) or failed (`failed`). It is read only through
  `GET .../cards/{n}/audit`, which needs a session and at least the `read` role in
  the project and is sent with `Cache-Control: no-store`; it is never public.
- **Idempotency**: the panel sends `X-Tradvia-Idempotency-Key` on every action. A
  repeat of the same key by the same user within 5 s after a successful action (or
  while it is still running) is ignored and answers `{"ok": true, "duplicate": true}`.

## Consequences

- Rotating `TRADVIA_SECRET_KEY` signs everyone out (and, per ADR 0009, makes the
  stored provider secrets unreadable).
- Each `/api/auth/me` can cost one GitHub call per project every 5 minutes.
- Status: Partial. Code and tests shipped; production needs the owner to register
  the App ([docs/github-app.md](../github-app.md)) and a maintainer to wire the secrets.
