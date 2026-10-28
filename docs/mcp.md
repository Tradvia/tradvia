# Tradvia MCP for agents

Status: **In progress**. Tested offline with fake HTTP and fake clients, and run once against
a local Tradvia API (memory store, fake GitHub): Claude Code CLI 2.1.312 connects to
`tradvia mcp` (`claude mcp list`: Connected), and a scripted stdio client and `curl` on the HTTP
endpoint list the tools, create and read a card, and get a merge refused and queued for a
person. Not yet run against production, nor with Kiro, Gemini CLI, Antigravity or Manus. See [ADR 0012](adr/0012-agent-communication-git-and-mcp.md).

Agents read cards, start or create them, and answer a plan with an adjustment, through the
same tools the orchestrator uses. Code goes through git as usual: a branch and a pull request
that closes the card.

## Tools

| Tool | What it does | Notes |
|---|---|---|
| `read_backlog` | The backlog in order | |
| `read_cards` | Cards of the project | `state`: open, closed or all |
| `read_card` | One card with its description | `number` |
| `read_plan` | The plan of a card waiting for a reply | `number` |
| `read_pulls`, `read_pull`, `read_checks` | Pull requests and their checks | |
| `read_deploys` | Deploys waiting for approval | |
| `create_card` | Create a card at the end of the backlog | `title`, `description` |
| `start_card` | Start a card (adds `agent:ready`) | `number` |
| `reply_plan` | Answer a plan with an adjustment | `decision` must be `adjust`; needs role `write` |
| `read_briefing` | Start here: how the project works, `AGENTS.md`, free cards, who holds which card with which agent, what waits for a person | |
| `claim_card` | Take a free card for you and your agent; Tradvia's own agent will not run it | `number`, `agent`, `provider`, `model`, `skills`; `agent` defaults to the MCP client's name |
| `release_card` | Give back a card you hold | `number`, `note` |
| `comment_card` | Progress, a question or a blocker, on the card | `number`, `text` |

Working with others: an agent works **with** people and other agents, not alone. The loop is in
[AGENTS.md](../AGENTS.md): `read_briefing`, `claim_card`, code on `tradvia/issue-<N>`,
`comment_card`, a pull request with `Closes #<N>`, and `release_card` if it stops. A claim is
the label `agent:claimed` plus a comment on the card naming the person, the agent, its provider,
model and specialties. Only a comment written by that person counts as their claim, so the text
cannot be forged by someone else, and a card held by one person cannot be taken or started by
another until it is released.

Not available to agents:

- **Approve or reject a plan, merge, approve or reject a deploy.** The call is refused and a
  pending item is queued for the person in the panel.
- **`review_pr`.** It runs `make check` on the server, on the code of a pull request.

## Get a token

A token belongs to one project and one role, and it expires (30 days by default, 90 at most).
It is shown once.

**In the panel:** sign in, open the project, and use **Connect my AI** ("Conectar minha IA") at
the bottom of the Dashboard. Choose the role and how many days, create the token, and copy it:
it is shown once. The same screen gives the MCP setup to paste into your client, with the
project and address filled in, and lists your tokens in this project with a Revoke button
(never their values). `GET /api/projects/{owner}/{repo}/tokens` returns that list, with a
signed-in session only.

From a terminal, with the session cookie instead:

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # value of the __Host-tradvia_session cookie of your signed-in browser
tradvia token create --project owner/repo --role triage --days 30
```

Roles: `triage` (default) reads, creates and starts cards; `write` also adjusts plans.
A `read` token cannot use the agent tools, because reads need the same role as starting a
card. A token never has more power than its owner has in the project.

Keep the token out of git. Put it in `TRADVIA_TOKEN` or in your client's secret store.
Revoke it with `tradvia token revoke --project owner/repo ID`.

## An AI manager (admins only)

An admin can make a token of kind **AI manager** ("IA gestora") on the Connect my AI screen
([ADR 0014](adr/0014-ai-manager-below-the-owner.md)). It works below the owner:

- **Base:** everything an agent does here: read everything, comment, create, start and take
  cards, adjust plans. Its `read_briefing` says it is a manager.
- **Only with your grant, for a set time:** approve or reject plans (`reply_plan` with `approve`
  or `reject`) and reorder the backlog (`reorder_backlog`). Grant and revoke each one on the same
  screen; a grant lasts at most 90 days and never past the token, and revoking takes effect on
  the next call.
- **Never:** merge, deploy, budget, connections, roles or invitations. Those stay pending items
  for a person.

From a terminal: `POST /api/projects/{owner}/{repo}/tokens` with `{"kind": "manager"}`, then
`POST .../tokens/{id}/grants` with `{"permission": "approve_plan", "expires_in_days": 7}` and
`DELETE .../tokens/{id}/grants/{grant_id}`, all with a signed-in admin session.

## Option A: stdio (a client on your machine)

Install the `tradvia` command from a checkout of this repository:

```bash
pip install ./core
```

The server reads three variables: `TRADVIA_URL`, `TRADVIA_PROJECT` (`owner/repo`) and
`TRADVIA_TOKEN`. It runs with `tradvia mcp`. Its stdout is the protocol channel, so it
prints nothing else there.

### Claude Code

Project file `.mcp.json` at the root of the repository. Claude Code expands `${VAR}` and asks
you to approve a project server before its first use (verified in the Claude Code docs):

```json
{
  "mcpServers": {
    "tradvia": {
      "command": "tradvia",
      "args": ["mcp"],
      "env": {
        "TRADVIA_URL": "https://app.tradvia.org",
        "TRADVIA_PROJECT": "owner/repo",
        "TRADVIA_TOKEN": "${TRADVIA_TOKEN}"
      }
    }
  }
}
```

### Kiro CLI

File `.kiro/settings/mcp.json` in the repository, or `~/.kiro/settings/mcp.json` for every
project (verified in the Kiro docs). Use the same `mcpServers` block as above. Two steps:

- Approve `TRADVIA_TOKEN` under **Mcp Approved Env Vars**. Kiro expands a variable only after
  that approval.
- List in `autoApprove` the tools you accept without a prompt. The docs do not say what a
  non-interactive run does with a tool that is not approved; test that before you rely on
  Kiro headless.

### Gemini CLI

File `~/.gemini/settings.json`, or `.gemini/settings.json` for one project (verified in the
Gemini CLI docs). `includeTools` lists the tools the agent may use. Leave `trust` unset:
`trust: true` skips every confirmation for the server.

```json
{
  "mcpServers": {
    "tradvia": {
      "command": "tradvia",
      "args": ["mcp"],
      "env": {
        "TRADVIA_URL": "https://app.tradvia.org",
        "TRADVIA_PROJECT": "owner/repo",
        "TRADVIA_TOKEN": "$TRADVIA_TOKEN"
      },
      "includeTools": ["read_briefing", "read_cards", "read_card", "read_plan", "claim_card", "comment_card", "release_card", "create_card", "reply_plan"]
    }
  }
}
```

### Antigravity

Not verified. A Google Cloud article describes `~/.gemini/config/mcp_config.json`, with
`serverUrl` and `headers` for remote servers. The same article reports that `env` values did
not reach the process in Antigravity. Prefer Option B and test it before you rely on it.

## Option B: HTTP (cloud agents, such as Manus)

The endpoint takes JSON-RPC over `POST`, with the same bearer token:

```
https://app.tradvia.org/api/projects/owner/repo/mcp
Authorization: Bearer <token>
```

Manus accepts a custom MCP server that is a remote HTTPS endpoint with an API key or bearer
token (verified in the Manus docs). It has not been tested with Manus yet.

Check the endpoint by hand:

```bash
curl -s https://app.tradvia.org/api/projects/owner/repo/mcp \
  -H "Authorization: Bearer $TRADVIA_TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## What the agent gets back

- A tool that succeeds returns its result as JSON text.
- A refusal (a human-only action, an unavailable tool, a role that is too low, a card in the
  wrong state) returns `isError: true` with the reason. It is not a protocol error.
- Errors never carry the token. The client replaces it with `[token]`.

Every call is audited with the token's owner as actor and `via: api_token`. Refusals are
audited too.

## Limits

- Responses are JSON. There is no event stream, and no MCP resources or prompts.
- Agents cannot edit a card or reorder the backlog yet.
- Agents act with the token owner's GitHub identity. Dedicated agent identities are planned.
- Retries: send `X-Tradvia-Idempotency-Key` with a write call (HTTP clients). The panel
  ignores an exact repeat of the same key within five seconds. Without a key, a retried
  `create_card` creates a second card.
