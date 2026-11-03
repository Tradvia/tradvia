# ADR 0012: Agents talk to the orchestrator through git and MCP

- Status: Proposed
- Date: 2026-10-27

## Context

A card is a GitHub issue. Its plan is written by a planner, approved by a person and
delivered as a pull request. Until now only Tradvia's own adapters (Claude Code, Gemini CLI,
Codex CLI; ADR 0010) did the work inside the card flow. Other agents work outside it: IDE
agents (Claude Code, Kiro, Antigravity, Gemini CLI on a developer machine) and cloud agents
(Manus). They have to:

- read a card and its plan, and answer the orchestrator's question with an adjustment;
- start or create cards;
- deliver code: a branch and a pull request that closes the card.

The orchestrator already has a closed set of tools (`orchestrator.py`). Approving a plan,
merging and deploying have no tool at all: asking for them queues a pending item for a person
(ADR 0004). API tokens (ADR 0009) give one project and one role, with an audit entry tied to
the owner. What is missing is a way for those agents to use the same tools.

## Decision

1. **Two channels, one gate.**
   - *Git* carries the code. An agent works in its own checkout, pushes a branch
     `tradvia/issue-<N>` and opens a pull request that says `Closes #N`. The panel and the
     core recognize only this prefix (`BRANCH_PREFIX`), so another name would leave the pull
     request out of the card's review state. CI runs `make check`,
     and a maintainer merges. Tradvia does not run code from an agent pull request.
   - *MCP* carries the conversation with the orchestrator: read, start, create, answer.
2. **MCP is an adapter, not a second implementation.** The agent's tools are
   `orchestrator.AGENT_TOOLS`, called through the same `call_tool` as the orchestrator, with
   the same guards and the same audit. Two transports share one protocol module
   (`mcp_server.py`):
   - stdio: `tradvia mcp`, for clients on a developer machine;
   - HTTP: `POST /api/projects/{owner}/{repo}/mcp`, for clients that only reach a URL
     (cloud agents such as Manus). It takes the same bearer token.
   The agent routes are `GET .../agent/tools` and `POST .../agent/call`; the MCP endpoint
   sits beside them.
3. **Identity and scope are the API token's (ADR 0009).** An agent acts with a token issued
   for one project and one role, and the token never exceeds its owner's current role. A
   token of role `read` cannot use the agent tools: reads need the role of starting a card
   (`triage`). Adjusting a plan needs `write`, as in the panel.
4. **The allow-list is closed.**
   - Collaboration (added with the first users): `read_briefing`, `claim_card`,
     `release_card`, `comment_card`. A claim names the person and the agent (provider, model,
     specialties), is exclusive until released, and uses the label `agent:claimed`, which no
     workflow listens to, so Tradvia's own agent never runs on a claimed card.
   - Available to agents: `read_backlog`, `read_cards`, `read_card`, `read_plan`,
     `read_pulls`, `read_pull`, `read_checks`, `read_deploys`, `create_card`, `start_card`,
     and `reply_plan` with the decision `adjust` only.
   - Not available to agents: `review_pr`. It runs `make check` on the server against the code
     of a pull request, so a token must never start it. It keeps its own decision for later.
   - Human only, queued as a pending item for the person: approve or reject a plan, merge,
     approve or reject a deploy, and the post-deploy operation.
5. **Audit.** Each agent request writes one `agent_call` row (the request, its idempotency
   key and its result). Each tool use writes one `orchestrator:<tool>` row with the token's
   owner as actor and `via = api_token`, so refusals show up too. Two rows per call is a
   deliberate trade-off: the request and the tool are different facts.
6. **Idempotency** follows the panel: `X-Tradvia-Idempotency-Key`, with the same five-second
   window. The stdio server sends a fresh key per call.
7. **The orchestrator chat stays disabled.** MCP calls run tools directly. They never start
   the language-model conversation, so enabling MCP does not enable the chat.

## Consequences

- An agent can create, start and adjust cards in one project. It cannot approve a plan, merge
  or deploy. A leaked token does what its role allows in that project until it expires or is
  revoked (ADR 0009).
- Adding a tool to agents is a line in `AGENT_TOOLS` plus tests. A tool that runs code on the
  server stays out of that list.
- Stdio clients need the `tradvia` command on their machine. HTTP clients need the public
  panel address and no installation.
- Limits: JSON responses only (no event stream), no MCP resources or prompts, and no edit
  tools for agents yet.
- Agents still act with a person's GitHub identity (the token's owner). Dedicated agent
  identities are Planned.

## Alternatives considered

- **GitHub's own MCP server only.** Rejected: it would bypass Tradvia's gate, role scope and
  audit.
- **Let agents talk to the orchestrator's chat.** Rejected: the chat stays disabled, and it
  would start the language-model run that the tool calls avoid.
- **The official MCP SDK.** Deferred. Its 2.x line renamed the server API, and the stdio part
  of the protocol is small, so the core keeps no new dependency. Revisit when the HTTP
  endpoint needs an event stream.

## Note: asking a person (`request_human`, #120)

An agent that needs a person (a login with 2FA, a CAPTCHA, a decision only a human can make)
calls `request_human` with what is needed and, optionally, a `live_url`. Tradvia posts it on the
card with a hidden marker (`<!-- tradvia:request_human -->`) that only the tool places: agent
text is stripped of it, repeatedly, so it cannot be forged. While that comment is the card's last
word, the card is pending as `human_request`, for whoever holds `live_takeover` (maintain by
default), in the inbox, Web Push and, when a channel is set, Discord. The person answers with
`POST /api/projects/{owner}/{repo}/cards/{n}/human-done` (session, CSRF, audited), which posts
"Done — @login finished what the agent asked."; any later comment closes the pending item, and the
agent goes on when it reads the card. No table and no change to the card graph.

## Status

| Capability | Status |
|---|---|
| Agent tools through the token API (read, create, start, adjust a plan) | In progress: offline tests only |
| Collaboration tools (briefing, claim with agent identity, release, progress) and `AGENTS.md` | In progress: offline tests; the panel does not show claims yet |
| stdio server (`tradvia mcp`) | In progress: offline tests; Claude Code CLI connected against a local API; not yet against production |
| HTTP endpoint (`/mcp`) for URL clients | In progress: offline tests and `curl` against a local API; not yet run with Manus |
| `request_human`: an agent asks a person to act, and the panel's "done" (`human_done`) answers it | In progress: offline tests; the panel button is #189 |
| `review_pr` for agents | Planned: needs its own decision |
| Edit card and reorder backlog for agents | Planned |
| Agent pull requests gated beyond CI (protected paths, `Closes #N`) | Planned |
| Dedicated agent identities | Planned |

Configuration and per-client steps are in [docs/mcp.md](../mcp.md).
