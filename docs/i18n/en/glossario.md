<!-- tradvia-translation source="docs/glossario.md" source-sha256="344e7949d999ed6774578a7d3c17adf0a6a11d4f7f48805e87f9bf6f46eff6f1" -->
# Glossary

Tradvia's main terms. The status of each capability follows the
[README](../README.md) and [ADR 0003](adr/0003-agentops-hybrid-execution-control-plane.md):
**Shipped**, **In progress**, **Partial** or **Planned**.

| Term | Definition | Status |
|---|---|---|
| **Card** | Unit of work of the executable hybrid Kanban; a durable state machine (`BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR`, `DONE`) that records tokens, compute seconds and time. | In progress — state machine and approval in chat |
| **Work item** | Item of the vendor-neutral work model, of which Kanban, Gantt, Burndown, WBS, calendar and capacity are projections; a card's lifecycle is a work item's lifecycle (ADR 0002). | Shipped (library in `core/src/tradvia/work/`) |
| **Human gate** | Approval point where the card pauses in `WAITING_HUMAN` until a person answers (for example, approving the plan before the agent opens a pull request). | Partial — approval in chat (Discord) and in an issue comment; other channels planned |
| **Live takeover** | When an agent hits a blocker (2FA, CAPTCHA, complex UI, an architecture decision), the session is frozen, a person is notified and takes over the terminal or browser through streaming (WebRTC/VNC), handing control back afterwards. | Planned (Phase 2) |
| **Session worker** | Container that runs a stateful CLI or browser session (Playwright/CDP) with a persistent authentication context and a session mutex, operating only accounts and portals the customer is authorized to use. | Planned (Phase 2) |
| **Token FinOps** | Execution cost control: recording the exact cost per token or per compute second on each card, plus account pools, fallback between vendors and budget circuit breakers, always within each vendor's limits. | Partial — cost recorded per card; pools, fallback and circuit breakers planned; governance in `ee/` planned |
