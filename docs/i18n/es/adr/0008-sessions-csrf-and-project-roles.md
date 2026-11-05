<!-- tradvia-translation source="docs/adr/0008-sessions-csrf-and-project-roles.md" source-sha256="07986d272fca603782d47db1a1adfd7cfeaa54b93171e8007ee6cc32ec727cab" -->
# ADR 0008: Sesiones, CSRF y roles por proyecto para el inicio de sesión con GitHub

- Estado: Aceptada
- Fecha: 2026-10-21

## Contexto

La ADR 0004 (fase 2) dice que el panel necesita un inicio de sesión antes de que alguien pueda actuar
a través de él, y la ADR 0006 hace del proyecto la unidad de alcance. El panel en
`app.tradvia.org` sigue siendo público y de solo lectura sin inicio de sesión.

## Decisión

- **Identidad**: una GitHub App de Tradvia usada solo para el inicio de sesión (flujo OAuth
  user-to-server, `state` aleatorio, PKCE S256). Sin webhook, permisos mínimos. El `state`
  es de un solo uso, vence en 10 minutos y además queda ligado al navegador que
  inició el inicio de sesión mediante una cookie `__Host-` de vida corta, así que no se puede forzar un inicio de sesión
  en otra persona.
- **Sesión**: una fila en la tabla `sessions` de Postgres. La cookie guarda 256 bits aleatorios;
  la tabla guarda solo su SHA-256. Cookie `__Host-tradvia_session` (`Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`, sin `Domain`). 8 h deslizantes, 7 días
  absolutos. Una cookie falsificada, vencida o desconocida se trata como anónima.
- **Token**: el token de usuario de GitHub se cifra con Fernet (clave derivada con
  HKDF de `TRADVIA_SECRET_KEY`), nunca se envía al navegador y nunca se registra en logs. Esto
  agrega la dependencia `cryptography`.
- **CSRF**: las solicitudes que cambian el estado y llevan una cookie de sesión necesitan `Origin`
  (o `Referer`) igual a `TRADVIA_DOMAIN` y `X-Tradvia-CSRF` igual a
  HMAC(clave, cookie de sesión). Los webhooks quedan fuera: tienen su propia
  firma. Las lecturas siguen siendo públicas.
- **Rol**: `role_of(user, project)` pide a GitHub el permiso de colaborador
  con el token de la persona, lo convierte con `policy.role_from_github` en los roles de
  `policy.py` y lo guarda en caché 5 minutos por sesión. La lectura de la política de
  `.tradvia.yml` en el panel llega con la primera acción.
- **Ruta de retorno**: `next` solo se acepta como `/runs/...`, validado antes de
  guardarse con el `state`; el callback lo lee de la base de datos.
- **Apagado por defecto**: sin `TRADVIA_GITHUB_APP_CLIENT_ID`,
  `TRADVIA_GITHUB_APP_CLIENT_SECRET` y `TRADVIA_SECRET_KEY` las rutas responden 404
  y `/api/auth/me` responde `{"enabled": false}`.

## Anexo: acciones del panel (fase 2 de la ADR 0004)

- **Política leída con el token de la persona**: `.tradvia.yml` se lee del branch
  por defecto con el token de la persona con sesión (necesita el permiso de la App **Contents:
  Read-only**). Un 404 significa los valores por defecto de la ADR 0004, como en el agente. Un 401/403 o un
  archivo inválido rechaza la acción (**falla cerrada**); el mensaje nombra el permiso
  que falta. Las lecturas exitosas se reutilizan durante 60 s solo para decidir qué botones
  mostrar; cada acción vuelve a leer el archivo.
- **Identidad**: crear un card, agregar `agent:ready` y comentar usan el token de la
  persona, nunca el `GITHUB_TOKEN` del servidor.
- **Registro de auditoría**: tabla `audit_log` (proyecto, autor, acción, card, resultado, detalle,
  clave de idempotencia, hora). Cada intento se registra, haya salido bien
  (`ok`), haya sido rechazado (`denied`) o haya fallado (`failed`). Solo se lee mediante
  `GET .../cards/{n}/audit` y `GET .../audit`, que requieren sesión y al menos
  el rol `read` en el proyecto y se envían con `Cache-Control: no-store`; nunca
  es público. La ruta del proyecto pagina por período (`from` inclusive, `to` exclusive,
  `limit` hasta 1000, `offset`) y exporta como JSON, CSV o JSON Lines
  (`format=json|csv|jsonl`). Cada valor de texto pasa por `redact`, así que ninguna forma de clave o
  token sale del panel, y las celdas CSV que empiezan con `=`, `+`, `-` o `@`
  reciben una comilla inicial contra la inyección de fórmulas.
- **Idempotencia**: el panel envía `X-Tradvia-Idempotency-Key` en cada acción. Una
  repetición de la misma clave por la misma persona dentro de 5 s después de una acción exitosa (o
  mientras todavía corre) se ignora y responde `{"ok": true, "duplicate": true}`.

## Consecuencias

- Rotar `TRADVIA_SECRET_KEY` cierra la sesión de todos (y, según la ADR 0019, vuelve
  ilegibles los secretos de proveedores guardados).
- Cada `/api/auth/me` puede costar una llamada a GitHub por proyecto cada 5 minutos.
- Estado: Partial. Código y pruebas entregados; producción necesita que el dueño registre
  la App ([docs/github-app.md](../github-app.md)) y que un mantenedor conecte los secretos.
