<!-- tradvia-translation source="docs/coding-agents.md" source-sha256="52d6ec758f4e964f2c26d22664205add370d06eb35d5d6c33640527c88a23db8" -->
# Agentes de código

El planner y el executor de un card son adaptadores detrás de una misma interfaz,
`CodingAgent` (`core/src/tradvia/agents.py`, [ADR 0010](adr/0010-coding-agent-adapters.md)).
El proveedor del rol elegido en la pantalla Conexiones
([ADR 0019](adr/0019-project-connections.md)) o en `TRADVIA_PLANNER` / `TRADVIA_EXECUTOR`
elige el adaptador. Sin proveedor, el comportamiento no cambia.

## Proveedor × rol

| Proveedor | Planner | Executor | Estado |
|---|---|---|---|
| Claude Code (`claude-code`) | sí | sí | Shipped |
| Gemini CLI (`gemini-cli`) | sí | sí | In progress (pruebas offline con un ejecutor simulado; aún no se ejecutó contra la CLI real en CI) |
| OpenAI Codex CLI (`codex-cli`) | sí | sí | In progress (lo mismo) |
| Kiro, Antigravity y otros IDE | no | no | Planned como executors: los nombres de las herramientas no interactivas aún no están documentados. Hoy pueden trabajar en un card a través del MCP de Tradvia ([mcp.md](mcp.md)) |

Los roles reviewer y orchestrator todavía no usan estos adaptadores.

## Herramientas restringidas y límites

Tradvia crea el branch, hace el commit y abre el pull request. Ningún adaptador ejecuta git,
salvo `git mv` y `git rm` en el executor de Claude Code, para que pueda renombrar y borrar archivos:
el pull request entonces también elimina las rutas antiguas (#185). Las rutas protegidas (`.github/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS`, `.tradvia.yml`) siguen bloqueadas para cualquier cambio, incluido
el borrado.

Orientación durante la ejecución (#99, ADR 0021): con el executor de Claude Code, quienes pueden responder planes pueden
enviar un mensaje a un card en ejecución (ventana del card o un comentario en la issue). La ejecución avanza en
tramos de `TRADVIA_STEER_EVERY_TURNS` turnos (por defecto 5) en una misma sesión (`--resume`) y
entrega los mensajes nuevos entre tramos, dentro de los límites de turnos, tiempo y costo del card. Los
adaptadores de Gemini y Codex corren sin orientación por ahora.

- **Claude Code**: `--allowedTools` (`Read,Glob,Grep` para planificar; herramientas de edición más
  `make check`/`make test` y `git mv`/`git rm` para ejecutar) y `--max-turns`.
- **Gemini CLI** (`gemini -p --output-format json`): `--allowed-tools` con herramientas de solo lectura
  para planificar, y edición de archivos más `make check`/`make test` para ejecutar; sin búsqueda ni acceso web.
  La CLI no tiene opción de turnos, así que el código de salida 53 o más llamadas a herramientas que el límite se informa como
  límite de turnos.
- **Codex CLI** (`codex exec --json`): sandbox `read-only` para planificar, `workspace-write` con
  el acceso a la red desactivado para ejecutar. Más elementos de comando/archivo que el límite se informa como
  límite de turnos.

Los límites de turnos vienen de `TRADVIA_PLAN_MAX_TURNS` y `TRADVIA_EXEC_MAX_TURNS`; el límite de tiempo,
de `TRADVIA_CLAUDE_TIMEOUT`. Los errores (CLI ausente, tiempo agotado, JSON inválido, salida distinta de cero,
límite de turnos) son `RuntimeError` con los secretos ocultos, y el card vuelve a
`WAITING_HUMAN`.

Para todo adaptador, después de la ejecución el flujo bloquea cambios en `.github/`, `.git/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS` y `.tradvia.yml`, y el card vuelve a
`WAITING_HUMAN` sin pull request.

## Normalización de costos

Cada ejecución informa duración, tokens y US$ al panel. Lo que el proveedor no informa
se marca (`usd_reported`, `tokens_reported`) y se muestra como "não informado", nunca se inventa.

| Proveedor | US$ | Tokens |
|---|---|---|
| Claude Code | informado | informados |
| Gemini CLI | no informado | informados cuando las estadísticas del JSON los traen |
| Codex CLI | no informado | informados por el evento `turn.completed` |

El modelo elegido en la pantalla Conexiones para los proveedores de CLI solo se registra; cada CLI
usa su propia configuración de modelo.
