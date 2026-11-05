<!-- tradvia-translation source="docs/adr/0010-coding-agent-adapters.md" source-sha256="6ce9287d4620e0e409c33e297f0ad6d0252374a887b41688e5578fa72e069055" -->
# ADR 0010: Adaptadores de agentes de código

- Status: Aceita
- Data: 2026-10-25

## Contexto

O planner/executor de um card era só o Claude Code. A ADR 0007 faz da tela Conexões
o lugar para escolhê-lo, e a ADR 0019 guarda o provedor e o segredo por papel.

## Decisão

- **Interface**: `CodingAgent` tem `plan(title, body, number, cwd, notice)`, que devolve o
  JSON do plano, e `execute(card, cwd, budget_notice)`, que devolve os arquivos alterados, o resumo,
  o custo, os tokens, a duração e as chamadas de ferramenta. `ClaudeCodeAgent` envolve as funções existentes
  sem mudá-las; `GeminiCliAgent` e `CodexCliAgent` compartilham `CliAgent`.
- **Só não interativo**: um adaptador só existe para uma CLI com modo não interativo
  documentado e saída JSON. Ferramentas restritas (sem git, sem rede além do provedor), limites de turnos e
  de tempo. Ferramentas de IDE (Kiro, Antigravity) continuam Planned até existir uma CLI/API estável.
- **Caminhos protegidos** são aplicados pelo fluxo depois da execução, a partir do `git status`, então
  valem para qualquer adaptador.
- **Custo**: o que um provedor não informa é marcado e aparece como "não informado".

## Consequências

Adicionar um provedor é uma classe e uma entrada no catálogo. Os limites de turnos de CLIs sem
opção de turnos são verificados depois da execução, não durante.
