# Evaluation: OpenHands as an executor (spike #134)

Status of this document: **incomplete evaluation**. The primary sources (the OpenHands
repository `LICENSE`, releases and CLI docs) could not be fetched when this was written
(web access was not granted in the run), so nothing below about OpenHands is stated as
fact. Every OpenHands item is marked **Not verified** and lists the exact source to read.
Tradvia-side facts come from this repository and are cited by path.

**OpenHands is not available in Tradvia today.** There is no adapter, no catalog entry and
no test for it.

## Recommendation

**Not yet. Do not open an adapter card until the checks below are done.** The decision
cannot be made from this document because the license of the specific package, the real
cost per task and the headless JSON output are all unverified, and the session driver
(#118) is not in this repository yet. If the checks pass, the answer becomes "yes, an
adapter in the [ADR 0010](../adr/0010-coding-agent-adapters.md) pattern, as a separate card".

Conditions for a "yes":

1. The `LICENSE` of the repository that publishes the SDK/CLI we would call (not the
   organization page) allows use as a runtime dependency under our terms, and any
   directory under another license (for example an `enterprise/` folder) is not used.
2. The CLI has a documented, stable non-interactive mode with machine-readable output
   (ADR 0010, "Headless only").
3. Tool restriction, no git, no network beyond the model provider, and turn and time
   limits can be enforced (ADR 0010; ADR 0006 budgets).
4. Cost and tokens are reported, or flagged "não informado" as ADR 0010 allows.

## 1. License

| Item | Evidence | Source | Date | Status |
|---|---|---|---|---|
| License of the SDK/CLI package repository | — | `LICENSE` in the specific repository (check the SDK/CLI repo, not the org) | — | Not verified |
| Parts under a different license (enterprise dirs) | — | same repository, directory listing and per-directory LICENSE files | — | Not verified |

Pending: read both and record SPDX id, URL and query date. Tradvia `core/` is FSL-1.1-ALv2
(ADR 0001); a dependency or subprocess call must be compatible with it.

## 2. Cost per task

Comparable Tradvia-side record: `core/src/tradvia/budget.py` and `claude_code.py` produce
duration, tokens and US$ per run; ADR 0006 reports about US$ 0.41 per card for Claude Code
(API-equivalent estimate, 2026-10-19).

| Item | Evidence | Status |
|---|---|---|
| Claude Code cost per card | ~US$ 0.41 (ADR 0006); US$ and tokens reported (`docs/coding-agents.md`) | Shipped (measured) |
| OpenHands cost per card | no run done, no API key available to measure | Not verified |
| Does OpenHands report US$? | unknown; if not, flag `usd_reported=False` | Not verified |

Pending: run the same small task on both executors and compare US$, tokens and duration;
if no key is available, estimate from the model's per-token price and mark it **estimated**.

## 3. Isolation

What Tradvia has today:

- The executor runs on a GitHub runner (ADR 0006: execution sandboxing is **Partial**).
- Session workers (isolated containers) are **Planned** (`docs/glossario.md`, Phase 2;
  README capability 1 is Partial).
- The session driver of #118 is not in this repository, so its coverage could not be read.
- Protected paths (`.github/`, `.git/`, `LICENSE.md`, `CLA.md`, `CODEOWNERS`,
  `.tradvia.yml`) are enforced by the flow after the run from `git status`, for any
  adapter (ADR 0010). That part does not depend on OpenHands.

Open questions, Not verified: whether the OpenHands Docker runtime fits inside a Tradvia
container session or duplicates it (Docker-in-Docker on a runner); whether it can run with
no git, no network beyond the provider, and turn and time limits.

## 4. Maintenance

| Item | Evidence | Source | Status |
|---|---|---|---|
| Release cadence | — | GitHub Releases of the package repository | Not verified |
| Commit/change rate, breaking changes in the CLI flags | — | commit history, changelog | Not verified |

## Comparison with the existing adapters

| Executor | Headless JSON | Cost reported | Tool limits | Status in Tradvia |
|---|---|---|---|---|
| Claude Code | `claude -p` | US$ and tokens | `--allowedTools`, `--max-turns` | Shipped |
| Gemini CLI | `gemini -p --output-format json` | tokens only | `--allowed-tools`, turns checked after the run | In progress |
| Codex CLI | `codex exec --json` | tokens only | sandbox mode, turns checked after the run | In progress |
| OpenHands | Not verified | Not verified | Not verified | Not available (evaluation only) |

Source for the first three rows: `docs/coding-agents.md`.

## Out of scope

- **CrewAI** is not an executor: it would duplicate LangGraph.
- **Mission Control** is in alpha and has a different stack.

## Next step

Whoever has web access: fill the "Not verified" cells above with source URL and date, run
the cost comparison, then update the recommendation. Only if it becomes "yes", add
OpenHands as **Planned** in `docs/coding-agents.md` and open a separate card for the
adapter. No new ADR is needed; ADR 0010 covers the pattern.
