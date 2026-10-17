# ADR 0001: Open-core layout with FSL core and commercial EE

- Status: Accepted
- Date: 2026-10-17

## Context

Tradvia must be free to self-host and inspect, so it can build a
community, while preventing cloud providers and competitors from selling it as
a managed service without contributing. The business model needs room for paid
enterprise features (SSO, WhatsApp Business API, Microsoft Teams).

## Decision

1. A single repository (monorepo) with two top-level directories:
   - `core/` under **FSL-1.1-ALv2**: any use except a Competing Use; each
     release becomes Apache 2.0 two years after it ships.
   - `ee/` under a **commercial license**: source visible, production use
     requires a subscription; features are gated by a license key.
2. `core/` never depends on `ee/`. `ee/` plugs into extension points exposed
   by `core/`. Enforced in CI by `scripts/check-license-boundary.sh`.
3. All contributions require the CLA in `CLA.md`, so the Company can license
   contributions under both licenses.

## Alternatives considered

- **Elastic License 2.0 for core**: similar protection, but it never converts
  to an open source license, which costs more community trust.
- **AGPLv3 for core**: OSI-approved open source, but it does not prevent a
  competitor from hosting the software if they publish their changes.
- **Separate private repository for EE**: stronger secrecy, but harder to keep
  versions in sync. Can be revisited if EE code must become confidential.

## Consequences

- The project is "fair source" / source-available, **not** OSI open source.
  Communication must not call it "open source".
- Changes to licensing files require admin review (see `CODEOWNERS`).
