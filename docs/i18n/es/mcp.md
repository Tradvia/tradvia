<!-- tradvia-translation source="docs/mcp.md" source-sha256="846fe34a4f3c860c3fe58b1fe291b844f116c57c2ffe26e7efe3986c8ac61169" -->
# MCP de Tradvia para agentes

Estado: **In progress**. Probado offline con HTTP y clientes simulados, y ejecutado una vez contra
una API local de Tradvia (store en memoria, GitHub simulado): la CLI de Claude Code 2.1.312 se conecta a
`tradvia mcp` (`claude mcp list`: Connected), y un cliente stdio con guion y `curl` sobre el endpoint
HTTP listan las herramientas, crean y leen un card, y reciben el rechazo de un merge, que queda en cola para una
persona. Aún no se ejecutó contra producción, ni con Kiro, Gemini CLI, Antigravity o Manus. Ver la [ADR 0012](adr/0012-agent-communication-git-and-mcp.md).

Los agentes leen cards, los inician o crean, y responden a un plan con un ajuste, con las
mismas herramientas que usa el orquestador. El código pasa por git como siempre: un branch y un pull request
que cierra el card.

## Herramientas

| Herramienta | Qué hace | Notas |
|---|---|---|
| `read_backlog` | El backlog en orden | |
| `read_cards` | Cards del proyecto | `state`: open, closed o all |
| `read_card` | Un card con su descripción | `number` |
| `read_plan` | El plan de un card que espera respuesta | `number` |
| `read_pulls`, `read_pull`, `read_checks` | Pull requests y sus checks | |
| `read_deploys` | Deploys que esperan aprobación | |
| `create_card` | Crea un card al final del backlog | `title`, `description` |
| `start_card` | Inicia un card (agrega `agent:ready`) | `number` |
| `reply_plan` | Responde a un plan con un ajuste | `decision` debe ser `adjust`; requiere el rol `write` |
| `read_briefing` | Empieza aquí: cómo funciona el proyecto, `AGENTS.md`, cards libres, quién tiene qué card con qué agente, qué espera a una persona | |
| `claim_card` | Toma un card libre para ti y tu agente; el agente propio de Tradvia no lo ejecutará | `number`, `agent`, `provider`, `model`, `skills`; `agent` toma por defecto el nombre del cliente MCP |
| `release_card` | Devuelve un card que tienes | `number`, `note` |
| `comment_card` | Avance, una pregunta o un bloqueo, en el card | `number`, `text` |

Trabajar con otros: un agente trabaja **con** personas y otros agentes, no solo. El ciclo está en
[AGENTS.md](../AGENTS.md): `read_briefing`, `claim_card`, código en `tradvia/issue-<N>`,
`comment_card`, un pull request con `Closes #<N>` y `release_card` si se detiene. Tomar un card es
la etiqueta `agent:claimed` más un comentario en el card con la persona, el agente, su proveedor,
el modelo y las especialidades. Solo un comentario escrito por esa persona cuenta como suyo, así que el texto
no puede falsificarlo otra persona, y un card que tiene alguien no lo puede tomar ni iniciar
otra persona hasta que se devuelva.

No disponible para los agentes:

- **Aprobar o rechazar un plan, hacer merge, aprobar o rechazar un deploy.** La llamada se rechaza y un
  pendiente queda en cola para la persona en el panel.
- **`review_pr`.** Ejecuta `make check` en el servidor, sobre el código de un pull request.

## Obtener un token

Un token pertenece a un proyecto y a un rol, y vence (30 días por defecto, como máximo 90).
Se muestra una sola vez.

**En el panel:** inicia sesión, abre el proyecto y usa **Conectar mi IA** ("Connect my AI") al
final del Panel. Elige el rol y cuántos días, crea el token y cópialo:
se muestra una sola vez. La misma pantalla trae la configuración MCP para pegar en tu cliente, con el
proyecto y la dirección completados, y lista tus tokens en este proyecto con un botón Revocar
(nunca sus valores). `GET /api/projects/{owner}/{repo}/tokens` devuelve esa lista, solo con
una sesión iniciada.

Desde un terminal, con la cookie de sesión:

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # valor de la cookie __Host-tradvia_session de tu navegador con sesión
tradvia token create --project owner/repo --role triage --days 30
```

Roles: `triage` (por defecto) lee, crea e inicia cards; `write` también ajusta planes.
Un token `read` no puede usar las herramientas de agente, porque las lecturas requieren el mismo rol que iniciar un
card. Un token nunca tiene más poder que el que su dueño tiene en el proyecto.

Mantén el token fuera de git. Ponlo en `TRADVIA_TOKEN` o en el almacén de secretos de tu cliente.
Revócalo con `tradvia token revoke --project owner/repo ID`.

## Gestor IA (solo admins)

Un admin puede crear un token de tipo **gestor IA** ("AI manager") en la pantalla Conectar mi IA
([ADR 0014](adr/0014-ai-manager-below-the-owner.md)). Trabaja por debajo del dueño:

- **Base:** todo lo que un agente hace aquí: leer todo, comentar, crear, iniciar y tomar
  cards, ajustar planes. Su `read_briefing` dice que es un gestor.
- **Solo con tu permiso, por un tiempo definido:** aprobar o rechazar planes (`reply_plan` con `approve`
  o `reject`) y reordenar el backlog (`reorder_backlog`). Concede y revoca cada uno en la misma
  pantalla; un permiso dura como máximo 90 días y nunca más que el token, y la revocación rige desde
  la llamada siguiente.
- **Nunca:** merge, deploy, presupuesto, conexiones, roles o invitaciones. Esos siguen siendo pendientes
  para una persona.

Desde un terminal: `POST /api/projects/{owner}/{repo}/tokens` con `{"kind": "manager"}`, luego
`POST .../tokens/{id}/grants` con `{"permission": "approve_plan", "expires_in_days": 7}` y
`DELETE .../tokens/{id}/grants/{grant_id}`, todo con una sesión de admin iniciada.

## Opción A: stdio (un cliente en tu máquina)

Instala el comando `tradvia` desde un checkout de este repositorio:

```bash
pip install ./core
```

El servidor lee tres variables: `TRADVIA_URL`, `TRADVIA_PROJECT` (`owner/repo`) y
`TRADVIA_TOKEN`. Se ejecuta con `tradvia mcp`. Su salida estándar es el canal del protocolo, así que
no escribe nada más ahí.

### Claude Code

Archivo de proyecto `.mcp.json` en la raíz del repositorio. Claude Code expande `${VAR}` y te pide
aprobar un servidor de proyecto antes del primer uso (verificado en la documentación de Claude Code):

```json
{
  "mcpServers": {
    "tradvia": {
      "command": "tradvia",
      "args": ["mcp"],
      "env": {
        "TRADVIA_URL": "https://app.tradvia.org",
        "TRADVIA_PROJECT": "owner/repo",
        "TRADVIA_TOKEN": "${TRADVIA_TOKEN}"
      }
    }
  }
}
```

### Kiro CLI

Archivo `.kiro/settings/mcp.json` en el repositorio, o `~/.kiro/settings/mcp.json` para todos
los proyectos (verificado en la documentación de Kiro). Usa el mismo bloque `mcpServers` de arriba. Dos pasos:

- Aprueba `TRADVIA_TOKEN` en **Mcp Approved Env Vars**. Kiro solo expande una variable después
  de esa aprobación.
- Lista en `autoApprove` las herramientas que aceptas sin confirmación. La documentación no dice qué hace una
  ejecución no interactiva con una herramienta no aprobada; pruébalo antes de confiar en
  Kiro sin interfaz.

### Gemini CLI

Archivo `~/.gemini/settings.json`, o `.gemini/settings.json` para un proyecto (verificado en la
documentación de Gemini CLI). `includeTools` lista las herramientas que el agente puede usar. Deja `trust` sin definir:
`trust: true` omite todas las confirmaciones para el servidor.

```json
{
  "mcpServers": {
    "tradvia": {
      "command": "tradvia",
      "args": ["mcp"],
      "env": {
        "TRADVIA_URL": "https://app.tradvia.org",
        "TRADVIA_PROJECT": "owner/repo",
        "TRADVIA_TOKEN": "$TRADVIA_TOKEN"
      },
      "includeTools": ["read_briefing", "read_cards", "read_card", "read_plan", "claim_card", "comment_card", "release_card", "create_card", "reply_plan"]
    }
  }
}
```

### Antigravity

No verificado. Un artículo de Google Cloud describe `~/.gemini/config/mcp_config.json`, con
`serverUrl` y `headers` para servidores remotos. El mismo artículo informa que los valores de `env`
no llegaron al proceso en Antigravity. Prefiere la Opción B y pruébala antes de confiar en ella.

## Opción B: HTTP (agentes en la nube, como Manus)

El endpoint recibe JSON-RPC por `POST`, con el mismo token bearer:

```
https://app.tradvia.org/api/projects/owner/repo/mcp
Authorization: Bearer <token>
```

Manus acepta un servidor MCP personalizado que sea un endpoint HTTPS remoto con clave de API o token
bearer (verificado en la documentación de Manus). Aún no se probó con Manus.

Comprueba el endpoint a mano:

```bash
curl -s https://app.tradvia.org/api/projects/owner/repo/mcp \
  -H "Authorization: Bearer $TRADVIA_TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Qué recibe el agente

- Una herramienta que funciona devuelve su resultado como texto JSON.
- Un rechazo (una acción solo para personas, una herramienta no disponible, un rol insuficiente, un card en el
  estado equivocado) devuelve `isError: true` con el motivo. No es un error de protocolo.
- Los errores nunca llevan el token. El cliente lo reemplaza por `[token]`.

Cada llamada se audita con el dueño del token como autor y `via: api_token`. Los rechazos también
se auditan.

## Límites

- Las respuestas son JSON. No hay flujo de eventos, ni recursos ni prompts MCP.
- Los agentes todavía no pueden editar un card ni reordenar el backlog.
- Los agentes actúan con la identidad de GitHub del dueño del token. Las identidades propias para agentes están planificadas.
- Reintentos: envía `X-Tradvia-Idempotency-Key` con una llamada de escritura (clientes HTTP). El panel
  ignora una repetición exacta de la misma clave dentro de cinco segundos. Sin clave, un
  `create_card` reintentado crea un segundo card.
