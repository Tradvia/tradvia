<!-- tradvia-translation source="docs/programmatic-api.md" source-sha256="ceb1692b75dc408960bd37f54ab8f0c103f9897745054fc5b1e3c2a7f68ece0a" -->
# API programática

Crea e inicia cards sin el panel web, desde scripts, CI o la CLI `tradvia`.
Un card creado así es una issue normal de GitHub: sigue pasando por el planner
y una persona sigue aprobando el plan. Relacionado: [#76](https://github.com/Tradvia/tradvia/issues/76),
diseño en la [ADR 0009](adr/0009-programmatic-activity-api.md).

| Funcionalidad | Estado |
| --- | --- |
| Tokens de API (crear, revocar, vencimiento, un proyecto y un rol cada uno) | Shipped |
| Crear un card con un token | Shipped |
| Iniciar un card (`agent:ready`) con un token | Shipped |
| CLI `tradvia token` y `tradvia card` | Shipped |
| Aprobar planes, hacer merge o deploy con un token | No disponible (solo desde el panel) |
| Herramientas de agente por MCP: leer cards, crear, iniciar, ajustar un plan ([mcp.md](mcp.md)) | In progress (pruebas offline) |
| Tarea rápida sin plan ni aprobación | Planned |

Requiere que el inicio de sesión con GitHub esté configurado ([github-app.md](github-app.md)).

## Cómo se comporta un token

- Actúa como su dueño, con la identidad de GitHub del dueño. Está limitado a un proyecto
  y al rol elegido al crearlo, y nunca supera el rol actual del dueño.
- Vence (por defecto 30 días, como máximo 90) y se puede revocar en cualquier momento.
- Deja de funcionar cuando vence el token de inicio de sesión de GitHub del dueño; vuelve a iniciar sesión y
  crea uno nuevo.
- Solo se guarda un hash. El token se muestra una sola vez.
- Las acciones aparecen en la auditoría del card (`GET /api/projects/{owner}/{repo}/cards/{n}/audit`)
  con el dueño como autor y `via: "api_token"`.

## curl

Crea un token con la sesión iniciada (cookie de sesión del navegador y el valor CSRF de
`/api/auth/me`):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/tokens \
  -H "Cookie: __Host-tradvia_session=$SESSION" \
  -H "Origin: https://app.tradvia.org" -H "X-Tradvia-CSRF: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"role": "triage", "expires_in_days": 30}'
```

Crea e inicia un card con el token (sin encabezado Origin ni CSRF):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards \
  -H "Authorization: Bearer $TRADVIA_TOKEN" \
  -H "X-Tradvia-Idempotency-Key: $(uuidgen | tr -d -)" \
  -H "Content-Type: application/json" \
  -d '{"title": "feat(core): ...", "description": "..."}'

curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards/123/start \
  -H "Authorization: Bearer $TRADVIA_TOKEN"
```

Revocar: `DELETE /api/projects/{owner}/{repo}/tokens/{id}` con los encabezados de la sesión.

Errores: 401 token desconocido, vencido o revocado; 403 rol insuficiente, otro proyecto o una
acción que los tokens no pueden hacer.

## CLI

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # valor de la cookie __Host-tradvia_session (solo para gestionar tokens)

tradvia token create --project Tradvia/tradvia --role triage --days 30   # muestra el token una sola vez
tradvia token revoke --project Tradvia/tradvia 3

export TRADVIA_TOKEN=tvt_...
tradvia card create --project Tradvia/tradvia --title "feat(core): ..." --body "..." [--start]
```
