<!-- tradvia-translation source="docs/adr/0009-programmatic-activity-api.md" source-sha256="2a8ad7e4c391d3ab084fd0b289e388df174b0179e529640eb752e4bfe466d1cf" -->
# ADR 0009: API programática para actividades (tokens de API)

- Estado: Aceptada
- Fecha: 2026-10-24
- Issue: #77 (documentada en [#76](https://github.com/Tradvia/tradvia/issues/76))

## Contexto

Toda actividad en Tradvia empieza como una issue de GitHub, pasa por el planner y
se ejecuta después de que una persona aprueba el plan. La issue #77 pide una segunda vía de entrada, con dos
direcciones posibles:

- **(a) Tarea rápida**: una tarea puntual sin el ciclo de plan/aprobación.
- **(b) Disparo programático**: una API/CLI para crear actividades sin el panel web.

## Decisión

Construir la **(b)**. La dirección (a) sigue en **Planned**.

- La (b) no agrega ninguna semántica de ejecución nueva: un card creado por la API es el mismo
  card, sigue pasando por el planner y por el punto de aprobación del plan, y todas
  las reglas de la ADR 0004 y la ADR 0008 siguen aplicándose.
- La (a) crearía un camino que se ejecuta sin plan y sin aprobación. Eso
  cambia el modelo de seguridad del plano de control (ADR 0003) y necesita una
  decisión propia sobre qué es una tarea "pequeña y bien definida", quién puede ejecutarla y con
  qué presupuesto. No se decide aquí.

### Tokens de API

- Un token lo crea una persona con sesión iniciada (cookie de sesión + CSRF, como en la ADR 0008) con
  `POST /api/projects/{owner}/{repo}/tokens` y se revoca con
  `DELETE /api/projects/{owner}/{repo}/tokens/{id}`. Los tokens bearer no pueden gestionar tokens.
- **Alcance**: un proyecto, un rol (`read`..`admin`, por defecto `triage`) y un
  vencimiento (por defecto 30 días, como máximo 90).
- **Un token nunca supera a su dueño**: la creación se rechaza si el rol está por encima
  del rol del dueño en el proyecto, y en cada solicitud el rol efectivo es el
  menor entre el rol del token y el rol *actual* del dueño en GitHub. Perder el rol
  en GitHub le quita el poder al token al instante.
- **Almacenamiento**: la tabla `api_tokens` guarda solo el SHA-256 del token
  (`auth.hash_token`), el rol, el proyecto, `expires_at` y `revoked_at`. El token
  en bruto (`tvt_` + 256 bits aleatorios) se muestra una sola vez, al crearlo.
- **Identidad**: la fila también guarda el token de usuario de GitHub del dueño, cifrado con la
  misma clave Fernet que las sesiones (ADR 0008). Por eso las acciones se ejecutan con la identidad
  de GitHub del dueño, nunca con el `GITHUB_TOKEN` del servidor. Una consecuencia es que un
  token deja de funcionar cuando el token de usuario de GitHub detrás de él vence o se revoca;
  el dueño entonces crea un token nuevo después de volver a iniciar sesión.
- **Solicitud**: `Authorization: Bearer <token>` solo se lee cuando no hay cookie
  de sesión. Una solicitud con cookie siempre sigue las reglas de la cookie, incluido el CSRF. La
  verificación de Origin/CSRF solo se omite en las solicitudes bearer, que no llevan credenciales
  automáticas. Los tokens desconocidos, vencidos o revocados responden 401.
- **Acciones permitidas**: solo `create_card` y `start_card` (y leer la auditoría del
  card). Aprobar planes, hacer merge, los deploys y los cambios en el backlog siguen siendo solo del panel.
  La verificación de rol existente (`policy.py`, `.tradvia.yml`) sigue decidiendo cada una.
- **Auditoría**: `audit_log` gana una columna `via`. Las entradas disparadas por token llevan al
  dueño como `actor_login` y `via = api_token`, así que aparecen en
  `GET .../cards/{n}/audit`. Crear y revocar tokens también se audita.
- **Idempotencia**: `X-Tradvia-Idempotency-Key` funciona como en el panel (mismo login,
  misma clave, 5 s).
- **CLI**: `tradvia token create|revoke` y `tradvia card create` llaman a esta API HTTP.

## Consecuencias

- Un token filtrado puede crear e iniciar cards en un proyecto hasta que venza o se
  revoque, pero no puede aprobar un plan, hacer merge ni deploy.
- Rotar `TRADVIA_SECRET_KEY` invalida los tokens, igual que las sesiones.
- Las tareas rápidas sin plan siguen en **Planned**; ver
  [docs/programmatic-api.md](../programmatic-api.md).
- Estado: Shipped (autenticación por token, creación e inicio de cards). Necesita la misma configuración de la App que
  la ADR 0008.
