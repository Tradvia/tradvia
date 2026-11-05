<!-- tradvia-translation source="docs/dashboard-kpis.md" source-sha256="f1d47ea41d535ee8a0332b0bad5aef972062d6a50928615db2eb690e7f1926ae" -->
# KPIs do painel, filtros e uso de tokens

O que cada indicador de `dashboard/index.html` mede e de onde vem cada número.
O status de cada funcionalidade e um guia passo a passo estão na página de Ajuda do painel (`dashboard/docs.html`, Shipped).

> **Custos são estimativas, não fatura.** Os valores em dólar são o custo equivalente à API
> que o `claude -p` informa (`total_cost_usd`) ou que o LiteLLM calcula para o planner. Não
> são o que uma assinatura ou o provedor de nuvem cobra.

## De onde vêm os números

| Número | Origem | Onde fica guardado |
| --- | --- | --- |
| Custo e tokens do executor | JSON do `claude -p`: `total_cost_usd` e `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens` + `cache_creation_input_tokens` como cache) | Linhas na descrição do pull request: `Estimated cost (API-equivalent): $1.0051 · 285s` e `Tokens: 12 345 in · 3 210 out · 80 000 cache`; lidas para `pull_requests` |
| Custo e tokens do plano | O resultado do planner (`claude -p` ou LiteLLM). Um por plano, então cada novo plano tem o seu número | As mesmas duas linhas no comentário de plano do agente; lidas para `comments` (`cost_usd`, `tokens_*`) |
| Custo "até agora" | Linha `estimated cost so far` do PR (planos + executor) | Só um recurso de reserva, veja abaixo |

Um `usage` ausente, ou sem os campos de cache, conta como 0 tokens. O leitor da linha `Tokens:`
aceita espaço, espaço inseparável, ponto ou vírgula como separador de milhar, e números simples.

O store (`schema.sql`) adiciona as colunas de forma idempotente (`ADD COLUMN IF NOT EXISTS`):
`pull_requests.tokens_in|tokens_out|tokens_cache` e `comments.cost_usd|tokens_in|tokens_out|tokens_cache`.
`python -m tradvia.backfill` regrava comentários e PRs, então o histórico é preenchido onde
a linha existe no GitHub. PRs e planos antigos sem a linha `Tokens:` ficam sem tokens.

A API de leitura acrescenta `tradvia_cost` (com tokens) a cada pull request e `tradvia_plan_cost`
(uma entrada por comentário de plano) a cada issue. Esses campos não são do GitHub.

## Custo e tokens por card

```
card = soma de todos os planos do card (cada novo plano) + o executor do PR dele
```

O "estimated cost so far" do PR já inclui os planos, então **não** é somado por cima.
Ele só é usado quando não há comentário de plano guardado para o card (histórico de antes
desta funcionalidade), e aí representa o card inteiro. A mesma regra vale para tokens, exceto que
a linha `Tokens:` do PR é só do executor.

## Indicadores

| Indicador | Mede | Status |
| --- | --- | --- |
| Taxa de sucesso, duração mediana | Execuções do executor escolhido no período (puladas e canceladas são ignoradas) | Shipped |
| PRs do agente mesclados, mediana PR → merge | PRs criados no período em branches `tradvia/` ou `claude/` | Shipped |
| Cards no período | Cards criados no período que passam pelos filtros | Shipped |
| Custo total (planejamento + execução) | Soma do custo dos cards visíveis que têm custo registrado | Shipped |
| Custo mediano por card | Mediana do custo dos cards | Shipped |
| Custo por PR mesclado | Média do custo dos cards cujo PR foi mesclado no período | Shipped |
| Tokens totais (entrada, saída, cache) | Soma sobre os cards visíveis | Shipped |
| Tokens por card | Mediana de entrada + saída + cache por card | Shipped |
| Orçamento mensal | Gasto do mês UTC atual (planos pela hora do comentário + executor pela hora de criação do PR; o "cost so far" do PR não é somado) contra o teto mensal do projeto (Configurações, Orçamento do projeto; ADR 0018), com uma barra: cor de alerta a partir de 80%, cor de parada a partir de 100%. "—" com uma dica quando não há teto. Exige login (lido de `/api/auth/me`) e o store. | Shipped |
| Custo e tokens por dia (barras SVG) | Planos pela hora do comentário, executor pela hora de criação do PR (UTC) | Shipped |

Sem dados de custo no período, um indicador de custo ou de tokens mostra "—" com uma dica
explicando o motivo. Cards sem nenhum custo registrado ficam fora das somas e medianas.

## Filtros

Os filtros valem para os indicadores, os cards e as execuções, e ficam na URL, ao lado de `?repo=`:

| Parâmetro | Valores |
| --- | --- |
| `from` | `7d`, `30d`, `90d` (a partir de hoje) ou `YYYY-MM-DD`; ausente = tudo |
| `to` | `YYYY-MM-DD`, inclusivo; ausente = sem fim |
| `executor` | `agent` (agente do Tradvia, `agent.yml`), `claude` (Claude Code, `claude.yml`); ausente = todos |
| `fcard` | um número de issue: o quadro mostra só esse card; ausente = todos. Um card que não está no quadro é ignorado, e o painel avisa |
| `card` | um número de issue: abre esse card ao carregar (o link das notificações e das pendências). Não filtra |
| `status` | `BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR` ou `DONE`; ausente = todos |

Exemplo: `?repo=Tradvia/tradvia&from=30d&executor=claude&status=DONE`.

## Detalhes do card

Em cada card, o quadro mostra como etiquetas os tipos de agente que trabalharam nele: `Planner`,
`Tradvia agent` e `Claude Code`. Clique no card, ou pressione Enter sobre ele, para abrir a janela:

| Seção | Conteúdo | Origem |
| --- | --- | --- |
| Agentes que trabalharam nele | Uma linha por agente: execuções, planos, tokens de entrada / saída / cache e custo estimado, e o total do card | Comentários de plano do card (store), o custo do executor do PR mais recente com custo, as execuções do card |
| Diagnóstico | Estado, por que o card está no quadro, o último marcador do agente, labels, execuções (total, sem resultado, em andamento), última execução e a duração dela, PRs, datas | O card, os PRs e as execuções dele |
| Aprovação, pull request, atividade, auditoria | Os mesmos controles de antes, para quem tem o papel | GitHub. Carregados quando o card abre e quando o card muda, não a cada atualização |

Um traço (—) significa que nenhum custo ou contagem foi registrado para aquele agente. Status: **Shipped**.
**Partial:** os tokens e o custo do planner vêm do store; quando o painel lê o GitHub
diretamente, eles aparecem como "—".

## Limites

- **Partial:** os custos por plano vêm do store. Quando o painel lê o GitHub diretamente
  (sem store), os comentários não são carregados, então o custo do card recorre ao
  "cost so far" do PR e os números de tokens cobrem só o executor.
- O quadro lê os 50 primeiros cards com a label `agent:ready`, toda issue aberta que tem um
  PR aberto do agente (até 100 issues abertas e 100 PRs abertos), os 50 PRs mais recentes e as últimas 30
  execuções por workflow. Um card com PR aberto do agente fica no quadro mesmo sem a label, porque
  o sino o conta.
- As datas são comparadas em UTC.
