<!-- tradvia-translation source="docs/coding-agents.md" source-sha256="52d6ec758f4e964f2c26d22664205add370d06eb35d5d6c33640527c88a23db8" -->
# Agentes de código

O planner e o executor de um card são adaptadores atrás de uma mesma interface,
`CodingAgent` (`core/src/tradvia/agents.py`, [ADR 0010](adr/0010-coding-agent-adapters.md)).
O provedor do papel escolhido na tela Conexões
([ADR 0019](adr/0019-project-connections.md)) ou em `TRADVIA_PLANNER` / `TRADVIA_EXECUTOR`
escolhe o adaptador. Sem provedor, o comportamento não muda.

## Provedor × papel

| Provedor | Planner | Executor | Status |
|---|---|---|---|
| Claude Code (`claude-code`) | sim | sim | Shipped |
| Gemini CLI (`gemini-cli`) | sim | sim | In progress (testes offline com um executor simulado; ainda não rodou contra a CLI real no CI) |
| OpenAI Codex CLI (`codex-cli`) | sim | sim | In progress (o mesmo) |
| Kiro, Antigravity e outras IDEs | não | não | Planned como executores: os nomes das ferramentas não interativas ainda não estão documentados. Hoje elas podem trabalhar num card pelo MCP do Tradvia ([mcp.md](mcp.md)) |

Os papéis reviewer e orchestrator ainda não usam esses adaptadores.

## Ferramentas restritas e limites

O Tradvia cria o branch, faz o commit e abre o pull request. Nenhum adaptador roda git,
exceto `git mv` e `git rm` no executor do Claude Code, para que ele possa renomear e apagar arquivos:
o pull request então também remove os caminhos antigos (#185). Caminhos protegidos (`.github/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS`, `.tradvia.yml`) continuam bloqueados para qualquer mudança, inclusive
exclusão.

Orientação durante a execução (#99, ADR 0021): com o executor do Claude Code, quem pode responder planos pode
mandar uma mensagem para um card em execução (janela do card ou um comentário na issue). A execução roda em
trechos de `TRADVIA_STEER_EVERY_TURNS` turnos (padrão 5) numa mesma sessão (`--resume`) e
entrega as mensagens novas entre um trecho e outro, dentro dos limites de turnos, tempo e custo do card. Os
adaptadores do Gemini e do Codex rodam sem orientação por enquanto.

- **Claude Code**: `--allowedTools` (`Read,Glob,Grep` para planejar; ferramentas de edição mais
  `make check`/`make test` e `git mv`/`git rm` para executar) e `--max-turns`.
- **Gemini CLI** (`gemini -p --output-format json`): `--allowed-tools` com ferramentas só de leitura
  para planejar, e edição de arquivos mais `make check`/`make test` para executar; sem busca ou acesso à web.
  A CLI não tem opção de turnos, então o código de saída 53 ou mais chamadas de ferramenta que o limite é tratado como
  limite de turnos.
- **Codex CLI** (`codex exec --json`): sandbox `read-only` para planejar, `workspace-write` com
  acesso à rede desligado para executar. Mais itens de comando/arquivo que o limite é tratado como
  limite de turnos.

Os limites de turnos vêm de `TRADVIA_PLAN_MAX_TURNS` e `TRADVIA_EXEC_MAX_TURNS`; o limite de tempo,
de `TRADVIA_CLAUDE_TIMEOUT`. Erros (CLI ausente, tempo esgotado, JSON inválido, saída diferente de zero,
limite de turnos) são `RuntimeError`s com os segredos ocultados, e o card volta para
`WAITING_HUMAN`.

Para todo adaptador, depois da execução o fluxo bloqueia mudanças em `.github/`, `.git/`,
`LICENSE.md`, `CLA.md`, `CODEOWNERS` e `.tradvia.yml`, e o card volta para
`WAITING_HUMAN` sem pull request.

## Normalização de custo

Cada execução informa duração, tokens e US$ para o painel. O que o provedor não informa
é marcado (`usd_reported`, `tokens_reported`) e aparece como "não informado", nunca inventado.

| Provedor | US$ | Tokens |
|---|---|---|
| Claude Code | informado | informados |
| Gemini CLI | não informado | informados quando as estatísticas do JSON os trazem |
| Codex CLI | não informado | informados pelo evento `turn.completed` |

O modelo escolhido na tela Conexões para os provedores de CLI só é registrado; cada CLI
usa a sua própria configuração de modelo.
