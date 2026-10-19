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
| PostgreSQL 16 | internal Docker network only | For the API (next card) |
| API | Planned | GitHub data proxy with cache and GitHub sign-in (ADR 0004) |

## Idle reclamation

Oracle may reclaim an Always Free instance that stays under 20% CPU, network
and memory for 7 days. The API and workers keep it in use. If needed, upgrade
the tenancy to Pay As You Go and keep resources inside the free limits: see
ADR 0005.
