# Working on Tradvia with your own agent

For any AI agent, from any provider (Claude Code, Kiro, Gemini CLI, Codex, Cursor, Manus, …),
and for the person who runs it. You work **with** people and other agents on the same
backlog: take one card, say who you are, report on the card, and leave the decisions to a
person.

The project rules are in [CLAUDE.md](CLAUDE.md) and apply to every agent, whatever its name.
Read them before you change code. The most important ones:

- `core/` never imports from `ee/` (`make boundary`).
- Every new source file starts with its SPDX header.
- Never modify `LICENSE.md`, `CLA.md`, `CODEOWNERS`, `.github/rulesets/` or the CLA workflow.
- Never commit secrets, tokens or `.env` files.
- Public text states the real status of each capability: Shipped, In progress, Partial, Planned.

## Before you start (the person, once)

1. Accept the GitHub invitation to `Tradvia/tradvia` (role *write*), and sign the CLA on your
   first pull request: comment `I have read the CLA Document and I hereby sign the CLA`.
2. Get a Tradvia token for this project and connect your agent to the Tradvia MCP server:
   [docs/mcp.md](docs/mcp.md). Keep the token out of git.

## The loop (the agent, every card)

1. **`read_briefing`.** How the project works, this guide, the free cards in backlog order,
   who holds which card and with which agent, the cards with Tradvia's own agent or an open
   pull request, and what waits for a person.
2. **Pick a free card** that fits your specialties. Read it with `read_card` (and `read_plan`
   if it has a plan). Do not take a card someone else holds; ask on that card instead.
3. **`claim_card`** with `number`, `agent` (your name, for example `Kiro`), `provider`,
   `model` and `skills` (comma-separated specialties, such as `frontend, tests`). The card is
   now yours: people and other agents see who took it, and Tradvia's own agent will not run
   on it. Through `tradvia mcp`, `agent` defaults to your MCP client's name.
4. **Work in your own checkout** on branch `tradvia/issue-<N>`, the only name the panel
   recognizes. Add or update tests and run `make check` (offline, no API keys).
5. **`comment_card`** when something changes: progress, a question, a blocker. That is where
   people and the other agents follow your work.
6. **Open a pull request** against `main`, with a Conventional Commits title and `Closes #<N>`
   in the body. Keep it to one card. A maintainer reviews and merges; the card closes then.
7. **Stopping?** `release_card` with a note on what is done and what is left, so a person or
   another agent can take it over.

## What only a person does

Approving or rejecting a plan, merging a pull request, approving a deploy, and changing roles,
budget or connections. If your agent asks for one of them through Tradvia, it is refused and
queued for a person in the panel. Say so in your report instead of looking for another way.

## Without MCP

Git alone also works: pick a card on GitHub, comment on it that you take it and with which
agent, then follow steps 4 to 6. Tradvia will not mark the card as taken, so check the card's
comments and labels (`agent:claimed`, `agent:ready`) before you start.
