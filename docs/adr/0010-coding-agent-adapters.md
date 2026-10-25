# ADR 0010: Coding agent adapters

- Status: Accepted
- Date: 2026-10-25

## Context

The planner/executor of a card was Claude Code only. ADR 0007 makes the Connections
screen the place to choose it, and ADR 0009 stores provider and secret per role.

## Decision

- **Interface**: `CodingAgent` has `plan(title, body, number, cwd, notice)` returning the
  plan JSON, and `execute(card, cwd, budget_notice)` returning the changed files, summary,
  cost, tokens, duration and tool calls. `ClaudeCodeAgent` wraps the existing functions
  without changing them; `GeminiCliAgent` and `CodexCliAgent` share `CliAgent`.
- **Headless only**: an adapter exists only for a CLI with a documented non-interactive
  mode and JSON output. Tools restricted (no git, no network beyond the provider), turn and
  time limits. IDE tools (Kiro, Antigravity) stay Planned until a stable CLI/API exists.
- **Protected paths** are enforced by the flow after the run, from `git status`, so they
  hold for any adapter.
- **Cost**: what a provider does not report is flagged and shown as "não informado".

## Consequences

Adding a provider is one class and one catalog entry. Turn limits of CLIs without a turn
flag are checked after the run, not during it.
