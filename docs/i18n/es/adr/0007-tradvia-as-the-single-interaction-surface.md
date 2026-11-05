<!-- tradvia-translation source="docs/adr/0007-tradvia-as-the-single-interaction-surface.md" source-sha256="e7ae6f5a321e5920e7e65c34a56d6b1741c2fe9bbf13f6d961f5b44ee6f7c9bf" -->
# ADR 0007: Tradvia como la única superficie de interacción

- Estado: Aceptada
- Fecha: 2026-10-20

## Contexto

Hoy el dueño maneja Tradvia desde cuatro lugares: un chat de IA que hace de
orquestador (crea cards, los inicia, revisa planes y pull requests),
las issues de GitHub (aprobar planes), los pull requests de GitHub (hacer merge) y GitHub
Actions (aprobar deploys). El 2026-10-20 esa división costó varias horas: las aprobaciones
de plan escritas en el chat de IA nunca llegaron al agente, dos cards agotaron el tiempo
esperando una respuesta en GitHub, y un deploy que esperaba aprobación bloqueó la
cola durante un día.

El objetivo es que toda interacción ocurra en Tradvia. Los proveedores de IA (Anthropic,
Google, OpenAI y otros) y los alojamientos de repositorios (GitHub, GitLab) se configuran
una vez y luego se usan a través de Tradvia; nadie necesita abrir sus pantallas para llevar un
proyecto.

## Decisión

1. **Una pantalla para cada punto de decisión humana.** Crear, ordenar e iniciar cards,
   responder planes (Aprovado / Rejeitado / Ajuste), revisar y hacer merge de pull
   requests, aprobar o rechazar deploys y ejecutar operaciones posteriores al deploy
   son acciones en Tradvia, ejecutadas con la propia identidad de la persona con sesión en
   el proveedor, para que el historial del proveedor siga mostrando quién hizo qué (ADR 0004).
2. **El orquestador es un agente de Tradvia.** La conversación que planifica el
   backlog, inicia cards, propone ajustes y revisa pull requests pasa
   a una conversación por proyecto en Tradvia. Usa un conjunto cerrado de herramientas
   de Tradvia. Nunca aprueba planes, hace merge ni deploy: esos puntos siguen siendo humanos.
3. **Los proveedores son conexiones.** Los proveedores de IA y los alojamientos de repositorios se
   configuran por proyecto en Tradvia, con secretos cifrados en reposo y nunca
   devueltos al navegador. Los agentes de código (Claude Code, Gemini CLI, Codex CLI)
   son adaptadores detrás de una interfaz; los alojamientos de repositorios (GitHub, GitLab) son
   adaptadores detrás de otra. Un proveedor solo figura como soportado cuando se entrega su
   adaptador; las herramientas sin CLI o API no interactiva estable siguen en Planned.
4. **La espera es duradera.** Un card que espera a una persona se detiene y se reanuda cuando
   llega la respuesta, por tarde que sea (ADR 0006, ejecución duradera), en lugar de
   agotar el tiempo después de una hora.
5. **A las personas se les avisa, no se les consulta.** Una bandeja de pendientes y notificaciones web push llevan
   al dueño a la acción pendiente exacta.

## Orden de entrega y estado

| Orden | Card | Funcionalidad | Estado |
|---|---|---|---|
| 1 | #45 | Inicio de sesión con GitHub, sesiones, roles por proyecto | Shipped |
| 2 | #46 | Crear, iniciar, ordenar y responder cards en Tradvia | Shipped |
| 3 | #56 | Revisar y hacer merge de PR, aprobar deploys en Tradvia | Partial |
| 4 | #40 | Espera duradera y reanudación | Planned |
| 5 | #47 | Bandeja de pendientes y notificaciones web push | Planned |
| 6 | #57 | Conversación con el orquestador dentro de Tradvia | Partial |
| 7 | #58 | Conexiones del proyecto para proveedores de IA y de repositorios | Partial |
| 8 | #59 | Adaptadores de agentes de código (Gemini CLI, Codex CLI) | In progress |
| 9 | #60 | GitLab como proveedor de repositorios | Planned |

Ya entregados y reutilizados: alcance por proyecto (#44), backlog en Tradvia (#50),
lecturas del panel desde el store (#36), operaciones en el momento del deploy (#54).

## Pull requests y deploys (#56)

Estado: **Partial** (implementado y probado offline; producción necesita los
permisos de la GitHub App de `docs/github-app.md`).

- El card muestra el PR y sus checks, leídos con el token de la persona. El merge
  es un squash con el título Conventional Commit del PR, seguido del borrado del
  branch. Un check obligatorio fallido (`core-tests`, `license-boundary`, `cla`)
  rechaza el merge; solo `cla` se puede ignorar, de forma explícita, y el registro de auditoría
  lo guarda.
- Hacer merge, actualizar un branch, aprobar o rechazar un deploy, cancelar un
  deploy reemplazado y ejecutar la operación posterior al deploy son nuevas
  interacciones de `.tradvia.yml` (`merge_pr`, `update_branch`, `approve_deploy`),
  todas con `maintain` por defecto. Ser revisor del entorno `production`
  lo sigue decidiendo GitHub, con el token de la persona.
- Un deploy en espera superado por uno más nuevo en la misma cola se muestra como
  "reemplazado" y es el único tipo que el panel puede cancelar.
- La operación posterior al deploy dispara `deploy-oci` con `backfill` y
  `backlog` (#54); el backlog viene completado con el orden guardado en Tradvia.

## Consecuencias

- La configuración inicial sigue haciéndose en los proveedores: registrar la GitHub App
  de Tradvia (y más adelante una app OAuth de GitLab) y hacer al dueño revisor del
  entorno `production`. Después de eso, el trabajo diario no necesita ninguna otra
  pantalla.
- Tradvia guarda tokens de personas y secretos de proveedores: el cifrado en reposo, CSRF,
  las sesiones cortas y un registro de auditoría son requisitos del primer card (#45), no
  un refuerzo para más adelante.
- Hasta que se entregue el #46, las aprobaciones de plan siguen ocurriendo en la conversación de la issue.

## Anexo: la conversación con el orquestador (#57)

El agente orquestador que conversa con el dueño dentro de Tradvia trabaja con un conjunto cerrado de
herramientas de Tradvia, nunca con un shell libre. El límite de las herramientas sigue la ADR 0004: aprobar un plan,
hacer merge y deploy no tienen herramienta, así que esas decisiones quedan con la persona; el orquestador
solo puede responder a un plan con un ajuste y dejar en cola un pendiente con un botón. Ver
[docs/orchestrator.md](../orchestrator.md).
