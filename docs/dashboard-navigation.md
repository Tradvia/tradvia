<!-- SPDX-License-Identifier: FSL-1.1-ALv2 -->
# Dashboard navigation

The dashboard (`dashboard/index.html`) has a sticky top bar with the main sections.
The section is selected by the URL hash (`#settings`, `#help`); anything else shows
the panel.

## Sections

| Section | Hash | Status |
|---|---|---|
| Painel / Dashboard | `#panel` | **Shipped** |
| Conexões / Connections (#58) | — | **Planned**: shown as disabled with a "soon" badge |
| Configurações / Settings | `#settings` | **Partial**: roles are read-only; notifications are Planned (#47) |
| Ajuda / Help | `#help` | **Shipped**: links to README, dashboard KPIs, projects and ADRs |

Auto-refresh only runs while the Painel view is visible.

## Items per role

| Session | Painel | Conexões | Configurações | Ajuda |
|---|---|---|---|---|
| No session (read-only) | yes | soon | language and theme only | yes |
| Signed in, any role (`read`, `write`, `admin`, or none) | yes | soon | roles view, notifications placeholder, language and theme | yes |

The server still checks the role on every action; the menu only hides what you cannot use.

## Settings

- **Roles and permissions**: shows your role in the current repository and whether you
  can start cards and approve plans. Read-only: roles and permissions are edited in
  `.tradvia.yml` (see [`tradvia.example.yml`](tradvia.example.yml) and
  [`projects.md`](projects.md)).
- **Notifications**: Planned (#47), shown as "soon".
- **Preferences**: language (PT-BR/EN) and theme (dark/light/system).

## Preference keys

Stored in `localStorage`; the dashboard works with defaults when storage is unavailable
or corrupt.

| Key | Value |
|---|---|
| `tradvia.prefs` | JSON `{"lang": "pt" \| "en", "theme": "dark" \| "light" \| "system"}`. Default language follows the browser, default theme is dark. |
| `tradvia.autoRefresh` | `"on"` or `"off"` |

The theme is applied through `<html data-theme>`; `system` follows `prefers-color-scheme`.
