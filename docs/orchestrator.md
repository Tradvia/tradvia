# Orchestrator conversation

Status: 🟦 Partial. The conversation, the closed tool set, the budget, the cost record and
the audit are implemented and tested offline with a scripted model. Not yet shipped: durable
execution of a run across API restarts (#40; a run lost in a restart leaves the message
without a reply) and a pending-items inbox shared with the rest of the panel (#47; for now
pending items live in the conversation).

Until #104, the orchestrator could never actually run in production: the deploy image had
no Node.js/Claude Code CLI and nothing passed `CLAUDE_CODE_OAUTH_TOKEN` through. `#104`
fixed the deployment (Node.js + the CLI in `infra/oci/Dockerfile.api`, the token wired
through `deploy-oci.yml` → `compose.yaml`'s `api` service) — see "Credentials" below for the
one remaining owner step. Live-checked against real Claude Code once that secret is set.
Part of [ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md).

## How it works

Each project has one conversation, stored in Postgres (`chat_messages`: project, author,
kind, text, references to cards and pull requests, and the cost of the run behind a reply).
In the dashboard it is the **Conversation with the orchestrator** section: the history, an
input box and an "orchestrator working…" indicator while a run is in progress. The section
refreshes by polling, faster while the orchestrator is working.

- `GET /api/projects/{owner}/{repo}/chat` returns `messages`, `working`, `cost` (total
  recorded for the project) and `can_chat`. It needs the `read` role.
- `POST /api/projects/{owner}/{repo}/chat` with `{"text": "...", "refs": [{"type": "card",
  "number": 7}]}` stores the message, starts a run in the background and answers `202`. It
  has the same session, CSRF, idempotency key and audit as every panel action, and it needs
  the `start_card` interaction of `.tradvia.yml` (ADR 0004; `triage` by default). While a run
  is working a second message gets `409`.

A run is a loop. The model (Claude Code headless, `claude -p`) receives the project's recent
conversation and the results of the tools it already used, and answers one JSON object:
`{"tool": "...", "args": {...}}` to use a tool or `{"reply": "...", "refs": [...]}` to finish.
Tradvia executes the tool and loops; the reply is stored in the conversation. The model has
no shell, no file and no network tool: `--allowedTools Read` with `Bash`, `Edit`, `Write` and
the web tools explicitly disallowed, started in an empty scratch directory.

## What the orchestrator can do

| Tool | Does |
|---|---|
| `read_backlog`, `read_cards`, `read_card`, `read_plan` | Read the backlog, cards and the plan that waits for a reply |
| `read_pulls`, `read_pull`, `read_checks`, `read_deploys` | Read pull requests, their checks and the deploys waiting for approval |
| `create_card`, `edit_card` | Create a card at the end of the backlog; change a title or description |
| `reorder_backlog` | Set the order or move one card up or down |
| `start_card` | Mark a card ready for the agent (`agent:ready`) |
| `reply_plan` | Answer a plan with **`adjust`** and a text |
| `review_pr` | Run `make check` on the pull request branch in the project's runner and comment the result on the PR |

Every tool acts on the one project of the conversation, with the **signed-in person's own
GitHub token** (so GitHub shows who acted), and only with the arguments listed for it.

## What it cannot do

- **Approve or reject a plan, merge a pull request, approve or cancel a deploy, run the
  post-deploy operation.** These have no tool (ADR 0004). `reply_plan` refuses any decision
  but `adjust`. When the model asks for one of them, Tradvia refuses, audits it as denied and
  stores a **pending** message with a reference and a button that calls the existing panel
  action (approve/reject plan, merge, approve deploy), checked again against the person's
  role when clicked.
- Run any command other than the fixed `git fetch`, `git worktree` and `make check` of
  `review_pr`. The review refuses pull requests from forks (their code is not run) and runs
  with no token and `TRADVIA_OFFLINE=1`.
- See another project. Store queries filter by project, GitHub calls use the project's
  repository, and a tool argument naming a repository is refused.
- Use a tool that is not in the table, or an argument that is not listed: both are refused
  and returned to the model.

Text from cards, comments and pull requests is untrusted: the prompt says it is data, and
the closed tool set limits what a prompt injection can do.

## Credentials

No credential is put in the prompt. The user's GitHub token stays inside the API process and
is used only by the tools. The model process gets an allow-listed environment (`PATH`,
`HOME`, locale, proxy and CA settings and `CLAUDE_CODE_OAUTH_TOKEN`, which is its own login
to Claude): no `GITHUB_TOKEN`, no `DATABASE_URL`, no `TRADVIA_SECRET_KEY`.

**Production (owner step, #104):** `CLAUDE_CODE_OAUTH_TOKEN` has to exist in the `api`
service's environment in the first place for the allow-list above to have anything to pass
through. Run `claude setup-token` on your own machine and save the result as the
`CLAUDE_CODE_OAUTH_TOKEN` secret of the `production` GitHub environment (see
[deploy-oci.md](deploy-oci.md), step 4) — `deploy-oci.yml` and `compose.yaml` already wire it
from there into the container. Without it, every chat message fails with "The orchestrator
stopped: Claude Code CLI not found" (no CLI on `PATH`) or the CLI's own not-logged-in error
(CLI present, no token) — this is the deploy image's dependency, not the API's.

`review_pr` needs the project's checkout on the runner: set `TRADVIA_RUNNER_DIR_<OWNER>_<REPO>`
(for `Tradvia/tradvia`: `TRADVIA_RUNNER_DIR_TRADVIA_TRADVIA`). Without it the tool answers
that no checkout is configured. `git fetch` uses the runner's own git credentials.

## Limits and cost

Each human message has a budget; when a run reaches it, Tradvia stops it and stores a
warning message saying which limit was hit (what was already done is in the audit log).

| Variable | Default | Limit |
|---|---|---|
| `TRADVIA_ORCH_MAX_TURNS` | 10 | model calls per message |
| `TRADVIA_ORCH_TIMEOUT` | 300 | seconds per message |
| `TRADVIA_ORCH_BUDGET_USD` | 1.0 | API-equivalent cost per message |
| `TRADVIA_ORCH_MAX_TOKENS` | 200000 | input + output tokens per message |
| `TRADVIA_ORCH_REVIEW_TIMEOUT` | 900 | seconds for `make check` in `review_pr` |

The cost and tokens of every run (replies and warnings) are stored on its last message, and
the conversation shows the project's total. Invalid values fall back to the default. In
offline mode (`TRADVIA_OFFLINE=1`) the orchestrator answers with a warning instead of calling
Claude Code.

## Audit

Each tool use, including refused and failed ones, is an `audit_log` entry with the actor
`orchestrator`, the action `orchestrator:<tool>` and a detail that starts with
`for @<login>` (the signed-in person). The message itself is audited as the action `chat`
by the person.
