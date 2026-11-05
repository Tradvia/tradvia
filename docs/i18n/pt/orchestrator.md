<!-- tradvia-translation source="docs/orchestrator.md" source-sha256="51a53205178097c8f2e30a67e04b6fb32c77e68a23aee0a8ce5766d88ddfab3d" -->
# Conversa com o orquestrador

Status: 🟦 Partial. A conversa, o conjunto fechado de ferramentas, o orçamento, o registro de custo e
a auditoria estão implementados e testados offline com um modelo roteirizado. Ainda não entregues: execução
durável de uma rodada entre reinícios da API (#40; uma rodada perdida num reinício deixa a mensagem
sem resposta), uma caixa de pendências compartilhada com o resto do painel (#47; por enquanto
as pendências ficam na conversa) e uma verificação ao vivo contra um provedor real. O chat
só roda com um provedor de IA com a chave de API do próprio projeto ([ADR
0013](adr/0013-chat-needs-an-api-key.md)): sem ela, fica desligado e o painel diz o motivo.
Faz parte da [ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md) e da
[ADR 0019](adr/0019-project-connections.md) (o papel `orchestrator`, abaixo).

## Como funciona

Cada projeto tem uma conversa, guardada no Postgres (`chat_messages`: projeto, autor,
tipo, texto, referências a cards e pull requests, e o custo da rodada por trás de uma resposta).
No painel ela é a seção **Conversa com o orquestrador**: o histórico, uma caixa de
texto e o indicador "orquestrador trabalhando…" enquanto uma rodada está em andamento. A seção
se atualiza por consulta periódica, mais rápido enquanto o orquestrador trabalha.

- `GET /api/projects/{owner}/{repo}/chat` devolve `messages`, `working`, `cost` (total
  registrado para o projeto), `can_chat` e `chat_off_reason`. Exige o papel `read`.
  `can_chat` só é verdadeiro para quem tem `start_card` **e** quando um provedor de IA com
  chave de API atende o chat; caso contrário, para quem tem `start_card`, `chat_off_reason` é
  `"no_api_key"` e o painel mostra como configurar.
- `POST /api/projects/{owner}/{repo}/chat` com `{"text": "...", "refs": [{"type": "card",
  "number": 7}]}` guarda a mensagem, inicia uma rodada em segundo plano e responde `202`. Tem
  a mesma sessão, CSRF, chave de idempotência e auditoria de toda ação do painel, e exige
  a interação `start_card` do `.tradvia.yml` (ADR 0004; `triage` por padrão). Enquanto uma rodada
  está em andamento, uma segunda mensagem recebe `409`; sem provedor de IA e sua chave de API também recebe `409`,
  e nada é guardado.

Uma rodada é um ciclo. O modelo recebe a conversa recente do projeto e os resultados das
ferramentas que já usou, e responde um objeto JSON: `{"tool": "...", "args": {...}}` para usar uma
ferramenta ou `{"reply": "...", "refs": [...]}` para terminar. O Tradvia executa a ferramenta e repete; a
resposta é guardada na conversa. O modelo não tem shell, arquivo nem ferramenta de rede
próprios: é um chat completion simples pelo LiteLLM, então não há ferramenta para ele chamar
além de responder o único objeto JSON que o Tradvia espera.

## Qual modelo responde

A linha `orchestrator` das conexões do projeto (`dashboard` → Conexões, [ADR
0019](adr/0019-project-connections.md)) decide, com a mesma precedência de todos os outros papéis
(a seção `providers` do `.tradvia.yml` é o padrão, e um valor definido na tela Conexões
a substitui):

- **`anthropic`, `openai` ou `gemini`, com a chave de API salva:** um chat completion simples
  pelo LiteLLM (que já é dependência do `core` — o planner, `llm.py`, usa do mesmo jeito),
  com a chave guardada do próprio projeto passada na própria chamada. Nada roda em shell, então nada
  disso precisa de Node.js nem de CLI. Cada mensagem é cobrada nessa chave (por uso).
- **Qualquer outra coisa — sem valor, sem chave salva ainda, ou uma CLI de agente de código:** o chat fica desligado para o
  projeto (`orchestrator.model_for_connection` não devolve modelo). Não há recurso ao Claude
  Code nem login por assinatura para o chat (ADR 0013): a conexão `claude-code` só executa
  o executor. O papel orchestrator recusa provedores de CLI tanto na tela quanto no
  `.tradvia.yml`.

Isso é por projeto, não uma configuração do servidor inteiro: dois projetos na mesma instalação do Tradvia podem
responder por dois provedores diferentes, e trocar de provedor não exige novo deploy — só uma
mudança na tela Conexões.

## O que o orquestrador pode fazer

| Ferramenta | Faz |
|---|---|
| `read_backlog`, `read_cards`, `read_card`, `read_plan` | Lê o backlog, os cards e o plano que espera resposta |
| `read_pulls`, `read_pull`, `read_checks`, `read_deploys` | Lê pull requests, os checks deles e os deploys esperando aprovação |
| `create_card`, `edit_card` | Cria um card no fim do backlog; muda um título ou uma descrição |
| `reorder_backlog` | Define a ordem ou sobe/desce um card |
| `start_card` | Marca um card como pronto para o agente (`agent:ready`) |
| `reply_plan` | Responde a um plano com **`adjust`** e um texto |
| `review_pr` | Roda `make check` no branch do pull request no runner do projeto e comenta o resultado no PR |

Toda ferramenta age sobre o único projeto da conversa, com o **próprio token do GitHub da pessoa
conectada** (assim o GitHub mostra quem agiu), e só com os argumentos listados para ela.

## O que ele não pode fazer

- **Aprovar ou rejeitar um plano, fazer merge de um pull request, aprovar ou cancelar um deploy, rodar a
  operação pós-deploy.** Não há ferramenta para isso (ADR 0004). `reply_plan` recusa qualquer decisão
  que não seja `adjust`. Quando o modelo pede uma dessas, o Tradvia recusa, audita como negada e
  guarda uma mensagem **pendente** com uma referência e um botão que chama a ação já existente do painel
  (aprovar/rejeitar plano, merge, aprovar deploy), verificada de novo contra o papel da pessoa
  no clique.
- Rodar qualquer comando além dos fixos `git fetch`, `git worktree` e `make check` do
  `review_pr`. A revisão recusa pull requests de forks (o código deles não é executado) e roda
  sem token e com `TRADVIA_OFFLINE=1`.
- Ver outro projeto. As consultas ao store filtram pelo projeto, as chamadas ao GitHub usam o
  repositório do projeto, e um argumento de ferramenta que nomeia um repositório é recusado.
- Usar uma ferramenta que não está na tabela, ou um argumento que não está listado: os dois são recusados
  e devolvidos ao modelo.

O texto de cards, comentários e pull requests não é confiável: o prompt diz que é dado, e
o conjunto fechado de ferramentas limita o que uma injeção de prompt consegue fazer.

## Credenciais

Nenhuma credencial vai no prompt. O token do GitHub da pessoa fica dentro do processo da API e
só é usado pelas ferramentas.

- O segredo decifrado da conexão do projeto (ADR 0019) é passado como o
  argumento `api_key` da única chamada de completion, nunca por uma variável de ambiente do
  processo da API. Rodadas de chat de vários projetos podem estar em andamento ao mesmo tempo, cada uma na sua thread
  do mesmo processo (`start_thread` do `api.py`); uma variável de ambiente seria compartilhada por todas
  elas e poderia vazar a chave de um projeto para a rodada de outro. Qualquer erro do provedor é
  ocultado do mesmo jeito que a saída do `review_pr` já é, então uma chave repetida numa mensagem
  de erro nunca chega à conversa guardada.

`review_pr` precisa do checkout do projeto no runner: defina `TRADVIA_RUNNER_DIR_<OWNER>_<REPO>`
(para `Tradvia/tradvia`: `TRADVIA_RUNNER_DIR_TRADVIA_TRADVIA`). Sem ele, a ferramenta responde
que nenhum checkout está configurado. O `git fetch` usa as credenciais git do próprio runner.

## Limites e custo

Cada mensagem de uma pessoa tem um orçamento; quando uma rodada o atinge, o Tradvia para e guarda uma
mensagem de aviso dizendo qual limite foi atingido (o que já foi feito está no registro de auditoria).

| Variável | Padrão | Limite |
|---|---|---|
| `TRADVIA_ORCH_MAX_TURNS` | 10 | chamadas ao modelo por mensagem |
| `TRADVIA_ORCH_TIMEOUT` | 300 | segundos por mensagem |
| `TRADVIA_ORCH_BUDGET_USD` | 1.0 | custo equivalente à API por mensagem |
| `TRADVIA_ORCH_MAX_TOKENS` | 200000 | tokens de entrada + saída por mensagem |
| `TRADVIA_ORCH_REVIEW_TIMEOUT` | 900 | segundos para o `make check` do `review_pr` |

O custo e os tokens de toda rodada (respostas e avisos) ficam guardados na última mensagem dela, e
a conversa mostra o total do projeto. Valores inválidos voltam ao padrão. No
modo offline (`TRADVIA_OFFLINE=1`) o orquestrador responde com um aviso em vez de chamar
o provedor.

## Auditoria

Cada uso de ferramenta, inclusive os recusados e os que falharam, é uma entrada em `audit_log` com o autor
`orchestrator`, a ação `orchestrator:<tool>` e um detalhe que começa com
`for @<login>` (a pessoa conectada). A mensagem em si é auditada como a ação `chat`
da pessoa.
