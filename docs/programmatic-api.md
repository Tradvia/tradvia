<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
# Programmatic API

Create and start cards without the web panel, from scripts, CI or the `tradvia` CLI.
A card created this way is a normal GitHub issue: it still goes through the planner
and a human still approves the plan. Related: [#76](https://github.com/Tradvia/tradvia/issues/76),
design in [ADR 0009](adr/0009-programmatic-activity-api.md).

| Capability | Status |
| --- | --- |
| API tokens (create, revoke, expiry, one project and role each) | Shipped |
| Create a card with a token | Shipped |
| Start a card (`agent:ready`) with a token | Shipped |
| `tradvia token` and `tradvia card` CLI | Shipped |
| Approve plans, merge or deploy with a token | Not available (panel only) |
| Quick task without a plan or approval | Planned |

It needs sign-in with GitHub to be configured ([github-app.md](github-app.md)).

## How a token behaves

- It acts as its owner, with the owner's GitHub identity. It is limited to one project
  and to the role chosen at creation, and never exceeds the owner's current role.
- It expires (default 30 days, at most 90) and can be revoked at any time.
- It stops working when the owner's GitHub sign-in token expires; sign in again and
  create a new one.
- Only a hash is stored. The token is shown once.
- Actions appear in the card audit (`GET /api/projects/{owner}/{repo}/cards/{n}/audit`)
  with the owner as actor and `via: "api_token"`.

## curl

Create a token while signed in (browser session cookie and CSRF value from
`/api/auth/me`):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/tokens \
  -H "Cookie: __Host-tradvia_session=$SESSION" \
  -H "Origin: https://app.tradvia.org" -H "X-Tradvia-CSRF: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"role": "triage", "expires_in_days": 30}'
```

Create and start a card with the token (no Origin or CSRF header needed):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards \
  -H "Authorization: Bearer $TRADVIA_TOKEN" \
  -H "X-Tradvia-Idempotency-Key: $(uuidgen | tr -d -)" \
  -H "Content-Type: application/json" \
  -d '{"title": "feat(core): ...", "description": "..."}'

curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards/123/start \
  -H "Authorization: Bearer $TRADVIA_TOKEN"
```

Revoke: `DELETE /api/projects/{owner}/{repo}/tokens/{id}` with the session headers.

Errors: 401 unknown, expired or revoked token; 403 role too low, other project, or an
action tokens cannot do.

## CLI

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # value of the __Host-tradvia_session cookie (only to manage tokens)

tradvia token create --project Tradvia/tradvia --role triage --days 30   # prints the token once
tradvia token revoke --project Tradvia/tradvia 3

export TRADVIA_TOKEN=tvt_...
tradvia card create --project Tradvia/tradvia --title "feat(core): ..." --body "..." [--start]
```
