# ADR 0012: Subscription mode for the orchestrator chat — owner only, paced, never stored

- Status: Accepted
- Date: 2026-10-26
- Issue: #109

## Context

The orchestrator chat (#57) can answer in two ways: an AI provider called with an API key
(#107, ADR 0009), or Claude Code running on a Claude subscription token
(`CLAUDE_CODE_OAUTH_TOKEN`, #104). The owner wants both kept: the subscription is how many
people already pay for Claude, and dropping it would close that way of using Tradvia.

Anthropic's terms for Claude Code
([Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)) shape what a
product may do with a subscription:

- plan limits assume ordinary, individual use;
- a product may not use one person's plan credentials to serve other users;
- a product may not collect, store or intermediate those credentials;
- Anthropic may enforce this without notice.

Before this decision, the Connections screen stored the subscription token (encrypted) and
wrote it to GitHub Actions; the chat fell back to the subscription whenever a project chose
nothing; and PR #106 put one token on the server for every project's chat.

## Decision

Keep the subscription mode, behind four rules. An API key stays the recommended way, and the
product says so wherever the mode is chosen or felt.

1. **An explicit choice.** The `orchestrator` role takes `claude-code` (model `default`, the
   CLI's own). A project that chose nothing gets a refusal that explains both ways; the chat
   never falls back to a subscription in silence.
2. **The owner's alone.** The server declares whose token it is in
   `CLAUDE_CODE_OAUTH_TOKEN_OWNER` (a GitHub login). In subscription mode the chat answers that
   person only (403 for anyone else, with the way out: an API key), and the mode stays off
   while no owner is declared.
3. **Paced on purpose.** At least 60 s between answers and at most 20 in any hour, counted
   across every project the server hosts, since it is one subscription. A project may be
   slower in `.tradvia.yml`:

   ```yaml
   subscription:
     min_interval_seconds: 120   # 60 or more; less becomes 60
     max_per_hour: 10            # 20 or fewer; more becomes 20
   ```

   Values outside the range are brought back to the ceiling, and anything invalid keeps it, so
   a typo can only make the pace slower. Each answer reserves its start in the audit log
   (`subscription_answer`, dated when it may start), so messages from two projects queue
   instead of racing. The wait happens before the answer, with the conversation marked as
   working. The ceiling is ours and conservative: Anthropic publishes no number.
4. **Never stored.** Tradvia refuses to save the subscription token. Its owner runs
   `claude setup-token` and puts the result in their own secrets: the server's, for the chat;
   the repository's Actions secrets, for the agent workflows (the pattern Anthropic documents
   for its own GitHub Action). The Connections screen shows only whether the server has a
   token and whose it is. A token stored before this ADR is deleted the next time the screen
   is read (audited as `secret_remove` by `tradvia`), is never handed to a runner, and removing
   it from Tradvia never deletes the owner's own GitHub secret.

**Said, every time.** The dashboard shows the chat's mode above the conversation: who owns the
subscription, the pace, and the answers in the last hour. Each deliberate wait posts a message
naming the rule that held it and pointing to an API key. Choosing the mode in Connections
shows the same explanation beside the choice.

## Consequences

- A deployment that relied on the silent fallback must choose a mode once in Connections. The
  refusal message says how.
- `CLAUDE_CODE_OAUTH_TOKEN_OWNER` joins the deploy next to the token (PR #106).
- The pace covers the server's chat only. The agent workflows (`agent.yml`) run in the owner's
  own GitHub Actions with the token from the repository's secrets and share no counter with
  the server; pacing them is separate work.
- This reads the published terms; it is not legal advice. A hosted offering that runs Claude
  Code for customers also needs Anthropic's Commercial Terms, and the hosted case is worth
  confirming with Anthropic.
