<!-- tradvia-translation source="docs/adr/0007-tradvia-as-the-single-interaction-surface.md" source-sha256="e7ae6f5a321e5920e7e65c34a56d6b1741c2fe9bbf13f6d961f5b44ee6f7c9bf" -->
# ADR 0007: O Tradvia como a única superfície de interação

- Status: Aceita
- Data: 2026-10-20

## Contexto

Hoje o dono opera o Tradvia a partir de quatro lugares: um chat de IA que faz o papel de
orquestrador (cria cards, inicia, revisa planos e pull requests),
issues do GitHub (aprovar planos), pull requests do GitHub (fazer merge) e GitHub
Actions (aprovar deploys). Em 2026-10-20 essa divisão custou várias horas: aprovações
de plano digitadas no chat de IA nunca chegaram ao agente, dois cards estouraram o tempo
esperando uma resposta no GitHub, e um deploy esperando aprovação bloqueou a
fila por um dia.

A meta é que toda interação aconteça no Tradvia. Fornecedores de IA (Anthropic,
Google, OpenAI e outros) e hospedagens de repositório (GitHub, GitLab) são configurados
uma vez e depois usados pelo Tradvia; ninguém precisa abrir as telas deles para tocar um
projeto.

## Decisão

1. **Uma tela para cada ponto de decisão humana.** Criar, ordenar e iniciar cards,
   responder planos (Aprovado / Rejeitado / Ajuste), revisar e fazer merge de pull
   requests, aprovar ou rejeitar deploys e rodar operações pós-deploy
   são ações no Tradvia, executadas com a própria identidade da pessoa conectada
   no provedor, para que o histórico do provedor continue mostrando quem fez o quê (ADR 0004).
2. **O orquestrador é um agente do Tradvia.** A conversa que planeja o
   backlog, inicia cards, propõe ajustes e revisa pull requests passa
   para uma conversa por projeto no Tradvia. Ela usa um conjunto fechado de ferramentas
   do Tradvia. Ela nunca aprova planos, faz merge ou deploy: esses pontos continuam humanos.
3. **Provedores são conexões.** Provedores de IA e hospedagens de repositório são
   configurados por projeto no Tradvia, com segredos criptografados em repouso e nunca
   devolvidos ao navegador. Agentes de código (Claude Code, Gemini CLI, Codex CLI)
   são adaptadores atrás de uma interface; hospedagens de repositório (GitHub, GitLab) são
   adaptadores atrás de outra. Um provedor só é listado como suportado quando o
   adaptador dele é entregue; ferramentas sem CLI ou API não interativa estável continuam Planned.
4. **A espera é durável.** Um card esperando uma pessoa pausa e retoma quando
   a resposta chega, por mais tarde que seja (ADR 0006, execução durável), em vez de
   estourar o tempo depois de uma hora.
5. **As pessoas são avisadas, não consultadas.** Uma caixa de pendências e notificações web push levam
   o dono à ação pendente exata.

## Ordem de entrega e status

| Ordem | Card | Funcionalidade | Status |
|---|---|---|---|
| 1 | #45 | Login com GitHub, sessões, papéis por projeto | Shipped |
| 2 | #46 | Criar, iniciar, ordenar e responder cards no Tradvia | Shipped |
| 3 | #56 | Revisar e fazer merge de PRs, aprovar deploys no Tradvia | Partial |
| 4 | #40 | Espera durável e retomada | Planned |
| 5 | #47 | Caixa de pendências e notificações web push | Planned |
| 6 | #57 | Conversa com o orquestrador dentro do Tradvia | Partial |
| 7 | #58 | Conexões do projeto para provedores de IA e de repositório | Partial |
| 8 | #59 | Adaptadores de agentes de código (Gemini CLI, Codex CLI) | In progress |
| 9 | #60 | GitLab como provedor de repositório | Planned |

Já entregues e reaproveitados: escopo de projeto (#44), backlog no Tradvia (#50),
leituras do painel a partir do store (#36), operações no momento do deploy (#54).

## Pull requests e deploys (#56)

Status: **Partial** (implementado e testado offline; a produção precisa das
permissões do GitHub App em `docs/github-app.md`).

- O card mostra o PR e os checks dele, lidos com o token da pessoa. O merge
  é um squash com o título Conventional Commit do PR, seguido da exclusão do
  branch. Um check obrigatório com falha (`core-tests`, `license-boundary`, `cla`)
  recusa o merge; só o `cla` pode ser ignorado, de forma explícita, e o registro de auditoria
  guarda isso.
- Fazer merge, atualizar um branch, aprovar ou rejeitar um deploy, cancelar um
  deploy substituído e rodar a operação pós-deploy são novas
  interações do `.tradvia.yml` (`merge_pr`, `update_branch`, `approve_deploy`),
  todas com `maintain` como padrão. Ser revisor do ambiente `production`
  continua sendo decidido pelo GitHub, com o token da pessoa.
- Um deploy esperando que foi superado por um mais novo na mesma fila aparece como
  "substituído" e é o único tipo que o painel pode cancelar.
- A operação pós-deploy dispara o `deploy-oci` com `backfill` e
  `backlog` (#54); o backlog vem preenchido com a ordem guardada no Tradvia.

## Consequências

- A configuração única ainda acontece nos provedores: registrar o GitHub App
  do Tradvia (e depois um app OAuth do GitLab) e tornar o dono revisor do
  ambiente `production`. Depois disso, o trabalho do dia a dia não precisa de nenhuma outra
  tela.
- O Tradvia guarda tokens de pessoas e segredos de provedores: criptografia em repouso, CSRF,
  sessões curtas e um registro de auditoria são requisitos do primeiro card (#45), não
  um reforço para depois.
- Até o #46 ser entregue, as aprovações de plano continuam acontecendo na conversa da issue.

## Adendo: a conversa com o orquestrador (#57)

O agente orquestrador que conversa com o dono dentro do Tradvia trabalha com um conjunto fechado de
ferramentas do Tradvia, nunca com um shell livre. O limite das ferramentas segue a ADR 0004: aprovar um plano,
fazer merge e deploy não têm ferramenta, então essas decisões ficam com a pessoa; o orquestrador
só pode responder a um plano com um ajuste e colocar na fila uma pendência com um botão. Veja
[docs/orchestrator.md](../orchestrator.md).
