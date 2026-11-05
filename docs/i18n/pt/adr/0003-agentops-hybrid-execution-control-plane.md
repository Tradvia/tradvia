<!-- tradvia-translation source="docs/adr/0003-agentops-hybrid-execution-control-plane.md" source-sha256="a5e92472f42d0f4876e71107cd8c2984284fdc9be15658fbf768ecf0932fd213" -->
# ADR 0003: Plano de controle open-core de AgentOps e execução híbrida

- Status: Aceita
- Data: 2026-10-18
- Altera: [ADR 0002](0002-positioning-multi-view-orchestration.md) (só o posicionamento público)

## Contexto

A ADR 0002 enquadrou o Tradvia em torno das notações de progresso. Isso subestimava o
núcleo de engenharia: como o trabalho é de fato executado, por qual runtime, a que
custo, e como uma pessoa entra quando um agente fica travado.

## Decisão

O Tradvia se posiciona como **o plano de controle open-core de AgentOps e execução
híbrida** que une a gestão de projetos feita por pessoas e a execução autônoma por IA.
Seis pilares:

| Pilar | Status em 2026-10-18 |
|---|---|
| **Execução em dois runtimes** — APIs de LLM sem estado (OpenAI, Anthropic, Gemini, Ollama) mais session workers de CLI/navegador com estado (Playwright/CDP), com mutex de sessão | Partial: APIs via LiteLLM |
| **Pool de várias contas e Token FinOps** — carga distribuída dentro da cota de RPM/TPM de cada conta, fallback entre fornecedores, custo por token e por segundo de computação, disjuntores de orçamento | Partial: custo registrado por card |
| **Kanban híbrido executável e Live Session Takeover** — todo card é uma máquina de estados durável; uma pessoa pode assumir uma sessão de navegador/terminal congelada (WebRTC/VNC) e devolver o controle | In progress: máquina de estados + aprovação no chat |
| **Sandbox zero-trust** — MicroVMs Firecracker efêmeras, RLS no PostgreSQL, cofres criptografados de credenciais/sessões | Planned |
| **MCP nativo e AST GraphRAG** — destilação de conhecimento episódico para reduzir o custo de tokens | Planned |
| **Monorepo open-core** — `/core` auto-hospedado (Docker Compose), `/ee` + Cloud com pools de sessão por equipe, RBAC, governança de FinOps | In progress: monorepo, licenças, CI |

Regras para o material público:

1. Toda funcionalidade mantém um status explícito Shipped / In progress / Partial / Planned.
2. O pool de contas **respeita os limites de uso e os termos de cada fornecedor**. Nós o descrevemos
   como distribuição de carga dentro das cotas e fallback entre fornecedores, nunca como
   forma de contornar limites.
3. Os session workers só operam contas e portais que o cliente está autorizado
   a usar. Uma pessoa resolve os pedidos de 2FA e CAPTCHA pelo Live Session
   Takeover; agentes nunca tentam resolvê-los.

## Consequências

- O site deixa a prévia das seis visões em favor do quadro executável
  com dois runtimes, pool de contas e live takeover. O modelo de trabalho e as
  projeções da ADR 0002 continuam em `core/src/tradvia/work/` como biblioteca.
- Os READMEs e o perfil da organização seguem este posicionamento.
