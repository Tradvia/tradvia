<!-- tradvia-translation source="docs/orchestrator.md" source-sha256="51a53205178097c8f2e30a67e04b6fb32c77e68a23aee0a8ce5766d88ddfab3d" -->
# Conversación con el orquestador

Estado: 🟦 Partial. La conversación, el conjunto cerrado de herramientas, el presupuesto, el registro de costos y
la auditoría están implementados y probados offline con un modelo con guion. Aún no entregados: ejecución
duradera de una ronda entre reinicios de la API (#40; una ronda perdida en un reinicio deja el mensaje
sin respuesta), una bandeja de pendientes compartida con el resto del panel (#47; por ahora
los pendientes viven en la conversación) y una verificación en vivo contra un proveedor real. El chat
solo funciona con un proveedor de IA con la clave de API del propio proyecto ([ADR
0013](adr/0013-chat-needs-an-api-key.md)): sin ella, queda apagado y el panel explica por qué.
Forma parte de la [ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md) y de la
[ADR 0019](adr/0019-project-connections.md) (el rol `orchestrator`, más abajo).

## Cómo funciona

Cada proyecto tiene una conversación, guardada en Postgres (`chat_messages`: proyecto, autor,
tipo, texto, referencias a cards y pull requests, y el costo de la ronda detrás de una respuesta).
En el panel es la sección **Conversación con el orquestador**: el historial, un cuadro de
texto y el indicador "orquestador trabajando…" mientras hay una ronda en curso. La sección
se actualiza por consultas periódicas, más rápido mientras el orquestador trabaja.

- `GET /api/projects/{owner}/{repo}/chat` devuelve `messages`, `working`, `cost` (total
  registrado para el proyecto), `can_chat` y `chat_off_reason`. Requiere el rol `read`.
  `can_chat` solo es verdadero para quien tiene `start_card` **y** cuando un proveedor de IA con
  clave de API atiende el chat; si no, para quien tiene `start_card`, `chat_off_reason` es
  `"no_api_key"` y el panel muestra cómo configurarlo.
- `POST /api/projects/{owner}/{repo}/chat` con `{"text": "...", "refs": [{"type": "card",
  "number": 7}]}` guarda el mensaje, inicia una ronda en segundo plano y responde `202`. Tiene
  la misma sesión, CSRF, clave de idempotencia y auditoría que toda acción del panel, y requiere
  la interacción `start_card` de `.tradvia.yml` (ADR 0004; `triage` por defecto). Mientras una ronda
  está en curso, un segundo mensaje recibe `409`; sin proveedor de IA y su clave de API también recibe `409`,
  y no se guarda nada.

Una ronda es un ciclo. El modelo recibe la conversación reciente del proyecto y los resultados de las
herramientas que ya usó, y responde un objeto JSON: `{"tool": "...", "args": {...}}` para usar una
herramienta o `{"reply": "...", "refs": [...]}` para terminar. Tradvia ejecuta la herramienta y repite; la
respuesta se guarda en la conversación. El modelo no tiene shell, archivos ni herramientas de red
propias: es un chat completion simple a través de LiteLLM, así que no hay ninguna herramienta que pueda llamar
más allá de responder el único objeto JSON que Tradvia espera.

## Qué modelo responde

La fila `orchestrator` de las conexiones del proyecto (`dashboard` → Conexiones, [ADR
0019](adr/0019-project-connections.md)) decide, con la misma precedencia que todos los demás roles
(la sección `providers` de `.tradvia.yml` es el valor por defecto, y un valor definido en la pantalla Conexiones
lo reemplaza):

- **`anthropic`, `openai` o `gemini`, con su clave de API guardada:** un chat completion simple
  a través de LiteLLM (ya es dependencia de `core` — el planner, `llm.py`, lo usa igual),
  con la clave guardada del propio proyecto pasada en la propia llamada. Nada se ejecuta en un shell, así que nada
  de esto necesita Node.js ni una CLI. Cada mensaje se cobra en esa clave (por uso).
- **Cualquier otra cosa — sin valor, sin clave guardada todavía, o una CLI de agente de código:** el chat queda apagado para el
  proyecto (`orchestrator.model_for_connection` no devuelve modelo). No hay respaldo en Claude
  Code ni inicio de sesión por suscripción para el chat (ADR 0013): la conexión `claude-code` solo ejecuta
  el executor. El rol orchestrator rechaza los proveedores de CLI tanto en la pantalla como en
  `.tradvia.yml`.

Esto es por proyecto, no una configuración de todo el servidor: dos proyectos en la misma instalación de Tradvia pueden
responder con dos proveedores distintos, y cambiar de proveedor no requiere un nuevo deploy — solo un
cambio en la pantalla Conexiones.

## Qué puede hacer el orquestador

| Herramienta | Hace |
|---|---|
| `read_backlog`, `read_cards`, `read_card`, `read_plan` | Lee el backlog, los cards y el plan que espera respuesta |
| `read_pulls`, `read_pull`, `read_checks`, `read_deploys` | Lee pull requests, sus checks y los deploys que esperan aprobación |
| `create_card`, `edit_card` | Crea un card al final del backlog; cambia un título o una descripción |
| `reorder_backlog` | Define el orden o sube/baja un card |
| `start_card` | Marca un card como listo para el agente (`agent:ready`) |
| `reply_plan` | Responde a un plan con **`adjust`** y un texto |
| `review_pr` | Ejecuta `make check` en el branch del pull request en el runner del proyecto y comenta el resultado en el PR |

Toda herramienta actúa sobre el único proyecto de la conversación, con el **propio token de GitHub de la persona
con sesión** (así GitHub muestra quién actuó), y solo con los argumentos listados para ella.

## Qué no puede hacer

- **Aprobar o rechazar un plan, hacer merge de un pull request, aprobar o cancelar un deploy, ejecutar la
  operación posterior al deploy.** No hay herramienta para eso (ADR 0004). `reply_plan` rechaza cualquier decisión
  que no sea `adjust`. Cuando el modelo pide una de ellas, Tradvia la rechaza, la audita como denegada y
  guarda un mensaje **pendiente** con una referencia y un botón que llama a la acción ya existente del panel
  (aprobar/rechazar plan, merge, aprobar deploy), verificada de nuevo contra el rol de la persona
  al hacer clic.
- Ejecutar cualquier comando distinto de los fijos `git fetch`, `git worktree` y `make check` de
  `review_pr`. La revisión rechaza pull requests de forks (su código no se ejecuta) y corre
  sin token y con `TRADVIA_OFFLINE=1`.
- Ver otro proyecto. Las consultas al store filtran por proyecto, las llamadas a GitHub usan el
  repositorio del proyecto, y un argumento de herramienta que nombra un repositorio se rechaza.
- Usar una herramienta que no está en la tabla, o un argumento que no está listado: ambos se rechazan
  y se devuelven al modelo.

El texto de cards, comentarios y pull requests no es confiable: el prompt dice que es un dato, y
el conjunto cerrado de herramientas limita lo que puede hacer una inyección de prompt.

## Credenciales

Ninguna credencial va en el prompt. El token de GitHub de la persona queda dentro del proceso de la API y
solo lo usan las herramientas.

- El secreto descifrado de la conexión del proyecto (ADR 0019) se pasa como el
  argumento `api_key` de la única llamada de completion, nunca por una variable de entorno del
  proceso de la API. Pueden estar en curso rondas de chat de varios proyectos a la vez, cada una en su hilo
  del mismo proceso (`start_thread` de `api.py`); una variable de entorno sería compartida por todas
  y podría filtrar la clave de un proyecto en la ronda de otro. Cualquier error del proveedor se
  oculta igual que ya se hace con la salida de `review_pr`, así que una clave repetida en un mensaje
  de error nunca llega a la conversación guardada.

`review_pr` necesita el checkout del proyecto en el runner: define `TRADVIA_RUNNER_DIR_<OWNER>_<REPO>`
(para `Tradvia/tradvia`: `TRADVIA_RUNNER_DIR_TRADVIA_TRADVIA`). Sin ella, la herramienta responde
que no hay ningún checkout configurado. `git fetch` usa las credenciales git del propio runner.

## Límites y costo

Cada mensaje de una persona tiene un presupuesto; cuando una ronda lo alcanza, Tradvia la detiene y guarda un
mensaje de aviso que dice qué límite se alcanzó (lo que ya se hizo está en el registro de auditoría).

| Variable | Por defecto | Límite |
|---|---|---|
| `TRADVIA_ORCH_MAX_TURNS` | 10 | llamadas al modelo por mensaje |
| `TRADVIA_ORCH_TIMEOUT` | 300 | segundos por mensaje |
| `TRADVIA_ORCH_BUDGET_USD` | 1.0 | costo equivalente a la API por mensaje |
| `TRADVIA_ORCH_MAX_TOKENS` | 200000 | tokens de entrada + salida por mensaje |
| `TRADVIA_ORCH_REVIEW_TIMEOUT` | 900 | segundos para el `make check` de `review_pr` |

El costo y los tokens de cada ronda (respuestas y avisos) se guardan en su último mensaje, y
la conversación muestra el total del proyecto. Los valores inválidos vuelven al valor por defecto. En
modo offline (`TRADVIA_OFFLINE=1`) el orquestador responde con un aviso en lugar de llamar
al proveedor.

## Auditoría

Cada uso de herramienta, incluidos los rechazados y los que fallaron, es una entrada de `audit_log` con el autor
`orchestrator`, la acción `orchestrator:<tool>` y un detalle que empieza con
`for @<login>` (la persona con sesión). El mensaje en sí se audita como la acción `chat`
de la persona.
