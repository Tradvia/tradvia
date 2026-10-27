# Tradvia MCP for agents

Status: **In progress**. Tested offline with fake HTTP and fake clients. Not yet run with a
real client. See [ADR 0012](adr/0012-agent-communication-git-and-mcp.md).

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

Not available to agents:

- **Approve or reject a plan, merge, approve or reject a deploy.** The call is refused and a
  pending item is queued for the person in the panel.
- **`review_pr`.** It runs `make check` on the server, on the code of a pull request.

## Get a token

A token belongs to one project and one role, and it expires (30 days by default, 90 at most).
It is shown once.

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
      "includeTools": ["read_cards", "read_card", "read_plan", "create_card", "start_card", "reply_plan"]
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
