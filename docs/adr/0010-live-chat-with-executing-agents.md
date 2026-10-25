# ADR 0010: Live chat with executing agents

- Status: Proposed
- Date: 2026-10-24

## Context

ADR 0007 moved every human gate — creating/starting cards, approving plans,
merging pull requests, approving deploys — into Tradvia, and gave the project
a conversation with an **orchestrator** agent (#57, shipped). That
conversation is real, but narrow by design: a closed tool set that only
reads state and proposes changes (`read_backlog`, `create_card`,
`reorder_backlog`, `reply_plan` with `adjust` only, `review_pr`...). It
cannot approve a plan, merge, deploy, or touch the agent that is actually
implementing a card.

The agent that implements a card (`ClaudeCodeExecutor` / `run_claude`,
`claude_code.py`) runs **headless and once**: `claude -p`, inside the
`agent.yml` job, with no input channel after it starts. The only human
checkpoint is the plan comment before implementation begins (Aprovado /
Rejeitado / Ajuste). Once it is coding, nobody can say anything to it until
it finishes, fails, or times out — that gap is card #99.

Separately, the owner asked for something larger on 2026-10-24, in the
session that diagnosed #96, #97 and #98: to be able to have the kind of
conversation that session itself was having — multi-step, able to read and
change anything in the repository, run `make check`, call the GitHub API,
merge, deploy — **inside Tradvia**, instead of in an external coding-agent
session. That is a materially different capability from "talk to the agent
that is running a card," and this ADR treats it as a separate decision (see
*Deferred: an operator session*, below), not as part of #99's scope.

## Decision

### 1. Card executions get a live input channel (#99)

The per-project conversation already in Tradvia (#57) is extended so a
message can be addressed to a **specific card's current run**, not only to
the orchestrator. Scope: one card, one running execution (plan or
implementation stage), same project.

**Mechanism (phase 1, ships first): async steering, not a persistent
process.** `ClaudeCodeExecutor` already loops in discrete steps (plan →
wait for a human reply → implement); this reuses that shape instead of
replacing it:

- Before each tool call (or every `TRADVIA_STEER_EVERY_TURNS` model turns,
  default 3), the executor checks the store for new messages addressed to
  this card's run since the last check — one query, same place `pending.py`
  already reads comments from.
- A new message is appended to the model's next turn as untrusted input,
  clearly labeled `human steering message` (never as a tool result, never
  mixed with the card/PR content the model is already told to treat as
  data) so it cannot be confused with content injected through a card,
  comment or PR body.
- The reply (if any) is stored back on the card's run, same as today's plan
  comment, and shown in the dashboard thread.
- Budget, turn and time limits (`budget.py`) are charged exactly as today;
  a steering message does not reset or extend them.

**Mechanism (phase 2, later, only if phase 1's UX is not enough): a
persistent agent process per run**, with the panel streaming messages to it
directly instead of polling the store, and the ability to interrupt a run
mid-turn rather than only between turns. This needs its own follow-up
ADR if proposed, because it changes how `agent.yml` and the executor are
hosted (a long-lived process inside a 75-minute job is a different
operational shape than a sequence of `claude -p` calls), and because the
`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` used by that process would
need to stay reachable for the run's whole lifetime rather than one
subprocess call at a time.

**What does not change:** approving a plan, merging, deploying, and
changing `.tradvia.yml` role requirements stay human-only panel actions
(ADR 0004, reaffirmed by ADR 0007 point 2). A steering message can ask the
agent to change course, but cannot itself approve, merge or deploy —
exactly like `reply_plan`'s `adjust`-only restriction today. This is not
relaxed by this ADR.

### 2. The orchestrator's tool set is not widened by this ADR

#57's orchestrator keeps the tools it has today. "Live chat with the agent
running a card" (§1) and "the orchestrator can do more" are independent
axes; this ADR only changes the first.

## Deferred: an operator session

The owner's second ask — a Tradvia-hosted conversation with the reach of an
external coding-agent session (shell, arbitrary file edits, `git`, the
GitHub API, merges, deploys) — is **not decided here**. It is a different
kind of capability from §1, not a bigger version of it, and bundling it into
#99 would understate what it actually changes:

- **Blast radius of prompt injection.** Today's orchestrator is deliberately
  read-mostly with a closed tool list precisely because card bodies, PR
  descriptions and comments are untrusted text an attacker (or a confused
  contributor) can put in front of the model. An operator session with shell
  and `git push` reachable from a chat box turns a successful injection into
  arbitrary code execution and repository write access, not a wrong
  `read_card` call.
- **Credential lifetime.** This session's tokens are long-lived and
  broad (enough to merge PRs and manage Actions runs, as this very session
  did for #90–#94 and the cards above). An operator session inside Tradvia
  would need the same reach, held by a process a chat message can direct —
  a materially larger thing to audit and contain than the per-action,
  per-role tokens panel actions use today (ADR 0004, ADR 0008).
- **Positioning.** `core/` is fair-source; an embedded, general-purpose
  coding-agent runtime (not "an adapter that executes one card," but a
  standing session with repository-wide reach) may belong in `ee/` or as its
  own explicitly-scoped product decision, not quietly grown out of the
  orchestrator's chat box. (ADR 0001.)

Opening this needs its own ADR, written once the owner decides how much of
that reach Tradvia should actually hold on a standing basis, versus keeping
this kind of broad work in an external session by design. Filing it now
would be deciding the security boundary in the middle of a card description,
which is the opposite of what ADR 0004's fail-closed, role-checked actions
are for.

## Consequences

- #99 ships as phase 1 (async steering) first; phase 2 (persistent process)
  is explicitly a follow-up, not bundled in.
- The operator-session idea is written down (so it is not lost) and
  explicitly not started until a separate ADR decides its shape and where it
  lives in the monorepo.
- No change to `.tradvia.yml` interactions, roles, or what the orchestrator
  can already do.

## Delivery

| Order | Card | Capability | Status |
|---|---|---|---|
| 1 | #99 | Live steering messages reach a card's current run (phase 1: async) | Planned |
| 2 | — | Persistent per-run agent process (phase 2, follow-up ADR) | Planned |
| 3 | — | Operator session with repository-wide reach (separate ADR, not scoped yet) | Planned |
