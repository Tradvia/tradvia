<!-- tradvia-translation source="docs/adr/0002-positioning-multi-view-orchestration.md" source-sha256="18434d2c28e105700fd7c8fde0d110ded69bdabc7a903a59ca4ce6505f123a0e" -->
# ADR 0002: Orquestração da execução de projetos com transparência em várias visões

- Status: Aceita
- Data: 2026-10-18
- Substitui: o enquadramento "Kanban híbrido" usado no README e no site até esta data

## Contexto

O material inicial descrevia o Tradvia como "agentes de IA num quadro Kanban híbrido". Isso
era só um exemplo. O Kanban servia para ilustrar a *transparência sobre o que
cada membro do projeto — pessoa ou agente — está fazendo*, não para definir o produto.

As equipes acompanham o progresso por notações diferentes, conforme o método e o
público, e a maioria já tem uma ferramenta de projetos que não vai abandonar.

## Decisão

O Tradvia é um **orquestrador multiagente para a execução de projetos**. Ele
coordena o trabalho de agentes de IA e de pessoas, e torna o progresso transparente
na notação de que cada público precisa:

| Notação | Responde |
|---|---|
| **Kanban** | Onde está cada item no fluxo agora? |
| **Gantt** | Quanto tempo cada item leva, o que depende do quê, quais marcos estão em risco? |
| **Scrum / Burndown** | O escopo da sprint vai ser entregue a tempo? |
| **WBS / EAP** | Como o escopo se decompõe em entregas? |
| **Calendário / Linha do tempo** | O que vence em qual data? |
| **Carga de trabalho / Capacidade** | Quem — pessoa ou agente — está sobrecarregado ou ocioso? |

Consequências para a arquitetura:

1. **Um modelo de trabalho neutro.** Toda tarefa é um `WorkItem` (hierarquia, datas,
   dependências, estimativa, responsável, sprint, status). O contrato de estado do card
   da ADR 0001 vira o ciclo de vida de um work item.
2. **As visões são projeções.** Kanban, Gantt, Burndown, WBS, Linha do tempo e
   Carga de trabalho são funções puras sobre a mesma lista de work items. Nenhuma visão é dona
   de dados.
3. **Visões nativas e conectores.** O Tradvia traz as próprias visões *e* sincroniza
   com as ferramentas que as equipes já usam. Os conectores implementam uma interface
   (`TrackerConnector`) e mapeiam itens externos para `WorkItem`.

Conectores iniciais: Trello, Jira, ClickUp, Monday.com, Asana, Wrike,
Smartsheet, Artia, Microsoft Planner, KanbanFlow e Airtable.

## Consequências

- O material público (site, perfil da organização, READMEs) é atualizado para este
  posicionamento. Cada funcionalidade mantém um status explícito Shipped / In progress /
  Planned.
- `core/src/tradvia/work/` guarda o modelo de trabalho, as projeções e a
  interface de conector. A renderização (interface web) consome as projeções depois.
- Conectores são boas primeiras contribuições: cada um fica isolado atrás da
  mesma interface.
