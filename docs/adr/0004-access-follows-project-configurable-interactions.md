# ADR 0004: Access follows the project; interactions are configurable

- Status: Accepted
- Date: 2026-10-19

## Context

The first runs dashboard went live at `tradvia.org/runs`. It reads public
GitHub data with no login. That raised the question of who should see the
dashboard and who should be able to act through Tradvia: start a card,
approve a plan, call an assistant or, later, take over a live session.

The data behind the dashboard comes from the project itself (issues, pull
requests, workflow runs). Hiding it in Tradvia when the project is public
protects nothing. Showing it in Tradvia when the project is private would
leak it.

## Decision

### 1. Viewing follows the project's visibility

| Project | Who can view cards, runs and KPIs |
|---|---|
| Public | Anyone, read-only, no login |
| Private | Only people signed in with GitHub who have at least `read` on the repository |

Tradvia never shows more than the source system shows. A project may
**restrict** viewing further (for example, require `read` even on a public
repository), but configuration can never **widen** it.

### 2. Interactions have a configurable minimum role

Anything that changes state is an *interaction*. Each one has a minimum
GitHub role, configured per project in `.tradvia.yml` at the repository root.
Roles follow GitHub's order: `read < triage < write < maintain < admin`.
Identity is always the person's GitHub account. Tradvia keeps no users or
passwords of its own.

| Interaction | What it allows | Default | Status |
|---|---|---|---|
| `start_card` | Start a card (`agent:ready` label today) | `triage` | Partial (GitHub enforces `triage` for labels) |
| `approve_plan` | Answer a plan: approve, reject or adjust | `write` | Partial (hard-coded today) |
| `mention_assistant` | Call `@claude` on issues and pull requests | `write` | Partial (enforced by the action) |
| `live_takeover` | Take over a frozen browser or terminal session | `maintain` | Planned |
| `budget_control` | Kill switch and budget limits | `admin` | Planned |

Example:

```yaml
# .tradvia.yml
access:
  view: inherit            # inherit | read | triage | write | maintain | admin
interactions:
  start_card: triage
  approve_plan: write
  mention_assistant: write
  live_takeover: maintain
  budget_control: admin
```

### 3. Safety invariants

1. No interaction can be configured below `read`: anonymous users never act.
2. Bots never count as approvers, whatever their role.
3. Agents can never edit `.tradvia.yml` (it joins the protected paths next to
   `.github/`, licenses and `CODEOWNERS`). It changes only through a reviewed
   pull request.
4. Unknown keys or invalid roles make the policy fail closed: Tradvia refuses
   the interaction and explains why, instead of falling back to open access.
5. The policy is read from the default branch, never from a pull request branch.

## Phases

1. **Core policy (next card):** `Policy` model and loader with defaults and
   validation; `approve_plan` and `start_card` enforced through GitHub's
   collaborator permission API (`role_name`); `.tradvia.yml` added to the
   protected paths; an example file in `docs/`.
2. **Workflow wiring (maintainer):** pass the label actor to the agent so
   `start_card` above `triage` can be enforced; align `@claude` with
   `mention_assistant` when it is set above `write`.
3. **Dashboard:** show the effective policy ("who can do what") on public
   projects; GitHub sign-in for private projects and for interactive
   dashboard actions, when they exist.

## Consequences

- `tradvia.org/runs` stays public because `Tradvia/tradvia` is public.
- Private customer projects need GitHub sign-in before any dashboard is
  served. This is a hard requirement for the Cloud edition.
- Admins should add `/.tradvia.yml @Tradvia/admins` to `CODEOWNERS` once the
  file exists, so policy changes need an admin review.
