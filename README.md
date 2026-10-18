# Tradvia

**Multi-agent orchestration for project execution, with progress everyone can see.**

> 🚧 Pre-alpha. We are building Tradvia in public, with Tradvia.
> **[Join the waitlist for the managed Cloud / Enterprise edition →](#waitlist)**
> · Website: [tradvia.org](https://tradvia.org)

---

## The manifesto: end coordination fatigue

Projects don't slip because people can't do the work. They slip because of
*coordination*: chasing status across tools, waiting on decisions, re-explaining
context in chat, and turning every update into another meeting.

AI agents were supposed to help. Instead they added a new kind of noise:
autonomous scripts that run where nobody watches, then stall, hallucinate or
deliver work no one asked for.

We believe:

1. **Every member is visible, human or agent.** Agents and people work on the
   same work items, and everyone can see who is doing what.
2. **Each audience gets its own notation.** The team follows a Kanban, the
   project manager a Gantt with milestones and dependencies, the Scrum Master
   a burndown, the sponsor a WBS, everyone a calendar, and the lead a capacity
   matrix. They are all views of the same data.
3. **Your tools, not ours.** Use Tradvia's native views, or keep Jira, Trello,
   ClickUp, Monday.com, Asana and others: connectors map them to the same
   work model.
4. **Humans approve; agents execute.** When an agent is unsure, it asks you in
   the chat you already use, and waits.
5. **Cost is a design constraint.** Every work item shows what it cost in
   tokens.
6. **Source you can read and run.** The core is free to self-host and becomes
   Apache 2.0 two years after each release.

See [ADR 0002](docs/adr/0002-positioning-multi-view-orchestration.md) for the
decision behind this positioning.

## Progress views

| Notation | Answers | Status |
|---|---|---|
| **Kanban** | Where is each item in the workflow right now? | 🔨 Library (`tradvia.work.kanban`) |
| **Gantt** | Duration, dependencies, milestones at risk | 🔨 Library (`tradvia.work.gantt`) |
| **Scrum / Burndown** | Will the sprint scope be delivered in time? | 🔨 Library (`tradvia.work.burndown`) |
| **WBS / EAP** | How does the scope decompose into deliverables? | 🔨 Library (`tradvia.work.wbs`) |
| **Calendar / Timeline** | What is due on which date? | 🔨 Library (`tradvia.work.timeline`) |
| **Workload / Capacity** | Who — person or agent — is over- or under-allocated? | 🔨 Library (`tradvia.work.workload`) |

All six are pure functions over one list of `WorkItem`s. The web UI that
renders them is planned for v0.2.

## Connectors

Planned targets, all behind one `TrackerConnector` interface
([`core/src/tradvia/work/connectors.py`](core/src/tradvia/work/connectors.py)):
Trello · Jira · ClickUp · Monday.com · Asana · Wrike · Smartsheet · Artia ·
Microsoft Planner · KanbanFlow · Airtable.

## Architecture

```mermaid
flowchart LR
    H([Human<br/>Tech Lead / Dev]) <-->|approve · answer · follow progress| K

    subgraph CORE["core/ — FSL-1.1-ALv2"]
        K[Work model + 6 views<br/>Kanban · Gantt · Burndown<br/>WBS · Calendar · Capacity]
        O{{Orchestrator<br/>LangGraph state machine}}
        A1[@Agent-Architect<br/>plans and splits]
        A2[@Agent-Coder<br/>writes code in sandbox]
        A3[@Agent-Reviewer<br/>tests and validates]
        R[Model Router<br/>LiteLLM]
        M[MCP Host]
        K <-->|work-item lifecycle| O
        O --> A1 & A2 & A3
        A1 & A2 & A3 --> R
        A1 & A2 & A3 --> M
    end

    R -->|planning| LLM1[(Reasoning models)]
    R -->|bulk coding| LLM2[(Low-cost coding models)]
    A2 --> SB[[Docker / E2B sandbox]]
    M --> GH[GitHub MCP<br/>branches · PRs · Projects]
    M --> CH[Slack / Discord<br/>human-in-the-loop]
    K <--> TR[Jira · Trello · ClickUp · Monday.com<br/>Asana · Wrike · and more]

    subgraph EE["ee/ — Enterprise"]
        SSO[SSO SAML/OIDC]
        WA[WhatsApp Business API]
        TM[Microsoft Teams]
        ROI[CTO ROI dashboard]
    end
    EE -.extends.-> CORE
```

### Work-item lifecycle

| Status | Who acts | What happens |
|---|---|---|
| `BACKLOG` | Human | Item created and prioritized |
| `AGENT_WORKING` | Agents | Architect plans, Coder writes in a sandbox, Reviewer tests |
| `WAITING_HUMAN` | Human | An agent asked a question in Slack/Discord and paused |
| `IN_REVIEW_PR` | Human + Reviewer | Pull request open, checks running |
| `DONE` | — | Delivered; cost and time recorded on the item |

The full contract is in
[`core/contracts/card-state.v1.json`](core/contracts/card-state.v1.json).

## Roadmap

- [ ] **v0.1 — engine:** orchestrator with 3 agents, the neutral work model
      with its six views, LiteLLM routing, GitHub, Slack/Discord approvals
- [ ] **v0.2 — the machine builds the machine:** v0.1 agents build the web
      views (Next.js) and the first connectors (Jira, Trello)
- [ ] **Live board:** public read-only view of our own development
- [ ] **Community connectors:** ClickUp, Monday.com, Asana, Wrike, Smartsheet,
      Artia, Microsoft Planner, KanbanFlow, Airtable
- [ ] **Enterprise:** SSO, WhatsApp Business, Teams, portfolio and ROI dashboards

## Repository layout

```
.
├── core/            # Community edition — FSL-1.1-ALv2
│   ├── contracts/           # Card state contract and schemas
│   └── src/tradvia/
│       └── work/        # Work model, six views, connector interface
├── ee/              # Enterprise Edition — commercial license
│   ├── auth/sso/
│   └── integrations/{whatsapp,teams}/
├── docs/adr/        # Architecture decision records
├── scripts/         # Repository tooling
└── .github/         # CI, templates, rulesets, CODEOWNERS
```

**One rule:** `core/` never depends on `ee/`. CI enforces it.

## Waitlist

Don't want to run Docker, MCP servers and API keys yourself? The managed
version is coming. **Waitlist link: coming soon.**

## Contributing

Connectors are the best first contribution: each one implements the same
`TrackerConnector` interface. Read
[CONTRIBUTING.md](CONTRIBUTING.md); every contributor signs the
[CLA](CLA.md) once, through a bot on the first pull request.

## Security

Please do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

Tradvia is **fair source**, not OSI open source. See
[LICENSE.md](LICENSE.md). "Tradvia" is a trademark of Anderson dos Santos Dutra; see
[TRADEMARKS.md](TRADEMARKS.md).
