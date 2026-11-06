# ADR 0024: Browser sessions on our own host

- Status: Accepted
- Date: 2026-11-06
- Amends: [ADR 0022](0022-session-driver-interface.md) (driver order)

## Context

ADR 0022 put a hosted browser service (Browserbase or Cloudflare Browser Run) first. The
owner compared them on 2026-11-05 and preferred not to depend on a third-party service.
The owner already runs an AWS instance with 16 GB of RAM and Docker (r6i.large), which has
room for a browser; the OCI host of ADR 0005 (1 GB) does not.

## Decision

- **First driver: our own browser service** on that AWS host. A container runs Chromium
  driven over CDP (Playwright), and streams the screen with Chrome's own screencast; a
  person's clicks and keys are sent back over the same channel. No third-party browser
  product is used, so there is no outside license to check (ADR 0023).
- **Network:** the service listens on HTTPS only for the OCI host's address (security
  group). Tradvia's API talks to it with a token and a pinned certificate; no public
  domain or public certificate is needed.
- **Takeover goes through Tradvia:** the panel shows the stream through the API, after the
  sign-in and the project's `live_takeover` role check. The card records only Tradvia's
  link, never the service's address, which would give control to whoever holds it.
- **Isolation:** one container per session, a user without sudo, no host files mounted, the
  container removed when the session closes. 2FA and CAPTCHA are still solved by a person.
- **Cost:** the host is a paid resource outside OCI Always Free (ADR 0005, rule 3): about
  US$ 90 a month on demand for an r6i.large running all the time. The session's cost, when
  known, counts toward the card's cost and budget (ADR 0006); a cost the driver does not
  report is marked, never invented.
- Hosted services stay possible later as other drivers of the same interface.

## Status

| Part | Status |
|---|---|
| `SessionDriver` interface, fake driver, takeover link on the card, close at 100% of the budget (`core/src/tradvia/sessions.py`) | In progress (offline tests) |
| Browser service container on the AWS host | Planned |
| Takeover view in the panel (stream, clicks and keys) | Planned |
| Session cost on the card (#121) | Planned |

## Consequences

- The owner prepares the host when the container exists: the deploy key, the security
  group rule and the token, never through the chat.
- The host must be reachable and on when a card uses a browser; a stopped host fails the
  session with a clear message on the card, and the card waits for a person.
