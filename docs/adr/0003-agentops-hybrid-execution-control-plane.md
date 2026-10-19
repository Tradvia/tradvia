# ADR 0003: Open-core AgentOps and hybrid execution control plane

- Status: Accepted
- Date: 2026-10-18
- Amends: [ADR 0002](0002-positioning-multi-view-orchestration.md) (public positioning only)

## Context

ADR 0002 framed Tradvia around progress notations. That undersold the
engineering core: how work is actually executed, by which runtime, at what
cost, and how a human steps in when an agent is stuck.

## Decision

Tradvia is positioned as **the open-core AgentOps and hybrid execution control
plane** that unifies human project management and autonomous AI execution.
Six pillars:

| Pillar | Status on 2026-10-18 |
|---|---|
| **Dual-runtime execution** — stateless LLM APIs (OpenAI, Anthropic, Gemini, Ollama) plus stateful CLI/browser session workers (Playwright/CDP) with session mutex locks | Partial: APIs through LiteLLM |
| **Multi-account pool & Token FinOps** — load spread within each account's RPM/TPM quota, cross-vendor fallback, cost per token and per compute second, budget circuit breakers | Partial: cost recorded per card |
| **Executable hybrid Kanban & Live Session Takeover** — every card is a durable state machine; a human can take over a frozen browser/terminal session (WebRTC/VNC) and hand control back | In progress: state machine + approval in chat |
| **Zero-trust sandbox** — ephemeral Firecracker MicroVMs, PostgreSQL RLS, encrypted credential/session vaults | Planned |
| **Native MCP & AST GraphRAG** — episodic knowledge distillation to cut token cost | Planned |
| **Open-core monorepo** — self-hosted `/core` (Docker Compose), `/ee` + Cloud with team session pools, RBAC, FinOps governance | In progress: monorepo, licenses, CI |

Rules for public material:

1. Every capability keeps an explicit Shipped / In progress / Partial / Planned status.
2. The account pool **respects each vendor's rate limits and terms**. We describe
   it as spreading load within quotas and falling back across vendors, never as
   bypassing limits.
3. Session workers only operate accounts and portals the customer is authorized
   to use. A human resolves 2FA and CAPTCHA prompts through Live Session
   Takeover; agents never attempt to solve them.

## Consequences

- The website drops the six-view preview in favor of the executable board
  with dual runtime, account pool and live takeover. The work model and its
  projections from ADR 0002 stay in `core/src/tradvia/work/` as a library.
- READMEs and the organization profile follow this positioning.
