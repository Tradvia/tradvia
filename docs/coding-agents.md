# Coding agents

The planner and the executor of a card are adapters behind one interface,
`CodingAgent` (`core/src/tradvia/agents.py`, [ADR 0010](adr/0010-coding-agent-adapters.md)).
The provider of the role chosen on the Connections screen
([ADR 0019](adr/0019-project-connections.md)) or in `TRADVIA_PLANNER` / `TRADVIA_EXECUTOR`
selects the adapter. With no provider, behavior is unchanged.

## Provider × role

| Provider | Planner | Executor | Status |
|---|---|---|---|
| Claude Code (`claude-code`) | yes | yes | Shipped |
| Gemini CLI (`gemini-cli`) | yes | yes | In progress (offline tests with a fake runner; not yet run against the real CLI in CI) |
| OpenAI Codex CLI (`codex-cli`) | yes | yes | In progress (same) |
| Kiro, Antigravity and other IDEs | no | no | Planned as executors: the non-interactive tool names are not documented yet. They can work on a card today through the Tradvia MCP ([mcp.md](mcp.md)) |

The reviewer and orchestrator roles do not use these adapters yet.

## Restricted tools and limits

Tradvia creates the branch, makes the commit and opens the pull request. No adapter runs git,
except `git mv` and `git rm` in the Claude Code executor, so it can rename and delete files:
the pull request then removes the old paths too (#185). Protected paths (`.github/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS`, `.tradvia.yml`) stay blocked for any change, deletion
included.

- **Claude Code**: `--allowedTools` (`Read,Glob,Grep` to plan; edit tools plus
  `make check`/`make test` and `git mv`/`git rm` to execute) and `--max-turns`.
- **Gemini CLI** (`gemini -p --output-format json`): `--allowed-tools` with read-only tools
  to plan, and file edit plus `make check`/`make test` to execute; no web fetch or search.
  The CLI has no turn flag, so exit code 53 or more tool calls than the limit is reported as
  the turn limit.
- **Codex CLI** (`codex exec --json`): sandbox `read-only` to plan, `workspace-write` with
  network access disabled to execute. More command/file items than the limit is reported as
  the turn limit.

Turn limits come from `TRADVIA_PLAN_MAX_TURNS` and `TRADVIA_EXEC_MAX_TURNS`; the time limit
from `TRADVIA_CLAUDE_TIMEOUT`. Errors (CLI missing, timeout, invalid JSON, non-zero exit,
turn limit) are `RuntimeError`s with secrets redacted, and the card returns to
`WAITING_HUMAN`.

For every adapter, after the run the flow blocks changes to `.github/`, `.git/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS` and `.tradvia.yml`, and the card returns to
`WAITING_HUMAN` without a pull request.

## Cost normalization

Each run reports duration, tokens and US$ for the panel. What the provider does not report
is flagged (`usd_reported`, `tokens_reported`) and shown as "não informado", never invented.

| Provider | US$ | Tokens |
|---|---|---|
| Claude Code | reported | reported |
| Gemini CLI | not reported | reported when the JSON stats have them |
| Codex CLI | not reported | reported by the `turn.completed` event |

The model chosen on the Connections screen for the CLI providers is recorded only; each CLI
uses its own model setting.
