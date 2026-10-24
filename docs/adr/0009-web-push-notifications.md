# ADR 0009: Web Push with VAPID and a minimal payload

- Status: Accepted
- Date: 2026-10-24

## Context

The panel is the single interaction surface (ADR 0007), but a card waits for a
human whether or not the panel is open. People need a warning when a plan, a
question, a pull request or a limit needs them. The warning must not need a paid
service, and it must not leak the content of private repositories to third
parties.

## Decision

- **Pending inbox first.** `pending.py` derives, from the data the webhook already
  stores, which cards wait for a human (`plan_ready`, `agent_question`, `pr_opened`,
  `budget_limit`, `adjust_limit`) and which policy interaction answers each
  (`approve_plan`, `merge_pr`, `budget_control`). The inbox lists, per project, only
  the cards the signed-in person's role may answer.
- **Web Push with VAPID** (RFC 8030, 8291, 8292), which browsers and phones
  support without an account on any paid service. The server holds one key pair
  (`TRADVIA_VAPID_PUBLIC_KEY`, `TRADVIA_VAPID_PRIVATE_KEY`). Push is off when either
  is unset. The private key stays in the server `.env` and is never logged or
  returned.
- **Library**: `pywebpush` (added to the `api` and `dev` extras) does the VAPID
  signing and the payload encryption, which is easy to get wrong by hand.
- **Minimal payload**: only the project slug, the card number and the kind of event.
  No title, comment, plan or branch, because the message passes through the
  browser vendor's push service. The service worker builds the text from fixed
  labels; the details are behind the panel's login and role checks.
- **Who gets it**: after a webhook delivery is applied (not for duplicates), the
  cards that entered a pending kind in that delivery are pushed to the project's
  subscribers whose cached role (from their live session) can answer that kind and
  who did not turn that event off. Without a live session or a role there is no push
  (fail closed).
- **Storage**: `push_subscriptions` (per user and project; one subscription per user
  and project) and `notify_prefs` (per user and project, which kinds). A 404 or 410
  from the push service deletes the subscription.
- **Endpoint is a request target**, so a subscription is accepted only for an `https`
  URL on a public host name, with a bounded size.
- **Failures are isolated**: a push error never changes the webhook's 202.

## Consequences

- Needs HTTPS, a service worker (`dashboard/sw.js`) and a browser with Push support.
- One new dependency, with a stated reason (above).
- Slack, WhatsApp and e-mail are not part of this decision; they stay Planned
  (docs/notifications.md).
