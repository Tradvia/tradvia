<!-- tradvia-translation source="docs/programmatic-api.md" source-sha256="ceb1692b75dc408960bd37f54ab8f0c103f9897745054fc5b1e3c2a7f68ece0a" -->
# API programática

Crie e inicie cards sem o painel web, a partir de scripts, do CI ou da CLI `tradvia`.
Um card criado assim é uma issue normal do GitHub: ele continua passando pelo planner
e uma pessoa continua aprovando o plano. Relacionado: [#76](https://github.com/Tradvia/tradvia/issues/76),
desenho na [ADR 0009](adr/0009-programmatic-activity-api.md).

| Funcionalidade | Status |
| --- | --- |
| Tokens de API (criar, revogar, validade, um projeto e um papel cada) | Shipped |
| Criar um card com um token | Shipped |
| Iniciar um card (`agent:ready`) com um token | Shipped |
| CLI `tradvia token` e `tradvia card` | Shipped |
| Aprovar planos, fazer merge ou deploy com um token | Não disponível (só pelo painel) |
| Ferramentas de agente por MCP: ler cards, criar, iniciar, ajustar um plano ([mcp.md](mcp.md)) | In progress (testes offline) |
| Tarefa rápida sem plano nem aprovação | Planned |

É preciso ter o login com GitHub configurado ([github-app.md](github-app.md)).

## Como um token se comporta

- Ele age como o seu dono, com a identidade do dono no GitHub. É limitado a um projeto
  e ao papel escolhido na criação, e nunca passa do papel atual do dono.
- Ele expira (padrão 30 dias, no máximo 90) e pode ser revogado a qualquer momento.
- Ele para de funcionar quando o token de login do dono no GitHub expira; entre de novo e
  crie outro.
- Só um hash é guardado. O token é mostrado uma única vez.
- As ações aparecem na auditoria do card (`GET /api/projects/{owner}/{repo}/cards/{n}/audit`)
  com o dono como autor e `via: "api_token"`.

## curl

Crie um token com login (cookie de sessão do navegador e o valor CSRF de
`/api/auth/me`):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/tokens \
  -H "Cookie: __Host-tradvia_session=$SESSION" \
  -H "Origin: https://app.tradvia.org" -H "X-Tradvia-CSRF: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"role": "triage", "expires_in_days": 30}'
```

Crie e inicie um card com o token (sem cabeçalho Origin nem CSRF):

```bash
curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards \
  -H "Authorization: Bearer $TRADVIA_TOKEN" \
  -H "X-Tradvia-Idempotency-Key: $(uuidgen | tr -d -)" \
  -H "Content-Type: application/json" \
  -d '{"title": "feat(core): ...", "description": "..."}'

curl -X POST https://app.tradvia.org/api/projects/Tradvia/tradvia/cards/123/start \
  -H "Authorization: Bearer $TRADVIA_TOKEN"
```

Revogar: `DELETE /api/projects/{owner}/{repo}/tokens/{id}` com os cabeçalhos da sessão.

Erros: 401 token desconhecido, expirado ou revogado; 403 papel insuficiente, outro projeto ou uma
ação que tokens não podem fazer.

## CLI

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # valor do cookie __Host-tradvia_session (só para gerenciar tokens)

tradvia token create --project Tradvia/tradvia --role triage --days 30   # mostra o token uma única vez
tradvia token revoke --project Tradvia/tradvia 3

export TRADVIA_TOKEN=tvt_...
tradvia card create --project Tradvia/tradvia --title "feat(core): ..." --body "..." [--start]
```
