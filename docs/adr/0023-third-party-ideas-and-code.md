# ADR 0023: Bringing in ideas and code from other projects

- Status: Accepted
- Date: 2026-11-04

## Context

Tradvia is often compared with other agent projects (OpenHands, CrewAI, Mission Control and
others), and several cards came out of those comparisons (#133, #128, #134). Each project has its
own license, and an organization may license its repositories differently. `core/` is FSL-1.1-ALv2
(ADR 0001) and `ee/` is commercial, so anything taken from elsewhere must keep both clean and
keep the public material honest about who made what.

The repository already has the pieces this needs: `scripts/check-license-boundary.sh` skips
`vendor/` directories when it checks SPDX headers and the `core` → `ee` boundary, `NOTICE` has a
"Third-party components" section, and `TRADEMARKS.md` sets how the Tradvia marks may be used.

## Decision

1. **Ideas and features: reimplement them in Tradvia's own way.** Copyright protects code, not
   ideas. A feature seen elsewhere is designed and written again here, following our ADRs and
   style, without copying its code, text or tests.
2. **Dependencies (packages): only with the license of that specific repository checked,** not
   the organization's usual one, and with the version pinned. The pull request states the license
   and why the dependency is needed (CLAUDE.md: no new dependency without a clear reason).
3. **Copied code: only a small, isolated piece,** kept in `core/vendor/<project>/` with the
   original `LICENSE` file next to it. A file derived from it carries both identifiers, for
   example `SPDX-License-Identifier: FSL-1.1-ALv2 AND MIT`, and the original copyright line, and
   the component is listed under "Third-party components" in `NOTICE` (name, version, license,
   copyright). Nothing is copied into `ee/`.
4. **Other projects' names and marks: only to state compatibility** ("works with …", "can run …
   as an executor"), never to suggest a partnership or endorsement. It is the same rule
   `TRADEMARKS.md` sets for our own marks.
5. **Whole forks: no.** Tradvia does not fork another project into this repository.

## Consequences

- Comparisons stay useful: they become cards that reimplement the idea, not imports.
- A reviewer checks three things on any pull request that brings something in: the license of
  the exact repository and version, where copied code lives (`core/vendor/<project>/` with its
  `LICENSE`), and the `NOTICE` entry.
- The license-boundary check keeps ignoring `vendor/` on purpose; the `LICENSE` file and the
  `NOTICE` entry are what cover that code.
- No code is vendored today, so `NOTICE` still has no third-party entry.

## Status

| Capability | Status |
|---|---|
| Rules for ideas, dependencies, copied code, marks and forks | Shipped (this ADR, CONTRIBUTING) |
| Automated check that every `core/vendor/<project>/` has a `LICENSE` and a `NOTICE` entry | Planned |
