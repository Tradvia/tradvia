<!-- tradvia-translation source="CONTRIBUTING.md" source-sha256="e5f2f6ba819d63dafdde636852de6387e260045a7259cb5134fec5cd2440c69d" -->
# Contribuir com o Tradvia

Obrigado pelo seu interesse no Tradvia!

## Antes de começar

1. **Assine o CLA.** No seu primeiro pull request, o robô do CLA vai pedir que você
   assine o [Contributor License Agreement](CLA.md) com um comentário. Pull
   requests não podem ser mesclados até que ele esteja assinado.
2. **Abra uma issue antes** para qualquer coisa maior que uma correção pequena, para
   combinarmos a abordagem.

## Ambiente de desenvolvimento

Abra um ambiente pronto para programar em um clique com **Code → Codespaces**, ou use
o Dev Container localmente. Depois rode `make test`. Os detalhes, e como o Tradvia
trabalha nas próprias issues, estão em [docs/dev-environment.md](docs/dev-environment.md).

Procurando o que fazer? Escolha uma issue com a label `good first issue`, ou
proponha uma que o agente possa pegar com a label `agent:ready`.

## Onde entra a sua mudança

| O que você está mudando | Diretório | Licença |
|---|---|---|
| Funcionalidades da comunidade, correções, documentação | `core/` | FSL-1.1-ALv2 |
| SSO, WhatsApp Business, Teams e outros módulos enterprise | `ee/` | Comercial |

`core/` nunca pode importar de `ee/`. O CI falha se isso acontecer.

## Fluxo de trabalho

1. Faça um fork do repositório e crie um branch a partir de `main`:
   `feat/<nome-curto>`, `fix/<nome-curto>` ou `docs/<nome-curto>`.
2. Use [Conventional Commits](https://www.conventionalcommits.org/):
   `feat(core): add metric retention setting`.
3. Adicione o cabeçalho SPDX a todo arquivo-fonte novo:
   - em `core/`: `SPDX-License-Identifier: FSL-1.1-ALv2`
   - em `ee/`: `SPDX-License-Identifier: LicenseRef-Tradvia-Commercial`
4. Rode `scripts/check-license-boundary.sh` localmente.
   Trazer uma ideia, uma dependência ou código de outro projeto segue a
   [ADR 0023](docs/adr/0023-third-party-ideas-and-code.md): reimplemente ideias, confira a
   licença exata de uma dependência e fixe a versão, mantenha pequenos trechos copiados em
   `core/vendor/<projeto>/` com a `LICENSE` deles e uma entrada no `NOTICE`, e nunca faça fork.
5. Abra um pull request contra `main`. `main` é protegido: as mudanças só entram
   por pull requests revisados com checks verdes, mesclados por squash.

## Código de conduta

Este projeto segue o [Código de Conduta](CODE_OF_CONDUCT.md).
