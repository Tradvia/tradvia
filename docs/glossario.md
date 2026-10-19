# Glossário

Termos principais do Tradvia. O status de cada capacidade segue o
[README](../README.md) e o [ADR 0003](adr/0003-agentops-hybrid-execution-control-plane.md):
**Entregue**, **Em andamento**, **Parcial** ou **Planejado**.

| Termo | Definição | Status |
|---|---|---|
| **Card** | Unidade de trabalho do Kanban híbrido executável; é uma máquina de estados durável (`BACKLOG`, `AGENT_WORKING`, `WAITING_HUMAN`, `IN_REVIEW_PR`, `DONE`) que registra tokens, segundos de computação e tempo. | Em andamento — máquina de estados e aprovação no chat |
| **Work item** | Item do modelo de trabalho neutro de fornecedor, sobre o qual Kanban, Gantt, Burndown, WBS, calendário e capacidade são projeções; o ciclo de vida do card é o ciclo de vida de um work item (ADR 0002). | Entregue (biblioteca em `core/src/tradvia/work/`) |
| **Human gate** | Ponto de aprovação em que o card pausa em `WAITING_HUMAN` até uma pessoa responder (por exemplo, aprovar o plano antes de o agente abrir um pull request). | Parcial — aprovação em chat (Discord) e em comentário de issue; demais canais planejados |
| **Live takeover** | Quando um agente encontra um bloqueio (2FA, CAPTCHA, UI complexa, decisão de arquitetura), a sessão é congelada, uma pessoa é avisada e assume o terminal ou navegador por streaming (WebRTC/VNC), devolvendo o controle depois. | Planejado (Fase 2) |
| **Session worker** | Container que executa uma sessão stateful de CLI ou navegador (Playwright/CDP) com contexto de autenticação persistente e mutex de sessão, só operando contas e portais que o cliente está autorizado a usar. | Planejado (Fase 2) |
| **Token FinOps** | Controle de custo da execução: registro do custo exato por token ou por segundo de computação em cada card, além de pool de contas, fallback entre fornecedores e disjuntores de orçamento, sempre dentro dos limites de cada fornecedor. | Parcial — custo registrado por card; pool, fallback e disjuntores planejados; governança em `ee/` planejada |
