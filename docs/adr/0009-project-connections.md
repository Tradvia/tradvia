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

## Status by provider

| Provider | Status | What it means today |
|---|---|---|
| GitHub | Shipped | Sign in, roles and actions through the GitHub App; Actions secrets written on save (needs the *Secrets* permission) |
| Anthropic / Claude Code | Partial | Secret and model stored and delivered; the planner calls Anthropic through LiteLLM and `claude.yml` runs Claude Code. The choice per role is not read yet by the workflows |
| Google Gemini | In progress | Secret and model stored and delivered; no executor uses them yet |
| OpenAI | In progress | Secret and model stored and delivered; no executor uses them yet |
| GitLab | Planned | Issue #60 |

## Consequences

- The workflows do not read the per-role choice yet; wiring the planner and
  executor to it is follow-up work, and the table above must change with it.
- The GitHub App needs the repository permission *Secrets: Read and write*
  ([docs/github-app.md](../github-app.md)); existing installs must accept it.
- Status: Partial. Code and offline tests shipped.
