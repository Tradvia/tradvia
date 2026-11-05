<!-- tradvia-translation source="docs/notifications.md" source-sha256="03d6337a3045ce1615ebcdfbf4fdae7643200c630ee36ea7b11ab93b83ce5263" -->
# Notificaciones: bandeja de pendientes y Web Push

Cuando un card necesita a una persona, Tradvia lo muestra en una **bandeja de pendientes** en el panel y,
si la persona lo activó, envía una notificación **Web Push** a su navegador o teléfono.
Decisión: [ADR 0020](adr/0020-web-push-notifications.md).

| Funcionalidad | Estado |
| --- | --- |
| Bandeja de pendientes (lista por proyecto, contador en el encabezado, enlace al card) | Shipped |
| Web Push (VAPID, por usuario y proyecto, preferencias de evento) | Shipped |
| Slack | Planned |
| WhatsApp | Planned |
| Correo electrónico | Planned |

Ambas dependen del inicio de sesión con GitHub ([ADR 0008](adr/0008-sessions-csrf-and-project-roles.md))
y de las acciones del panel ([ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md)).
Las pruebas corren offline con un emisor simulado; la entrega a un servicio de push real necesita las claves de abajo.

## Qué cuenta como pendiente

Un card está pendiente para alguien cuando el siguiente paso es una decisión humana y el rol
de esa persona (leído de `.tradvia.yml`, [ADR 0004](adr/0004-access-follows-project-configurable-interactions.md))
alcanza para responderla:

| Evento (`kind`) | Qué pasó | Interacción que lo responde (rol por defecto) |
| --- | --- | --- |
| `plan_ready` | El agente publicó un plan y espera "Aprovado", "Rejeitado" o "Ajuste" | `approve_plan` (write) |
| `agent_question` | El agente hizo una pregunta (`❓`) y espera | `approve_plan` (write) |
| `adjust_limit` | Se alcanzó el límite de ajustes; el card necesita a una persona | `approve_plan` (write) |
| `human_request` | Un agente pidió que una persona actúe (`request_human`), con el enlace de la sesión en vivo cuando la hay. También va a Discord cuando hay un canal configurado | `live_takeover` (maintain) |
| `pr_opened` | El agente abrió un pull request que sigue abierto | `merge_pr` (maintain) |
| `budget_limit` | El límite de presupuesto detuvo el card | `budget_control` (admin) |

Una persona sin rol en el proyecto, o con un rol por debajo de la interacción, no ve nada
de ese tipo ni recibe push de él. Un card sale de la bandeja cuando alguien lo responde
(un comentario después del agente, o se cierra el PR) o cuando se cierra la issue.

## La bandeja

Con sesión iniciada, el encabezado muestra una campana con el número de cards que te esperan en todos los
proyectos. Haz clic para verlos agrupados por proyecto; cada fila lleva al card
(`?repo=owner/repo&card=N`), que se abre con los controles para responder, aprobar o hacer merge.

API: `GET /api/projects/{owner}/{repo}/pending` devuelve `{count, items}` para la persona
con sesión; `/api/auth/me` trae `pending_count` por proyecto.

## Activar Web Push

1. **Genera las claves VAPID una vez** (dos cadenas base64url):

   ```bash
   npx web-push generate-vapid-keys
   ```

   Sin Node: `pip install py-vapid`, luego `vapid --gen` y
   `vapid --applicationServerKey` (muestra la clave pública; la privada queda en
   `private_key.pem`, cuya ruta el emisor también acepta).

2. **Define las variables** en el `.env` del servidor (`core/.env.example` las lista):

   ```
   TRADVIA_VAPID_PUBLIC_KEY=...
   TRADVIA_VAPID_PRIVATE_KEY=...
   TRADVIA_VAPID_SUBJECT=mailto:you@example.org   # opcional; por defecto https://$TRADVIA_DOMAIN
   ```

   La clave privada vive **solo** en el `.env` del servidor: nunca se envía al navegador,
   se registra en logs ni la devuelve la API. El push queda apagado si falta cualquiera de las claves, y el panel
   entonces oculta los controles de notificación. Reinicia la API después de cambiarlas.

3. **Actívalo en el panel**: inicia sesión, abre la campana, pulsa **Activar notificaciones** y
   acepta el aviso del navegador. El panel registra `sw.js` (service worker), se suscribe con
   la clave pública y guarda la suscripción para ti y el proyecto actual. Hazlo en cada
   proyecto (y en cada navegador o teléfono) del que quieras recibir avisos. Requiere HTTPS y un
   navegador con soporte de Push; en iOS, agrega primero el panel a la pantalla de inicio.

4. **Elige los eventos** con las casillas debajo del botón; se guardan por usuario y
   proyecto. Por defecto todos los eventos están activos. **Desactivar notificaciones** elimina la suscripción.

Se envía un push cuando un webhook de GitHub registra un evento que lleva un card a uno de los
tipos de arriba, a los suscriptores de ese proyecto cuyo rol puede responderlo y que mantuvieron ese
evento activo. Los webhooks reenviados no notifican dos veces. Las suscripciones que el servicio de push informa como
inexistentes (HTTP 404 o 410) se eliminan. Un fallo de push nunca cambia la respuesta del webhook.
El envío usa el rol guardado del suscriptor (de su sesión activa), así que quien tiene la sesión
vencida no recibe push hasta que vuelva a iniciar sesión.

## Qué contiene la notificación

Solo el **proyecto**, el **número del card** y el **tipo de evento**, por ejemplo
"tradvia-tradvia · #47 · plan listo para aprobar". El mensaje nunca lleva el título del card,
un comentario, el texto del plan, el branch ni ningún otro contenido de GitHub: el service worker arma el
texto con esos tres valores y etiquetas fijas (portugués, inglés o español según el idioma del navegador).
El servicio de push (Google, Mozilla, Apple) solo ve el contenido cifrado y el endpoint.
Al hacer clic se abre el card en el panel, donde se aplican el inicio de sesión y las verificaciones de rol de siempre.

El servidor solo envía a endpoints `https` en nombres de host públicos, ya que el endpoint
de la suscripción es un destino de solicitudes.

## Alertas proactivas

Estado: **In progress** (pasa a Shipped después del merge).

Mientras el servidor corre, una verificación cada `TRADVIA_ALERT_INTERVAL_S` segundos (por defecto `300`)
avisa por Discord y Web Push sobre tres cosas, una vez por ocurrencia, con el enlace al
card (o a la ejecución cuando no hay card):

- **Cola de deploy atascada.** Un deploy esperando aprobación por más de
  `TRADVIA_DEPLOY_WAIT_ALERT_MINUTES` (por defecto `30`). Las ejecuciones reemplazadas no alertan. Requiere
  `GITHUB_TOKEN` y el inicio de sesión configurados.
- **Costo fuera de lo normal.** Un card cuyo costo sumado supera `TRADVIA_COST_ALERT_FACTOR`
  (por defecto `3`) veces el costo mediano por card del proyecto. Requiere al menos 5 cards con costo
  e ignora una mediana de 0.
- **Ejecución sin actualizaciones.** Una ejecución todavía en curso o en cola sin actualizarse por más de
  `TRADVIA_RUN_STALE_MINUTES` (por defecto `30`).

Las verificaciones solo corren cuando Discord (`DISCORD_BOT_TOKEN` y `DISCORD_CHANNEL_ID`) o Web Push
está configurado. El mensaje de Discord tiene el tipo, el proyecto y el enlace; el push tiene solo
proyecto, número del card y tipo (`deploy_stuck`, `cost_outlier`, `run_dead`), se puede silenciar por
tipo en las preferencias y va a quien puede responderlo según su rol. Una alerta se anuncia de nuevo
solo después de que su condición dejó de cumplirse y volvió. Las alertas anunciadas se guardan en memoria:
un reinicio puede reenviar las que siguen activas. Slack y los webhooks salientes quedan fuera del alcance.
