<!-- tradvia-translation source="docs/evaluations/openhands-executor.md" source-sha256="c5f2a83bacb75bd3077647b53b61926b9c7cc54d212e5b9d2a24b0fe9a6116b8" -->
# Avaliação: OpenHands como executor (spike #134)

Status deste documento: **avaliação incompleta**. As fontes primárias (o `LICENSE` do
repositório do OpenHands, as releases e a documentação da CLI) não puderam ser lidas quando isto foi escrito
(o acesso à web não foi concedido na execução), então nada abaixo sobre o OpenHands é afirmado como
fato. Todo item do OpenHands está marcado como **Não verificado** e lista a fonte exata a ler.
Os fatos do lado do Tradvia vêm deste repositório e são citados pelo caminho.

**O OpenHands não está disponível no Tradvia hoje.** Não há adaptador, entrada no catálogo
nem teste para ele.

## Recomendação

**Ainda não. Não abra um card de adaptador até que as verificações abaixo sejam feitas.** A decisão
não pode ser tomada a partir deste documento, porque a licença do pacote específico, o custo real
por tarefa e a saída JSON não interativa não foram verificados, e o driver de sessão
(#118) ainda não está neste repositório. Se as verificações passarem, a resposta passa a ser "sim, um
adaptador no padrão da [ADR 0010](../adr/0010-coding-agent-adapters.md), como um card separado".

Condições para um "sim":

1. O `LICENSE` do repositório que publica o SDK/CLI que chamaríamos (não a
   página da organização) permite o uso como dependência de runtime nos nossos termos, e nenhum
   diretório sob outra licença (por exemplo uma pasta `enterprise/`) é usado.
2. A CLI tem um modo não interativo documentado e estável, com saída legível por máquina
   (ADR 0010, "Headless only").
3. Restrição de ferramentas, sem git, sem rede além do provedor do modelo, e limites de turnos e de tempo
   podem ser aplicados (ADR 0010; orçamentos da ADR 0006).
4. Custo e tokens são informados, ou marcados como "não informado", como a ADR 0010 permite.

## 1. Licença

| Item | Evidência | Fonte | Data | Status |
|---|---|---|---|---|
| Licença do repositório do pacote SDK/CLI | — | `LICENSE` no repositório específico (confira o repositório do SDK/CLI, não a organização) | — | Não verificado |
| Partes sob uma licença diferente (diretórios enterprise) | — | o mesmo repositório, listagem de diretórios e arquivos LICENSE por diretório | — | Não verificado |

Pendente: ler os dois e registrar o id SPDX, a URL e a data da consulta. O `core/` do Tradvia é FSL-1.1-ALv2
(ADR 0001); uma dependência ou chamada de subprocesso precisa ser compatível com ela.

## 2. Custo por tarefa

Registro comparável do lado do Tradvia: `core/src/tradvia/budget.py` e `claude_code.py` produzem
duração, tokens e US$ por execução; a ADR 0006 informa cerca de US$ 0,41 por card para o Claude Code
(estimativa equivalente à API, 2026-10-19).

| Item | Evidência | Status |
|---|---|---|
| Custo do Claude Code por card | ~US$ 0,41 (ADR 0006); US$ e tokens informados (`docs/coding-agents.md`) | Shipped (medido) |
| Custo do OpenHands por card | nenhuma execução feita, nenhuma chave de API disponível para medir | Não verificado |
| O OpenHands informa US$? | desconhecido; se não, marcar `usd_reported=False` | Não verificado |

Pendente: rodar a mesma tarefa pequena nos dois executores e comparar US$, tokens e duração;
se não houver chave disponível, estimar pelo preço por token do modelo e marcar como **estimado**.

## 3. Isolamento

O que o Tradvia tem hoje:

- O executor roda num runner do GitHub (ADR 0006: o isolamento da execução está **Partial**).
- Os session workers (containers isolados) estão **Planned** (`docs/glossario.md`, Fase 2;
  a funcionalidade 1 do README está Partial).
- O driver de sessão do #118 não está neste repositório, então a cobertura dele não pôde ser lida.
- Os caminhos protegidos (`.github/`, `.git/`, `LICENSE.md`, `CLA.md`, `CODEOWNERS`,
  `.tradvia.yml`) são aplicados pelo fluxo depois da execução, a partir do `git status`, para qualquer
  adaptador (ADR 0010). Essa parte não depende do OpenHands.

Perguntas em aberto, Não verificado: se o runtime Docker do OpenHands cabe dentro de uma sessão de container
do Tradvia ou a duplica (Docker-in-Docker num runner); se ele pode rodar sem
git, sem rede além do provedor, e com limites de turnos e de tempo.

## 4. Manutenção

| Item | Evidência | Fonte | Status |
|---|---|---|---|
| Ritmo de releases | — | GitHub Releases do repositório do pacote | Não verificado |
| Ritmo de commits/mudanças, mudanças incompatíveis nas opções da CLI | — | histórico de commits, changelog | Não verificado |

## Comparação com os adaptadores existentes

| Executor | JSON não interativo | Custo informado | Limites de ferramentas | Status no Tradvia |
|---|---|---|---|---|
| Claude Code | `claude -p` | US$ e tokens | `--allowedTools`, `--max-turns` | Shipped |
| Gemini CLI | `gemini -p --output-format json` | só tokens | `--allowed-tools`, turnos verificados depois da execução | In progress |
| Codex CLI | `codex exec --json` | só tokens | modo sandbox, turnos verificados depois da execução | In progress |
| OpenHands | Não verificado | Não verificado | Não verificado | Não disponível (só avaliação) |

Fonte das três primeiras linhas: `docs/coding-agents.md`.

## Fora do escopo

- **CrewAI** não é um executor: duplicaria o LangGraph.
- **Mission Control** está em alfa e tem outra stack.

## Próximo passo

Quem tiver acesso à web: preencha as células "Não verificado" acima com a URL da fonte e a data, rode
a comparação de custo e depois atualize a recomendação. Só se ela virar "sim", adicione o
OpenHands como **Planned** em `docs/coding-agents.md` e abra um card separado para o
adaptador. Não é preciso uma ADR nova; a ADR 0010 cobre o padrão.
