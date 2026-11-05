<!-- tradvia-translation source="docs/dev-environment.md" source-sha256="15b86c50727a3360220cbd838ff6f02e0376e172a7de9713efd09cbf9c6a6dc1" -->
# Ambiente de desenvolvimento

O Tradvia é construído em público e, daqui em diante, em parte **pelo próprio Tradvia**.
Há dois ambientes:

| | O que é | Quem usa | Custo |
|---|---|---|---|
| **Ambiente de desenvolvimento** | GitHub Codespaces ou um Dev Container local com Python 3.12, o pacote core e os testes prontos | Toda pessoa que contribui | Codespaces: a sua cota mensal gratuita pessoal; local: grátis |
| **Runtime de dogfooding** | O workflow `agent` do GitHub Actions rodando o Tradvia nas issues deste repositório | Mantenedores iniciam; todos veem os resultados | Minutos gratuitos do Actions para repositórios públicos + tokens de LLM (o modo offline não custa nada) |

## 1. Abrir o ambiente de desenvolvimento

**Online (sem instalar nada):** na página do repositório clique em **Code → Codespaces →
Create codespace on main**, ou use o selo do README. O container roda
`make setup`, então depois de uns dois minutos você pode rodar:

```bash
make test                 # core test suite, offline, no API keys
make check                # tests + license boundary, same as CI
make manual               # render docs/ into dashboard/manual/, as the deploy does
```

Para traduzir uma página do manual, escreva-a em `docs/i18n/<idioma>/` com o mesmo caminho que
em `docs/` (por exemplo `docs/i18n/pt/projects.md`), mantenha os links como estão no original
e depois rode `PYTHONPATH=core/src python -m tradvia.manual stamp docs/i18n/pt/projects.md`.
`python -m tradvia.manual check` lista o que falta ou está desatualizado.

**Local:** instale o Docker e o VS Code com a extensão *Dev Containers*, clone
o repositório e escolha **Reopen in Container**. Sem Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make setup && make test
```

`TRADVIA_OFFLINE=1` vem definido por padrão no container, então nada chama um modelo
pago a menos que você mude isso em `core/.env`.

## 2. Deixar o Tradvia trabalhar numa issue (dogfooding)

1. Abra uma issue descrevendo uma mudança pequena e autocontida (um arquivo novo na v0.1).
2. Um mantenedor adiciona a label **`agent:ready`**.
3. O `@Agent-Architect` comenta um plano na issue e o card vai para
   `WAITING_HUMAN`.
4. Um mantenedor responde na issue: **Aprovado** / **Approved**, **Rejeitado** /
   **Rejected**, ou um ajuste (o agente planeja de novo, até 3 vezes).
5. Na aprovação, o `@Agent-Coder` cria `tradvia/issue-<n>`, faz o commit do arquivo e
   abre um pull request que passa pela revisão normal.

Regras de segurança embutidas no fluxo:

- Só quem tem acesso de triage pode adicionar labels, e só as respostas de
  `OWNER`, `MEMBER` ou `COLLABORATOR` são lidas; bots e pessoas de fora são ignorados.
- Agentes nunca podem escrever em `.github/`, `.git/`, `LICENSE.md`, `CLA.md` ou
  `CODEOWNERS`, e toda mudança continua precisando de um pull request revisado.
- Sem o segredo de repositório `ANTHROPIC_API_KEY`, a execução usa o planner
  offline, então todo o ciclo pode ser testado a custo zero.

Para usar um modelo real, um mantenedor adiciona o segredo `ANTHROPIC_API_KEY` em
**Settings → Secrets and variables → Actions** e, opcionalmente, a variável
`TRADVIA_PLANNER_MODEL` (qualquer nome de modelo do LiteLLM).

Limitação conhecida da v0.1: pull requests abertos com o `GITHUB_TOKEN` do workflow
não disparam outros workflows, então um mantenedor roda o CI de novo no PR do agente
(fechando e reabrindo). Um token de GitHub App resolve isso na v0.2.

## 3. Rodar o fluxo a partir do seu ambiente

```bash
cp core/.env.example core/.env    # fill GITHUB_TOKEN and TRADVIA_REPO for your fork
cd core && TRADVIA_OFFLINE=1 tradvia --issue <n> --chat github
```

Use o seu **fork** como `TRADVIA_REPO` enquanto experimenta, para que branches e
pull requests de teste fiquem fora do repositório principal.

`TRADVIA_PROJECTS` (separado por vírgulas, padrão `TRADVIA_REPO`) lista os repositórios
que a API guarda e serve. Carregue um com `tradvia backfill --project owner/repo`
(ele precisa estar em `TRADVIA_PROJECTS`). Veja `docs/projects.md`.

## 4. CLI do Claude Code como planner e executor (assinatura, sem chave de API)

Duas variáveis de ambiente opcionais escolhem quem planeja e quem implementa. As duas
têm como padrão o comportamento atual, então nada muda a menos que você as defina.

| Variável | Valores | Padrão |
|---|---|---|
| `TRADVIA_PLANNER` | `claude-code` (`claude -p`, ferramentas só de leitura), `litellm`, `offline` | `litellm` (offline quando `TRADVIA_OFFLINE=1`) |
| `TRADVIA_EXECUTOR` | `claude-code` (edita a árvore de trabalho), `note` (um arquivo pela API) | `note` |

Com `claude-code`, a CLI precisa estar instalada e no `PATH`, autenticada com
`CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`); não é preciso `ANTHROPIC_API_KEY`.
`TRADVIA_CLAUDE_TIMEOUT` (segundos, padrão 900) limita cada chamada.
`TRADVIA_EXEC_MAX_TURNS` (inteiro positivo, padrão 80) limita os turnos do executor e
`TRADVIA_PLAN_MAX_TURNS` (padrão 30) os do planner; um valor inválido volta ao
padrão. Uma falha do planner é avisada do mesmo jeito: nenhum plano é postado, o card fica em
`WAITING_HUMAN`. Se o executor falhar (limite de turnos, tempo esgotado ou
erro da CLI), o agente comenta na issue, não abre PR e o card fica em
`WAITING_HUMAN`; reinicie-o depois de ajustar ou dividir a issue.

`TRADVIA_MAX_REPLANS` (inteiro positivo, padrão 3) limita quantos ajustes de plano um
card aceita. No limite, o agente comenta na issue, pede que a descrição seja
atualizada e o card reiniciado (remova e adicione de novo `agent:ready`), e o card fica
em `WAITING_HUMAN`.

Depois da aprovação, o Claude Code implementa o plano no diretório atual (comece de um
checkout limpo: todo arquivo alterado entra no PR). O Tradvia então faz o commit de todos os arquivos
alterados em `tradvia/issue-<n>` e abre o PR com o resumo, o custo e o tempo do Claude. Se
algum arquivo alterado estiver em `.github/` ou `.git/`, ou for `LICENSE.md`, `CLA.md` ou
`CODEOWNERS`, nenhum PR é aberto e o card volta para `WAITING_HUMAN`.

Status: a integração da CLI está **Partial**. Arquivos apagados não são levados ao PR, e
o workflow `agent.yml` ainda não instala a CLI (um mantenedor faz isso).

## 5. Política de acesso (`.tradvia.yml`)

Status: **Partial** (ADR 0004, fase 1). Veja `docs/tradvia.example.yml`.

- O arquivo fica na raiz do repositório e é lido **só do branch padrão**
  (`TRADVIA_BASE_BRANCH`), nunca da árvore de trabalho nem de um branch de pull request.
  Agentes não podem editá-lo. Sem o arquivo, valem os padrões.
- Ele falha fechado: uma chave desconhecida ou um papel inválido impede o card de começar, e
  o motivo é postado na issue. Nada pode ser configurado abaixo de `read`.
- Os papéis seguem o GitHub: `read < triage < write < maintain < admin`, tirados da
  API de permissão de colaboradores (`role_name`). Quem não é colaborador não tem papel, e bots
  nunca contam.
- Padrões: `start_card: triage`, `approve_plan: write`, `mention_assistant: write`,
  `live_takeover: maintain`, `budget_control: admin`.
- Aplicado hoje: `approve_plan` (quem pode responder a um plano) e `start_card`, que é
  verificado só quando `TRADVIA_ACTOR` (o login que aplicou a label) está definido.
- Planned: o workflow passar `TRADVIA_ACTOR` (fase 2, feito por um mantenedor).
  `mention_assistant`, `live_takeover` e `budget_control` são lidos, mas ainda não
  aplicados pelo core.
