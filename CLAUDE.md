# Tradvia — guide for Claude

Tradvia is an open-core AgentOps and hybrid execution control plane: humans,
multi-model APIs and isolated container sessions on an executable hybrid
Kanban. Read `README.md` and `docs/adr/` before larger changes.

## Repository layout

- `core/` — community edition, license FSL-1.1-ALv2. Python ≥ 3.11, package `tradvia`
  in `core/src/tradvia/`, tests in `core/tests/`.
- `ee/` — enterprise edition, commercial license.
- `docs/adr/` — architecture decisions. Add an ADR for any decision that changes
  architecture or public positioning.
- `.github/` — CI, rulesets, templates. Do not edit unless the issue asks for it.

## Hard rules

1. `core/` must never import from `ee/`. Check with `make boundary`.
2. Every new source file starts with an SPDX header:
   `# SPDX-License-Identifier: FSL-1.1-ALv2` in `core/`,
   `# SPDX-License-Identifier: LicenseRef-Tradvia-Commercial` in `ee/`.
3. Never modify `LICENSE.md`, `CLA.md`, `CODEOWNERS`, `.github/rulesets/` or the
   CLA workflow.
4. Never commit secrets, tokens or `.env` files.
5. Public material (README, site, profile) must state the real status of each
   capability: Shipped, In progress, Partial or Planned. Do not describe planned
   features as available.

## Before opening a pull request

- Run `make check` (tests in offline mode + license boundary). Tests must pass
  without API keys (`TRADVIA_OFFLINE=1`).
- Add or update tests for behavior changes.
- Use Conventional Commits: `feat(core): ...`, `fix(agent): ...`, `docs: ...`.
- Keep pull requests small and focused on one issue; reference it with `Closes #N`.

## When running as the GitHub Action (@claude)

1. Make the change on your branch and run `make check`. If it fails, fix the
   code and run it again; never open a pull request with failing checks.
2. After pushing, open the pull request yourself against `main`:
   `gh pr create --base main --title "<conventional commit title>" --body "<summary>\n\nCloses #N"`.
   Say in the body what you changed and paste the last lines of `make check`.
3. Reply on the issue with the pull request link. Merging is always done by a
   maintainer.

## Style

- Python: type hints, small pure functions where possible, no new dependencies
  without a clear reason stated in the PR.
- Docs and code in English. Issues and discussions may be in Portuguese; answer
  in the language the person used.
