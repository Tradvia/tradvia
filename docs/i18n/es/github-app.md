<!-- tradvia-translation source="docs/github-app.md" source-sha256="54395dc5d9cabfad568c67f9718861b9e9b3f3652076f4cbdaa725001129103b" -->
# Iniciar sesión con GitHub: registrar la GitHub App

Estado: **Partial.** Las rutas de la API, las sesiones, las verificaciones de CSRF y el botón del panel
están implementados y probados offline. El inicio de sesión solo funciona en producción después de que el
dueño complete los pasos de abajo y un mantenedor conecte los secretos al workflow de
deploy. Las acciones del panel (crear, iniciar y reordenar cards, aprobar
el plan) están **Partial**: implementadas y probadas offline, y solo funcionan en
producción con el inicio de sesión conectado y los permisos de abajo.

Revisar y hacer merge de PR, aprobar deploys y la operación posterior al deploy
(ADR 0007, #56) están **Partial**: probados offline, y funcionan en producción
cuando la App tenga los permisos de abajo y las instalaciones los acepten.

La GitHub App de Tradvia es sobre todo un **proveedor de identidad**: prueba quién es la
persona y, con su propio token, qué rol tiene en cada proyecto.
No tiene webhook. Las acciones del panel se ejecutan en GitHub con el **propio token de la persona**, así
el historial de GitHub muestra quién actuó; el `GITHUB_TOKEN` del servidor nunca se usa
para ellas. Diseño: ADR 0008.

## Pasos del dueño

1. GitHub: **Settings > Developer settings > GitHub Apps > New GitHub App**
   (en la organización Tradvia, si la App debe pertenecerle).
2. Completa:
   - **GitHub App name**: `Tradvia`
   - **Homepage URL**: `https://tradvia.org`
   - **Callback URL**: `https://app.tradvia.org/api/auth/callback`
   - **Request user authorization (OAuth) during installation**: desactivado
   - **Expire user authorization tokens**: a tu elección (la sesión dura como máximo
     7 días; Tradvia todavía no usa refresh tokens)
   - **Webhook > Active**: **desactivado** (sin webhook)
3. Permisos (de repositorio), el mínimo:
   - **Issues**: Read and write. Crear cards, agregar `agent:ready`, comentar.
   - **Pull requests**: Read and write. Leer el PR, sus archivos y los comentarios
     de revisión; `PUT /pulls/{n}/update-branch`.
   - **Contents**: Read and write. Leer `.tradvia.yml` del branch por defecto
     con el token de la persona, que define el rol mínimo de cada acción del panel
     (ADR 0004); sin él, toda acción del panel se rechaza (falla cerrado) con un
     mensaje que nombra este permiso. Escritura: `PUT /pulls/{n}/merge` (squash) y
     `DELETE /git/refs/heads/{branch}` (borrar el branch después del merge).
   - **Actions**: Read and write. `GET /actions/runs` (deploys en espera),
     `POST /actions/runs/{id}/cancel` (deploys reemplazados),
     `POST /actions/runs/{id}/approve` (workflows de un PR que GitHub retiene, ver la
     última sección de esta página) y
     `POST /actions/workflows/deploy-oci.yml/dispatches` (operación posterior al deploy).
   - **Deployments**: Read and write. `GET` y
     `POST /actions/runs/{id}/pending_deployments` (aprobar o rechazar; la
     persona también debe ser revisora obligatoria del entorno).
   - **Secrets**: Read and write. Conexiones del proyecto (ADR 0019): Tradvia lee
     la clave pública del repositorio y escribe o borra los secretos de Actions `ANTHROPIC_API_KEY`,
     `GEMINI_API_KEY`, `OPENAI_API_KEY` y `CLAUDE_CODE_OAUTH_TOKEN`
     cuando un admin guarda o quita un secreto de proveedor. Sin él, el
     secreto sigue guardado en Tradvia y la pantalla dice que no se pudo escribir
     en GitHub Actions.
   - **Checks**: Read-only. `GET /commits/{sha}/check-runs`: los checks `core-tests`,
     `license-boundary` y `cla` en la pantalla del PR y antes de un merge.
   - **Metadata**: Read-only (obligatorio)

   Nada más, y ningún permiso de cuenta. **Las instalaciones existentes deben aceptar
   los nuevos permisos:** cambiarlos en la página de la App no rige hasta que
   el dueño (y cada organización que la instaló) apruebe la solicitud en la
   configuración de la instalación. Hasta entonces, los botones afectados responden con un
   mensaje que nombra el permiso que falta. Una sesión abierta antes de la actualización conserva
   su token antiguo, así que la persona vuelve a iniciar sesión después de que el dueño la acepte.
4. **Where can this GitHub App be installed?** Any account, para que los colaboradores de
   otros proyectos registrados puedan iniciar sesión. Crea la App.
5. En la página de la App copia el **Client ID** y haz clic en **Generate a new client
   secret**; cópialo una vez.
6. Genera la clave que cifra los tokens de las personas en la base de datos:

   ```
   python -c 'import secrets;print(secrets.token_urlsafe(32))'
   ```

7. Entrega los tres valores a un mantenedor, que los agrega al entorno `production`
   y los conecta al workflow de deploy (pull request aparte):

   | Variable de `.env` | Valor |
   |---|---|
   | `TRADVIA_GITHUB_APP_CLIENT_ID` | Client ID |
   | `TRADVIA_GITHUB_APP_CLIENT_SECRET` | client secret |
   | `TRADVIA_SECRET_KEY` | la clave del paso 6 |

   Mantenlos fuera del repositorio, del chat y de los logs. Cambiar `TRADVIA_SECRET_KEY`
   cierra la sesión de todos (los tokens guardados ya no se pueden descifrar) y vuelve
   ilegibles los secretos de proveedores guardados en la pantalla Conexiones: defínelos de nuevo.
8. Instala la App en cada proyecto registrado (`TRADVIA_PROJECTS`) para que las
   consultas de permisos funcionen.

## Qué hace la API

| Ruta | Comportamiento |
|---|---|
| `GET /api/auth/login?next=/runs/?repo=owner/repo` | Guarda un `state` aleatorio, un verificador PKCE y la ruta de retorno validada, y redirige a GitHub. Solo se acepta `/runs/...` como `next`. |
| `GET /api/auth/callback` | Verifica el `state` de un solo uso (y la cookie del navegador que inició el inicio de sesión), intercambia el código con el verificador PKCE, crea la sesión y redirige a la ruta guardada. |
| `POST /api/auth/logout` | Borra la sesión y limpia la cookie. |
| `GET /api/auth/me` | `login`, `avatar_url`, el valor CSRF, el rol en cada proyecto y `can_start` / `can_approve` (lo que el panel puede mostrar como botones). Nunca el token de GitHub. |
| `POST /api/projects/{owner}/{repo}/cards` | Crea la issue (`title`, `description`), la guarda y la agrega al final del backlog. Requiere `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/start` | Agrega `agent:ready`. Requiere `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/reply` | `decision` = `approve` \| `reject` \| `adjust` (+ `text`) comenta "Aprovado", "Rejeitado" o "Ajuste: …". Requiere `approve_plan`; 409 a menos que el último comentario del agente sea el plan. |
| `POST /api/projects/{owner}/{repo}/backlog` | `{"issue": n, "direction": "up"\|"down"}` o `{"order": [...]}`. Requiere `start_card`. |
| `GET /api/projects/{owner}/{repo}/cards/{n}/audit` | Entradas de auditoría del card y, para quien puede aprobar, el plan completo. Requiere al menos el rol `read`; `Cache-Control: no-store`. |
| `GET /api/projects/{owner}/{repo}/pulls/{n}` | El PR: título, descripción, resumen y costo del executor, archivos con +/−, los checks obligatorios, comentarios de revisión y qué botones puede usar la persona. Requiere `read`. |
| `GET /api/projects/{owner}/{repo}/pulls/{n}/files/{path}/diff` | El diff de texto de un archivo, bajo demanda. Requiere `read`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/merge` | Squash merge con el título Conventional Commit del PR, y luego borra el branch. El cuerpo `{"ignore_cla": true}` ignora solo un check `cla` fallido (registrado en la auditoría); cualquier otro check obligatorio fallido rechaza el merge. Requiere `merge_pr`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/update-branch` | Pone al día el branch del PR con main. Requiere `update_branch`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/approve-workflows` | Aprueba las ejecuciones que GitHub retiene para aprobación en el commit más reciente del PR. Cuerpo `{"sha": "<head>"}`; 409 si el PR tiene commits nuevos o nada espera. Requiere `merge_pr`. |
| `GET /api/projects/{owner}/{repo}/deployments/pending` | Deploys que esperan aprobación (los más antiguos de la misma cola quedan como `replaced`) y el orden del backlog guardado en Tradvia. Requiere `read`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/approve` y `/reject` | Revisa el deploy pendiente con el token de la persona; la persona debe ser revisora del entorno. Requiere `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/cancel` | Cancela un deploy reemplazado (cualquier otra ejecución se rechaza). Requiere `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/operations/post-deploy` | Dispara `deploy-oci` con `{"backfill": bool, "backlog": "44 50 45"}`; el panel completa el backlog con el orden guardado. Requiere `approve_deploy`. |

Las rutas de acción responden 401 sin sesión, 403 por CSRF ausente o un
rol insuficiente (el mensaje nombra el rol requerido), 404 para un proyecto que no está en
`TRADVIA_PROJECTS` o un card que no es del proyecto, y 409 para un card en el
estado equivocado. El panel envía un `X-Tradvia-Idempotency-Key` aleatorio; la misma clave
de nuevo dentro de 5 segundos se ignora.

Cada acción de pull request y de deploy se audita (quién, qué, resultado; `ignore_cla`
aparece en el detalle) y se ejecuta con el token de la persona, nunca con el del servidor.
Cuando GitHub rechaza una llamada porque a la **App** le falta un permiso (`Resource not
accessible by integration`), la respuesta lo nombra, por ejemplo *The GitHub App needs
Actions: Read and write*; Contents para merge y borrar branch, Pull requests para
update-branch, Deployments para aprobar y rechazar. Un rechazo para la persona
dice *You are not a reviewer of the production environment.* Otros mensajes claros:
sin rol en el proyecto, *Merge refused: the required check(s) failed*, *Merge refused: the required check(s) wait for their workflows to be approved* y *The branch is behind main*.

Las nuevas interacciones de `.tradvia.yml` son `merge_pr`, `update_branch` y
`approve_deploy` (también aprobar, rechazar, cancelar y la operación posterior al deploy);
todas tienen `maintain` por defecto. Ver `docs/tradvia.example.yml`.

- Cookie `__Host-tradvia_session`: `Secure`, `HttpOnly`, `SameSite=Lax`,
  `Path=/`, sin `Domain`. Válida 8 h desde el último uso, como máximo 7 días desde el inicio de sesión.
- Todo `POST`/`PUT`/`PATCH`/`DELETE` con cookie de sesión requiere el encabezado
  `X-Tradvia-CSRF` (valor de `/api/auth/me`) y un `Origin` (o `Referer`)
  igual a `https://$TRADVIA_DOMAIN`. Los webhooks mantienen su propia firma.
- El token de la persona se guarda cifrado (Fernet, clave derivada de
  `TRADVIA_SECRET_KEY`), nunca se envía al navegador y nunca se registra en logs.
- Los roles vienen de `GET /repos/{owner}/{repo}/collaborators/{login}/permission`
  con el token de la persona. Una respuesta de GitHub queda en caché 5 minutos por sesión; una
  verificación fallida no queda en caché, y la siguiente solicitud vuelve a preguntar. Sin rol, la respuesta dice
  por qué, en las rutas de acción y de lectura y por proyecto en `GET /api/auth/me`
  (`role_reason` y `notice`):
  - El token de inicio de sesión fue revocado o venció (401): *Your GitHub sign-in is no longer valid*. Vuelve a iniciar sesión.
  - GitHub rechazó la verificación (403): la App necesita `Metadata: Read-only`. Un dueño acepta la actualización en la instalación, y luego la persona vuelve a iniciar sesión.
  - Límite de uso de GitHub (403 o 429): espera unos minutos.
  - No se pudo llegar a GitHub, o respondió de forma inesperada: inténtalo de nuevo.
  - GitHub dice que la persona no es colaboradora (404, o `permission: none`): la respuesta directa, *You have no access* o *You need at least the 'read' role*.

## Aprobar los workflows que esperan en un pull request

GitHub retiene las ejecuciones de workflow de algunos pull requests hasta que alguien con acceso
de escritura las aprueba. Eso pasa con las ejecuciones de forks y, como se observó en el PR #95
(issue #97), con las ejecuciones de los propios pull requests del agente. Mientras esperan, los
checks obligatorios aparecen como *awaiting approval* en lugar de fallidos, el merge se rechaza
hasta que se ejecuten, y la pantalla del PR muestra **Aprobar workflows** a quien puede
hacer merge (`merge_pr`, `maintain` por defecto).

El botón llama a `POST /actions/runs/{id}/approve` para cada ejecución que GitHub retiene
en el commit más reciente del PR, con el propio token de la persona, y escribe una entrada de auditoría
`approve_workflows`. El panel envía el commit que mostró: si el PR
recibió un commit nuevo desde entonces, no se aprueba nada y la respuesta es 409. Aprobar
inicia los workflows de ese commit con los secretos del repositorio, así que aprueba solo
código que hayas leído.

Si GitHub pide o no esta aprobación lo decide la configuración de Actions del
repositorio (Settings → Actions → General, *Fork pull request workflows from
outside collaborators*). Tradvia no pudo leer esa configuración desde el
entorno donde se construyó esto, así que el dueño del repositorio debe revisarla
allí.
