<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
# Notifications: pending inbox and Web Push

When a card needs a person, Tradvia shows it in a **pending inbox** in the panel and,
if the person turned it on, sends a **Web Push** notification to their browser or phone.
Decision: [ADR 0009](adr/0009-web-push-notifications.md).

| Capability | Status |
| --- | --- |
| Pending inbox (list per project, counter in the header, link to the card) | Shipped |
| Web Push (VAPID, per user and project, event preferences) | Shipped |
| Slack | Planned |
| WhatsApp | Planned |
| E-mail | Planned |

Both depend on signing in with GitHub ([ADR 0008](adr/0008-sessions-csrf-and-project-roles.md))
and on the panel actions ([ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md)).
The tests run offline with a fake sender; delivery to a real push service needs the keys below.

## What counts as pending

A card is pending for someone when its next step is a human decision, and that person's
role (read from `.tradvia.yml`, [ADR 0004](adr/0004-access-follows-project-configurable-interactions.md))
is enough to answer it:

| Event (`kind`) | What happened | Interaction that answers it (default role) |
| --- | --- | --- |
| `plan_ready` | The agent posted a plan and waits for "Aprovado", "Rejeitado" or "Ajuste" | `approve_plan` (write) |
| `agent_question` | The agent asked a question (`❓`) and waits | `approve_plan` (write) |
| `adjust_limit` | The adjustment limit was reached; the card needs a human | `approve_plan` (write) |
| `pr_opened` | The agent opened a pull request that is still open | `merge_pr` (maintain) |
| `budget_limit` | The budget limit stopped the card | `budget_control` (admin) |

A person without a role in the project, or whose role is below the interaction, sees nothing
for that kind and gets no push for it. A card leaves the inbox when someone answers it
(a comment after the agent's, or the PR is closed) or the issue is closed.

## The inbox

Signed in, the header shows a bell with the number of cards waiting for you across all
projects. Click it to see them grouped by project; each row links to the card
(`?repo=owner/repo&card=N`), which opens with the reply, approve or merge controls.

API: `GET /api/projects/{owner}/{repo}/pending` returns `{count, items}` for the signed-in
person; `/api/auth/me` carries `pending_count` per project.

## Turning on Web Push

1. **Generate the VAPID keys once** (two base64url strings):

   ```bash
   npx web-push generate-vapid-keys
   ```

   Without Node: `pip install py-vapid`, then `vapid --gen` and
   `vapid --applicationServerKey` (prints the public key; the private one is in
   `private_key.pem`, whose path the sender accepts too).

2. **Set the variables** in the server `.env` (`core/.env.example` lists them):

   ```
   TRADVIA_VAPID_PUBLIC_KEY=...
   TRADVIA_VAPID_PRIVATE_KEY=...
   TRADVIA_VAPID_SUBJECT=mailto:you@example.org   # optional; default https://$TRADVIA_DOMAIN
   ```

   The private key lives **only** in the server's `.env`: it is never sent to the browser,
   logged or returned by the API. Push is off if either key is unset, and the panel then
   hides the notification controls. Restart the API after changing them.

3. **Enable it in the panel**: sign in, open the bell, press **Enable notifications** and
   accept the browser prompt. The panel registers `sw.js` (service worker), subscribes with
   the public key and saves the subscription for you and the current project. Do it in each
   project (and each browser or phone) you want to be warned about. It needs HTTPS and a
   browser with Push support; on iOS, add the panel to the Home Screen first.

4. **Choose the events** with the checkboxes under the button; they are saved per user and
   project. By default every event is on. **Disable notifications** removes the subscription.

A push is sent when a GitHub webhook records an event that moves a card into one of the
kinds above, to the subscribers of that project whose role can answer it and who kept that
event on. Redelivered webhooks do not notify twice. Subscriptions the push service reports as
gone (HTTP 404 or 410) are deleted. A push failure never changes the webhook's answer.
Sending uses the subscriber's cached role (from their live session), so someone whose session
expired gets no push until they sign in again.

## What the notification contains

Only the **project**, the **card number** and the **kind of event**, for example
"tradvia-tradvia · #47 · plan ready to approve". The message never carries a card title,
comment, plan text, branch or any other content from GitHub: the service worker builds the
text from those three values and fixed labels (Portuguese or English by browser language).
The push service (Google, Mozilla, Apple) sees the encrypted payload and the endpoint only.
Clicking opens the card in the panel, where the usual sign in and role checks apply.

The server only sends to `https` endpoints on public host names, since the subscription's
endpoint is a request target.
