# ADR 0016: an evals harness with golden tasks, scripted by default

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

**Prompt versions (#125).** The planner and executor prompts live in
`core/src/tradvia/prompts/` (`plan.txt`, `exec.txt`, `architect.txt`) with a version and a
SHA-256 each in `versions.json`. A test fails when a text changes without a new version, so a
prompt change is always a new version, and every eval report records the versions it measured
(`"prompts": {"plan": "plan@1", ...}`). Plan comments and pull requests carry the same
reference (`Prompt: plan@1 · exec@1`), next to the cost and tokens.

**Canary (#128).** A prompt may have a *candidate* version (`plan@2.txt`, registered under
`candidate` in `versions.json`, hash-checked like the stable one). A project's canary, set on
Tradvia (Settings, Prompt and model canary, admin only; ADR 0018), runs a share of the cards on
the candidates and, optionally, on another model (LiteLLM planner and Claude Code). The cards are
picked by number, so a card keeps its side. Plans and PRs say so (`Prompt: plan@2 (canary)`),
the Settings screen shows both sides next to each other (cards, merged PRs, median plan cost),
and "Revert" sets the share to 0 in one action, keeping the record and an audit entry. A canary
should first show its gain in `make evals`.

## Consequences

- Scripted runs prove the harness and the expectations, not model quality;
  real quality needs manual live runs, so the capability is *Partial*.
- Expected outcomes are keyword/range heuristics: cheap and deterministic,
  but coarse. Richer graders can be added without changing the report format.
