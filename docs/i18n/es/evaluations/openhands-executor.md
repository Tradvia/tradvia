<!-- tradvia-translation source="docs/evaluations/openhands-executor.md" source-sha256="c5f2a83bacb75bd3077647b53b61926b9c7cc54d212e5b9d2a24b0fe9a6116b8" -->
# Evaluación: OpenHands como executor (spike #134)

Estado de este documento: **evaluación incompleta**. Las fuentes primarias (el `LICENSE` del
repositorio de OpenHands, las releases y la documentación de la CLI) no se pudieron obtener cuando se escribió esto
(no se concedió acceso web en la ejecución), así que nada de lo que sigue sobre OpenHands se afirma como
un hecho. Cada elemento de OpenHands está marcado como **No verificado** e indica la fuente exacta a leer.
Los hechos del lado de Tradvia vienen de este repositorio y se citan por ruta.

**OpenHands no está disponible en Tradvia hoy.** No hay adaptador, ni entrada en el catálogo
ni prueba para él.

## Recomendación

**Todavía no. No abras un card de adaptador hasta que se hagan las verificaciones de abajo.** La decisión
no se puede tomar a partir de este documento, porque la licencia del paquete específico, el costo real
por tarea y la salida JSON no interactiva no están verificados, y el driver de sesión
(#118) aún no está en este repositorio. Si las verificaciones pasan, la respuesta pasa a ser "sí, un
adaptador según el patrón de la [ADR 0010](../adr/0010-coding-agent-adapters.md), como un card aparte".

Condiciones para un "sí":

1. El `LICENSE` del repositorio que publica el SDK/CLI que llamaríamos (no la
   página de la organización) permite usarlo como dependencia de runtime en nuestros términos, y no se usa
   ningún directorio bajo otra licencia (por ejemplo una carpeta `enterprise/`).
2. La CLI tiene un modo no interactivo documentado y estable, con salida legible por máquina
   (ADR 0010, "Headless only").
3. Se pueden aplicar la restricción de herramientas, sin git, sin red más allá del proveedor del modelo, y límites de turnos y de tiempo
   (ADR 0010; presupuestos de la ADR 0006).
4. El costo y los tokens se informan, o se marcan como "não informado", como permite la ADR 0010.

## 1. Licencia

| Elemento | Evidencia | Fuente | Fecha | Estado |
|---|---|---|---|---|
| Licencia del repositorio del paquete SDK/CLI | — | `LICENSE` en el repositorio específico (revisa el repositorio del SDK/CLI, no la organización) | — | No verificado |
| Partes bajo una licencia distinta (directorios enterprise) | — | el mismo repositorio, listado de directorios y archivos LICENSE por directorio | — | No verificado |

Pendiente: leer ambos y registrar el id SPDX, la URL y la fecha de consulta. El `core/` de Tradvia es FSL-1.1-ALv2
(ADR 0001); una dependencia o llamada a subproceso debe ser compatible con ella.

## 2. Costo por tarea

Registro comparable del lado de Tradvia: `core/src/tradvia/budget.py` y `claude_code.py` producen
duración, tokens y US$ por ejecución; la ADR 0006 informa unos US$ 0,41 por card para Claude Code
(estimación equivalente a la API, 2026-10-19).

| Elemento | Evidencia | Estado |
|---|---|---|
| Costo de Claude Code por card | ~US$ 0,41 (ADR 0006); US$ y tokens informados (`docs/coding-agents.md`) | Shipped (medido) |
| Costo de OpenHands por card | no se hizo ninguna ejecución, no hay clave de API disponible para medir | No verificado |
| ¿OpenHands informa US$? | desconocido; si no, marcar `usd_reported=False` | No verificado |

Pendiente: ejecutar la misma tarea pequeña en ambos executors y comparar US$, tokens y duración;
si no hay clave disponible, estimar por el precio por token del modelo y marcarlo como **estimado**.

## 3. Aislamiento

Lo que Tradvia tiene hoy:

- El executor corre en un runner de GitHub (ADR 0006: el aislamiento de la ejecución está **Partial**).
- Los session workers (contenedores aislados) están **Planned** (`docs/glossario.md`, Fase 2;
  la funcionalidad 1 del README está Partial).
- El driver de sesión del #118 no está en este repositorio, así que no se pudo leer su cobertura.
- Las rutas protegidas (`.github/`, `.git/`, `LICENSE.md`, `CLA.md`, `CODEOWNERS`,
  `.tradvia.yml`) las aplica el flujo después de la ejecución, a partir de `git status`, para cualquier
  adaptador (ADR 0010). Esa parte no depende de OpenHands.

Preguntas abiertas, No verificado: si el runtime Docker de OpenHands cabe dentro de una sesión de contenedor
de Tradvia o la duplica (Docker-in-Docker en un runner); si puede correr sin
git, sin red más allá del proveedor, y con límites de turnos y de tiempo.

## 4. Mantenimiento

| Elemento | Evidencia | Fuente | Estado |
|---|---|---|---|
| Ritmo de releases | — | GitHub Releases del repositorio del paquete | No verificado |
| Ritmo de commits/cambios, cambios incompatibles en las opciones de la CLI | — | historial de commits, changelog | No verificado |

## Comparación con los adaptadores existentes

| Executor | JSON no interactivo | Costo informado | Límites de herramientas | Estado en Tradvia |
|---|---|---|---|---|
| Claude Code | `claude -p` | US$ y tokens | `--allowedTools`, `--max-turns` | Shipped |
| Gemini CLI | `gemini -p --output-format json` | solo tokens | `--allowed-tools`, turnos verificados después de la ejecución | In progress |
| Codex CLI | `codex exec --json` | solo tokens | modo sandbox, turnos verificados después de la ejecución | In progress |
| OpenHands | No verificado | No verificado | No verificado | No disponible (solo evaluación) |

Fuente de las tres primeras filas: `docs/coding-agents.md`.

## Fuera del alcance

- **CrewAI** no es un executor: duplicaría LangGraph.
- **Mission Control** está en alfa y tiene otra stack.

## Próximo paso

Quien tenga acceso web: completa las celdas "No verificado" de arriba con la URL de la fuente y la fecha, ejecuta
la comparación de costos y luego actualiza la recomendación. Solo si pasa a ser "sí", agrega
OpenHands como **Planned** en `docs/coding-agents.md` y abre un card aparte para el
adaptador. No hace falta una ADR nueva; la ADR 0010 cubre el patrón.
