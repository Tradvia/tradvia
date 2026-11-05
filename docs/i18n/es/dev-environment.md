<!-- tradvia-translation source="docs/dev-environment.md" source-sha256="15b86c50727a3360220cbd838ff6f02e0376e172a7de9713efd09cbf9c6a6dc1" -->
# Entorno de desarrollo

Tradvia se construye en público y, de ahora en adelante, en parte **por el propio Tradvia**.
Hay dos entornos:

| | Qué es | Quién lo usa | Costo |
|---|---|---|---|
| **Entorno de desarrollo** | GitHub Codespaces o un Dev Container local con Python 3.12, el paquete core y las pruebas listos | Toda persona que contribuye | Codespaces: tu cuota mensual gratuita personal; local: gratis |
| **Runtime de dogfooding** | El workflow `agent` de GitHub Actions ejecutando Tradvia sobre las issues de este repositorio | Los mantenedores lo inician; todos ven los resultados | Minutos gratuitos de Actions para repositorios públicos + tokens de LLM (el modo offline no cuesta nada) |

## 1. Abrir el entorno de desarrollo

**En línea (sin instalar nada):** en la página del repositorio haz clic en **Code → Codespaces →
Create codespace on main**, o usa la insignia del README. El contenedor ejecuta
`make setup`, así que después de unos dos minutos puedes ejecutar:

```bash
make test                 # core test suite, offline, no API keys
make check                # tests + license boundary, same as CI
make manual               # render docs/ into dashboard/manual/, as the deploy does
```

Para traducir una página del manual, escríbela en `docs/i18n/<idioma>/` con la misma ruta que
en `docs/` (por ejemplo `docs/i18n/pt/projects.md`), mantén los enlaces como están en el original
y luego ejecuta `PYTHONPATH=core/src python -m tradvia.manual stamp docs/i18n/pt/projects.md`.
`python -m tradvia.manual check` lista lo que falta o está desactualizado.

**Local:** instala Docker y VS Code con la extensión *Dev Containers*, clona
el repositorio y elige **Reopen in Container**. Sin Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make setup && make test
```

`TRADVIA_OFFLINE=1` viene definido por defecto en el contenedor, así que nada llama a un modelo
de pago a menos que lo cambies en `core/.env`.

## 2. Dejar que Tradvia trabaje en una issue (dogfooding)

1. Abre una issue que describa un cambio pequeño y autocontenido (un archivo nuevo en la v0.1).
2. Un mantenedor agrega la etiqueta **`agent:ready`**.
3. `@Agent-Architect` comenta un plan en la issue y el card pasa a
   `WAITING_HUMAN`.
4. Un mantenedor responde en la issue: **Aprovado** / **Approved**, **Rejeitado** /
   **Rejected**, o un ajuste (el agente vuelve a planificar, hasta 3 veces).
5. Con la aprobación, `@Agent-Coder` crea `tradvia/issue-<n>`, hace el commit del archivo y
   abre un pull request que pasa por la revisión normal.

Reglas de seguridad incorporadas al flujo:

- Solo quienes tienen acceso de triage pueden agregar etiquetas, y solo se leen las respuestas de
  `OWNER`, `MEMBER` o `COLLABORATOR`; los bots y las personas externas se ignoran.
- Los agentes nunca pueden escribir en `.github/`, `.git/`, `LICENSE.md`, `CLA.md` o
  `CODEOWNERS`, y todo cambio sigue necesitando un pull request revisado.
- Sin el secreto de repositorio `ANTHROPIC_API_KEY`, la ejecución usa el planner
  offline, así que todo el ciclo se puede probar a costo cero.

Para usar un modelo real, un mantenedor agrega el secreto `ANTHROPIC_API_KEY` en
**Settings → Secrets and variables → Actions** y, opcionalmente, la variable
`TRADVIA_PLANNER_MODEL` (cualquier nombre de modelo de LiteLLM).

Limitación conocida de la v0.1: los pull requests abiertos con el `GITHUB_TOKEN` del workflow
no disparan otros workflows, así que un mantenedor vuelve a ejecutar el CI en el PR del agente
(cerrándolo y reabriéndolo). Un token de GitHub App elimina esto en la v0.2.

## 3. Ejecutar el flujo desde tu entorno

```bash
cp core/.env.example core/.env    # fill GITHUB_TOKEN and TRADVIA_REPO for your fork
cd core && TRADVIA_OFFLINE=1 tradvia --issue <n> --chat github
```

Usa tu **fork** como `TRADVIA_REPO` mientras experimentas, para que los branches y
pull requests de prueba queden fuera del repositorio principal.

`TRADVIA_PROJECTS` (separado por comas, por defecto `TRADVIA_REPO`) lista los repositorios
que la API guarda y sirve. Carga uno con `tradvia backfill --project owner/repo`
(debe estar en `TRADVIA_PROJECTS`). Ver `docs/projects.md`.

## 4. CLI de Claude Code como planner y executor (suscripción, sin clave de API)

Dos variables de entorno opcionales eligen quién planifica y quién implementa. Ambas
tienen como valor por defecto el comportamiento actual, así que nada cambia a menos que las definas.

| Variable | Valores | Por defecto |
|---|---|---|
| `TRADVIA_PLANNER` | `claude-code` (`claude -p`, herramientas de solo lectura), `litellm`, `offline` | `litellm` (offline cuando `TRADVIA_OFFLINE=1`) |
| `TRADVIA_EXECUTOR` | `claude-code` (edita el árbol de trabajo), `note` (un archivo por la API) | `note` |

Con `claude-code`, la CLI debe estar instalada y en el `PATH`, autenticada con
`CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`); no hace falta `ANTHROPIC_API_KEY`.
`TRADVIA_CLAUDE_TIMEOUT` (segundos, por defecto 900) limita cada llamada.
`TRADVIA_EXEC_MAX_TURNS` (entero positivo, por defecto 80) limita los turnos del executor y
`TRADVIA_PLAN_MAX_TURNS` (por defecto 30) los del planner; un valor inválido vuelve al
valor por defecto. Un fallo del planner se anuncia de la misma forma: no se publica ningún plan y el card queda en
`WAITING_HUMAN`. Si el executor falla (límite de turnos, tiempo agotado o
error de la CLI), el agente comenta en la issue, no abre PR y el card queda en
`WAITING_HUMAN`; reinícialo después de ajustar o dividir la issue.

`TRADVIA_MAX_REPLANS` (entero positivo, por defecto 3) limita cuántos ajustes de plan acepta un
card. En el límite, el agente comenta en la issue, pide que se actualice la descripción
y se reinicie el card (quita y vuelve a poner `agent:ready`), y el card queda
en `WAITING_HUMAN`.

Después de la aprobación, Claude Code implementa el plan en el directorio actual (empieza desde un
checkout limpio: cada archivo modificado entra en el PR). Tradvia entonces hace el commit de todos los archivos
modificados en `tradvia/issue-<n>` y abre el PR con el resumen, el costo y el tiempo de Claude. Si
algún archivo modificado está en `.github/` o `.git/`, o es `LICENSE.md`, `CLA.md` o
`CODEOWNERS`, no se abre ningún PR y el card vuelve a `WAITING_HUMAN`.

Estado: la integración de la CLI está **Partial**. Los archivos borrados no se propagan al PR, y
el workflow `agent.yml` todavía no instala la CLI (lo hace un mantenedor).

## 5. Política de acceso (`.tradvia.yml`)

Estado: **Partial** (ADR 0004, fase 1). Ver `docs/tradvia.example.yml`.

- El archivo vive en la raíz del repositorio y se lee **solo del branch por defecto**
  (`TRADVIA_BASE_BRANCH`), nunca del árbol de trabajo ni de un branch de pull request.
  Los agentes no pueden editarlo. Sin el archivo, se aplican los valores por defecto.
- Falla cerrado: una clave desconocida o un rol inválido impiden que el card empiece, y
  el motivo se publica en la issue. Nada se puede configurar por debajo de `read`.
- Los roles siguen a GitHub: `read < triage < write < maintain < admin`, tomados de la
  API de permisos de colaboradores (`role_name`). Quien no es colaborador no tiene rol, y los bots
  nunca cuentan.
- Valores por defecto: `start_card: triage`, `approve_plan: write`, `mention_assistant: write`,
  `live_takeover: maintain`, `budget_control: admin`.
- Aplicado hoy: `approve_plan` (quién puede responder a un plan) y `start_card`, que se
  verifica solo cuando `TRADVIA_ACTOR` (el login que aplicó la etiqueta) está definido.
- Planned: que el workflow pase `TRADVIA_ACTOR` (fase 2, lo hace un mantenedor).
  `mention_assistant`, `live_takeover` y `budget_control` se leen, pero el core todavía no
  los aplica.
