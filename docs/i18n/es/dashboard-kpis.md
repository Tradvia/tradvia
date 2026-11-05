<!-- tradvia-translation source="docs/dashboard-kpis.md" source-sha256="f1d47ea41d535ee8a0332b0bad5aef972062d6a50928615db2eb690e7f1926ae" -->
# KPIs del panel, filtros y uso de tokens

Qué mide cada indicador de `dashboard/index.html` y de dónde sale cada número.
El estado de cada funcionalidad y una guía paso a paso están en la página de Ayuda del panel (`dashboard/docs.html`, Shipped).

> **Los costos son estimaciones, no una factura.** Los valores en dólares son el costo equivalente a la API
> que informa `claude -p` (`total_cost_usd`) o que LiteLLM calcula para el planner. No
> son lo que cobra una suscripción o el proveedor de nube.

## De dónde salen los números

| Número | Origen | Dónde se guarda |
| --- | --- | --- |
| Costo y tokens del executor | JSON de `claude -p`: `total_cost_usd` y `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens` + `cache_creation_input_tokens` como caché) | Líneas en la descripción del pull request: `Estimated cost (API-equivalent): $1.0051 · 285s` y `Tokens: 12 345 in · 3 210 out · 80 000 cache`; se leen hacia `pull_requests` |
| Costo y tokens del plan | El resultado del planner (`claude -p` o LiteLLM). Uno por plan, así que cada nuevo plan tiene su propio número | Las mismas dos líneas en el comentario de plan del agente; se leen hacia `comments` (`cost_usd`, `tokens_*`) |
| Costo "hasta ahora" | Línea `estimated cost so far` del PR (planes + executor) | Solo un respaldo, ver más abajo |

Un `usage` ausente, o sin los campos de caché, cuenta como 0 tokens. El lector de la línea `Tokens:`
acepta espacio, espacio de no separación, punto o coma como separador de miles, y números simples.

El store (`schema.sql`) agrega las columnas de forma idempotente (`ADD COLUMN IF NOT EXISTS`):
`pull_requests.tokens_in|tokens_out|tokens_cache` y `comments.cost_usd|tokens_in|tokens_out|tokens_cache`.
`python -m tradvia.backfill` reescribe comentarios y PR, así que el historial se completa donde
la línea existe en GitHub. Los PR y planes antiguos sin la línea `Tokens:` quedan sin tokens.

La API de lectura agrega `tradvia_cost` (con tokens) a cada pull request y `tradvia_plan_cost`
(una entrada por comentario de plan) a cada issue. Estos campos no son de GitHub.

## Costo y tokens por card

```
card = suma de todos los planes del card (cada nuevo plan) + el executor de su PR
```

El "estimated cost so far" del PR ya incluye los planes, así que **no** se suma encima.
Solo se usa cuando no hay ningún comentario de plan guardado para el card (historial anterior
a esta funcionalidad), y entonces representa el card completo. La misma regla vale para los tokens, salvo que
la línea `Tokens:` del PR es solo del executor.

## Indicadores

| Indicador | Mide | Estado |
| --- | --- | --- |
| Tasa de éxito, duración mediana | Ejecuciones del executor elegido en el período (se ignoran las omitidas y canceladas) | Shipped |
| PR del agente fusionados, mediana PR → merge | PR creados en el período en branches `tradvia/` o `claude/` | Shipped |
| Cards en el período | Cards creados en el período que pasan los filtros | Shipped |
| Costo total (planificación + ejecución) | Suma del costo de los cards visibles que tienen costo registrado | Shipped |
| Costo mediano por card | Mediana del costo de los cards | Shipped |
| Costo por PR fusionado | Promedio del costo de los cards cuyo PR se fusionó en el período | Shipped |
| Tokens totales (entrada, salida, caché) | Suma sobre los cards visibles | Shipped |
| Tokens por card | Mediana de entrada + salida + caché por card | Shipped |
| Presupuesto mensual | Gasto del mes UTC actual (planes por la hora del comentario + executor por la hora de creación del PR; el "cost so far" del PR no se suma) frente al tope mensual del proyecto (Configuración, Presupuesto del proyecto; ADR 0018), con una barra: color de alerta desde el 80%, color de detención desde el 100%. "—" con una explicación cuando no hay tope. Requiere sesión (se lee de `/api/auth/me`) y el store. | Shipped |
| Costo y tokens por día (barras SVG) | Planes por la hora del comentario, executor por la hora de creación del PR (UTC) | Shipped |

Sin datos de costo en el período, un indicador de costo o de tokens muestra "—" con una explicación
del motivo. Los cards sin ningún costo registrado quedan fuera de las sumas y medianas.

## Filtros

Los filtros se aplican a los indicadores, los cards y las ejecuciones, y viven en la URL, junto a `?repo=`:

| Parámetro | Valores |
| --- | --- |
| `from` | `7d`, `30d`, `90d` (desde hoy) o `YYYY-MM-DD`; ausente = todo |
| `to` | `YYYY-MM-DD`, inclusive; ausente = sin fin |
| `executor` | `agent` (agente de Tradvia, `agent.yml`), `claude` (Claude Code, `claude.yml`); ausente = todos |
| `fcard` | un número de issue: el tablero muestra solo ese card; ausente = todos. Un card que no está en el tablero se ignora, y el panel lo avisa |
| `card` | un número de issue: abre ese card al cargar (el enlace de las notificaciones y de los pendientes). No filtra |
| `status` | `BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR` o `DONE`; ausente = todos |

Ejemplo: `?repo=Tradvia/tradvia&from=30d&executor=claude&status=DONE`.

## Detalles del card

En cada card, el tablero muestra como etiquetas los tipos de agente que trabajaron en él: `Planner`,
`Tradvia agent` y `Claude Code`. Haz clic en el card, o presiona Enter sobre él, para abrir su ventana:

| Sección | Contenido | Origen |
| --- | --- | --- |
| Agentes que trabajaron en él | Una fila por agente: ejecuciones, planes, tokens de entrada / salida / caché y costo estimado, y el total del card | Comentarios de plan del card (store), el costo del executor del PR más reciente con costo, las ejecuciones del card |
| Diagnóstico | Estado, por qué el card está en el tablero, el último marcador del agente, etiquetas, ejecuciones (total, sin resultado, en curso), última ejecución y su duración, PR, fechas | El card, sus PR y sus ejecuciones |
| Aprobación, pull request, actividad, auditoría | Los mismos controles de antes, para quien tiene el rol | GitHub. Se cargan cuando se abre el card y cuando el card cambia, no en cada actualización |

Un guion (—) significa que no se registró costo ni conteo para ese agente. Estado: **Shipped**.
**Partial:** los tokens y el costo del planner vienen del store; cuando el panel lee GitHub
directamente, aparecen como "—".

## Límites

- **Partial:** los costos por plan vienen del store. Cuando el panel lee GitHub directamente
  (sin store), los comentarios no se cargan, así que el costo del card recurre al
  "cost so far" del PR y las cifras de tokens cubren solo el executor.
- El tablero lee los primeros 50 cards con la etiqueta `agent:ready`, toda issue abierta que tiene un
  PR abierto del agente (hasta 100 issues abiertas y 100 PR abiertos), los 50 PR más recientes y las últimas 30
  ejecuciones por workflow. Un card con un PR abierto del agente está en el tablero aunque no tenga la etiqueta, porque
  la campana lo cuenta.
- Las fechas se comparan en UTC.
