# ADR 0006: Autonomous multi-agent orchestration (skills, topologies, context, governance)

- Status: Accepted
- Date: 2026-10-19

## Context

Today Tradvia runs one fixed flow per card: a planner (Claude Code) writes a
plan, a human approves it in the issue thread, an executor (Claude Code)
implements it, and Tradvia opens a pull request. That flow shipped seven
features on 2026-10-19 for about US$ 0.41 per card (API-equivalent estimate).

The founder's target is broader: agents with many skills that act on their own,
with Tradvia acting as the **operating system for governance, routing and
context** instead of a micro-manager that dictates each step. With dozens of
skills per agent, putting every skill into the prompt degrades reasoning
("context rot"), raises token cost and causes wrong tool choices. Giving
agents autonomy without infrastructure guardrails leads to loops, runaway cost
and destructive actions.

## Decision

### Pillar: everything belongs to a project

The **project** is the unit of scope for everything Tradvia stores or decides,
the same way Tradvia itself is the project `Tradvia/tradvia` today. Whatever is
added while the orchestrator is in use is linked to one project:

- cards, plans, approvals, runs, pull requests and their costs;
- skills, agents and their manifests (a project may enable skills from a shared
  catalog and add its own; enabling or overriding is recorded per project);
- context and memory: specs, decisions, compiled context payloads, episodic and
  procedural memory, embeddings;
- budgets, risk policy and access rules (`.tradvia.yml`, ADR 0004);
- events on the bus, blackboard entries, checkpoints and sandboxes.

Consequences for every capability below: data carries a `project_id`; queries,
memory retrieval and skill routing never cross projects unless the project
explicitly opts into a shared resource; viewing and interaction follow the
project's access level (ADR 0004); and a project can be exported or deleted as a
whole. Status: **Partial**. Configuration (`.tradvia.yml`) and access are already
per repository; the store and API still assume a single repository
(`TRADVIA_REPO`).

Tradvia evolves into an event-driven orchestrator with shared state and dynamic
discovery of capabilities, organised in four areas. Each item has a real status
(CLAUDE.md rule 5) and is delivered as small cards through the normal flow.

### 1. Skill lifecycle

| Capability | What Tradvia does | Status |
|---|---|---|
| Agent & Skill Registry | Every agent and skill publishes a manifest (MCP-compatible JSON Schema): input preconditions, output guarantees, estimated cost, average latency and **risk level** (read, reversible write, destructive/external). | Planned |
| Lazy skill loading | Agents start with a light catalog (name + one-line description, ~50 tokens per skill). The full instructions, examples and tool schemas are loaded only when the agent asks to expand a skill for a subtask. | Planned |
| Semantic skill router | Before the main model runs, embeddings (pgvector) or a small model pick the top-K relevant skills and inject only those. | Planned |

### 2. Orchestration topologies

Tradvia supports several topologies and picks one per task type:

| Topology | How it maps onto Tradvia | Use for | Status |
|---|---|---|---|
| Hierarchical supervisor with sub-graphs | The supervisor decomposes the goal into a dynamic DAG and delegates whole nodes to agents that run their own loop until their output contract is met. Today's planner → executor flow is the one-worker case. | Auditable work with SLAs | Partial (single worker) |
| Blackboard | The Kanban **is** the blackboard: cards and subtasks live in Postgres; agents watch for work that matches their skills and claim it with an atomic lock (`SELECT … FOR UPDATE SKIP LOCKED`), then publish artifacts back. | Non-linear problems (incident diagnosis, research, architecture) | Planned |
| Event choreography (pub/sub) | GitHub webhooks already enter Tradvia (ADR 0005). Internal events (`CardReady`, `CodeGenerated`, `VulnerabilityFound`, `TaskCompleted`) use CloudEvents envelopes on an event bus: Postgres `LISTEN/NOTIFY` first, a broker later. | Long-running asynchronous pipelines | Partial (webhook ingestion) |
| Dynamic handoff (swarm) | A `transfer_to_agent` primitive passes control of a session plus a structured context summary to another specialist. | Triage and multi-step resolution | Planned |

### 3. Context engineering and layered memory

| Capability | What Tradvia does | Status |
|---|---|---|
| Scratchpad isolation | When an agent delegates, Tradvia passes a **compiled context payload** (goal + relevant artifacts + constraints), never the other agent's chain of thought or tool errors. | Partial (planner passes only the plan to the executor) |
| Episodic and procedural memory | Successful runs are stored (Postgres + pgvector). A trajectory that solved a class of problem is offered as a reference for similar tasks; per-project memory keeps specs and decisions. | Planned |
| Context garbage collection | After a heavy skill (long logs, scraping), Tradvia summarises the result into a structured artifact and drops the raw tokens from the active window. | Planned |
| Spec-driven work | A written spec becomes planned, reviewable cards and the reference the agents are checked against. | Partial (issue body is the spec) |

### 4. Resilience, governance and execution control

| Capability | What Tradvia does | Status |
|---|---|---|
| Execution budgets | Each autonomous run gets a TTL, a maximum number of steps and a token/cost ceiling. At 80% without converging, Tradvia injects a system notice forcing a synthesis or a request for help. | Partial (workflow timeout, replan limit, cost per card) |
| Stagnation detection | Tradvia watches tool calls and intermediate outputs; three identical or equivalent calls in a row stop the loop, inject a reflective error or route to a reviewer agent. | Planned |
| Durable execution and checkpointing | Every state change is persisted (LangGraph checkpointer on Postgres). A failure at step 14 of 15 resumes at 14; a developer can fork the state at step 10 to try another skill. | Partial (in-memory checkpointer) |
| Risk-based permission gateways (HITL) | Skills are classified by risk. Reading and drafting run freely; reversible writes follow policy; destructive or external actions suspend the agent until a human, or a Critic/Policy agent, approves. Roles come from ADR 0004. | Partial (plan approval gate + role policy) |
| Execution sandboxing | Code and file skills run in ephemeral isolated environments per agent session (containers now, microVMs later), so autonomy never compromises the host. | Partial (GitHub runners / self-hosted runner with limits) |

## Delivery order

0. **Project as a first-class entity**: `projects` table, `project_id` on every
   stored record, API and dashboard scoped by project, migration of today's
   single-repository data. Everything else builds on it.
1. **Governance foundation** (makes autonomy safe, builds on what exists):
   durable checkpointing on Postgres → execution budgets with the 80% notice →
   stagnation detection → risk levels on skills and the permission gateway.
2. **Skills and context**: registry and manifests → lazy loading → compiled
   context payloads and context GC → semantic router.
3. **Topologies**: blackboard on the Kanban (atomic claims) → event bus with
   CloudEvents → handoff primitive → supervisor DAG with sub-graphs.
4. **Memory and isolation at scale**: episodic/procedural memory with pgvector,
   per-session sandboxes (needs a larger host).

## Consequences

- Tradvia's value moves from "a pipeline that writes PRs" to the control plane
  that decides what each agent sees, can do and may spend.
- Postgres becomes central (state, blackboard, event log, checkpoints,
  memory). Backups and migrations become a requirement, not an option.
- Embeddings for the router and memory need either an API (cost per call) or a
  host larger than today's 1 GB machine.
- More autonomy means more surface for mistakes: every capability ships with
  tests for its guardrail first, and the public site keeps stating real status.
