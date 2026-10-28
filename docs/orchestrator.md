# Orchestrator conversation

Status: 🟦 Partial. The conversation, the closed tool set, the budget, the cost record and
the audit are implemented and tested offline with a scripted model. Not yet shipped: durable
execution of a run across API restarts (#40; a run lost in a restart leaves the message
without a reply), a pending-items inbox shared with the rest of the panel (#47; for now
pending items live in the conversation) and a live check against a real provider. The chat
runs only on an AI provider with the project's own API key ([ADR
0013](adr/0013-chat-needs-an-api-key.md)): without one it is off and the panel says why.
Part of [ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md) and
[ADR 0009](adr/0009-project-connections.md) (the `orchestrator` role, below).

## How it works

Each project has one conversation, stored in Postgres (`chat_messages`: project, author,
kind, text, references to cards and pull requests, and the cost of the run behind a reply).
In the dashboard it is the **Conversation with the orchestrator** section: the history, an
input box and an "orchestrator working…" indicator while a run is in progress. The section
refreshes by polling, faster while the orchestrator is working.

- `GET /api/projects/{owner}/{repo}/chat` returns `messages`, `working`, `cost` (total
  recorded for the project), `can_chat` and `chat_off_reason`. It needs the `read` role.
  `can_chat` is true only for someone with `start_card` **and** when an AI provider with an
  API key answers the chat; otherwise, for someone with `start_card`, `chat_off_reason` is
  `"no_api_key"` and the panel shows how to configure it.
- `POST /api/projects/{owner}/{repo}/chat` with `{"text": "...", "refs": [{"type": "card",
  "number": 7}]}` stores the message, starts a run in the background and answers `202`. It
  has the same session, CSRF, idempotency key and audit as every panel action, and it needs
  the `start_card` interaction of `.tradvia.yml` (ADR 0004; `triage` by default). While a run
  is working a second message gets `409`; without an AI provider and its API key it gets `409`
  too, and nothing is stored.

A run is a loop. The model receives the project's recent conversation and the results of the
tools it already used, and answers one JSON object: `{"tool": "...", "args": {...}}` to use a
tool or `{"reply": "...", "refs": [...]}` to finish. Tradvia executes the tool and loops; the
reply is stored in the conversation. The model has no shell, no file and no network tool of
its own: it is a plain chat completion through LiteLLM, so there is no tool for it to call
beyond answering the one JSON object Tradvia expects.

## Which model answers

The `orchestrator` row of the project's connections (`dashboard` → Connections, [ADR
0009](adr/0009-project-connections.md)) decides, same precedence as every other role
(`.tradvia.yml`'s `providers` section is the default, a value set on the Connections screen
overrides it):

- **`anthropic`, `openai` or `gemini`, with its API key saved:** a plain chat completion
  through LiteLLM (already a `core` dependency — the planner, `llm.py`, uses it the same way),
  with the project's own stored key passed on the call itself. Nothing is shelled out, so none
  of this needs Node.js or a CLI. Each message is billed to that key (per use).
- **Anything else — unset, no key saved yet, or a coding-agent CLI:** the chat is off for the
  project (`orchestrator.model_for_connection` gives no model). There is no fallback to Claude
  Code and no subscription login for the chat (ADR 0013): the `claude-code` connection runs
  only the executor. The orchestrator role refuses CLI providers on the screen and in
  `.tradvia.yml` alike.

This is per project, not a server-wide setting: two projects on the same Tradvia deployment can
answer through two different providers, and switching providers needs no redeploy — only a
change on the Connections screen.

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
is used only by the tools.

- The project's decrypted connection secret (ADR 0009) is passed as the
  `api_key` argument of the one completion call, never through an environment variable of the
  API process. Several projects' chat runs can be in flight at once, each in its own thread of
  the same process (`api.py`'s `start_thread`); an environment variable would be shared by all
  of them and could leak one project's key into another's run. Any error from the provider is
  redacted the same way `review_pr`'s output already is, so a key echoed back in an error
  message never reaches the stored conversation.

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
the provider.

## Audit

Each tool use, including refused and failed ones, is an `audit_log` entry with the actor
`orchestrator`, the action `orchestrator:<tool>` and a detail that starts with
`for @<login>` (the signed-in person). The message itself is audited as the action `chat`
by the person.
