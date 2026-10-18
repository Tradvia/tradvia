# ADR 0002: Project execution orchestration with multi-view transparency

- Status: Accepted
- Date: 2026-10-18
- Supersedes: the "hybrid Kanban" framing used in the README and website up to this date

## Context

Early material described Tradvia as "AI agents on a hybrid Kanban board". That
was only an example. The Kanban was meant to illustrate *transparency of what
each project member — human or agent — is doing*, not to define the product.

Teams follow progress through different notations depending on their method
and audience, and most already own a project tool they will not abandon.

## Decision

Tradvia is a **multi-agent orchestrator for project execution**. It
coordinates the work of AI agents and people, and makes progress transparent
through the notation each audience needs:

| Notation | Answers |
|---|---|
| **Kanban** | Where is each item in the workflow right now? |
| **Gantt** | How long does each item take, what depends on what, which milestones are at risk? |
| **Scrum / Burndown** | Is the sprint scope going to be delivered in time? |
| **WBS / EAP** | How does the scope decompose into deliverables? |
| **Calendar / Timeline** | What is due on which date? |
| **Workload / Capacity** | Who — person or agent — is over- or under-allocated? |

Architecture consequences:

1. **One neutral work model.** Every task is a `WorkItem` (hierarchy, dates,
   dependencies, estimate, assignee, sprint, status). The card-state contract
   from ADR 0001 becomes the lifecycle of a work item.
2. **Views are projections.** Kanban, Gantt, Burndown, WBS, Timeline and
   Workload are pure functions over the same list of work items. No view owns
   data.
3. **Native views and connectors.** Tradvia ships its own views *and* syncs
   with the tools teams already use. Connectors implement one interface
   (`TrackerConnector`) and map external items to `WorkItem`.

Initial connector targets: Trello, Jira, ClickUp, Monday.com, Asana, Wrike,
Smartsheet, Artia, Microsoft Planner, KanbanFlow and Airtable.

## Consequences

- Public material (website, organization profile, READMEs) is updated to this
  positioning. Every capability keeps an explicit Shipped / In progress /
  Planned status.
- `core/src/tradvia/work/` holds the work model, the projections and the
  connector interface. Rendering (web UI) consumes the projections later.
- Connectors are good first contributions: each one is isolated behind the
  same interface.
