# ADR 0009: Project connections configured in Tradvia

- Status: Accepted
- Date: 2026-10-24

## Context

ADR 0007 makes Tradvia the single interaction surface. Choosing the AI provider
and model of each step, and storing the API key or CLI token behind it, still
meant editing `.tradvia.yml` and repository secrets by hand on GitHub. An admin
must be able to do it on the "Connections" screen, without the secret ever
coming back to a browser.

## Decision

- **Per role, per project**: the roles of the flow are `planner`, `executor`,
  `reviewer` and `orchestrator`. Each has a provider and a model, stored in
  `project_connections`. The catalog of providers and their allowed models lives
  in `core/src/tradvia/connections.py`; anything outside it is a 400.
- **Precedence**: `.tradvia.yml` (optional `providers` section, `role:
  {provider, model}`) is the default. A value set in Tradvia overrides it as a
  whole (provider and model together). The API and the screen show the origin of
  each value: `tradvia.yml`, `tradvia` or `unset`. The section is validated like the
  rest of the file: unknown roles, providers or models make the policy fail closed.
- **Who**: the `manage_connections` interaction, fixed to `admin`. `.tradvia.yml`
  may not lower it (a lower value makes the file invalid, so it fails closed),
  because a secret is behind it. Routes need a session, the CSRF header and the
  role, checked on every request:
  `GET /api/projects/{owner}/{repo}/connections`,
  `PUT|DELETE .../connections/secrets/{provider}`,
  `PUT .../connections/roles/{role}`.
- **Secrets at rest**: provider secrets (API keys, CLI OAuth tokens) are stored
  only as Fernet ciphertext in `connection_secrets`. The key is derived (HKDF,
  info `tradvia-connection-secrets`) from `TRADVIA_SECRET_KEY`, and is independent
  from the key that protects user tokens (ADR 0008) and from the CSRF key.
  Rotating `TRADVIA_SECRET_KEY` makes stored secrets unreadable: set them again.
- **Never returned**: the API answers only `configured`, `set_at` and `set_by`.
  The request body is never logged, the audit log carries action and provider but
  never the value, and errors carry no secret. `connections.redact` masks known
  values and key-shaped strings in what the executor prints or raises.
- **Audit**: every change (`secret_set`, `secret_replace`, `secret_remove`,
  `set_connection`) and every refused attempt goes to `audit_log`, with the actor.
- **Delivery to the executor**:
  - *GitHub Actions*: when a secret is saved, Tradvia reads the repository
    public key (`GET /actions/secrets/public-key`), seals the value with a
    libsodium sealed box and writes it with `PUT /actions/secrets/{NAME}` and the
    `key_id`, using the admin's own token. Removing the secret calls `DELETE`. If
    GitHub refuses (for example the App lacks *Secrets: Read and write*), the
    secret stays saved in Tradvia and the answer says `github_actions: "failed"`.
  - *Self-hosted runner*: `Connections.runner_env` returns the decrypted secrets
    as environment variables of one execution (`ANTHROPIC_API_KEY`,
    `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`). It goes only to
    the subprocess (`run_claude(env=...)`) and the object never prints its values.
- **New dependency**: `PyNaCl`. GitHub requires the sealed box
  (X25519 + XSalsa20-Poly1305), which `cryptography` does not implement.
- **The "Conexões" nav item was dead** (`status: "soon"` since #58, even after the screen
  shipped): it rendered disabled with a permanent "em breve" badge while the real screen sat,
  reachable, at the bottom of the panel view. Fixed: the link is live once the signed-in person
  has `manage_connections` on the current project, and clicking it scrolls to the screen (it is
  not a separate routed view, so there was nowhere else to send the click).
- **Authentication is not symmetric across providers, and the catalog says so.** Every AI
  provider's `note` (shown on its card) states plainly what is and is not possible today, so
  nothing here is presented as available before it is:
  - *Claude Code*: a subscription login, not an API key, and since ADR 0012 never stored by
    Tradvia: its owner runs `claude setup-token` and keeps the token in their own secrets (the
    server's, for the chat's subscription mode; the repository's Actions secrets, for the agent
    workflows), with `CLAUDE_CODE_OAUTH_TOKEN_OWNER` beside it. The Connections screen shows only
    whether the server has it and whose it is. In the chat the mode answers that owner alone,
    at a deliberate pace (ADR 0012).
  - *Anthropic (API key)*, *Gemini*, *OpenAI*: pasting an API key is the only method. OpenAI
    has since announced "Sign in with ChatGPT" — a subscription login comparable to Claude
    Code's, letting an *approved partner app* spend a subscriber's ChatGPT plan allowance
    instead of a metered key — but Tradvia is not one of those partners (it is a short list of
    named launch partners), so it cannot be offered here yet. Google has no equivalent program
    for Gemini: there is no sanctioned way for a third-party app to use a personal Gemini
    subscription, and Google has enforced against unofficial attempts at it. Neither gap is a
    Tradvia limitation to engineer around; they are decided by Anthropic/OpenAI/Google, so the
    note says so instead of implying a button is merely "not built yet".

## Status by provider

| Provider | Status | What it means today |
|---|---|---|
| GitHub | Shipped | Sign in, roles and actions through the GitHub App; Actions secrets written on save (needs the *Secrets* permission) |
| Anthropic / Claude Code | Partial | Secret and model stored and delivered; the planner calls Anthropic through LiteLLM and `claude.yml` runs Claude Code. The `orchestrator` role reads its own choice (#107; [orchestrator.md](../orchestrator.md)); `planner`/`executor`/`reviewer` still do not |
| Google Gemini | In progress | Secret and model stored and delivered; the `orchestrator` role can use it (#107); `planner`/`executor`/`reviewer` do not yet |
| OpenAI | In progress | Secret and model stored and delivered; the `orchestrator` role can use it (#107); `planner`/`executor`/`reviewer` do not yet |
| GitLab | Planned | Issue #60 |

## Consequences

- The `agent.yml`/`claude.yml` workflows (`planner`, `executor`, `reviewer`) do not read the
  per-role choice yet; wiring them to it is follow-up work, and the table above must change
  with it. The `orchestrator` role, called in-process rather than as a workflow, reads its own
  choice already (#107).
- The GitHub App needs the repository permission *Secrets: Read and write*
  ([docs/github-app.md](../github-app.md)); existing installs must accept it.
- Status: Partial. Code and offline tests shipped.
