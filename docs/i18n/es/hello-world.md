<!-- tradvia-translation source="docs/hello-world.md" source-sha256="31e87c7fdaf473725da90e0bff6fb49f43026330c1851a26f54e5389c7602f8d" -->
# Hello World: el primer flujo híbrido

El ciclo de punta a punta más pequeño de Tradvia v0.1:

```
issue en GitHub ──► @Agent-Architect planifica (LiteLLM)
                     │
                     ▼
             pregunta en Discord ──► card WAITING_HUMAN (ejecución en pausa)
                     │
          respondes "Aprovado" / "Rejeitado" / un pedido de ajuste
                     │
                     ▼
             @Agent-Coder: branch ─► commit ─► pull request ──► card IN_REVIEW_PR
```

Los estados siguen [`core/contracts/card-state.v1.json`](../core/contracts/card-state.v1.json).
La pausa usa el `interrupt()` de LangGraph, así que la ejecución se reanuda exactamente donde se detuvo.

## Preparación (una vez)

1. **Token de GitHub**: Settings → Developer settings → Fine-grained tokens → Generate.
   Resource owner `Tradvia`, solo el repositorio `Tradvia/tradvia`, permisos
   *Contents*, *Pull requests* e *Issues*: Read and write.
2. **Bot de Discord**: [Developer Portal](https://discord.com/developers/applications) →
   New Application → Bot → Reset Token (cópialo) → activa **Message Content Intent**.
   OAuth2 → URL Generator → alcance `bot`, permisos *Send Messages* y
   *Read Message History* → abre la URL y agrega el bot a tu servidor.
3. **ID del canal**: Discord → User Settings → Advanced → Developer Mode activado;
   clic derecho en el canal → Copy Channel ID.
4. **Clave del modelo**: una clave de API del proveedor en `TRADVIA_PLANNER_MODEL`
   (por defecto Anthropic → `ANTHROPIC_API_KEY`).

```bash
cd core
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # completa los valores
```

## Ejecutar

1. Abre una issue en `Tradvia/tradvia`, por ejemplo *"Add a hello world note"*.
2. Desde `core/`:

   ```bash
   tradvia --issue 1
   ```

3. Responde en Discord: **Aprovado** abre el PR, **Rejeitado** devuelve el card
   a `BACKLOG`, y cualquier otra respuesta se toma como ajuste y el Architect vuelve a
   planificar (hasta 3 veces).

Usa `TRADVIA_OFFLINE=1` para una primera ejecución sin ninguna llamada a un LLM.

## Protecciones

- Los agentes nunca hacen merge. El PR pasa por la revisión normal del branch protegido.
- Los agentes no pueden escribir en `.github/`, `.git/`, `LICENSE.md`, `CLA.md` o `CODEOWNERS`,
  ni fuera del repositorio.
- Los cambios de estado que rompen el contrato del card generan un error.

## Pruebas

```bash
cd core && pytest -q
```

GitHub, Discord y el modelo se reemplazan por simulaciones, así que las pruebas no necesitan tokens.
