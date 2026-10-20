# Deploying Tradvia on OCI Always Free

Decision and rules: [ADR 0005](adr/0005-hosting-on-oci-always-free.md).
Result: `https://app.tradvia.org/runs/` served from an always-on OCI instance,
deployed by GitHub Actions after a maintainer approves.

Steps marked **(owner)** need the account owner: they involve sign-in, payment
verification or secrets, and cannot be done by an agent.

## 1. Create the OCI account (owner)

1. Sign up at <https://signup.cloud.oracle.com>. A card is used only for
   identity verification.
2. **Choose the home region carefully: it cannot be changed**, and Always Free
   compute only runs there. For users in Brazil: `Brazil East (São Paulo)` or
   `Brazil Southeast (Vinhedo)`.
3. Enable MFA on the account.

## 2. Create the instance (owner)

Console: **Compute > Instances > Create instance**.

| Field | Value |
|---|---|
| Name | `tradvia-host` |
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.E2.1.Micro` (Always Free, x86, 1 GB RAM). If available, `VM.Standard.A1.Flex` up to 2 OCPU / 12 GB is better |
| Networking | New VCN with a public subnet, assign a public IPv4 address |
| SSH keys | Upload **your own** public key (keep the private key on your computer) |
| Boot volume | 50 GB (default) |
| Initialization script | Paste the content of [`infra/oci/cloud-init.yaml`](../infra/oci/cloud-init.yaml) |

A1.Flex often returns "Out of capacity" in regions with a single availability
domain (São Paulo did on 2026-10-19, even at 1 OCPU / 6 GB). E2.1.Micro usually
has capacity. The cloud-init works on both and adds 2 GB of swap.

Then open HTTP/HTTPS in the subnet: **Networking > Virtual cloud networks >
(your VCN) > Security Lists > Default** → add two ingress rules, source
`0.0.0.0/0`, TCP, destination ports `80` and `443`.

## 3. Point the domain (owner)

At the DNS provider of `tradvia.org`, create an `A` record:
`app` → the instance's public IP. Keep `tradvia.org` itself where it is.

## 4. Deploy key and secrets (owner)

On your computer (not on the instance):

```bash
ssh-keygen -t ed25519 -f tradvia-deploy -N "" -C "github-actions deploy"
```

Add `tradvia-deploy.pub` to the instance (`~/.ssh/authorized_keys` of user
`ubuntu`), using your own SSH session. Get the host key for pinning:

```bash
ssh-keyscan -t ed25519 <public-ip>
```

In GitHub: **Settings > Environments > New environment `production`**, add
yourself as a **required reviewer**, then add:

| Kind | Name | Value |
|---|---|---|
| Secret | `OCI_HOST` | the public IP |
| Secret | `OCI_SSH_KEY` | content of `tradvia-deploy` (private key) |
| Secret | `OCI_KNOWN_HOSTS` | the `ssh-keyscan` line |
| Secret | `POSTGRES_PASSWORD` | a long random password |
| Variable | `TRADVIA_DOMAIN` | `app.tradvia.org` |
| Variable | `ACME_EMAIL` | e-mail for the HTTPS certificate notices |

Delete the local `tradvia-deploy` private key after saving it as a secret.

## 5. Deploy

**Actions > deploy-oci > Run workflow**, then approve the `production`
deployment. The workflow uploads `infra/oci/` and `dashboard/`, starts the
services and checks `https://app.tradvia.org/healthz`.

## What runs where

| Service | Exposure | Notes |
|---|---|---|
| Caddy | ports 80/443 | Automatic HTTPS, security headers |
| Dashboard | `/runs/` via Caddy | Static files from `dashboard/` |
| PostgreSQL 16 | internal Docker network only | Webhook storage (API slice 2) |
| API | `/api/*` via Caddy, no published port | Read-only GitHub proxy with cache |

## API

The `api` service (`core/src/tradvia/api.py`, Starlette + uvicorn, 256 MB limit)
removes the 60 requests/hour anonymous limit per visitor IP that the dashboard
hits when it calls GitHub from the browser. On `app.tradvia.org` the dashboard
detects `/api/healthz` and uses `/api/github/...`; elsewhere it still calls
GitHub directly.

- `GET /api/healthz` returns `{"ok": true}`.
- `GET /api/github/{path}` forwards to `https://api.github.com/{path}` only for
  `TRADVIA_REPO` (default `Tradvia/tradvia`): `issues`, `issues/{n}/timeline`,
  `issues/{n}/comments`, `pulls`, `actions/runs` and the `agent.yml` and
  `claude.yml` workflow runs. Everything else is 404. Only `state`, `per_page`,
  `page`, `labels`, `sort` and `direction` are forwarded.
- Cache: in memory, per URL, 60 s TTL (`TRADVIA_API_CACHE_TTL`), 500 entries,
  revalidated with `ETag`. If GitHub fails, an expired entry is served with
  `X-Tradvia-Stale: 1`.

Optional secret `TRADVIA_GITHUB_TOKEN`: a fine-grained token with read-only
access to Issues, Pull requests and Actions on `Tradvia/tradvia`. A maintainer
wires it into the deploy workflow as `GITHUB_TOKEN` in `.env`. Without it the
API works at the anonymous GitHub limit. The token is never returned or logged.

| Capability | Status |
|---|---|
| Read-only GitHub proxy with cache | Shipped |
| GitHub webhooks stored in Postgres | Shipped |
| Dashboard reading issues, PRs and runs from the database | Shipped |
| Card timeline from the database | Planned |
| GitHub login | Planned |
| Actions from the dashboard | Planned |

## Webhooks

GitHub notifies the API of every change and the API stores it in the Postgres
that already runs on the host (tables are created on first connection from
`core/src/tradvia/schema.sql`). The dashboard reads issues, PRs and runs from it
(see "Dashboard reads" below); only a card's timeline still goes to GitHub.

Owner step, in the repository: **Settings > Webhooks > Add webhook**.

- Payload URL: `https://app.tradvia.org/api/webhooks/github`
- Content type: `application/json`
- Secret: the same value as the `TRADVIA_WEBHOOK_SECRET` secret of the
  `production` environment
- Events (select individually): **Issues**, **Issue comments**,
  **Pull requests** and **Workflow runs**

Behavior of `POST /api/webhooks/github`:

- No secret configured, or a missing or invalid `X-Hub-Signature-256`: 401,
  nothing is stored. Bodies over 1 MB: 413.
- Other repositories, unhandled events and `ping`: 202, ignored.
- Idempotent by `X-GitHub-Delivery`: a repeated delivery is acknowledged (202)
  and not stored twice. A record is only replaced by one with a newer
  `updated_at`.
- Database down: 503. Unexpected error: 500 (the delivery is rolled back as a
  whole). In both cases redeliver from **Settings > Webhooks > Recent
  Deliveries > Redeliver**; it is processed normally.
- `GET /api/store/health` returns `{"ok": true, "issues": n, "runs": n,
  "last_delivery": "..."}` (503 if the database is down). It never returns
  comment bodies.

Initial load (safe to repeat; everything is an upsert), on the host:

```bash
docker compose exec api tradvia backfill
```

It reads issues labelled `agent:ready`, their comments, pull requests and the
runs of `agent.yml` and `claude.yml`. `GITHUB_TOKEN` is optional (it raises the
GitHub rate limit). On the 1 GB host the database is capped at 256 MB
(`shared_buffers=64MB`, `max_connections=20`) and the API uses at most 3
connections.

**After deploying the slice that adds the dashboard reads, run the backfill
again.** The schema gains columns (`html_url`, `state_reason`, `name`,
`display_title`, `run_attempt`) that rows stored earlier do not have; the backfill
refills them.

## Dashboard reads

`GET /api/store/repos/{repo}/...` returns the same JSON as GitHub for the fields
the dashboard uses, from Postgres. Only `TRADVIA_REPO` and these paths exist (404
otherwise): `issues` (`labels`, `state`), `pulls` (`state`) and
`actions/workflows/{agent|claude}.yml/runs`. `per_page` is capped at 100 and
results are newest first. Comment bodies are never exposed. If the database is
down or has no data for the resource yet, the answer is 503 and the dashboard
retries that call through `/api/github/...`. On `app.tradvia.org` the dashboard
uses the database only when `/api/store/health` reports `issues > 0`, and shows
"dados: Tradvia" or "dados: GitHub" in the header.

## Idle reclamation

Oracle may reclaim an Always Free instance that stays under 20% CPU, network
and memory for 7 days. The API and workers keep it in use. If needed, upgrade
the tenancy to Pay As You Go and keep resources inside the free limits: see
ADR 0005.

## Agent runner on the OCI host (owner)

The `agent` workflow (cards with the `agent:ready` label) can run on the OCI host
instead of GitHub-hosted runners. `@claude` (`claude.yml`) and every other workflow
stay on GitHub: they can be triggered by anyone's comment, and code from outside
contributors must never run on our machine. The agent job only starts on
`issues: labeled`, which needs triage access.

Status: **In progress**. The host has 1 GB of RAM, so cards run one at a time and
slower than on GitHub; the runner is capped by systemd (`MemoryHigh=600M`,
`MemoryMax=900M`) so a heavy card cannot take down Caddy, the API or Postgres.
It moves to a larger host when capacity allows (ADR 0005).

1. On GitHub: **Settings → Actions → Runners → New self-hosted runner** and copy
   only the **token** shown in the `./config.sh --token ...` line (valid for one hour).
   Do not paste it anywhere else.
2. On the host:
   ```bash
   sudo bash /opt/tradvia/src/infra/oci/runner-setup.sh
   ```
   Paste the token when asked (input is hidden). The script creates the
   `gha-runner` user (no sudo, no Docker access), registers the runner with the
   label `tradvia-oci` and starts it as a service with the memory limits.
3. Check that the runner shows as **Idle** in **Settings → Actions → Runners**.
4. Switch the workflow: **Settings → Secrets and variables → Actions → Variables →
   New repository variable** `TRADVIA_AGENT_RUNS_ON` = `["self-hosted","tradvia-oci"]`.
   To roll back to GitHub-hosted runners, delete the variable.

Useful commands on the host:

```bash
systemctl status 'actions.runner.*'             # service state
journalctl -u 'actions.runner.*' -n 100 --no-pager
free -m; systemd-cgtop -1 --depth=2 | head -20  # memory use
```
