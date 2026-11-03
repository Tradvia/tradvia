# Sign in with GitHub: registering the GitHub App

Status: **Partial.** The API routes, sessions, CSRF checks and the panel button
are implemented and tested offline. Login only works in production after the
owner completes the steps below and a maintainer wires the secrets into the
deploy workflow. Actions from the panel (create, start and reorder cards, approve
the plan) are **Partial**: implemented and tested offline, and they only work in
production with sign-in wired and the permissions below.

Reviewing and merging PRs, approving deploys and the post-deploy operation
(ADR 0007, #56) are **Partial**: tested offline, and they work in production
once the App has the permissions below and the installations accept them.

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
   - **Issues**: Read and write. Create cards, add `agent:ready`, comment.
   - **Pull requests**: Read and write. Read the PR, its files and review
     comments; `PUT /pulls/{n}/update-branch`.
   - **Contents**: Read and write. Read `.tradvia.yml` from the default branch
     with the user's token, which sets the minimum role of each panel action
     (ADR 0004); without it every panel action is refused (fail closed) with a
     message naming this permission. Write: `PUT /pulls/{n}/merge` (squash) and
     `DELETE /git/refs/heads/{branch}` (delete the branch after the merge).
   - **Actions**: Read and write. `GET /actions/runs` (waiting deploys),
     `POST /actions/runs/{id}/cancel` (replaced deploys),
     `POST /actions/runs/{id}/approve` (a PR's workflows that GitHub holds, see the
     last section of this page) and
     `POST /actions/workflows/deploy-oci.yml/dispatches` (post-deploy operation).
   - **Deployments**: Read and write. `GET` and
     `POST /actions/runs/{id}/pending_deployments` (approve or reject; the
     person must also be a required reviewer of the environment).
   - **Secrets**: Read and write. Project connections (ADR 0019): Tradvia reads
     the repository public key and writes or deletes the `ANTHROPIC_API_KEY`,
     `GEMINI_API_KEY`, `OPENAI_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` Actions
     secrets when an admin saves or removes a provider secret. Without it the
     secret is still saved in Tradvia and the screen says it could not be written
     to GitHub Actions.
   - **Checks**: Read-only. `GET /commits/{sha}/check-runs`: the `core-tests`,
     `license-boundary` and `cla` checks on the PR screen and before a merge.
   - **Metadata**: Read-only (mandatory)

   Nothing else, and no account permissions. **Existing installs must accept
   the new permissions:** changing them on the App page does not apply until
   the owner (and each organization that installed it) approves the request in
   the installation settings. Until then the affected buttons answer with a
   message naming the missing permission. A session opened before the update keeps
   its old token, so the person signs in again after the owner accepts it.
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
   signs everyone out (stored tokens can no longer be decrypted) and makes the
   provider secrets saved on the Connections screen unreadable: set them again.
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
| `GET /api/projects/{owner}/{repo}/pulls/{n}` | The PR: title, description, executor summary and cost, files with +/−, the required checks, review comments and which buttons the user may use. Needs `read`. |
| `GET /api/projects/{owner}/{repo}/pulls/{n}/files/{path}/diff` | The text diff of one file, on demand. Needs `read`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/merge` | Squash merge with the PR's Conventional Commit title, then deletes the branch. Body `{"ignore_cla": true}` ignores only a failed `cla` check (recorded in the audit); any other failed required check refuses the merge. Needs `merge_pr`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/update-branch` | Brings the PR branch up to date with main. Needs `update_branch`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/approve-workflows` | Approves the runs GitHub holds for approval on the PR's head commit. Body `{"sha": "<head>"}`; 409 if the PR has new commits or nothing waits. Needs `merge_pr`. |
| `GET /api/projects/{owner}/{repo}/deployments/pending` | Deploys waiting for approval (older ones in the same queue are `replaced`) and the backlog order stored in Tradvia. Needs `read`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/approve` and `/reject` | Reviews the pending deployment with the user's token; the user must be a reviewer of the environment. Needs `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/cancel` | Cancels a replaced deploy (any other run is refused). Needs `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/operations/post-deploy` | Dispatches `deploy-oci` with `{"backfill": bool, "backlog": "44 50 45"}`; the panel pre-fills the backlog from the stored order. Needs `approve_deploy`. |

Action routes answer 401 without a session, 403 for a missing CSRF or an
insufficient role (the message names the required role), 404 for a project not in
`TRADVIA_PROJECTS` or a card that is not in the project, and 409 for a card in the
wrong state. The panel sends a random `X-Tradvia-Idempotency-Key`; the same key
again within 5 seconds is ignored.

Every pull request and deploy action is audited (who, what, result; `ignore_cla`
appears in the detail) and runs with the user's token, never the server's.
When GitHub refuses a call because the **App** lacks a permission (`Resource not
accessible by integration`), the answer names it, e.g. *The GitHub App needs
Actions: Read and write*; Contents for merge and delete branch, Pull requests for
update-branch, Deployments for approve and reject. A refusal for the person
reads *You are not a reviewer of the production environment.* Other clear
messages: no role in the project, *Merge refused: the required check(s) failed*, *Merge refused: the required check(s) wait for their workflows to be approved*, and *The branch is behind main*.

The new `.tradvia.yml` interactions are `merge_pr`, `update_branch` and
`approve_deploy` (also approve, reject, cancel and the post-deploy operation);
all default to `maintain`. See `docs/tradvia.example.yml`.

- Cookie `__Host-tradvia_session`: `Secure`, `HttpOnly`, `SameSite=Lax`,
  `Path=/`, no `Domain`. Valid 8 h from the last use, at most 7 days from login.
- Every `POST`/`PUT`/`PATCH`/`DELETE` with a session cookie needs the header
  `X-Tradvia-CSRF` (value from `/api/auth/me`) and an `Origin` (or `Referer`)
  equal to `https://$TRADVIA_DOMAIN`. Webhooks keep their own signature.
- The user token is stored encrypted (Fernet, key derived from
  `TRADVIA_SECRET_KEY`), is never sent to the browser and is never logged.
- Roles come from `GET /repos/{owner}/{repo}/collaborators/{login}/permission`
  with the user's token. A GitHub answer is cached for 5 minutes per session; a failed
  check is not cached, and the next request asks again. With no role, the answer says
  why, in the action and read routes and per project in `GET /api/auth/me`
  (`role_reason` and `notice`):
  - The sign-in token was revoked or expired (401): *Your GitHub sign-in is no longer valid*. Sign in again.
  - GitHub refused the check (403): the App needs `Metadata: Read-only`. An owner accepts the update on the installation, then the person signs in again.
  - GitHub's rate limit (403 or 429): wait a few minutes.
  - GitHub could not be reached, or answered in an unexpected way: try again.
  - GitHub says the person is not a collaborator (404, or `permission: none`): the plain answer, *You have no access* or *You need at least the 'read' role*.

## Approving a pull request's waiting workflows

GitHub holds the workflow runs of some pull requests until someone with write
access approves them. That happens to runs from forks and, as observed on PR #95
(issue #97), to runs of the agent's own pull requests. While they wait, the
required checks read *awaiting approval* rather than failed, the merge is refused
until they run, and the PR screen shows **Approve workflows** to a person who may
merge (`merge_pr`, `maintain` by default).

The button calls `POST /actions/runs/{id}/approve` for each run that GitHub holds
on the PR's head commit, with the person's own token, and writes an
`approve_workflows` audit entry. The panel sends the commit it showed: if the PR
got a new commit since, nothing is approved and the answer is 409. Approving
starts that commit's workflows with the repository's secrets, so approve only
code you have read.

Whether GitHub asks for this approval at all is decided by the repository's
Actions settings (Settings → Actions → General, *Fork pull request workflows from
outside collaborators*). Tradvia could not read those settings from the
environment where this was built, so the repository owner should check them
there.
