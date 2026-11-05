<!-- tradvia-translation source="docs/adr/0001-open-core-licensing.md" source-sha256="5ff62055259d112e4b1ecae960e7cf28ff0943626f22101aab164a743a430343" -->
# ADR 0001: Organização open-core com core FSL e EE comercial

- Status: Aceita
- Data: 2026-10-17

## Contexto

O Tradvia precisa ser livre para auto-hospedar e inspecionar, para que possa formar uma
comunidade, e ao mesmo tempo impedir que provedores de nuvem e concorrentes o vendam como
serviço gerenciado sem contribuir. O modelo de negócio precisa de espaço para funcionalidades
enterprise pagas (SSO, WhatsApp Business API, Microsoft Teams).

## Decisão

1. Um único repositório (monorepo) com dois diretórios de primeiro nível:
   - `core/` sob **FSL-1.1-ALv2**: qualquer uso exceto um Competing Use; cada
     release vira Apache 2.0 dois anos depois de lançada.
   - `ee/` sob uma **licença comercial**: código visível, uso em produção
     exige assinatura; as funcionalidades são liberadas por uma chave de licença.
2. `core/` nunca depende de `ee/`. `ee/` se conecta a pontos de extensão expostos
   pelo `core/`. Garantido no CI por `scripts/check-license-boundary.sh`.
3. Toda contribuição exige o CLA em `CLA.md`, para que a Empresa possa licenciar
   as contribuições sob as duas licenças.

## Alternativas consideradas

- **Elastic License 2.0 para o core**: proteção parecida, mas ela nunca se converte
  numa licença open source, o que custa mais confiança da comunidade.
- **AGPLv3 para o core**: open source aprovada pela OSI, mas não impede um
  concorrente de hospedar o software se ele publicar as próprias mudanças.
- **Repositório privado separado para o EE**: mais sigilo, mas é mais difícil manter
  as versões sincronizadas. Pode ser revisto se o código do EE precisar se tornar confidencial.

## Consequências

- O projeto é "fair source" / source-available, **não** open source pela OSI.
  A comunicação não deve chamá-lo de "open source".
- Mudanças nos arquivos de licença exigem revisão de um admin (veja `CODEOWNERS`).
