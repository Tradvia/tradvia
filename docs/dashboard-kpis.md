# Dashboard KPIs, filters and token usage

What each indicator in `dashboard/index.html` measures and where every number comes from.
Capability status and a step-by-step guide are on the dashboard's Help page (`dashboard/docs.html`, Shipped).

> **Costs are estimates, not an invoice.** The dollar figures are the API-equivalent cost
> that `claude -p` reports (`total_cost_usd`) or that LiteLLM computes for the planner. They
> are not what a subscription or the cloud provider bills.

## Where the numbers come from

| Number | Source | Where it is kept |
| --- | --- | --- |
| Executor cost and tokens | JSON of `claude -p`: `total_cost_usd` and `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens` + `cache_creation_input_tokens` as cache) | Lines in the pull request description: `Estimated cost (API-equivalent): $1.0051 · 285s` and `Tokens: 12 345 in · 3 210 out · 80 000 cache`; parsed into `pull_requests` |
| Plan cost and tokens | The planner result (`claude -p` or LiteLLM). One per plan, so every re-plan is its own number | Same two lines in the plan comment of the agent; parsed into `comments` (`cost_usd`, `tokens_*`) |
| Cost "so far" | `estimated cost so far` line of the PR (plans + executor) | Only a fallback, see below |

A missing `usage`, or one without cache fields, counts as 0 tokens. The `Tokens:` parser
accepts a space, non-breaking space, dot or comma as thousands separator, and plain numbers.

The store (`schema.sql`) adds the columns idempotently (`ADD COLUMN IF NOT EXISTS`):
`pull_requests.tokens_in|tokens_out|tokens_cache` and `comments.cost_usd|tokens_in|tokens_out|tokens_cache`.
`python -m tradvia.backfill` rewrites comments and PRs, so history is filled in wherever the
line exists in GitHub. Older PRs and plans without the `Tokens:` line stay without tokens.

The read API adds `tradvia_cost` (with tokens) to each pull request and `tradvia_plan_cost`
(one entry per plan comment) to each issue. These are not GitHub fields.

## Cost and tokens per card

```
card = sum of all plans of the card (every re-plan) + the executor of its PR
```

The PR's "estimated cost so far" already includes the plans, so it is **not** added on top.
It is used only when no plan comment is stored for the card (history from before this
feature), and then it stands for the whole card. The same rule applies to tokens, except that
the PR `Tokens:` line is the executor alone.

## Indicators

| Indicator | Measures | Status |
| --- | --- | --- |
| Success rate, median duration | Runs of the selected executor in the period (skipped and cancelled ignored) | Shipped |
| Agent PRs merged, median PR → merge | PRs created in the period on `tradvia/` or `claude/` branches | Shipped |
| Cards in the period | Cards created in the period that pass the filters | Shipped |
| Total cost (planning + execution) | Sum of the card cost over the cards in view that have a recorded cost | Shipped |
| Median cost per card | Median of the card cost | Shipped |
| Cost per merged PR | Mean card cost of the cards whose PR was merged in the period | Shipped |
| Total tokens (in, out, cache) | Sum over the cards in view | Shipped |
| Tokens per card | Median of in + out + cache per card | Shipped |
| Cost and tokens per day (SVG bars) | Plans by comment time, executor by PR creation time (UTC) | Shipped |

With no cost data in the period a cost or token indicator shows "—" with a tooltip
explaining why. Cards without any recorded cost are left out of the sums and medians.

## Filters

Filters apply to the indicators, the cards and the runs, and live in the URL, next to `?repo=`:

| Parameter | Values |
| --- | --- |
| `from` | `7d`, `30d`, `90d` (relative to today) or `YYYY-MM-DD`; absent = all |
| `to` | `YYYY-MM-DD`, inclusive; absent = open-ended |
| `executor` | `agent` (Tradvia agent, `agent.yml`), `claude` (Claude Code, `claude.yml`); absent = all |
| `fcard` | an issue number: the board shows only that card; absent = all. A card that is not on the board is ignored, and the panel says so |
| `card` | an issue number: opens that card on load (the link from notifications and the inbox). It does not filter |
| `status` | `BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR` or `DONE`; absent = all |

Example: `?repo=Tradvia/tradvia&from=30d&executor=claude&status=DONE`.

## Card details

The board shows, on each card, the agent types that worked on it as chips: `Planner`,
`Tradvia agent` and `Claude Code`. Click the card, or press Enter on it, to open its dialog:

| Section | Content | Source |
| --- | --- | --- |
| Agents that worked on it | One row per agent: runs, plans, tokens in / out / cache and estimated cost, and the card total | Plan comments of the card (store), the executor cost of the newest costed PR, the card's runs |
| Diagnosis | State, why the card is on the board, the agent's last marker, labels, runs (total, without result, running now), last run and its duration, PRs, dates | The card, its PRs and its runs |
| Approval, pull request, activity, audit | The same controls as before, for whoever has the role | GitHub. Loaded when the card opens and when the card changes, not on every refresh |

A dash (—) means no cost or count was recorded for that agent. Status: **Shipped**.
**Partial:** the planner's tokens and cost come from the store; when the panel reads GitHub
directly they show "—".

## Limits

- **Partial:** per-plan costs come from the store. When the panel reads GitHub directly
  (no store), comments are not loaded, so the card cost falls back to the PR's
  "cost so far" and the token figures cover the executor only.
- The board reads the first 50 cards with the `agent:ready` label, every open issue that has an
  open agent PR (up to 100 open issues and 100 open PRs), the 50 most recent PRs and the last 30
  runs per workflow. A card with an open agent PR is on the board even without the label, because
  the bell counts it.
- Dates are compared in UTC.
