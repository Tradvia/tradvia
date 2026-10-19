# Development environment

Tradvia is built in public, and from now on partly **by Tradvia itself**.
There are two environments:

| | What it is | Who uses it | Cost |
|---|---|---|---|
| **Dev workspace** | GitHub Codespaces or a local Dev Container with Python 3.12, the core package and tests ready | Every contributor | Codespaces: your personal free monthly quota; local: free |
| **Dogfooding runtime** | The `agent` GitHub Actions workflow running Tradvia on this repository's issues | Maintainers start it; everyone sees the results | Free Actions minutes for public repos + LLM tokens (offline mode costs nothing) |

## 1. Open the dev workspace

**Online (no install):** on the repository page click **Code → Codespaces →
Create codespace on main**, or use the badge in the README. The container runs
`make setup`, so after about two minutes you can run:

```bash
make test                 # core test suite, offline, no API keys
make check                # tests + license boundary, same as CI
```

**Local:** install Docker and VS Code with the *Dev Containers* extension, clone
the repository and choose **Reopen in Container**. Without Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make setup && make test
```

`TRADVIA_OFFLINE=1` is set by default in the container, so nothing calls a paid
model unless you change it in `core/.env`.

## 2. Let Tradvia work on an issue (dogfooding)

1. Open an issue describing a small, self-contained change (one new file in v0.1).
2. A maintainer adds the **`agent:ready`** label.
3. `@Agent-Architect` comments a plan on the issue and the card moves to
   `WAITING_HUMAN`.
4. A maintainer answers in the issue: **Aprovado** / **Approved**, **Rejeitado** /
   **Rejected**, or an adjustment (the agent re-plans, up to 3 times).
5. On approval `@Agent-Coder` creates `tradvia/issue-<n>`, commits the file and
   opens a pull request that goes through normal review.

Safety rules built into the flow:

- Only people with triage access can add labels, and only replies from
  `OWNER`, `MEMBER` or `COLLABORATOR` are read; bots and outsiders are ignored.
- Agents can never write to `.github/`, `.git/`, `LICENSE.md`, `CLA.md` or
  `CODEOWNERS`, and every change still needs a reviewed pull request.
- Without the `ANTHROPIC_API_KEY` repository secret the run uses the offline
  planner, so the whole loop can be tested at zero cost.

To use a real model, a maintainer adds the `ANTHROPIC_API_KEY` secret under
**Settings → Secrets and variables → Actions** and, optionally, the
`TRADVIA_PLANNER_MODEL` variable (any LiteLLM model string).

Known v0.1 limitation: pull requests opened with the workflow's `GITHUB_TOKEN`
do not trigger other workflows, so a maintainer re-runs CI on the agent's PR
(close and reopen it). A GitHub App token removes this in v0.2.

## 3. Run the flow from your workspace

```bash
cp core/.env.example core/.env    # fill GITHUB_TOKEN and TRADVIA_REPO for your fork
cd core && TRADVIA_OFFLINE=1 tradvia --issue <n> --chat github
```

Use your **fork** as `TRADVIA_REPO` while experimenting, so test branches and
pull requests stay out of the main repository.

## 4. Claude Code CLI as planner and executor (subscription, no API key)

Two optional environment variables choose who plans and who implements. Both
default to today's behavior, so nothing changes unless you set them.

| Variable | Values | Default |
|---|---|---|
| `TRADVIA_PLANNER` | `claude-code` (`claude -p`, read-only tools), `litellm`, `offline` | `litellm` (offline when `TRADVIA_OFFLINE=1`) |
| `TRADVIA_EXECUTOR` | `claude-code` (edits the working tree), `note` (one file via the API) | `note` |

With `claude-code` the CLI must be installed and on `PATH`, authenticated with
`CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`); no `ANTHROPIC_API_KEY` is needed.
`TRADVIA_CLAUDE_TIMEOUT` (seconds, default 900) limits each call.

After approval, Claude Code implements the plan in the current directory (start from a
clean checkout: every changed file goes into the PR). Tradvia then commits all changed
files to `tradvia/issue-<n>` and opens the PR with Claude's summary, cost and time. If
any changed file is under `.github/` or `.git/`, or is `LICENSE.md`, `CLA.md` or
`CODEOWNERS`, no PR is opened and the card goes back to `WAITING_HUMAN`.

Status: the CLI wiring is **Partial**. Deleted files are not propagated to the PR, and
the `agent.yml` workflow does not install the CLI yet (a maintainer does that).
