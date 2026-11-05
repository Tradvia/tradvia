<!-- tradvia-translation source="docs/deploy-oci.md" source-sha256="782fe4977af58a5fde899b0e05b8edae444c874981a8fc7dfd3376083f37a0bc" -->
# Deploy de Tradvia en OCI Always Free

Decisión y reglas: [ADR 0005](adr/0005-hosting-on-oci-always-free.md).
Resultado: `https://app.tradvia.org/runs/` servido desde una instancia OCI siempre encendida,
con deploy por GitHub Actions después de que un mantenedor lo aprueba.

Los pasos marcados con **(dueño)** necesitan al dueño de la cuenta: implican inicio de sesión, verificación de
pago o secretos, y no los puede hacer un agente.

## 1. Crear la cuenta OCI (dueño)

1. Regístrate en <https://signup.cloud.oracle.com>. La tarjeta se usa solo para
   verificar la identidad.
2. **Elige la región principal con cuidado: no se puede cambiar**, y el cómputo
   Always Free solo corre allí. Para usuarios en Brasil: `Brazil East (São Paulo)` o
   `Brazil Southeast (Vinhedo)`.
3. Activa MFA en la cuenta.

## 2. Crear la instancia (dueño)

Consola: **Compute > Instances > Create instance**.

| Campo | Valor |
|---|---|
| Name | `tradvia-host` |
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.E2.1.Micro` (Always Free, x86, 1 GB de RAM). Si está disponible, `VM.Standard.A1.Flex` con hasta 2 OCPU / 12 GB es mejor |
| Networking | VCN nueva con subred pública, asignar una dirección IPv4 pública |
| SSH keys | Sube **tu propia** clave pública (guarda la clave privada en tu computadora) |
| Boot volume | 50 GB (por defecto) |
| Initialization script | Pega el contenido de [`infra/oci/cloud-init.yaml`](../infra/oci/cloud-init.yaml) |

A1.Flex suele responder "Out of capacity" en regiones con un solo dominio de
disponibilidad (São Paulo respondió así el 2026-10-19, incluso con 1 OCPU / 6 GB). E2.1.Micro normalmente
tiene capacidad. El cloud-init funciona en ambos y agrega 2 GB de swap.

Luego abre HTTP/HTTPS en la subred: **Networking > Virtual cloud networks >
(tu VCN) > Security Lists > Default** → agrega dos reglas de entrada, origen
`0.0.0.0/0`, TCP, puertos de destino `80` y `443`.

## 3. Apuntar el dominio (dueño)

En el proveedor de DNS de `tradvia.org`, crea un registro `A`:
`app` → la IP pública de la instancia. Deja `tradvia.org` donde está.

## 4. Clave de deploy y secretos (dueño)

En tu computadora (no en la instancia):

```bash
ssh-keygen -t ed25519 -f tradvia-deploy -N "" -C "github-actions deploy"
```

Agrega `tradvia-deploy.pub` a la instancia (`~/.ssh/authorized_keys` del usuario
`ubuntu`), usando tu propia sesión SSH. Obtén la clave del host para fijarla:

```bash
ssh-keyscan -t ed25519 <public-ip>
```

En GitHub: **Settings > Environments > New environment `production`**, agrégate
como **required reviewer** y luego agrega:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `OCI_HOST` | la IP pública |
| Secret | `OCI_SSH_KEY` | contenido de `tradvia-deploy` (clave privada) |
| Secret | `OCI_KNOWN_HOSTS` | la línea de `ssh-keyscan` |
| Secret | `POSTGRES_PASSWORD` | una contraseña aleatoria larga |
| Variable | `TRADVIA_DOMAIN` | `app.tradvia.org` |
| Variable | `ACME_EMAIL` | correo para los avisos del certificado HTTPS |

Borra la clave privada local `tradvia-deploy` después de guardarla como secreto.

## 5. Deploy

**Actions > deploy-oci > Run workflow**, luego aprueba el deploy de `production`.
El workflow sube `infra/oci/` y `dashboard/`, levanta los
servicios y verifica `https://app.tradvia.org/healthz`.

## Cola de deploy

El workflow corre en el grupo de concurrencia `deploy-oci` con `cancel-in-progress: false`.
Un deploy que ya está corriendo nunca se interrumpe por un push más nuevo, y los deploys nunca
corren en paralelo. GitHub mantiene una ejecución en curso y una pendiente por grupo, y una
ejecución pendiente más nueva cancela la pendiente más antigua
([control workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)).

Una ejecución que espera aprobación en `production` retiene el grupo. El 24 de octubre una ejecución esperó
más de una hora, y cada push posterior quedó en cola detrás de ella sin pedir
aprobación, hasta que alguien canceló a mano la ejecución más antigua. El panel muestra dos cosas
a quienes pueden aprobar:

- **Esperando demasiado.** Un deploy que espera aprobación por más de
  `TRADVIA_DEPLOY_WAIT_ALERT_MINUTES` minutos (por defecto `30`) recibe un botón ⏳ en el
  encabezado. El tiempo cuenta desde que se creó la ejecución. El botón solo recuerda:
  no se envía nada a ningún lado.
- **Reemplazado.** Una ejecución en espera se marca como *reemplazada* cuando una ejecución más nueva del mismo workflow
  también está esperando. El panel la destaca como la ejecución que bloquea el deploy más nuevo, y
  su botón **Cancelar y liberar la cola** la cancela. Cancelar sigue siendo una acción de una persona
  en el panel ([ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md)).

Para liberar la cola de una ejecución que espera demasiado, recházala: la ejecución termina y el siguiente
deploy pide aprobación. Aprobar la ejecución ejecuta ese deploy.

## Qué corre dónde

| Servicio | Exposición | Notas |
|---|---|---|
| Caddy | puertos 80/443 | HTTPS automático, encabezados de seguridad |
| Panel | `/runs/` vía Caddy | Archivos estáticos de `dashboard/` |
| PostgreSQL 16 | solo en la red interna de Docker | Almacenamiento de webhooks (fase 2 de la API) |
| API | `/api/*` vía Caddy, sin puerto publicado | Proxy de lectura de GitHub con caché |

## API

El servicio `api` (`core/src/tradvia/api.py`, Starlette + uvicorn, límite de 256 MB)
elimina el límite anónimo de 60 solicitudes/hora por IP de visitante que el panel
alcanza cuando llama a GitHub desde el navegador. En `app.tradvia.org` el panel
detecta `/api/healthz` y usa `/api/github/...`; en otros lugares sigue llamando a
GitHub directamente.

- `GET /api/healthz` devuelve `{"ok": true}`.
- `GET /api/github/{path}` reenvía a `https://api.github.com/{path}` solo para
  los repositorios de `TRADVIA_PROJECTS` (separados por comas; por defecto `TRADVIA_REPO`,
  que por defecto es `Tradvia/tradvia`): `issues`, `issues/{n}/timeline`,
  `issues/{n}/comments`, `pulls`, `actions/runs` y las ejecuciones de los workflows `agent.yml` y
  `claude.yml`. Todo lo demás es 404. Solo se reenvían `state`, `per_page`,
  `page`, `labels`, `sort` y `direction`.
- Caché: en memoria, por URL, TTL de 60 s (`TRADVIA_API_CACHE_TTL`), 500 entradas,
  revalidada con `ETag`. Si GitHub falla, se sirve una entrada vencida con
  `X-Tradvia-Stale: 1`.

Secreto opcional `TRADVIA_GITHUB_TOKEN`: un token fine-grained con acceso de solo lectura
a Issues, Pull requests y Actions en `Tradvia/tradvia`. Un mantenedor
lo conecta al workflow de deploy como `GITHUB_TOKEN` en `.env`. Sin él, la
API funciona con el límite anónimo de GitHub. El token nunca se devuelve ni se registra en logs.

Los cards recurrentes (#133, Configuración → Cards recurrentes) se crean con este mismo token, porque ninguna
persona tiene sesión iniciada cuando se dispara una programación. Para ellos necesita **Issues: Read and write**; con el
token de solo lectura GitHub rechaza el card, no se crea nada y la pantalla de Configuración muestra el
rechazo junto a la programación.

| Funcionalidad | Estado |
|---|---|
| Proxy de lectura de GitHub con caché | Shipped |
| Webhooks de GitHub guardados en Postgres | Shipped |
| Panel que lee issues, PR y ejecuciones de la base de datos | Shipped |
| Historial del card desde la base de datos | Planned |
| Inicio de sesión con GitHub | Partial: implementado, queda apagado hasta que se registre la GitHub App y sus secretos se conecten al deploy ([github-app.md](github-app.md)) |
| Acciones desde el panel | Planned |

## Iniciar sesión con GitHub

Opcional. La API habilita `/api/auth/*` solo cuando `TRADVIA_GITHUB_APP_CLIENT_ID`,
`TRADVIA_GITHUB_APP_CLIENT_SECRET` y `TRADVIA_SECRET_KEY` están todos definidos en `.env`
(`compose.yaml` ya los pasa a la API, además de `TRADVIA_DOMAIN`). Sin
ellos, `GET /api/auth/me` responde `{"enabled": false}` y el panel oculta el
botón. La lectura sigue siendo pública en cualquier caso. Registrar la App y generar los
secretos son pasos del dueño: ver [github-app.md](github-app.md). Un mantenedor conecta
los tres valores al workflow de deploy en un pull request aparte.

## Webhooks

GitHub avisa a la API de cada cambio y la API lo guarda en el Postgres
que ya corre en el host (las tablas se crean en la primera conexión a partir de
`core/src/tradvia/schema.sql`). El panel lee de ahí issues, PR y ejecuciones
(ver "Lecturas del panel" más abajo); solo el historial de un card sigue yendo a GitHub.

Paso del dueño, en el repositorio: **Settings > Webhooks > Add webhook**.

- Payload URL: `https://app.tradvia.org/api/webhooks/github`
- Content type: `application/json`
- Secret: el mismo valor que el secreto `TRADVIA_WEBHOOK_SECRET` del
  entorno `production`
- Eventos (selecciónalos uno por uno): **Issues**, **Issue comments**,
  **Pull requests** y **Workflow runs**

Comportamiento de `POST /api/webhooks/github`:

- Sin secreto configurado, o con un `X-Hub-Signature-256` ausente o inválido: 401,
  no se guarda nada. Cuerpos de más de 1 MB: 413.
- Otros repositorios, eventos no manejados y `ping`: 202, se ignoran.
- Idempotente por `X-GitHub-Delivery`: una entrega repetida se confirma (202)
  y no se guarda dos veces. Un registro solo se reemplaza por otro con
  `updated_at` más reciente.
- Base de datos caída: 503. Error inesperado: 500 (la entrega se revierte
  entera). En ambos casos, reenvíala desde **Settings > Webhooks > Recent
  Deliveries > Redeliver**; se procesa normalmente.
- Un webhook de un repositorio que no está en `TRADVIA_PROJECTS` recibe 202
  y no se escribe nada. Cada entrega se guarda en el proyecto que figura en
  `repository.full_name`.
- `GET /api/store/health` devuelve `{"ok": true, "issues": n, "runs": n,
  "last_delivery": "...", "projects": {"owner/repo": {"issues": n, "runs": n,
  "pull_requests": n, "last_delivery": "..."}}}` (503 si la base de datos está caída).
  Los campos `issues`, `runs` y `last_delivery` del nivel superior están obsoletos (describen el
  primer proyecto); usa `projects`. Nunca devuelve el texto de los comentarios.
- `GET /api/projects` lista los proyectos registrados. Ver `docs/projects.md`.

Carga inicial (se puede repetir sin riesgo; todo es upsert), en el host:

```bash
docker compose exec api tradvia backfill                          # TRADVIA_REPO
docker compose exec api tradvia backfill --project owner/repo     # another project
```

`--project` debe estar en `TRADVIA_PROJECTS`.

Después del deploy y del backfill, guarda el backlog del proyecto (orden = orden de los
argumentos; las dependencias vienen de las líneas "Depende do #N" de las issues):

```bash
docker compose exec api tradvia backlog set --project owner/repo 44 45 46 ...
```

**Sin acceso SSH al host**, ejecuta ambos desde GitHub: Actions → `deploy-oci` →
**Run workflow**, marca `backfill` y/o completa `backlog` con los números de las issues en
orden (por ejemplo `44 50 45 46`). Después del deploy y de la verificación de salud, el workflow
los ejecuta en el host para `TRADVIA_REPO` e imprime `tradvia backlog show`.
`backlog` solo acepta dígitos y espacios.

Lee las issues con la etiqueta `agent:ready`, sus comentarios, los pull requests y las
ejecuciones de `agent.yml` y `claude.yml`. `GITHUB_TOKEN` es opcional (sube el
límite de uso de GitHub). En el host de 1 GB la base de datos queda limitada a 256 MB
(`shared_buffers=64MB`, `max_connections=20`) y la API usa como máximo 3
conexiones.

**Después del deploy de la fase que agrega las lecturas del panel, vuelve a ejecutar el
backfill.** El esquema gana columnas (`html_url`, `state_reason`, `name`,
`display_title`, `run_attempt`) que las filas guardadas antes no tienen; el backfill
las completa.

## Lecturas del panel

`GET /api/store/repos/{repo}/...` devuelve el mismo JSON que GitHub para los campos
que usa el panel, desde Postgres, limitado al proyecto `{repo}`. Solo existen repositorios
de `TRADVIA_PROJECTS` y estas rutas (404 en las demás): `issues` (`labels`, `state`), `pulls` (`state`) y
`actions/workflows/{agent|claude}.yml/runs`. `per_page` está limitado a 100 y
los resultados van del más nuevo al más antiguo. El texto de los comentarios nunca se expone. Si la base de datos está
caída o todavía no tiene datos del recurso, la respuesta es 503 y el panel
reintenta esa llamada por `/api/github/...`. En `app.tradvia.org` el panel
usa la base de datos solo cuando `/api/store/health` informa `issues > 0`, y muestra
"dados: Tradvia" o "dados: GitHub" en el encabezado.

## Recuperación por inactividad

Oracle puede recuperar una instancia Always Free que se mantenga por debajo del 20% de CPU, red
y memoria durante 7 días. La API y los workers la mantienen en uso. Si hace falta, pasa
el tenancy a Pay As You Go y mantén los recursos dentro de los límites gratuitos: ver
la ADR 0005.

## Runner del agente en el host de OCI (dueño)

El workflow `agent` (cards con la etiqueta `agent:ready`) puede correr en el host de OCI
en lugar de en los runners alojados por GitHub. `@claude` (`claude.yml`) y todos los demás workflows
siguen en GitHub: cualquiera puede dispararlos con un comentario, y el código de
contribuidores externos nunca debe ejecutarse en nuestra máquina. El job del agente solo empieza con
`issues: labeled`, que requiere acceso de triage.

Estado: **In progress**. El host tiene 1 GB de RAM, así que los cards corren de a uno y
más lento que en GitHub; el runner está limitado por systemd (`MemoryHigh=600M`,
`MemoryMax=900M`) para que un card pesado no tumbe Caddy, la API ni Postgres.
Pasará a un host más grande cuando haya capacidad (ADR 0005).

1. En GitHub: **Settings → Actions → Runners → New self-hosted runner** y copia
   solo el **token** que aparece en la línea `./config.sh --token ...` (válido por una hora).
   No lo pegues en ningún otro lugar.
2. En el host:
   ```bash
   sudo bash /opt/tradvia/src/infra/oci/runner-setup.sh
   ```
   Pega el token cuando se pida (lo que escribes queda oculto). El script crea el
   usuario `gha-runner` (sin sudo, sin acceso a Docker), registra el runner con la
   etiqueta `tradvia-oci` y lo inicia como servicio con los límites de memoria.
3. Comprueba que el runner aparece como **Idle** en **Settings → Actions → Runners**.
4. Cambia el workflow: **Settings → Secrets and variables → Actions → Variables →
   New repository variable** `TRADVIA_AGENT_RUNS_ON` = `["self-hosted","tradvia-oci"]`.
   Para volver a los runners alojados por GitHub, borra la variable.

Comandos útiles en el host:

```bash
systemctl status 'actions.runner.*'             # service state
journalctl -u 'actions.runner.*' -n 100 --no-pager
free -m; systemd-cgtop -1 --depth=2 | head -20  # memory use
```
