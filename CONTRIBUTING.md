# Contributing to Tradvia

Thanks for your interest in Tradvia!

## Before you start

1. **Sign the CLA.** On your first pull request the CLA bot will ask you to
   sign the [Contributor License Agreement](CLA.md) with a comment. Pull
   requests cannot be merged until it is signed.
2. **Open an issue first** for anything larger than a small fix, so we can
   agree on the approach.

## Development environment

Open a ready-to-code workspace in one click with **Code → Codespaces**, or use
the Dev Container locally. Then run `make test`. Details, plus how Tradvia
works on its own issues, are in [docs/dev-environment.md](docs/dev-environment.md).

Looking for something to do? Pick an issue labelled `good first issue`, or
propose one that the agent can take with the `agent:ready` label.

## Where your change goes

| You are changing | Directory | License |
|---|---|---|
| Community features, bug fixes, docs | `core/` | FSL-1.1-ALv2 |
| SSO, WhatsApp Business, Teams and other enterprise modules | `ee/` | Commercial |

`core/` must never import from `ee/`. CI fails if it does.

## Workflow

1. Fork the repository and create a branch from `main`:
   `feat/<short-name>`, `fix/<short-name>` or `docs/<short-name>`.
2. Use [Conventional Commits](https://www.conventionalcommits.org/):
   `feat(core): add metric retention setting`.
3. Add the SPDX header to every new source file:
   - in `core/`: `SPDX-License-Identifier: FSL-1.1-ALv2`
   - in `ee/`: `SPDX-License-Identifier: LicenseRef-Tradvia-Commercial`
4. Run `scripts/check-license-boundary.sh` locally.
5. Open a pull request against `main`. `main` is protected: changes land only
   through reviewed pull requests with green checks, merged by squash.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
