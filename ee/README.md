# Tradvia Enterprise Edition

Code in this directory is **not** covered by the core license. It is
proprietary and governed by [`ee/LICENSE.md`](LICENSE.md). Production use
requires a paid subscription.

Every source file in this directory starts with:

```
SPDX-License-Identifier: LicenseRef-Tradvia-Commercial
Copyright (c) 2026 Anderson dos Santos Dutra
```

## Modules

| Module | Path | Status |
|---|---|---|
| Single sign-on (SAML 2.0 / OIDC) | `auth/sso/` | Planned |
| WhatsApp Business API | `integrations/whatsapp/` | Planned |
| Microsoft Teams | `integrations/teams/` | Planned |
| Message privacy filter (WhatsApp / e-mail) | `privacy/` | Planned |
| CTO ROI dashboard (hours saved vs. token spend) | `analytics/roi/` | Planned |

## Rules

- EE modules depend on `core/` only through its public extension points.
- Every EE feature is gated by the license key check; it must stay off when no
  valid key is present.
- Changes here require review from `@Tradvia/ee-maintainers` (see `CODEOWNERS`).
