# ADR 0012: an evals harness with golden tasks, scripted by default

- Status: Accepted
- Date: 2026-10-26

## Context

The only safety net is `make check`, which runs after a change has already
happened. Nothing tells us whether a new prompt or model plans better or worse
than the current one. Card #124 asks for a harness that does, and the canary
card and any future RAG work depend on it.

## Decision

1. **Golden tasks.** `core/src/tradvia/evals/tasks/*.yaml` hold small,
   representative issues (docs fix, bug fix, ambiguous request, protected
   path, ...) with an expected outcome: step range, words the plan must
   mention, paths it must not target. Plans are validated with
   `claude_code.parse_plan`; cost uses `agents.Cost`.
2. **Scripted planner by default.** `scripts/<label>.yaml` holds fixed answers
   per task, so `make evals` needs no network and no API key, like the
   current tests. A label names a prompt/model version.
3. **Live is optional and manual.** `--planner live` calls
   `llm.architect_plan` with `TRADVIA_PLANNER_MODEL`, and only with
   `TRADVIA_EVALS_LIVE=1`. Without it `litellm` is never imported. Live runs
   are not part of `make check` or CI.
4. **Comparison.** `python -m tradvia.evals compare base.json head.json`
   aligns tasks by id and shows the pass transition plus deltas of steps,
   cost and tokens. A pass-to-fail regression exits non-zero.
5. **Prerequisite.** The canary and any RAG work must show gains here first.

`make evals` is deliberately not part of `make check` yet; wiring it into CI
is a separate change.

## Consequences

- Scripted runs prove the harness and the expectations, not model quality;
  real quality needs manual live runs, so the capability is *Partial*.
- Expected outcomes are keyword/range heuristics: cheap and deterministic,
  but coarse. Richer graders can be added without changing the report format.
