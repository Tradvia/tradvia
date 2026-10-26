# ADR 0012 — Command and data guardrails

Status: Accepted · Capability status: **Partial** (regex patterns only)

## Context

`risk.py` decides who approves an action. Nothing decided what an agent may generate or
run before that: a plan with `rm -rf /` or a file with a token reached the approval gate
and the pull request.

## Decision

`core/src/tradvia/guardrails.py` holds a table of compiled regular expressions in three
kinds: `destructive` commands, `credential`s and `pii` (CPF and CNPJ with check digits,
personal e-mail). The graph reviews twice:

1. in `risk_gate`, before anything runs: title, plan, action and `file_content`;
2. in `open_pr`, before the branch exists: files, pull request title and body.

A hit creates no branch or pull request, posts `⛔ ... bloqueado por guardrail: <rule>` in
the chat, stores `guardrail` (`phase`, `state`, `findings`, `suppressed`) on the card and
moves it to `WAITING_HUMAN` with the reason in the history. Excerpts are masked.

False positives: `tradvia: allow <rule>` on the line, or a known documentation value.
Allowed hits stay recorded in `guardrail.suppressed` so the rule can be tuned.

## Scope

Only code, cards and pull requests. Scanning WhatsApp and e-mail messages stays in
`ee/privacy`, still Planned.

## Consequences

- The browser driver with credentials (#119) may only be enabled after this guardrail is
  active.
- Regex cannot see obfuscated commands or secrets without a known format; this is not a
  sandbox. Stage 3 of the roadmap (robustness) is In progress.
