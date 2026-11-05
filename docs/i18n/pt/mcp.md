<!-- tradvia-translation source="docs/mcp.md" source-sha256="846fe34a4f3c860c3fe58b1fe291b844f116c57c2ffe26e7efe3986c8ac61169" -->
# MCP do Tradvia para agentes

Status: **In progress**. Testado offline com HTTP e clientes simulados, e executado uma vez contra
uma API local do Tradvia (store em memória, GitHub simulado): a CLI do Claude Code 2.1.312 conecta em
`tradvia mcp` (`claude mcp list`: Connected), e um cliente stdio roteirizado e o `curl` no endpoint
HTTP listam as ferramentas, criam e leem um card, e recebem a recusa de um merge, que fica na fila para uma
pessoa. Ainda não rodou contra a produção, nem com Kiro, Gemini CLI, Antigravity ou Manus. Veja a [ADR 0012](adr/0012-agent-communication-git-and-mcp.md).

Os agentes leem cards, iniciam ou criam cards e respondem a um plano com um ajuste, pelas
mesmas ferramentas que o orquestrador usa. O código passa pelo git como sempre: um branch e um pull request
que fecha o card.

## Ferramentas

| Ferramenta | O que faz | Observações |
|---|---|---|
| `read_backlog` | O backlog em ordem | |
| `read_cards` | Cards do projeto | `state`: open, closed ou all |
| `read_card` | Um card com a descrição | `number` |
| `read_plan` | O plano de um card que espera resposta | `number` |
| `read_pulls`, `read_pull`, `read_checks` | Pull requests e seus checks | |
| `read_deploys` | Deploys esperando aprovação | |
| `create_card` | Cria um card no fim do backlog | `title`, `description` |
| `start_card` | Inicia um card (adiciona `agent:ready`) | `number` |
| `reply_plan` | Responde a um plano com um ajuste | `decision` precisa ser `adjust`; exige o papel `write` |
| `read_briefing` | Comece aqui: como o projeto funciona, o `AGENTS.md`, cards livres, quem está com qual card e com qual agente, o que espera uma pessoa | |
| `claim_card` | Pega um card livre para você e o seu agente; o agente do próprio Tradvia não vai executá-lo | `number`, `agent`, `provider`, `model`, `skills`; `agent` tem como padrão o nome do cliente MCP |
| `release_card` | Devolve um card que está com você | `number`, `note` |
| `comment_card` | Progresso, uma pergunta ou um bloqueio, no card | `number`, `text` |

Trabalhar com os outros: um agente trabalha **com** pessoas e outros agentes, não sozinho. O ciclo está no
[AGENTS.md](../AGENTS.md): `read_briefing`, `claim_card`, código em `tradvia/issue-<N>`,
`comment_card`, um pull request com `Closes #<N>` e `release_card` se parar. Pegar um card é
a label `agent:claimed` mais um comentário no card com a pessoa, o agente, o provedor dele,
o modelo e as especialidades. Só um comentário escrito por aquela pessoa vale como dela, então o texto
não pode ser forjado por outra pessoa, e um card que está com alguém não pode ser pego nem iniciado por
outra pessoa até ser devolvido.

Não disponível para agentes:

- **Aprovar ou rejeitar um plano, fazer merge, aprovar ou rejeitar um deploy.** A chamada é recusada e uma
  pendência fica na fila para a pessoa no painel.
- **`review_pr`.** Ele roda `make check` no servidor, sobre o código de um pull request.

## Obter um token

Um token pertence a um projeto e a um papel, e expira (30 dias por padrão, no máximo 90).
Ele é mostrado uma única vez.

**No painel:** entre, abra o projeto e use **Conectar minha IA** ("Connect my AI") no
fim do Painel. Escolha o papel e quantos dias, crie o token e copie:
ele é mostrado uma única vez. A mesma tela traz a configuração do MCP para colar no seu cliente, com o
projeto e o endereço preenchidos, e lista os seus tokens neste projeto com um botão Revogar
(nunca os valores). `GET /api/projects/{owner}/{repo}/tokens` devolve essa lista, só com
uma sessão conectada.

Pelo terminal, com o cookie de sessão:

```bash
export TRADVIA_URL=https://app.tradvia.org
export TRADVIA_SESSION=...   # valor do cookie __Host-tradvia_session do seu navegador conectado
tradvia token create --project owner/repo --role triage --days 30
```

Papéis: `triage` (padrão) lê, cria e inicia cards; `write` também ajusta planos.
Um token `read` não pode usar as ferramentas de agente, porque as leituras exigem o mesmo papel que iniciar um
card. Um token nunca tem mais poder do que o dono dele tem no projeto.

Mantenha o token fora do git. Coloque-o em `TRADVIA_TOKEN` ou no cofre de segredos do seu cliente.
Revogue com `tradvia token revoke --project owner/repo ID`.

## IA gestora (só admins)

Um admin pode criar um token do tipo **IA gestora** ("AI manager") na tela Conectar minha IA
([ADR 0014](adr/0014-ai-manager-below-the-owner.md)). Ela trabalha abaixo do dono:

- **Base:** tudo o que um agente faz aqui: ler tudo, comentar, criar, iniciar e pegar
  cards, ajustar planos. O `read_briefing` dela diz que ela é gestora.
- **Só com a sua concessão, por tempo definido:** aprovar ou rejeitar planos (`reply_plan` com `approve`
  ou `reject`) e reordenar o backlog (`reorder_backlog`). Conceda e revogue cada uma na mesma
  tela; uma concessão dura no máximo 90 dias e nunca passa do token, e a revogação vale na
  chamada seguinte.
- **Nunca:** merge, deploy, orçamento, conexões, papéis ou convites. Esses continuam pendências
  para uma pessoa.

Pelo terminal: `POST /api/projects/{owner}/{repo}/tokens` com `{"kind": "manager"}`, depois
`POST .../tokens/{id}/grants` com `{"permission": "approve_plan", "expires_in_days": 7}` e
`DELETE .../tokens/{id}/grants/{grant_id}`, todos com uma sessão de admin conectada.

## Opção A: stdio (um cliente na sua máquina)

Instale o comando `tradvia` a partir de um checkout deste repositório:

```bash
pip install ./core
```

O servidor lê três variáveis: `TRADVIA_URL`, `TRADVIA_PROJECT` (`owner/repo`) e
`TRADVIA_TOKEN`. Ele roda com `tradvia mcp`. A saída padrão dele é o canal do protocolo, então ele
não escreve mais nada ali.

### Claude Code

Arquivo de projeto `.mcp.json` na raiz do repositório. O Claude Code expande `${VAR}` e pede
que você aprove um servidor de projeto antes do primeiro uso (conferido na documentação do Claude Code):

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

Arquivo `.kiro/settings/mcp.json` no repositório, ou `~/.kiro/settings/mcp.json` para todos
os projetos (conferido na documentação do Kiro). Use o mesmo bloco `mcpServers` acima. Dois passos:

- Aprove `TRADVIA_TOKEN` em **Mcp Approved Env Vars**. O Kiro só expande uma variável depois
  dessa aprovação.
- Liste em `autoApprove` as ferramentas que você aceita sem confirmação. A documentação não diz o que uma
  execução não interativa faz com uma ferramenta não aprovada; teste isso antes de confiar no
  Kiro sem interface.

### Gemini CLI

Arquivo `~/.gemini/settings.json`, ou `.gemini/settings.json` para um projeto (conferido na
documentação do Gemini CLI). `includeTools` lista as ferramentas que o agente pode usar. Deixe `trust` sem valor:
`trust: true` pula todas as confirmações para o servidor.

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

Não conferido. Um artigo do Google Cloud descreve `~/.gemini/config/mcp_config.json`, com
`serverUrl` e `headers` para servidores remotos. O mesmo artigo relata que os valores de `env`
não chegaram ao processo no Antigravity. Prefira a Opção B e teste antes de confiar nela.

## Opção B: HTTP (agentes na nuvem, como o Manus)

O endpoint recebe JSON-RPC por `POST`, com o mesmo token bearer:

```
https://app.tradvia.org/api/projects/owner/repo/mcp
Authorization: Bearer <token>
```

O Manus aceita um servidor MCP personalizado que seja um endpoint HTTPS remoto com chave de API ou token
bearer (conferido na documentação do Manus). Ainda não foi testado com o Manus.

Confira o endpoint à mão:

```bash
curl -s https://app.tradvia.org/api/projects/owner/repo/mcp \
  -H "Authorization: Bearer $TRADVIA_TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## O que o agente recebe de volta

- Uma ferramenta que dá certo devolve o resultado como texto JSON.
- Uma recusa (uma ação só para pessoas, uma ferramenta indisponível, um papel insuficiente, um card no
  estado errado) devolve `isError: true` com o motivo. Não é um erro de protocolo.
- Os erros nunca levam o token. O cliente o substitui por `[token]`.

Toda chamada é auditada com o dono do token como autor e `via: api_token`. As recusas também
são auditadas.

## Limites

- As respostas são JSON. Não há fluxo de eventos, nem recursos ou prompts MCP.
- Os agentes ainda não podem editar um card nem reordenar o backlog.
- Os agentes agem com a identidade do dono do token no GitHub. Identidades próprias para agentes estão planejadas.
- Novas tentativas: envie `X-Tradvia-Idempotency-Key` com uma chamada de escrita (clientes HTTP). O painel
  ignora uma repetição exata da mesma chave em até cinco segundos. Sem chave, um
  `create_card` repetido cria um segundo card.
