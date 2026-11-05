<!-- tradvia-translation source="docs/adr/0002-positioning-multi-view-orchestration.md" source-sha256="18434d2c28e105700fd7c8fde0d110ded69bdabc7a903a59ca4ce6505f123a0e" -->
# ADR 0002: Orquestación de la ejecución de proyectos con transparencia en varias vistas

- Estado: Aceptada
- Fecha: 2026-10-18
- Reemplaza: el enfoque de "Kanban híbrido" usado en el README y en el sitio web hasta esta fecha

## Contexto

El material inicial describía Tradvia como "agentes de IA en un tablero Kanban híbrido". Eso
era solo un ejemplo. El Kanban servía para ilustrar la *transparencia sobre lo que
hace cada miembro del proyecto — persona o agente —*, no para definir el producto.

Los equipos siguen el avance con notaciones distintas según su método y su
público, y la mayoría ya tiene una herramienta de proyectos que no va a abandonar.

## Decisión

Tradvia es un **orquestador multiagente para la ejecución de proyectos**.
Coordina el trabajo de agentes de IA y de personas, y hace transparente el avance
en la notación que cada público necesita:

| Notación | Responde |
|---|---|
| **Kanban** | ¿Dónde está cada elemento en el flujo ahora mismo? |
| **Gantt** | ¿Cuánto tarda cada elemento, qué depende de qué, qué hitos están en riesgo? |
| **Scrum / Burndown** | ¿Se va a entregar a tiempo el alcance del sprint? |
| **WBS / EDT** | ¿Cómo se descompone el alcance en entregables? |
| **Calendario / Línea de tiempo** | ¿Qué vence en qué fecha? |
| **Carga de trabajo / Capacidad** | ¿Quién — persona o agente — está sobrecargado o subutilizado? |

Consecuencias para la arquitectura:

1. **Un modelo de trabajo neutral.** Toda tarea es un `WorkItem` (jerarquía, fechas,
   dependencias, estimación, responsable, sprint, estado). El contrato de estado del card
   de la ADR 0001 pasa a ser el ciclo de vida de un work item.
2. **Las vistas son proyecciones.** Kanban, Gantt, Burndown, WBS, Línea de tiempo y
   Carga de trabajo son funciones puras sobre la misma lista de work items. Ninguna vista es dueña
   de datos.
3. **Vistas nativas y conectores.** Tradvia trae sus propias vistas *y* se sincroniza
   con las herramientas que los equipos ya usan. Los conectores implementan una interfaz
   (`TrackerConnector`) y mapean los elementos externos a `WorkItem`.

Conectores iniciales: Trello, Jira, ClickUp, Monday.com, Asana, Wrike,
Smartsheet, Artia, Microsoft Planner, KanbanFlow y Airtable.

## Consecuencias

- El material público (sitio web, perfil de la organización, READMEs) se actualiza a este
  posicionamiento. Cada funcionalidad mantiene un estado explícito Shipped / In progress /
  Planned.
- `core/src/tradvia/work/` contiene el modelo de trabajo, las proyecciones y la
  interfaz de conector. La interfaz web consume las proyecciones más adelante.
- Los conectores son buenas primeras contribuciones: cada uno queda aislado detrás de la
  misma interfaz.
