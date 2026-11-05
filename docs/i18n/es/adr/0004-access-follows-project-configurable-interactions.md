<!-- tradvia-translation source="docs/adr/0004-access-follows-project-configurable-interactions.md" source-sha256="c62e7a3e3afb6bd5bc984bf50e4220c9697a8460d54264305ae5bf30c7aa1b4e" -->
# ADR 0004: El acceso sigue al proyecto; las interacciones son configurables

- Estado: Aceptada
- Fecha: 2026-10-19

## Contexto

El primer panel de ejecuciones se publicó en `tradvia.org/runs`. Lee datos públicos
de GitHub sin inicio de sesión. Eso planteó la pregunta de quién debería ver el
panel y quién debería poder actuar a través de Tradvia: iniciar un card,
aprobar un plan, llamar a un asistente o, más adelante, tomar una sesión en vivo.

Los datos detrás del panel vienen del propio proyecto (issues, pull
requests, ejecuciones de workflow). Ocultarlos en Tradvia cuando el proyecto es público
no protege nada. Mostrarlos en Tradvia cuando el proyecto es privado
los filtraría.

## Decisión

### 1. La visualización sigue la visibilidad del proyecto

| Proyecto | Quién puede ver cards, ejecuciones y KPIs |
|---|---|
| Público | Cualquiera, solo lectura, sin inicio de sesión |
| Privado | Solo personas con sesión iniciada en GitHub que tengan al menos `read` en el repositorio |

Tradvia nunca muestra más de lo que muestra el sistema de origen. Un proyecto puede
**restringir** aún más la visualización (por ejemplo, exigir `read` incluso en un repositorio
público), pero la configuración nunca puede **ampliarla**.

### 2. Las interacciones tienen un rol mínimo configurable

Todo lo que cambia el estado es una *interacción*. Cada una tiene un rol mínimo
de GitHub, configurado por proyecto. Primero en `.tradvia.yml` en la raíz del repositorio; desde
la ADR 0018, en Tradvia (Configuración, Roles y riesgo del proyecto), y la sección del archivo se ignora.
Los roles siguen el orden de GitHub: `read < triage < write < maintain < admin`.
La identidad es siempre la cuenta de GitHub de la persona. Tradvia no guarda usuarios ni
contraseñas propios.

| Interacción | Qué permite | Por defecto | Estado |
|---|---|---|---|
| `start_card` | Iniciar un card (hoy, la etiqueta `agent:ready`) | `triage` | Partial (GitHub exige `triage` para las etiquetas) |
| `approve_plan` | Responder a un plan: aprobar, rechazar o ajustar | `write` | Partial (fijo en el código hoy) |
| `mention_assistant` | Llamar a `@claude` en issues y pull requests | `write` | Partial (lo aplica la action) |
| `live_takeover` | Tomar una sesión de navegador o terminal congelada | `maintain` | Planned |
| `budget_control` | Botón de parada y límites de presupuesto | `admin` | Planned |

Ejemplo:

```yaml
# .tradvia.yml
access:
  view: inherit            # inherit | read | triage | write | maintain | admin
interactions:
  start_card: triage
  approve_plan: write
  mention_assistant: write
  live_takeover: maintain
  budget_control: admin
budget:                    # per-card execution limits (ADR 0006)
  max_minutes: 30
  max_steps: 40
  max_usd: 5.0
```

La sección `interactions` de arriba, y la sección `risk` de la ADR 0006, fueron reemplazadas de la misma
forma por la ADR 0018: son la configuración del proyecto en Tradvia, que solo cambia un admin.

`.tradvia.yml` también tenía una sección `budget` (presupuestos de ejecución por card, ADR 0006).
Reemplazada por la ADR 0018: los presupuestos ahora son una configuración del proyecto en Tradvia (Configuración, Presupuesto
del proyecto), que cambia quien tiene `budget_control`; una sección `budget` en el archivo se ignora.

### 3. Invariantes de seguridad

1. Ninguna interacción se puede configurar por debajo de `read`: los usuarios anónimos nunca actúan.
2. Los bots nunca cuentan como aprobadores, sea cual sea su rol.
3. Los agentes nunca pueden editar `.tradvia.yml` (se suma a las rutas protegidas junto a
   `.github/`, las licencias y `CODEOWNERS`). Solo cambia mediante un pull request
   revisado.
4. Las claves desconocidas o los roles inválidos hacen que la política falle cerrada: Tradvia rechaza
   la interacción y explica por qué, en lugar de caer en un acceso abierto.
5. La política se lee del branch por defecto, nunca del branch de un pull request.

## Fases

1. **Política en el core (siguiente card):** modelo `Policy` y cargador con valores por defecto y
   validación; `approve_plan` y `start_card` aplicados con la API de permisos de
   colaboradores de GitHub (`role_name`); `.tradvia.yml` agregado a las
   rutas protegidas; un archivo de ejemplo en `docs/`.
2. **Conexión con el workflow (mantenedor):** pasar al agente quién aplicó la etiqueta, para que
   se pueda aplicar `start_card` por encima de `triage`; alinear `@claude` con
   `mention_assistant` cuando esté por encima de `write`.
3. **Panel:** mostrar la política efectiva ("quién puede hacer qué") en los proyectos
   públicos; inicio de sesión con GitHub para proyectos privados y para las acciones interactivas
   del panel, cuando existan.

## Consecuencias

- `tradvia.org/runs` sigue siendo público porque `Tradvia/tradvia` es público.
- Los proyectos privados de clientes necesitan inicio de sesión con GitHub antes de servir cualquier
  panel. Es un requisito obligatorio de la edición Cloud.
- Los admins deben agregar `/.tradvia.yml @Tradvia/admins` a `CODEOWNERS` cuando el
  archivo exista, para que los cambios de política requieran la revisión de un admin.
