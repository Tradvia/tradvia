<!-- tradvia-translation source="docs/adr/0010-coding-agent-adapters.md" source-sha256="6ce9287d4620e0e409c33e297f0ad6d0252374a887b41688e5578fa72e069055" -->
# ADR 0010: Adaptadores de agentes de código

- Estado: Aceptada
- Fecha: 2026-10-25

## Contexto

El planner/executor de un card era solo Claude Code. La ADR 0007 hace de la pantalla Conexiones
el lugar para elegirlo, y la ADR 0019 guarda el proveedor y el secreto por rol.

## Decisión

- **Interfaz**: `CodingAgent` tiene `plan(title, body, number, cwd, notice)`, que devuelve el
  JSON del plan, y `execute(card, cwd, budget_notice)`, que devuelve los archivos modificados, el resumen,
  el costo, los tokens, la duración y las llamadas a herramientas. `ClaudeCodeAgent` envuelve las funciones existentes
  sin cambiarlas; `GeminiCliAgent` y `CodexCliAgent` comparten `CliAgent`.
- **Solo no interactivo**: un adaptador solo existe para una CLI con un modo no interactivo
  documentado y salida JSON. Herramientas restringidas (sin git, sin red más allá del proveedor), límites de turnos y
  de tiempo. Las herramientas de IDE (Kiro, Antigravity) siguen en Planned hasta que exista una CLI/API estable.
- **Las rutas protegidas** las aplica el flujo después de la ejecución, a partir de `git status`, así que
  valen para cualquier adaptador.
- **Costo**: lo que un proveedor no informa se marca y se muestra como "não informado".

## Consecuencias

Agregar un proveedor es una clase y una entrada en el catálogo. Los límites de turnos de las CLI sin
opción de turnos se verifican después de la ejecución, no durante ella.
