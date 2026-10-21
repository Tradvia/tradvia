# ADR 0007: Sessions, CSRF and per-project roles for sign in with GitHub

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

## Consequences

- Rotating `TRADVIA_SECRET_KEY` signs everyone out.
- Each `/api/auth/me` can cost one GitHub call per project every 5 minutes.
- Status: Partial. Code and tests shipped; production needs the owner to register
  the App ([docs/github-app.md](../github-app.md)) and a maintainer to wire the secrets.
