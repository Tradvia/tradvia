# Tradvia

**AI agents as real members of your team's Kanban board.**

> 🚧 Pre-alpha. We are building Tradvia in public, with Tradvia.
> **[Join the waitlist for the managed Cloud / Enterprise edition →](#waitlist)**
> · Website: [tradvia.org](https://tradvia.org)

---

## The manifesto: end coordination fatigue

Software teams don't lose their days to writing code. They lose them to
*coordination*: chasing status in Jira, waiting on code review, re-explaining
context on WhatsApp and Teams, and turning every decision into another meeting.

AI coding agents were supposed to fix this. Instead they added a new kind of
noise: autonomous scripts that run in a terminal nobody watches, then stall,
hallucinate or open pull requests no one asked for.

We believe:

1. **Agents belong on the board, not in a terminal.** Every agent task is a
   card. You see who is working on what, human or agent, in one place.
2. **Humans approve; agents execute.** When an agent is unsure, it asks
   you, in the chat you already use, and waits. You answer from your phone;
   the card moves on.
3. **Cost is a design constraint.** Reasoning models plan; cheaper models
   type. Every card shows what it cost.
4. **Your tools, not ours.** Agents act through open MCP connectors to
   GitHub, Slack, Discord, Jira and more.
5. **Source you can read and run.** The core is free to self-host and
   becomes Apache 2.0 two years after each release.

## Architecture

```mermaid
flowchart LR
    H([Human<br/>Tech Lead / Dev]) <-->|approve · answer · move cards| K

    subgraph CORE["core/ — FSL-1.1-ALv2"]
        K[Hybrid Kanban<br/>humans + agents]
        O{{Orchestrator<br/>LangGraph state machine}}
        A1[@Agent-Architect<br/>plans and splits]
        A2[@Agent-Coder<br/>writes code in sandbox]
        A3[@Agent-Reviewer<br/>tests and validates]
        R[Model Router<br/>LiteLLM]
        M[MCP Host]
        K <-->|card state contract| O
        O --> A1 & A2 & A3
        A1 & A2 & A3 --> R
        A1 & A2 & A3 --> M
    end

    R -->|planning| LLM1[(Reasoning models)]
    R -->|bulk coding| LLM2[(Low-cost coding models)]
    A2 --> SB[[Docker / E2B sandbox]]
    M --> GH[GitHub MCP<br/>branches · PRs · Projects]
    M --> CH[Slack / Discord<br/>human-in-the-loop]

    subgraph EE["ee/ — Enterprise"]
        SSO[SSO SAML/OIDC]
        WA[WhatsApp Business API]
        TM[Microsoft Teams]
        ROI[CTO ROI dashboard]
    end
    EE -.extends.-> CORE
```

### How a card moves

| Status | Who acts | What happens |
|---|---|---|
| `BACKLOG` | Human | Card created and prioritized |
| `AGENT_WORKING` | Agents | Architect plans, Coder writes in a sandbox, Reviewer tests |
| `WAITING_HUMAN` | Human | An agent asked a question in Slack/Discord and paused |
| `IN_REVIEW_PR` | Human + Reviewer | Pull request open, checks running |
| `DONE` | — | PR merged, cost and time recorded on the card |

The full contract is in
[`core/contracts/card-state.v1.json`](core/contracts/card-state.v1.json).

## Roadmap

- [ ] **v0.1 — headless engine:** orchestrator with 3 agents, LiteLLM routing,
      GitHub MCP, Slack/Discord approvals
- [ ] **v0.2 — the machine builds the machine:** v0.1 agents build the web
      Kanban (Next.js)
- [ ] **Live board:** public read-only view of our own development
- [ ] **Community MCP connectors:** Trello, Linear, Notion, Telegram
- [ ] **Enterprise:** SSO, WhatsApp Business, Teams, ROI dashboard

## Repository layout

```
.
├── core/            # Community edition — FSL-1.1-ALv2
│   ├── contracts/           # Card state contract and schemas
│   └── src/
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

We will open `good first issue` bounties for MCP connectors soon. Read
[CONTRIBUTING.md](CONTRIBUTING.md); every contributor signs the
[CLA](CLA.md) once, through a bot on the first pull request.

## Security

Please do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

Tradvia is **fair source**, not OSI open source. See
[LICENSE.md](LICENSE.md). "Tradvia" is a trademark of Anderson dos Santos Dutra; see
[TRADEMARKS.md](TRADEMARKS.md).
