<!-- tradvia-translation source="docs/adr/0003-agentops-hybrid-execution-control-plane.md" source-sha256="a5e92472f42d0f4876e71107cd8c2984284fdc9be15658fbf768ecf0932fd213" -->
# ADR 0003: Plano de control open-core de AgentOps y ejecución híbrida

- Estado: Aceptada
- Fecha: 2026-10-18
- Modifica: [ADR 0002](0002-positioning-multi-view-orchestration.md) (solo el posicionamiento público)

## Contexto

La ADR 0002 enmarcó Tradvia en torno a las notaciones de avance. Eso subestimaba el
núcleo de ingeniería: cómo se ejecuta realmente el trabajo, con qué runtime, a qué
costo, y cómo interviene una persona cuando un agente se atasca.

## Decisión

Tradvia se posiciona como **el plano de control open-core de AgentOps y ejecución
híbrida** que une la gestión de proyectos hecha por personas y la ejecución autónoma por IA.
Seis pilares:

| Pilar | Estado al 2026-10-18 |
|---|---|
| **Ejecución en dos runtimes** — APIs de LLM sin estado (OpenAI, Anthropic, Gemini, Ollama) más session workers de CLI/navegador con estado (Playwright/CDP), con mutex de sesión | Partial: APIs vía LiteLLM |
| **Pool de varias cuentas y Token FinOps** — carga repartida dentro de la cuota de RPM/TPM de cada cuenta, fallback entre proveedores, costo por token y por segundo de cómputo, disyuntores de presupuesto | Partial: costo registrado por card |
| **Kanban híbrido ejecutable y Live Session Takeover** — cada card es una máquina de estados duradera; una persona puede tomar una sesión de navegador/terminal congelada (WebRTC/VNC) y devolver el control | In progress: máquina de estados + aprobación en el chat |
| **Sandbox zero-trust** — MicroVMs Firecracker efímeras, RLS en PostgreSQL, bóvedas cifradas de credenciales/sesiones | Planned |
| **MCP nativo y AST GraphRAG** — destilación de conocimiento episódico para bajar el costo de tokens | Planned |
| **Monorepo open-core** — `/core` autoalojado (Docker Compose), `/ee` + Cloud con pools de sesión por equipo, RBAC, gobernanza de FinOps | In progress: monorepo, licencias, CI |

Reglas para el material público:

1. Cada funcionalidad mantiene un estado explícito Shipped / In progress / Partial / Planned.
2. El pool de cuentas **respeta los límites de uso y los términos de cada proveedor**. Lo describimos
   como reparto de carga dentro de las cuotas y fallback entre proveedores, nunca como
   una forma de eludir límites.
3. Los session workers solo operan cuentas y portales que el cliente está autorizado
   a usar. Una persona resuelve los pedidos de 2FA y CAPTCHA con el Live Session
   Takeover; los agentes nunca intentan resolverlos.

## Consecuencias

- El sitio web deja la vista previa de las seis vistas en favor del tablero ejecutable
  con dos runtimes, pool de cuentas y live takeover. El modelo de trabajo y sus
  proyecciones de la ADR 0002 siguen en `core/src/tradvia/work/` como biblioteca.
- Los READMEs y el perfil de la organización siguen este posicionamiento.
