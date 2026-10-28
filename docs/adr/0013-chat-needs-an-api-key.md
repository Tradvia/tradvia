# ADR 0013: the orchestrator chat runs only on an AI provider with an API key

- Status: Accepted
- Date: 2026-10-27

## Context

The orchestrator chat ([orchestrator.md](../orchestrator.md)) first ran only on Claude Code
headless, on the subscription token (`CLAUDE_CODE_OAUTH_TOKEN`). #107 let a project choose
its own `orchestrator` connection ([ADR 0009](0009-project-connections.md)) — Anthropic, OpenAI
or Gemini through LiteLLM — but kept Claude Code as the fallback whenever no provider and key
were configured. #109 proposed keeping that subscription path as an owner-only mode.

Two problems with any subscription path for the chat:

1. **It ties the chat to one vendor.** Only Anthropic offers a subscription login a CLI can
   use; OpenAI's "Sign in with ChatGPT" is for approved partners and Google has none (ADR
   0009). A chat that works without configuration only on Claude is not provider-neutral.
2. **It is a personal login.** A subscription token belongs to one person and must not serve
   other people's requests (#109). A project chat serves everyone with `start_card`.

An API key is the one method all three providers support, it is billed per use to the project
that saved it, and the project's monthly cost cap (#131) can bound it.

## Decision

- The chat runs **only** when the project's `orchestrator` connection is an AI provider
  (`anthropic`, `openai`, `gemini`) with a model and that provider's API key saved.
- Otherwise the chat is **off**: `GET …/chat` answers `can_chat: false` with
  `chat_off_reason: "no_api_key"` for anyone who may start cards, the panel shows how to
  configure it, and `POST …/chat` answers `409` without storing the message.
- **No fallback** to Claude Code and **no subscription mode** for the chat, not even for the
  owner. #109/#110 (owner-only subscription mode) are closed as not planned.
- The `claude-code` connection (subscription token) keeps running the **executor** only
  (`claude.yml`); the chat never reads it. The orchestrator role keeps refusing CLI providers.

## Consequences

- A new project has the chat off until an admin sets the orchestrator role and its key on the
  Connections screen. The panel says so instead of failing a message.
- The API image needs neither Node.js nor the Claude Code CLI for the chat; the open fixes
  that install them for this purpose (#106, #148) lose that reason.
- Every chat message is billed to the project's own API key.
- Revisiting a subscription path later needs a new ADR, not a flag.
