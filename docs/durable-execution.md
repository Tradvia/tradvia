<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
# Durable execution

Status: **Shipped** (ADR 0006, phase 1). The card graph checkpoints every step, so a
run that dies in the middle of a card resumes instead of starting over.

## Thread id

Each card has one stable thread: `<project_id>:<repo>#<issue>`, for example
`1:Tradvia/tradvia#40`. The project id comes first and is also stored in every
checkpoint's metadata, so the same issue number in two projects never shares a
thread, and `history` and `fork` never return or copy another project's data.
Without a database the project id is the fixed local id `0`.

## Resume

`tradvia --issue N` looks up the card's thread before doing anything:

- a checkpoint with a pending human question: waits for the reply and continues
  (the planner is not called again);
- a checkpoint with pending steps: continues from it;
- no checkpoint (or a finished run): starts a new run.

## Durable wait in CI

With `--wait 0` the run makes one check for a reply and exits, leaving the card in
`WAITING_HUMAN`. The checkpoint in Postgres keeps the plan, so a reply that arrives
hours later resumes the card instead of replanning it:

- the `agent:ready` label starts the run, which posts the plan and ends;
- a comment from an OWNER / MEMBER / COLLABORATOR on the issue starts a new run that
  finds the checkpoint, reads the reply and continues.

`DATABASE_URL` must be passed to the job (as a repository secret) for this to work.
Without it the checkpoint is in memory and would be lost between jobs, so the CLI
prints a warning that durable waiting is disabled and falls back to waiting in the
same job (`--wait 3600`).

Status of the CI workflow side (`.github/workflows/agent.yml` passing `DATABASE_URL`
and triggering on comments): **Partial**. The CLI side is shipped; the workflow change
is still to be made by a maintainer.

## History and fork

```
tradvia history --issue N                       # step, node, status, date, checkpoint id
tradvia fork --issue N --checkpoint <ID>        # copy that checkpoint to a new thread
```

`fork` creates `<thread>~fork-<n>` in the same project, starting at the chosen
checkpoint. The original thread is not changed, and a checkpoint that does not
belong to the card's thread is rejected. Both commands read `TRADVIA_REPO` and
`DATABASE_URL` from the environment (or `.env`); neither needs `GITHUB_TOKEN`.

## Storage

- **With `DATABASE_URL`**: checkpoints live in Postgres through
  `langgraph-checkpoint-postgres`, which creates its own tables on first use.
- **Without it**: an in-memory saver is used and nothing connects to a database
  (offline tests and `TRADVIA_OFFLINE=1`). Checkpoints are lost when the process exits.

### New dependency

`langgraph-checkpoint-postgres>=2` is added to the `api` and `dev` extras. It reuses
the `psycopg` and `psycopg-pool` packages already there and is imported only when
`DATABASE_URL` is set, so the base install does not need it.

## Limits

A fork copies the checkpoint's state, not pending interrupt answers: resuming a fork
that sits at the approval step asks the question again.
