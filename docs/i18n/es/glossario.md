<!-- tradvia-translation source="docs/glossario.md" source-sha256="344e7949d999ed6774578a7d3c17adf0a6a11d4f7f48805e87f9bf6f46eff6f1" -->
# Glosario

Términos principales de Tradvia. El estado de cada capacidad sigue el
[README](../README.md) y la [ADR 0003](adr/0003-agentops-hybrid-execution-control-plane.md):
**Shipped**, **In progress**, **Partial** o **Planned**.

| Término | Definición | Estado |
|---|---|---|
| **Card** | Unidad de trabajo del Kanban híbrido ejecutable; es una máquina de estados duradera (`BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR`, `DONE`) que registra tokens, segundos de cómputo y tiempo. | In progress — máquina de estados y aprobación en el chat |
| **Work item** | Elemento del modelo de trabajo neutral respecto al proveedor, del que Kanban, Gantt, Burndown, WBS, calendario y capacidad son proyecciones; el ciclo de vida del card es el ciclo de vida de un work item (ADR 0002). | Shipped (biblioteca en `core/src/tradvia/work/`) |
| **Human gate** | Punto de aprobación en el que el card se detiene en `WAITING_HUMAN` hasta que una persona responde (por ejemplo, aprobar el plan antes de que el agente abra un pull request). | Partial — aprobación en el chat (Discord) y en un comentario de issue; otros canales planificados |
| **Live takeover** | Cuando un agente encuentra un bloqueo (2FA, CAPTCHA, una interfaz compleja, una decisión de arquitectura), la sesión se congela, se avisa a una persona y esta toma el terminal o el navegador por streaming (WebRTC/VNC), devolviendo el control después. | Planned (Fase 2) |
| **Session worker** | Contenedor que ejecuta una sesión con estado de CLI o navegador (Playwright/CDP), con contexto de autenticación persistente y un mutex de sesión, operando solo cuentas y portales que el cliente está autorizado a usar. | Planned (Fase 2) |
| **Token FinOps** | Control de costos de la ejecución: registro del costo exacto por token o por segundo de cómputo en cada card, además de pool de cuentas, fallback entre proveedores y disyuntores de presupuesto, siempre dentro de los límites de cada proveedor. | Partial — costo registrado por card; pool, fallback y disyuntores planificados; gobernanza en `ee/` planificada |
