# ADR 0005: Hosting on Oracle Cloud (OCI) Always Free

- Status: Accepted
- Date: 2026-10-19

## Context

Until now everything ran on GitHub: Pages for the site and dashboard, Actions
for the agents. That hit limits that block the next steps:

- The dashboard calls the GitHub API from the browser without a token, so each
  viewer gets 60 requests per hour and private projects cannot be shown
  (ADR 0004 requires GitHub sign-in for those).
- There is no place for a long-lived API, a database for cards and costs, or
  the stateful session workers and Live Session Takeover planned in ADR 0003.

Free tiers were compared in October 2026. Several popular ones no longer fit:
Vercel Hobby forbids commercial use, Fly.io and Koyeb dropped free compute,
Render deletes free databases after 30 days. OCI Always Free is the only
option with an always-on machine.

## Decision

Run Tradvia's own infrastructure on one OCI Always Free Ampere A1 instance in
the account's home region:

| Resource (Always Free, Oct 2026) | Use |
|---|---|
| A1.Flex, 2 OCPU / 12 GB RAM (1,500 OCPU-h and 9,000 GB-h per month) | Docker host: reverse proxy, API, database, later session workers |
| Block volume, 200 GB total | Boot volume and data |
| Outbound transfer, 10 TB per month | Dashboard and API traffic |

Layout on the instance (`infra/oci/`):

- **Caddy**: the only public service, ports 80/443, automatic HTTPS for
  `app.tradvia.org`, security headers.
- **API**: next card. It calls GitHub server-side with a token, caches the
  answers, and implements GitHub sign-in (ADR 0004).
- **PostgreSQL 16**: internal network only, never published to the internet.
- **Dashboard**: served as static files from `dashboard/`.

Deploys run from GitHub Actions over SSH with a deploy-only key, in a
protected `production` environment that needs a maintainer's approval.

`tradvia.org` (the marketing site) stays on GitHub Pages for now. Moving it is
a separate decision.

## Rules

1. Only ports 22, 80 and 443 are open: in the OCI security list and on the
   instance firewall. SSH accepts keys only, no passwords and no root login.
2. Secrets (deploy key, GitHub tokens, OAuth client secret, database password)
   live in GitHub environment secrets or in a root-only `.env` on the
   instance. Never in the repository.
3. Stay within the Always Free limits. Any change that would create a paid
   resource needs a new ADR.
4. Oracle reclaims Always Free instances that stay below 20% CPU, network and
   memory for 7 days. Mitigation: real workloads (API, database, workers). If
   that is not enough, upgrade the tenancy to Pay As You Go, where
   reclamation does not apply, while keeping the usage inside the free limits.

## Consequences

- Tradvia gets an always-on host at no cost, with room for the API and the
  first session workers.
- The founder owns the OCI account: sign-up, card verification, 2FA and the
  choice of home region (it cannot be changed later).
- Capacity for A1 instances is sometimes unavailable in busy regions. Retrying
  later or choosing another availability domain is part of the setup guide.
- Free limits change without notice, as Oracle did in June 2026 (A1 went from
  4 OCPU / 24 GB to 2 OCPU / 12 GB). The setup stays small enough to fit and
  is documented so it can move elsewhere.
