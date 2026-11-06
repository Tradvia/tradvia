# ADR 0022: Session driver interface (hosted browser first)

- Status: Accepted
- Date: 2026-11-03
- Amended by: [ADR 0024](0024-browser-sessions-on-our-own-host.md) (the first driver runs on our own host)

## Context

Some sessions are more than API calls: a browser the agent drives, a terminal in an
isolated container. They need one interface, like the coding agent adapters of ADR 0010.

The current host (VM.Standard.E2.1.Micro, 1 GB, ADR 0005) has no KVM. Oracle's nested
virtualization is documented only for paid shapes, and Kata Containers also requires KVM.
gVisor (Systrap platform) runs without KVM.

## Decision

- **Interface**: `SessionDriver`, in the same pattern as the coding agent adapters of
  ADR 0010: one class per driver and one entry in the catalog.
- **Driver order** (all Planned, none is available today):
  1. hosted browser with live view (Browserbase or Cloudflare Browser Run);
  2. gVisor, on a larger host;
  3. Firecracker, last.
- **2FA and CAPTCHA** are solved by a person through the takeover. The agent does not
  bypass them (base rule of the README).
- **Credentials** live only in the authorized vaults: the project connections (ADR 0019).
  They are never put in card text, prompts or logs.
- **Cost**: session minutes count toward the card cost and its budget (ADR 0006).

## Consequences

Adding a driver is one class and one catalog entry. The first driver depends on an
external service. gVisor and Firecracker depend on a larger host than the current one.
