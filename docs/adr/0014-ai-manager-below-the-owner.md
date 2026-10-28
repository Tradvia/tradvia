# ADR 0014: an AI manager below the project's owner

- Status: Accepted
- Date: 2026-10-28

## Context

People bring their own AI to Tradvia through MCP (ADR 0012) and work cards together with other
people and agents. The owner also wants an AI that **manages**: it follows the work, keeps the
backlog organized and moves plans along. The owner's rule: an AI can manage only *below* the
owner, and it manages other agents and members only with the owner's permission.

ADR 0004 keeps approving plans, merging and deploying with a person, and ADR 0012 gives agents a
closed tool set where those decisions become pending items for a person.

## Decision

1. **An AI manager is an API token of kind `manager`** (`api_tokens.kind`). Only an admin of the
   project makes one, and it always has the role `write`. Like every token it belongs to one
   project, expires (at most 90 days), is revocable, never exceeds its owner's current role and
   acts with the owner's GitHub identity.
2. **Base, with no grant: follow and organize.** Everything an agent token does (ADR 0012):
   read everything (`read_briefing` included), comment, create, start and take cards, adjust
   plans. Its catalog says it is a manager and what it may not do.
3. **Grants: what the owner lets it do beyond the base, one permission at a time.** Grantable
   today: `approve_plan` (approve or reject a plan, with the same comment as the panel) and
   `reorder_backlog`. Each grant is a row in `manager_grants` with its own expiry (at most 90
   days, never past the token), revocable at once, and checked on every call. Only an admin
   grants, only on their own manager token, only with a signed-in session (a token never grants
   anything, to itself or another).
4. **Never grantable:** merge, deploy and the post-deploy operation, budget, connections, roles and
   invitations. Asking for them stays a pending item for a person (ADR 0004), grants or not.
5. **Audit:** making the token, each grant and each revocation are audit rows with the admin as
   actor; each tool call the manager makes is audited as an agent call (`via: api_token`).

## Consequences

- An owner can hand routine coordination to an AI and widen it for a limited time, without
  giving it merge, deploy or money decisions.
- A leaked manager token does what its base and current grants allow until it expires or is
  revoked; revoking a grant or the token takes effect on the next call.
- The chat orchestrator is not a manager: grants count only through an API token.

## Status

| Capability | Status |
|---|---|
| Manager token, base scope, grants for `approve_plan` and `reorder_backlog`, revocation, audit | In progress: offline tests |
| Grant and revoke on the "Connect my AI" screen | In progress: checked with a mocked API |
| Cost cap per AI manager inside the project's monthly cap | Planned: needs the monthly cap (#131, PR #142) |
| Managing members (roles, invitations) | Not planned: stays with people on GitHub |
