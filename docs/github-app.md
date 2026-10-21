# Sign in with GitHub: registering the GitHub App

Status: **Partial.** The API routes, sessions, CSRF checks and the panel button
are implemented and tested offline. Login only works in production after the
owner completes the steps below and a maintainer wires the secrets into the
deploy workflow. Actions from the panel (create, start and reorder cards, approve
the plan) are **Partial**: implemented and tested offline, and they only work in
production with sign-in wired and the **Contents: Read-only** permission below.

The Tradvia GitHub App is mainly an **identity provider**: it proves who the
person is and, with their own token, what role they have in each project. It
has no webhook. Panel actions run on GitHub with the **user's own token**, so
the GitHub history shows who acted; the server's `GITHUB_TOKEN` is never used
for them. Design: ADR 0008.

## Owner steps

1. GitHub: **Settings > Developer settings > GitHub Apps > New GitHub App**
   (on the Tradvia organization if the App should belong to it).
2. Fill in:
   - **GitHub App name**: `Tradvia`
   - **Homepage URL**: `https://tradvia.org`
   - **Callback URL**: `https://app.tradvia.org/api/auth/callback`
   - **Request user authorization (OAuth) during installation**: off
   - **Expire user authorization tokens**: your choice (the session lasts at most
     7 days; Tradvia does not use refresh tokens yet)
   - **Webhook > Active**: **off** (no webhook)
3. Permissions (repository), the minimum:
   - **Issues**: Read and write
   - **Pull requests**: Read-only
   - **Contents**: Read-only. Needed to read `.tradvia.yml` from the default
     branch with the user's token, which sets the minimum role of each panel
     action (ADR 0004). Without it every panel action is refused (fail closed)
     with a message naming this permission.
   - **Metadata**: Read-only (mandatory)

   Nothing else, and no account permissions.
4. **Where can this GitHub App be installed?** Any account, so collaborators of
   other registered projects can sign in. Create the App.
5. On the App page copy the **Client ID** and click **Generate a new client
   secret**; copy it once.
6. Generate the key that encrypts user tokens in the database:

   ```
   python -c 'import secrets;print(secrets.token_urlsafe(32))'
   ```

7. Give the three values to a maintainer, who adds them to the `production`
   environment and wires them into the deploy workflow (separate pull request):

   | `.env` variable | Value |
   |---|---|
   | `TRADVIA_GITHUB_APP_CLIENT_ID` | Client ID |
   | `TRADVIA_GITHUB_APP_CLIENT_SECRET` | client secret |
   | `TRADVIA_SECRET_KEY` | the key from step 6 |

   Keep them out of the repository, chat and logs. Changing `TRADVIA_SECRET_KEY`
   signs everyone out (stored tokens can no longer be decrypted).
8. Install the App on each registered project (`TRADVIA_PROJECTS`) so the
   permission lookups work.

## What the API does

| Route | Behavior |
|---|---|
| `GET /api/auth/login?next=/runs/?repo=owner/repo` | Stores a random `state`, a PKCE verifier and the validated return path, then redirects to GitHub. Only `/runs/...` is accepted as `next`. |
| `GET /api/auth/callback` | Checks the one-use `state` (and the browser cookie that started the login), exchanges the code with the PKCE verifier, creates the session and redirects to the stored path. |
| `POST /api/auth/logout` | Deletes the session and clears the cookie. |
| `GET /api/auth/me` | `login`, `avatar_url`, the CSRF value, the role in each project and `can_start` / `can_approve` (what the panel may show as buttons). Never the GitHub token. |
| `POST /api/projects/{owner}/{repo}/cards` | Creates the issue (`title`, `description`), stores it and appends it to the end of the backlog. Needs `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/start` | Adds `agent:ready`. Needs `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/reply` | `decision` = `approve` \| `reject` \| `adjust` (+ `text`) comments "Aprovado", "Rejeitado" or "Ajuste: …". Needs `approve_plan`; 409 unless the agent's last comment is the plan. |
| `POST /api/projects/{owner}/{repo}/backlog` | `{"issue": n, "direction": "up"\|"down"}` or `{"order": [...]}`. Needs `start_card`. |
| `GET /api/projects/{owner}/{repo}/cards/{n}/audit` | Audit entries of the card and, for who may approve, the full plan. Needs at least the `read` role; `Cache-Control: no-store`. |

Action routes answer 401 without a session, 403 for a missing CSRF or an
insufficient role (the message names the required role), 404 for a project not in
`TRADVIA_PROJECTS` or a card that is not in the project, and 409 for a card in the
wrong state. The panel sends a random `X-Tradvia-Idempotency-Key`; the same key
again within 5 seconds is ignored.

- Cookie `__Host-tradvia_session`: `Secure`, `HttpOnly`, `SameSite=Lax`,
  `Path=/`, no `Domain`. Valid 8 h from the last use, at most 7 days from login.
- Every `POST`/`PUT`/`PATCH`/`DELETE` with a session cookie needs the header
  `X-Tradvia-CSRF` (value from `/api/auth/me`) and an `Origin` (or `Referer`)
  equal to `https://$TRADVIA_DOMAIN`. Webhooks keep their own signature.
- The user token is stored encrypted (Fernet, key derived from
  `TRADVIA_SECRET_KEY`), is never sent to the browser and is never logged.
- Roles come from `GET /repos/{owner}/{repo}/collaborators/{login}/permission`
  with the user's token and are cached for 5 minutes per session.
