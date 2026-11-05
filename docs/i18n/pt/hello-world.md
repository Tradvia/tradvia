<!-- tradvia-translation source="docs/hello-world.md" source-sha256="31e87c7fdaf473725da90e0bff6fb49f43026330c1851a26f54e5389c7602f8d" -->
# Hello World: o primeiro fluxo híbrido

O menor ciclo de ponta a ponta do Tradvia v0.1:

```
issue no GitHub ──► @Agent-Architect planeja (LiteLLM)
                     │
                     ▼
             pergunta no Discord ──► card WAITING_HUMAN (execução pausada)
                     │
          você responde "Aprovado" / "Rejeitado" / um pedido de ajuste
                     │
                     ▼
             @Agent-Coder: branch ─► commit ─► pull request ──► card IN_REVIEW_PR
```

Os status seguem [`core/contracts/card-state.v1.json`](../core/contracts/card-state.v1.json).
A pausa usa o `interrupt()` do LangGraph, então a execução retoma exatamente de onde parou.

## Preparação (uma vez)

1. **Token do GitHub**: Settings → Developer settings → Fine-grained tokens → Generate.
   Resource owner `Tradvia`, só o repositório `Tradvia/tradvia`, permissões
   *Contents*, *Pull requests* e *Issues*: Read and write.
2. **Bot do Discord**: [Developer Portal](https://discord.com/developers/applications) →
   New Application → Bot → Reset Token (copie) → ative **Message Content Intent**.
   OAuth2 → URL Generator → escopo `bot`, permissões *Send Messages* e
   *Read Message History* → abra a URL e adicione o bot ao seu servidor.
3. **ID do canal**: Discord → User Settings → Advanced → Developer Mode ligado;
   clique com o botão direito no canal → Copy Channel ID.
4. **Chave do modelo**: uma chave de API do provedor em `TRADVIA_PLANNER_MODEL`
   (padrão Anthropic → `ANTHROPIC_API_KEY`).

```bash
cd core
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # preencha os valores
```

## Executar

1. Abra uma issue em `Tradvia/tradvia`, por exemplo *"Add a hello world note"*.
2. A partir de `core/`:

   ```bash
   tradvia --issue 1
   ```

3. Responda no Discord: **Aprovado** abre o PR, **Rejeitado** devolve o card
   para `BACKLOG`, e qualquer outra resposta é tratada como ajuste e o Architect planeja
   de novo (até 3 vezes).

Use `TRADVIA_OFFLINE=1` para uma primeira execução sem nenhuma chamada a LLM.

## Proteções

- Agentes nunca fazem merge. O PR passa pela revisão normal do branch protegido.
- Agentes não podem escrever em `.github/`, `.git/`, `LICENSE.md`, `CLA.md` ou `CODEOWNERS`,
  nem fora do repositório.
- Mudanças de status que quebram o contrato do card geram erro.

## Testes

```bash
cd core && pytest -q
```

GitHub, Discord e o modelo são substituídos por simulações, então os testes não precisam de tokens.
