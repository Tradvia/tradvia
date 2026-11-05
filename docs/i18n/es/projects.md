<!-- tradvia-translation source="docs/projects.md" source-sha256="81cf345f272fb87fffc3913cd959d5e1658782eff41538ba438eeed929c8380a" -->
# Proyectos

El **proyecto** es la unidad de alcance de todo lo que Tradvia guarda (ADR 0006, fase 0).
Estado: **Shipped** para almacenamiento y API. Memoria, skills y políticas por proyecto están
**Planned** para las próximas fases.

## Modelo

Tabla `projects`:

| Columna | Significado |
|---|---|
| `id` | clave primaria `bigserial` |
| `slug` | única, derivada del repositorio (`Tradvia/tradvia` → `tradvia-tradvia`) |
| `kind` | `github_repo` (el único tipo hoy) |
| `external_id` | única, `owner/repo` |
| `created_at` | momento de creación |
| `settings` | `jsonb`, vacío por ahora |

`deliveries`, `issues`, `comments`, `pull_requests` y `workflow_runs` tienen
`project_id bigint NOT NULL REFERENCES projects (id)` y la clave primaria
`(project_id, id)`: los ids de GitHub solo son únicos dentro de los datos de un proyecto, y un
id de entrega se registra una vez por proyecto. Los índices sobre `issue_number` empiezan por
`project_id`.

### Migración

`schema.sql` es idempotente y corre en la primera conexión a la base de datos. Crea
`projects`, inserta `Tradvia/tradvia`, agrega `project_id`, asigna cada fila existente
con `NULL` a `Tradvia/tradvia`, define `NOT NULL` y reemplaza las claves primarias de una sola
columna por `(project_id, id)` solo cuando aún no son compuestas. Ejecutarlo de nuevo
no cambia nada.

## Registrar proyectos

`TRADVIA_PROJECTS` es una lista de `owner/repo` separada por comas (por defecto: `TRADVIA_REPO`,
que por defecto es `Tradvia/tradvia`). Solo se aceptan esos repositorios; cada uno
recibe una fila en `projects` la primera vez que se usa.

- **Webhook**: el proyecto viene de `repository.full_name`. Un repositorio que no
  está registrado recibe `202` y no se escribe nada.
- **Backfill**: `tradvia backfill --project owner/repo` (por defecto `TRADVIA_REPO`); el
  repositorio debe estar en `TRADVIA_PROJECTS`.
- **Rutas del proxy y del store**: el `{repo}` de `/api/github/repos/{repo}/...` y
  `/api/store/repos/{repo}/...` debe estar registrado; si no, `404`.

## Aislamiento

Cada método del `Store` recibe primero `project_id` y cada consulta sobre una tabla guardada
filtra por él; una prueba recorre `store.py` para asegurarlo. Dos proyectos pueden tener los mismos
ids y números de issue sin ver nunca las filas del otro.

## Visibilidad

Por ahora todo proyecto registrado es público. La visibilidad por rol llega con el inicio de sesión
(ADR 0004); `GET /api/projects` ya devuelve una marca `public` para eso.

## API

- `GET /api/projects` → `[{"slug", "kind", "external_id", "public": true}]`.
- `GET /api/store/health` → `{"ok", "issues", "runs", "last_delivery", "projects":
  {"owner/repo": {"issues", "runs", "pull_requests", "last_delivery"}}}`. Los campos
  `issues`, `runs` y `last_delivery` del nivel superior están **obsoletos** (primer proyecto
  registrado); usa `projects`.
- `GET /api/store/repos/{repo}/issues|pulls|actions/workflows/{agent|claude}.yml/runs`
  como antes, limitados al proyecto `{repo}`.

## Backlog

Estado: **Shipped** (lectura). Crear, iniciar y reordenar cards en el panel
(`POST /api/projects/{owner}/{repo}/cards`, `.../cards/{n}/start`, `.../backlog`)
está **Partial**: código y pruebas offline listos; solo funciona en producción cuando
la GitHub App tenga el permiso **Contents: Read-only** y el inicio de sesión esté conectado
([docs/github-app.md](github-app.md)). Un card nuevo entra al final del backlog.

El backlog se guarda por proyecto en la tabla `backlog_items` (`project_id`,
`issue_number`, `position`, `depends_on int[]`, `note`, `updated_at`; clave primaria
`(project_id, issue_number)`). `set_backlog` reemplaza el orden completo en una sola
transacción; cada consulta filtra por `project_id`.

- CLI: `tradvia backlog set --project owner/repo 44 45 46 ...` guarda el orden
  (posición = orden de los argumentos; se rechazan duplicados y valores no enteros) y lee
  las dependencias de la descripción de cada issue, de líneas como `Depende do #N` o
  `Depends on #N`. `tradvia backlog show --project owner/repo` lo muestra.
  `--project` tiene como valor por defecto `TRADVIA_REPO`.
- API: `GET /api/store/repos/{repo}/backlog` devuelve los elementos en orden con
  `issue_number`, `position`, `title`, `state`, `labels` (de la tabla `issues`),
  `depends_on`, `note` y `status`. 404 para un proyecto no registrado, 503 sin base de datos.
- Estado, verificado en este orden: `concluído` (issue cerrada), `em andamento` (etiqueta
  `agent:ready` o un PR abierto del branch `tradvia/issue-N`), `bloqueado` (alguna
  dependencia está abierta o es desconocida), `pronto`.
- Panel: una columna Backlog antes de las columnas de cards, mostrada solo cuando la API del
  store responde.

## Panel

El encabezado tiene un selector de proyecto alimentado por `/api/projects`. Mantiene `?repo=` en la URL
y se oculta cuando la API no está disponible.

Con sesión iniciada, una campana muestra cuántos cards de tus proyectos te esperan, con la lista
por proyecto y notificaciones Web Push opcionales por usuario y proyecto; la bandeja de pendientes
sigue los roles del `.tradvia.yml` de cada proyecto. Ver
[notifications.md](notifications.md). `?card=N` abre ese card al cargar.
